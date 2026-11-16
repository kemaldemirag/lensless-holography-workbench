from __future__ import annotations

import unittest
from dataclasses import dataclass

import numpy as np

from lensless_holography_workbench import (
    BrennerFocusMetric,
    FocusDirection,
    SobelVarianceFocusMetric,
    evaluate_focus_metric,
)


class FocusMetricTests(unittest.TestCase):
    def test_brenner_uses_symmetric_equal_axis_mean(self) -> None:
        y, x = np.indices((4, 5), dtype=np.float64)
        amplitude = 10.0 * y + x

        score = BrennerFocusMetric().evaluate(amplitude)

        self.assertEqual(score, 202.0)

    def test_sobel_variance_uses_explicit_valid_kernel(self) -> None:
        amplitude = np.zeros((5, 5), dtype=np.float64)
        amplitude[2, 2] = 1.0
        expected = (120.0 - 64.0 * np.sqrt(2.0)) / 81.0

        score = SobelVarianceFocusMetric().evaluate(amplitude)

        self.assertAlmostEqual(score, expected, places=15)

    def test_constant_plane_has_zero_scores(self) -> None:
        amplitude = np.full((7, 9), 4.5, dtype=np.float64)

        self.assertEqual(BrennerFocusMetric().evaluate(amplitude), 0.0)
        self.assertEqual(SobelVarianceFocusMetric().evaluate(amplitude), 0.0)

    def test_roi_is_half_open_and_ignores_outside_values(self) -> None:
        amplitude = np.full((8, 9), 1000.0, dtype=np.float64)
        y, x = np.indices((4, 5), dtype=np.float64)
        amplitude[2:6, 3:8] = 10.0 * y + x

        score = evaluate_focus_metric(
            amplitude,
            BrennerFocusMetric(),
            roi_bounds=(2, 3, 6, 8),
        )

        self.assertEqual(score, 202.0)

    def test_evaluation_does_not_mutate_input(self) -> None:
        amplitude = np.arange(42, dtype=np.float64).reshape(6, 7)
        original = amplitude.copy()

        evaluate_focus_metric(amplitude, SobelVarianceFocusMetric())

        np.testing.assert_array_equal(amplitude, original)

    def test_integer_and_float_inputs_have_identical_scores(self) -> None:
        integer = np.arange(30, dtype=np.uint16).reshape(5, 6)
        floating = integer.astype(np.float64)

        for metric in (BrennerFocusMetric(), SobelVarianceFocusMetric()):
            with self.subTest(metric=metric.name):
                self.assertEqual(metric.evaluate(integer), metric.evaluate(floating))

    def test_exact_three_by_three_plane_is_supported(self) -> None:
        amplitude = np.arange(9, dtype=np.float64).reshape(3, 3)

        self.assertTrue(np.isfinite(BrennerFocusMetric().evaluate(amplitude)))
        self.assertTrue(np.isfinite(SobelVarianceFocusMetric().evaluate(amplitude)))

    def test_builtin_metadata_is_versioned_and_maximized(self) -> None:
        metrics = (BrennerFocusMetric(), SobelVarianceFocusMetric())

        for metric in metrics:
            self.assertTrue(metric.name)
            self.assertEqual(metric.version, "1.0")
            self.assertIs(metric.direction, FocusDirection.MAXIMIZE)

        with self.assertRaises(TypeError):
            BrennerFocusMetric(name="changed")  # type: ignore[call-arg]

    def test_rejects_invalid_amplitude_inputs(self) -> None:
        metric = BrennerFocusMetric()
        invalid = (
            np.ones(3),
            np.empty((0, 3)),
            np.ones((2, 3)),
            np.array([[1.0, 2.0, 3.0], [4.0, np.nan, 6.0], [7.0, 8.0, 9.0]]),
            np.ones((3, 3), dtype=np.complex128),
            np.full((3, 3), 10**400, dtype=object),
        )

        for amplitude in invalid:
            with self.subTest(shape=amplitude.shape, dtype=str(amplitude.dtype)):
                with self.assertRaises(ValueError):
                    metric.evaluate(amplitude)

    def test_rejects_invalid_roi_bounds(self) -> None:
        amplitude = np.ones((8, 9), dtype=np.float64)
        invalid = (
            (0, 0, 3),
            [0, 0, 3, 3],
            (True, 0, 3, 3),
            (-1, 0, 3, 3),
            (0, 0, 0, 3),
            (0, 0, 9, 3),
            (0, 0, 3, 10),
            (0, 0, 2, 3),
        )

        for bounds in invalid:
            with self.subTest(bounds=bounds):
                with self.assertRaises(ValueError):
                    evaluate_focus_metric(
                        amplitude,
                        BrennerFocusMetric(),
                        roi_bounds=bounds,  # type: ignore[arg-type]
                    )

    def test_rejects_non_finite_builtin_score(self) -> None:
        amplitude = np.zeros((3, 3), dtype=np.float64)
        amplitude[:, 0] = np.finfo(np.float64).max
        amplitude[:, 2] = -np.finfo(np.float64).max

        with self.assertRaisesRegex(ValueError, "finite"):
            BrennerFocusMetric().evaluate(amplitude)

    def test_validates_protocol_metadata_and_result(self) -> None:
        @dataclass(frozen=True)
        class DummyMetric:
            name: str = "dummy"
            version: str = "1.0"
            direction: object = FocusDirection.MINIMIZE
            score: object = 1.25

            def evaluate(self, _amplitude: np.ndarray) -> object:
                return self.score

        amplitude = np.ones((3, 3), dtype=np.float64)
        self.assertEqual(evaluate_focus_metric(amplitude, DummyMetric()), 1.25)  # type: ignore[arg-type]

        invalid = (
            DummyMetric(name=""),
            DummyMetric(version=""),
            DummyMetric(direction="MAXIMIZE"),
            DummyMetric(score=np.inf),
            DummyMetric(score=True),
            DummyMetric(score=1.0 + 2.0j),
        )
        for metric in invalid:
            with self.subTest(metric=metric):
                with self.assertRaises(ValueError):
                    evaluate_focus_metric(amplitude, metric)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
