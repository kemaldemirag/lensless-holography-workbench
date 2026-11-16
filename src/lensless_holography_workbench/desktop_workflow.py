"""Headless state model for the Windows workbench.

The Tkinter layer owns widgets and worker threads. This module owns validated
input selection, stale-worker protection, scan state, depth-plane caching,
preview scaling, and session export/reopen. It never turns an unevaluated
comparison into a physical-focus or customer-acceptance claim.
"""

from __future__ import annotations

import copy
import shutil
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

import numpy as np

from .depth_scan import (
    DepthMetric,
    DepthScanResult,
    PreparedWavefront,
    PropagationParameters,
    measure_depth_plane,
    normalize_hologram,
)
from .session import (
    InputFileSnapshot,
    U02_T2_STATUS,
    export_session,
    load_float_tiff_bytes,
    snapshot_input_files,
    snapshot_input_hashes,
)
from .image_io import load_grayscale_bytes
from .opened_session import OpenedSession, open_session_for_inspection

ProgressCallback = Callable[[int, int], None]
PREVIEW_LOW_PERCENTILE = 0.8
PREVIEW_HIGH_PERCENTILE = 99.2
COMPARISON_RESULT = "NOT_EVALUATED"


class WorkflowState(str, Enum):
    EMPTY = "EMPTY"
    READY = "READY"
    RUNNING = "RUNNING"
    SCANNED = "SCANNED"
    EXPORTED = "EXPORTED"
    REOPENED = "REOPENED"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"


class StaleWorkflowError(RuntimeError):
    """Raised when an asynchronous result belongs to an older workflow."""


@dataclass(frozen=True, slots=True)
class InputSelection:
    signal: Path
    backgrounds: tuple[Path, ...]
    reference_tiff: Path | None = None

    @classmethod
    def create(
        cls,
        signal: str | Path,
        backgrounds: tuple[str | Path, ...] | list[str | Path],
        reference_tiff: str | Path | None = None,
    ) -> "InputSelection":
        selection = cls(
            signal=Path(signal).resolve(),
            backgrounds=tuple(Path(path).resolve() for path in backgrounds),
            reference_tiff=(
                Path(reference_tiff).resolve()
                if reference_tiff is not None
                else None
            ),
        )
        selection.validate()
        return selection

    def validate(self) -> None:
        if not self.signal.is_file():
            raise FileNotFoundError(f"signal does not exist: {self.signal}")
        if not self.backgrounds:
            raise ValueError("at least one background image is required")
        for path in self.backgrounds:
            if not path.is_file():
                raise FileNotFoundError(f"background does not exist: {path}")
        if self.reference_tiff is not None:
            if not self.reference_tiff.is_file():
                raise FileNotFoundError(f"reference TIFF does not exist: {self.reference_tiff}")
            if self.reference_tiff.suffix.lower() not in {".tif", ".tiff"}:
                raise ValueError("reference must be an aligned float TIFF")

        all_paths = [self.signal, *self.backgrounds]
        if self.reference_tiff is not None:
            all_paths.append(self.reference_tiff)
        identities = [str(path.resolve()).casefold() for path in all_paths]
        if len(identities) != len(set(identities)):
            raise ValueError("signal, backgrounds, and reference must be unique files")

    def as_session_inputs(self) -> dict[str, Path]:
        records: dict[str, Path] = {"signal": self.signal}
        records.update(
            {
                f"background_{index}": path
                for index, path in enumerate(self.backgrounds, start=1)
            }
        )
        if self.reference_tiff is not None:
            records["reference_plane"] = self.reference_tiff
        return records


@dataclass(frozen=True, slots=True)
class ScanRequest:
    generation: int
    inputs: InputSelection
    parameters: PropagationParameters
    cancel_event: threading.Event
    input_hashes: tuple[tuple[str, str], ...]
    input_snapshots: tuple[tuple[str, InputFileSnapshot], ...]


@dataclass(frozen=True, slots=True)
class PlanePreview:
    depth_um: float
    amplitude: np.ndarray
    image_u8: np.ndarray
    display_low: float
    display_high: float
    metric: DepthMetric | None
    acceptance_status: str = U02_T2_STATUS
    comparison_result: str = COMPARISON_RESULT


def _input_hashes(inputs: InputSelection) -> tuple[tuple[str, str], ...]:
    return tuple(snapshot_input_hashes(inputs.as_session_inputs()).items())


def _plain_copy(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_copy(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_copy(item) for item in value]
    if isinstance(value, list):
        return [_plain_copy(item) for item in value]
    return copy.deepcopy(value)


def _matching_depth(depths: tuple[float, ...], requested: float) -> float:
    if not np.isfinite(requested):
        raise ValueError("selected depth must be finite")
    matches = [
        depth
        for depth in depths
        if np.isclose(depth, requested, rtol=0.0, atol=1e-12)
    ]
    if not matches:
        raise ValueError("selected depth must be one of the scanned depths")
    return float(matches[0])


def _preview_bounds(planes: dict[float, np.ndarray]) -> tuple[float, float]:
    if not planes:
        raise ValueError("at least one cached plane is required")
    values = np.stack(tuple(planes.values()), axis=0)
    low, high = np.percentile(
        values,
        [PREVIEW_LOW_PERCENTILE, PREVIEW_HIGH_PERCENTILE],
    )
    low_value, high_value = float(low), float(high)
    if not np.isfinite([low_value, high_value]).all():
        raise ValueError("preview bounds must be finite")
    if high_value <= low_value:
        high_value = float(np.nextafter(low_value, np.inf))
    return low_value, high_value


class DesktopWorkflow:
    """Thread-safe, headless workflow used by the desktop view."""

    def __init__(
        self,
        *,
        parameters: PropagationParameters | None = None,
    ) -> None:
        if parameters is not None:
            parameters.validate()
        self._lock = threading.RLock()
        self._state = WorkflowState.EMPTY
        self._inputs: InputSelection | None = None
        self._parameters = parameters
        self._generation = 0
        self._active_cancel: threading.Event | None = None
        self._prepared: PreparedWavefront | None = None
        self._scan: DepthScanResult | None = None
        self._planes: dict[float, np.ndarray] = {}
        self._reference: np.ndarray | None = None
        self._input_hashes: tuple[tuple[str, str], ...] = ()
        self._opened_session: OpenedSession | None = None
        self._preview_range: tuple[float, float] | None = None
        self._selected_depth_um: float | None = None
        self._session: dict[str, object] | None = None
        self._error_message: str | None = None

    @property
    def state(self) -> WorkflowState:
        with self._lock:
            return self._state

    @property
    def inputs(self) -> InputSelection | None:
        with self._lock:
            return self._inputs

    @property
    def parameters(self) -> PropagationParameters | None:
        with self._lock:
            return self._parameters

    @property
    def error_message(self) -> str | None:
        with self._lock:
            return self._error_message

    @property
    def available_depths(self) -> tuple[float, ...]:
        with self._lock:
            return tuple(self._planes)

    @property
    def cached_depths(self) -> tuple[float, ...]:
        return self.available_depths

    @property
    def selected_depth_um(self) -> float | None:
        with self._lock:
            return self._selected_depth_um

    @property
    def preview_range(self) -> tuple[float, float] | None:
        with self._lock:
            return self._preview_range

    @property
    def scan_result(self) -> DepthScanResult | None:
        with self._lock:
            return self._scan

    @property
    def session(self) -> dict[str, object] | None:
        with self._lock:
            return copy.deepcopy(self._session)

    def configure_inputs(
        self,
        signal: str | Path,
        backgrounds: tuple[str | Path, ...] | list[str | Path],
        reference_tiff: str | Path | None = None,
    ) -> None:
        selection = InputSelection.create(signal, backgrounds, reference_tiff)
        with self._lock:
            self._invalidate_locked()
            self._inputs = selection
            self._state = (
                WorkflowState.READY
                if self._parameters is not None
                else WorkflowState.EMPTY
            )

    def clear_inputs(self) -> None:
        with self._lock:
            self._invalidate_locked()
            self._inputs = None
            self._state = WorkflowState.EMPTY

    def configure_parameters(self, parameters: PropagationParameters) -> None:
        parameters.validate()
        with self._lock:
            self._invalidate_locked()
            self._parameters = parameters
            self._state = (
                WorkflowState.READY
                if self._inputs is not None
                else WorkflowState.EMPTY
            )

    def _invalidate_locked(self) -> None:
        if self._active_cancel is not None:
            self._active_cancel.set()
        self._generation += 1
        self._active_cancel = None
        self._prepared = None
        self._scan = None
        self._planes.clear()
        self._reference = None
        self._input_hashes = ()
        self._opened_session = None
        self._preview_range = None
        self._selected_depth_um = None
        self._session = None
        self._error_message = None

    def begin_scan(self) -> ScanRequest:
        with self._lock:
            if self._inputs is None or self._parameters is None:
                raise RuntimeError("inputs and parameters must be configured before scanning")
            if self._state is WorkflowState.RUNNING:
                raise RuntimeError("a scan is already running")
            self._inputs.validate()
            self._parameters.validate()
            snapshots = snapshot_input_files(
                self._inputs.as_session_inputs()
            )
            hashes = tuple(
                (role, snapshot.sha256)
                for role, snapshot in snapshots.items()
            )
            self._generation += 1
            cancel_event = threading.Event()
            self._active_cancel = cancel_event
            self._state = WorkflowState.RUNNING
            self._error_message = None
            return ScanRequest(
                generation=self._generation,
                inputs=self._inputs,
                parameters=self._parameters,
                cancel_event=cancel_event,
                input_hashes=hashes,
                input_snapshots=tuple(snapshots.items()),
            )

    def cancel_scan(self) -> bool:
        with self._lock:
            if self._state is not WorkflowState.RUNNING:
                return False
            if self._active_cancel is not None:
                self._active_cancel.set()
            self._state = WorkflowState.CANCELLED
            return True

    def _request_is_current_locked(self, request: ScanRequest) -> bool:
        return (
            request.generation == self._generation
            and request.cancel_event is self._active_cancel
        )

    def execute_scan(
        self,
        request: ScanRequest,
        *,
        progress: ProgressCallback | None = None,
    ) -> bool:
        """Execute a request from a worker thread.

        Returns false when cancellation or a newer configuration makes the
        request stale. Current-request errors are recorded and re-raised.
        """

        try:
            with self._lock:
                if not self._request_is_current_locked(request):
                    return False
            if request.cancel_event.is_set():
                return False

            snapshots = dict(request.input_snapshots)
            signal = load_grayscale_bytes(snapshots["signal"].content)
            backgrounds = [
                load_grayscale_bytes(
                    snapshots[f"background_{index}"].content
                )
                for index in range(1, len(request.inputs.backgrounds) + 1)
            ]
            reference = (
                load_float_tiff_bytes(snapshots["reference_plane"].content)
                if request.inputs.reference_tiff is not None
                else None
            )
            if request.cancel_event.is_set():
                return False

            normalized = normalize_hologram(signal, backgrounds)
            prepared = PreparedWavefront.from_normalized(
                normalized,
                request.parameters,
            )
            depths = tuple(float(depth) for depth in request.parameters.depths_um())
            total = len(depths)
            if progress is not None:
                progress(0, total)

            planes: dict[float, np.ndarray] = {}
            metrics: list[DepthMetric] = []
            for completed, depth in enumerate(depths, start=1):
                if request.cancel_event.is_set():
                    return False
                amplitude = prepared.amplitude_at(depth)
                metrics.append(measure_depth_plane(depth, amplitude))
                planes[depth] = np.asarray(amplitude, dtype=np.float32)
                if progress is not None:
                    progress(completed, total)

            scan = DepthScanResult(
                metrics=tuple(metrics),
                input_shape=prepared.input_shape,
                roi_bounds=prepared.roi_bounds,
                source_fingerprint=prepared.source_fingerprint,
            )

            preview_range = _preview_bounds(planes)
            if reference is not None:
                first_plane = next(iter(planes.values()))
                if reference.shape != first_plane.shape:
                    raise ValueError(
                        "aligned reference TIFF must match the scanned ROI shape"
                    )
                if not np.isfinite(reference).all():
                    raise ValueError("aligned reference TIFF must be finite")
            if _input_hashes(request.inputs) != request.input_hashes:
                raise ValueError("input files changed while the scan was running")

            with self._lock:
                if (
                    not self._request_is_current_locked(request)
                    or request.cancel_event.is_set()
                ):
                    return False
                self._prepared = prepared
                self._scan = scan
                self._planes = planes
                self._reference = reference
                self._input_hashes = request.input_hashes
                self._opened_session = None
                self._preview_range = preview_range
                self._selected_depth_um = depths[0]
                self._session = None
                self._active_cancel = None
                self._state = WorkflowState.SCANNED
                self._error_message = None
            return True
        except Exception as exc:
            with self._lock:
                if not self._request_is_current_locked(request):
                    return False
                self._active_cancel = None
                self._state = WorkflowState.ERROR
                self._error_message = str(exc)
            raise

    def select_depth(self, depth_um: float) -> float:
        with self._lock:
            if not self._planes:
                raise RuntimeError("no scanned or reopened plane is available")
            selected = _matching_depth(tuple(self._planes), depth_um)
            if self._selected_depth_um != selected:
                self._generation += 1
                if self._state is WorkflowState.EXPORTED:
                    self._state = WorkflowState.SCANNED
                    self._session = None
            self._selected_depth_um = selected
            return selected

    def plane_preview(self, depth_um: float | None = None) -> PlanePreview:
        with self._lock:
            if not self._planes or self._preview_range is None:
                raise RuntimeError("no scanned or reopened plane is available")
            requested = (
                self._selected_depth_um if depth_um is None else float(depth_um)
            )
            if requested is None:
                raise RuntimeError("no depth is selected")
            selected = _matching_depth(tuple(self._planes), requested)
            amplitude = self._planes[selected].copy()
            low, high = self._preview_range
            metric = None
            if self._scan is not None:
                metric = next(
                    item
                    for item in self._scan.metrics
                    if np.isclose(
                        item.depth_um,
                        selected,
                        rtol=0.0,
                        atol=1e-12,
                    )
                )
        scaled = np.clip((amplitude - low) / (high - low), 0.0, 1.0)
        image_u8 = np.rint(scaled * 255.0).astype(np.uint8)
        return PlanePreview(
            depth_um=selected,
            amplitude=amplitude,
            image_u8=image_u8,
            display_low=low,
            display_high=high,
            metric=metric,
        )

    def export(
        self,
        output_directory: str | Path,
        *,
        comparison_line_row: int | None = None,
    ) -> dict[str, object]:
        with self._lock:
            if self._state not in {
                WorkflowState.SCANNED,
                WorkflowState.EXPORTED,
            }:
                raise RuntimeError(
                    "export is allowed only after a current completed scan"
                )
            if (
                self._prepared is None
                or self._scan is None
                or self._inputs is None
                or self._parameters is None
                or self._selected_depth_um is None
            ):
                raise RuntimeError("a completed scan is required before export")
            generation = self._generation
            prepared = self._prepared
            scan = self._scan
            inputs = self._inputs
            parameters = self._parameters
            expected_hashes = self._input_hashes
            selected_depth = self._selected_depth_um
            reference = (
                self._reference.copy()
                if self._reference is not None
                else None
            )
        temporary_output: Path | None = None
        try:
            output = Path(output_directory)
            if output.exists() and (
                not output.is_dir() or any(output.iterdir())
            ):
                raise FileExistsError(
                    "session destination must not exist or must be empty"
                )
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary_output = Path(
                tempfile.mkdtemp(
                    prefix=f".{output.name}.workflow-",
                    dir=output.parent,
                )
            )
            session = export_session(
                temporary_output,
                prepared=prepared,
                scan=scan,
                parameters=parameters,
                selected_depth_um=selected_depth,
                inputs=inputs.as_session_inputs(),
                expected_input_hashes=dict(expected_hashes),
                reference=reference,
                comparison_line_row=comparison_line_row,
            )
            if session.get("status") != U02_T2_STATUS:
                raise ValueError(
                    "desktop export must preserve the U02-T2 status"
                )
            comparison = session.get("comparison")
            if comparison is not None:
                if not isinstance(comparison, Mapping):
                    raise ValueError("comparison metadata must be an object")
                if (
                    comparison.get("acceptance_result")
                    != COMPARISON_RESULT
                    or comparison.get("acceptance_threshold") is not None
                ):
                    raise ValueError(
                        "desktop export must keep comparison unevaluated"
                    )
            stored_session = copy.deepcopy(session)
            returned_session = copy.deepcopy(stored_session)
            with self._lock:
                if (
                    generation != self._generation
                    or self._state
                    not in {WorkflowState.SCANNED, WorkflowState.EXPORTED}
                ):
                    raise StaleWorkflowError(
                        "workflow changed during export"
                    )
                if _input_hashes(inputs) != expected_hashes:
                    raise ValueError(
                        "input files changed before publication"
                    )
                if output.exists():
                    if not output.is_dir() or any(output.iterdir()):
                        raise FileExistsError(
                            "session destination must not exist or must be empty"
                        )
                    output.rmdir()
                temporary_output.replace(output)
                temporary_output = None
                self._session = stored_session
                self._state = WorkflowState.EXPORTED
                self._error_message = None
                return returned_session
        except Exception as exc:
            with self._lock:
                if (
                    generation != self._generation
                    or self._state
                    not in {WorkflowState.SCANNED, WorkflowState.EXPORTED}
                ):
                    raise StaleWorkflowError(
                        "workflow changed during export"
                    ) from exc
                self._state = WorkflowState.ERROR
                self._error_message = str(exc)
            raise
        finally:
            if temporary_output is not None:
                shutil.rmtree(temporary_output, ignore_errors=True)

    def reopen(self, session_directory: str | Path) -> dict[str, object]:
        try:
            opened = open_session_for_inspection(
                session_directory
            )
            session = _plain_copy(opened.metadata)
            if not isinstance(session, dict):
                raise ValueError("opened session metadata must be an object")
            selected_depth = float(session["selected_depth_um"])
            plane = np.asarray(opened.selected_plane, dtype=np.float32).copy()
            preview_range = _preview_bounds({selected_depth: plane})
        except Exception as exc:
            with self._lock:
                self._invalidate_locked()
                self._state = WorkflowState.ERROR
                self._error_message = str(exc)
            raise
        with self._lock:
            self._invalidate_locked()
            self._planes = {selected_depth: plane}
            self._preview_range = preview_range
            self._selected_depth_um = selected_depth
            self._session = copy.deepcopy(session)
            self._opened_session = opened
            self._state = WorkflowState.REOPENED
            return copy.deepcopy(self._session)
