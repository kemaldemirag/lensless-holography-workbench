"""Build and package the Windows desktop application without signing claims."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import stat
import struct
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence


APP_DIRECTORY_NAME = "LenslessHolographyWorkbench"
PROJECT_NAME = "lensless-holography-workbench"
PRODUCT_ID = "U02"
TEST_ID = "U02-T2"
ACCEPTANCE_STATUS = "BEKLİYOR"
COMPARISON_RESULT = "NOT_EVALUATED"
SIGNING_REASON = (
    "No signing certificate or Authenticode verification was supplied."
)
READINESS_REASON = (
    "Repository LICENSE, publisher identity, signing certificate, and "
    "installer validation are not established."
)
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
MANIFEST_NAME = "RELEASE-MANIFEST.json"
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
WINDOWS_INVALID_CHARACTERS = frozenset('<>:"/\\|?*')
PE_MACHINE_AMD64 = 0x8664


class ReleaseError(RuntimeError):
    """Raised when a release would be incomplete or misleading."""


@dataclass(frozen=True, slots=True)
class ProjectMetadata:
    name: str
    version: str
    description: str


@dataclass(frozen=True, slots=True)
class ReleaseArtifacts:
    directory: Path
    archive: Path
    archive_sha256: str
    checksum_file: Path


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_project_metadata(pyproject: str | Path) -> ProjectMetadata:
    path = Path(pyproject)
    with path.open("rb") as stream:
        document = tomllib.load(stream)
    project = document.get("project")
    if not isinstance(project, dict):
        raise ReleaseError("pyproject.toml must contain a [project] table")
    values = {
        key: project.get(key) for key in ("name", "version", "description")
    }
    if not all(isinstance(value, str) and value.strip() for value in values.values()):
        raise ReleaseError("project name, version, and description must be non-empty")
    return ProjectMetadata(
        name=values["name"],
        version=values["version"],
        description=values["description"],
    )


def windows_version_tuple(version: str) -> tuple[int, int, int, int]:
    core = version.split("+", 1)[0].split("-", 1)[0]
    pieces = core.split(".")
    if not 1 <= len(pieces) <= 4 or any(
        not piece.isascii() or not piece.isdecimal() for piece in pieces
    ):
        raise ReleaseError(
            "project version must begin with one to four numeric components"
        )
    numbers = [int(piece) for piece in pieces]
    if any(number > 65535 for number in numbers):
        raise ReleaseError("Windows version components must be at most 65535")
    return tuple((numbers + [0, 0, 0, 0])[:4])  # type: ignore[return-value]


def write_windows_version_file(
    output: str | Path,
    metadata: ProjectMetadata,
) -> Path:
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    version = windows_version_tuple(metadata.version)
    dotted = ".".join(str(item) for item in version)
    contents = f"""# Generated from pyproject.toml. Do not edit.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version!r},
    prodvers={version!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('FileDescription', {metadata.description!r}),
        StringStruct('FileVersion', {dotted!r}),
        StringStruct('InternalName', {APP_DIRECTORY_NAME!r}),
        StringStruct('OriginalFilename', 'LenslessHolographyWorkbench.exe'),
        StringStruct('ProductName', 'Lensless Holography Workbench'),
        StringStruct('ProductVersion', {metadata.version!r})
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    destination.write_text(contents, encoding="utf-8", newline="\n")
    return destination


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_windows_x64_executable(app_directory: str | Path) -> Path:
    """Require a regular AMD64 PE executable at the portable app root."""

    executable = Path(app_directory) / "LenslessHolographyWorkbench.exe"
    if not executable.is_file() or executable.is_symlink():
        raise ReleaseError(f"packaged executable is missing: {executable}")
    with executable.open("rb") as stream:
        dos_header = stream.read(64)
        if len(dos_header) != 64 or dos_header[:2] != b"MZ":
            raise ReleaseError("packaged executable is not a PE file")
        pe_offset = struct.unpack_from("<I", dos_header, 0x3C)[0]
        if pe_offset < 64 or pe_offset > 16 * 1024 * 1024:
            raise ReleaseError("packaged executable has an invalid PE offset")
        stream.seek(pe_offset)
        header = stream.read(6)
    if len(header) != 6 or header[:4] != b"PE\0\0":
        raise ReleaseError("packaged executable has no PE signature")
    machine = struct.unpack_from("<H", header, 4)[0]
    if machine != PE_MACHINE_AMD64:
        raise ReleaseError(
            f"packaged executable is not Windows x64 (machine=0x{machine:04x})"
        )
    return executable


def validate_release_member(value: str) -> str:
    """Validate one portable, Windows-safe relative archive member."""

    if not value or "\\" in value:
        raise ReleaseError(f"invalid release path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseError(f"release path must be relative and normalized: {value!r}")
    for part in path.parts:
        if part.endswith((" ", ".")):
            raise ReleaseError(f"Windows path must not end in dot or space: {value!r}")
        if any(character in WINDOWS_INVALID_CHARACTERS for character in part):
            raise ReleaseError(f"Windows path contains an invalid character: {value!r}")
        if any(ord(character) < 32 for character in part):
            raise ReleaseError(f"Windows path contains a control character: {value!r}")
        base_name = part.split(".", 1)[0].upper()
        if base_name in WINDOWS_RESERVED_NAMES:
            raise ReleaseError(f"Windows reserved path name is not allowed: {value!r}")
    normalized = path.as_posix()
    if normalized != value:
        raise ReleaseError(f"release path must be normalized: {value!r}")
    return normalized


def validate_member_names(values: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(validate_release_member(value) for value in values)
    casefolded: dict[str, str] = {}
    for value in normalized:
        identity = value.casefold()
        previous = casefolded.get(identity)
        if previous is not None:
            raise ReleaseError(
                f"case-insensitive release path collision: {previous!r}, {value!r}"
            )
        casefolded[identity] = value
    return normalized


def _is_reparse_point(path: Path, metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    is_junction = getattr(os.path, "isjunction", lambda _path: False)
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT) or bool(
        is_junction(path)
    )


def _relative_files(root: Path) -> tuple[Path, ...]:
    if not root.is_dir():
        raise ReleaseError(f"application directory does not exist: {root}")
    root_metadata = root.lstat()
    if root.is_symlink() or _is_reparse_point(root, root_metadata):
        raise ReleaseError(f"release root must not be a reparse point: {root}")

    files: list[Path] = []
    members: list[str] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name.casefold()):
                path = Path(entry.path)
                relative = path.relative_to(root)
                member = relative.as_posix()
                validate_release_member(member)
                members.append(member)
                metadata = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or _is_reparse_point(path, metadata):
                    raise ReleaseError(
                        f"release input must not contain reparse points: {path}"
                    )
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(path)
                elif stat.S_ISREG(metadata.st_mode):
                    files.append(relative)
                else:
                    raise ReleaseError(
                        f"release input must contain only regular files: {path}"
                    )
    validate_member_names(members)
    if not files:
        raise ReleaseError("application directory must contain at least one file")
    return tuple(sorted(files, key=lambda item: item.as_posix()))


def _pyinstaller_version() -> str | None:
    try:
        return importlib.metadata.version("pyinstaller")
    except importlib.metadata.PackageNotFoundError:
        return None


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _git_build_state(root: Path) -> tuple[str | None, bool | None]:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None
    return head or None, not bool(status)


def validate_packaged_self_test_results(
    results: object,
) -> dict[str, dict[str, object]]:
    """Validate frozen-runtime evidence before recording or publishing it."""

    if not isinstance(results, dict) or set(results) != {"runtime", "ui"}:
        raise ReleaseError("packaged self-test results must contain runtime and ui")
    validated: dict[str, dict[str, object]] = {}
    for label in ("runtime", "ui"):
        payload = results.get(label)
        if not isinstance(payload, dict) or payload.get("self_test") != "OK":
            raise ReleaseError(f"packaged {label} self-test did not pass")
        if payload.get("product_id") != PRODUCT_ID or payload.get("test_id") != TEST_ID:
            raise ReleaseError(f"packaged {label} self-test identity is invalid")
        runtime = payload.get("runtime")
        checks = payload.get("checks")
        boundary = payload.get("acceptance_boundary")
        if not isinstance(runtime, dict) or not all(
            isinstance(runtime.get(key), str) and runtime.get(key)
            for key in ("python", "numpy", "pillow", "tk")
        ):
            raise ReleaseError(f"packaged {label} runtime metadata is invalid")
        if runtime.get("image_tk_imported") is not True:
            raise ReleaseError(f"packaged {label} ImageTk import did not pass")
        if not isinstance(checks, dict) or any(
            checks.get(key) != "OK"
            for key in (
                "numpy_fft",
                "pillow_jpeg",
                "float_tiff",
                "resource_limits",
            )
        ):
            raise ReleaseError(f"packaged {label} numerical/image checks failed")
        ui = checks.get("ui")
        if not isinstance(ui, dict) or ui.get("requested") is not (label == "ui"):
            raise ReleaseError(f"packaged {label} UI request metadata is invalid")
        if label == "ui" and (
            ui.get("created") is not True
            or ui.get("image_tk_photo_created") is not True
            or not isinstance(ui.get("requested_width"), int)
            or not isinstance(ui.get("requested_height"), int)
            or ui["requested_width"] <= 0
            or ui["requested_height"] <= 0
        ):
            raise ReleaseError("packaged UI smoke test did not create valid widgets")
        if not isinstance(boundary, dict) or (
            boundary.get("status") != ACCEPTANCE_STATUS
            or boundary.get("comparison_result") != COMPARISON_RESULT
            or boundary.get("comparison_threshold") is not None
            or boundary.get("physical_focus_claim") is not False
        ):
            raise ReleaseError(
                f"packaged {label} self-test changed the acceptance boundary"
            )
        validated[label] = payload
    return validated


def create_release_manifest(
    app_directory: str | Path,
    metadata: ProjectMetadata,
    runtime_validation: object,
    source_repository: str | Path | None = None,
) -> dict[str, object]:
    root = Path(app_directory)
    validate_windows_x64_executable(root)
    validated_runtime = validate_packaged_self_test_results(runtime_validation)
    records = []
    for relative in _relative_files(root):
        path = root / relative
        records.append(
            {
                "path": PurePosixPath("app", *relative.parts).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    git_head, git_clean = (
        _git_build_state(Path(source_repository).resolve())
        if source_repository is not None
        else (None, None)
    )
    return {
        "schema_version": "1.0",
        "product_id": PRODUCT_ID,
        "test_id": TEST_ID,
        "name": metadata.name,
        "version": metadata.version,
        "platform": "windows",
        "architecture": "x64",
        "application_directory": "app",
        "files": records,
        "packaged_runtime_validation": validated_runtime,
        "build_environment": {
            "python": platform.python_version(),
            "pyinstaller": _pyinstaller_version(),
            "pyinstaller_hooks_contrib": _distribution_version(
                "pyinstaller-hooks-contrib"
            ),
            "numpy": _distribution_version("numpy"),
            "pillow": _distribution_version("Pillow"),
            "windows": platform.version(),
            "machine": platform.machine(),
            "pointer_bits": struct.calcsize("P") * 8,
            "zlib": zlib.ZLIB_VERSION,
            "git_head": git_head,
            "git_worktree_clean": git_clean,
            "source_provenance": (
                "RECORDED_GIT_WORKTREE"
                if git_head is not None
                else "NOT_ESTABLISHED"
            ),
            "archive_determinism": "FIXED_METADATA_FOR_IDENTICAL_FILE_TREE",
            "binary_reproducibility": "NOT_ESTABLISHED",
        },
        "code_signing": {
            "status": "NOT_SIGNED",
            "verified": False,
            "reason": SIGNING_REASON,
        },
        "authenticity": {
            "status": "NOT_ESTABLISHED",
            "publisher": None,
        },
        "distribution_readiness": {
            "status": "INTERNAL_VALIDATION_ONLY",
            "installer": "NOT_BUILT",
            "reason": READINESS_REASON,
        },
        "acceptance_boundary": {
            "status": ACCEPTANCE_STATUS,
            "comparison_result": COMPARISON_RESULT,
            "comparison_threshold": None,
            "physical_focus_claim": False,
        },
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _replace_directory_atomic(source: Path, destination: Path) -> None:
    """Atomically publish a directory, retrying only transient Windows locks."""

    for attempt in range(5):
        try:
            source.replace(destination)
            return
        except PermissionError:
            if destination.exists() or attempt == 4:
                raise
            time.sleep(0.1 * (attempt + 1))


def _write_deterministic_zip(
    source: Path,
    destination: Path,
    *,
    root_name: str | None = None,
) -> None:
    archive_root = root_name or source.name
    validate_release_member(archive_root)
    relative_files = _relative_files(source)
    with zipfile.ZipFile(
        destination,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for relative in relative_files:
            path = source / relative
            member = PurePosixPath(archive_root, *relative.parts).as_posix()
            info = zipfile.ZipInfo(member, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            with path.open("rb") as stream:
                archive.writestr(info, stream.read(), compresslevel=9)


def _verify_release_top_level(root: Path, *, packaged: bool) -> None:
    expected = {"app", MANIFEST_NAME}
    if packaged:
        archive_name = f"{root.name}.zip"
        expected.update({archive_name, f"{archive_name}.sha256"})
    actual: set[str] = set()
    for path in root.iterdir():
        validate_release_member(path.name)
        metadata = path.lstat()
        if path.is_symlink() or _is_reparse_point(path, metadata):
            raise ReleaseError(f"release output contains a reparse point: {path}")
        if path.name == "app":
            if not stat.S_ISDIR(metadata.st_mode):
                raise ReleaseError("release app entry must be a directory")
        elif not stat.S_ISREG(metadata.st_mode):
            raise ReleaseError(f"release output must contain regular files: {path}")
        actual.add(path.name)
    validate_member_names(sorted(actual))
    if actual != expected:
        raise ReleaseError(
            f"release top-level set mismatch; expected={sorted(expected)}, "
            f"actual={sorted(actual)}"
        )


def verify_release_directory(
    release_directory: str | Path,
    *,
    packaged: bool = False,
) -> dict[str, object]:
    """Verify the exact app file set, hashes, sizes, and claim boundaries."""

    root = Path(release_directory)
    if not root.is_dir():
        raise ReleaseError(f"release directory does not exist: {root}")
    _verify_release_top_level(root, packaged=packaged)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ReleaseError(f"missing regular release manifest: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ReleaseError("release manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise ReleaseError("release manifest must be a JSON object")
    expected_constants = {
        "schema_version": "1.0",
        "product_id": PRODUCT_ID,
        "test_id": TEST_ID,
        "platform": "windows",
        "architecture": "x64",
        "application_directory": "app",
    }
    for key, expected in expected_constants.items():
        if manifest.get(key) != expected:
            raise ReleaseError(f"release manifest has invalid {key}")
    signing = manifest.get("code_signing")
    authenticity = manifest.get("authenticity")
    readiness = manifest.get("distribution_readiness")
    build_environment = manifest.get("build_environment")
    boundary = manifest.get("acceptance_boundary")
    if manifest.get("name") != PROJECT_NAME:
        raise ReleaseError("release manifest has an invalid project name")
    if not isinstance(manifest.get("version"), str):
        raise ReleaseError("release manifest has an invalid version")
    if signing != {
        "status": "NOT_SIGNED",
        "verified": False,
        "reason": SIGNING_REASON,
    }:
        raise ReleaseError("release must remain explicitly NOT_SIGNED")
    if authenticity != {"status": "NOT_ESTABLISHED", "publisher": None}:
        raise ReleaseError("release authenticity must remain NOT_ESTABLISHED")
    if readiness != {
        "status": "INTERNAL_VALIDATION_ONLY",
        "installer": "NOT_BUILT",
        "reason": READINESS_REASON,
    }:
        raise ReleaseError(
            "release distribution readiness must remain INTERNAL_VALIDATION_ONLY"
        )
    if not isinstance(build_environment, dict) or (
        build_environment.get("archive_determinism")
        != "FIXED_METADATA_FOR_IDENTICAL_FILE_TREE"
        or build_environment.get("binary_reproducibility") != "NOT_ESTABLISHED"
    ):
        raise ReleaseError("release reproducibility claims are invalid")
    provenance = build_environment.get("source_provenance")
    git_head = build_environment.get("git_head")
    git_clean = build_environment.get("git_worktree_clean")
    if provenance == "RECORDED_GIT_WORKTREE":
        if (
            not isinstance(git_head, str)
            or len(git_head) != 40
            or any(character not in "0123456789abcdef" for character in git_head)
            or not isinstance(git_clean, bool)
        ):
            raise ReleaseError("recorded Git source provenance is invalid")
    elif provenance == "NOT_ESTABLISHED":
        if git_head is not None or git_clean is not None:
            raise ReleaseError("unestablished source provenance must not claim Git state")
    else:
        raise ReleaseError("release source provenance status is invalid")
    runtime_validation = validate_packaged_self_test_results(
        manifest.get("packaged_runtime_validation")
    )
    for payload in runtime_validation.values():
        runtime = payload["runtime"]
        if (
            runtime["python"] != build_environment.get("python")
            or runtime["numpy"] != build_environment.get("numpy")
            or runtime["pillow"] != build_environment.get("pillow")
        ):
            raise ReleaseError(
                "packaged runtime versions do not match the build manifest"
            )
    if not isinstance(boundary, dict) or (
        boundary.get("status") != ACCEPTANCE_STATUS
        or boundary.get("comparison_result") != COMPARISON_RESULT
        or boundary.get("comparison_threshold") is not None
        or boundary.get("physical_focus_claim") is not False
    ):
        raise ReleaseError("release acceptance boundary is invalid")

    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise ReleaseError("release manifest must contain file records")
    expected: dict[str, dict[str, object]] = {}
    record_paths: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            raise ReleaseError("release file record must be an object")
        member = record.get("path")
        if not isinstance(member, str):
            raise ReleaseError("release file path must be a string")
        validate_release_member(member)
        if not member.startswith("app/"):
            raise ReleaseError(f"release file must be inside app/: {member}")
        record_paths.append(member)
        expected[member] = record
    validate_member_names(record_paths)
    if record_paths != sorted(record_paths):
        raise ReleaseError("release file records must be sorted")

    actual_paths = [
        PurePosixPath("app", *relative.parts).as_posix()
        for relative in _relative_files(root / "app")
    ]
    if set(actual_paths) != set(record_paths):
        missing = sorted(set(record_paths) - set(actual_paths))
        unexpected = sorted(set(actual_paths) - set(record_paths))
        raise ReleaseError(
            f"release file set mismatch; missing={missing}, unexpected={unexpected}"
        )
    for member, record in expected.items():
        relative = PurePosixPath(member).relative_to("app")
        path = root / "app" / Path(*relative.parts)
        if record.get("size_bytes") != path.stat().st_size:
            raise ReleaseError(f"release file size mismatch: {member}")
        if record.get("sha256") != sha256_file(path):
            raise ReleaseError(f"release file hash mismatch: {member}")
    return manifest


def verify_release_archive(
    archive_path: str | Path,
    release_directory: str | Path,
    *,
    root_name: str | None = None,
    packaged: bool = False,
) -> None:
    archive_path = Path(archive_path)
    release_root = Path(release_directory)
    manifest = verify_release_directory(release_root, packaged=packaged)
    archive_root = root_name or release_root.name
    validate_release_member(archive_root)
    expected_relative = [MANIFEST_NAME, *[row["path"] for row in manifest["files"]]]
    expected_members = [
        PurePosixPath(archive_root, relative).as_posix()
        for relative in expected_relative
    ]
    with zipfile.ZipFile(archive_path) as archive:
        if archive.comment:
            raise ReleaseError("release archive comment must be empty")
        infos = archive.infolist()
        names = [info.filename for info in infos]
        validate_member_names(names)
        if names != sorted(names) or names != sorted(expected_members):
            raise ReleaseError("release archive has an unexpected or unsorted file set")
        for info in infos:
            if info.is_dir() or info.date_time != ZIP_TIMESTAMP:
                raise ReleaseError("release archive metadata is not deterministic")
            if (
                info.compress_type != zipfile.ZIP_DEFLATED
                or info.create_system != 3
                or info.extra
                or (info.external_attr >> 16) != 0o100644
            ):
                raise ReleaseError(
                    "release archive permissions or compression are not canonical"
                )
            relative = PurePosixPath(info.filename).relative_to(archive_root)
            path = release_root / Path(*relative.parts)
            if archive.read(info) != path.read_bytes():
                raise ReleaseError(f"release archive content mismatch: {info.filename}")


def verify_release_bundle(release_directory: str | Path) -> ReleaseArtifacts:
    """Self-test a published directory, archive, checksum, and claims."""

    root = Path(release_directory)
    manifest = verify_release_directory(root, packaged=True)
    expected_name = (
        f"{manifest['name']}-{manifest['version']}-windows-x64"
    )
    if root.name != expected_name:
        raise ReleaseError(
            f"release directory name mismatch: expected {expected_name!r}"
        )
    archive = root / f"{root.name}.zip"
    checksum_file = root / f"{archive.name}.sha256"
    verify_release_archive(archive, root, packaged=True)
    archive_hash = sha256_file(archive)
    expected_checksum = f"{archive_hash}  {archive.name}\n"
    try:
        actual_checksum = checksum_file.read_text(encoding="ascii")
    except (UnicodeError, OSError) as exc:
        raise ReleaseError("release checksum file is not valid ASCII") from exc
    if actual_checksum != expected_checksum:
        raise ReleaseError("release archive checksum does not match")
    return ReleaseArtifacts(
        directory=root,
        archive=archive,
        archive_sha256=archive_hash,
        checksum_file=checksum_file,
    )


def assemble_portable_release(
    app_directory: str | Path,
    output_directory: str | Path,
    metadata: ProjectMetadata,
    *,
    source_repository: str | Path | None = None,
) -> ReleaseArtifacts:
    source = Path(app_directory).resolve()
    _relative_files(source)
    output = Path(output_directory).resolve()
    if output == source or source in output.parents:
        raise ReleaseError("release output must not be inside the application tree")
    release_name = f"{metadata.name}-{metadata.version}-windows-x64"
    release_directory = output / release_name
    archive_name = f"{release_name}.zip"
    checksum_name = f"{archive_name}.sha256"
    if release_directory.exists():
        raise FileExistsError(f"release output already exists: {release_directory}")

    output.mkdir(parents=True, exist_ok=True)
    staged_release = Path(tempfile.mkdtemp(prefix=".u02-", dir=output))
    archive_descriptor, archive_temporary_name = tempfile.mkstemp(
        prefix=".u02-",
        suffix=".zip",
        dir=output,
    )
    os.close(archive_descriptor)
    staged_archive = Path(archive_temporary_name)
    staged_archive.unlink()
    published = False
    try:
        shutil.copytree(source, staged_release / "app", symlinks=True)
        runtime_validation = run_packaged_self_tests(staged_release / "app")
        manifest = create_release_manifest(
            staged_release / "app",
            metadata,
            runtime_validation,
            source_repository,
        )
        _write_json(staged_release / MANIFEST_NAME, manifest)
        verify_release_directory(staged_release)
        _write_deterministic_zip(
            staged_release,
            staged_archive,
            root_name=release_name,
        )
        verify_release_archive(
            staged_archive,
            staged_release,
            root_name=release_name,
        )
        archive_hash = sha256_file(staged_archive)
        (staged_release / checksum_name).write_text(
            f"{archive_hash}  {archive_name}\n",
            encoding="ascii",
            newline="\n",
        )
        staged_archive.replace(staged_release / archive_name)
        _replace_directory_atomic(staged_release, release_directory)
        published = True
        verified = verify_release_bundle(release_directory)
        if verified.archive_sha256 != archive_hash:
            raise ReleaseError("published archive hash changed after atomic publish")
        return verified
    except BaseException:
        if published and release_directory.is_dir():
            shutil.rmtree(release_directory, ignore_errors=True)
        elif published and release_directory.exists():
            release_directory.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staged_release, ignore_errors=True)
        staged_archive.unlink(missing_ok=True)


def pyinstaller_is_available() -> bool:
    return importlib.util.find_spec("PyInstaller") is not None


def run_packaged_self_tests(
    application: str | Path,
    *,
    timeout_seconds: int = 90,
) -> dict[str, dict[str, object]]:
    """Run the frozen numerical and hidden-UI smoke checks."""

    root = Path(application)
    executable = validate_windows_x64_executable(root)
    results: dict[str, dict[str, object]] = {}
    with tempfile.TemporaryDirectory(prefix="u02-frozen-check-") as temporary:
        output_root = Path(temporary)
        for label, option in (("runtime", "--self-test"), ("ui", "--ui-smoke")):
            output = output_root / f"{label}.json"
            try:
                subprocess.run(
                    [str(executable), option, str(output)],
                    cwd=root,
                    check=True,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise ReleaseError(
                    f"packaged {label} self-test timed out"
                ) from exc
            except subprocess.CalledProcessError as exc:
                raise ReleaseError(
                    f"packaged {label} self-test failed with {exc.returncode}"
                ) from exc
            try:
                payload = json.loads(output.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ReleaseError(
                    f"packaged {label} self-test did not write valid JSON"
                ) from exc
            results[label] = payload
    return validate_packaged_self_test_results(results)


def build_application(repo: str | Path) -> Path:
    root = Path(repo).resolve()
    if os.name != "nt":
        raise ReleaseError("the Windows executable must be built on Windows")
    if not pyinstaller_is_available():
        raise ReleaseError(
            "PyInstaller is required; install the project with the "
            "'windows-release' optional dependency"
        )
    metadata = read_project_metadata(root / "pyproject.toml")
    destination = (
        root
        / "dist"
        / "windows"
        / f"{APP_DIRECTORY_NAME}-{metadata.version}"
    )
    if destination.exists():
        raise FileExistsError(f"Windows build already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="u02-pyinstaller-") as temporary:
        build_root = Path(temporary)
        version_file = write_windows_version_file(
            build_root / "version_info.txt",
            metadata,
        )
        environment = os.environ.copy()
        environment["U02_VERSION_FILE"] = str(version_file)
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(build_root / "dist"),
            "--workpath",
            str(build_root / "work"),
            str(
                root
                / "release"
                / "windows"
                / "lensless_holography_workbench.spec"
            ),
        ]
        subprocess.run(command, cwd=root, env=environment, check=True)
        built_application = build_root / "dist" / APP_DIRECTORY_NAME
        _relative_files(built_application)
        for notice_name in ("PORTABLE-README.txt", "THIRD-PARTY-NOTICES.txt"):
            source_notice = root / "release" / "windows" / notice_name
            if not source_notice.is_file():
                raise ReleaseError(f"release notice is missing: {source_notice}")
            shutil.copy2(source_notice, built_application / notice_name)
        run_packaged_self_tests(built_application)

        staging_parent = Path(
            tempfile.mkdtemp(prefix=".u02-build-", dir=destination.parent)
        )
        staged_application = staging_parent / "app"
        try:
            shutil.copytree(
                built_application,
                staged_application,
                symlinks=True,
            )
            _relative_files(staged_application)
            _replace_directory_atomic(staged_application, destination)
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)

    try:
        _relative_files(destination)
        run_packaged_self_tests(destination)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an unsigned, hash-manifested U02 Windows release"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    package = subparsers.add_parser(
        "package", description="Package an existing PyInstaller directory"
    )
    package.add_argument("--app-dir", type=Path, required=True)
    package.add_argument("--output-dir", type=Path, required=True)
    package.add_argument("--pyproject", type=Path)

    build = subparsers.add_parser(
        "build", description="Run PyInstaller and package its output"
    )
    build.add_argument("--repo-root", type=Path, default=repository_root())
    build.add_argument("--output-dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "package":
        pyproject = args.pyproject or repository_root() / "pyproject.toml"
        metadata = read_project_metadata(pyproject)
        artifacts = assemble_portable_release(
            args.app_dir,
            args.output_dir,
            metadata,
        )
    else:
        root = args.repo_root.resolve()
        metadata = read_project_metadata(root / "pyproject.toml")
        application = build_application(root)
        output = args.output_dir or root / "dist" / "releases"
        artifacts = assemble_portable_release(
            application,
            output,
            metadata,
            source_repository=root,
        )
    print(
        json.dumps(
            {
                "directory": str(artifacts.directory),
                "archive": str(artifacts.archive),
                "archive_sha256": artifacts.archive_sha256,
                "checksum_file": str(artifacts.checksum_file),
                "code_signing": "NOT_SIGNED",
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
