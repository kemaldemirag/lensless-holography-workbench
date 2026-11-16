# Lensless Holography Workbench

This repository contains the deterministic processing core, Windows desktop
shell, and internal portable-build workflow for product U02 in the 15 November
2026 model reports. Version 0.3.0 reproduces an angular-spectrum depth scan,
keeps every reconstructed plane available for inspection, and exports a
hash-verified file-based session. Physical-focus acceptance remains outside
this slice.

The core records numerical metrics without claiming that the largest focus
metric is the physical focus. U02-T2 remains `BEKLİYOR` until a comparison
threshold is defined before evaluating a published reference pair.

## Development environment

- Python 3.12
- NumPy 2.3.5
- Pillow 13.0.0
- PyInstaller 6.22.3 for the optional Windows release workflow

Run tests with the workspace Python runtime and `PYTHONPATH=src`:

```text
python -m unittest discover -s tests -t . -v
```

## File-based session

The CLI accepts one hologram and one or more background frames. Defaults are
the U02-T1 numerical-repeat parameters: 0.0851 µm pixel pitch, 0.66 µm vacuum
wavelength, refractive index 1.33, a 0–20 µm inclusive scan in 1 µm steps, and
a centred 256 × 256 pixel measurement region.

```text
holography-workbench \
  --signal image01.jpg \
  --background bg01.jpg \
  --background bg02.jpg \
  --background bg03.jpg \
  --output artifacts/u02-session
```

The session contains stable CSV/JSON, a float32 TIFF that reopens to the same
pixel array, and SHA-256 hashes. An optional pre-aligned float TIFF reference
can produce a difference map and centre-line profile. The comparison still
reports `NOT_EVALUATED`: no similarity threshold is embedded in the code.

Both CLI and desktop workflows read each canonical `signal`, `background_N`,
and optional `reference_plane` source into one immutable byte snapshot before
processing. SHA-256 and image decoding consume those same bytes, so the hash
cannot describe a different read from the pixels being processed. Sources and
roles are rechecked immediately before session publication; if any source
changes, no session is published. Inputs are limited to 128 MiB per file and
256 MiB in total.

See [acceptance status](docs/acceptance.md) for the boundary between numerical
reproduction and physical or customer acceptance.

## Windows desktop workflow

After installing the project in the active Python environment, start the
desktop shell with:

```text
holography-workbench-gui
```

Select one signal image, at least one background image, and optionally an
already aligned 32-bit floating-point TIFF reference. Enter the optical and
depth-scan parameters, run the scan, inspect any cached depth plane, and export
to a new session directory. An existing session can be opened only after its
manifest, hashes, metadata, array shapes, and comparison semantics pass
verification.

The displayed numerical peak is a navigation aid: **sayısal tepe fiziksel
odak değildir**. The desktop shell preserves U02-T2 as `BEKLİYOR` and any
reference comparison as `NOT_EVALUATED`; it cannot produce PASS or FAIL.

See [Windows workflow](docs/windows-workflow.md) for the operational sequence,
input constraints, and current packaging boundary.

## Windows portable validation build

The 0.3.0 release workflow builds a PyInstaller `onedir` application, runs
frozen-runtime numerical and hidden-UI smoke tests, then publishes an atomic
portable directory and deterministic ZIP with exact file hashes and sizes.
Install the hash-locked Windows x64 / CPython 3.12 build environment and run:

```text
python -m pip install --require-hashes -r requirements-release.lock
python -m tools.windows_release build --repo-root . --output-dir dist/releases
```

Recheck a copied release directory offline without rebuilding it:

```text
python -m tools.windows_release verify --release-dir <release-dir>
```

A successful verification returns ASCII-safe JSON with
`schema_version: "1.0"`, `command: "verify"`, `verification: "OK"`, the
`directory`, `archive`, `archive_sha256`, `checksum_file`, `name`, and
`version`. It also returns `source_provenance` (`status`, `git_head`, and
`git_worktree_clean`) plus fail-closed `claims`, including
`code_signing: "NOT_SIGNED"`. An expected verification failure writes one
ASCII-safe JSON object to stderr with `verification: "FAILED"` and an `error`
object containing `type` and `message`, exits with code 2, and emits no
traceback. This confirms the recorded file set, hashes, ZIP, checksum, and
claim boundaries. It does not establish a publisher identity or prove an
Authenticode signature.

Every generated manifest deliberately reports `NOT_SIGNED`, authenticity
`NOT_ESTABLISHED`, distribution readiness `INTERNAL_VALIDATION_ONLY`, and
installer `NOT_BUILT`. The repository has no selected project license,
publisher identity, Authenticode certificate, SignTool, or validated installer
toolchain, so this artifact is not a signed public release.

See [Windows release workflow](docs/windows-release.md) for build inputs,
verification rules, artifact structure, and the remaining release gates.

