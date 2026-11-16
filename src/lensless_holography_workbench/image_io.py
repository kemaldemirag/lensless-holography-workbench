"""Shared image loading rules for every U02 front end."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import numpy as np
from PIL import Image


def _load_grayscale_image(source: str | Path | BinaryIO) -> np.ndarray:
    """Decode one image as float64 under the canonical grayscale policy.

    Integer and floating-point single-channel modes retain their numerical
    dynamic range. Colour, palette, and binary modes use Pillow's 8-bit
    ``L`` conversion so the CLI and desktop shell produce identical pixels.
    """

    with Image.open(source) as image:
        if image.mode in {"F", "I"} or image.mode.startswith("I;"):
            return np.asarray(image, dtype=np.float64)
        return np.asarray(image.convert("L"), dtype=np.float64)


def load_grayscale_image(path: str | Path) -> np.ndarray:
    return _load_grayscale_image(path)


def load_grayscale_bytes(content: bytes) -> np.ndarray:
    """Decode immutable source bytes under the canonical grayscale policy."""

    return _load_grayscale_image(BytesIO(content))
