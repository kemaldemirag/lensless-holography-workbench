from __future__ import annotations

import importlib
import sys
import tomllib
import unittest
from pathlib import Path

from lensless_holography_workbench.depth_scan import DepthMetric


class WindowsAppHelpersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = importlib.import_module(
            "lensless_holography_workbench.windows_app"
        )

    def _defaults(self) -> dict[str, str]:
        return {
            name: default
            for name, _label, default in self.app.PARAMETER_FIELDS
        }

    def test_import_does_not_import_tkinter_or_create_a_root(self) -> None:
        # On a fresh interpreter the module has no reason to import Tk at all.
        # Other test runners may already have imported it, so also assert the
        # workbench module carries no module-level root object.
        self.assertNotIn("root", vars(self.app))
        self.assertNotIn("_default_root", vars(self.app))
        if "tkinter" not in sys.modules:
            self.assertNotIn("tkinter", sys.modules)

    def test_parse_parameters_returns_validated_record(self) -> None:
        parameters = self.app.parse_parameters(self._defaults())

        self.assertEqual(parameters.pixel_pitch_um, 0.0851)
        self.assertEqual(parameters.wavelength_um, 0.66)
        self.assertEqual(parameters.refractive_index, 1.33)
        self.assertEqual(parameters.depths_um().tolist(), list(range(21)))
        self.assertEqual(parameters.roi_size_px, 256)

    def test_parse_parameters_rejects_missing_non_numeric_and_invalid_grid(self) -> None:
        values = self._defaults()
        del values["wavelength_um"]
        with self.assertRaisesRegex(ValueError, "missing parameter fields"):
            self.app.parse_parameters(values)

        values = self._defaults()
        values["pixel_pitch_um"] = "not-a-number"
        with self.assertRaisesRegex(ValueError, "must be a number"):
            self.app.parse_parameters(values)

        values = self._defaults()
        values["roi_size_px"] = "256.0"
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            self.app.parse_parameters(values)

        values = self._defaults()
        values["depth_step_um"] = "3"
        with self.assertRaisesRegex(ValueError, "integer number of steps"):
            self.app.parse_parameters(values)

    def test_parse_parameters_enforces_desktop_resource_limits(self) -> None:
        values = self._defaults()
        values["depth_stop_um"] = "1000"
        with self.assertRaisesRegex(ValueError, "maximum is 1000"):
            self.app.parse_parameters(values)

        values = self._defaults()
        values["roi_size_px"] = "4096"
        with self.assertRaisesRegex(ValueError, "512 MiB"):
            self.app.parse_parameters(values)

    def test_status_and_warning_preserve_claim_boundary(self) -> None:
        self.assertEqual(
            self.app.STATUS_BANNER,
            "U02-T2 · BEKLİYOR · NOT_EVALUATED",
        )
        self.assertIn(
            "sayısal tepe fiziksel odak değildir",
            self.app.FOCUS_WARNING,
        )
        self.assertNotIn("PASS", self.app.STATUS_BANNER)

    def test_metric_formatting_distinguishes_reopened_session(self) -> None:
        metric = DepthMetric(
            depth_um=2.5,
            gradient_energy=0.125,
            amplitude_std=0.25,
        )

        text = self.app.format_metric(metric)

        self.assertIn("2.5 µm", text)
        self.assertIn("0.125", text)
        self.assertIn("0.25", text)
        self.assertIn("yeniden hesaplanmadı", self.app.format_metric(None))

    def test_nearest_depth_index_clamps_and_rejects_bad_values(self) -> None:
        depths = (0.0, 1.0, 2.0)
        self.assertEqual(self.app.nearest_depth_index(depths, -4.0), 0)
        self.assertEqual(self.app.nearest_depth_index(depths, 1.4), 1)
        self.assertEqual(self.app.nearest_depth_index(depths, 9.0), 2)
        with self.assertRaisesRegex(ValueError, "at least one"):
            self.app.nearest_depth_index((), 0.0)
        with self.assertRaisesRegex(ValueError, "finite"):
            self.app.nearest_depth_index(depths, float("nan"))

    def test_scan_signature_tracks_inputs_parameters_and_reference(self) -> None:
        parameters = self.app.parse_parameters(self._defaults())
        first = self.app.scan_configuration_signature(
            "signal.tiff",
            [Path("background.tiff")],
            None,
            parameters,
        )
        second = self.app.scan_configuration_signature(
            "signal.tiff",
            [Path("background.tiff")],
            "reference.tiff",
            parameters,
        )

        self.assertNotEqual(first, second)

    def test_pyproject_declares_gui_entrypoint(self) -> None:
        root = Path(__file__).resolve().parents[2]
        metadata = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))

        self.assertEqual(
            metadata["project"]["gui-scripts"]["holography-workbench-gui"],
            "lensless_holography_workbench.windows_app:main",
        )
        self.assertEqual(metadata["project"]["version"], "0.2.0")
        self.assertIn("Windows desktop shell", metadata["project"]["description"])


if __name__ == "__main__":
    unittest.main()
