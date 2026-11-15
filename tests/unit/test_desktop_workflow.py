from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from lensless_holography_workbench.depth_scan import PropagationParameters
from lensless_holography_workbench.desktop_workflow import (
    COMPARISON_RESULT,
    DesktopWorkflow,
    StaleWorkflowError,
    WorkflowState,
)
from lensless_holography_workbench.session import (
    U02_T2_STATUS,
    export_session as core_export_session,
    save_float_tiff,
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


class DesktopWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_root = Path.cwd() / ".test-tmp"
        self.temporary_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=self.temporary_root)
        self.base = Path(self.temporary.name)
        y, x = np.indices((32, 32), dtype=np.float64)
        signal = 120.0 + 10.0 * np.cos(2 * np.pi * x / 9.0)
        signal += 5.0 * np.sin(2 * np.pi * y / 11.0)
        self.signal = self.base / "signal.png"
        Image.fromarray(np.rint(signal).astype(np.uint8)).save(self.signal)
        self.backgrounds = []
        for index, value in enumerate((100, 102), start=1):
            path = self.base / f"background-{index}.png"
            Image.fromarray(
                np.full((32, 32), value, dtype=np.uint8)
            ).save(path)
            self.backgrounds.append(path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _configured(self, *, reference: Path | None = None) -> DesktopWorkflow:
        workflow = DesktopWorkflow(parameters=parameters())
        workflow.configure_inputs(
            self.signal,
            self.backgrounds,
            reference,
        )
        self.assertEqual(workflow.state, WorkflowState.READY)
        return workflow

    def _scan(
        self,
        workflow: DesktopWorkflow,
        *,
        progress: list[tuple[int, int]] | None = None,
    ) -> None:
        request = workflow.begin_scan()
        callback = (
            (lambda completed, total: progress.append((completed, total)))
            if progress is not None
            else None
        )
        self.assertTrue(workflow.execute_scan(request, progress=callback))
        self.assertEqual(workflow.state, WorkflowState.SCANNED)

    def test_scan_caches_planes_with_one_preview_scale(self) -> None:
        workflow = self._configured()
        progress: list[tuple[int, int]] = []

        self._scan(workflow, progress=progress)

        self.assertEqual(workflow.available_depths, (0.0, 1.0, 2.0))
        self.assertEqual(workflow.cached_depths, workflow.available_depths)
        self.assertEqual(progress, [(0, 3), (1, 3), (2, 3), (3, 3)])
        first = workflow.plane_preview(0.0)
        last = workflow.plane_preview(2.0)
        self.assertEqual(
            (first.display_low, first.display_high),
            (last.display_low, last.display_high),
        )
        self.assertEqual(first.image_u8.dtype, np.uint8)
        self.assertEqual(first.image_u8.shape, (16, 16))
        self.assertEqual(first.acceptance_status, U02_T2_STATUS)
        self.assertEqual(first.comparison_result, COMPARISON_RESULT)
        self.assertIsNotNone(first.metric)

    def test_input_or_parameter_change_invalidates_cached_scan(self) -> None:
        workflow = self._configured()
        self._scan(workflow)

        workflow.configure_parameters(parameters(depth_stop_um=1.0))

        self.assertEqual(workflow.state, WorkflowState.READY)
        self.assertEqual(workflow.cached_depths, ())
        self.assertIsNone(workflow.scan_result)
        with self.assertRaisesRegex(RuntimeError, "no scanned"):
            workflow.plane_preview()

    def test_stale_and_cancelled_requests_cannot_publish(self) -> None:
        workflow = self._configured()
        stale = workflow.begin_scan()
        self.assertFalse(
            workflow.execute_scan(
                stale,
                progress=lambda completed, _total: (
                    workflow.configure_parameters(
                        parameters(depth_stop_um=1.0)
                    )
                    if completed == 0
                    else None
                ),
            )
        )
        self.assertEqual(workflow.state, WorkflowState.READY)

        cancelled = workflow.begin_scan()
        self.assertFalse(
            workflow.execute_scan(
                cancelled,
                progress=lambda completed, _total: (
                    workflow.cancel_scan() if completed == 0 else None
                ),
            )
        )
        self.assertEqual(workflow.state, WorkflowState.CANCELLED)

    def test_input_mutation_during_scan_is_rejected(self) -> None:
        workflow = self._configured()
        request = workflow.begin_scan()

        def mutate_after_scan(completed: int, _total: int) -> None:
            if completed == 0:
                self.signal.write_bytes(b"changed-during-scan")

        with self.assertRaisesRegex(ValueError, "changed while"):
            workflow.execute_scan(request, progress=mutate_after_scan)

        self.assertEqual(workflow.state, WorkflowState.ERROR)

    def test_selected_depth_must_be_on_scan_grid(self) -> None:
        workflow = self._configured()
        self._scan(workflow)

        self.assertEqual(workflow.select_depth(1.0), 1.0)
        self.assertEqual(workflow.selected_depth_um, 1.0)
        with self.assertRaisesRegex(ValueError, "scanned depths"):
            workflow.select_depth(1.5)

    def test_export_and_reopen_preserve_unevaluated_boundary(self) -> None:
        reference = self.base / "reference.tiff"
        save_float_tiff(reference, np.ones((16, 16), dtype=np.float32))
        workflow = self._configured(reference=reference)
        self._scan(workflow)
        workflow.select_depth(1.0)

        output = self.base / "session"
        session = workflow.export(output)

        self.assertEqual(workflow.state, WorkflowState.EXPORTED)
        self.assertEqual(session["status"], U02_T2_STATUS)
        self.assertEqual(
            session["comparison"]["acceptance_result"],
            COMPARISON_RESULT,
        )
        self.assertIsNone(session["comparison"]["acceptance_threshold"])

        reopened = DesktopWorkflow()
        reopened_session = reopened.reopen(output)
        self.assertEqual(reopened.state, WorkflowState.REOPENED)
        self.assertEqual(reopened_session["status"], U02_T2_STATUS)
        preview = reopened.plane_preview()
        self.assertEqual(preview.depth_um, 1.0)
        self.assertEqual(preview.amplitude.dtype, np.float32)
        self.assertIsNone(preview.metric)
        with self.assertRaisesRegex(RuntimeError, "only after"):
            reopened.export(self.base / "inspection-cannot-export")

    def test_post_scan_input_mutation_blocks_export(self) -> None:
        workflow = self._configured()
        self._scan(workflow)
        self.signal.write_bytes(b"changed-after-scan")

        output = self.base / "session"
        with self.assertRaisesRegex(ValueError, "changed since"):
            workflow.export(output)

        self.assertEqual(workflow.state, WorkflowState.ERROR)
        self.assertFalse(output.exists())

    def test_export_result_is_not_published_after_new_scan_is_cancelled(self) -> None:
        workflow = self._configured()
        self._scan(workflow)
        entered = threading.Event()
        release = threading.Event()
        failures: list[BaseException] = []

        def slow_export(*args: object, **kwargs: object) -> dict[str, object]:
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            return core_export_session(*args, **kwargs)

        def run_export() -> None:
            try:
                workflow.export(self.base / "stale-session")
            except BaseException as exc:
                failures.append(exc)

        with patch(
            "lensless_holography_workbench.desktop_workflow.export_session",
            side_effect=slow_export,
        ):
            worker = threading.Thread(target=run_export)
            worker.start()
            self.assertTrue(entered.wait(timeout=5))
            workflow.begin_scan()
            self.assertTrue(workflow.cancel_scan())
            release.set()
            worker.join(timeout=10)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], StaleWorkflowError)
        self.assertEqual(workflow.state, WorkflowState.CANCELLED)
        self.assertIsNone(workflow.session)

    def test_export_and_session_property_are_defensive_copies(self) -> None:
        workflow = self._configured()
        self._scan(workflow)

        returned = workflow.export(self.base / "session")
        returned["status"] = "PASS"
        returned["outputs"]["selected_plane"]["file"] = "tampered.tiff"

        first_snapshot = workflow.session
        assert first_snapshot is not None
        self.assertEqual(first_snapshot["status"], U02_T2_STATUS)
        self.assertEqual(
            first_snapshot["outputs"]["selected_plane"]["file"],
            "selected-plane.tiff",
        )
        first_snapshot["status"] = "PASS"
        second_snapshot = workflow.session
        assert second_snapshot is not None
        self.assertEqual(second_snapshot["status"], U02_T2_STATUS)

    def test_reopen_uses_strict_semantic_inspection(self) -> None:
        workflow = self._configured()
        self._scan(workflow)
        output = self.base / "session"
        workflow.export(output)
        (output / "untracked.bin").write_bytes(b"not in manifest")

        reopened = DesktopWorkflow()
        with self.assertRaisesRegex(ValueError, "unexpected: untracked.bin"):
            reopened.reopen(output)
        self.assertEqual(reopened.state, WorkflowState.ERROR)

    def test_invalid_reference_shape_records_error_without_outputs(self) -> None:
        reference = self.base / "reference.tiff"
        save_float_tiff(reference, np.ones((15, 16), dtype=np.float32))
        workflow = self._configured(reference=reference)
        request = workflow.begin_scan()

        with self.assertRaisesRegex(ValueError, "ROI shape"):
            workflow.execute_scan(request)

        self.assertEqual(workflow.state, WorkflowState.ERROR)
        self.assertIn("ROI shape", workflow.error_message or "")
        self.assertFalse((self.base / "session").exists())

    def test_rejects_duplicate_inputs_and_non_tiff_reference(self) -> None:
        workflow = DesktopWorkflow(parameters=parameters())
        with self.assertRaisesRegex(ValueError, "unique"):
            workflow.configure_inputs(
                self.signal,
                [self.backgrounds[0], self.backgrounds[0]],
            )
        invalid_reference = self.base / "reference.png"
        Image.fromarray(np.ones((16, 16), dtype=np.uint8)).save(
            invalid_reference
        )
        with self.assertRaisesRegex(ValueError, "float TIFF"):
            workflow.configure_inputs(
                self.signal,
                self.backgrounds,
                invalid_reference,
            )


if __name__ == "__main__":
    unittest.main()
