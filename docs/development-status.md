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
- a PyInstaller 6.22.3 Windows x64 `onedir` build with frozen NumPy FFT,
  JPEG, float TIFF, and hidden-Tk smoke tests;
- hash-locked release dependencies, exact file-set SHA-256 manifests,
  deterministic portable ZIP creation, and atomic release publication;
- unit, negative, and K03 real-data reproducibility tests.

## Not implemented

- project license and publisher identity;
- Authenticode signing and signature verification;
- a validated, signed Windows installer and upgrade/uninstall workflow;
- published UNAL reference-pair alignment;
- customer-approved similarity threshold;
- physical-focus calibration;
- Raspberry Pi RAW acquisition and metadata binding.

Version 0.3.0 can produce a standalone portable application for internal
validation. Its release metadata is fail-closed: `NOT_SIGNED`, authenticity
`NOT_ESTABLISHED`, distribution readiness `INTERNAL_VALIDATION_ONLY`, and
installer `NOT_BUILT`. Hashes establish integrity, not publisher authenticity.
Public distribution remains blocked until the project license, publisher,
certificate, signing tools, and installer validation are established.

