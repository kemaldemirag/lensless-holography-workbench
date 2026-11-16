from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from lensless_holography_workbench import cli
from lensless_holography_workbench.depth_scan import evaluate_depth_scan
from lensless_holography_workbench.image_io import load_grayscale_bytes
from lensless_holography_workbench.session import snapshot_input_hashes


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_root = Path.cwd() / ".test-tmp"
        temporary_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temporary_root)
        self.base = Path(self.temporary.name)
        y, x = np.indices((16, 16), dtype=np.float64)
        signal = 120.0 + 8.0 * np.cos(2 * np.pi * x / 7.0)
        background = np.full_like(signal, 100.0)
        self.signal = self.base / "signal.tiff"
        self.background = self.base / "background.tiff"
        Image.fromarray(np.asarray(signal, dtype=np.float32)).save(self.signal)
        Image.fromarray(np.asarray(background, dtype=np.float32)).save(
            self.background
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _arguments(self, output: Path) -> list[str]:
        return [
            "--signal",
            str(self.signal),
            "--background",
            str(self.background),
            "--output",
            str(output),
            "--depth-stop-um",
            "0",
            "--roi-size-px",
            "8",
        ]

    def test_cli_records_the_preprocessing_input_snapshot(self) -> None:
        output = self.base / "session"
        expected = snapshot_input_hashes(
            {"signal": self.signal, "background_1": self.background}
        )
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = cli.main(self._arguments(output))

        self.assertEqual(exit_code, 0)
        session = json.loads((output / "session.json").read_text(encoding="utf-8"))
        recorded = {row["role"]: row["sha256"] for row in session["inputs"]}
        self.assertEqual(recorded, expected)

    def test_cli_rejects_input_mutation_before_publication(self) -> None:
        output = self.base / "changed-session"

        def mutate_after_scan(
            signal: np.ndarray,
            backgrounds: list[np.ndarray],
            parameters: object,
        ) -> object:
            result = evaluate_depth_scan(signal, backgrounds, parameters)
            self.signal.write_bytes(b"changed-after-scan")
            return result

        with (
            patch.object(cli, "evaluate_depth_scan", side_effect=mutate_after_scan),
            self.assertRaisesRegex(
                ValueError,
                "changed since processing: signal",
            ),
        ):
            cli.main(self._arguments(output))

        self.assertFalse(output.exists())

    def test_cli_decodes_the_immutable_input_bytes(self) -> None:
        output = self.base / "immutable-session"
        original = self.signal.read_bytes()
        calls = 0

        def decode_while_source_changes(content: bytes) -> np.ndarray:
            nonlocal calls
            calls += 1
            if calls != 1:
                return load_grayscale_bytes(content)
            self.assertEqual(content, original)
            self.signal.write_bytes(b"temporary-different-content")
            try:
                return load_grayscale_bytes(content)
            finally:
                self.signal.write_bytes(original)

        stdout = io.StringIO()
        with (
            patch.object(
                cli,
                "load_grayscale_bytes",
                side_effect=decode_while_source_changes,
            ),
            redirect_stdout(stdout),
        ):
            exit_code = cli.main(self._arguments(output))

        self.assertEqual(exit_code, 0)
        self.assertTrue((output / "session.json").is_file())
        self.assertEqual(self.signal.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
