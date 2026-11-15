"""Deterministic U02 session export and verification."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .comparison import ComparisonResult, compare_images
from .depth_scan import DepthScanResult, PreparedWavefront, PropagationParameters, write_metrics_csv

SESSION_SCHEMA_VERSION = "1.0"
U02_T2_STATUS = "BEKLIYOR"
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

    parameters.validate()
    selected_depth = _validate_selected_depth(scan, selected_depth_um)
    output = Path(output_directory)
    managed_names = {
        "metrics.csv",
        "selected-plane.tiff",
        "difference-map.tiff",
        "line-profile.csv",
        "session.json",
        "sha256.json",
    }
    if output.exists() and any((output / name).exists() for name in managed_names):
        raise FileExistsError("session directory already contains managed outputs")
    output.mkdir(parents=True, exist_ok=True)

    metrics_path = write_metrics_csv(scan, output / "metrics.csv")
    selected_float64 = prepared.amplitude_at(selected_depth)
    selected_float32 = np.asarray(selected_float64, dtype=np.float32)
    selected_path = save_float_tiff(output / "selected-plane.tiff", selected_float32)

    outputs: dict[str, object] = {
        "metrics": _output_record(metrics_path),
        "selected_plane": _output_record(selected_path, selected_float32),
    }
    comparison_summary: dict[str, object] | None = None
    if reference is not None:
        comparison = compare_images(selected_float32, reference, line_row=comparison_line_row)
        difference_float32 = np.asarray(comparison.difference, dtype=np.float32)
        difference_path = save_float_tiff(output / "difference-map.tiff", difference_float32)
        profile_path = output / "line-profile.csv"
        _write_line_profile(profile_path, comparison)
        outputs["difference_map"] = _output_record(difference_path, difference_float32)
        outputs["line_profile"] = _output_record(profile_path)
        comparison_summary = comparison.as_summary()

    session: dict[str, Any] = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "product_id": "U02",
        "test_id": "U02-T2",
        "status": U02_T2_STATUS,
        "status_reason": U02_T2_STATUS_REASON,
        "parameters": parameters.as_dict(),
        "selected_depth_um": selected_depth,
        "inputs": _input_records(inputs),
        "scan": scan.as_summary(),
        "outputs": outputs,
        "comparison": comparison_summary,
    }
    session_path = output / "session.json"
    _write_json(session_path, session)

    manifest: dict[str, str] = {}
    for path in sorted(output.iterdir(), key=lambda item: item.name):
        if path.is_file() and path.name != "sha256.json":
            manifest[path.name] = sha256_file(path)
    _write_json(output / "sha256.json", {"algorithm": "sha256", "files": manifest})
    return session


def reopen_session(
    output_directory: str | Path,
    *,
    expected_selected_plane: np.ndarray | None = None,
) -> tuple[dict[str, Any], np.ndarray]:
    """Verify all recorded hashes and reopen the selected TIFF pixel array."""

    output = Path(output_directory)
    session = json.loads((output / "session.json").read_text(encoding="utf-8"))
    if session.get("schema_version") != SESSION_SCHEMA_VERSION:
        raise ValueError("unsupported session schema version")
    manifest = json.loads((output / "sha256.json").read_text(encoding="utf-8"))
    if manifest.get("algorithm") != "sha256" or not isinstance(manifest.get("files"), dict):
        raise ValueError("invalid hash manifest")
    for name, expected in manifest["files"].items():
        path = output / name
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"hash verification failed: {name}")

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

