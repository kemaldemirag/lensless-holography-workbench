"""Fail-closed loader for the licensed-external UNAL K18 fixture.

The loader never downloads data and never treats fixture availability as an
acceptance result.  Missing external data and an explicitly ungranted local
usage permission are represented as ``UNAVAILABLE``.  A present but invalid,
unsafe, partial, or tampered fixture raises :class:`UnalFixtureError`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import stat
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Never
from urllib.parse import urlsplit

SCHEMA_VERSION = "1.0"
FIXTURE_ID = "UNAL-K18-SINGLE-MICROORGANISM"
PRODUCT_ID = "U02"
TEST_ID = "U02-T2"
CODE_COMMIT_SHA1 = "5d5273e2ebb08735c531815d3e4b100a4e3f3772"
DOCUMENTATION_COMMIT_SHA1 = "536e70f1e179bf285492343b989a478d093c32e3"
SOURCE_REPOSITORY = "unal-optodigital/DLHM"
PROJECT_PAGE_URL = "https://unal-optodigital.github.io/DLHM/"

MAX_MANIFEST_BYTES = 64 * 1024
MAX_ASSET_BYTES = 128 * 1024 * 1024
MAX_TOTAL_ASSET_BYTES = 256 * 1024 * 1024

_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CONIN$",
    "CONOUT$",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
    *(f"COM{number}" for number in ("¹", "²", "³")),
    *(f"LPT{number}" for number in ("¹", "²", "³")),
}
_PINNED_ASSETS: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
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
)


class UnalFixtureError(ValueError):
    """A hard fixture-contract failure that must not be converted to a skip."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class FixtureAsset:
    """One source-pinned asset backed by an immutable verified snapshot."""

    role: str
    path: str
    source_name: str
    source_url: str
    git_blob_sha1: str
    accessed_on: str
    size_bytes: int
    sha256: str
    content: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class FixturePermission:
    status: str
    usage: str
    redistribution: str
    granted_by: str
    granted_on: str
    evidence_reference: str


@dataclass(frozen=True, slots=True)
class FixtureSource:
    repository: str
    code_commit_sha1: str
    documentation_commit_sha1: str
    project_page_url: str


@dataclass(frozen=True, slots=True)
class ParameterVerification:
    source_role: str
    transcribed_by: str
    independently_checked_by: str
    checked_on: str


@dataclass(frozen=True, slots=True)
class FixtureOptics:
    backend: str
    wavelength_um: float
    reconstruction_distance_um: float
    source_to_screen_distance_um: float
    detector_width_um: float
    detector_height_um: float
    output_width_um: float
    output_height_um: float
    input_sample_pitch_um: float
    output_sample_pitch_um: float


@dataclass(frozen=True, slots=True)
class SharedCrop:
    coordinate_system: str
    top: int
    left: int
    height: int
    width: int


@dataclass(frozen=True, slots=True)
class IntensityMapping:
    method: str
    clip: bool
    candidate_input_min: float
    candidate_input_max: float
    reference_input_min: float
    reference_input_max: float
    output_min: float
    output_max: float


@dataclass(frozen=True, slots=True)
class FixtureAcceptance:
    u02_t2_status: str
    acceptance_result: str
    acceptance_threshold: None


@dataclass(frozen=True, slots=True)
class LoadedFixture:
    """A complete fixture whose bytes and source identities were verified."""

    manifest_path: Path
    manifest_sha256: str
    permission: FixturePermission
    source: FixtureSource
    assets: Mapping[str, FixtureAsset]
    parameter_verification: ParameterVerification
    optics: FixtureOptics
    shared_crop_px: SharedCrop
    intensity_mapping: IntensityMapping
    acceptance: FixtureAcceptance
    status: str = field(init=False, default="AVAILABLE")


@dataclass(frozen=True, slots=True)
class UnavailableFixture:
    """A legitimate absence that CI may report as an explicit skip."""

    reason: str
    detail: str
    manifest_path: Path | None = None
    status: str = field(init=False, default="UNAVAILABLE")


FixtureResolution = LoadedFixture | UnavailableFixture


def _fail(code: str, message: str) -> Never:
    raise UnalFixtureError(code, message)


def _handle_identity(metadata: os.stat_result) -> tuple[object, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        getattr(metadata, "st_file_attributes", None),
    )


def _path_identity(metadata: os.stat_result) -> tuple[object, ...]:
    return (*_handle_identity(metadata), metadata.st_ctime_ns)


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def _is_junction(path: Path) -> bool:
    checker = getattr(os.path, "isjunction", None)
    return bool(checker is not None and checker(path))


def _local_manifest_path(value: str | Path) -> Path:
    """Reject explicit Windows network, device, ADS, and alias paths before I/O."""

    raw = os.fspath(value)
    if type(raw) is not str or not raw or "\x00" in raw:
        _fail("UNSAFE_PATH", "fixture manifest path must be a non-empty local path")
    normalized_prefix = raw.replace("/", "\\")
    if normalized_prefix.startswith(("\\\\", "\\\\?\\", "\\\\.\\")):
        _fail("UNSAFE_PATH", "fixture manifest path must not use UNC or a device namespace")
    windows_path = PureWindowsPath(raw)
    if windows_path.drive.startswith("\\\\"):
        _fail("UNSAFE_PATH", "fixture manifest path must not use a UNC drive")
    if windows_path.drive and not windows_path.root:
        _fail("UNSAFE_PATH", "fixture manifest path must not be drive-relative")
    for part in windows_path.parts:
        if part in (windows_path.anchor, "\\", "/"):
            continue
        if part in (".", "..") or part[-1:] in (" ", "."):
            _fail("UNSAFE_PATH", "fixture manifest path has an unsafe component")
        if any(ord(character) < 32 or character in '<>:"|?*' for character in part):
            _fail("UNSAFE_PATH", "fixture manifest path has an unsafe component")
        if part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            _fail("UNSAFE_PATH", "fixture manifest path uses a reserved Windows name")

    path = Path(raw)
    if os.name == "nt":
        # GetDriveTypeW performs a local classification and closes the mapped-
        # drive alias that an ordinary ``C:\\...`` lexical check cannot see.
        import ctypes

        absolute_drive = PureWindowsPath(os.path.abspath(raw)).drive
        if absolute_drive.startswith("\\\\"):
            _fail(
                "UNSAFE_PATH",
                "fixture manifest path must not resolve through a UNC working directory",
            )
        if absolute_drive:
            root = f"{absolute_drive}\\"
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
            if drive_type == 4:  # DRIVE_REMOTE
                _fail("UNSAFE_PATH", "fixture manifest path must not use a remote drive")
    return path


def _lstat(path: Path, *, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        _fail("READ_ERROR", f"cannot inspect {label}: {exc}")
    if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata) or _is_junction(path):
        _fail("UNSAFE_PATH", f"{label} must not be a link or reparse point")
    return metadata


def _assert_safe_root(root: Path) -> Path:
    metadata = _lstat(root, label="fixture root")
    if not stat.S_ISDIR(metadata.st_mode):
        _fail("UNSAFE_FILE_TYPE", "fixture root must be a directory")
    try:
        return root.resolve(strict=True)
    except OSError as exc:
        _fail("READ_ERROR", f"cannot resolve fixture root: {exc}")


def _read_stable_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
    expected_size: int | None = None,
) -> bytes:
    before = _lstat(path, label=label)
    if not stat.S_ISREG(before.st_mode):
        _fail("UNSAFE_FILE_TYPE", f"{label} must be a regular file")
    if before.st_size > maximum_bytes:
        _fail("SIZE_LIMIT_EXCEEDED", f"{label} exceeds its byte limit")
    if expected_size is not None and before.st_size != expected_size:
        _fail("SIZE_MISMATCH", f"{label} size does not match the manifest")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            _fail("UNSAFE_FILE_TYPE", f"{label} must be a regular file")
        # Windows exposes different ctime semantics through lstat and fstat,
        # so handle comparisons use the shared identity fields.  Path-to-path
        # comparison below additionally covers ctime.
        if _handle_identity(before) != _handle_identity(opened):
            _fail("FILE_CHANGED", f"{label} changed before it was opened")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                _fail("SIZE_LIMIT_EXCEEDED", f"{label} exceeds its byte limit")
        after_handle = os.fstat(descriptor)
    except UnalFixtureError:
        raise
    except OSError as exc:
        _fail("READ_ERROR", f"cannot read {label}: {exc}")
    finally:
        if descriptor is not None:
            os.close(descriptor)

    after = _lstat(path, label=label)
    if (
        _handle_identity(before) != _handle_identity(after_handle)
        or _path_identity(before) != _path_identity(after)
    ):
        _fail("FILE_CHANGED", f"{label} changed while it was read")
    content = b"".join(chunks)
    if len(content) != before.st_size:
        _fail("FILE_CHANGED", f"{label} size changed while it was read")
    if expected_size is not None and len(content) != expected_size:
        _fail("SIZE_MISMATCH", f"{label} size does not match the manifest")
    return content


def _reject_duplicate_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("INVALID_MANIFEST", f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _reject_json_constant(token: str) -> Never:
    _fail("INVALID_MANIFEST", f"non-finite JSON number is not allowed: {token}")


def _parse_manifest(content: bytes) -> dict[str, Any]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("INVALID_MANIFEST", f"manifest is not UTF-8: {exc}")
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except UnalFixtureError:
        raise
    except json.JSONDecodeError as exc:
        _fail("INVALID_MANIFEST", f"manifest is not valid JSON: {exc.msg}")
    if type(payload) is not dict:
        _fail("INVALID_MANIFEST", "manifest root must be an object")
    return payload


def _object(
    value: object,
    *,
    label: str,
    required: set[str],
) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("INVALID_MANIFEST", f"{label} must be an object")
    payload: dict[str, Any] = value
    missing = required.difference(payload)
    extra = set(payload).difference(required)
    if missing:
        _fail("INVALID_MANIFEST", f"{label} is missing: {', '.join(sorted(missing))}")
    if extra:
        _fail("INVALID_MANIFEST", f"{label} has unknown fields: {', '.join(sorted(extra))}")
    return payload


def _string(value: object, *, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        _fail("INVALID_MANIFEST", f"{label} must be a non-empty trimmed string")
    if any(ord(character) < 32 for character in value):
        _fail("INVALID_MANIFEST", f"{label} contains a control character")
    return value


def _const(value: object, expected: object, *, label: str) -> None:
    if value != expected or type(value) is not type(expected):
        _fail("INVALID_MANIFEST", f"{label} must be {expected!r}")


def _iso_date(value: object, *, label: str) -> str:
    text = _string(value, label=label)
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        _fail("INVALID_MANIFEST", f"{label} must be an ISO 8601 calendar date")
    if parsed.isoformat() != text:
        _fail("INVALID_MANIFEST", f"{label} must use YYYY-MM-DD")
    return text


def _https_url(value: object, *, label: str) -> str:
    text = _string(value, label=label)
    try:
        parsed = urlsplit(text)
        parsed.port
    except ValueError:
        _fail("INVALID_MANIFEST", f"{label} is not a valid URL")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        _fail(
            "INVALID_MANIFEST",
            f"{label} must be an HTTPS URL without credentials or a fragment",
        )
    return text


def _hex(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    text = _string(value, label=label)
    if pattern.fullmatch(text) is None:
        _fail("INVALID_MANIFEST", f"{label} has an invalid lowercase hexadecimal value")
    return text


def _positive_number(value: object, *, label: str) -> float:
    if type(value) not in (int, float):
        _fail("INVALID_MANIFEST", f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        _fail("INVALID_MANIFEST", f"{label} must be finite and positive")
    return result


def _finite_number(value: object, *, label: str) -> float:
    if type(value) not in (int, float):
        _fail("INVALID_MANIFEST", f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        _fail("INVALID_MANIFEST", f"{label} must be finite")
    return result


def _nonnegative_integer(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0:
        _fail("INVALID_MANIFEST", f"{label} must be a non-negative integer")
    return value


def _positive_integer(value: object, *, label: str) -> int:
    if type(value) is not int or value <= 0:
        _fail("INVALID_MANIFEST", f"{label} must be a positive integer")
    return value


def _relative_asset_path(value: object, *, label: str) -> str:
    text = _string(value, label=label)
    if not text.isascii():
        _fail("UNSAFE_PATH", f"{label} must use ASCII characters")
    if "\\" in text or ":" in text or text.startswith(("/", "//")):
        _fail("UNSAFE_PATH", f"{label} must be a canonical relative POSIX path")
    pure = PurePosixPath(text)
    if pure.is_absolute() or pure.as_posix() != text or any(part in ("", ".", "..") for part in pure.parts):
        _fail("UNSAFE_PATH", f"{label} must be a canonical relative POSIX path")
    if len(text) > 240:
        _fail("UNSAFE_PATH", f"{label} is too long")
    for part in pure.parts:
        if len(part) > 120 or part[-1] in (" ", "."):
            _fail("UNSAFE_PATH", f"{label} contains an unsafe path component")
        if any(ord(character) < 32 or character in '<>"|?*' for character in part):
            _fail("UNSAFE_PATH", f"{label} contains an unsafe path component")
        if part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            _fail("UNSAFE_PATH", f"{label} contains a reserved Windows name")
    return text


def _resolve_asset(root: Path, relative_path: str, *, role: str) -> Path | None:
    current = root
    parts = PurePosixPath(relative_path).parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            _fail("READ_ERROR", f"cannot inspect asset {role}: {exc}")
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata) or _is_junction(current):
            _fail("UNSAFE_PATH", f"asset {role} traverses a link or reparse point")
        if index < len(parts) - 1:
            if not stat.S_ISDIR(metadata.st_mode):
                _fail("UNSAFE_FILE_TYPE", f"asset {role} has a non-directory parent")
        elif not stat.S_ISREG(metadata.st_mode):
            _fail("UNSAFE_FILE_TYPE", f"asset {role} must be a regular file")
    try:
        current.resolve(strict=True).relative_to(root)
    except (OSError, ValueError):
        _fail("UNSAFE_PATH", f"asset {role} escapes the fixture root")
    return current


def _git_blob_sha1(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(header)
    digest.update(content)
    return digest.hexdigest()


def _validate_acceptance(value: object) -> FixtureAcceptance:
    payload = _object(
        value,
        label="acceptance",
        required={"u02_t2_status", "acceptance_result", "acceptance_threshold"},
    )
    _const(payload["u02_t2_status"], "BEKLİYOR", label="acceptance.u02_t2_status")
    _const(
        payload["acceptance_result"],
        "NOT_EVALUATED",
        label="acceptance.acceptance_result",
    )
    _const(
        payload["acceptance_threshold"],
        None,
        label="acceptance.acceptance_threshold",
    )
    return FixtureAcceptance("BEKLİYOR", "NOT_EVALUATED", None)


def _validate_source(value: object) -> FixtureSource:
    payload = _object(
        value,
        label="fixture.source",
        required={
            "repository",
            "code_commit_sha1",
            "documentation_commit_sha1",
            "project_page_url",
        },
    )
    expected = {
        "repository": SOURCE_REPOSITORY,
        "code_commit_sha1": CODE_COMMIT_SHA1,
        "documentation_commit_sha1": DOCUMENTATION_COMMIT_SHA1,
        "project_page_url": PROJECT_PAGE_URL,
    }
    for name, identity in expected.items():
        if type(payload[name]) is not str or payload[name] != identity:
            _fail(
                "SOURCE_IDENTITY_MISMATCH",
                f"fixture.source.{name} does not match the pinned source profile",
            )
    return FixtureSource(
        SOURCE_REPOSITORY,
        CODE_COMMIT_SHA1,
        DOCUMENTATION_COMMIT_SHA1,
        PROJECT_PAGE_URL,
    )


def _validate_permission(value: object) -> FixturePermission | str:
    if type(value) is not dict:
        _fail("INVALID_MANIFEST", "permission must be an object")
    status = value.get("status")
    if status == "NOT_GRANTED":
        payload = _object(
            value,
            label="permission",
            required={"status", "reason"},
        )
        return _string(payload["reason"], label="permission.reason")
    if status != "GRANTED":
        _fail("INVALID_MANIFEST", "permission.status must be GRANTED or NOT_GRANTED")
    payload = _object(
        value,
        label="permission",
        required={
            "status",
            "usage",
            "redistribution",
            "granted_by",
            "granted_on",
            "evidence_reference",
        },
    )
    _const(
        payload["usage"],
        "LOCAL_U02_T2_VALIDATION_ONLY",
        label="permission.usage",
    )
    _const(
        payload["redistribution"],
        "PROHIBITED",
        label="permission.redistribution",
    )
    return FixturePermission(
        status="GRANTED",
        usage="LOCAL_U02_T2_VALIDATION_ONLY",
        redistribution="PROHIBITED",
        granted_by=_string(payload["granted_by"], label="permission.granted_by"),
        granted_on=_iso_date(payload["granted_on"], label="permission.granted_on"),
        evidence_reference=_string(
            payload["evidence_reference"],
            label="permission.evidence_reference",
        ),
    )


def _validate_parameter_verification(value: object) -> ParameterVerification:
    payload = _object(
        value,
        label="fixture.parameter_verification",
        required={
            "source_role",
            "transcribed_by",
            "independently_checked_by",
            "checked_on",
        },
    )
    _const(
        payload["source_role"],
        "parameter_screenshot",
        label="fixture.parameter_verification.source_role",
    )
    transcribed_by = _string(
        payload["transcribed_by"],
        label="fixture.parameter_verification.transcribed_by",
    )
    checked_by = _string(
        payload["independently_checked_by"],
        label="fixture.parameter_verification.independently_checked_by",
    )
    if unicodedata.normalize("NFKC", transcribed_by).casefold() == unicodedata.normalize(
        "NFKC", checked_by
    ).casefold():
        _fail(
            "INVALID_MANIFEST",
            "parameter transcription and independent check must name different identities",
        )
    return ParameterVerification(
        source_role="parameter_screenshot",
        transcribed_by=transcribed_by,
        independently_checked_by=checked_by,
        checked_on=_iso_date(
            payload["checked_on"],
            label="fixture.parameter_verification.checked_on",
        ),
    )


def _validate_optics(value: object) -> FixtureOptics:
    numeric_fields = {
        "wavelength_um",
        "reconstruction_distance_um",
        "source_to_screen_distance_um",
        "detector_width_um",
        "detector_height_um",
        "output_width_um",
        "output_height_um",
        "input_sample_pitch_um",
        "output_sample_pitch_um",
    }
    payload = _object(
        value,
        label="fixture.optics",
        required={"backend", *numeric_fields},
    )
    _const(
        payload["backend"],
        "point_source_kirchhoff_helmholtz",
        label="fixture.optics.backend",
    )
    values = {
        name: _positive_number(payload[name], label=f"fixture.optics.{name}")
        for name in numeric_fields
    }
    return FixtureOptics(
        backend="point_source_kirchhoff_helmholtz",
        wavelength_um=values["wavelength_um"],
        reconstruction_distance_um=values["reconstruction_distance_um"],
        source_to_screen_distance_um=values["source_to_screen_distance_um"],
        detector_width_um=values["detector_width_um"],
        detector_height_um=values["detector_height_um"],
        output_width_um=values["output_width_um"],
        output_height_um=values["output_height_um"],
        input_sample_pitch_um=values["input_sample_pitch_um"],
        output_sample_pitch_um=values["output_sample_pitch_um"],
    )


def _validate_crop(value: object) -> SharedCrop:
    payload = _object(
        value,
        label="fixture.shared_crop_px",
        required={"coordinate_system", "top", "left", "height", "width"},
    )
    _const(
        payload["coordinate_system"],
        "TOP_LEFT_ZERO_BASED_HALF_OPEN",
        label="fixture.shared_crop_px.coordinate_system",
    )
    return SharedCrop(
        coordinate_system="TOP_LEFT_ZERO_BASED_HALF_OPEN",
        top=_nonnegative_integer(payload["top"], label="fixture.shared_crop_px.top"),
        left=_nonnegative_integer(payload["left"], label="fixture.shared_crop_px.left"),
        height=_positive_integer(payload["height"], label="fixture.shared_crop_px.height"),
        width=_positive_integer(payload["width"], label="fixture.shared_crop_px.width"),
    )


def _validate_intensity_mapping(value: object) -> IntensityMapping:
    numeric_fields = {
        "candidate_input_min",
        "candidate_input_max",
        "reference_input_min",
        "reference_input_max",
        "output_min",
        "output_max",
    }
    payload = _object(
        value,
        label="fixture.intensity_mapping",
        required={"method", "clip", *numeric_fields},
    )
    _const(
        payload["method"],
        "FIXED_RANGE_LINEAR",
        label="fixture.intensity_mapping.method",
    )
    _const(payload["clip"], True, label="fixture.intensity_mapping.clip")
    values = {
        name: _finite_number(
            payload[name],
            label=f"fixture.intensity_mapping.{name}",
        )
        for name in numeric_fields
    }
    for minimum, maximum in (
        ("candidate_input_min", "candidate_input_max"),
        ("reference_input_min", "reference_input_max"),
        ("output_min", "output_max"),
    ):
        if values[minimum] >= values[maximum]:
            _fail(
                "INVALID_MANIFEST",
                f"fixture.intensity_mapping.{minimum} must be less than {maximum}",
            )
    return IntensityMapping(
        method="FIXED_RANGE_LINEAR",
        clip=True,
        candidate_input_min=values["candidate_input_min"],
        candidate_input_max=values["candidate_input_max"],
        reference_input_min=values["reference_input_min"],
        reference_input_max=values["reference_input_max"],
        output_min=values["output_min"],
        output_max=values["output_max"],
    )


def _validate_asset_records(value: object) -> dict[str, dict[str, Any]]:
    if type(value) is not list or len(value) != len(_PINNED_ASSETS):
        _fail("INVALID_MANIFEST", "fixture.assets must contain exactly four records")
    records: dict[str, dict[str, Any]] = {}
    normalized_paths: set[str] = set()
    for index, item in enumerate(value):
        payload = _object(
            item,
            label=f"fixture.assets[{index}]",
            required={
                "role",
                "path",
                "source_name",
                "source_url",
                "git_blob_sha1",
                "sha256",
                "size_bytes",
                "accessed_on",
            },
        )
        role = _string(payload["role"], label=f"fixture.assets[{index}].role")
        if role not in _PINNED_ASSETS or role in records:
            _fail("INVALID_MANIFEST", f"invalid or duplicate fixture asset role: {role}")
        path = _relative_asset_path(
            payload["path"],
            label=f"fixture.assets[{index}].path",
        )
        normalized = unicodedata.normalize("NFKC", path).casefold()
        if normalized in normalized_paths:
            _fail("UNSAFE_PATH", "fixture asset paths collide after normalization")
        normalized_paths.add(normalized)
        expected_name, expected_blob = _PINNED_ASSETS[role]
        _const(
            payload["source_name"],
            expected_name,
            label=f"fixture.assets[{index}].source_name",
        )
        blob = _hex(
            payload["git_blob_sha1"],
            label=f"fixture.assets[{index}].git_blob_sha1",
            pattern=_HEX40,
        )
        if blob != expected_blob:
            _fail("SOURCE_IDENTITY_MISMATCH", f"asset {role} has an unpinned Git blob identity")
        records[role] = {
            "role": role,
            "path": path,
            "source_name": expected_name,
            "source_url": _https_url(
                payload["source_url"],
                label=f"fixture.assets[{index}].source_url",
            ),
            "git_blob_sha1": blob,
            "sha256": _hex(
                payload["sha256"],
                label=f"fixture.assets[{index}].sha256",
                pattern=_HEX64,
            ),
            "size_bytes": _positive_integer(
                payload["size_bytes"],
                label=f"fixture.assets[{index}].size_bytes",
            ),
            "accessed_on": _iso_date(
                payload["accessed_on"],
                label=f"fixture.assets[{index}].accessed_on",
            ),
        }
    if set(records) != set(_PINNED_ASSETS):
        _fail("INVALID_MANIFEST", "fixture.assets does not contain every pinned role")
    total_size = sum(record["size_bytes"] for record in records.values())
    if any(record["size_bytes"] > MAX_ASSET_BYTES for record in records.values()):
        _fail("SIZE_LIMIT_EXCEEDED", "a fixture asset exceeds the per-file byte limit")
    if total_size > MAX_TOTAL_ASSET_BYTES:
        _fail("SIZE_LIMIT_EXCEEDED", "fixture assets exceed the total byte limit")
    return records


def _load_assets(
    root: Path,
    records: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, FixtureAsset] | UnavailableFixture:
    located: dict[str, Path | None] = {
        role: _resolve_asset(root, record["path"], role=role)
        for role, record in records.items()
    }
    missing = sorted(role for role, path in located.items() if path is None)
    if len(missing) == len(records):
        return UnavailableFixture(
            reason="PAYLOAD_NOT_PROVISIONED",
            detail="none of the four licensed-external assets is present",
        )
    if missing:
        _fail("PARTIAL_PAYLOAD", f"fixture assets are missing: {', '.join(missing)}")

    snapshots: dict[str, FixtureAsset] = {}
    for role in _PINNED_ASSETS:
        record = records[role]
        path = located[role]
        assert path is not None
        content = _read_stable_file(
            path,
            label=f"asset {role}",
            maximum_bytes=MAX_ASSET_BYTES,
            expected_size=record["size_bytes"],
        )
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(actual_sha256, record["sha256"]):
            _fail("HASH_MISMATCH", f"asset {role} SHA-256 does not match the manifest")
        actual_blob = _git_blob_sha1(content)
        if not hmac.compare_digest(actual_blob, record["git_blob_sha1"]):
            _fail("SOURCE_IDENTITY_MISMATCH", f"asset {role} content does not match its pinned Git blob")
        snapshots[role] = FixtureAsset(content=content, **record)
    return MappingProxyType(snapshots)


def load_unal_k18_fixture(
    manifest_path: str | Path | None,
    *,
    expected_manifest_sha256: str | None = None,
) -> FixtureResolution:
    """Load and verify one externally provisioned UNAL K18 fixture.

    This function issues no network requests and performs no writes.  Operating
    system mounts and sync providers remain the caller's responsibility.  A
    separately approved manifest digest may be supplied as a trust anchor.
    Callers may skip only when the returned value is
    :class:`UnavailableFixture`; every :class:`UnalFixtureError` is a hard
    validation failure.
    """

    if manifest_path is None:
        return UnavailableFixture(
            reason="NOT_CONFIGURED",
            detail="UNAL K18 fixture manifest path is not configured",
        )
    path = _local_manifest_path(manifest_path)
    try:
        path_metadata = path.lstat()
    except FileNotFoundError:
        return UnavailableFixture(
            reason="MANIFEST_NOT_FOUND",
            detail="UNAL K18 fixture manifest is not present",
            manifest_path=path,
        )
    except OSError as exc:
        _fail("READ_ERROR", f"cannot inspect fixture manifest: {exc}")
    if (
        stat.S_ISLNK(path_metadata.st_mode)
        or _is_reparse(path_metadata)
        or _is_junction(path)
    ):
        _fail("UNSAFE_PATH", "fixture manifest must not be a link or reparse point")
    if not stat.S_ISREG(path_metadata.st_mode):
        _fail("UNSAFE_FILE_TYPE", "fixture manifest must be a regular file")
    root = _assert_safe_root(path.parent)
    manifest_content = _read_stable_file(
        path,
        label="fixture manifest",
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    manifest_sha256 = hashlib.sha256(manifest_content).hexdigest()
    if expected_manifest_sha256 is not None:
        if (
            type(expected_manifest_sha256) is not str
            or _HEX64.fullmatch(expected_manifest_sha256) is None
        ):
            _fail(
                "INVALID_EXPECTATION",
                "expected manifest SHA-256 must be 64 lowercase hexadecimal characters",
            )
        if not hmac.compare_digest(manifest_sha256, expected_manifest_sha256):
            _fail(
                "MANIFEST_HASH_MISMATCH",
                "fixture manifest does not match the separately approved SHA-256",
            )
    payload = _parse_manifest(manifest_content)
    top = _object(
        payload,
        label="manifest",
        required={
            "schema_version",
            "fixture_id",
            "product_id",
            "test_id",
            "permission",
            "fixture",
            "acceptance",
        },
    )
    _const(top["schema_version"], SCHEMA_VERSION, label="schema_version")
    _const(top["fixture_id"], FIXTURE_ID, label="fixture_id")
    _const(top["product_id"], PRODUCT_ID, label="product_id")
    _const(top["test_id"], TEST_ID, label="test_id")
    acceptance = _validate_acceptance(top["acceptance"])
    permission = _validate_permission(top["permission"])
    if isinstance(permission, str):
        _const(top["fixture"], None, label="fixture")
        return UnavailableFixture(
            reason="PERMISSION_NOT_GRANTED",
            detail=permission,
            manifest_path=path,
        )

    fixture = _object(
        top["fixture"],
        label="fixture",
        required={
            "source",
            "assets",
            "parameter_verification",
            "optics",
            "shared_crop_px",
            "intensity_mapping",
        },
    )
    source = _validate_source(fixture["source"])
    asset_records = _validate_asset_records(fixture["assets"])
    parameter_verification = _validate_parameter_verification(
        fixture["parameter_verification"]
    )
    optics = _validate_optics(fixture["optics"])
    shared_crop = _validate_crop(fixture["shared_crop_px"])
    intensity_mapping = _validate_intensity_mapping(fixture["intensity_mapping"])
    assets = _load_assets(root, asset_records)
    if isinstance(assets, UnavailableFixture):
        return UnavailableFixture(
            reason=assets.reason,
            detail=assets.detail,
            manifest_path=path,
        )
    return LoadedFixture(
        manifest_path=path,
        manifest_sha256=manifest_sha256,
        permission=permission,
        source=source,
        assets=assets,
        parameter_verification=parameter_verification,
        optics=optics,
        shared_crop_px=shared_crop,
        intensity_mapping=intensity_mapping,
        acceptance=acceptance,
    )


__all__ = [
    "FixtureAcceptance",
    "FixtureAsset",
    "FixtureResolution",
    "LoadedFixture",
    "UnavailableFixture",
    "UnalFixtureError",
    "load_unal_k18_fixture",
]
