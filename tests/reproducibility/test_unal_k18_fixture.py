from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path

from lensless_holography_workbench.unal_fixture import (
    LoadedFixture,
    UnavailableFixture,
    load_unal_k18_fixture,
)


ENVIRONMENT_VARIABLE = "UNAL_K18_FIXTURE_MANIFEST"
MANIFEST_SHA256_VARIABLE = "UNAL_K18_FIXTURE_MANIFEST_SHA256"
REQUIRED_ROLES = {
    "hologram",
    "reference",
    "parameter_screenshot",
    "published_intensity_visual",
}


def _git_blob_sha1(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(header)
    digest.update(content)
    return digest.hexdigest()


class ExternalUnalK18FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        configured = os.environ.get(ENVIRONMENT_VARIABLE) or None
        expected_manifest_sha256 = os.environ.get(MANIFEST_SHA256_VARIABLE) or None
        if configured is not None and expected_manifest_sha256 is None:
            raise ValueError(
                f"{MANIFEST_SHA256_VARIABLE} must pin every configured external manifest"
            )
        resolution = load_unal_k18_fixture(
            configured,
            expected_manifest_sha256=expected_manifest_sha256,
        )
        if isinstance(resolution, UnavailableFixture):
            raise unittest.SkipTest(
                f"UNAVAILABLE:{resolution.reason}: {resolution.detail}"
            )
        if not isinstance(resolution, LoadedFixture):
            raise TypeError("UNAL fixture loader returned an unknown resolution type")
        cls.fixture = resolution
        cls.manifest_path = Path(configured) if configured is not None else None

    def test_external_assets_are_hash_and_git_blob_verified_snapshots(self) -> None:
        self.assertEqual(self.fixture.status, "AVAILABLE")
        self.assertEqual(set(self.fixture.assets), REQUIRED_ROLES)
        for asset in self.fixture.assets.values():
            self.assertIsInstance(asset.content, bytes)
            self.assertEqual(asset.size_bytes, len(asset.content))
            self.assertEqual(asset.sha256, hashlib.sha256(asset.content).hexdigest())
            self.assertEqual(asset.git_blob_sha1, _git_blob_sha1(asset.content))

    def test_fixture_cannot_claim_u02_t2_acceptance(self) -> None:
        assert self.manifest_path is not None
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["acceptance"]["u02_t2_status"], "BEKLİYOR")
        self.assertEqual(
            manifest["acceptance"]["acceptance_result"],
            "NOT_EVALUATED",
        )
        self.assertIsNone(manifest["acceptance"]["acceptance_threshold"])


if __name__ == "__main__":
    unittest.main()
