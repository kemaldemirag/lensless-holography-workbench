# Lensless Holography Workbench

This repository contains the headless processing core for product U02 in the
15 November 2026 model reports. The first slice reproduces a deterministic
angular-spectrum depth scan. The Windows user interface and physical-focus
acceptance remain outside this slice.

The core records numerical metrics without claiming that the largest focus
metric is the physical focus. U02-T2 remains `BEKLİYOR` until a comparison
threshold is defined before evaluating a published reference pair.

## Development environment

- Python 3.12
- NumPy 2.3.5
- Pillow 13.0.0

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

See [acceptance status](docs/acceptance.md) for the boundary between numerical
reproduction and physical or customer acceptance.

