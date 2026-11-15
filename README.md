# Lensless Holography Workbench

This repository contains the headless processing core for product U02 in the
15 November 2026 model reports. The first slice reproduces a deterministic
angular-spectrum depth scan. The Windows user interface and physical-focus
acceptance remain outside this slice.

The core records numerical metrics without claiming that the largest focus
metric is the physical focus. U02-T2 remains `BEKLIYOR` until a comparison
threshold is defined before evaluating a published reference pair.

## Development environment

- Python 3.12
- NumPy 2.3.5
- Pillow 13.0.0

Run tests with the workspace Python runtime and `PYTHONPATH=src`:

```text
python -m unittest discover -s tests -t . -v
```

