from __future__ import annotations

import csv
import hashlib
import json
import os
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from lensless_holography_workbench.depth_scan import PropagationParameters, evaluate_depth_scan


REPOSITORY = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = (
    REPOSITORY.parent.parent
    / "003-Proje Kayıtları"
    / "deneyler"
    / "15.11.2026"
)
DEFAULT_K03 = EXPERIMENT_ROOT / "source-package"
REQUIRED_RELATIVE_PATHS = (
    Path("veri/image01.jpg"),
    Path("veri/bg01.jpg"),
    Path("veri/bg02.jpg"),
    Path("veri/bg03.jpg"),
    Path("sonuclar/hologram.csv"),
    Path("sonuclar/ozet.json"),
)
EXPECTED_INPUT_SHA256 = {
    "bg01.jpg": "ef60eea4ac055f4b71781092eae0c8f227fa0f5a31208907bc33b1c41cf7667b",
    "bg02.jpg": "4636c89ef144ba53b074654b7f27ec2bc0330d9d9c5176891397fa827851f196",
    "bg03.jpg": "d8dec73658316929696d73188e89e0a2b0bd0f56176121abd511da98752e7c8e",
    "image01.jpg": "b313374f90ec2ef124e815e0071b427af418d8c12238a6174c93817d4a7d5a01",
}


def _load_grayscale(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("L"), dtype=np.float64)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_required_files(candidate: Path) -> bool:
    return candidate.is_dir() and all((candidate / relative).is_file() for relative in REQUIRED_RELATIVE_PATHS)


def _resolve_source_package() -> Path | None:
    configured = os.environ.get("K03_SOURCE_PACKAGE")
    if configured:
        candidate = Path(configured)
        return candidate if _contains_required_files(candidate) else None
    if _contains_required_files(DEFAULT_K03):
        return DEFAULT_K03
    runs = EXPERIMENT_ROOT / "runs"
    if runs.is_dir():
        for candidate in sorted((path for path in runs.rglob("*") if path.is_dir()), reverse=True):
            if _contains_required_files(candidate):
                return candidate
    return None


class K03RealDataReproducibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = _resolve_source_package()
        if cls.source is None:
            raise unittest.SkipTest("hash-verified K03 inputs are not present")

    def test_all_21_metrics_match_k03_golden_values(self) -> None:
        for name, expected_sha256 in EXPECTED_INPUT_SHA256.items():
            self.assertEqual(_sha256(self.source / "veri" / name), expected_sha256)
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

