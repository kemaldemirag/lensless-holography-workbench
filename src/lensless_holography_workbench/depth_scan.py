"""Deterministic angular-spectrum propagation and depth metrics.

Distances and wavelengths are expressed in micrometres. The implementation
keeps only propagating spatial frequencies. Focus metrics are numerical scan
descriptors; they do not establish a physical-focus acceptance result.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np


@dataclass(frozen=True, slots=True)
class PropagationParameters:
    """Validated parameters for one inclusive depth scan."""

    pixel_pitch_um: float
    wavelength_um: float
    refractive_index: float
    depth_start_um: float
    depth_stop_um: float
    depth_step_um: float
    roi_size_px: int

    def validate(self) -> None:
        numeric = {
            "pixel_pitch_um": self.pixel_pitch_um,
            "wavelength_um": self.wavelength_um,
            "refractive_index": self.refractive_index,
            "depth_start_um": self.depth_start_um,
            "depth_stop_um": self.depth_stop_um,
            "depth_step_um": self.depth_step_um,
        }
        for name, value in numeric.items():
            if not np.isfinite(value):
                raise ValueError(f"{name} must be finite")
        for name in ("pixel_pitch_um", "wavelength_um", "refractive_index", "depth_step_um"):
            if numeric[name] <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if self.depth_stop_um < self.depth_start_um:
            raise ValueError("depth_stop_um must be greater than or equal to depth_start_um")
        if isinstance(self.roi_size_px, bool) or not isinstance(self.roi_size_px, int):
            raise ValueError("roi_size_px must be an integer")
        if self.roi_size_px <= 0:
            raise ValueError("roi_size_px must be greater than zero")

        steps = (self.depth_stop_um - self.depth_start_um) / self.depth_step_um
        if not np.isclose(steps, round(steps), rtol=0.0, atol=1e-10):
            raise ValueError("depth range must contain an integer number of steps")

    def depths_um(self) -> np.ndarray:
        self.validate()
        count = int(round((self.depth_stop_um - self.depth_start_um) / self.depth_step_um)) + 1
        return np.linspace(self.depth_start_um, self.depth_stop_um, count, dtype=np.float64)

    def as_dict(self) -> dict[str, float | int]:
        return {
            "pixel_pitch_um": self.pixel_pitch_um,
            "wavelength_um": self.wavelength_um,
            "refractive_index": self.refractive_index,
            "depth_start_um": self.depth_start_um,
            "depth_stop_um": self.depth_stop_um,
            "depth_step_um": self.depth_step_um,
            "roi_size_px": self.roi_size_px,
        }


@dataclass(frozen=True, slots=True)
class DepthMetric:
    depth_um: float
    gradient_energy: float
    amplitude_std: float

    def as_dict(self) -> dict[str, float]:
        return {
            "depth_um": self.depth_um,
            "gradient_energy": self.gradient_energy,
            "amplitude_std": self.amplitude_std,
        }


@dataclass(frozen=True, slots=True)
class DepthScanResult:
    metrics: tuple[DepthMetric, ...]
    input_shape: tuple[int, int]
    roi_bounds: tuple[int, int, int, int]
    source_fingerprint: str

    def __post_init__(self) -> None:
        if not self.metrics:
            raise ValueError("at least one depth metric is required")

    @property
    def gradient_peak_um(self) -> float:
        return max(self.metrics, key=lambda item: item.gradient_energy).depth_um

    @property
    def amplitude_std_peak_um(self) -> float:
        return max(self.metrics, key=lambda item: item.amplitude_std).depth_um

    def as_summary(self) -> dict[str, object]:
        top, left, bottom, right = self.roi_bounds
        return {
            "gradient_peak_um": self.gradient_peak_um,
            "amplitude_std_peak_um": self.amplitude_std_peak_um,
            "input_shape": list(self.input_shape),
            "roi": {
                "top": top,
                "left": left,
                "bottom": bottom,
                "right": right,
                "shape": [bottom - top, right - left],
            },
            "planes": len(self.metrics),
            "source_fingerprint_sha256": self.source_fingerprint,
        }


def _as_image(array: np.ndarray, *, name: str) -> np.ndarray:
    image = np.asarray(array, dtype=np.float64)
    if image.ndim != 2:
        raise ValueError(f"{name} must be a two-dimensional image")
    if image.size == 0:
        raise ValueError(f"{name} must not be empty")
    if not np.isfinite(image).all():
        raise ValueError(f"{name} must contain only finite values")
    return image


def normalize_hologram(signal: np.ndarray, backgrounds: Sequence[np.ndarray]) -> np.ndarray:
    """Divide a signal by the mean background and scale its mean to one."""

    source = _as_image(signal, name="signal")
    if not backgrounds:
        raise ValueError("at least one background image is required")
    converted = [_as_image(background, name=f"background[{index}]") for index, background in enumerate(backgrounds)]
    if any(background.shape != source.shape for background in converted):
        raise ValueError("signal and background images must have the same shape")

    mean_background = np.mean(np.stack(converted, axis=0), axis=0, dtype=np.float64)
    if np.any(mean_background <= 0):
        raise ValueError("mean background must be strictly positive at every pixel")
    normalized = source / mean_background
    scale = float(normalized.mean())
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("background-corrected signal must have a positive finite mean")
    normalized /= scale
    return normalized


def _center_bounds(shape: tuple[int, int], size: int) -> tuple[int, int, int, int]:
    height, width = shape
    if size > height or size > width:
        raise ValueError("roi_size_px must fit inside the input image")
    top = (height - size) // 2
    left = (width - size) // 2
    return top, left, top + size, left + size


def _source_fingerprint(normalized: np.ndarray, parameters: PropagationParameters) -> str:
    canonical = np.ascontiguousarray(normalized, dtype="<f8")
    parameter_bytes = json.dumps(
        parameters.as_dict(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    digest = hashlib.sha256()
    digest.update(b"lensless-holography-wavefront-v1\0")
    digest.update(parameter_bytes)
    digest.update(b"\0")
    digest.update(json.dumps(list(canonical.shape), separators=(",", ":")).encode("ascii"))
    digest.update(b"\0")
    digest.update(canonical.tobytes(order="C"))
    return digest.hexdigest()


@dataclass(slots=True)
class PreparedWavefront:
    """Precomputed spectrum and propagation wave numbers for one hologram."""

    spectrum: np.ndarray
    kz_per_um: np.ndarray
    roi_bounds: tuple[int, int, int, int]
    parameters: PropagationParameters
    source_fingerprint: str

    @classmethod
    def from_normalized(
        cls,
        normalized: np.ndarray,
        parameters: PropagationParameters,
    ) -> "PreparedWavefront":
        parameters.validate()
        image = _as_image(normalized, name="normalized")
        height, width = image.shape
        bounds = _center_bounds((height, width), parameters.roi_size_px)

        fx = np.fft.fftfreq(width, d=parameters.pixel_pitch_um)
        fy = np.fft.fftfreq(height, d=parameters.pixel_pitch_um)
        grid_x, grid_y = np.meshgrid(fx, fy, indexing="xy")
        radicand = (parameters.refractive_index / parameters.wavelength_um) ** 2 - grid_x**2 - grid_y**2
        propagating = radicand >= 0
        kz = np.sqrt(np.maximum(radicand, 0.0))
        spectrum = np.where(propagating, np.fft.fft2(image), 0.0)
        return cls(
            spectrum=spectrum,
            kz_per_um=kz,
            roi_bounds=bounds,
            parameters=parameters,
            source_fingerprint=_source_fingerprint(image, parameters),
        )

    @property
    def input_shape(self) -> tuple[int, int]:
        return int(self.spectrum.shape[0]), int(self.spectrum.shape[1])

    def amplitude_at(self, depth_um: float) -> np.ndarray:
        if not np.isfinite(depth_um):
            raise ValueError("depth_um must be finite")
        phase = np.exp(-2j * np.pi * float(depth_um) * self.kz_per_um)
        amplitude = np.abs(np.fft.ifft2(self.spectrum * phase))
        top, left, bottom, right = self.roi_bounds
        return np.asarray(amplitude[top:bottom, left:right], dtype=np.float64)


def measure_depth_plane(depth_um: float, amplitude: np.ndarray) -> DepthMetric:
    """Measure one reconstructed plane without recomputing propagation."""

    image = _as_image(amplitude, name="amplitude")
    gradient_y, gradient_x = np.gradient(image)
    return DepthMetric(
        depth_um=float(depth_um),
        gradient_energy=float(np.mean(gradient_x**2 + gradient_y**2)),
        amplitude_std=float(np.std(image)),
    )


def scan_depths(
    prepared: PreparedWavefront,
    depths_um: Iterable[float],
) -> DepthScanResult:
    depths = tuple(float(depth) for depth in depths_um)
    if not depths:
        raise ValueError("at least one depth is required")
    if not np.isfinite(depths).all():
        raise ValueError("all depths must be finite")
    metrics = tuple(
        measure_depth_plane(depth, prepared.amplitude_at(depth))
        for depth in depths
    )
    return DepthScanResult(
        metrics=metrics,
        input_shape=prepared.input_shape,
        roi_bounds=prepared.roi_bounds,
        source_fingerprint=prepared.source_fingerprint,
    )


def evaluate_depth_scan(
    signal: np.ndarray,
    backgrounds: Sequence[np.ndarray],
    parameters: PropagationParameters,
) -> tuple[PreparedWavefront, DepthScanResult]:
    normalized = normalize_hologram(signal, backgrounds)
    prepared = PreparedWavefront.from_normalized(normalized, parameters)
    return prepared, scan_depths(prepared, parameters.depths_um())


def write_metrics_csv(result: DepthScanResult, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("depth_um", "gradient_energy", "amplitude_std"), lineterminator="\n")
        writer.writeheader()
        for metric in result.metrics:
            writer.writerow(
                {
                    "depth_um": format(metric.depth_um, ".17g"),
                    "gradient_energy": format(metric.gradient_energy, ".17g"),
                    "amplitude_std": format(metric.amplitude_std, ".17g"),
                }
            )
    return output

