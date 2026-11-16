from __future__ import annotations

import io
import json
import platform
import struct
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import __version__ as pillow_version

from release.windows.launcher import runtime_self_test
from tools.windows_release import (
    ACCEPTANCE_STATUS,
    COMPARISON_RESULT,
    MANIFEST_NAME,
    ProjectMetadata,
    ReleaseError,
    assemble_portable_release,
    build_application,
    main as release_main,
    read_project_metadata,
    run_packaged_self_tests,
    sha256_file,
    validate_member_names,
    validate_release_member,
    validate_windows_x64_executable,
    verify_release_bundle,
    verify_release_directory,
    windows_version_tuple,
    write_windows_version_file,
)


class WindowsReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_root = Path.cwd() / ".test-tmp"
        temporary_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temporary_root)
        self.base = Path(self.temporary.name)
        self.metadata = ProjectMetadata(
            name="lensless-holography-workbench",
            version="0.3.0",
            description="Desktop shell",
        )
        runtime = {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pillow": pillow_version,
            "tk": "8.6",
            "image_tk_imported": True,
        }
        boundary = {
            "status": ACCEPTANCE_STATUS,
            "comparison_result": COMPARISON_RESULT,
            "comparison_threshold": None,
            "physical_focus_claim": False,
        }
        common_checks = {
            "numpy_fft": "OK",
            "pillow_jpeg": "OK",
            "float_tiff": "OK",
            "resource_limits": "OK",
        }
        self.validation = {
            "runtime": {
                "self_test": "OK",
                "product_id": "U02",
                "test_id": "U02-T2",
                "runtime": runtime,
                "checks": {
                    **common_checks,
                    "ui": {"requested": False},
                },
                "acceptance_boundary": boundary,
            },
            "ui": {
                "self_test": "OK",
                "product_id": "U02",
                "test_id": "U02-T2",
                "runtime": runtime,
                "checks": {
                    **common_checks,
                    "ui": {
                        "requested": True,
                        "created": True,
                        "image_tk_photo_created": True,
                        "requested_width": 980,
                        "requested_height": 680,
                    },
                },
                "acceptance_boundary": boundary,
            },
        }
        self.runner_patcher = patch(
            "tools.windows_release.run_packaged_self_tests",
            return_value=self.validation,
        )
        self.runner_patcher.start()
        self.addCleanup(self.runner_patcher.stop)
        self.application = self.base / "application"
        (self.application / "_internal").mkdir(parents=True)
        executable = bytearray(512)
        executable[:2] = b"MZ"
        struct.pack_into("<I", executable, 0x3C, 0x80)
        executable[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", executable, 0x84, 0x8664)
        (self.application / "LenslessHolographyWorkbench.exe").write_bytes(
            executable
        )
        (self.application / "_internal" / "runtime.bin").write_bytes(
            b"runtime"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_requires_an_amd64_pe_executable(self) -> None:
        self.assertTrue(validate_windows_x64_executable(self.application).is_file())
        executable = self.application / "LenslessHolographyWorkbench.exe"
        contents = bytearray(executable.read_bytes())
        struct.pack_into("<H", contents, 0x84, 0xAA64)
        executable.write_bytes(contents)

        with self.assertRaisesRegex(ReleaseError, "not Windows x64"):
            validate_windows_x64_executable(self.application)

    def test_project_version_is_read_from_pyproject(self) -> None:
        root = Path(__file__).resolve().parents[2]
        metadata = read_project_metadata(root / "pyproject.toml")

        self.assertEqual(metadata.name, "lensless-holography-workbench")
        self.assertEqual(metadata.version, "0.3.0")

        lock = (root / "requirements-release.lock").read_text(encoding="utf-8")
        self.assertEqual(lock.count("--hash=sha256:"), 9)
        self.assertIn("PyInstaller==6.22.3", lock)
        self.assertIn("numpy==2.3.5", lock)

        portable_notice = (
            root / "release" / "windows" / "PORTABLE-README.txt"
        ).read_text(encoding="utf-8")
        third_party = (
            root / "release" / "windows" / "THIRD-PARTY-NOTICES.txt"
        ).read_text(encoding="utf-8")
        self.assertIn("0.3.0", portable_notice)
        self.assertIn("Code signing: NOT_SIGNED", portable_notice)
        self.assertIn("project LICENSE has not yet been selected", third_party)

    def test_windows_version_file_uses_project_version(self) -> None:
        self.assertEqual(windows_version_tuple("2.4.6-rc1"), (2, 4, 6, 0))
        output = write_windows_version_file(
            self.base / "version_info.txt",
            self.metadata,
        )
        contents = output.read_text(encoding="utf-8")

        self.assertIn("filevers=(0, 3, 0, 0)", contents)
        self.assertIn("ProductVersion', '0.3.0'", contents)
        with self.assertRaisesRegex(ReleaseError, "numeric components"):
            windows_version_tuple("release")

    def test_portable_release_is_deterministic_and_explicitly_unsigned(self) -> None:
        first = assemble_portable_release(
            self.application,
            self.base / "first",
            self.metadata,
        )
        second = assemble_portable_release(
            self.application,
            self.base / "second",
            self.metadata,
        )

        self.assertEqual(first.archive_sha256, second.archive_sha256)
        self.assertEqual(sha256_file(first.archive), first.archive_sha256)
        checksum = first.checksum_file.read_text(encoding="ascii")
        self.assertEqual(
            checksum,
            f"{first.archive_sha256}  {first.archive.name}\n",
        )
        manifest = json.loads(
            (first.directory / MANIFEST_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["code_signing"]["status"], "NOT_SIGNED")
        self.assertFalse(manifest["code_signing"]["verified"])
        self.assertEqual(
            manifest["authenticity"]["status"],
            "NOT_ESTABLISHED",
        )
        self.assertEqual(
            manifest["distribution_readiness"]["status"],
            "INTERNAL_VALIDATION_ONLY",
        )
        self.assertEqual(
            manifest["acceptance_boundary"]["status"],
            ACCEPTANCE_STATUS,
        )
        self.assertEqual(
            manifest["acceptance_boundary"]["comparison_result"],
            COMPARISON_RESULT,
        )
        self.assertNotIn("PASS", json.dumps(manifest))
        self.assertEqual(
            manifest["build_environment"]["binary_reproducibility"],
            "NOT_ESTABLISHED",
        )
        self.assertEqual(
            manifest["build_environment"]["source_provenance"],
            "NOT_ESTABLISHED",
        )
        self.assertIsNone(manifest["build_environment"]["git_head"])
        self.assertEqual(
            [row["path"] for row in manifest["files"]],
            [
                "app/LenslessHolographyWorkbench.exe",
                "app/_internal/runtime.bin",
            ],
        )

        with zipfile.ZipFile(first.archive) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names))
            self.assertTrue(
                all(
                    info.date_time == (1980, 1, 1, 0, 0, 0)
                    for info in archive.infolist()
                )
            )
            self.assertTrue(
                all(
                    info.compress_type == zipfile.ZIP_DEFLATED
                    and info.create_system == 3
                    and info.extra == b""
                    for info in archive.infolist()
                )
            )
            self.assertIn(
                f"{first.directory.name}/{MANIFEST_NAME}",
                names,
            )
        verified = verify_release_bundle(first.directory)
        self.assertEqual(verified.archive_sha256, first.archive_sha256)

    def test_rejects_unsafe_windows_member_names_and_collisions(self) -> None:
        for invalid in (
            "../escape.bin",
            "/absolute.bin",
            "CON.txt",
            "directory./file.bin",
            "trailing-space /file.bin",
            "stream:alternate",
            "back\\slash.bin",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ReleaseError):
                    validate_release_member(invalid)
        with self.assertRaisesRegex(ReleaseError, "case-insensitive"):
            validate_member_names(("app/File.bin", "app/file.BIN"))

    def test_self_test_detects_exact_file_set_and_hash_tampering(self) -> None:
        release = assemble_portable_release(
            self.application,
            self.base / "tamper-release",
            self.metadata,
        )
        unexpected = release.directory / "app" / "unexpected.bin"
        unexpected.write_bytes(b"unexpected")
        with self.assertRaisesRegex(ReleaseError, "file set mismatch"):
            verify_release_bundle(release.directory)
        unexpected.unlink()
        (release.directory / "app" / "_internal" / "runtime.bin").write_bytes(
            b"tampered"
        )
        with self.assertRaisesRegex(ReleaseError, "size mismatch|hash mismatch"):
            verify_release_bundle(release.directory)

    def test_bundle_verification_streams_archive_content(self) -> None:
        release = assemble_portable_release(
            self.application,
            self.base / "streaming-verifier",
            self.metadata,
        )

        with patch.object(
            Path,
            "read_bytes",
            side_effect=AssertionError("full-file reads are forbidden"),
        ):
            verified = verify_release_bundle(release.directory)

        self.assertEqual(verified.archive_sha256, release.archive_sha256)

    def test_verify_command_reports_integrity_and_claim_boundaries(self) -> None:
        release = assemble_portable_release(
            self.application,
            self.base / "verify-command",
            self.metadata,
        )
        output = io.StringIO()

        with (
            patch(
                "tools.windows_release.verify_release_directory",
                wraps=verify_release_directory,
            ) as directory_verifier,
            redirect_stdout(output),
        ):
            exit_code = release_main(
                ["verify", "--release-dir", str(release.directory)]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(directory_verifier.call_count, 1)
        output.getvalue().encode("ascii")
        result = json.loads(output.getvalue())
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["command"], "verify")
        self.assertEqual(result["verification"], "OK")
        self.assertEqual(result["archive_sha256"], release.archive_sha256)
        self.assertEqual(result["claims"]["code_signing"], "NOT_SIGNED")
        self.assertEqual(
            result["claims"]["distribution_readiness"],
            "INTERNAL_VALIDATION_ONLY",
        )
        self.assertEqual(
            result["source_provenance"]["status"],
            "NOT_ESTABLISHED",
        )
        self.assertEqual(result["claims"]["acceptance_status"], "BEKLİYOR")

        release.checksum_file.write_text(
            f"{'0' * 64}  {release.archive.name}\n",
            encoding="ascii",
            newline="\n",
        )
        error_output = io.StringIO()
        with redirect_stderr(error_output):
            exit_code = release_main(
                ["verify", "--release-dir", str(release.directory)]
            )
        self.assertEqual(exit_code, 2)
        error_output.getvalue().encode("ascii")
        self.assertNotIn("Traceback", error_output.getvalue())
        error = json.loads(error_output.getvalue())
        self.assertEqual(error["schema_version"], "1.0")
        self.assertEqual(error["command"], "verify")
        self.assertEqual(error["verification"], "FAILED")
        self.assertEqual(error["error"]["type"], "ReleaseError")
        self.assertIn("checksum does not match", error["error"]["message"])

    def test_verify_command_reports_missing_directory_without_traceback(self) -> None:
        error_output = io.StringIO()

        with redirect_stderr(error_output):
            exit_code = release_main(
                ["verify", "--release-dir", str(self.base / "missing")]
            )

        self.assertEqual(exit_code, 2)
        self.assertNotIn("Traceback", error_output.getvalue())
        error = json.loads(error_output.getvalue())
        self.assertEqual(error["verification"], "FAILED")
        self.assertIn("does not exist", error["error"]["message"])

    def test_verifier_rejects_false_distribution_claims(self) -> None:
        release = assemble_portable_release(
            self.application,
            self.base / "claim-release",
            self.metadata,
        )
        manifest_path = release.directory / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["distribution_readiness"]["status"] = "PUBLIC_RELEASE"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        with self.assertRaisesRegex(ReleaseError, "INTERNAL_VALIDATION_ONLY"):
            verify_release_bundle(release.directory)

    def test_failed_staging_does_not_publish_partial_release(self) -> None:
        output = self.base / "failed-release"
        with patch(
            "tools.windows_release._write_deterministic_zip",
            side_effect=OSError("simulated write failure"),
        ):
            with self.assertRaisesRegex(OSError, "simulated"):
                assemble_portable_release(
                    self.application,
                    output,
                    self.metadata,
                )
        if output.exists():
            self.assertEqual(list(output.iterdir()), [])

    def test_release_refuses_overwrite_and_empty_input(self) -> None:
        assemble_portable_release(
            self.application,
            self.base / "release",
            self.metadata,
        )
        with self.assertRaisesRegex(FileExistsError, "already exists"):
            assemble_portable_release(
                self.application,
                self.base / "release",
                self.metadata,
            )

        empty = self.base / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(ReleaseError, "at least one file"):
            assemble_portable_release(
                empty,
                self.base / "empty-release",
                self.metadata,
            )

        with self.assertRaisesRegex(ReleaseError, "inside the application tree"):
            assemble_portable_release(
                self.application,
                self.application / "recursive-output",
                self.metadata,
            )

    def test_publish_race_does_not_delete_competing_destination(self) -> None:
        output = self.base / "race-output"
        release_name = (
            "lensless-holography-workbench-0.3.0-windows-x64"
        )
        destination = output / release_name

        def collide(_source: Path, target: Path) -> None:
            self.assertEqual(target, destination)
            target.mkdir(parents=True)
            (target / "competitor.txt").write_text("keep", encoding="utf-8")
            raise FileExistsError("simulated publisher race")

        with patch(
            "tools.windows_release._replace_directory_atomic",
            side_effect=collide,
        ):
            with self.assertRaisesRegex(FileExistsError, "publisher race"):
                assemble_portable_release(
                    self.application,
                    output,
                    self.metadata,
                )

        self.assertEqual(
            (destination / "competitor.txt").read_text(encoding="utf-8"),
            "keep",
        )

    def test_build_fails_closed_when_pyinstaller_is_missing(self) -> None:
        with patch(
            "tools.windows_release.pyinstaller_is_available",
            return_value=False,
        ):
            with self.assertRaisesRegex(ReleaseError, "PyInstaller is required"):
                build_application(Path(__file__).resolve().parents[2])

    def test_launcher_runtime_self_test_preserves_claim_boundary(self) -> None:
        with patch("release.windows.launcher.tempfile.tempdir", str(self.base)):
            result = runtime_self_test(include_ui=False)

        self.assertEqual(result["self_test"], "OK")
        self.assertEqual(result["checks"]["numpy_fft"], "OK")
        self.assertEqual(result["checks"]["pillow_jpeg"], "OK")
        self.assertEqual(result["checks"]["float_tiff"], "OK")
        self.assertEqual(
            result["acceptance_boundary"]["status"],
            ACCEPTANCE_STATUS,
        )
        self.assertEqual(
            result["acceptance_boundary"]["comparison_result"],
            COMPARISON_RESULT,
        )

    def test_frozen_self_test_runner_requires_valid_json_and_boundary(self) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> None:
            label = "ui" if command[-2] == "--ui-smoke" else "runtime"
            Path(command[-1]).write_text(
                json.dumps(self.validation[label]),
                encoding="utf-8",
            )

        with (
            patch("tools.windows_release.subprocess.run", side_effect=fake_run),
            patch("tools.windows_release.tempfile.tempdir", str(self.base)),
        ):
            results = run_packaged_self_tests(self.application)

        self.assertEqual(set(results), {"runtime", "ui"})
        self.assertEqual(results["runtime"]["self_test"], "OK")


if __name__ == "__main__":
    unittest.main()
