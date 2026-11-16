# Point-source Kirchhoff--Helmholtz backend

`point_source_kirchhoff_helmholtz` is a library-level numerical backend for
reconstructing one real, already prepared DLHM contrast hologram at one
point-source reconstruction plane. It is intentionally separate from the
plane-wave angular-spectrum depth-scan backend.

The implementation performs numerical reconstruction only. It does not load
the external UNAL fixture, infer optical parameters, choose a physical focus,
align or map a published comparison image, apply a similarity threshold, or
produce an acceptance decision.

## Pinned upstream source and attribution

The NumPy implementation follows the numerical path in the Universidad
Nacional de Colombia `unal-optodigital/DLHM` Java source at commit
`5d5273e2ebb08735c531815d3e4b100a4e3f3772`:

- [`KirchhoffHelmholtz.java`](https://github.com/unal-optodigital/DLHM/blob/5d5273e2ebb08735c531815d3e4b100a4e3f3772/src/unal/od/dlhm/diffraction/KirchhoffHelmholtz.java),
  Git blob `842c2390721018504fe74968a5bf8a378e7536d6`;
- [`ReconstructionWorker.java`](https://github.com/unal-optodigital/DLHM/blob/5d5273e2ebb08735c531815d3e4b100a4e3f3772/src/unal/od/dlhm/rec/ReconstructionWorker.java),
  Git blob `20c0f66ce42db53db4a11aec110d9841dff35682`.

The upstream Java files state copyright 2017 Universidad Nacional de
Colombia and carry the Apache License, Version 2.0 header. The source-derived
backend retains that copyright and license notice; the Apache-2.0 license text
is available at <https://www.apache.org/licenses/LICENSE-2.0>.

This attribution does not select a project-wide license for this repository
and does not grant rights to the upstream example images. The repository
still has no selected project license, and the image permission and
redistribution boundary in the
[UNAL source profile](unal-k18-source-profile.md) remains unchanged. The
preferred scientific citation is C. Trujillo, P. Piedrahita-Quintero, and
J. Garcia-Sucerquia, "Digital lensless holographic microscopy: numerical
simulation and reconstruction with ImageJ," *Applied Optics* 59, 5788--5795
(2020), <https://doi.org/10.1364/AO.395672>.

## Public contract

The backend is exposed by
`lensless_holography_workbench.point_source`. Its primary records and
operations are:

- `PointSourceParameters`, the physical geometry for one plane;
- `subtract_reference(hologram, reference)`, the exact real-valued
  `hologram - reference` preprocessing operation;
- `prepare_point_source(shape, parameters)`, which validates and precomputes
  interpolation geometry and convolution kernels;
- `PreparedPointSource.reconstruct(contrast)`, which reuses those kernels;
- `reconstruct_point_source(contrast, parameters)`, the one-call path;
- `parameters_from_fixture_optics(...)`, which translates an already verified
  fixture optics record and checks its redundant sample pitches.

`PreparedPointSource` and `PointSourceReconstruction` are factory-owned output
records; callers create them through the operations above, not by passing
unverified kernels or result arrays to their dataclass constructors.

The returned `PointSourceReconstruction` contains the immutable complex field,
the interpolated contrast field, geometry, and a deterministic source
fingerprint. Amplitude is `abs(field)`, intensity is `abs(field) ** 2`, and
phase is `angle(field)`. No output normalization or display-range mapping is
performed by the backend.

## Geometry, axes, and units

Every physical length is in micrometres. NumPy arrays use `(height, width)` or
`(N, M)` order. In the equations below, `M` is the number of detector columns
and `N` is the number of detector rows:

- `lambda` is `wavelength_um`;
- `z` is `reconstruction_distance_um`, measured from the point source to the
  requested reconstruction plane;
- `L` is `source_to_screen_distance_um`, measured from the point source to the
  detector;
- `W` and `H` are the detector width and height;
- `W'` and `H'` are the requested output-plane width and height;
- `dx = W / M`, `dy = H / N`, `dx' = W' / M`, and `dy' = H' / N`.

The backend requires `0 < z < L`. Therefore `L - z` is the distance from the
reconstruction plane to the detector. The upstream UI's automatic output-size
choice is `W' = W z/L` and `H' = H z/L`, giving `dx' = dx z/L` and
`dy' = dy z/L`; the numerical API also permits explicit positive output sizes.

The port deliberately preserves the upstream even-grid convention rather
than replacing it with a symmetric coordinate vector:

```text
x0 = -dx M/2                    xf = dx (M/2 - 1)
y0 = -dy N/2                    yf = dy (N/2 - 1)
X0 = x0 L / sqrt(L^2 + x0^2)   Xf = xf L / sqrt(L^2 + xf^2)
Y0 = y0 L / sqrt(L^2 + y0^2)   Yf = yf L / sqrt(L^2 + yf^2)
dX = (Xf - X0) / M             dY = (Yf - Y0) / N
m = -M/2 + 1, ..., M/2         n = -N/2 + 1, ..., N/2
```

In particular, `dX` and `dY` divide by `M` and `N`, not by `M - 1` and
`N - 1`. This asymmetric-looking convention is part of the pinned numerical
contract.

## Preprocessing and spherical interpolation

The reconstruction input is a finite two-dimensional real contrast field.
For the upstream intensity-with-reference path, that contrast is exactly
`hologram - reference`; it is not a ratio. `subtract_reference` requires equal
shapes and does not clip, normalize, rescale, or infer a background.

Before diffraction, the detector samples are remapped to the transformed
spherical grid. For a transformed point `(X, Y)`, the inverse detector map is:

```text
q  = 1 / sqrt(L^2 - X^2 - Y^2)
xs = X L q
ys = Y L q
column = (xs - x0) / dx
row    = (ys - y0) / dy
```

The backend uses the pinned bilinear interpolation weights and four-quadrant
mirroring. The upstream strict interior bounds are preserved, so samples on
some interpolation boundaries remain zero. A constant input therefore is not
required to produce a spatially constant interpolated field.

This backend implements the real contrast/intensity path. It does not
implement the separate upstream phase-with-reference workflow that propagates
two fields and divides their complex results.

## Kirchhoff--Helmholtz kernel and FFT convention

With `k = 2 pi/lambda`, define, for each pinned `(m, n)` sample:

```text
R^2  = L^2 - (m dX)^2 - (n dY)^2
rp^2 = z^2 + (m dx')^2 + (n dy')^2
phi1 = -(k/L^2) sqrt(R^2) (z L - rp^2/2)
phi2 = -(k/(2 L)) (m^2 dX dx' + n^2 dY dy')
c    = -(1/(2 lambda R^2)) (1 + sqrt(R^2)/L)

K1 = -c sin(phi1 + phi2) + i c cos(phi1 + phi2)
K2 = exp(-i phi2)
P  = dX dY exp(+i phi2)
```

The operation sequence is fixed:

1. multiply the interpolated complex contrast by `K1`;
2. centre it in a zero-filled `(2N, 2M)` complex array, starting at
   `(N/2, M/2)`;
3. centre `K2` in an identically sized array and transform both arrays with
   NumPy `fft2`;
4. multiply the spectra and apply NumPy `ifft2`;
5. apply NumPy `fftshift`, centre-crop back to `(N, M)`, and multiply by `P`.

NumPy's unscaled forward transform and normalized inverse transform match the
sign and inverse-scaling convention used by the pinned JTransforms call. The
two-times zero padding is part of the algorithm and prevents the intended
linear convolution from being replaced by an unpadded circular convolution.
The public x coordinate always maps to array axis 1 and y to axis 0.

## Validation boundary

The backend fails closed before or during numerical work when:

- the input is not finite, real, and two-dimensional;
- the input shape differs from the prepared shape;
- either dimension is odd or smaller than four pixels;
- the source image exceeds 1,048,576 pixels, the deterministic cap derived
  from a 768 MiB peak-work budget at a conservative 768 bytes per source
  pixel;
- any length is non-numeric, non-finite, or non-positive;
- derived geometry or the wave number exceeds the safe `float64` range;
- `z >= L`;
- a kernel or interpolation square-root radicand is not strictly positive;
- a kernel or reconstruction result contains a non-finite component;
- a fixture optics record names another backend or its declared sample
  pitches disagree with the physical sizes and array shape.

Inputs are copied to deterministic `float64` or `complex128` arrays. Returned
numerical arrays use immutable bytes-backed storage, so callers cannot
re-enable NumPy's write flag. The source fingerprint binds the
pinned upstream commit, canonical parameters, shape, and little-endian
`float64` contrast bytes with SHA-256. It is a reproducibility and integrity
identifier, not evidence of source-image permission, scientific correctness,
or acceptance.

## Numerical-parity limits

The implementation preserves the pinned equations, phase signs, even-grid
indexing, quadrant mirroring, two-times padding, shift, and crop. It does not
claim bit-for-bit equality with the Java plugin:

- the Java implementation primarily uses single-precision arrays, while this
  backend uses `float64` and `complex128`;
- NumPy FFT and JTransforms may differ in operation order and rounding;
- odd image dimensions are rejected so that shift and pinned half-grid
  conventions are unambiguous;
- interpolation boundary zeros and the upstream asymmetric sample convention
  are intentional, not corrected approximations;
- the backend covers one already prepared real contrast plane, not every UI
  preprocessing mode or the phase-recovery path.

Synthetic invariants and direct numerical checks can establish deterministic
implementation behavior. They cannot establish agreement with a real
microscope, identify physical focus, or substitute for the authorized UNAL
comparison source.

## Fixture and acceptance status

No UNAL example image, permission record, or ready-to-run real fixture is
included. The published intensity JPG is a visual source, not a bitwise golden
array. The external fixture loader and this numerical backend are separate:
an `AVAILABLE` fixture result would establish only the fixture contract and
still would not execute or approve a comparison.

Exact parameter transcription and independent checking, shared alignment and
crop, fixed intensity mapping, and a customer-approved similarity threshold
remain open. Consequently U02-T2 remains `BEKLİYOR`, every comparison remains
`NOT_EVALUATED`, and this backend makes no physical-focus, reconstruction-
accuracy, customer-acceptance, or public-release claim.
