from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from lensless_holography_workbench import cli, desktop_workflow
from lensless_holography_workbench.image_io import load_grayscale_image


class ImageIoTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_root = Path.cwd() / ".test-tmp"
        temporary_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temporary_root)
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_cli_and_desktop_share_the_same_loader(self) -> None:
        self.assertIs(cli.load_grayscale_image, load_grayscale_image)
        self.assertIs(desktop_workflow.load_grayscale_image, load_grayscale_image)

        path = self.base / "rgb.png"
        pixels = np.array(
            [
                [[10, 120, 240], [250, 20, 80]],
                [[40, 200, 30], [90, 70, 50]],
            ],
            dtype=np.uint8,
        )
        Image.fromarray(pixels, mode="RGB").save(path)

        with Image.open(path) as image:
            expected = np.asarray(image.convert("L"), dtype=np.float64)
        np.testing.assert_array_equal(load_grayscale_image(path), expected)

    def test_preserves_16_bit_and_float_single_channel_values(self) -> None:
        integer_path = self.base / "integer.tiff"
        integer = np.array([[1000, 4095], [8192, 65535]], dtype=np.uint16)
        Image.fromarray(integer).save(integer_path)
        np.testing.assert_array_equal(load_grayscale_image(integer_path), integer)

        float_path = self.base / "float.tiff"
        floating = np.array([[0.125, 1.5], [12.25, 100.75]], dtype=np.float32)
        Image.fromarray(floating, mode="F").save(float_path)
        np.testing.assert_array_equal(load_grayscale_image(float_path), floating)


if __name__ == "__main__":
    unittest.main()
