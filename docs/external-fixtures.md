# External fixture contract

This document defines the version 1.0 local-only fixture contract for the
K07/K18 UNAL single-microorganism source set. The repository contains the
contract and loader only. It contains no UNAL payload, ready-to-run fixture
manifest, permission grant, or permission evidence.

## Public API

`load_unal_k18_fixture(manifest_path, expected_manifest_sha256=...)` returns a
`FixtureResolution`, which is either a `LoadedFixture` or an
`UnavailableFixture`. Both expose `status`.
An unavailable result also exposes `reason` and `detail`; a loaded result
exposes the immutable, role-keyed `assets` mapping. Each loaded asset snapshot
records `content`, its manifest-relative `path`, `sha256`, and `size_bytes` from the same
bounded read.

The loader resolves only the path supplied by the caller. It does not search
other directories, issue network requests, download an artifact, or
substitute K03 or synthetic data. The caller remains responsible for ensuring
that the configured local path is not backed by a network mount or a
cloud-hydration provider.

The normal unavailable outcomes are:

| Reason | Condition |
|---|---|
| `NOT_CONFIGURED` | No manifest path was configured. |
| `MANIFEST_NOT_FOUND` | The configured manifest path does not exist. |
| `PERMISSION_NOT_GRANTED` | A valid manifest explicitly records that usage permission is not granted. |
| `PAYLOAD_NOT_PROVISIONED` | Permission and metadata are valid, but all four payload files are absent. |

These outcomes may cause a reason-bearing integration-test skip. They are not
successful fixture loads and must not be reported as a passed comparison.

`UnalFixtureError` is a hard failure. Stable codes include
`INVALID_MANIFEST`, `UNSAFE_PATH`, `UNSAFE_FILE_TYPE`, `READ_ERROR`,
`SIZE_LIMIT_EXCEEDED`, `SIZE_MISMATCH`, `HASH_MISMATCH`, `FILE_CHANGED`,
`SOURCE_IDENTITY_MISMATCH`, `PARTIAL_PAYLOAD`, `INVALID_EXPECTATION`, and
`MANIFEST_HASH_MISMATCH`. Callers and CI must not catch these errors and
convert them into skips. In particular, a partial payload set is invalid: only
the complete absence of all four payloads is an unavailable condition.

## Manifest envelope

The manifest is UTF-8 JSON. Version 1.0 forbids unknown fields and has exactly
these top-level members:

| Member | Required value or shape |
|---|---|
| `schema_version` | Constant `"1.0"`. |
| `fixture_id` | Constant `"UNAL-K18-SINGLE-MICROORGANISM"`. |
| `product_id` | Constant `"U02"`. |
| `test_id` | Constant `"U02-T2"`. |
| `permission` | One of the two permission records below. |
| `fixture` | `null` when permission is not granted; otherwise the complete fixture record. |
| `acceptance` | The fail-closed acceptance record below. |

This repository deliberately provides no filled manifest. A real record must
be created only after the operator has the payload bytes and evidence-backed
permission applicable to those exact artifacts.

[`schemas/unal-k18-fixture.schema.json`](../schemas/unal-k18-fixture.schema.json)
is the machine-readable structural contract. The loader additionally enforces
cross-field, path-containment, file-identity, finite-number, and immutable-read
rules that JSON Schema alone cannot establish.

## Permission gate

Permission is a gate evaluated independently from public availability and
source-code licensing.

When permission has not been established, the only valid record is:

```text
permission = {status: "NOT_GRANTED", reason: <non-empty text>}
fixture = null
```

This resolves to `UNAVAILABLE` without opening payload files.

A positive permission record has exactly these members:

| Member | Rule |
|---|---|
| `status` | Constant `"GRANTED"`. |
| `usage` | Constant `"LOCAL_U02_T2_VALIDATION_ONLY"`. |
| `redistribution` | Constant `"PROHIBITED"`. |
| `granted_by` | Non-empty identity of the grant authority. |
| `granted_on` | ISO `YYYY-MM-DD` date. |
| `evidence_reference` | Non-empty reference to the permission evidence. |

The Java source headers, repository visibility, project-page visibility, or
possession of a file must not be used to synthesize this grant. Permission to
run local U02-T2 validation does not authorize committing, packaging, or
redistributing the images or the evidence record.

The loader treats this record as a declaration by the trusted local operator.
It does not fetch the evidence reference, authenticate the grant authority,
verify a signature, or determine legal sufficiency. Environments that do not
trust the manifest author must pass a SHA-256 from an independent approval
record as `expected_manifest_sha256`, or verify a signature before calling the
loader. A mismatch is a hard failure before payload access. `AVAILABLE` is a
technical contract result, not an independent legal-permission attestation.

## Pinned source identity

The `fixture.source` object is fixed to the source profile already recorded in
this repository:

| Member | Required value |
|---|---|
| `repository` | `"unal-optodigital/DLHM"` |
| `code_commit_sha1` | `"5d5273e2ebb08735c531815d3e4b100a4e3f3772"` |
| `documentation_commit_sha1` | `"536e70f1e179bf285492343b989a478d093c32e3"` |
| `project_page_url` | `"https://unal-optodigital.github.io/DLHM/"` |

The fixed values prevent a branch tip, a similarly named repository, or an
ambiguous version label from being treated as the reviewed source.

## Four fixed asset roles

`fixture.assets` contains exactly one record for each role below. There may be
no missing, additional, duplicate, or path-colliding role.

| Role | Pinned source name | Git blob SHA-1 | Meaning |
|---|---|---|---|
| `hologram` | `c0025.jpg` | `f4218e67ca133e28ab2c2e66e46d9399ad153031` | Reconstruction input. |
| `reference` | `c0039.jpg` | `98510a0d67853ef87e68a8115eee67be754e69f6` | Published reference input, not output truth. |
| `parameter_screenshot` | `OneMicrorganism_ReconstructionParameters.jpg` | `dd537fde038a78e924b3f1dfb855021d977c89fc` | Provenance for parameter transcription. |
| `published_intensity_visual` | `OneMicrorganism_Intensity.jpg` | `8d5519fc90c45c583ec5774952525b1552928cef` | Visual comparison source, not a bitwise golden output. |

Each asset record has exactly these members:

- `role`, one of the four constants above;
- `path`, a local path relative to the manifest directory;
- `source_name`, fixed by the table above;
- `source_url`, the artifact's recorded HTTPS source URL;
- `git_blob_sha1`, fixed by the table above;
- `sha256`, the lowercase 64-hex digest of the local bytes;
- `size_bytes`, the exact positive local byte count;
- `accessed_on`, an ISO `YYYY-MM-DD` date.

A Git blob SHA-1 identifies the reviewed upstream object but does not replace
the SHA-256 of the local bytes. The loader recomputes both identities from the
same immutable snapshot; later decoding must consume that snapshot too.

Asset paths must remain below the manifest directory. Absolute paths, parent
traversal, symlinks, reparse points, non-regular files, duplicate targets, and
targets that resolve outside that directory are rejected. The loader applies
the repository's bounded-input rules before retaining bytes. A file that
changes during the read is rejected.

## Parameter and processing preregistration

An authorized payload is not runnable until the processing record is complete.
The following objects fix the information before an output is inspected.

`fixture.parameter_verification` contains:

- `source_role`, constant `"parameter_screenshot"`;
- non-empty `transcribed_by` and `independently_checked_by` identities, which
  must be different;
- `checked_on`, an ISO `YYYY-MM-DD` date.

`fixture.optics` contains:

- `backend`, constant `"point_source_kirchhoff_helmholtz"`;
- positive finite `wavelength_um`, `reconstruction_distance_um`,
  `source_to_screen_distance_um`, `detector_width_um`, `detector_height_um`,
  `output_width_um`, `output_height_um`, `input_sample_pitch_um`, and
  `output_sample_pitch_um` values.

The plane-wave `configs/u02-t1.json` parameters and angular-spectrum output
are not valid substitutes for this point-source contract.

`fixture.shared_crop_px` contains:

- `coordinate_system`, constant `"TOP_LEFT_ZERO_BASED_HALF_OPEN"`;
- integer `top` and `left` values greater than or equal to zero;
- integer `height` and `width` values greater than or equal to one.

`fixture.intensity_mapping` contains:

- `method`, constant `"FIXED_RANGE_LINEAR"`;
- `clip`, constant `true`;
- finite `candidate_input_min`, `candidate_input_max`,
  `reference_input_min`, `reference_input_max`, `output_min`, and `output_max`
  values, with each associated minimum strictly below its maximum.

The crop and ranges are provenance, not values to tune after viewing the
comparison result.

## Acceptance boundary

The manifest acceptance object is deliberately fixed:

```text
acceptance = {
  u02_t2_status: "BEKLİYOR",
  acceptance_result: "NOT_EVALUATED",
  acceptance_threshold: null
}
```

Changing any of these values invalidates the manifest. `LoadedFixture` means
only that permission metadata, source identity, processing preregistration,
paths, sizes, hashes, and immutable local bytes satisfy this contract. It does
not run the point-source backend, prove alignment or reconstruction accuracy,
identify physical focus, approve a threshold, or establish customer
acceptance.

## CI and local storage

Local manifests, permission evidence, and payloads belong under the ignored
`external-fixtures/` directory. They must stay outside Git history and Windows
release packages.

An integration test may skip only after receiving an `UnavailableFixture` and
must include its `reason` and `detail` in the skip message. It must allow
`UnalFixtureError` to fail the run. When `UNAL_K18_FIXTURE_MANIFEST` is set,
the checked-in reproducibility test also requires the independently managed
`UNAL_K18_FIXTURE_MANIFEST_SHA256` value. Unit tests for this contract use
synthetic temporary bytes and synthetic permission identities; they are not
evidence that the UNAL fixture is authorized or available.
