"""Deterministic U02 session export and verification."""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, __version__ as pillow_version

from .comparison import ComparisonResult, compare_images
from .depth_scan import DepthScanResult, PreparedWavefront, PropagationParameters, write_metrics_csv

SESSION_SCHEMA_VERSION = "1.0"
U02_T2_STATUS = "BEKLİYOR"
U02_T2_STATUS_REASON = (
    "Published-reference alignment and the image-similarity threshold must be fixed before comparison."
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: object) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def save_float_tiff(path: str | Path, array: np.ndarray) -> Path:
    output = Path(path)
    image = np.asarray(array, dtype=np.float32)
    if image.ndim != 2 or image.size == 0:
        raise ValueError("TIFF array must be a non-empty two-dimensional image")
    if not np.isfinite(image).all():
        raise ValueError("TIFF array must contain only finite values")
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(image).save(output, format="TIFF", compression="raw")
    return output


def load_float_tiff(path: str | Path) -> np.ndarray:
    with Image.open(path) as image:
        if image.format != "TIFF" or image.mode != "F":
            raise ValueError(
                "session/reference TIFF must contain one 32-bit floating-point plane"
            )
        array = np.asarray(image, dtype=np.float32).copy()
    if array.ndim != 2:
        raise ValueError("session TIFF must contain one two-dimensional plane")
    return array


def _write_line_profile(path: Path, result: ComparisonResult) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("column", "candidate", "reference", "difference"),
            lineterminator="\n",
        )
        writer.writeheader()
        for column, (candidate, reference, difference) in enumerate(
            zip(result.candidate_line, result.reference_line, result.difference_line, strict=True)
        ):
            writer.writerow(
                {
                    "column": column,
                    "candidate": format(float(candidate), ".17g"),
                    "reference": format(float(reference), ".17g"),
                    "difference": format(float(difference), ".17g"),
                }
            )


def _input_records(inputs: Mapping[str, str | Path]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for role, raw_path in sorted(inputs.items()):
        path = Path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(f"input does not exist: {path}")
        records.append(
            {
                "role": role,
                "file_name": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return records


def _environment_record() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "numpy": np.__version__,
        "pillow": pillow_version,
    }


def _output_record(path: Path, array: np.ndarray | None = None) -> dict[str, object]:
    record: dict[str, object] = {
        "file": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if array is not None:
        record["shape"] = list(array.shape)
        record["dtype"] = str(array.dtype)
    return record


def _validate_selected_depth(scan: DepthScanResult, selected_depth_um: float) -> float:
    if not np.isfinite(selected_depth_um):
        raise ValueError("selected_depth_um must be finite")
    matches = [metric.depth_um for metric in scan.metrics if np.isclose(metric.depth_um, selected_depth_um, rtol=0, atol=1e-12)]
    if not matches:
        raise ValueError("selected_depth_um must be one of the scanned depths")
    return float(matches[0])


def _validate_session_consistency(
    prepared: PreparedWavefront,
    scan: DepthScanResult,
    parameters: PropagationParameters,
) -> None:
    parameters.validate()
    if prepared.parameters != parameters:
        raise ValueError("prepared wavefront parameters do not match session parameters")
    if scan.source_fingerprint != prepared.source_fingerprint:
        raise ValueError("scan source fingerprint does not match the prepared wavefront")
    if prepared.spectrum.ndim != 2 or prepared.kz_per_um.shape != prepared.spectrum.shape:
        raise ValueError("prepared wavefront arrays are inconsistent")
    if prepared.input_shape != scan.input_shape:
        raise ValueError("scan input shape does not match the prepared wavefront")
    if prepared.roi_bounds != scan.roi_bounds:
        raise ValueError("scan ROI does not match the prepared wavefront")
    top, left, bottom, right = prepared.roi_bounds
    if (bottom - top, right - left) != (parameters.roi_size_px, parameters.roi_size_px):
        raise ValueError("prepared ROI does not match roi_size_px")

    expected_depths = parameters.depths_um()
    actual_depths = np.asarray([metric.depth_um for metric in scan.metrics], dtype=np.float64)
    if actual_depths.shape != expected_depths.shape or not np.array_equal(actual_depths, expected_depths):
        raise ValueError("scan depth grid does not match session parameters")
    metric_values = np.asarray(
        [[metric.gradient_energy, metric.amplitude_std] for metric in scan.metrics],
        dtype=np.float64,
    )
    if not np.isfinite(metric_values).all() or np.any(metric_values < 0):
        raise ValueError("scan metrics must be finite and non-negative")


def export_session(
    output_directory: str | Path,
    *,
    prepared: PreparedWavefront,
    scan: DepthScanResult,
    parameters: PropagationParameters,
    selected_depth_um: float,
    inputs: Mapping[str, str | Path],
    reference: np.ndarray | None = None,
    comparison_line_row: int | None = None,
) -> dict[str, Any]:
    """Write one immutable session directory and return its JSON payload."""

    _validate_session_consistency(prepared, scan, parameters)
    selected_depth = _validate_selected_depth(scan, selected_depth_um)
    input_records = _input_records(inputs)
    selected_float64 = prepared.amplitude_at(selected_depth)
    selected_float32 = np.asarray(selected_float64, dtype=np.float32)
    comparison: ComparisonResult | None = None
    if reference is not None:
        comparison = compare_images(selected_float32, reference, line_row=comparison_line_row)

    output = Path(output_directory)
    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            raise FileExistsError("session destination must not exist or must be empty")
        output.rmdir()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent)
    )
    try:
        metrics_path = write_metrics_csv(scan, staging / "metrics.csv")
        selected_path = save_float_tiff(
            staging / "selected-plane.tiff", selected_float32
        )

        outputs: dict[str, object] = {
            "metrics": _output_record(metrics_path),
            "selected_plane": _output_record(selected_path, selected_float32),
        }
        comparison_summary: dict[str, object] | None = None
        if comparison is not None:
            difference_float32 = np.asarray(comparison.difference, dtype=np.float32)
            difference_path = save_float_tiff(
                staging / "difference-map.tiff", difference_float32
            )
            profile_path = staging / "line-profile.csv"
            _write_line_profile(profile_path, comparison)
            outputs["difference_map"] = _output_record(
                difference_path, difference_float32
            )
            outputs["line_profile"] = _output_record(profile_path)
            comparison_summary = comparison.as_summary()

        session: dict[str, Any] = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "product_id": "U02",
            "test_id": "U02-T2",
            "status": U02_T2_STATUS,
            "status_reason": U02_T2_STATUS_REASON,
            "environment": _environment_record(),
            "units": {
                "depth": "um",
                "gradient_energy": "normalized_amplitude_squared_per_pixel_squared",
                "amplitude_std": "normalized_amplitude",
                "selected_plane": "normalized_amplitude",
            },
            "parameters": parameters.as_dict(),
            "selected_depth_um": selected_depth,
            "inputs": input_records,
            "scan": scan.as_summary(),
            "outputs": outputs,
            "comparison": comparison_summary,
        }
        session_path = staging / "session.json"
        _write_json(session_path, session)

        manifest: dict[str, str] = {}
        for path in sorted(staging.iterdir(), key=lambda item: item.name):
            if path.is_file() and path.name != "sha256.json":
                manifest[path.name] = sha256_file(path)
        _write_json(
            staging / "sha256.json",
            {"algorithm": "sha256", "files": manifest},
        )
        staging.replace(output)
        return session
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def reopen_session(
    output_directory: str | Path,
    *,
    expected_selected_plane: np.ndarray | None = None,
) -> tuple[dict[str, Any], np.ndarray]:
    """Verify all recorded hashes and reopen the selected TIFF pixel array."""

    output = Path(output_directory)
    session = json.loads((output / "session.json").read_text(encoding="utf-8"))
    expected_constants = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "product_id": "U02",
        "test_id": "U02-T2",
        "status": U02_T2_STATUS,
    }
    for key, expected in expected_constants.items():
        if session.get(key) != expected:
            raise ValueError(f"invalid session {key}")
    if not isinstance(session.get("outputs"), dict):
        raise ValueError("session outputs must be an object")
    for required_output in ("metrics", "selected_plane"):
        if required_output not in session["outputs"]:
            raise ValueError(f"missing session output: {required_output}")

    manifest = json.loads((output / "sha256.json").read_text(encoding="utf-8"))
    if manifest.get("algorithm") != "sha256" or not isinstance(manifest.get("files"), dict):
        raise ValueError("invalid hash manifest")
    manifest_files: dict[str, str] = manifest["files"]
    required_manifest_files = {"session.json"}
    for output_name, record in session["outputs"].items():
        if not isinstance(record, dict) or not isinstance(record.get("file"), str):
            raise ValueError(f"invalid output record: {output_name}")
        name = record["file"]
        if name != Path(name).name:
            raise ValueError(f"invalid output file name: {name}")
        required_manifest_files.add(name)
    if not required_manifest_files.issubset(manifest_files):
        missing = sorted(required_manifest_files.difference(manifest_files))
        raise ValueError(f"hash manifest is missing required files: {', '.join(missing)}")

    comparison_metadata = session.get("comparison")
    if comparison_metadata is None:
        if "difference_map" in session["outputs"] or "line_profile" in session["outputs"]:
            raise ValueError("comparison outputs exist without comparison metadata")
    else:
        if not isinstance(comparison_metadata, dict):
            raise ValueError("comparison metadata must be an object or null")
        if comparison_metadata.get("acceptance_result") != "NOT_EVALUATED":
            raise ValueError("comparison acceptance result must remain NOT_EVALUATED")
        if comparison_metadata.get("acceptance_threshold") is not None:
            raise ValueError("comparison acceptance threshold must remain null")
        for required_output in ("difference_map", "line_profile"):
            if required_output not in session["outputs"]:
                raise ValueError(f"missing comparison output: {required_output}")

    for name, expected in manifest_files.items():
        if name != Path(name).name or not isinstance(expected, str):
            raise ValueError(f"invalid hash manifest entry: {name}")
        path = output / name
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"hash verification failed: {name}")

    for output_name, record in session["outputs"].items():
        name = record["file"]
        path = output / name
        if record.get("sha256") != manifest_files[name]:
            raise ValueError(f"output hash is not covered by manifest: {output_name}")
        if record.get("size_bytes") != path.stat().st_size:
            raise ValueError(f"output size does not match session: {output_name}")

    selected_record = session["outputs"]["selected_plane"]
    selected_path = output / selected_record["file"]
    reopened = load_float_tiff(selected_path)
    if list(reopened.shape) != selected_record["shape"] or str(reopened.dtype) != selected_record["dtype"]:
        raise ValueError("selected TIFF shape or dtype does not match the session")
    if sha256_file(selected_path) != selected_record["sha256"]:
        raise ValueError("selected TIFF hash does not match the session")
    if expected_selected_plane is not None:
        expected = np.asarray(expected_selected_plane, dtype=np.float32)
        if not np.array_equal(reopened, expected):
            raise ValueError("reopened selected TIFF does not match the expected pixel array")
    return session, reopened

