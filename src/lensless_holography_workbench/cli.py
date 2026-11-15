"""Command line interface for one file-based U02 processing session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .depth_scan import PropagationParameters, evaluate_depth_scan
from .session import export_session, load_float_tiff


def _load_grayscale(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("L"), dtype=np.float64)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one deterministic U02 depth-scan session")
    parser.add_argument("--signal", type=Path, required=True)
    parser.add_argument("--background", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-plane", type=Path)
    parser.add_argument("--pixel-pitch-um", type=float, default=0.0851)
    parser.add_argument("--wavelength-um", type=float, default=0.66)
    parser.add_argument("--refractive-index", type=float, default=1.33)
    parser.add_argument("--depth-start-um", type=float, default=0.0)
    parser.add_argument("--depth-stop-um", type=float, default=20.0)
    parser.add_argument("--depth-step-um", type=float, default=1.0)
    parser.add_argument("--roi-size-px", type=int, default=256)
    parser.add_argument("--selected-depth-um", type=float, default=0.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    parameters = PropagationParameters(
        pixel_pitch_um=args.pixel_pitch_um,
        wavelength_um=args.wavelength_um,
        refractive_index=args.refractive_index,
        depth_start_um=args.depth_start_um,
        depth_stop_um=args.depth_stop_um,
        depth_step_um=args.depth_step_um,
        roi_size_px=args.roi_size_px,
    )
    signal = _load_grayscale(args.signal)
    backgrounds = [_load_grayscale(path) for path in args.background]
    prepared, scan = evaluate_depth_scan(signal, backgrounds, parameters)
    inputs: dict[str, Path] = {"signal": args.signal}
    inputs.update({f"background_{index}": path for index, path in enumerate(args.background, start=1)})
    reference = None
    if args.reference_plane is not None:
        reference = load_float_tiff(args.reference_plane)
        inputs["reference_plane"] = args.reference_plane
    session = export_session(
        args.output,
        prepared=prepared,
        scan=scan,
        parameters=parameters,
        selected_depth_um=args.selected_depth_um,
        inputs=inputs,
        reference=reference,
    )
    print(json.dumps(session, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

