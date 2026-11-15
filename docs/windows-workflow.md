# Windows desktop workflow

The Tkinter desktop shell exposes the deterministic U02 scan without changing
its acceptance boundary. Run it from an environment in which this project is
installed:

```text
holography-workbench-gui
```

## New scan

1. Select one signal image.
2. Select one or more background images. Signal, backgrounds, and reference
   must all be different files.
3. Optionally select an already aligned 32-bit floating-point TIFF reference.
   Its dimensions must match the reconstructed measurement region and all
   values must be finite. Integer TIFF references are rejected rather than
   silently rescaled.
4. Enter pixel pitch, vacuum wavelength, refractive index, inclusive depth
   start/stop/step, and the centred square ROI size.
5. Start the scan. Computation runs off the Tk event loop; progress and
   cancellation are reported back to the interface.
6. Select any completed depth plane to inspect it under the single display
   range calculated for the full scan. This common range makes plane-to-plane
   visual changes meaningful.
7. Export to a new or empty session directory. The workflow rechecks source
   file hashes before publishing the session and writes the session
   atomically.

Changing an input or parameter invalidates the previous scan. If a source file
changes during or after scanning, its result cannot be exported under the new
file identity.

Signal and background images are converted to a floating-point grayscale
plane. Integer and floating-point TIFF dynamic range is preserved; colour
inputs use Pillow's grayscale conversion. The desktop shell limits one scan
to 1,000 planes and the cached reconstructed planes to 512 MiB. Requests over
either limit must be divided into smaller depth ranges or ROIs.

## Open an existing session

Opening a session is inspection-only. Before any plane is displayed, the
loader verifies the exact artifact set, SHA-256 manifest, metadata contract,
metrics grid, selected depth, float TIFF shape and dtype, and optional
comparison artifacts. A reopened session cannot be re-exported as if it were a
new computation.

## Interpretation boundary

The metric maximum is recorded so the operator can navigate the scan, but
**sayısal tepe fiziksel odak değildir**. U02-T2 remains `BEKLİYOR`; optional
reference output remains `NOT_EVALUATED`. The shell has no customer-approved
threshold and therefore cannot display or export PASS or FAIL.

## Packaging boundary

The current entry point runs from an installed Python 3.12 environment and
uses the Tk runtime supplied with that environment. A standalone executable,
code signing, installer, and installer-level Windows test are not yet
provided.
