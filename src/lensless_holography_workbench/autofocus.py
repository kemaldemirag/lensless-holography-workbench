"""Deterministic coarse-to-fine numerical focus navigation.

Every result from this module is ``NUMERICAL_NAVIGATION_ONLY``.  A metric
optimum does not validate physical focus or reconstruction accuracy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from numbers import Integral, Real

import numpy as np

from .depth_scan import PreparedWavefront
from .focus_metrics import FocusDirection, FocusMetric, evaluate_focus_metric

NUMERICAL_NAVIGATION_ONLY = "NUMERICAL_NAVIGATION_ONLY"
_MAX_UNIQUE_PLANES = 10_000
_MAX_EXACT_FINE_GRID_INDEX = (1 << 53) - 1
_RATIO_TOLERANCE = 1e-10


def _finite_real(value: object, *, name: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a real number")
    result = float(value)
    if not np.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _integer_ratio(numerator: float, denominator: float, *, name: str) -> int:
    ratio = numerator / denominator
    if not np.isfinite(ratio):
        raise ValueError(f"{name} must be finite")
    nearest = int(round(ratio))
    if not np.isclose(ratio, nearest, rtol=0.0, atol=_RATIO_TOLERANCE):
        raise ValueError(f"{name} must contain an integer number of steps")
    return nearest


def _canonical_roi_bounds(
    roi_bounds: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int] | None:
    if roi_bounds is None:
        return None
    if not isinstance(roi_bounds, tuple) or len(roi_bounds) != 4:
        raise ValueError("roi_bounds must be a four-integer tuple")
    if any(
        isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral)
        for value in roi_bounds
    ):
        raise ValueError("roi_bounds must contain only integers")
    top, left, bottom, right = (int(value) for value in roi_bounds)
    if top < 0 or left < 0 or bottom <= top or right <= left:
        raise ValueError("roi_bounds must define a non-empty half-open region")
    return top, left, bottom, right


@dataclass(frozen=True, slots=True)
class CoarseToFinePlan:
    """Validated inclusive micrometre plan on one global fine-step grid."""

    start: float
    stop: float
    coarse_step: float
    fine_step: float
    fine_radius: float

    def __post_init__(self) -> None:
        for name in (
            "start",
            "stop",
            "coarse_step",
            "fine_step",
            "fine_radius",
        ):
            object.__setattr__(
                self,
                name,
                _finite_real(getattr(self, name), name=name),
            )
        _plan_indices(self)


@dataclass(frozen=True, slots=True)
class FocusSample:
    """One finite numerical focus score at one finite depth."""

    depth_um: float
    score: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "depth_um",
            _finite_real(self.depth_um, name="depth_um"),
        )
        object.__setattr__(
            self,
            "score",
            _finite_real(self.score, name="score"),
        )


@dataclass(frozen=True, slots=True)
class AutofocusResult:
    """A ``NUMERICAL_NAVIGATION_ONLY`` coarse-to-fine ranking result."""

    metric_name: str
    metric_version: str
    metric_direction: FocusDirection
    coarse_samples: tuple[FocusSample, ...]
    fine_samples: tuple[FocusSample, ...]
    best_depth_um: float
    best_score: float
    roi_bounds: tuple[int, int, int, int] | None
    result_type: str = field(default=NUMERICAL_NAVIGATION_ONLY, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.metric_name, str) or not self.metric_name.strip():
            raise ValueError("metric_name must be a non-empty string")
        if not isinstance(self.metric_version, str) or not self.metric_version.strip():
            raise ValueError("metric_version must be a non-empty string")
        if not isinstance(self.metric_direction, FocusDirection):
            raise ValueError("metric_direction must be a FocusDirection")
        try:
            coarse_samples = tuple(self.coarse_samples)
            fine_samples = tuple(self.fine_samples)
        except TypeError as exc:
            raise ValueError("coarse and fine samples must be iterable") from exc
        if not coarse_samples or not fine_samples:
            raise ValueError("coarse and fine samples must not be empty")
        if any(not isinstance(sample, FocusSample) for sample in coarse_samples):
            raise ValueError("coarse_samples must contain only FocusSample values")
        if any(not isinstance(sample, FocusSample) for sample in fine_samples):
            raise ValueError("fine_samples must contain only FocusSample values")
        object.__setattr__(self, "coarse_samples", coarse_samples)
        object.__setattr__(self, "fine_samples", fine_samples)
        best_depth = _finite_real(self.best_depth_um, name="best_depth_um")
        best_score = _finite_real(self.best_score, name="best_score")
        object.__setattr__(self, "best_depth_um", best_depth)
        object.__setattr__(self, "best_score", best_score)
        expected = _select_best(fine_samples, self.metric_direction)
        if expected.depth_um != best_depth or expected.score != best_score:
            raise ValueError(
                "best depth and score must identify the selected fine sample"
            )
        object.__setattr__(
            self,
            "roi_bounds",
            _canonical_roi_bounds(self.roi_bounds),
        )


def _plan_indices(
    plan: CoarseToFinePlan,
) -> tuple[tuple[int, ...], int, int, int]:
    start = plan.start
    stop = plan.stop
    coarse_step = plan.coarse_step
    fine_step = plan.fine_step
    radius = plan.fine_radius

    if stop < start:
        raise ValueError("stop must be greater than or equal to start")
    if coarse_step <= 0.0:
        raise ValueError("coarse_step must be greater than zero")
    if fine_step <= 0.0:
        raise ValueError("fine_step must be greater than zero")
    if fine_step >= coarse_step:
        raise ValueError("fine_step must be smaller than coarse_step")
    if radius < 0.0:
        raise ValueError("fine_radius must be greater than or equal to zero")

    coarse_stride = _integer_ratio(
        coarse_step,
        fine_step,
        name="coarse_step / fine_step",
    )
    coarse_intervals = _integer_ratio(
        stop - start,
        coarse_step,
        name="depth range / coarse_step",
    )
    radius_steps = _integer_ratio(
        radius,
        fine_step,
        name="fine_radius / fine_step",
    )
    coarse_count = coarse_intervals + 1
    if coarse_count > _MAX_UNIQUE_PLANES:
        raise ValueError(
            f"autofocus plan exceeds {_MAX_UNIQUE_PLANES} unique planes"
        )

    fine_span = coarse_intervals * coarse_stride
    if fine_span > _MAX_EXACT_FINE_GRID_INDEX:
        raise ValueError(
            "autofocus fine grid exceeds the exact float indexing limit"
        )
    if fine_span:
        first_fine_depth = start + fine_step
        last_fine_depth = stop - fine_step
        maximum_float_spacing = max(
            float(np.nextafter(start, np.inf) - start),
            float(stop - np.nextafter(stop, -np.inf)),
        )
        if not (
            np.isfinite(first_fine_depth)
            and np.isfinite(last_fine_depth)
            and start < first_fine_depth <= stop
            and start <= last_fine_depth < stop
            and fine_step >= maximum_float_spacing
        ):
            raise ValueError(
                "fine_step is too small to produce distinct float depths"
            )
    coarse_indices = tuple(
        interval * coarse_stride for interval in range(coarse_count)
    )

    maximum_unique = 0
    for coarse_index in coarse_indices:
        fine_low = max(0, coarse_index - radius_steps)
        fine_high = min(fine_span, coarse_index + radius_steps)
        fine_count = fine_high - fine_low + 1
        first_overlap = (
            (fine_low + coarse_stride - 1) // coarse_stride
        ) * coarse_stride
        if first_overlap > fine_high:
            overlap_count = 0
        else:
            overlap_count = (fine_high - first_overlap) // coarse_stride + 1
        maximum_unique = max(
            maximum_unique,
            coarse_count + fine_count - overlap_count,
        )
    if maximum_unique > _MAX_UNIQUE_PLANES:
        raise ValueError(
            f"autofocus plan exceeds {_MAX_UNIQUE_PLANES} unique planes"
        )

    return coarse_indices, fine_span, coarse_stride, radius_steps


def _select_best(
    samples: tuple[FocusSample, ...],
    direction: FocusDirection,
) -> FocusSample:
    if direction is FocusDirection.MAXIMIZE:
        return max(samples, key=lambda sample: (sample.score, -sample.depth_um))
    if direction is FocusDirection.MINIMIZE:
        return min(samples, key=lambda sample: (sample.score, sample.depth_um))
    raise ValueError("focus metric direction must be a FocusDirection")


def _depth_at_index(
    plan: CoarseToFinePlan,
    index: int,
    fine_span: int,
) -> float:
    if index == 0:
        return plan.start
    if index == fine_span:
        return plan.stop
    return float(plan.start + index * plan.fine_step)


def _validated_depths(
    plan: CoarseToFinePlan,
    indices: tuple[int, ...],
    fine_span: int,
) -> tuple[float, ...]:
    depths = tuple(_depth_at_index(plan, index, fine_span) for index in indices)
    if any(current <= previous for previous, current in zip(depths, depths[1:])):
        raise ValueError("autofocus grid must map to distinct increasing depths")
    return depths


def coarse_to_fine_autofocus(
    prepared: PreparedWavefront,
    plan: CoarseToFinePlan,
    metric: FocusMetric,
    *,
    roi_bounds: tuple[int, int, int, int] | None = None,
) -> AutofocusResult:
    """Return a deterministic ``NUMERICAL_NAVIGATION_ONLY`` focus ranking.

    All depths are derived from integer indices on the global fine-step grid
    anchored at ``start``.  A score cache ensures that a depth shared
    by the coarse and fine stages calls ``prepared.amplitude_at`` only once.
    """

    if not isinstance(plan, CoarseToFinePlan):
        raise TypeError("plan must be a CoarseToFinePlan")
    coarse_indices, fine_span, _coarse_stride, radius_steps = _plan_indices(plan)
    cache: dict[int, FocusSample] = {}

    def sample(index: int, depth: float) -> FocusSample:
        existing = cache.get(index)
        if existing is not None:
            return existing
        amplitude = prepared.amplitude_at(depth)
        score = evaluate_focus_metric(
            amplitude,
            metric,
            roi_bounds=roi_bounds,
        )
        measured = FocusSample(depth_um=depth, score=score)
        cache[index] = measured
        return measured

    coarse_depths = _validated_depths(plan, coarse_indices, fine_span)
    depth_owners = dict(zip(coarse_depths, coarse_indices, strict=True))
    coarse_samples = tuple(
        sample(index, depth)
        for index, depth in zip(coarse_indices, coarse_depths, strict=True)
    )
    direction = getattr(metric, "direction", None)
    if not isinstance(direction, FocusDirection):
        raise ValueError("focus metric direction must be a FocusDirection")
    coarse_best = _select_best(coarse_samples, direction)
    coarse_best_index = next(
        index
        for index, measured in zip(coarse_indices, coarse_samples, strict=True)
        if measured is coarse_best
    )

    fine_low = max(0, coarse_best_index - radius_steps)
    fine_high = min(fine_span, coarse_best_index + radius_steps)
    fine_indices = tuple(range(fine_low, fine_high + 1))
    fine_depths = _validated_depths(plan, fine_indices, fine_span)
    for index, depth in zip(fine_indices, fine_depths, strict=True):
        owner = depth_owners.get(depth)
        if owner is not None and owner != index:
            raise ValueError("autofocus grid must map to distinct increasing depths")
    fine_samples = tuple(
        sample(index, depth)
        for index, depth in zip(fine_indices, fine_depths, strict=True)
    )
    best = _select_best(fine_samples, direction)

    return AutofocusResult(
        metric_name=metric.name,
        metric_version=metric.version,
        metric_direction=direction,
        coarse_samples=coarse_samples,
        fine_samples=fine_samples,
        best_depth_um=best.depth_um,
        best_score=best.score,
        roi_bounds=_canonical_roi_bounds(roi_bounds),
    )


__all__ = [
    "AutofocusResult",
    "CoarseToFinePlan",
    "FocusSample",
    "coarse_to_fine_autofocus",
]
