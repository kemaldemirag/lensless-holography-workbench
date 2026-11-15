from __future__ import annotations

import csv
import json
import os
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from lensless_holography_workbench.depth_scan import PropagationParameters, evaluate_depth_scan


REPOSITORY = Path(__file__).resolve().parents[2]
DEFAULT_K03 = (
    REPOSITORY.parent.parent
    / "003-Proje Kayıtları"
    / "deneyler"
    / "15.11.2026"
    / "source-package"
)


def _load_grayscale(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("L"), dtype=np.float64)


class K03RealDataReproducibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = Path(os.environ.get("K03_SOURCE_PACKAGE", DEFAULT_K03))
        required = [
            cls.source / "veri" / "image01.jpg",
            cls.source / "veri" / "bg01.jpg",
            cls.source / "veri" / "bg02.jpg",
            cls.source / "veri" / "bg03.jpg",
            cls.source / "sonuclar" / "hologram.csv",
            cls.source / "sonuclar" / "ozet.json",
        ]
        if not all(path.is_file() for path in required):
            raise unittest.SkipTest("hash-verified K03 inputs are not present")

    def test_all_21_metrics_match_k03_golden_values(self) -> None:
        signal = _load_grayscale(self.source / "veri" / "image01.jpg")
        backgrounds = [_load_grayscale(self.source / "veri" / f"bg0{index}.jpg") for index in (1, 2, 3)]
        parameters = PropagationParameters(
            pixel_pitch_um=0.0851,
            wavelength_um=0.66,
            refractive_index=1.33,
            depth_start_um=0.0,
            depth_stop_um=20.0,
            depth_step_um=1.0,
            roi_size_px=256,
        )

        _, result = evaluate_depth_scan(signal, backgrounds, parameters)

        with (self.source / "sonuclar" / "hologram.csv").open(encoding="utf-8", newline="") as stream:
            expected_rows = list(csv.DictReader(stream))
        self.assertEqual(len(result.metrics), 21)
        self.assertEqual(len(expected_rows), 21)
        for actual, expected in zip(result.metrics, expected_rows, strict=True):
            self.assertEqual(actual.depth_um, float(expected["depth_um"]))
            self.assertLessEqual(abs(actual.gradient_energy - float(expected["gradient_energy"])), 1e-12)
            self.assertLessEqual(abs(actual.amplitude_std - float(expected["amplitude_std"])), 1e-12)
        self.assertEqual(result.gradient_peak_um, 0.0)
        self.assertEqual(result.amplitude_std_peak_um, 0.0)
        self.assertEqual(result.input_shape, (512, 512))
        self.assertEqual(result.roi_bounds, (128, 128, 384, 384))

        golden_summary = json.loads((self.source / "sonuclar" / "ozet.json").read_text(encoding="utf-8"))["hologram"]
        self.assertEqual(golden_summary["gradient_peak_um"], result.gradient_peak_um)
        self.assertEqual(golden_summary["contrast_peak_um"], result.amplitude_std_peak_um)
        self.assertEqual(golden_summary["planes"], len(result.metrics))


if __name__ == "__main__":
    unittest.main()

