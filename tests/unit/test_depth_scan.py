from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

from lensless_holography_workbench.depth_scan import (
    PreparedWavefront,
    PropagationParameters,
    evaluate_depth_scan,
    normalize_hologram,
    scan_depths,
    write_metrics_csv,
)


def parameters(**overrides: object) -> PropagationParameters:
    values: dict[str, object] = {
        "pixel_pitch_um": 1.0,
        "wavelength_um": 0.5,
        "refractive_index": 1.0,
        "depth_start_um": 0.0,
        "depth_stop_um": 2.0,
        "depth_step_um": 1.0,
        "roi_size_px": 16,
    }
    values.update(overrides)
    return PropagationParameters(**values)  # type: ignore[arg-type]


class NormalizeHologramTests(unittest.TestCase):
    def test_scales_background_corrected_mean_to_one(self) -> None:
        signal = np.arange(1, 65, dtype=np.float64).reshape(8, 8)
        backgrounds = [np.full((8, 8), 2.0), np.full((8, 8), 4.0)]

        normalized = normalize_hologram(signal, backgrounds)

        self.assertAlmostEqual(float(normalized.mean()), 1.0, places=14)
        self.assertEqual(normalized.shape, signal.shape)

    def test_rejects_shape_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "same shape"):
            normalize_hologram(np.ones((8, 8)), [np.ones((7, 8))])

    def test_rejects_zero_background_pixel(self) -> None:
        background = np.ones((8, 8))
        background[3, 4] = 0
        with self.assertRaisesRegex(ValueError, "strictly positive"):
            normalize_hologram(np.ones((8, 8)), [background])


class DepthScanTests(unittest.TestCase):
    def test_constant_wavefront_has_zero_metrics_and_first_peak(self) -> None:
        signal = np.full((32, 32), 9.0)
        prepared, result = evaluate_depth_scan(signal, [np.full((32, 32), 3.0)], parameters())

        self.assertEqual(prepared.input_shape, (32, 32))
        self.assertEqual(len(result.metrics), 3)
        self.assertEqual(result.gradient_peak_um, 0.0)
        self.assertEqual(result.amplitude_std_peak_um, 0.0)
        for metric in result.metrics:
            self.assertAlmostEqual(metric.gradient_energy, 0.0, places=15)
            self.assertAlmostEqual(metric.amplitude_std, 0.0, places=15)

    def test_repeat_scan_is_bitwise_deterministic(self) -> None:
        y, x = np.indices((32, 32), dtype=np.float64)
        normalized = 1.0 + 0.1 * np.cos(2 * np.pi * x / 8.0) + 0.05 * np.sin(2 * np.pi * y / 7.0)
        prepared = PreparedWavefront.from_normalized(normalized, parameters())

        first = scan_depths(prepared, [0, 1, 2])
        second = scan_depths(prepared, [0, 1, 2])

        self.assertEqual(first, second)
        np.testing.assert_array_equal(prepared.amplitude_at(1), prepared.amplitude_at(1))

    def test_csv_writer_uses_stable_columns_and_rows(self) -> None:
        prepared = PreparedWavefront.from_normalized(np.ones((32, 32)), parameters())
        result = scan_depths(prepared, [0, 1, 2])
        temporary_root = Path.cwd() / ".test-tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as temporary:
            output = write_metrics_csv(result, Path(temporary) / "metrics.csv")
            with output.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual(list(rows[0]), ["depth_um", "gradient_energy", "amplitude_std"])
        self.assertEqual([float(row["depth_um"]) for row in rows], [0.0, 1.0, 2.0])

    def test_rejects_non_integral_depth_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "integer number"):
            parameters(depth_stop_um=2.5).depths_um()

    def test_rejects_roi_larger_than_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "fit inside"):
            PreparedWavefront.from_normalized(np.ones((8, 8)), parameters(roi_size_px=9))


if __name__ == "__main__":
    unittest.main()

