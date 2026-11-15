"""PyInstaller launcher and packaged-runtime checks for the U02 GUI."""

from __future__ import annotations

import argparse
import json
import os
import platform
import tempfile
import uuid
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageTk, __version__ as pillow_version

from lensless_holography_workbench.image_io import load_grayscale_image
from lensless_holography_workbench.session import (
    U02_T2_STATUS,
    load_float_tiff,
    save_float_tiff,
)
from lensless_holography_workbench.windows_app import (
    COMPARISON_RESULT,
    PARAMETER_FIELDS,
    WorkbenchApp,
    main as windows_main,
    parse_parameters,
)


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def runtime_self_test(*, include_ui: bool) -> dict[str, object]:
    """Exercise packaged numerical, image, TIFF, Tk, and claim boundaries."""

    import tkinter as tk

    source = np.arange(64, dtype=np.float64).reshape(8, 8)
    transformed = np.fft.ifft2(np.fft.fft2(source)).real
    if not np.allclose(transformed, source, rtol=0.0, atol=1e-12):
        raise RuntimeError("NumPy FFT packaged-runtime check failed")

    with tempfile.TemporaryDirectory(prefix="u02-self-test-") as temporary:
        root = Path(temporary)
        jpeg = root / "input.jpg"
        Image.fromarray(np.rint(source * 4).astype(np.uint8)).save(
            jpeg,
            format="JPEG",
        )
        loaded_jpeg = load_grayscale_image(jpeg)
        if loaded_jpeg.shape != source.shape or not np.isfinite(loaded_jpeg).all():
            raise RuntimeError("Pillow JPEG packaged-runtime check failed")

        floating = np.asarray(source / 10.0, dtype=np.float32)
        tiff = save_float_tiff(root / "float.tiff", floating)
        if not np.array_equal(load_float_tiff(tiff), floating):
            raise RuntimeError("float TIFF packaged-runtime check failed")

    oversized = {name: default for name, _label, default in PARAMETER_FIELDS}
    oversized["depth_stop_um"] = "1000"
    try:
        parse_parameters(oversized)
    except ValueError as exc:
        if "maximum is 1000" not in str(exc):
            raise RuntimeError("desktop resource-limit check changed") from exc
    else:
        raise RuntimeError("desktop resource-limit check did not reject input")

    ui_result: dict[str, object] = {"requested": include_ui}
    if include_ui:
        tk_root = tk.Tk()
        try:
            tk_root.withdraw()
            WorkbenchApp(tk_root)
            photo = ImageTk.PhotoImage(
                image=Image.new("L", (2, 2), color=0),
                master=tk_root,
            )
            tk_root.update_idletasks()
            ui_result.update(
                {
                    "created": True,
                    "image_tk_photo_created": photo.width() == 2,
                    "requested_width": tk_root.winfo_reqwidth(),
                    "requested_height": tk_root.winfo_reqheight(),
                }
            )
        finally:
            tk_root.destroy()

    return {
        "schema_version": "1.0",
        "self_test": "OK",
        "product_id": "U02",
        "test_id": "U02-T2",
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pillow": pillow_version,
            "tk": str(tk.TkVersion),
            "image_tk_imported": ImageTk is not None,
        },
        "checks": {
            "numpy_fft": "OK",
            "pillow_jpeg": "OK",
            "float_tiff": "OK",
            "resource_limits": "OK",
            "ui": ui_result,
        },
        "acceptance_boundary": {
            "status": U02_T2_STATUS,
            "comparison_result": COMPARISON_RESULT,
            "comparison_threshold": None,
            "physical_focus_claim": False,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-test", type=Path, metavar="JSON")
    mode.add_argument("--ui-smoke", type=Path, metavar="JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = args.self_test or args.ui_smoke
    if output is None:
        return windows_main()
    try:
        payload = runtime_self_test(include_ui=args.ui_smoke is not None)
    except Exception as exc:
        _write_json_atomic(
            output,
            {
                "schema_version": "1.0",
                "self_test": "ERROR",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return 1
    _write_json_atomic(output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
