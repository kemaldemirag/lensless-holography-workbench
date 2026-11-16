# Numerical autofocus navigation

## Scope and claim boundary

The autofocus module ranks reconstructed amplitude planes on a deterministic
discrete depth grid. It is a library-level navigation aid. Every
`AutofocusResult` is marked `NUMERICAL_NAVIGATION_ONLY`; the API has no PASS,
FAIL, physical-focus, reconstruction-accuracy, or customer-acceptance
semantics. U02-T2 remains `BEKLİYOR`, and comparison output remains
`NOT_EVALUATED`.

The module does not currently change CLI or desktop depth selection and is not
stored in session-schema 1.0 or `metrics.csv`. Callers must opt in explicitly.

## Public API

- `BrennerFocusMetric`: version `1.0`, MAXIMIZE. The score is the arithmetic
  mean of the horizontal and vertical mean squared two-pixel differences, so
  rectangular regions give both axes equal weight.
- `SobelVarianceFocusMetric`: version `1.0`, MAXIMIZE. The score is the
  population variance of valid 3 × 3 Sobel gradient magnitudes; no padding is
  introduced.
- `evaluate_focus_metric`: validates a real finite 2D amplitude plane,
  versioned metric metadata, a finite score, and an optional half-open ROI.
- `CoarseToFinePlan`: an inclusive search range defined by `start`, `stop`,
  `coarse_step`, `fine_step`, and `fine_radius`, all in micrometres.
- `coarse_to_fine_autofocus`: evaluates the coarse grid, centres a clipped
  fine window on the coarse optimum, then returns the fine optimum.
- `AutofocusResult.as_dict()`: returns a detached autofocus-result schema 1.0
  record, distinct from session-schema 1.0, containing the plan, source
  fingerprint, metric identity/direction, ROI coordinate space, ordered
  samples, units, and best numerical candidate.

Example:

```python
from lensless_holography_workbench import (
    BrennerFocusMetric,
    CoarseToFinePlan,
    coarse_to_fine_autofocus,
)

plan = CoarseToFinePlan(
    start=0.0,
    stop=20.0,
    coarse_step=2.0,
    fine_step=0.25,
    fine_radius=2.0,
)
result = coarse_to_fine_autofocus(
    prepared_wavefront,
    plan,
    BrennerFocusMetric(),
    roi_bounds=(64, 64, 192, 192),
)
record = result.as_dict()

assert record["result_type"] == "NUMERICAL_NAVIGATION_ONLY"
assert record["source_fingerprint_sha256"] == prepared_wavefront.source_fingerprint
candidate_depth_um = record["best"]["depth_um"]
```

`roi_bounds` uses `(top, left, bottom, right)` with an excluded bottom and
right edge. It is local to the already cropped amplitude returned by
`PreparedWavefront.amplitude_at`, not to the original input image. The record
therefore names it `metric_roi_bounds` and fixes
`roi_coordinate_space` to `prepared_amplitude_half_open`. The ROI must fit
every reconstructed amplitude plane and be large enough for the chosen
metric.

Results produced through `coarse_to_fine_autofocus` always carry the plan and
the selected `PreparedWavefront.source_fingerprint`. Legacy-compatible direct
`AutofocusResult` construction remains possible; `as_dict()` fails closed
unless the caller supplies both a validated plan and a canonical source
fingerprint. The record does not read or hash source files by itself.

## Determinism and validation

The fine grid is anchored at `start` and addressed with integer indices. The
range must contain an integer number of coarse steps; a coarse step and fine
radius must each contain an integer number of fine steps. `fine_step` must be
smaller than `coarse_step`.

The plan fails closed when values are non-finite, steps are non-positive or
non-divisible, the range is reversed, the possible run exceeds 10,000 unique
planes, or binary64 cannot represent every sampled depth distinctly. Coarse
and selected fine grids are rechecked before propagation. A coarse/fine
overlap is served from the score cache, so each unique depth is propagated at
most once. Plan-bound direct result construction also rejects different
scores for the same overlapping depth.

Metrics declare whether their optimum is MAXIMIZE or MINIMIZE. Exact score
ties always select the lower depth. Results and samples are immutable, scores
must be finite, and direct construction of an inconsistent best sample fails.
The source fingerprint is validated before the first propagation.

## Reference provenance

The architectural reference was PyHoloscope, especially its documented focus
metrics, ROI-based assessment, propagator reuse, and optional coarse search:

- official repository: <https://github.com/MikeHughesKent/PyHoloscope>;
- official autofocus documentation:
  <https://pyholoscope.readthedocs.io/en/latest/autofocus.html>;
- reviewed release: <https://pypi.org/project/PyHoloscope/1.1.3/>, publishing
  commit
  `61ce4f44a4f5cc2896a8e6d280a0a8fc534d5e9a`, accessed 2026-11-16.

This repository does not depend on, vendor, or copy PyHoloscope code. The
implementation is an independent NumPy design with different, explicitly
versioned score definitions and a deterministic discrete coarse-to-fine
search instead of claiming algorithmic or numerical parity with PyHoloscope.
PyHoloscope is MIT-licensed; its license does not become this repository's
project license, which is still undecided.

## Next integration gate

Persisting an autofocus suggestion inside a session requires an opt-in
`autofocus.json` output that is covered by the existing SHA-256 manifest and
semantically verified on reopen. It must be calculated from the same
`PreparedWavefront`, and it must not replace `selected_depth_um` implicitly.
Until that sidecar contract is implemented, session-schema 1.0, its uniform
`metrics.csv`, CLI selection, and desktop selection remain unchanged.
