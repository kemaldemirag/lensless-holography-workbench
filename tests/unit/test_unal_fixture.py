from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import lensless_holography_workbench.unal_fixture as unal_fixture
from lensless_holography_workbench.unal_fixture import (
    FixtureResolution,
    LoadedFixture,
    UnalFixtureError,
    UnavailableFixture,
    load_unal_k18_fixture,
)


REPOSITORY = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPOSITORY / "schemas" / "unal-k18-fixture.schema.json"
TEMPORARY_ROOT = REPOSITORY / ".test-tmp"
TEMPORARY_ROOT.mkdir(exist_ok=True)
ASSET_IDENTITIES = {
    "hologram": (
        "c0025.jpg",
        "f4218e67ca133e28ab2c2e66e46d9399ad153031",
    ),
    "reference": (
        "c0039.jpg",
        "98510a0d67853ef87e68a8115eee67be754e69f6",
    ),
    "parameter_screenshot": (
        "OneMicrorganism_ReconstructionParameters.jpg",
        "dd537fde038a78e924b3f1dfb855021d977c89fc",
    ),
    "published_intensity_visual": (
        "OneMicrorganism_Intensity.jpg",
        "8d5519fc90c45c583ec5774952525b1552928cef",
    ),
}


def _acceptance() -> dict[str, object]:
    return {
        "u02_t2_status": "BEKLİYOR",
        "acceptance_result": "NOT_EVALUATED",
        "acceptance_threshold": None,
    }


def _denied_manifest() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "fixture_id": "UNAL-K18-SINGLE-MICROORGANISM",
        "product_id": "U02",
        "test_id": "U02-T2",
        "permission": {
            "status": "NOT_GRANTED",
            "reason": "Image-use permission has not been established.",
        },
        "fixture": None,
        "acceptance": _acceptance(),
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _git_blob_sha1(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(header)
    digest.update(content)
    return digest.hexdigest()


def _synthetic_pins(content_by_role: dict[str, bytes]) -> dict[str, tuple[str, str]]:
    return {
        role: (source_name, _git_blob_sha1(content_by_role[role]))
        for role, (source_name, _production_blob) in ASSET_IDENTITIES.items()
    }


def _load_synthetic_fixture(
    manifest_path: Path,
    content_by_role: dict[str, bytes],
) -> FixtureResolution:
    with patch.object(
        unal_fixture,
        "_PINNED_ASSETS",
        _synthetic_pins(content_by_role),
    ):
        return load_unal_k18_fixture(manifest_path)


def _create_granted_fixture(root: Path) -> tuple[Path, dict[str, object], dict[str, bytes]]:
    payload_directory = root / "payload"
    payload_directory.mkdir()
    content_by_role: dict[str, bytes] = {}
    assets: list[dict[str, object]] = []
    for role, (source_name, _production_blob) in ASSET_IDENTITIES.items():
        content = f"synthetic fixture bytes for {role}\n".encode()
        content_by_role[role] = content
        relative_path = f"payload/{source_name}"
        (root / Path(relative_path)).write_bytes(content)
        assets.append(
            {
                "role": role,
                "path": relative_path,
                "source_name": source_name,
                "source_url": f"https://example.invalid/unal/{source_name}",
                "git_blob_sha1": _git_blob_sha1(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "accessed_on": "2026-11-15",
            }
        )

    manifest: dict[str, object] = {
        "schema_version": "1.0",
        "fixture_id": "UNAL-K18-SINGLE-MICROORGANISM",
        "product_id": "U02",
        "test_id": "U02-T2",
        "permission": {
            "status": "GRANTED",
            "usage": "LOCAL_U02_T2_VALIDATION_ONLY",
            "redistribution": "PROHIBITED",
            "granted_by": "synthetic-test-authority",
            "granted_on": "2026-11-15",
            "evidence_reference": "synthetic-test-record",
        },
        "fixture": {
            "source": {
                "repository": "unal-optodigital/DLHM",
                "code_commit_sha1": "5d5273e2ebb08735c531815d3e4b100a4e3f3772",
                "documentation_commit_sha1": "536e70f1e179bf285492343b989a478d093c32e3",
                "project_page_url": "https://unal-optodigital.github.io/DLHM/",
            },
            "assets": assets,
            "parameter_verification": {
                "source_role": "parameter_screenshot",
                "transcribed_by": "synthetic-transcriber",
                "independently_checked_by": "synthetic-independent-checker",
                "checked_on": "2026-11-15",
            },
            "optics": {
                "backend": "point_source_kirchhoff_helmholtz",
                "wavelength_um": 0.532,
                "reconstruction_distance_um": 1000.0,
                "source_to_screen_distance_um": 10000.0,
                "detector_width_um": 5000.0,
                "detector_height_um": 4000.0,
                "output_width_um": 1000.0,
                "output_height_um": 800.0,
                "input_sample_pitch_um": 2.0,
                "output_sample_pitch_um": 1.0,
            },
            "shared_crop_px": {
                "coordinate_system": "TOP_LEFT_ZERO_BASED_HALF_OPEN",
                "top": 0,
                "left": 0,
                "height": 16,
                "width": 16,
            },
            "intensity_mapping": {
                "method": "FIXED_RANGE_LINEAR",
                "clip": True,
                "candidate_input_min": 0.0,
                "candidate_input_max": 1.0,
                "reference_input_min": 0.0,
                "reference_input_max": 255.0,
                "output_min": 0.0,
                "output_max": 1.0,
            },
        },
        "acceptance": _acceptance(),
    }
    manifest_path = root / "unal-k18-fixture.json"
    _write_json(manifest_path, manifest)
    return manifest_path, manifest, content_by_role


def _asset(manifest: dict[str, object], role: str) -> dict[str, object]:
    fixture = manifest["fixture"]
    assert isinstance(fixture, dict)
    assets = fixture["assets"]
    assert isinstance(assets, list)
    return next(record for record in assets if record["role"] == role)


class UnalFixtureContractTests(unittest.TestCase):
    def test_checked_in_schema_pins_source_roles_backend_and_claim_boundary(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.0")
        self.assertEqual(
            schema["$defs"]["source"]["properties"]["code_commit_sha1"]["const"],
            "5d5273e2ebb08735c531815d3e4b100a4e3f3772",
        )
        self.assertEqual(
            schema["$defs"]["optics"]["properties"]["backend"]["const"],
            "point_source_kirchhoff_helmholtz",
        )
        self.assertEqual(
            schema["$defs"]["acceptance"]["properties"]["acceptance_result"]["const"],
            "NOT_EVALUATED",
        )

    def test_not_configured_is_explicitly_unavailable(self) -> None:
        result: FixtureResolution = load_unal_k18_fixture(None)

        self.assertIsInstance(result, UnavailableFixture)
        self.assertEqual(result.status, "UNAVAILABLE")
        self.assertEqual(result.reason, "NOT_CONFIGURED")
        self.assertTrue(result.detail)

    def test_missing_manifest_is_explicitly_unavailable(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            result = load_unal_k18_fixture(Path(temporary) / "missing.json")

        self.assertIsInstance(result, UnavailableFixture)
        self.assertEqual(result.status, "UNAVAILABLE")
        self.assertEqual(result.reason, "MANIFEST_NOT_FOUND")

    def test_network_and_device_manifest_paths_are_rejected_before_io(self) -> None:
        unsafe_paths = (
            r"\\server\share\fixture.json",
            r"\\?\C:\external-fixtures\fixture.json",
            r"\\.\C:\external-fixtures\fixture.json",
            "//server/share/fixture.json",
            r"C:fixture.json",
            r"C:\external-fixtures\NUL.json",
            "C:\\external-fixtures\\COM¹.json",
            r"C:\external-fixtures\CONOUT$.json",
            r"C:\external-fixtures\fixture.json:secret",
        )
        for unsafe_path in unsafe_paths:
            with self.subTest(path=unsafe_path), patch.object(
                Path,
                "lstat",
                side_effect=AssertionError("filesystem must not be touched"),
            ), self.assertRaises(UnalFixtureError) as raised:
                load_unal_k18_fixture(unsafe_path)

            self.assertEqual(raised.exception.code, "UNSAFE_PATH")

    def test_permission_not_granted_does_not_require_payload(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            manifest_path = Path(temporary) / "unal-k18-fixture.json"
            _write_json(manifest_path, _denied_manifest())

            with patch.object(
                unal_fixture,
                "_resolve_asset",
                side_effect=AssertionError("payload must not be inspected"),
            ):
                result = load_unal_k18_fixture(manifest_path)

        self.assertIsInstance(result, UnavailableFixture)
        self.assertEqual(result.status, "UNAVAILABLE")
        self.assertEqual(result.reason, "PERMISSION_NOT_GRANTED")
        self.assertIn("permission", result.detail.lower())

    def test_valid_synthetic_fixture_returns_immutable_verified_snapshots(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            root = Path(temporary)
            manifest_path, manifest, expected_content = _create_granted_fixture(root)

            result = _load_synthetic_fixture(manifest_path, expected_content)

            self.assertIsInstance(result, LoadedFixture)
            self.assertEqual(result.status, "AVAILABLE")
            self.assertIsInstance(result.assets, Mapping)
            self.assertEqual(set(result.assets), set(ASSET_IDENTITIES))
            for role, content in expected_content.items():
                snapshot = result.assets[role]
                self.assertIsInstance(snapshot.content, bytes)
                self.assertEqual(snapshot.content, content)
                self.assertEqual(snapshot.size_bytes, len(content))
                self.assertEqual(snapshot.sha256, hashlib.sha256(content).hexdigest())
                self.assertEqual(snapshot.path, _asset(manifest, role)["path"])

            with self.assertRaises(TypeError):
                result.assets["replacement"] = result.assets["hologram"]  # type: ignore[index]

            hologram_path = root / result.assets["hologram"].path
            hologram_path.write_bytes(b"changed after the verified read")
            self.assertEqual(result.assets["hologram"].content, expected_content["hologram"])

    def test_separately_approved_manifest_hash_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            manifest_path, _manifest, content = _create_granted_fixture(Path(temporary))
            approved = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

            with patch.object(
                unal_fixture,
                "_PINNED_ASSETS",
                _synthetic_pins(content),
            ):
                result = load_unal_k18_fixture(
                    manifest_path,
                    expected_manifest_sha256=approved,
                )
                self.assertIsInstance(result, LoadedFixture)

                with self.assertRaises(UnalFixtureError) as raised:
                    load_unal_k18_fixture(
                        manifest_path,
                        expected_manifest_sha256="0" * 64,
                    )

        self.assertEqual(raised.exception.code, "MANIFEST_HASH_MISMATCH")

    def test_wholly_unprovisioned_payload_is_explicitly_unavailable(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            root = Path(temporary)
            manifest_path, manifest, content = _create_granted_fixture(root)
            for role in ASSET_IDENTITIES:
                (root / Path(_asset(manifest, role)["path"])).unlink()

            result = _load_synthetic_fixture(manifest_path, content)

        self.assertIsInstance(result, UnavailableFixture)
        self.assertEqual(result.reason, "PAYLOAD_NOT_PROVISIONED")

    def test_partially_provisioned_payload_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            root = Path(temporary)
            manifest_path, manifest, content = _create_granted_fixture(root)
            (root / Path(_asset(manifest, "hologram")["path"])).unlink()

            with patch.object(
                unal_fixture,
                "_PINNED_ASSETS",
                _synthetic_pins(content),
            ), self.assertRaises(UnalFixtureError) as raised:
                load_unal_k18_fixture(manifest_path)

        self.assertEqual(raised.exception.code, "PARTIAL_PAYLOAD")

    def test_hash_tamper_fails_closed_instead_of_becoming_a_skip(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            root = Path(temporary)
            manifest_path, manifest, content = _create_granted_fixture(root)
            path = root / Path(_asset(manifest, "reference")["path"])
            original = path.read_bytes()
            path.write_bytes(b"x" * len(original))

            with patch.object(
                unal_fixture,
                "_PINNED_ASSETS",
                _synthetic_pins(content),
            ), self.assertRaises(UnalFixtureError) as raised:
                load_unal_k18_fixture(manifest_path)

        self.assertEqual(raised.exception.code, "HASH_MISMATCH")

    def test_declared_size_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            root = Path(temporary)
            manifest_path, manifest, content = _create_granted_fixture(root)
            record = _asset(manifest, "hologram")
            record["size_bytes"] = int(record["size_bytes"]) + 1
            _write_json(manifest_path, manifest)

            with patch.object(
                unal_fixture,
                "_PINNED_ASSETS",
                _synthetic_pins(content),
            ), self.assertRaises(UnalFixtureError) as raised:
                load_unal_k18_fixture(manifest_path)

        self.assertEqual(raised.exception.code, "SIZE_MISMATCH")

    def test_manifest_and_payload_byte_limits_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            manifest_path = Path(temporary) / "unal-k18-fixture.json"
            _write_json(manifest_path, _denied_manifest())
            with patch.object(
                unal_fixture,
                "MAX_MANIFEST_BYTES",
                8,
            ), self.assertRaises(UnalFixtureError) as raised:
                load_unal_k18_fixture(manifest_path)
            self.assertEqual(raised.exception.code, "SIZE_LIMIT_EXCEEDED")

        limit_patches = (
            {"MAX_ASSET_BYTES": 1, "MAX_TOTAL_ASSET_BYTES": 1024},
            {"MAX_ASSET_BYTES": 1024, "MAX_TOTAL_ASSET_BYTES": 1},
        )
        for limits in limit_patches:
            with self.subTest(limits=limits), tempfile.TemporaryDirectory(
                dir=TEMPORARY_ROOT
            ) as temporary:
                manifest_path, _manifest, content = _create_granted_fixture(
                    Path(temporary)
                )
                with patch.object(
                    unal_fixture,
                    "_PINNED_ASSETS",
                    _synthetic_pins(content),
                ), patch.multiple(
                    unal_fixture,
                    **limits,
                ), self.assertRaises(UnalFixtureError) as raised:
                    load_unal_k18_fixture(manifest_path)
                self.assertEqual(raised.exception.code, "SIZE_LIMIT_EXCEEDED")

    def test_changed_file_identity_during_read_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            path = Path(temporary) / "asset.bin"
            path.write_bytes(b"stable bytes")
            with patch.object(
                unal_fixture,
                "_path_identity",
                side_effect=(("before",), ("after",)),
            ), self.assertRaises(UnalFixtureError) as raised:
                unal_fixture._read_stable_file(
                    path,
                    label="test asset",
                    maximum_bytes=1024,
                )
        self.assertEqual(raised.exception.code, "FILE_CHANGED")

    def test_case_colliding_paths_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            manifest_path, manifest, content = _create_granted_fixture(Path(temporary))
            hologram_path = str(_asset(manifest, "hologram")["path"])
            _asset(manifest, "reference")["path"] = hologram_path.upper()
            _write_json(manifest_path, manifest)

            with patch.object(
                unal_fixture,
                "_PINNED_ASSETS",
                _synthetic_pins(content),
            ), self.assertRaises(UnalFixtureError) as raised:
                load_unal_k18_fixture(manifest_path)

        self.assertEqual(raised.exception.code, "UNSAFE_PATH")

    def test_git_blob_identity_survives_a_rewritten_sha256_manifest(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            root = Path(temporary)
            manifest_path, manifest, content = _create_granted_fixture(root)
            record = _asset(manifest, "reference")
            rewritten = b"attacker-controlled replacement bytes"
            (root / Path(str(record["path"]))).write_bytes(rewritten)
            record["size_bytes"] = len(rewritten)
            record["sha256"] = hashlib.sha256(rewritten).hexdigest()
            _write_json(manifest_path, manifest)

            with patch.object(
                unal_fixture,
                "_PINNED_ASSETS",
                _synthetic_pins(content),
            ), self.assertRaises(UnalFixtureError) as raised:
                load_unal_k18_fixture(manifest_path)

        self.assertEqual(raised.exception.code, "SOURCE_IDENTITY_MISMATCH")

    def test_nonregular_asset_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            root = Path(temporary)
            manifest_path, manifest, content = _create_granted_fixture(root)
            path = root / Path(str(_asset(manifest, "hologram")["path"]))
            path.unlink()
            path.mkdir()

            with patch.object(
                unal_fixture,
                "_PINNED_ASSETS",
                _synthetic_pins(content),
            ), self.assertRaises(UnalFixtureError) as raised:
                load_unal_k18_fixture(manifest_path)

        self.assertEqual(raised.exception.code, "UNSAFE_FILE_TYPE")

    def test_unsafe_relative_paths_fail_closed(self) -> None:
        invalid_paths = (
            "../escape.jpg",
            "/absolute.jpg",
            "C:/drive-relative.jpg",
            "payload\\backslash.jpg",
            "payload/../escape.jpg",
            "payload/CON.jpg",
            "payload/trailing-space /image.jpg",
        )
        for invalid_path in invalid_paths:
            with self.subTest(path=invalid_path), tempfile.TemporaryDirectory(
                dir=TEMPORARY_ROOT
            ) as temporary:
                manifest_path, manifest, content = _create_granted_fixture(Path(temporary))
                _asset(manifest, "hologram")["path"] = invalid_path
                _write_json(manifest_path, manifest)

                with patch.object(
                    unal_fixture,
                    "_PINNED_ASSETS",
                    _synthetic_pins(content),
                ), self.assertRaises(UnalFixtureError) as raised:
                    load_unal_k18_fixture(manifest_path)

                self.assertEqual(raised.exception.code, "UNSAFE_PATH")

    def test_symlink_asset_fails_closed_when_supported(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            root = Path(temporary)
            manifest_path, manifest, content = _create_granted_fixture(root)
            hologram_path = root / Path(_asset(manifest, "hologram")["path"])
            target = root / "outside.jpg"
            target.write_bytes(hologram_path.read_bytes())
            hologram_path.unlink()
            try:
                hologram_path.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")

            with patch.object(
                unal_fixture,
                "_PINNED_ASSETS",
                _synthetic_pins(content),
            ), self.assertRaises(UnalFixtureError) as raised:
                load_unal_k18_fixture(manifest_path)

        self.assertEqual(raised.exception.code, "UNSAFE_FILE_TYPE")

    def test_manifest_symlink_fails_closed_when_supported(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            root = Path(temporary)
            manifest_path, _manifest, content = _create_granted_fixture(root)
            linked_manifest = root / "linked-manifest.json"
            try:
                linked_manifest.symlink_to(manifest_path)
            except OSError as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")

            with patch.object(
                unal_fixture,
                "_PINNED_ASSETS",
                _synthetic_pins(content),
            ), self.assertRaises(UnalFixtureError) as raised:
                load_unal_k18_fixture(linked_manifest)

        self.assertEqual(raised.exception.code, "UNSAFE_PATH")

    def test_reparse_bit_and_intermediate_junction_checks_are_deterministic(self) -> None:
        self.assertTrue(
            unal_fixture._is_reparse(SimpleNamespace(st_file_attributes=0x400))
        )

        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            root = Path(temporary)
            manifest_path, _manifest, content = _create_granted_fixture(root)

            def junction_only_for_payload(path: Path) -> bool:
                return Path(path).name == "payload"

            with patch.object(
                unal_fixture,
                "_PINNED_ASSETS",
                _synthetic_pins(content),
            ), patch.object(
                unal_fixture,
                "_is_junction",
                side_effect=junction_only_for_payload,
            ), self.assertRaises(UnalFixtureError) as raised:
                load_unal_k18_fixture(manifest_path)

        self.assertEqual(raised.exception.code, "UNSAFE_PATH")

    def test_malformed_duplicate_and_unknown_json_fail_closed(self) -> None:
        invalid_documents = (
            b"{not-json",
            b'{"schema_version":"1.0","schema_version":"1.0"}',
            b"\xff\xfe",
        )
        for content in invalid_documents:
            with self.subTest(content=content), tempfile.TemporaryDirectory(
                dir=TEMPORARY_ROOT
            ) as temporary:
                manifest_path = Path(temporary) / "unal-k18-fixture.json"
                manifest_path.write_bytes(content)

                with self.assertRaises(UnalFixtureError) as raised:
                    load_unal_k18_fixture(manifest_path)

                self.assertEqual(raised.exception.code, "INVALID_MANIFEST")

        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            manifest_path = Path(temporary) / "unal-k18-fixture.json"
            document = _denied_manifest()
            document["unexpected"] = True
            _write_json(manifest_path, document)
            with self.assertRaises(UnalFixtureError) as raised:
                load_unal_k18_fixture(manifest_path)
            self.assertEqual(raised.exception.code, "INVALID_MANIFEST")

    def test_source_identity_is_pinned(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
            manifest_path, manifest, content = _create_granted_fixture(Path(temporary))
            fixture = manifest["fixture"]
            assert isinstance(fixture, dict)
            source = fixture["source"]
            assert isinstance(source, dict)
            source["code_commit_sha1"] = "0" * 40
            _write_json(manifest_path, manifest)

            with patch.object(
                unal_fixture,
                "_PINNED_ASSETS",
                _synthetic_pins(content),
            ), self.assertRaises(UnalFixtureError) as raised:
                load_unal_k18_fixture(manifest_path)

        self.assertEqual(raised.exception.code, "SOURCE_IDENTITY_MISMATCH")

    def test_optics_crop_intensity_and_independent_check_are_strict(self) -> None:
        mutations = {
            "plane-wave backend": lambda fixture: fixture["optics"].__setitem__(
                "backend", "angular_spectrum"
            ),
            "non-positive wavelength": lambda fixture: fixture["optics"].__setitem__(
                "wavelength_um", 0
            ),
            "boolean crop integer": lambda fixture: fixture["shared_crop_px"].__setitem__(
                "top", False
            ),
            "reversed candidate range": lambda fixture: fixture["intensity_mapping"].update(
                {"candidate_input_min": 1.0, "candidate_input_max": 1.0}
            ),
            "same parameter checker": lambda fixture: fixture["parameter_verification"].update(
                {
                    "transcribed_by": "same-person",
                    "independently_checked_by": "SAME-PERSON",
                }
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory(
                dir=TEMPORARY_ROOT
            ) as temporary:
                manifest_path, manifest, content = _create_granted_fixture(Path(temporary))
                fixture = manifest["fixture"]
                assert isinstance(fixture, dict)
                mutate(fixture)
                _write_json(manifest_path, manifest)

                with patch.object(
                    unal_fixture,
                    "_PINNED_ASSETS",
                    _synthetic_pins(content),
                ), self.assertRaises(UnalFixtureError) as raised:
                    load_unal_k18_fixture(manifest_path)

                self.assertEqual(raised.exception.code, "INVALID_MANIFEST")

    def test_permission_and_acceptance_states_are_closed_enums(self) -> None:
        documents = []
        denied = _denied_manifest()
        denied["permission"] = {"status": "UNKNOWN", "reason": "not enough"}
        documents.append(denied)

        invalid_claim = _denied_manifest()
        invalid_claim["acceptance"] = deepcopy(_acceptance())
        invalid_claim["acceptance"]["acceptance_result"] = "PASS"
        documents.append(invalid_claim)

        for document in documents:
            with tempfile.TemporaryDirectory(dir=TEMPORARY_ROOT) as temporary:
                manifest_path = Path(temporary) / "unal-k18-fixture.json"
                _write_json(manifest_path, document)
                with self.assertRaises(UnalFixtureError) as raised:
                    load_unal_k18_fixture(manifest_path)
                self.assertEqual(raised.exception.code, "INVALID_MANIFEST")


if __name__ == "__main__":
    unittest.main()
