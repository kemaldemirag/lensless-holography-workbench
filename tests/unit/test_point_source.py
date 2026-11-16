from __future__ import annotations

import hashlib
import json
import math
import unittest

import numpy as np

from lensless_holography_workbench.point_source import (
    BACKEND_ID,
    MAX_POINT_SOURCE_PIXELS,
    MAX_POINT_SOURCE_WORKING_BYTES,
    UPSTREAM_COMMIT,
    PointSourceParameters,
    PointSourceReconstruction,
    PreparedPointSource,
    parameters_from_fixture_optics,
    prepare_point_source,
    reconstruct_point_source,
    subtract_reference,
)
from lensless_holography_workbench.unal_fixture import FixtureOptics


def parameters(**overrides: object) -> PointSourceParameters:
    values: dict[str, object] = {
        "wavelength_um": 0.5,
        "reconstruction_distance_um": 100.0,
        "source_to_screen_distance_um": 1_000.0,
        "detector_width_um": 80.0,
        "detector_height_um": 80.0,
        "output_width_um": 40.0,
        "output_height_um": 40.0,
    }
    values.update(overrides)
    return PointSourceParameters(**values)  # type: ignore[arg-type]


def fixture_optics(**overrides: object) -> FixtureOptics:
    values: dict[str, object] = {
        "backend": BACKEND_ID,
        "wavelength_um": 0.5,
        "reconstruction_distance_um": 100.0,
        "source_to_screen_distance_um": 1_000.0,
        "detector_width_um": 80.0,
        "detector_height_um": 80.0,
        "output_width_um": 40.0,
        "output_height_um": 40.0,
        "input_sample_pitch_um": 10.0,
        "output_sample_pitch_um": 5.0,
    }
    values.update(overrides)
    return FixtureOptics(**values)  # type: ignore[arg-type]


def scalar_interpolation_oracle(
    image_yx: np.ndarray,
    optics: PointSourceParameters,
) -> np.ndarray:
    """Direct scalar transcription of the pinned four-quadrant remap."""

    image = np.asarray(image_yx, dtype=np.float64)
    height, width = image.shape
    dx = float(optics.detector_width_um) / width
    dy = float(optics.detector_height_um) / height
    source_to_screen = float(optics.source_to_screen_distance_um)
    source_to_screen_squared = source_to_screen**2

    detector_x0 = -dx * width / 2
    detector_y0 = -dy * height / 2
    detector_xf = dx * (width // 2 - 1)
    detector_yf = dy * (height // 2 - 1)
    transformed_x0 = (
        detector_x0
        * source_to_screen
        / math.sqrt(source_to_screen_squared + detector_x0**2)
    )
    transformed_y0 = (
        detector_y0
        * source_to_screen
        / math.sqrt(source_to_screen_squared + detector_y0**2)
    )
    transformed_xf = (
        detector_xf
        * source_to_screen
        / math.sqrt(source_to_screen_squared + detector_xf**2)
    )
    transformed_yf = (
        detector_yf
        * source_to_screen
        / math.sqrt(source_to_screen_squared + detector_yf**2)
    )
    transformed_dx = (transformed_xf - transformed_x0) / width
    transformed_dy = (transformed_yf - transformed_y0) / height

    result = np.zeros((height, width), dtype=np.complex128)
    last_x = width - 1
    last_y = height - 1
    for first_quadrant_x in range(width // 2):
        transformed_x = transformed_x0 + first_quadrant_x * transformed_dx
        for first_quadrant_y in range(height // 2):
            transformed_y = transformed_y0 + first_quadrant_y * transformed_dy
            inverse_radius = 1 / math.sqrt(
                source_to_screen_squared
                - transformed_x**2
                - transformed_y**2
            )
            source_x = transformed_x * source_to_screen * inverse_radius
            source_y = transformed_y * source_to_screen * inverse_radius
            source_column = (source_x - detector_x0) / dx
            source_row = (source_y - detector_y0) / dy
            column0 = math.floor(source_column)
            row0 = math.floor(source_row)
            column_weights = (
                column0 + 1 - source_column,
                source_column - column0,
            )
            row_weights = (
                row0 + 1 - source_row,
                source_row - row0,
            )
            if not (
                0 < column0 < width // 2
                and 0 < row0 < height // 2
            ):
                continue

            for mirror_x in (False, True):
                output_x = (
                    last_x - first_quadrant_x
                    if mirror_x
                    else first_quadrant_x
                )
                source_columns = (
                    (last_x - column0, last_x - (column0 + 1))
                    if mirror_x
                    else (column0, column0 + 1)
                )
                for mirror_y in (False, True):
                    output_y = (
                        last_y - first_quadrant_y
                        if mirror_y
                        else first_quadrant_y
                    )
                    source_rows = (
                        (last_y - row0, last_y - (row0 + 1))
                        if mirror_y
                        else (row0, row0 + 1)
                    )
                    value = 0.0
                    for column_offset in (0, 1):
                        for row_offset in (0, 1):
                            value += (
                                column_weights[column_offset]
                                * row_weights[row_offset]
                                * image[
                                    source_rows[row_offset],
                                    source_columns[column_offset],
                                ]
                            )
                    result[output_y, output_x] = value
    return result


def scalar_kernels(
    shape: tuple[int, int],
    optics: PointSourceParameters,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build all three KH kernels independently, one pixel at a time."""

    height, width = shape
    wavelength = float(optics.wavelength_um)
    z = float(optics.reconstruction_distance_um)
    source_to_screen = float(optics.source_to_screen_distance_um)
    dx = float(optics.detector_width_um) / width
    dy = float(optics.detector_height_um) / height
    dx_out = float(optics.output_width_um) / width
    dy_out = float(optics.output_height_um) / height

    detector_x0 = -dx * width / 2
    detector_y0 = -dy * height / 2
    detector_xf = dx * (width // 2 - 1)
    detector_yf = dy * (height // 2 - 1)
    source_to_screen_squared = source_to_screen**2
    transformed_x0 = (
        detector_x0
        * source_to_screen
        / math.sqrt(source_to_screen_squared + detector_x0**2)
    )
    transformed_y0 = (
        detector_y0
        * source_to_screen
        / math.sqrt(source_to_screen_squared + detector_y0**2)
    )
    transformed_xf = (
        detector_xf
        * source_to_screen
        / math.sqrt(source_to_screen_squared + detector_xf**2)
    )
    transformed_yf = (
        detector_yf
        * source_to_screen
        / math.sqrt(source_to_screen_squared + detector_yf**2)
    )
    transformed_dx = (transformed_xf - transformed_x0) / width
    transformed_dy = (transformed_yf - transformed_y0) / height

    kernel1 = np.empty(shape, dtype=np.complex128)
    kernel2 = np.empty(shape, dtype=np.complex128)
    output_phase = np.empty(shape, dtype=np.complex128)
    wave_number = 2 * math.pi / wavelength
    for row in range(height):
        n = row - (height // 2 - 1)
        for column in range(width):
            m = column - (width // 2 - 1)
            radius_squared = (
                source_to_screen_squared
                - (m * transformed_dx) ** 2
                - (n * transformed_dy) ** 2
            )
            radius = math.sqrt(radius_squared)
            output_radius_squared = (
                z**2 + (m * dx_out) ** 2 + (n * dy_out) ** 2
            )
            phase1 = (
                -(wave_number / source_to_screen_squared)
                * radius
                * (z * source_to_screen - output_radius_squared / 2)
            )
            phase2 = -(
                wave_number
                / (2 * source_to_screen)
                * (
                    m**2 * transformed_dx * dx_out
                    + n**2 * transformed_dy * dy_out
                )
            )
            factor3 = (
                -0.5
                / wavelength
                * (1 / radius_squared)
                * (1 + radius / source_to_screen)
            )
            phase = phase1 + phase2
            kernel1[row, column] = complex(
                -factor3 * math.sin(phase),
                factor3 * math.cos(phase),
            )
            kernel2[row, column] = complex(
                math.cos(-phase2),
                math.sin(-phase2),
            )
            output_phase[row, column] = transformed_dx * transformed_dy * complex(
                math.cos(phase2),
                math.sin(phase2),
            )
    return kernel1, kernel2, output_phase


def explicit_dft2(source: np.ndarray, *, inverse: bool = False) -> np.ndarray:
    """Tiny O(N^4) DFT oracle which deliberately does not call np.fft."""

    array = np.asarray(source, dtype=np.complex128)
    rows, columns = array.shape
    sign = 1.0 if inverse else -1.0
    scale = 1 / (rows * columns) if inverse else 1.0
    result = np.empty_like(array)
    for output_row in range(rows):
        for output_column in range(columns):
            total = 0j
            for input_row in range(rows):
                for input_column in range(columns):
                    angle = sign * 2 * math.pi * (
                        output_row * input_row / rows
                        + output_column * input_column / columns
                    )
                    total += array[input_row, input_column] * complex(
                        math.cos(angle),
                        math.sin(angle),
                    )
            result[output_row, output_column] = total * scale
    return result


def explicit_diffraction_oracle(
    interpolated_field: np.ndarray,
    optics: PointSourceParameters,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """KH convolution using scalar kernels and explicit forward/inverse DFTs."""

    height, width = interpolated_field.shape
    kernel1, kernel2, output_phase = scalar_kernels(
        (height, width),
        optics,
    )
    padded_shape = (2 * height, 2 * width)
    row_start = height // 2
    column_start = width // 2
    padded_field = np.zeros(padded_shape, dtype=np.complex128)
    padded_kernel2 = np.zeros(padded_shape, dtype=np.complex128)
    rows = slice(row_start, row_start + height)
    columns = slice(column_start, column_start + width)
    padded_field[rows, columns] = interpolated_field * kernel1
    padded_kernel2[rows, columns] = kernel2
    kernel2_spectrum = explicit_dft2(padded_kernel2)
    convolution = explicit_dft2(
        explicit_dft2(padded_field) * kernel2_spectrum,
        inverse=True,
    )

    shifted = np.empty_like(convolution)
    padded_height, padded_width = padded_shape
    for row in range(padded_height):
        for column in range(padded_width):
            shifted[
                (row + height) % padded_height,
                (column + width) % padded_width,
            ] = convolution[row, column]
    reconstructed = shifted[rows, columns] * output_phase
    return reconstructed, kernel1, kernel2_spectrum, output_phase


class PointSourceParameterTests(unittest.TestCase):
    def test_all_physical_values_must_be_positive_finite_real_numbers(self) -> None:
        names = (
            "wavelength_um",
            "reconstruction_distance_um",
            "source_to_screen_distance_um",
            "detector_width_um",
            "detector_height_um",
            "output_width_um",
            "output_height_um",
        )
        invalid_values = (0, -1, float("nan"), float("inf"), float("-inf"), True, "1")
        for name in names:
            for invalid in invalid_values:
                with self.subTest(name=name, invalid=invalid), self.assertRaises(
                    ValueError
                ):
                    parameters(**{name: invalid}).validate((8, 8))

    def test_reconstruction_plane_must_be_between_source_and_screen(self) -> None:
        for reconstruction_distance in (1_000.0, 1_001.0):
            with self.subTest(
                reconstruction_distance=reconstruction_distance
            ), self.assertRaisesRegex(ValueError, "must be less than"):
                parameters(
                    reconstruction_distance_um=reconstruction_distance
                ).validate((8, 8))

    def test_shape_is_strict_even_bounded_height_width_tuple(self) -> None:
        self.assertEqual(MAX_POINT_SOURCE_WORKING_BYTES, 768 * 1024 * 1024)
        self.assertEqual(MAX_POINT_SOURCE_PIXELS, 1_048_576)
        invalid_shapes: tuple[object, ...] = (
            [8, 8],
            (8,),
            (8, 8, 8),
            (True, 8),
            (8.0, 8),
            (2, 8),
            (8, 2),
            (7, 8),
            (8, 7),
            (4, MAX_POINT_SOURCE_PIXELS // 4 + 2),
        )
        for shape in invalid_shapes:
            with self.subTest(shape=shape), self.assertRaises(ValueError):
                parameters().validate(shape)  # type: ignore[arg-type]

    def test_sample_pitch_order_is_x_y_for_input_then_output(self) -> None:
        optics = parameters(
            detector_width_um=100.0,
            detector_height_um=80.0,
            output_width_um=50.0,
            output_height_um=40.0,
        )

        self.assertEqual(
            optics.sample_pitches_um((8, 10)),
            (10.0, 10.0, 5.0, 5.0),
        )

    def test_spherical_domain_is_checked_before_reconstruction(self) -> None:
        outside_spherical_domain = parameters(
            source_to_screen_distance_um=1_000.0,
            detector_width_um=1e12,
            detector_height_um=1e12,
        )

        with self.assertRaisesRegex(ValueError, "spherical domain"):
            prepare_point_source((8, 8), outside_spherical_domain)

    def test_prepare_requires_the_dedicated_parameter_type(self) -> None:
        with self.assertRaisesRegex(TypeError, "PointSourceParameters"):
            prepare_point_source((8, 8), object())  # type: ignore[arg-type]

    def test_derived_float64_overflow_is_rejected_as_validation_error(self) -> None:
        extreme_cases = (
            parameters(
                reconstruction_distance_um=1e307,
                source_to_screen_distance_um=1e308,
            ),
            parameters(wavelength_um=5e-324),
        )
        for optics in extreme_cases:
            with self.subTest(optics=optics), self.assertRaises(ValueError):
                prepare_point_source((4, 4), optics)


class FixtureParameterAdapterTests(unittest.TestCase):
    def test_verified_fixture_maps_to_parameters_without_acceptance_semantics(self) -> None:
        result = parameters_from_fixture_optics(fixture_optics(), (8, 8))

        self.assertEqual(result, parameters())
        self.assertEqual(result.as_dict()["backend"], BACKEND_ID)

    def test_redundant_pitch_must_match_both_axes(self) -> None:
        mismatches = (
            ("input x", {"detector_width_um": 88.0}),
            ("input y", {"detector_height_um": 88.0}),
            ("output x", {"output_width_um": 44.0}),
            ("output y", {"output_height_um": 44.0}),
        )
        for expected_axis, overrides in mismatches:
            with self.subTest(axis=expected_axis), self.assertRaisesRegex(
                ValueError,
                expected_axis,
            ):
                parameters_from_fixture_optics(
                    fixture_optics(**overrides),
                    (8, 8),
                )

    def test_fixture_backend_is_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, BACKEND_ID):
            parameters_from_fixture_optics(
                fixture_optics(backend="angular_spectrum"),
                (8, 8),
            )

    def test_adapter_rejects_unverified_lookalikes(self) -> None:
        with self.assertRaisesRegex(TypeError, "FixtureOptics"):
            parameters_from_fixture_optics(parameters(), (8, 8))  # type: ignore[arg-type]


class ReferenceSubtractionTests(unittest.TestCase):
    def test_returns_exact_hologram_minus_reference_as_readonly_float64(self) -> None:
        hologram = np.array([[1, 5, 9], [3, 2, 8]], dtype=np.int16)
        reference = np.array([[4, 2, 9], [1, 7, 3]], dtype=np.uint8)

        contrast = subtract_reference(hologram, reference)

        np.testing.assert_array_equal(
            contrast,
            np.array([[-3.0, 3.0, 0.0], [2.0, -5.0, 5.0]]),
        )
        self.assertEqual(contrast.dtype, np.dtype(np.float64))
        self.assertFalse(contrast.flags.writeable)
        with self.assertRaises(ValueError):
            contrast[0, 0] = 100.0
        with self.assertRaises(ValueError):
            contrast.setflags(write=True)

        hologram[0, 0] = 99
        reference[0, 1] = 99
        np.testing.assert_array_equal(
            contrast,
            np.array([[-3.0, 3.0, 0.0], [2.0, -5.0, 5.0]]),
        )

    def test_rejects_shape_rank_complex_and_nonfinite_inputs(self) -> None:
        invalid_pairs = (
            (np.ones((2, 3)), np.ones((3, 2)), "same shape"),
            (np.ones(4), np.ones(4), "two-dimensional"),
            (np.ones((2, 2), dtype=np.complex128), np.ones((2, 2)), "real-valued"),
            (np.array([[1.0, np.nan]]), np.ones((1, 2)), "finite"),
            (np.ones((1, 2)), np.array([[1.0, np.inf]]), "finite"),
        )
        for hologram, reference, message in invalid_pairs:
            with self.subTest(message=message), self.assertRaisesRegex(
                ValueError,
                message,
            ):
                subtract_reference(hologram, reference)


class PointSourceReconstructionTests(unittest.TestCase):
    def test_output_and_prepared_records_reject_direct_construction(self) -> None:
        with self.assertRaises(TypeError):
            PointSourceReconstruction(
                field=np.ones((4, 4), dtype=np.complex128),
                interpolated_contrast=np.ones((4, 4), dtype=np.complex128),
                parameters=parameters(),
                source_fingerprint="0" * 64,
            )
        with self.assertRaises(TypeError):
            PreparedPointSource(
                parameters=parameters(),
                input_shape=(4, 4),
                kernel1=np.ones((4, 4), dtype=np.complex128),
                kernel2_spectrum=np.ones((8, 8), dtype=np.complex128),
                output_phase=np.ones((4, 4), dtype=np.complex128),
                transformed_origin_xy_um=(-1.0, -1.0),
                transformed_pitch_xy_um=(0.5, 0.5),
                detector_origin_xy_um=(-2.0, -2.0),
            )

    def test_zero_contrast_reconstructs_to_exact_zero(self) -> None:
        result = reconstruct_point_source(np.zeros((8, 8)), parameters())

        np.testing.assert_array_equal(result.interpolated_contrast, np.zeros((8, 8)))
        np.testing.assert_array_equal(result.field, np.zeros((8, 8)))
        np.testing.assert_array_equal(result.amplitude, np.zeros((8, 8)))
        np.testing.assert_array_equal(result.intensity, np.zeros((8, 8)))
        self.assertEqual(result.field.dtype, np.dtype(np.complex128))

    def test_repeat_is_bitwise_deterministic_and_outputs_are_readonly(self) -> None:
        y, x = np.indices((8, 8), dtype=np.float64)
        contrast = 0.25 * x - 0.4 * y + np.sin(x + 2 * y)
        original = contrast.copy()
        prepared = prepare_point_source(contrast.shape, parameters())

        first = prepared.reconstruct(contrast)
        second = prepared.reconstruct(contrast)

        np.testing.assert_array_equal(contrast, original)
        np.testing.assert_array_equal(first.interpolated_contrast, second.interpolated_contrast)
        np.testing.assert_array_equal(first.field, second.field)
        self.assertEqual(first.source_fingerprint, second.source_fingerprint)
        arrays = (
            prepared.kernel1,
            prepared.kernel2_spectrum,
            prepared.output_phase,
            first.interpolated_contrast,
            first.field,
            first.amplitude,
            first.intensity,
            first.phase,
            first.real,
            first.imaginary,
        )
        for array in arrays:
            with self.subTest(dtype=array.dtype):
                self.assertFalse(array.flags.writeable)
                with self.assertRaises(ValueError):
                    array.flat[0] = 0
                with self.assertRaises(ValueError):
                    array.setflags(write=True)

    def test_reconstruction_is_linear(self) -> None:
        random = np.random.default_rng(20261116)
        left = random.normal(size=(8, 8))
        right = random.normal(size=(8, 8))
        prepared = prepare_point_source((8, 8), parameters())

        combined = prepared.reconstruct(1.75 * left - 0.4 * right).field
        expected = (
            1.75 * prepared.reconstruct(left).field
            - 0.4 * prepared.reconstruct(right).field
        )

        np.testing.assert_allclose(combined, expected, rtol=2e-13, atol=2e-15)

    def test_square_equal_extent_geometry_is_transpose_symmetric(self) -> None:
        random = np.random.default_rng(7247)
        contrast = random.normal(size=(8, 8))
        optics = parameters()

        direct = reconstruct_point_source(contrast, optics)
        transposed = reconstruct_point_source(contrast.T, optics)

        np.testing.assert_allclose(
            transposed.interpolated_contrast,
            direct.interpolated_contrast.T,
            rtol=2e-13,
            atol=2e-15,
        )
        np.testing.assert_allclose(
            transposed.field,
            direct.field.T,
            rtol=2e-12,
            atol=2e-15,
        )

    def test_input_field_validation_is_fail_closed(self) -> None:
        prepared = prepare_point_source((8, 8), parameters())
        invalid_contrasts = (
            np.ones(8),
            np.ones((7, 8)),
            np.ones((8, 8), dtype=np.complex128),
            np.full((8, 8), np.nan),
        )
        for invalid in invalid_contrasts:
            with self.subTest(shape=invalid.shape), self.assertRaises(ValueError):
                prepared.reconstruct(invalid)

        invalid_fields = (
            np.ones((7, 8), dtype=np.complex128),
            np.full((8, 8), complex(np.inf, 0)),
            np.full((8, 8), complex(0, np.nan)),
        )
        for invalid in invalid_fields:
            with self.subTest(shape=invalid.shape), self.assertRaises(ValueError):
                prepared.diffract_interpolated(invalid)

    def test_field_views_and_summary_have_only_numerical_core_semantics(self) -> None:
        contrast = np.arange(64, dtype=np.float64).reshape(8, 8)
        result = reconstruct_point_source(contrast, parameters())

        np.testing.assert_allclose(result.amplitude, np.abs(result.field), rtol=0, atol=0)
        np.testing.assert_allclose(result.intensity, np.abs(result.field) ** 2, rtol=0, atol=0)
        np.testing.assert_allclose(result.phase, np.angle(result.field), rtol=0, atol=0)
        np.testing.assert_array_equal(result.real, result.field.real)
        np.testing.assert_array_equal(result.imaginary, result.field.imag)
        summary = result.as_summary()
        self.assertEqual(summary["backend"], BACKEND_ID)
        self.assertEqual(summary["validation_scope"], "NUMERICAL_CORE_ONLY")
        self.assertNotIn("acceptance", summary)
        self.assertNotIn("pass", summary)

    def test_fingerprint_is_canonical_and_domain_separated(self) -> None:
        contrast = np.arange(64, dtype=np.float64).reshape(8, 8) / 7
        c_order = np.array(contrast, order="C")
        f_order = np.array(contrast, order="F")
        big_endian = contrast.astype(">f8")
        optics = parameters()

        fingerprints = {
            reconstruct_point_source(array, optics).source_fingerprint
            for array in (c_order, f_order, big_endian)
        }
        self.assertEqual(len(fingerprints), 1)
        fingerprint = fingerprints.pop()
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(int(fingerprint, 16).bit_length() <= 256, True)

        canonical = np.ascontiguousarray(contrast, dtype="<f8")
        parameter_bytes = json.dumps(
            optics.as_dict(),
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
        digest.update(json.dumps([8, 8], separators=(",", ":")).encode("ascii"))
        digest.update(b"\0")
        digest.update(canonical.tobytes(order="C"))
        self.assertEqual(fingerprint, digest.hexdigest())

    def test_fingerprint_changes_with_input_and_every_geometry_parameter(self) -> None:
        contrast = np.arange(64, dtype=np.float64).reshape(8, 8)
        baseline = reconstruct_point_source(
            contrast,
            parameters(),
        ).source_fingerprint
        changed_input = contrast.copy()
        changed_input[3, 4] += 1
        self.assertNotEqual(
            baseline,
            reconstruct_point_source(
                changed_input,
                parameters(),
            ).source_fingerprint,
        )

        changes = {
            "wavelength_um": 0.51,
            "reconstruction_distance_um": 101.0,
            "source_to_screen_distance_um": 1_001.0,
            "detector_width_um": 81.0,
            "detector_height_um": 81.0,
            "output_width_um": 41.0,
            "output_height_um": 41.0,
        }
        for name, changed in changes.items():
            with self.subTest(name=name):
                self.assertNotEqual(
                    baseline,
                    reconstruct_point_source(
                        contrast,
                        parameters(**{name: changed}),
                    ).source_fingerprint,
                )


class PointSourceNumericalOracleTests(unittest.TestCase):
    def test_interpolation_matches_independent_scalar_oracle(self) -> None:
        y, x = np.indices((6, 8), dtype=np.float64)
        contrast = 100 * y + x**2 + 0.25 * x * y
        optics = parameters(
            detector_width_um=96.0,
            detector_height_um=54.0,
            output_width_um=32.0,
            output_height_um=18.0,
        )
        prepared = prepare_point_source(contrast.shape, optics)

        expected = scalar_interpolation_oracle(contrast, optics)
        actual = prepared.interpolate_contrast(contrast)

        self.assertGreater(np.count_nonzero(expected), 0)
        np.testing.assert_allclose(actual, expected, rtol=2e-14, atol=2e-14)

    def test_kernels_padding_shift_crop_and_fft_match_explicit_dft(self) -> None:
        shape = (4, 6)
        optics = parameters(
            detector_width_um=84.0,
            detector_height_um=44.0,
            output_width_um=42.0,
            output_height_um=12.0,
        )
        prepared = prepare_point_source(shape, optics)
        y, x = np.indices(shape, dtype=np.float64)
        interpolated = (1 + 2 * x - 0.5 * y) + 1j * (0.25 * x * y - y)

        expected, kernel1, kernel2_spectrum, output_phase = (
            explicit_diffraction_oracle(interpolated, optics)
        )
        actual = prepared.diffract_interpolated(interpolated)

        np.testing.assert_allclose(prepared.kernel1, kernel1, rtol=2e-13, atol=2e-15)
        np.testing.assert_allclose(
            prepared.kernel2_spectrum,
            kernel2_spectrum,
            rtol=3e-13,
            atol=2e-13,
        )
        np.testing.assert_allclose(
            prepared.output_phase,
            output_phase,
            rtol=2e-13,
            atol=2e-15,
        )
        np.testing.assert_allclose(actual, expected, rtol=5e-12, atol=5e-12)


if __name__ == "__main__":
    unittest.main()
