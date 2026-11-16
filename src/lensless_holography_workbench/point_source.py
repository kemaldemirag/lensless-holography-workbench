# Copyright 2017 Universidad Nacional de Colombia
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Point-source Kirchhoff--Helmholtz reconstruction for DLHM contrast fields.

This is an independent NumPy port of the numerical path in UNAL DLHM's
``KirchhoffHelmholtz.java`` at pinned commit
``5d5273e2ebb08735c531815d3e4b100a4e3f3772``.  It preserves the upstream
even-grid indexing, spherical interpolation, two-times zero padding, FFT
convolution, quadrant shift, centre crop, and phase signs.  It deliberately
does not reuse the plane-wave angular-spectrum backend.

All lengths are micrometres.  Public arrays use NumPy's conventional
``(height, width)`` order; the upstream implementation internally used
``(width, height)`` arrays.  The input is an already prepared real contrast
hologram, such as ``hologram - reference``.  No background inference,
intensity scaling, fixture loading, focus selection, or acceptance decision is
performed here.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field as dataclass_field
from typing import Any, ClassVar

import numpy as np

from .unal_fixture import FixtureOptics

BACKEND_ID = "point_source_kirchhoff_helmholtz"
UPSTREAM_COMMIT = "5d5273e2ebb08735c531815d3e4b100a4e3f3772"
UPSTREAM_KERNEL_BLOB = "842c2390721018504fe74968a5bf8a378e7536d6"
UPSTREAM_WORKER_BLOB = "20c0f66ce42db53db4a11aec110d9841dff35682"

# The conservative peak model reserves 768 bytes per source pixel for the
# persistent kernels, 2x-by-2x complex FFT arrays, NumPy temporaries, immutable
# result copies, and FFT workspace.  The guard is evaluated before any kernel
# allocation; it is a deterministic resource boundary, not an OS-memory probe.
MAX_POINT_SOURCE_WORKING_BYTES = 768 * 1024 * 1024
_ESTIMATED_PEAK_BYTES_PER_PIXEL = 768
MAX_POINT_SOURCE_PIXELS = (
    MAX_POINT_SOURCE_WORKING_BYTES // _ESTIMATED_PEAK_BYTES_PER_PIXEL
)
_MAX_SAFE_DISTANCE_UM = math.sqrt(float(np.finfo(np.float64).max) / 16.0)
_INTERNAL_CONSTRUCTION_TOKEN = object()


def _positive_finite(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")
    return result


def _validated_shape(shape: tuple[int, int]) -> tuple[int, int]:
    if (
        not isinstance(shape, tuple)
        or len(shape) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) for value in shape)
    ):
        raise ValueError("input_shape must be a (height, width) integer tuple")
    height, width = shape
    if height < 4 or width < 4:
        raise ValueError("point-source input dimensions must each be at least 4 pixels")
    if height % 2 or width % 2:
        raise ValueError("point-source input dimensions must be even")
    if height * width > MAX_POINT_SOURCE_PIXELS:
        raise ValueError("point-source input exceeds the pixel limit")
    return height, width


def _real_image(array: np.ndarray, *, name: str) -> np.ndarray:
    source = np.asarray(array)
    if np.iscomplexobj(source):
        raise ValueError(f"{name} must be real-valued")
    image = np.array(source, dtype=np.float64, order="C", copy=True)
    if image.ndim != 2:
        raise ValueError(f"{name} must be a two-dimensional image")
    if image.size == 0:
        raise ValueError(f"{name} must not be empty")
    if not np.isfinite(image).all():
        raise ValueError(f"{name} must contain only finite values")
    return image


def _complex_image(
    array: np.ndarray,
    *,
    name: str,
    expected_shape: tuple[int, int],
) -> np.ndarray:
    field = np.array(array, dtype=np.complex128, order="C", copy=True)
    if field.ndim != 2 or field.shape != expected_shape:
        raise ValueError(f"{name} must have shape {expected_shape}")
    if not np.isfinite(field.real).all() or not np.isfinite(field.imag).all():
        raise ValueError(f"{name} must contain only finite values")
    return field


def _readonly(array: np.ndarray) -> np.ndarray:
    source = np.ascontiguousarray(array)
    immutable_buffer = source.tobytes(order="C")
    return np.frombuffer(immutable_buffer, dtype=source.dtype).reshape(source.shape)


@dataclass(frozen=True, slots=True)
class PointSourceParameters:
    """Physical geometry for one point-source reconstruction plane."""

    wavelength_um: float
    reconstruction_distance_um: float
    source_to_screen_distance_um: float
    detector_width_um: float
    detector_height_um: float
    output_width_um: float
    output_height_um: float

    def validate(self, input_shape: tuple[int, int]) -> tuple[int, int]:
        height, width = _validated_shape(input_shape)
        values = {
            "wavelength_um": self.wavelength_um,
            "reconstruction_distance_um": self.reconstruction_distance_um,
            "source_to_screen_distance_um": self.source_to_screen_distance_um,
            "detector_width_um": self.detector_width_um,
            "detector_height_um": self.detector_height_um,
            "output_width_um": self.output_width_um,
            "output_height_um": self.output_height_um,
        }
        checked = {
            name: _positive_finite(value, name=name)
            for name, value in values.items()
        }
        for name in (
            "reconstruction_distance_um",
            "source_to_screen_distance_um",
            "detector_width_um",
            "detector_height_um",
            "output_width_um",
            "output_height_um",
        ):
            if checked[name] > _MAX_SAFE_DISTANCE_UM:
                raise ValueError(f"{name} exceeds the float64 geometry limit")
        if (
            checked["reconstruction_distance_um"]
            >= checked["source_to_screen_distance_um"]
        ):
            raise ValueError(
                "reconstruction_distance_um must be less than "
                "source_to_screen_distance_um"
            )
        return height, width

    def as_dict(self) -> dict[str, float | str]:
        return {
            "backend": BACKEND_ID,
            "wavelength_um": float(self.wavelength_um),
            "reconstruction_distance_um": float(self.reconstruction_distance_um),
            "source_to_screen_distance_um": float(self.source_to_screen_distance_um),
            "detector_width_um": float(self.detector_width_um),
            "detector_height_um": float(self.detector_height_um),
            "output_width_um": float(self.output_width_um),
            "output_height_um": float(self.output_height_um),
        }

    def sample_pitches_um(
        self,
        input_shape: tuple[int, int],
    ) -> tuple[float, float, float, float]:
        height, width = self.validate(input_shape)
        return (
            float(self.detector_width_um) / width,
            float(self.detector_height_um) / height,
            float(self.output_width_um) / width,
            float(self.output_height_um) / height,
        )


def parameters_from_fixture_optics(
    optics: FixtureOptics,
    input_shape: tuple[int, int],
) -> PointSourceParameters:
    """Translate a verified fixture record and check its redundant pitches."""

    if not isinstance(optics, FixtureOptics):
        raise TypeError("optics must be a verified FixtureOptics record")
    if optics.backend != BACKEND_ID:
        raise ValueError(f"fixture backend must be {BACKEND_ID}")
    parameters = PointSourceParameters(
        wavelength_um=optics.wavelength_um,
        reconstruction_distance_um=optics.reconstruction_distance_um,
        source_to_screen_distance_um=optics.source_to_screen_distance_um,
        detector_width_um=optics.detector_width_um,
        detector_height_um=optics.detector_height_um,
        output_width_um=optics.output_width_um,
        output_height_um=optics.output_height_um,
    )
    input_x, input_y, output_x, output_y = parameters.sample_pitches_um(
        input_shape
    )
    pitch_pairs = (
        ("input x", input_x, optics.input_sample_pitch_um),
        ("input y", input_y, optics.input_sample_pitch_um),
        ("output x", output_x, optics.output_sample_pitch_um),
        ("output y", output_y, optics.output_sample_pitch_um),
    )
    for label, actual, declared in pitch_pairs:
        if not np.isclose(actual, declared, rtol=1e-9, atol=1e-12):
            raise ValueError(
                f"fixture {label} sample pitch does not match its physical size"
            )
    return parameters


def subtract_reference(
    hologram: np.ndarray,
    reference: np.ndarray,
) -> np.ndarray:
    """Return the exact UNAL intensity contrast ``hologram - reference``."""

    source = _real_image(hologram, name="hologram")
    background = _real_image(reference, name="reference")
    if source.shape != background.shape:
        raise ValueError("hologram and reference must have the same shape")
    return _readonly(source - background)


def _source_fingerprint(
    contrast: np.ndarray,
    parameters: PointSourceParameters,
) -> str:
    canonical = np.ascontiguousarray(contrast, dtype="<f8")
    parameter_bytes = json.dumps(
        parameters.as_dict(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    digest = hashlib.sha256()
    digest.update(b"lensless-holography-point-source-kh-v1\0")
    digest.update(UPSTREAM_COMMIT.encode("ascii"))
    digest.update(b"\0")
    digest.update(parameter_bytes)
    digest.update(b"\0")
    digest.update(json.dumps(list(canonical.shape), separators=(",", ":")).encode("ascii"))
    digest.update(b"\0")
    digest.update(canonical.tobytes(order="C"))
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class PointSourceReconstruction:
    """Immutable numerical output with no acceptance semantics."""

    field: np.ndarray = dataclass_field(repr=False, compare=False)
    interpolated_contrast: np.ndarray = dataclass_field(repr=False, compare=False)
    parameters: PointSourceParameters
    source_fingerprint: str
    _construction_token: object = dataclass_field(
        repr=False,
        compare=False,
        kw_only=True,
    )
    backend: str = dataclass_field(init=False, default=BACKEND_ID)

    def __post_init__(self) -> None:
        if self._construction_token is not _INTERNAL_CONSTRUCTION_TOKEN:
            raise TypeError("use reconstruct_point_source or PreparedPointSource.reconstruct")
        raw_field = np.asarray(self.field)
        if raw_field.ndim != 2:
            raise ValueError("field must be a two-dimensional image")
        shape = (int(raw_field.shape[0]), int(raw_field.shape[1]))
        self.parameters.validate(shape)
        checked_field = _complex_image(
            raw_field,
            name="field",
            expected_shape=shape,
        )
        checked_interpolated = _complex_image(
            self.interpolated_contrast,
            name="interpolated_contrast",
            expected_shape=shape,
        )
        if (
            not isinstance(self.source_fingerprint, str)
            or len(self.source_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.source_fingerprint
            )
        ):
            raise ValueError("source_fingerprint must be a lowercase SHA-256 hex digest")
        object.__setattr__(self, "field", _readonly(checked_field))
        object.__setattr__(
            self,
            "interpolated_contrast",
            _readonly(checked_interpolated),
        )

    @property
    def amplitude(self) -> np.ndarray:
        return _readonly(np.abs(self.field))

    @property
    def intensity(self) -> np.ndarray:
        return _readonly(np.abs(self.field) ** 2)

    @property
    def phase(self) -> np.ndarray:
        return _readonly(np.angle(self.field))

    @property
    def real(self) -> np.ndarray:
        return _readonly(self.field.real.copy())

    @property
    def imaginary(self) -> np.ndarray:
        return _readonly(self.field.imag.copy())

    def as_summary(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "upstream_commit": UPSTREAM_COMMIT,
            "upstream_kernel_blob": UPSTREAM_KERNEL_BLOB,
            "upstream_worker_blob": UPSTREAM_WORKER_BLOB,
            "shape": list(self.field.shape),
            "dtype": str(self.field.dtype),
            "source_fingerprint_sha256": self.source_fingerprint,
            "parameters": self.parameters.as_dict(),
            "validation_scope": "NUMERICAL_CORE_ONLY",
        }


@dataclass(frozen=True, slots=True)
class PreparedPointSource:
    """Precomputed interpolation geometry and KH convolution kernels."""

    parameters: PointSourceParameters
    input_shape: tuple[int, int]
    kernel1: np.ndarray = dataclass_field(repr=False, compare=False)
    kernel2_spectrum: np.ndarray = dataclass_field(repr=False, compare=False)
    output_phase: np.ndarray = dataclass_field(repr=False, compare=False)
    transformed_origin_xy_um: tuple[float, float]
    transformed_pitch_xy_um: tuple[float, float]
    detector_origin_xy_um: tuple[float, float]
    _construction_token: object = dataclass_field(
        repr=False,
        compare=False,
        kw_only=True,
    )
    backend: ClassVar[str] = BACKEND_ID

    def __post_init__(self) -> None:
        if self._construction_token is not _INTERNAL_CONSTRUCTION_TOKEN:
            raise TypeError("use prepare_point_source to construct prepared kernels")
        height, width = self.parameters.validate(self.input_shape)
        if self.input_shape != (height, width):
            raise ValueError("input_shape must use canonical integer dimensions")
        checked_kernel1 = _complex_image(
            self.kernel1,
            name="kernel1",
            expected_shape=(height, width),
        )
        checked_kernel2_spectrum = _complex_image(
            self.kernel2_spectrum,
            name="kernel2_spectrum",
            expected_shape=(2 * height, 2 * width),
        )
        checked_output_phase = _complex_image(
            self.output_phase,
            name="output_phase",
            expected_shape=(height, width),
        )
        coordinate_records = (
            ("transformed_origin_xy_um", self.transformed_origin_xy_um),
            ("transformed_pitch_xy_um", self.transformed_pitch_xy_um),
            ("detector_origin_xy_um", self.detector_origin_xy_um),
        )
        checked_coordinates: dict[str, tuple[float, float]] = {}
        for name, values in coordinate_records:
            if not isinstance(values, tuple) or len(values) != 2:
                raise ValueError(f"{name} must be an (x, y) tuple")
            checked = tuple(float(value) for value in values)
            if not all(math.isfinite(value) for value in checked):
                raise ValueError(f"{name} must contain only finite values")
            checked_coordinates[name] = checked
        if not all(value > 0 for value in checked_coordinates["transformed_pitch_xy_um"]):
            raise ValueError("transformed_pitch_xy_um must be positive")

        object.__setattr__(self, "input_shape", (height, width))
        object.__setattr__(self, "kernel1", _readonly(checked_kernel1))
        object.__setattr__(
            self,
            "kernel2_spectrum",
            _readonly(checked_kernel2_spectrum),
        )
        object.__setattr__(
            self,
            "output_phase",
            _readonly(checked_output_phase),
        )
        for name, values in checked_coordinates.items():
            object.__setattr__(self, name, values)

    @classmethod
    def from_shape(
        cls,
        input_shape: tuple[int, int],
        parameters: PointSourceParameters,
    ) -> "PreparedPointSource":
        if not isinstance(parameters, PointSourceParameters):
            raise TypeError("parameters must be PointSourceParameters")
        height, width = parameters.validate(input_shape)
        dx, dy, dx_out, dy_out = parameters.sample_pitches_um((height, width))
        wavelength = float(parameters.wavelength_um)
        z = float(parameters.reconstruction_distance_um)
        source_to_screen = float(parameters.source_to_screen_distance_um)
        source_to_screen_sq = source_to_screen * source_to_screen
        z_squared = z * z
        try:
            wave_number = 2 * math.pi / wavelength
        except OverflowError as error:
            raise ValueError("wavelength_um is too small for float64") from error
        if not math.isfinite(wave_number):
            raise ValueError("wavelength_um is too small for float64")

        # Upstream M/N are width/height and use this exact asymmetric even-grid
        # endpoint convention.  It is intentionally not "cleaned up" here.
        detector_x0 = -dx * width / 2
        detector_y0 = -dy * height / 2
        detector_xf = dx * (width // 2 - 1)
        detector_yf = dy * (height // 2 - 1)
        transformed_x0 = (
            detector_x0 / math.hypot(source_to_screen, detector_x0)
        ) * source_to_screen
        transformed_y0 = (
            detector_y0 / math.hypot(source_to_screen, detector_y0)
        ) * source_to_screen
        transformed_xf = (
            detector_xf / math.hypot(source_to_screen, detector_xf)
        ) * source_to_screen
        transformed_yf = (
            detector_yf / math.hypot(source_to_screen, detector_yf)
        ) * source_to_screen
        transformed_dx = (transformed_xf - transformed_x0) / width
        transformed_dy = (transformed_yf - transformed_y0) / height
        if (
            not math.isfinite(transformed_dx)
            or not math.isfinite(transformed_dy)
            or transformed_dx <= 0
            or transformed_dy <= 0
        ):
            raise ValueError("transformed detector pitches must be finite and positive")

        m = np.arange(width, dtype=np.float64) - (width // 2 - 1)
        n = np.arange(height, dtype=np.float64) - (height // 2 - 1)
        grid_m, grid_n = np.meshgrid(m, n, indexing="xy")
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                radius_squared = (
                    source_to_screen_sq
                    - (grid_m * transformed_dx) ** 2
                    - (grid_n * transformed_dy) ** 2
                )
                if not np.all(radius_squared > 0):
                    raise ValueError(
                        "Kirchhoff-Helmholtz kernel leaves the spherical domain"
                    )
                radius = np.sqrt(radius_squared)
                output_radius_squared = (
                    z_squared
                    + (grid_m * dx_out) ** 2
                    + (grid_n * dy_out) ** 2
                )
                phase1 = (
                    -(wave_number / source_to_screen_sq)
                    * radius
                    * (z * source_to_screen - output_radius_squared / 2)
                )
                phase2 = -(
                    wave_number
                    / (2 * source_to_screen)
                    * (
                        grid_m**2 * transformed_dx * dx_out
                        + grid_n**2 * transformed_dy * dy_out
                    )
                )
                factor3 = (
                    -0.5
                    / wavelength
                    * (1 / radius_squared)
                    * (1 + radius / source_to_screen)
                )
                phase = phase1 + phase2
                kernel1 = (
                    -factor3 * np.sin(phase) + 1j * factor3 * np.cos(phase)
                )
                kernel2 = np.exp(-1j * phase2)
                output_phase = (
                    transformed_dx * transformed_dy * np.exp(1j * phase2)
                )
        except FloatingPointError as error:
            raise ValueError(
                "point-source geometry exceeds the float64 numerical range"
            ) from error

        padded_kernel2 = np.zeros((2 * height, 2 * width), dtype=np.complex128)
        row_start = height // 2
        column_start = width // 2
        padded_kernel2[
            row_start : row_start + height,
            column_start : column_start + width,
        ] = kernel2
        kernel2_spectrum = np.fft.fft2(padded_kernel2)
        if not (
            np.isfinite(kernel1.real).all()
            and np.isfinite(kernel1.imag).all()
            and np.isfinite(kernel2_spectrum.real).all()
            and np.isfinite(kernel2_spectrum.imag).all()
            and np.isfinite(output_phase.real).all()
            and np.isfinite(output_phase.imag).all()
        ):
            raise ValueError("point-source kernels must be finite")

        # Interpolation uses only the first quadrant before mirroring.  Validate
        # that every transformed coordinate has a real inverse map.
        interpolation_x = transformed_x0 + np.arange(width // 2) * transformed_dx
        interpolation_y = transformed_y0 + np.arange(height // 2) * transformed_dy
        interpolation_grid_x, interpolation_grid_y = np.meshgrid(
            interpolation_x,
            interpolation_y,
            indexing="xy",
        )
        interpolation_radicand = (
            source_to_screen_sq
            - interpolation_grid_x**2
            - interpolation_grid_y**2
        )
        if not np.all(interpolation_radicand > 0):
            raise ValueError("spherical interpolation leaves its real-valued domain")

        return cls(
            parameters=parameters,
            input_shape=(height, width),
            kernel1=np.asarray(kernel1, dtype=np.complex128),
            kernel2_spectrum=np.asarray(kernel2_spectrum, dtype=np.complex128),
            output_phase=np.asarray(output_phase, dtype=np.complex128),
            transformed_origin_xy_um=(transformed_x0, transformed_y0),
            transformed_pitch_xy_um=(transformed_dx, transformed_dy),
            detector_origin_xy_um=(detector_x0, detector_y0),
            _construction_token=_INTERNAL_CONSTRUCTION_TOKEN,
        )

    def interpolate_contrast(self, contrast_hologram: np.ndarray) -> np.ndarray:
        """Apply the pinned spherical remap and return a complex field."""

        image_yx = _real_image(contrast_hologram, name="contrast_hologram")
        if image_yx.shape != self.input_shape:
            raise ValueError(
                f"contrast_hologram must have shape {self.input_shape}"
            )
        image_xy = image_yx.T
        width, height = image_xy.shape
        output_xy = np.zeros((width, height), dtype=np.float64)
        source_to_screen = float(self.parameters.source_to_screen_distance_um)
        source_to_screen_sq = source_to_screen**2
        dx, dy, _dx_out, _dy_out = self.parameters.sample_pitches_um(
            self.input_shape
        )
        transformed_x0, transformed_y0 = self.transformed_origin_xy_um
        transformed_dx, transformed_dy = self.transformed_pitch_xy_um
        detector_x0, detector_y0 = self.detector_origin_xy_um
        end_x = width - 1
        end_y = height - 1

        for x_index in range(width // 2):
            transformed_x = transformed_x0 + x_index * transformed_dx
            for y_index in range(height // 2):
                transformed_y = transformed_y0 + y_index * transformed_dy
                radicand = (
                    source_to_screen_sq
                    - transformed_x**2
                    - transformed_y**2
                )
                if radicand <= 0:
                    raise ValueError(
                        "spherical interpolation left its real-valued domain"
                    )
                inverse_radius = 1 / math.sqrt(radicand)
                source_x = transformed_x * source_to_screen * inverse_radius
                source_y = transformed_y * source_to_screen * inverse_radius
                source_column = (source_x - detector_x0) / dx
                source_row = (source_y - detector_y0) / dy
                column0 = math.floor(source_column)
                row0 = math.floor(source_row)
                column_weight0 = column0 + 1 - source_column
                column_weight1 = 1 - column_weight0
                row_weight0 = row0 + 1 - source_row
                row_weight1 = 1 - row_weight0
                weight00 = column_weight0 * row_weight0
                weight10 = column_weight1 * row_weight0
                weight01 = column_weight0 * row_weight1
                weight11 = column_weight1 * row_weight1

                if (
                    column0 > 0
                    and column0 < width // 2
                    and row0 > 0
                    and row0 < height // 2
                ):
                    output_xy[x_index, y_index] = (
                        weight00 * image_xy[column0, row0]
                        + weight10 * image_xy[column0 + 1, row0]
                        + weight01 * image_xy[column0, row0 + 1]
                        + weight11 * image_xy[column0 + 1, row0 + 1]
                    )
                    output_xy[end_x - x_index, y_index] = (
                        weight00 * image_xy[end_x - column0, row0]
                        + weight10 * image_xy[end_x - (column0 + 1), row0]
                        + weight01 * image_xy[end_x - column0, row0 + 1]
                        + weight11 * image_xy[end_x - (column0 + 1), row0 + 1]
                    )
                    output_xy[x_index, end_y - y_index] = (
                        weight00 * image_xy[column0, end_y - row0]
                        + weight10 * image_xy[column0 + 1, end_y - row0]
                        + weight01 * image_xy[column0, end_y - (row0 + 1)]
                        + weight11 * image_xy[column0 + 1, end_y - (row0 + 1)]
                    )
                    output_xy[end_x - x_index, end_y - y_index] = (
                        weight00 * image_xy[end_x - column0, end_y - row0]
                        + weight10
                        * image_xy[end_x - (column0 + 1), end_y - row0]
                        + weight01
                        * image_xy[end_x - column0, end_y - (row0 + 1)]
                        + weight11
                        * image_xy[
                            end_x - (column0 + 1),
                            end_y - (row0 + 1),
                        ]
                    )
        return _readonly(np.asarray(output_xy.T, dtype=np.complex128))

    def diffract_interpolated(self, interpolated_field: np.ndarray) -> np.ndarray:
        """Propagate one already interpolated complex contrast field."""

        field_yx = _complex_image(
            interpolated_field,
            name="interpolated_field",
            expected_shape=self.input_shape,
        )
        height, width = self.input_shape
        multiplied = field_yx * self.kernel1
        padded = np.zeros((2 * height, 2 * width), dtype=np.complex128)
        row_start = height // 2
        column_start = width // 2
        padded[
            row_start : row_start + height,
            column_start : column_start + width,
        ] = multiplied
        convolved = np.fft.ifft2(np.fft.fft2(padded) * self.kernel2_spectrum)
        shifted = np.fft.fftshift(convolved)
        cropped = shifted[
            row_start : row_start + height,
            column_start : column_start + width,
        ]
        result = cropped * self.output_phase
        if not np.isfinite(result.real).all() or not np.isfinite(result.imag).all():
            raise ValueError("point-source reconstruction produced non-finite values")
        return _readonly(np.asarray(result, dtype=np.complex128))

    def reconstruct(self, contrast_hologram: np.ndarray) -> PointSourceReconstruction:
        contrast = _real_image(contrast_hologram, name="contrast_hologram")
        if contrast.shape != self.input_shape:
            raise ValueError(
                f"contrast_hologram must have shape {self.input_shape}"
            )
        interpolated = self.interpolate_contrast(contrast)
        reconstructed = self.diffract_interpolated(interpolated)
        return PointSourceReconstruction(
            field=reconstructed,
            interpolated_contrast=interpolated,
            parameters=self.parameters,
            source_fingerprint=_source_fingerprint(contrast, self.parameters),
            _construction_token=_INTERNAL_CONSTRUCTION_TOKEN,
        )


def prepare_point_source(
    input_shape: tuple[int, int],
    parameters: PointSourceParameters,
) -> PreparedPointSource:
    return PreparedPointSource.from_shape(input_shape, parameters)


def reconstruct_point_source(
    contrast_hologram: np.ndarray,
    parameters: PointSourceParameters,
) -> PointSourceReconstruction:
    """Prepare and reconstruct one real point-source contrast hologram."""

    contrast = _real_image(contrast_hologram, name="contrast_hologram")
    prepared = prepare_point_source(contrast.shape, parameters)
    return prepared.reconstruct(contrast)


__all__ = [
    "BACKEND_ID",
    "MAX_POINT_SOURCE_PIXELS",
    "MAX_POINT_SOURCE_WORKING_BYTES",
    "PointSourceParameters",
    "PointSourceReconstruction",
    "PreparedPointSource",
    "parameters_from_fixture_optics",
    "prepare_point_source",
    "reconstruct_point_source",
    "subtract_reference",
]
