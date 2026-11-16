from __future__ import annotations

import unittest
from collections import Counter
from dataclasses import dataclass

import numpy as np

from lensless_holography_workbench import (
    AutofocusResult,
    BrennerFocusMetric,
    CoarseToFinePlan,
    FocusDirection,
    PreparedWavefront,
    PropagationParameters,
    coarse_to_fine_autofocus,
)


class SyntheticPrepared:
    def __init__(self, peak_um: float) -> None:
        self.peak_um = peak_um
        self.calls: Counter[float] = Counter()
        _y, self._x = np.indices((7, 9), dtype=np.float64)

    def amplitude_at(self, depth_um: float) -> np.ndarray:
        depth = float(depth_um)
        self.calls[depth] += 1
        scale = max(0.0, 20.0 - (depth - self.peak_um) ** 2)
        return scale * self._x


@dataclass(frozen=True)
class DistanceMetric:
    target_um: float
    direction: FocusDirection
    name: str = "distance"
    version: str = "1.0"

    def evaluate(self, amplitude: np.ndarray) -> float:
        depth = float(amplitude[0, 0])
        return abs(depth - self.target_um)


@dataclass(frozen=True)
class ConstantMetric:
    name: str = "constant"
    version: str = "1.0"
    direction: FocusDirection = FocusDirection.MAXIMIZE

    def evaluate(self, _amplitude: np.ndarray) -> float:
        return 1.0


@dataclass(frozen=True)
class ShapeMetric:
    name: str = "shape"
    version: str = "1.0"
    direction: FocusDirection = FocusDirection.MAXIMIZE

    def evaluate(self, amplitude: np.ndarray) -> float:
        return float(amplitude.shape[0] * 100 + amplitude.shape[1])


class DepthPrepared:
    def __init__(self) -> None:
        self.calls: Counter[float] = Counter()

    def amplitude_at(self, depth_um: float) -> np.ndarray:
        depth = float(depth_um)
        self.calls[depth] += 1
        return np.full((3, 3), depth, dtype=np.float64)


class AutofocusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = CoarseToFinePlan(
            start=0.0,
            stop=10.0,
            coarse_step=2.0,
            fine_step=0.5,
            fine_radius=2.0,
        )

    def test_expected_grids_peak_and_unique_call_count(self) -> None:
        prepared = SyntheticPrepared(peak_um=5.5)

        result = coarse_to_fine_autofocus(
            prepared,  # type: ignore[arg-type]
            self.plan,
            BrennerFocusMetric(),
        )

        self.assertEqual(
            [sample.depth_um for sample in result.coarse_samples],
            [0.0, 2.0, 4.0, 6.0, 8.0, 10.0],
        )
        self.assertEqual(
            [sample.depth_um for sample in result.fine_samples],
            [4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0],
        )
        self.assertEqual(result.best_depth_um, 5.5)
        self.assertEqual(sum(prepared.calls.values()), 12)
        self.assertEqual(set(prepared.calls.values()), {1})
        self.assertEqual(result.result_type, "NUMERICAL_NAVIGATION_ONLY")
        self.assertFalse(hasattr(result, "acceptance_result"))

    def test_fine_window_is_clipped_at_range_edge(self) -> None:
        prepared = SyntheticPrepared(peak_um=0.5)

        result = coarse_to_fine_autofocus(
            prepared,  # type: ignore[arg-type]
            self.plan,
            BrennerFocusMetric(),
        )

        self.assertEqual(
            [sample.depth_um for sample in result.fine_samples],
            [0.0, 0.5, 1.0, 1.5, 2.0],
        )
        self.assertEqual(result.best_depth_um, 0.5)
        self.assertEqual(sum(prepared.calls.values()), 9)

        upper_prepared = SyntheticPrepared(peak_um=9.5)
        upper = coarse_to_fine_autofocus(
            upper_prepared,  # type: ignore[arg-type]
            self.plan,
            BrennerFocusMetric(),
        )

        self.assertEqual(
            [sample.depth_um for sample in upper.fine_samples],
            [8.0, 8.5, 9.0, 9.5, 10.0],
        )
        self.assertEqual(upper.best_depth_um, 9.5)
        self.assertEqual(sum(upper_prepared.calls.values()), 9)

    def test_minimize_direction_is_supported(self) -> None:
        prepared = DepthPrepared()
        metric = DistanceMetric(1.5, FocusDirection.MINIMIZE)
        plan = CoarseToFinePlan(0.0, 4.0, 2.0, 0.5, 2.0)

        result = coarse_to_fine_autofocus(
            prepared,  # type: ignore[arg-type]
            plan,
            metric,
        )

        self.assertEqual(result.best_depth_um, 1.5)
        self.assertEqual(result.best_score, 0.0)
        self.assertIs(result.metric_direction, FocusDirection.MINIMIZE)

    def test_exact_ties_select_the_lowest_depth(self) -> None:
        prepared = DepthPrepared()
        metric = ConstantMetric()
        plan = CoarseToFinePlan(0.0, 4.0, 2.0, 0.5, 2.0)

        result = coarse_to_fine_autofocus(
            prepared,  # type: ignore[arg-type]
            plan,
            metric,
        )

        self.assertEqual(result.best_depth_um, 0.0)
        self.assertTrue(all(sample.score == 1.0 for sample in result.fine_samples))

        minimize = coarse_to_fine_autofocus(
            DepthPrepared(),  # type: ignore[arg-type]
            plan,
            ConstantMetric(direction=FocusDirection.MINIMIZE),
        )
        self.assertEqual(minimize.best_depth_um, 0.0)

    def test_roi_is_forwarded_to_metric_evaluation(self) -> None:
        prepared = SyntheticPrepared(peak_um=5.5)

        result = coarse_to_fine_autofocus(
            prepared,  # type: ignore[arg-type]
            self.plan,
            ShapeMetric(),
            roi_bounds=(1, 2, 6, 8),
        )

        self.assertEqual(result.roi_bounds, (1, 2, 6, 8))
        self.assertEqual(result.best_score, 506.0)

    def test_repeat_is_deterministic(self) -> None:
        first = coarse_to_fine_autofocus(
            SyntheticPrepared(5.5),  # type: ignore[arg-type]
            self.plan,
            BrennerFocusMetric(),
        )
        second = coarse_to_fine_autofocus(
            SyntheticPrepared(5.5),  # type: ignore[arg-type]
            self.plan,
            BrennerFocusMetric(),
        )

        self.assertEqual(first, second)

    def test_small_real_prepared_wavefront(self) -> None:
        y, x = np.indices((8, 10), dtype=np.float64)
        normalized = 1.0 + 0.1 * np.cos(2.0 * np.pi * x / 5.0) + 0.05 * y
        parameters = PropagationParameters(
            pixel_pitch_um=1.0,
            wavelength_um=0.5,
            refractive_index=1.0,
            depth_start_um=0.0,
            depth_stop_um=2.0,
            depth_step_um=1.0,
            roi_size_px=6,
        )
        prepared = PreparedWavefront.from_normalized(normalized, parameters)
        plan = CoarseToFinePlan(0.0, 2.0, 1.0, 0.5, 1.0)

        first = coarse_to_fine_autofocus(
            prepared,
            plan,
            BrennerFocusMetric(),
        )
        second = coarse_to_fine_autofocus(
            prepared,
            plan,
            BrennerFocusMetric(),
        )

        self.assertEqual(first, second)
        self.assertIn(first.best_depth_um, [0.0, 0.5, 1.0, 1.5, 2.0])
        self.assertTrue(np.isfinite(first.best_score))

    def test_invalid_plans_fail_closed(self) -> None:
        invalid = (
            (np.nan, 10.0, 2.0, 0.5, 2.0),
            (0.0, np.inf, 2.0, 0.5, 2.0),
            (-1e308, 1e308, 2.0, 0.5, 2.0),
            (0.0, 1.0, 1.0, np.nextafter(0.0, 1.0), 0.0),
            (10.0, 0.0, 2.0, 0.5, 2.0),
            (0.0, 10.0, 0.0, 0.5, 2.0),
            (0.0, 10.0, 2.0, 0.0, 2.0),
            (0.0, 10.0, 2.0, 2.0, 2.0),
            (0.0, 10.0, 2.0, 0.3, 2.0),
            (0.0, 9.0, 2.0, 0.5, 2.0),
            (0.0, 10.0, 2.0, 0.5, 2.1),
            (0.0, 10.0, 2.0, 0.5, -0.5),
            (0.0, 19_998.0, 2.0, 1.0, 1.0),
            (0.0, 3.0, 1.0, 1e-308, 0.0),
            (1e16, 1e16 + 4.0, 2.0, 0.5, 1.0),
            (float(2**53), float(2**53 + 22), 11.0, 1.1, 11.0),
        )

        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    CoarseToFinePlan(*values)

    def test_single_depth_range_is_valid(self) -> None:
        plan = CoarseToFinePlan(3.0, 3.0, 2.0, 0.5, 0.0)
        prepared = SyntheticPrepared(peak_um=3.0)

        result = coarse_to_fine_autofocus(
            prepared,  # type: ignore[arg-type]
            plan,
            BrennerFocusMetric(),
        )

        self.assertEqual(result.coarse_samples, result.fine_samples)
        self.assertEqual(result.best_depth_um, 3.0)
        self.assertEqual(sum(prepared.calls.values()), 1)

    def test_result_is_frozen_and_validates_best_sample(self) -> None:
        result = coarse_to_fine_autofocus(
            SyntheticPrepared(5.5),  # type: ignore[arg-type]
            self.plan,
            BrennerFocusMetric(),
        )
        with self.assertRaises((AttributeError, TypeError)):
            result.best_depth_um = 2.0  # type: ignore[misc]

        with self.assertRaisesRegex(ValueError, "fine sample"):
            AutofocusResult(
                metric_name=result.metric_name,
                metric_version=result.metric_version,
                metric_direction=result.metric_direction,
                coarse_samples=result.coarse_samples,
                fine_samples=result.fine_samples,
                best_depth_um=3.25,
                best_score=result.best_score,
                roi_bounds=None,
            )

        with self.assertRaisesRegex(ValueError, "selected fine sample"):
            AutofocusResult(
                metric_name=result.metric_name,
                metric_version=result.metric_version,
                metric_direction=result.metric_direction,
                coarse_samples=result.coarse_samples,
                fine_samples=result.fine_samples,
                best_depth_um=result.fine_samples[0].depth_um,
                best_score=result.fine_samples[0].score,
                roi_bounds=None,
            )

        with self.assertRaisesRegex(ValueError, "half-open"):
            AutofocusResult(
                metric_name=result.metric_name,
                metric_version=result.metric_version,
                metric_direction=result.metric_direction,
                coarse_samples=result.coarse_samples,
                fine_samples=result.fine_samples,
                best_depth_um=result.best_depth_um,
                best_score=result.best_score,
                roi_bounds=(9, 9, 1, 1),
            )

        copied = AutofocusResult(
            metric_name=result.metric_name,
            metric_version=result.metric_version,
            metric_direction=result.metric_direction,
            coarse_samples=list(result.coarse_samples),  # type: ignore[arg-type]
            fine_samples=list(result.fine_samples),  # type: ignore[arg-type]
            best_depth_um=result.best_depth_um,
            best_score=result.best_score,
            roi_bounds=None,
        )
        self.assertIsInstance(copied.coarse_samples, tuple)
        self.assertIsInstance(copied.fine_samples, tuple)

    def test_invalid_roi_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "fit inside"):
            coarse_to_fine_autofocus(
                SyntheticPrepared(5.5),  # type: ignore[arg-type]
                self.plan,
                BrennerFocusMetric(),
                roi_bounds=(0, 0, 8, 9),
            )


if __name__ == "__main__":
    unittest.main()
