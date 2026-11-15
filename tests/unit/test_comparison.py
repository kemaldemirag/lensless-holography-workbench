from __future__ import annotations

import unittest

import numpy as np

from lensless_holography_workbench.comparison import compare_images


class ComparisonTests(unittest.TestCase):
    def test_reports_difference_and_center_line_without_acceptance(self) -> None:
        reference = np.arange(20, dtype=np.float64).reshape(4, 5)
        candidate = reference + 2.0

        result = compare_images(candidate, reference)

        np.testing.assert_array_equal(result.difference, np.full((4, 5), 2.0))
        np.testing.assert_array_equal(result.candidate_line, candidate[2])
        self.assertEqual(result.rmse, 2.0)
        self.assertEqual(result.max_abs_difference, 2.0)
        self.assertEqual(result.as_summary()["acceptance_result"], "NOT_EVALUATED")
        self.assertIsNone(result.as_summary()["acceptance_threshold"])

    def test_rejects_unaligned_shapes(self) -> None:
        with self.assertRaisesRegex(ValueError, "same shape"):
            compare_images(np.ones((4, 5)), np.ones((5, 4)))

    def test_rejects_invalid_line_row(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside"):
            compare_images(np.ones((4, 5)), np.ones((4, 5)), line_row=4)


if __name__ == "__main__":
    unittest.main()

