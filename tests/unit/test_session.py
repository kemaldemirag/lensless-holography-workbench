from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from lensless_holography_workbench.depth_scan import PropagationParameters, evaluate_depth_scan
from lensless_holography_workbench.session import (
    export_session,
    load_float_tiff,
    reopen_session,
    save_float_tiff,
    sha256_file,
    snapshot_input_files,
    snapshot_input_hashes,
)


class SessionTests(unittest.TestCase):
    def setUp(self) -> None:
        y, x = np.indices((32, 32), dtype=np.float64)
        self.signal = 120.0 + 10.0 * np.cos(2 * np.pi * x / 9.0) + 5.0 * np.sin(2 * np.pi * y / 11.0)
        self.backgrounds = [np.full((32, 32), 100.0), np.full((32, 32), 102.0)]
        self.parameters = PropagationParameters(
            pixel_pitch_um=1.0,
            wavelength_um=0.5,
            refractive_index=1.0,
            depth_start_um=0.0,
            depth_stop_um=2.0,
            depth_step_um=1.0,
            roi_size_px=16,
        )
        self.prepared, self.scan = evaluate_depth_scan(self.signal, self.backgrounds, self.parameters)
        self.temporary_root = Path.cwd() / ".test-tmp"
        self.temporary_root.mkdir(exist_ok=True)

    def _inputs(self, directory: Path) -> dict[str, Path]:
        signal = directory / "signal.bin"
        background = directory / "background.bin"
        signal.write_bytes(b"stable-signal")
        background.write_bytes(b"stable-background")
        return {"signal": signal, "background_1": background}

    def test_reopened_tiff_matches_exact_float32_pixels(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            session_dir = base / "session"
            export_session(
                session_dir,
                prepared=self.prepared,
                scan=self.scan,
                parameters=self.parameters,
                selected_depth_um=1.0,
                inputs=self._inputs(base),
            )
            expected = np.asarray(self.prepared.amplitude_at(1.0), dtype=np.float32)

            session, reopened = reopen_session(session_dir, expected_selected_plane=expected)

        np.testing.assert_array_equal(reopened, expected)
        self.assertEqual(session["status"], "BEKLİYOR")
        self.assertEqual(session["schema_version"], "1.0")
        self.assertEqual(session["environment"]["numpy"], np.__version__)
        self.assertEqual(session["units"]["depth"], "um")
        self.assertEqual(session["units"]["selected_plane"], "normalized_amplitude")
        self.assertIsNone(session["comparison"])

    def test_load_float_tiff_rejects_integer_sample_format(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            path = Path(temporary) / "integer-reference.tiff"
            Image.fromarray(
                np.full((16, 16), 4095, dtype=np.uint16)
            ).save(path)

            with self.assertRaisesRegex(ValueError, "32-bit floating-point"):
                load_float_tiff(path)

    def test_repeated_export_has_identical_managed_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            inputs = self._inputs(base)
            for name in ("first", "second"):
                export_session(
                    base / name,
                    prepared=self.prepared,
                    scan=self.scan,
                    parameters=self.parameters,
                    selected_depth_um=1.0,
                    inputs=inputs,
                )
            first = {path.name: sha256_file(path) for path in (base / "first").iterdir() if path.is_file()}
            second = {path.name: sha256_file(path) for path in (base / "second").iterdir() if path.is_file()}
        self.assertEqual(first, second)

    def test_expected_input_hashes_are_recorded_without_drift(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            inputs = self._inputs(base)
            expected = snapshot_input_hashes(inputs)

            session = export_session(
                base / "session",
                prepared=self.prepared,
                scan=self.scan,
                parameters=self.parameters,
                selected_depth_um=1.0,
                inputs=inputs,
                expected_input_hashes=expected,
            )

        recorded = {row["role"]: row["sha256"] for row in session["inputs"]}
        self.assertEqual(recorded, expected)

    def test_changed_expected_input_leaves_no_partial_session(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            inputs = self._inputs(base)
            expected = snapshot_input_hashes(inputs)
            inputs["signal"].write_bytes(b"changed-after-processing")
            output = base / "session"

            with self.assertRaisesRegex(
                ValueError,
                "changed since processing: signal",
            ):
                export_session(
                    output,
                    prepared=self.prepared,
                    scan=self.scan,
                    parameters=self.parameters,
                    selected_depth_um=1.0,
                    inputs=inputs,
                    expected_input_hashes=expected,
                )

            self.assertFalse(output.exists())

    def test_expected_input_hash_roles_must_match_exactly(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            inputs = self._inputs(base)
            output = base / "session"

            with self.assertRaisesRegex(ValueError, "exactly match input roles"):
                export_session(
                    output,
                    prepared=self.prepared,
                    scan=self.scan,
                    parameters=self.parameters,
                    selected_depth_um=1.0,
                    inputs=inputs,
                    expected_input_hashes={
                        "signal": sha256_file(inputs["signal"]),
                    },
                )

            self.assertFalse(output.exists())

    def test_snapshot_rejects_mutation_while_hashing(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            inputs = self._inputs(base)

            def mutate_after_hash(path: str | Path) -> str:
                digest = sha256_file(path)
                Path(path).write_bytes(b"changed-during-hash")
                return digest

            with (
                patch(
                    "lensless_holography_workbench.session.sha256_file",
                    side_effect=mutate_after_hash,
                ),
                self.assertRaisesRegex(ValueError, "changed while hashing: signal"),
            ):
                snapshot_input_hashes({"signal": inputs["signal"]})

    def test_input_snapshot_enforces_the_file_byte_limit(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            path = Path(temporary) / "oversized.bin"
            path.write_bytes(b"12345")

            with (
                patch(
                    "lensless_holography_workbench.session.MAX_INPUT_FILE_BYTES",
                    4,
                ),
                self.assertRaisesRegex(ValueError, "exceeds byte limit: signal"),
            ):
                snapshot_input_files({"signal": path})

    def test_input_snapshot_enforces_total_limit_before_next_read(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            first = base / "first.bin"
            second = base / "second.bin"
            first.write_bytes(b"1234")
            second.write_bytes(b"5678")

            with (
                patch(
                    "lensless_holography_workbench.session.MAX_INPUT_FILE_BYTES",
                    10,
                ),
                patch(
                    "lensless_holography_workbench.session.MAX_TOTAL_INPUT_BYTES",
                    6,
                ),
                self.assertRaisesRegex(ValueError, "total byte limit"),
            ):
                snapshot_input_files({"first": first, "second": second})

    def test_input_mutation_during_staging_prevents_publication(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            inputs = self._inputs(base)
            expected = snapshot_input_hashes(inputs)
            output = base / "session"
            changed = False

            def save_then_mutate(path: str | Path, array: np.ndarray) -> Path:
                nonlocal changed
                result = save_float_tiff(path, array)
                if not changed:
                    inputs["signal"].write_bytes(b"changed-during-staging")
                    changed = True
                return result

            with (
                patch(
                    "lensless_holography_workbench.session.save_float_tiff",
                    side_effect=save_then_mutate,
                ),
                self.assertRaisesRegex(
                    ValueError,
                    "changed since processing: signal",
                ),
            ):
                export_session(
                    output,
                    prepared=self.prepared,
                    scan=self.scan,
                    parameters=self.parameters,
                    selected_depth_um=1.0,
                    inputs=inputs,
                    expected_input_hashes=expected,
                )

            self.assertFalse(output.exists())
            self.assertFalse(
                any(path.name.startswith(".session.tmp-") for path in base.iterdir())
            )

    def test_comparison_artifacts_do_not_claim_acceptance(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            reference = np.asarray(self.prepared.amplitude_at(1.0), dtype=np.float32) + 0.25
            session = export_session(
                base / "session",
                prepared=self.prepared,
                scan=self.scan,
                parameters=self.parameters,
                selected_depth_um=1.0,
                inputs=self._inputs(base),
                reference=reference,
            )
            session_dir = base / "session"
            self.assertTrue((session_dir / "difference-map.tiff").is_file())
            self.assertTrue((session_dir / "line-profile.csv").is_file())
            self.assertEqual(session["comparison"]["acceptance_result"], "NOT_EVALUATED")
            self.assertIsNone(session["comparison"]["acceptance_threshold"])

    def test_tampered_output_fails_hash_verification(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            session_dir = base / "session"
            export_session(
                session_dir,
                prepared=self.prepared,
                scan=self.scan,
                parameters=self.parameters,
                selected_depth_um=1.0,
                inputs=self._inputs(base),
            )
            with (session_dir / "metrics.csv").open("a", encoding="utf-8") as stream:
                stream.write("tampered\n")
            with self.assertRaisesRegex(ValueError, "metrics.csv"):
                reopen_session(session_dir)

    def test_rejects_overwrite_of_managed_session(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            session_dir = base / "session"
            inputs = self._inputs(base)
            export_session(
                session_dir,
                prepared=self.prepared,
                scan=self.scan,
                parameters=self.parameters,
                selected_depth_um=1.0,
                inputs=inputs,
            )
            with self.assertRaises(FileExistsError):
                export_session(
                    session_dir,
                    prepared=self.prepared,
                    scan=self.scan,
                    parameters=self.parameters,
                    selected_depth_um=1.0,
                    inputs=inputs,
                )

    def test_write_failure_leaves_no_partial_session(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            session_dir = base / "session"
            with patch(
                "lensless_holography_workbench.session.save_float_tiff",
                side_effect=OSError("simulated disk failure"),
            ):
                with self.assertRaisesRegex(OSError, "simulated disk failure"):
                    export_session(
                        session_dir,
                        prepared=self.prepared,
                        scan=self.scan,
                        parameters=self.parameters,
                        selected_depth_um=1.0,
                        inputs=self._inputs(base),
                    )

            self.assertFalse(session_dir.exists())
            self.assertFalse(
                any(path.name.startswith(".session.tmp-") for path in base.iterdir())
            )

    def test_rejects_nonempty_unmanaged_destination(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            session_dir = base / "session"
            session_dir.mkdir()
            marker = session_dir / "keep.txt"
            marker.write_text("user data", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                export_session(
                    session_dir,
                    prepared=self.prepared,
                    scan=self.scan,
                    parameters=self.parameters,
                    selected_depth_um=1.0,
                    inputs=self._inputs(base),
                )

            self.assertEqual(marker.read_text(encoding="utf-8"), "user data")

    def test_reopen_rejects_missing_output_manifest_coverage(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            session_dir = base / "session"
            export_session(
                session_dir,
                prepared=self.prepared,
                scan=self.scan,
                parameters=self.parameters,
                selected_depth_um=1.0,
                inputs=self._inputs(base),
            )
            manifest_path = session_dir / "sha256.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            del manifest["files"]["selected-plane.tiff"]
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            with self.assertRaisesRegex(ValueError, "missing required files"):
                reopen_session(session_dir)

    def test_reopen_rejects_tampered_session_constants_even_when_rehashed(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            session_dir = base / "session"
            export_session(
                session_dir,
                prepared=self.prepared,
                scan=self.scan,
                parameters=self.parameters,
                selected_depth_um=1.0,
                inputs=self._inputs(base),
            )
            session_path = session_dir / "session.json"
            session = json.loads(session_path.read_text(encoding="utf-8"))
            session["status"] = "TEKRARLANDI"
            session_path.write_text(
                json.dumps(session, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            manifest_path = session_dir / "sha256.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["session.json"] = sha256_file(session_path)
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            with self.assertRaisesRegex(ValueError, "invalid session status"):
                reopen_session(session_dir)

    def test_reopen_rejects_rehashed_comparison_pass(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            base = Path(temporary)
            session_dir = base / "session"
            reference = np.asarray(self.prepared.amplitude_at(1.0), dtype=np.float32)
            export_session(
                session_dir,
                prepared=self.prepared,
                scan=self.scan,
                parameters=self.parameters,
                selected_depth_um=1.0,
                inputs=self._inputs(base),
                reference=reference,
            )
            session_path = session_dir / "session.json"
            session = json.loads(session_path.read_text(encoding="utf-8"))
            session["comparison"]["acceptance_result"] = "PASS"
            session["comparison"]["acceptance_threshold"] = 0.5
            session_path.write_text(
                json.dumps(session, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            manifest_path = session_dir / "sha256.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["session.json"] = sha256_file(session_path)
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            with self.assertRaisesRegex(ValueError, "must remain NOT_EVALUATED"):
                reopen_session(session_dir)


if __name__ == "__main__":
    unittest.main()

