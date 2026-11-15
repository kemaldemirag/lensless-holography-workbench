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

The headless core can save a selected plane as a float32 TIFF, reopen the same
pixel array, record a versioned session, verify output hashes, and calculate a
difference map and centre-line profile for aligned images. It deliberately
does not emit PASS or FAIL. The following acceptance inputs are still open:

- the published reference pair and exact geometry;
- the common crop and intensity scaling rule;
- a similarity threshold fixed before comparison;
- a Windows build and user-interface workflow.

## U02-T3 Raspberry Pi acquisition

Status: `SAHA VERİSİ GEREKİR`

The first slice does not acquire Pi RAW data. A device session must bind each
frame to a unique request, exposure, gain, sensor mode, timestamp, and DNG
record before this test can be evaluated.

