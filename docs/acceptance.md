# U02 acceptance status

## U02-T1 numerical depth scan

Status: `TEKRARLANDI`

The K03 input set contains one 512 × 512 hologram and three background JPEG
frames. The numerical repeat uses the centre 256 × 256 pixels and 21 planes
from 0 through 20 µm. Both gradient energy and amplitude standard deviation
reach their maximum at 0 µm. These facts reproduce the recorded computation;
they do not identify a physically correct focus position.

The reproducibility test compares every metric row against the K03 golden CSV
with an absolute tolerance of 1e-12. It runs only when the dated workspace
contains the hash-verified K03 source package.

## U02-T2 Windows comparison and session

Status: `BEKLİYOR`

The core and Windows desktop shell can save a selected plane as a float32
TIFF, reopen the same pixel array, record a versioned session, verify output
hashes, and calculate a difference map and centre-line profile for aligned
images. They deliberately do not emit PASS or FAIL. The following acceptance
inputs are still open:

- evidence-backed permission and a complete hash-verified local copy of the
  published four-role fixture;
- independently verified reconstruction parameters and exact geometry;
- the common crop and intensity scaling rule;
- a similarity threshold fixed before comparison;
- a signed, standalone Windows build and installer validation.

`load_unal_k18_fixture(manifest_path, expected_manifest_sha256=...)`
distinguishes fixture unavailability from invalid evidence. A missing
manifest, permission that is not granted, or a wholly absent payload set
yields `UNAVAILABLE` and may produce a
reason-bearing integration-test skip. Malformed metadata, a partial payload
set, unsafe paths, source-identity conflicts, size or SHA-256 mismatches, and
file mutation are hard failures and must not be converted to skips. The loader
does not download or substitute data.

Even a fully available fixture does not change this acceptance status. The
fixture proves only that the local bytes and recorded provenance agree. Until
the point-source backend, verified parameters, shared crop, intensity mapping,
alignment procedure, and pre-registered threshold are all in place, U02-T2
remains `BEKLİYOR` and its comparison result remains `NOT_EVALUATED`.

## U02-T3 Raspberry Pi acquisition

Status: `SAHA VERİSİ GEREKİR`

The first slice does not acquire Pi RAW data. A device session must bind each
frame to a unique request, exposure, gain, sensor mode, timestamp, and DNG
record before this test can be evaluated.

