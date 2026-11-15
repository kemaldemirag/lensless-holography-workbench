"""Read-only, hash-verified views of exported U02 sessions."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .depth_scan import PropagationParameters
from .session import U02_T2_STATUS, load_float_tiff, reopen_session


@dataclass(frozen=True, slots=True)
class OpenedMetric:
    """One verified numerical depth-scan row."""

    depth_um: float
    gradient_energy: float
    amplitude_std: float


@dataclass(frozen=True, slots=True)
class OpenedLinePoint:
    """One verified point from the exported comparison line."""

    column: int
    candidate: float
    reference: float
    difference: float


@dataclass(frozen=True, slots=True)
class OpenedSession:
    """Inspection-only representation of an immutable U02 session."""

    directory: Path
    metadata: Mapping[str, Any]
    metrics: tuple[OpenedMetric, ...]
    selected_plane: NDArray[np.float32]
    difference_map: NDArray[np.float32] | None
    line_profile: tuple[OpenedLinePoint, ...]

    @property
    def status(self) -> str:
        return str(self.metadata["status"])

    @property
    def acceptance_result(self) -> str:
        """The inspection model never derives a PASS or physical-focus claim."""

        return "NOT_EVALUATED"

    @property
    def has_comparison(self) -> bool:
        return self.difference_map is not None


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _strict_directory_files(
    directory: Path,
    session: Mapping[str, Any],
) -> None:
    manifest_path = directory / "sha256.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    names = manifest.get("files")
    if not isinstance(names, dict):
        raise ValueError("invalid hash manifest")
    outputs = session.get("outputs")
    if not isinstance(outputs, Mapping):
        raise ValueError("session outputs must be an object")
    declared = {"session.json"}
    for label, record in outputs.items():
        if not isinstance(record, Mapping) or not isinstance(record.get("file"), str):
            raise ValueError(f"invalid session output record: {label}")
        declared.add(record["file"])
    manifest_names = set(names)
    if manifest_names != declared:
        unexpected = sorted(manifest_names - declared)
        missing = sorted(declared - manifest_names)
        details: list[str] = []
        if unexpected:
            details.append(f"not declared by session: {', '.join(unexpected)}")
        if missing:
            details.append(f"missing from manifest: {', '.join(missing)}")
        raise ValueError(f"hash manifest does not match session outputs ({'; '.join(details)})")
    expected = declared | {"sha256.json"}
    entries = tuple(directory.iterdir())
    if any(not entry.is_file() for entry in entries):
        raise ValueError("session directory must contain files only")
    actual = {entry.name for entry in entries}
    if actual != expected:
        unexpected = sorted(actual - expected)
        missing = sorted(expected - actual)
        details: list[str] = []
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        raise ValueError(f"session directory does not match hash manifest ({'; '.join(details)})")


def _read_metrics(directory: Path, session: Mapping[str, Any]) -> tuple[OpenedMetric, ...]:
    record = session["outputs"]["metrics"]
    path = directory / record["file"]
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        expected_fields = ["depth_um", "gradient_energy", "amplitude_std"]
        if reader.fieldnames != expected_fields:
            raise ValueError("metrics CSV has invalid columns")
        raw_rows = list(reader)

    metrics: list[OpenedMetric] = []
    for index, row in enumerate(raw_rows):
        try:
            metric = OpenedMetric(
                depth_um=float(row["depth_um"]),
                gradient_energy=float(row["gradient_energy"]),
                amplitude_std=float(row["amplitude_std"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"metrics CSV row {index + 1} is invalid") from exc
        values = (metric.depth_um, metric.gradient_energy, metric.amplitude_std)
        if not np.isfinite(values).all():
            raise ValueError(f"metrics CSV row {index + 1} is not finite")
        if metric.gradient_energy < 0 or metric.amplitude_std < 0:
            raise ValueError(f"metrics CSV row {index + 1} has a negative metric")
        metrics.append(metric)
    if not metrics:
        raise ValueError("metrics CSV must contain at least one row")

    try:
        parameters = PropagationParameters(**dict(session["parameters"]))
        expected_depths = parameters.depths_um()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("session propagation parameters are invalid") from exc
    actual_depths = np.asarray([row.depth_um for row in metrics], dtype=np.float64)
    if not np.array_equal(actual_depths, expected_depths):
        raise ValueError("metrics depth grid does not match session parameters")

    scan = session.get("scan")
    if not isinstance(scan, Mapping):
        raise ValueError("session scan summary is invalid")
    if scan.get("planes") != len(metrics):
        raise ValueError("metrics row count does not match session scan")
    gradient_peak = max(metrics, key=lambda row: row.gradient_energy).depth_um
    amplitude_peak = max(metrics, key=lambda row: row.amplitude_std).depth_um
    if scan.get("gradient_peak_um") != gradient_peak:
        raise ValueError("gradient peak does not match metrics CSV")
    if scan.get("amplitude_std_peak_um") != amplitude_peak:
        raise ValueError("amplitude peak does not match metrics CSV")

    selected_depth = session.get("selected_depth_um")
    if selected_depth not in {row.depth_um for row in metrics}:
        raise ValueError("selected depth is not present in metrics CSV")
    return tuple(metrics)


def _validate_image_record(
    array: NDArray[np.float32],
    record: Mapping[str, Any],
    *,
    label: str,
) -> None:
    if list(array.shape) != record.get("shape"):
        raise ValueError(f"{label} shape does not match session")
    if str(array.dtype) != record.get("dtype"):
        raise ValueError(f"{label} dtype does not match session")
    if not np.isfinite(array).all():
        raise ValueError(f"{label} contains a non-finite value")


def _read_line_profile(path: Path) -> tuple[OpenedLinePoint, ...]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        expected_fields = ["column", "candidate", "reference", "difference"]
        if reader.fieldnames != expected_fields:
            raise ValueError("line profile CSV has invalid columns")
        raw_rows = list(reader)

    points: list[OpenedLinePoint] = []
    for index, row in enumerate(raw_rows):
        try:
            point = OpenedLinePoint(
                column=int(row["column"]),
                candidate=float(row["candidate"]),
                reference=float(row["reference"]),
                difference=float(row["difference"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"line profile row {index + 1} is invalid") from exc
        if point.column != index:
            raise ValueError("line profile columns must be contiguous and zero-based")
        values = (point.candidate, point.reference, point.difference)
        if not np.isfinite(values).all():
            raise ValueError(f"line profile row {index + 1} is not finite")
        if not np.isclose(
            point.candidate - point.reference,
            point.difference,
            rtol=0.0,
            atol=1e-12,
        ):
            raise ValueError(f"line profile row {index + 1} has an inconsistent difference")
        points.append(point)
    if not points:
        raise ValueError("line profile CSV must contain at least one row")
    return tuple(points)


def open_session_for_inspection(output_directory: str | Path) -> OpenedSession:
    """Verify and load a session without reconstructing or accepting its result."""

    directory = Path(output_directory)
    session, selected_plane = reopen_session(directory)
    _strict_directory_files(directory, session)
    metrics = _read_metrics(directory, session)

    selected_record = session["outputs"]["selected_plane"]
    _validate_image_record(selected_plane, selected_record, label="selected plane")
    scan = session["scan"]
    roi = scan.get("roi")
    if not isinstance(roi, Mapping) or roi.get("shape") != list(selected_plane.shape):
        raise ValueError("selected plane shape does not match session ROI")

    difference_map: NDArray[np.float32] | None = None
    line_profile: tuple[OpenedLinePoint, ...] = ()
    comparison = session.get("comparison")
    if comparison is not None:
        if session.get("status") != U02_T2_STATUS:
            raise ValueError("comparison session must remain BEKLİYOR")
        if comparison.get("acceptance_result") != "NOT_EVALUATED":
            raise ValueError("comparison must remain NOT_EVALUATED")
        if comparison.get("acceptance_threshold") is not None:
            raise ValueError("comparison acceptance threshold must remain null")

        difference_record = session["outputs"]["difference_map"]
        difference_map = load_float_tiff(directory / difference_record["file"])
        _validate_image_record(difference_map, difference_record, label="difference map")
        if difference_map.shape != selected_plane.shape:
            raise ValueError("difference map shape does not match selected plane")

        profile_record = session["outputs"]["line_profile"]
        line_profile = _read_line_profile(directory / profile_record["file"])
        line_row = comparison.get("line_row")
        if isinstance(line_row, bool) or not isinstance(line_row, int):
            raise ValueError("comparison line row is invalid")
        if not 0 <= line_row < selected_plane.shape[0]:
            raise ValueError("comparison line row is outside the selected plane")
        if len(line_profile) != selected_plane.shape[1]:
            raise ValueError("line profile width does not match selected plane")

        candidate = np.asarray([point.candidate for point in line_profile], dtype=np.float32)
        difference = np.asarray([point.difference for point in line_profile], dtype=np.float32)
        if not np.array_equal(candidate, selected_plane[line_row]):
            raise ValueError("line profile candidate does not match selected plane")
        if not np.array_equal(difference, difference_map[line_row]):
            raise ValueError("line profile difference does not match difference map")

        rmse = float(np.sqrt(np.mean(np.asarray(difference_map, dtype=np.float64) ** 2)))
        maximum = float(np.max(np.abs(difference_map)))
        try:
            recorded_rmse = float(comparison["rmse"])
            recorded_maximum = float(comparison["max_abs_difference"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("comparison summary is invalid") from exc
        if not np.isclose(recorded_rmse, rmse, rtol=1e-6, atol=1e-7):
            raise ValueError("comparison RMSE does not match difference map")
        if not np.isclose(
            recorded_maximum, maximum, rtol=1e-6, atol=1e-7
        ):
            raise ValueError("comparison maximum does not match difference map")

    selected_plane.setflags(write=False)
    if difference_map is not None:
        difference_map.setflags(write=False)
    return OpenedSession(
        directory=directory.resolve(),
        metadata=_freeze(session),
        metrics=metrics,
        selected_plane=selected_plane,
        difference_map=difference_map,
        line_profile=line_profile,
    )
