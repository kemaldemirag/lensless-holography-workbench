"""Versioned numerical focus metrics for reconstructed amplitude planes.

The scores in this module are navigation aids.  They do not establish a
physical focus, reconstruction-accuracy result, or customer acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from numbers import Integral
from typing import Protocol

import numpy as np


class FocusDirection(str, Enum):
    """Describe whether a metric selects its smallest or largest score."""

    MAXIMIZE = "MAXIMIZE"
    MINIMIZE = "MINIMIZE"


class FocusMetric(Protocol):
    """Structural interface implemented by versioned focus metrics."""

    name: str
    version: str
    direction: FocusDirection

    def evaluate(self, amplitude: np.ndarray) -> float:
        """Return one finite score for a two-dimensional amplitude plane."""


def _as_amplitude(array: np.ndarray) -> np.ndarray:
    source = np.asarray(array)
    if np.iscomplexobj(source):
        raise ValueError("amplitude must be real-valued")
    try:
        image = np.asarray(source, dtype=np.float64)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("amplitude must contain numeric values") from exc
    if image.ndim != 2:
        raise ValueError("amplitude must be a two-dimensional image")
    if image.size == 0:
        raise ValueError("amplitude must not be empty")
    if not np.isfinite(image).all():
        raise ValueError("amplitude must contain only finite values")
    return image


def _require_three_by_three(image: np.ndarray) -> None:
    if image.shape[0] < 3 or image.shape[1] < 3:
        raise ValueError("focus metric requires an image of at least 3 by 3 pixels")


def _finite_score(value: object) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError("focus metric score must be a real number")
    if np.iscomplexobj(value):
        raise ValueError("focus metric score must be real-valued")
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("focus metric score must be a real number") from exc
    if not np.isfinite(score):
        raise ValueError("focus metric score must be finite")
    return score


@dataclass(frozen=True, slots=True)
class BrennerFocusMetric:
    """Symmetric two-pixel Brenner score with equal x/y weighting.

    Version 1.0 is the arithmetic mean of the mean squared two-pixel
    difference on each axis.  Equal axis weighting keeps rectangular regions
    from silently weighting the longer axis more heavily.
    """

    name: str = field(default="brenner_symmetric_mean", init=False)
    version: str = field(default="1.0", init=False)
    direction: FocusDirection = field(default=FocusDirection.MAXIMIZE, init=False)

    def evaluate(self, amplitude: np.ndarray) -> float:
        image = _as_amplitude(amplitude)
        _require_three_by_three(image)
        with np.errstate(over="ignore", invalid="ignore"):
            horizontal = float(np.mean((image[:, 2:] - image[:, :-2]) ** 2))
            vertical = float(np.mean((image[2:, :] - image[:-2, :]) ** 2))
            score = 0.5 * (horizontal + vertical)
        return _finite_score(score)


@dataclass(frozen=True, slots=True)
class SobelVarianceFocusMetric:
    """Variance of a valid 3x3 Sobel gradient-magnitude plane.

    Version 1.0 applies the conventional unnormalised Sobel kernels without
    padding.  Only positions with a complete 3x3 neighbourhood contribute.
    """

    name: str = field(default="sobel_magnitude_variance_valid", init=False)
    version: str = field(default="1.0", init=False)
    direction: FocusDirection = field(default=FocusDirection.MAXIMIZE, init=False)

    def evaluate(self, amplitude: np.ndarray) -> float:
        image = _as_amplitude(amplitude)
        _require_three_by_three(image)

        top_left = image[:-2, :-2]
        top_middle = image[:-2, 1:-1]
        top_right = image[:-2, 2:]
        middle_left = image[1:-1, :-2]
        middle_right = image[1:-1, 2:]
        bottom_left = image[2:, :-2]
        bottom_middle = image[2:, 1:-1]
        bottom_right = image[2:, 2:]

        with np.errstate(over="ignore", invalid="ignore"):
            gradient_x = (
                -top_left
                + top_right
                - 2.0 * middle_left
                + 2.0 * middle_right
                - bottom_left
                + bottom_right
            )
            gradient_y = (
                -top_left
                - 2.0 * top_middle
                - top_right
                + bottom_left
                + 2.0 * bottom_middle
                + bottom_right
            )
            magnitude = np.hypot(gradient_x, gradient_y)
            score = float(np.var(magnitude, dtype=np.float64))
        return _finite_score(score)


def _validated_roi(
    shape: tuple[int, int],
    roi_bounds: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int]:
    height, width = shape
    if roi_bounds is None:
        return 0, 0, height, width
    if not isinstance(roi_bounds, tuple) or len(roi_bounds) != 4:
        raise ValueError("roi_bounds must be a four-integer tuple")
    if any(isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral) for value in roi_bounds):
        raise ValueError("roi_bounds must contain only integers")
    top, left, bottom, right = (int(value) for value in roi_bounds)
    if top < 0 or left < 0 or bottom <= top or right <= left:
        raise ValueError("roi_bounds must define a non-empty half-open region")
    if bottom > height or right > width:
        raise ValueError("roi_bounds must fit inside the amplitude image")
    return top, left, bottom, right


def evaluate_focus_metric(
    amplitude: np.ndarray,
    metric: FocusMetric,
    *,
    roi_bounds: tuple[int, int, int, int] | None = None,
) -> float:
    """Evaluate a metric on a full plane or validated half-open ROI."""

    image = _as_amplitude(amplitude)
    top, left, bottom, right = _validated_roi(image.shape, roi_bounds)

    name = getattr(metric, "name", None)
    version = getattr(metric, "version", None)
    direction = getattr(metric, "direction", None)
    evaluator = getattr(metric, "evaluate", None)
    if not isinstance(name, str) or not name.strip():
        raise ValueError("focus metric name must be a non-empty string")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("focus metric version must be a non-empty string")
    if not isinstance(direction, FocusDirection):
        raise ValueError("focus metric direction must be a FocusDirection")
    if not callable(evaluator):
        raise ValueError("focus metric must provide an evaluate method")

    return _finite_score(evaluator(image[top:bottom, left:right]))


__all__ = [
    "BrennerFocusMetric",
    "FocusDirection",
    "FocusMetric",
    "SobelVarianceFocusMetric",
    "evaluate_focus_metric",
]
