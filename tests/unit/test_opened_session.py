from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from lensless_holography_workbench.depth_scan import PropagationParameters, evaluate_depth_scan
from lensless_holography_workbench.opened_session import open_session_for_inspection
from lensless_holography_workbench.session import export_session, sha256_file


class OpenedSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        y, x = np.indices((32, 32), dtype=np.float64)
        self.signal = 120.0 + 10.0 * np.cos(2 * np.pi * x / 9.0) + 5.0 * np.sin(
            2 * np.pi * y / 11.0
        )
        self.backgrounds = [
            np.full((32, 32), 100.0),
            np.full((32, 32), 102.0),
        ]
        self.parameters = PropagationParameters(
            pixel_pitch_um=1.0,
            wavelength_um=0.5,
            refractive_index=1.0,
            depth_start_um=0.0,
            depth_stop_um=2.0,
            depth_step_um=1.0,
            roi_size_px=16,
        )
        self.prepared, self.scan = evaluate_depth_scan(
            self.signal, self.backgrounds, self.parameters
        )
        self.temporary_root = Path.cwd() / ".test-tmp"
        self.temporary_root.mkdir(exist_ok=True)

    def _inputs(self, directory: Path) -> dict[str, Path]:
        signal = directory / "signal.bin"
        background = directory / "background.bin"
        signal.write_bytes(b"stable-signal")
        background.write_bytes(b"stable-background")
        return {"signal": signal, "background_1": background}

    def _export(self, base: Path, *, comparison: bool = False) -> Path:
        session_dir = base / "session"
        reference = None
        if comparison:
            reference = np.asarray(self.prepared.amplitude_at(1.0), dtype=np.float32) + 0.25
        export_session(
            session_dir,
            prepared=self.prepared,
            scan=self.scan,
            parameters=self.parameters,
            selected_depth_um=1.0,
            inputs=self._inputs(base),
            reference=reference,
        )
        return session_dir

    def _rehash_output(self, session_dir: Path, output_key: str) -> None:
        session_path = session_dir / "session.json"
        session = json.loads(session_path.read_text(encoding="utf-8"))
        record = session["outputs"][output_key]
        output_path = session_dir / record["file"]
        record["sha256"] = sha256_file(output_path)
        record["size_bytes"] = output_path.stat().st_size
        session_path.write_text(
            json.dumps(session, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        manifest_path = session_dir / "sha256.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][record["file"]] = record["sha256"]
        manifest["files"]["session.json"] = sha256_file(session_path)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def test_loads_verified_inspection_only_session(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            session_dir = self._export(Path(temporary))
            opened = open_session_for_inspection(session_dir)

        self.assertEqual(opened.status, "BEKLİYOR")
        self.assertEqual(opened.acceptance_result, "NOT_EVALUATED")
        self.assertFalse(opened.has_comparison)
        self.assertEqual([row.depth_um for row in opened.metrics], [0.0, 1.0, 2.0])
        self.assertEqual(opened.selected_plane.shape, (16, 16))
        self.assertFalse(opened.selected_plane.flags.writeable)
        with self.assertRaises(TypeError):
            opened.metadata["status"] = "PASS"  # type: ignore[index]

    def test_loads_verified_comparison_artifacts_without_acceptance(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            session_dir = self._export(Path(temporary), comparison=True)
            opened = open_session_for_inspection(session_dir)

        self.assertTrue(opened.has_comparison)
        self.assertEqual(opened.acceptance_result, "NOT_EVALUATED")
        self.assertIsNotNone(opened.difference_map)
        assert opened.difference_map is not None
        self.assertFalse(opened.difference_map.flags.writeable)
        self.assertEqual(opened.difference_map.shape, opened.selected_plane.shape)
        self.assertEqual(len(opened.line_profile), opened.selected_plane.shape[1])
        self.assertTrue(all(point.difference < 0 for point in opened.line_profile))

    def test_rejects_file_not_covered_by_hash_manifest(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            session_dir = self._export(Path(temporary))
            (session_dir / "untracked.bin").write_bytes(b"not in manifest")

            with self.assertRaisesRegex(ValueError, "unexpected: untracked.bin"):
                open_session_for_inspection(session_dir)

    def test_rejects_manifest_file_not_declared_by_session(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            session_dir = self._export(Path(temporary))
            extra = session_dir / "extra.bin"
            extra.write_bytes(b"hash covered but not a session output")
            manifest_path = session_dir / "sha256.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["extra.bin"] = sha256_file(extra)
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            with self.assertRaisesRegex(ValueError, "not declared by session: extra.bin"):
                open_session_for_inspection(session_dir)

    def test_rejects_rehashed_metrics_with_wrong_depth_grid(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            session_dir = self._export(Path(temporary))
            metrics_path = session_dir / "metrics.csv"
            with metrics_path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.reader(stream))
            rows[2][0] = "1.5"
            with metrics_path.open("w", encoding="utf-8", newline="") as stream:
                csv.writer(stream, lineterminator="\n").writerows(rows)
            self._rehash_output(session_dir, "metrics")

            with self.assertRaisesRegex(ValueError, "depth grid"):
                open_session_for_inspection(session_dir)

    def test_rejects_rehashed_inconsistent_line_profile(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            session_dir = self._export(Path(temporary), comparison=True)
            profile_path = session_dir / "line-profile.csv"
            with profile_path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.reader(stream))
            rows[1][1] = str(float(rows[1][1]) + 1.0)
            with profile_path.open("w", encoding="utf-8", newline="") as stream:
                csv.writer(stream, lineterminator="\n").writerows(rows)
            self._rehash_output(session_dir, "line_profile")

            with self.assertRaisesRegex(ValueError, "inconsistent difference"):
                open_session_for_inspection(session_dir)


if __name__ == "__main__":
    unittest.main()
