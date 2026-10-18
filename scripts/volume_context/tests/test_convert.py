"""Canonical conversion: value mapping, provenance, overflow safety and the renderer rule."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from volume_context.budgets import Budgets
from volume_context.convert import convert_to_canonical, renderer_uint8, round_half_up
from volume_context.errors import InputError
from volume_context.stats import compute_statistics

DTYPES = {
    "uint8": "<u1",
    "uint16": "<u2",
    "int8": "|i1",
    "int16": "<i2",
    "int32": "<i4",
    "uint32": "<u4",
    "float32": "<f4",
    "float64": "<f8",
}
CANONICAL_DTYPES = {"uint8": "<u1", "uint16": "<u2"}


def convert(values: np.ndarray, scalar_type: str, staging: Path, spacing=None, chunk_bytes: int = 1 << 20):
    dtype = np.dtype(DTYPES[scalar_type])
    payload = values.astype(dtype, copy=False)
    per_chunk = max(1, chunk_bytes // dtype.itemsize)

    def reader():
        for start in range(0, payload.size, per_chunk):
            yield np.frombuffer(payload[start : start + per_chunk].tobytes(), dtype=np.uint8)

    budgets = Budgets(chunk_bytes=chunk_bytes, quantile_sample_limit=max(2, min(1_000_000, payload.size)))
    statistics = compute_statistics(
        dtype=dtype,
        scalar_type=scalar_type,
        dimensions=(payload.size, 1, 1),
        chunk_reader=reader(),
        budgets=budgets,
    )
    staging.mkdir(parents=True, exist_ok=True)
    result = convert_to_canonical(
        dtype=dtype,
        source_scalar_type=scalar_type,
        statistics=statistics,
        chunk_reader=reader(),
        staging_dir=staging,
        spacing=spacing,
    )
    return result, statistics


def read_canonical(result, dimensions=None):
    dtype = np.dtype(CANONICAL_DTYPES[result.scalar_type])
    return np.frombuffer(result.payload_path.read_bytes(), dtype=dtype)


class IdentityCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.staging = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_uint8_values_are_preserved_exactly(self) -> None:
        values = np.array([0, 7, 200, 255], dtype=np.uint8)
        result, _ = convert(values, "uint8", self.staging)
        self.assertEqual(result.scalar_type, "uint8")
        self.assertTrue(np.array_equal(read_canonical(result), values))
        self.assertEqual(result.transforms[0]["kind"], "identity-copy")
        self.assertEqual(result.transforms[0]["invertibility"], "invertible")

    def test_uint16_values_are_preserved_exactly(self) -> None:
        values = np.array([0, 4096, 65535], dtype=np.uint16)
        result, _ = convert(values, "uint16", self.staging)
        self.assertEqual(result.scalar_type, "uint16")
        self.assertTrue(np.array_equal(read_canonical(result), values))
        self.assertEqual(result.byte_length, 6)

    def test_constant_uint16_stays_constant_in_canonical(self) -> None:
        values = np.full(5, 1000, dtype=np.uint16)
        result, _ = convert(values, "uint16", self.staging)
        # Copying must not min/max normalize; the renderer rule is a separate step.
        self.assertTrue(np.array_equal(read_canonical(result), values))

    def test_canonical_header_declares_little_endian_and_the_layout(self) -> None:
        values = np.array([1, 2, 3, 4], dtype=np.uint16)
        result, _ = convert(values, "uint16", self.staging, spacing=(0.5, 0.5, 0.5))
        text = result.header_path.read_text(encoding="ascii")
        self.assertIn("ObjectFileName: canonical.raw", text)
        self.assertIn("Resolution: 4 1 1", text)
        self.assertIn("SliceThickness: 0.5 0.5 0.5", text)
        self.assertIn("Format: ushort", text)
        self.assertIn("Endianess: little", text)

    def test_absent_spacing_becomes_a_rendering_default_of_one(self) -> None:
        result, _ = convert(np.array([1, 2], dtype=np.uint8), "uint8", self.staging, spacing=None)
        self.assertIn("SliceThickness: 1.0 1.0 1.0", result.header_path.read_text(encoding="ascii"))


class SignedOffsetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.staging = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_int16_is_shifted_by_the_source_minimum(self) -> None:
        values = np.array([-5, 0, 5], dtype=np.int16)
        result, _ = convert(values, "int16", self.staging)
        self.assertEqual(result.scalar_type, "uint16")
        self.assertTrue(np.array_equal(read_canonical(result), np.array([0, 5, 10], dtype=np.uint16)))
        transform = result.transforms[0]
        self.assertEqual(transform["kind"], "signed-offset")
        self.assertEqual(transform["offset"], -5.0)
        self.assertEqual(transform["invertibility"], "invertible")

    def test_int8_shifts_into_uint8(self) -> None:
        values = np.array([-128, 0, 127], dtype=np.int8)
        result, _ = convert(values, "int8", self.staging)
        self.assertEqual(result.scalar_type, "uint8")
        self.assertTrue(np.array_equal(read_canonical(result), np.array([0, 128, 255], dtype=np.uint8)))

    def test_int16_full_span_fits_uint16(self) -> None:
        values = np.array([-32_768, 32_767], dtype=np.int16)
        result, _ = convert(values, "int16", self.staging)
        self.assertTrue(np.array_equal(read_canonical(result), np.array([0, 65_535], dtype=np.uint16)))


class QuantizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.staging = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_round_half_up_boundaries(self) -> None:
        # 0.5 must round up, and exact integers must be preserved.
        values = np.array([0.0, 0.5, 1.4999999, 1.5, 2.5], dtype=np.float64)
        self.assertEqual(round_half_up(np.array([0.5])).tolist(), [1.0])
        result, _ = convert(values, "float64", self.staging)
        # min 0, max 2.5 -> canonical = round_half_up(v / 2.5 * 65535)
        expected = np.floor(np.array(values) / 2.5 * 65_535 + 0.5)
        self.assertTrue(np.array_equal(read_canonical(result), expected.astype(np.uint16)))

    def test_quantization_maps_min_and_max_to_the_full_range(self) -> None:
        values = np.array([-10.0, 0.0, 10.0], dtype=np.float64)
        result, _ = convert(values, "float64", self.staging)
        canonical = read_canonical(result)
        self.assertEqual(int(canonical[0]), 0)
        self.assertEqual(int(canonical[2]), 65_535)
        # 0 is exactly halfway, so round-half-up gives 32768.
        self.assertEqual(int(canonical[1]), 32_768)

    def test_int32_is_quantized_to_uint16(self) -> None:
        values = np.array([0, 1_000, 2_000], dtype=np.int32)
        result, _ = convert(values, "int32", self.staging)
        self.assertEqual(result.scalar_type, "uint16")
        canonical = read_canonical(result)
        self.assertEqual(int(canonical[0]), 0)
        self.assertEqual(int(canonical[2]), 65_535)
        self.assertEqual(result.transforms[0]["rounding"], "round-half-up")
        self.assertEqual(result.transforms[0]["invertibility"], "lossy-range-and-precision")

    def test_uint32_full_range_does_not_overflow(self) -> None:
        values = np.array([0, 2_147_483_647, 4_294_967_295], dtype=np.uint32)
        result, _ = convert(values, "uint32", self.staging)
        canonical = read_canonical(result)
        self.assertEqual(int(canonical[0]), 0)
        self.assertEqual(int(canonical[2]), 65_535)
        self.assertTrue(np.all(np.isfinite(canonical.astype(np.float64))))

    def test_extreme_float64_values_do_not_overflow_the_normalization(self) -> None:
        values = np.array([-1e308, 0.0, 1e308], dtype=np.float64)
        result, _ = convert(values, "float64", self.staging)
        canonical = read_canonical(result)
        self.assertEqual(int(canonical[0]), 0)
        self.assertEqual(int(canonical[2]), 65_535)
        # The midpoint of an unrepresentable range still lands mid-range.
        self.assertGreater(int(canonical[1]), 32_000)
        self.assertLess(int(canonical[1]), 33_600)

    def test_constant_range_produces_zeros_and_records_the_degeneracy(self) -> None:
        values = np.full(4, 7.5, dtype=np.float64)
        result, _ = convert(values, "float64", self.staging)
        self.assertTrue(np.all(read_canonical(result) == 0))
        transform = result.transforms[0]
        self.assertEqual(transform["kind"], "degenerate-range-zero")
        self.assertEqual(transform["invertibility"], "constant-degenerate")
        self.assertEqual(transform["clipping"]["reason"], "degenerate-range")
        self.assertEqual(transform["clipping"]["count"], 0)

    def test_quantization_is_chunk_size_independent(self) -> None:
        values = np.linspace(-3.5, 9.25, 64)
        reference, _ = convert(values, "float64", self.staging / "a", chunk_bytes=1 << 20)
        other, _ = convert(values, "float64", self.staging / "b", chunk_bytes=64)
        self.assertTrue(np.array_equal(read_canonical(reference), read_canonical(other)))


class NonfiniteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.staging = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_partial_nonfinite_writes_zero_and_records_each_count(self) -> None:
        values = np.array([0.0, np.nan, 2.0, np.inf, -np.inf, 4.0], dtype=np.float64)
        result, statistics = convert(values, "float64", self.staging)
        canonical = read_canonical(result)
        self.assertEqual(int(canonical[1]), 0)
        self.assertEqual(int(canonical[3]), 0)
        self.assertEqual(int(canonical[4]), 0)
        self.assertEqual(int(canonical[0]), 0)
        self.assertEqual(int(canonical[5]), 65_535)
        # The finite statistics describe the source, before any substitution.
        self.assertEqual(statistics.finite_count, 3)
        self.assertEqual(statistics.minimum, 0.0)
        self.assertEqual(statistics.maximum, 4.0)
        replace = [t for t in result.transforms if t["kind"] == "nonfinite-replace"]
        self.assertEqual(len(replace), 1)
        self.assertEqual(replace[0]["nonfiniteReplacement"]["nanCount"], 1)
        self.assertEqual(replace[0]["nonfiniteReplacement"]["positiveInfinityCount"], 1)
        self.assertEqual(replace[0]["nonfiniteReplacement"]["negativeInfinityCount"], 1)
        self.assertEqual(replace[0]["invertibility"], "lossy-nonfinite-replaced")

    def test_source_zero_and_substituted_zero_are_distinguishable_in_provenance(self) -> None:
        values = np.array([0.0, np.nan, 4.0], dtype=np.float64)
        result, statistics = convert(values, "float64", self.staging)
        # Both land on canonical 0, but only one of them was a substitution.
        self.assertEqual(int(read_canonical(result)[0]), 0)
        self.assertEqual(int(read_canonical(result)[1]), 0)
        self.assertEqual(statistics.nan_count, 1)
        self.assertEqual(statistics.finite_count, 2)
        self.assertTrue(
            any(d["code"] == "nonfinite_values_replaced" for d in result.diagnostics),
            "a substituted value must be reported, not silent",
        )

    def test_all_nonfinite_produces_no_usable_asset(self) -> None:
        values = np.array([np.nan, np.inf, -np.inf], dtype=np.float64)
        with self.assertRaises(InputError) as caught:
            convert(values, "float64", self.staging)
        self.assertEqual(caught.exception.code, "no_finite_values")


class RendererRuleTests(unittest.TestCase):
    def test_hand_computed_uint16_mapping(self) -> None:
        values = np.array([0, 1, 2, 3, 4], dtype=np.uint16)
        # min 0, max 4, range 4: (v * 255 + 2) // 4
        self.assertEqual(renderer_uint8(values).tolist(), [0, 64, 128, 191, 255])

    def test_constant_uint16_maps_to_zero(self) -> None:
        self.assertTrue(np.all(renderer_uint8(np.full(4, 42, dtype=np.uint16)) == 0))

    def test_uint8_is_copied_not_normalized(self) -> None:
        values = np.array([0, 1, 128, 255], dtype=np.uint8)
        self.assertTrue(np.array_equal(renderer_uint8(values), values))

    def test_matches_the_reference_formula_over_a_wide_range(self) -> None:
        values = np.arange(0, 65_536, 97, dtype=np.uint16)
        minimum, maximum = int(values.min()), int(values.max())
        value_range = maximum - minimum
        reference = ((values.astype(np.int64) - minimum) * 255 + value_range // 2) // value_range
        self.assertTrue(np.array_equal(renderer_uint8(values).astype(np.int64), reference))

    def test_non_16_bit_input_is_refused(self) -> None:
        with self.assertRaises(InputError) as caught:
            renderer_uint8(np.array([1, 2], dtype=np.int32))
        self.assertEqual(caught.exception.code, "unsupported_scalar_type")


if __name__ == "__main__":
    unittest.main()
