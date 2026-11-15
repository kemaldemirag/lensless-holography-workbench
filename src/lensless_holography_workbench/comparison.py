"""Reference-comparison primitives without an embedded acceptance threshold."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _validated_image(array: np.ndarray, *, name: str) -> np.ndarray:
    image = np.asarray(array, dtype=np.float64)
    if image.ndim != 2 or image.size == 0:
        raise ValueError(f"{name} must be a non-empty two-dimensional image")
    if not np.isfinite(image).all():
        raise ValueError(f"{name} must contain only finite values")
    return image


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    difference: np.ndarray
    candidate_line: np.ndarray
    reference_line: np.ndarray
    difference_line: np.ndarray
    line_row: int
    rmse: float
    max_abs_difference: float

    def as_summary(self) -> dict[str, object]:
        return {
            "line_row": self.line_row,
            "rmse": self.rmse,
            "max_abs_difference": self.max_abs_difference,
            "acceptance_threshold": None,
            "acceptance_result": "NOT_EVALUATED",
        }


def compare_images(
    candidate: np.ndarray,
    reference: np.ndarray,
    *,
    line_row: int | None = None,
) -> ComparisonResult:
    """Compare already aligned images on the same crop and intensity scale."""

    candidate_image = _validated_image(candidate, name="candidate")
    reference_image = _validated_image(reference, name="reference")
    if candidate_image.shape != reference_image.shape:
        raise ValueError("candidate and reference images must have the same shape")

    if line_row is None:
        row = candidate_image.shape[0] // 2
    else:
        if isinstance(line_row, bool) or not isinstance(line_row, int):
            raise ValueError("line_row must be an integer")
        row = line_row
    if not 0 <= row < candidate_image.shape[0]:
        raise ValueError("line_row is outside the image")

    difference = candidate_image - reference_image
    return ComparisonResult(
        difference=difference,
        candidate_line=candidate_image[row].copy(),
        reference_line=reference_image[row].copy(),
        difference_line=difference[row].copy(),
        line_row=row,
        rmse=float(np.sqrt(np.mean(difference**2))),
        max_abs_difference=float(np.max(np.abs(difference))),
    )

