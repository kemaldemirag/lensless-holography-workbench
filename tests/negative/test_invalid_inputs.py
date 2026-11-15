from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from lensless_holography_workbench.comparison import compare_images
from lensless_holography_workbench.depth_scan import (
    DepthScanResult,
    PropagationParameters,
    evaluate_depth_scan,
    normalize_hologram,
)
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

    def test_invalid_reference_leaves_no_partial_session(self) -> None:
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
            session_directory = Path(temporary) / "session"
            with self.assertRaisesRegex(ValueError, "same shape"):
                export_session(
                    session_directory,
                    prepared=prepared,
                    scan=scan,
                    parameters=self.parameters,
                    selected_depth_um=1.0,
                    inputs={"signal": source},
                    reference=np.ones((7, 8)),
                )
            self.assertFalse(session_directory.exists())

    def test_mismatched_parameters_leave_no_partial_session(self) -> None:
        prepared, scan = evaluate_depth_scan(
            np.ones((16, 16)),
            [np.ones((16, 16))],
            self.parameters,
        )
        mismatched = PropagationParameters(
            pixel_pitch_um=1.0,
            wavelength_um=0.6,
            refractive_index=1.0,
            depth_start_um=0.0,
            depth_stop_um=2.0,
            depth_step_um=1.0,
            roi_size_px=8,
        )
        temporary_root = Path.cwd() / ".test-tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as temporary:
            source = Path(temporary) / "source.bin"
            source.write_bytes(b"source")
            session_directory = Path(temporary) / "session"
            with self.assertRaisesRegex(ValueError, "parameters do not match"):
                export_session(
                    session_directory,
                    prepared=prepared,
                    scan=scan,
                    parameters=mismatched,
                    selected_depth_um=1.0,
                    inputs={"signal": source},
                )
            self.assertFalse(session_directory.exists())

    def test_mismatched_scan_shape_leaves_no_partial_session(self) -> None:
        prepared, scan = evaluate_depth_scan(
            np.ones((16, 16)),
            [np.ones((16, 16))],
            self.parameters,
        )
        inconsistent_scan = DepthScanResult(
            metrics=scan.metrics,
            input_shape=(15, 16),
            roi_bounds=scan.roi_bounds,
            source_fingerprint=prepared.source_fingerprint,
        )
        temporary_root = Path.cwd() / ".test-tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as temporary:
            source = Path(temporary) / "source.bin"
            source.write_bytes(b"source")
            session_directory = Path(temporary) / "session"
            with self.assertRaisesRegex(ValueError, "input shape"):
                export_session(
                    session_directory,
                    prepared=prepared,
                    scan=inconsistent_scan,
                    parameters=self.parameters,
                    selected_depth_um=1.0,
                    inputs={"signal": source},
                )
            self.assertFalse(session_directory.exists())

    def test_scan_from_different_wavefront_leaves_no_partial_session(self) -> None:
        prepared, _ = evaluate_depth_scan(
            np.ones((16, 16)),
            [np.ones((16, 16))],
            self.parameters,
        )
        y, x = np.indices((16, 16), dtype=np.float64)
        different_signal = 1.0 + 0.1 * np.cos(2 * np.pi * x / 5.0) + 0.05 * np.sin(2 * np.pi * y / 7.0)
        _, different_scan = evaluate_depth_scan(
            different_signal,
            [np.ones((16, 16))],
            self.parameters,
        )
        self.assertNotEqual(prepared.source_fingerprint, different_scan.source_fingerprint)

        temporary_root = Path.cwd() / ".test-tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as temporary:
            source = Path(temporary) / "source.bin"
            source.write_bytes(b"source")
            session_directory = Path(temporary) / "session"
            with self.assertRaisesRegex(ValueError, "source fingerprint"):
                export_session(
                    session_directory,
                    prepared=prepared,
                    scan=different_scan,
                    parameters=self.parameters,
                    selected_depth_um=1.0,
                    inputs={"signal": source},
                )
            self.assertFalse(session_directory.exists())


if __name__ == "__main__":
    unittest.main()

