# Windows release workflow

Version 0.3.0 adds a Windows x64 portable-build path for internal validation.
It does not add a signed installer or establish publisher authenticity.

## Locked build environment

The release lock targets CPython 3.12 on Windows x64. It fixes NumPy, Pillow,
PyInstaller, PyInstaller hooks, and every direct build dependency to an exact
version and wheel SHA-256.

```text
python -m pip install --require-hashes -r requirements-release.lock
```

Do not replace the lock with an unpinned `pip install PyInstaller` command for
a recorded build. Regenerate and review the complete lock when any dependency
changes.

## Build

From the repository root:

```text
python -m tools.windows_release build --repo-root . --output-dir dist/releases
```

The builder performs these operations:

1. reads the package name and version from `pyproject.toml`;
2. creates Windows version metadata;
3. runs PyInstaller 6.22.3 in a short system temporary directory using the
   checked-in `onedir`, windowed, no-UPX specification;
4. rejects links, junctions, reparse points, non-regular files, unsafe Windows
   names, case-insensitive collisions, and non-normalized archive paths;
5. runs the frozen executable's NumPy FFT, JPEG, float TIFF, and hidden-Tk UI
   self-tests with timeouts and records both validated JSON results in the
   release manifest;
6. copies the validated application through a sibling staging directory;
7. records every application file's POSIX-relative path, byte size, and
   SHA-256 in `RELEASE-MANIFEST.json`;
8. creates a sorted ZIP with fixed timestamps, permissions, separators, and
   compression settings;
9. writes the ZIP SHA-256 sidecar and verifies the exact directory, archive,
   hashes, sizes, and claim boundaries before returning success.

An existing versioned application or release directory is never overwritten.
A failed build removes only its own unpublished staging output.

## Artifact layout

For version 0.3.0, the versioned release directory is:

```text
dist/releases/lensless-holography-workbench-0.3.0-windows-x64/
  app/
    LenslessHolographyWorkbench.exe
    PORTABLE-README.txt
    THIRD-PARTY-NOTICES.txt
    _internal/...
  RELEASE-MANIFEST.json
  lensless-holography-workbench-0.3.0-windows-x64.zip
  lensless-holography-workbench-0.3.0-windows-x64.zip.sha256
```

The deterministic ZIP claim is deliberately narrow: the same input file tree
produces the same archive bytes. PyInstaller binary reproducibility remains
`NOT_ESTABLISHED` until two independent clean builds are compared.

## Verification and claims

`RELEASE-MANIFEST.json` must retain all of these values:

- code signing: `NOT_SIGNED`;
- authenticity: `NOT_ESTABLISHED`;
- distribution readiness: `INTERNAL_VALIDATION_ONLY`;
- installer: `NOT_BUILT`;
- U02-T2 status: `BEKLİYOR`;
- comparison result: `NOT_EVALUATED`;
- physical-focus claim: `false`.

The file manifest and ZIP checksum establish integrity only. They do not prove
who published the files. Any modification to the application tree, manifest,
archive, checksum, or acceptance boundary causes verification to fail.

## Public-release gates

A public installer remains blocked until all of the following are supplied and
validated:

- a selected project license compatible with the distribution plan;
- reviewed third-party license texts and notices;
- a publisher or company identity;
- an Authenticode code-signing certificate;
- SignTool plus an approved RFC 3161 timestamp service;
- signature verification for the executable and installer;
- a reviewed per-user installer with a stable application identifier;
- clean upgrade, uninstall, and installer-level Windows tests.

Secrets, PFX files, private keys, and certificate passwords must never be
stored in this repository, passed on command lines, or written to logs.
