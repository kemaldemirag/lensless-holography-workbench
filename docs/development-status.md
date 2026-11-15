# Development status

## Implemented

- background normalization from one signal and multiple background frames;
- angular-spectrum propagation with evanescent frequencies excluded;
- inclusive depth scans and deterministic gradient-energy and amplitude-
  standard-deviation metrics;
- centred region-of-interest measurement;
- stable metric CSV, float32 TIFF, session JSON, and SHA-256 manifest;
- explicit runtime versions and measurement units in every session;
- SHA-256 binding between the normalized source wavefront and its scan metrics;
- exact TIFF pixel-array verification on reopen;
- aligned-image difference maps and centre-line profiles;
- a Tkinter Windows desktop shell with asynchronous scan progress,
  cancellation, cached-plane navigation, verified session export, and
  inspection-only session reopen;
- unit, negative, and K03 real-data reproducibility tests.

## Not implemented

- signed/standalone Windows packaging and installer;
- published UNAL reference-pair alignment;
- customer-approved similarity threshold;
- physical-focus calibration;
- Raspberry Pi RAW acquisition and metadata binding.

The desktop entry point is intended for an installed Python environment. A
standalone executable, code signing, installer, and installer-level Windows
validation are not part of the current slice.

