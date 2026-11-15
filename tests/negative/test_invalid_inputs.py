from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from lensless_holography_workbench.comparison import compare_images
from lensless_holography_workbench.depth_scan import PropagationParameters, evaluate_depth_scan, normalize_hologram
from lensless_holography_workbench.session import export_session


class InvalidInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parameters = PropagationParameters(
            pixel_pitch_um=1.0,
            wavelength_um=0.5,
            refractive_index=1.0,
            depth_start_um=0.0,
            depth_stop_um=2.0,
            depth_step_um=1.0,
            roi_size_px=8,
        )

    def test_non_finite_signal_is_rejected(self) -> None:
        signal = np.ones((16, 16))
        signal[0, 0] = np.nan
        with self.assertRaisesRegex(ValueError, "finite"):
            normalize_hologram(signal, [np.ones((16, 16))])

    def test_non_finite_reference_is_rejected(self) -> None:
        reference = np.ones((8, 8))
        reference[0, 0] = np.inf
        with self.assertRaisesRegex(ValueError, "finite"):
            compare_images(np.ones((8, 8)), reference)

    def test_unscanned_selected_depth_is_rejected(self) -> None:
        prepared, scan = evaluate_depth_scan(
            np.ones((16, 16)),
            [np.ones((16, 16))],
            self.parameters,
        )
        temporary_root = Path.cwd() / ".test-tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as temporary:
            source = Path(temporary) / "source.bin"
            source.write_bytes(b"source")
            with self.assertRaisesRegex(ValueError, "scanned depths"):
                export_session(
                    Path(temporary) / "session",
                    prepared=prepared,
                    scan=scan,
                    parameters=self.parameters,
                    selected_depth_um=0.5,
                    inputs={"signal": source},
                )


if __name__ == "__main__":
    unittest.main()

