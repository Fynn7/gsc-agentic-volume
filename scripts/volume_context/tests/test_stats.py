"""Chunked source statistics: hand-computable values, sampling, and chunk invariance."""

from __future__ import annotations

import unittest

import numpy as np

from volume_context.budgets import Budgets
from volume_context.stats import compute_statistics, nearest_rank, sample_indices


def statistics_for(values: np.ndarray, scalar_type: str, chunk_bytes: int = 1 << 20, **budget_kwargs):
    dtype = np.dtype(
        {
            "uint8": "<u1",
            "uint16": "<u2",
            "int16": "<i2",
            "int32": "<i4",
            "uint32": "<u4",
            "float32": "<f4",
            "float64": "<f8",
        }[scalar_type]
    )
    payload = values.astype(dtype, copy=False)

    def reader():
        block = max(1, chunk_bytes // dtype.itemsize)
        for start in range(0, payload.size, block):
            yield np.frombuffer(payload[start : start + block].tobytes(), dtype=np.uint8)

    budgets = Budgets(chunk_bytes=chunk_bytes, **budget_kwargs)
    return compute_statistics(
        dtype=dtype,
        scalar_type=scalar_type,
        dimensions=(payload.size, 1, 1),
        chunk_reader=reader(),
        budgets=budgets,
    )


class DeterministicSampleTests(unittest.TestCase):
    def test_index_plan_matches_the_documented_formula(self) -> None:
        plan = sample_indices(10, 4)
        # floor(j * (10 - 1) / max(4 - 1, 1)) for j = 0..3
        self.assertEqual(plan.tolist(), [0, 3, 6, 9])

    def test_index_plan_covers_every_voxel_when_the_limit_is_larger(self) -> None:
        plan = sample_indices(5, 100)
        self.assertEqual(plan.tolist(), [0, 1, 2, 3, 4])

    def test_index_plan_is_deterministic_and_in_range(self) -> None:
        plan = sample_indices(1_000_000, 1_000_000)
        self.assertEqual(int(plan.min()), 0)
        self.assertEqual(int(plan.max()), 999_999)
        self.assertTrue(np.array_equal(plan, sample_indices(1_000_000, 1_000_000)))


class NearestRankTests(unittest.TestCase):
    def test_hand_computed_ranks(self) -> None:
        values = np.array([1.0, 2.0, 3.0, 4.0])
        # ceil(q * 4): 0.01 -> 1, 0.05 -> 1, 0.5 -> 2, 0.95 -> 4, 0.99 -> 4
        self.assertEqual(nearest_rank(values, 0.01), 1.0)
        self.assertEqual(nearest_rank(values, 0.05), 1.0)
        self.assertEqual(nearest_rank(values, 0.5), 2.0)
        self.assertEqual(nearest_rank(values, 0.95), 4.0)
        self.assertEqual(nearest_rank(values, 0.99), 4.0)

    def test_single_sample(self) -> None:
        self.assertEqual(nearest_rank(np.array([7.0]), 0.5), 7.0)


class ExactHistogramTests(unittest.TestCase):
    def test_uint8_uses_a_complete_exact_histogram_and_hand_computed_quantiles(self) -> None:
        values = np.array([1, 2, 3, 4], dtype=np.uint8)
        stats = statistics_for(values, "uint8")
        self.assertEqual(stats.histogram["method"], "complete-exact-bins")
        self.assertEqual(stats.histogram["binCount"], 256)
        self.assertEqual(stats.histogram["edges"][0], 0)
        self.assertEqual(stats.histogram["edges"][-1], 256)
        self.assertEqual(sum(stats.histogram["counts"]), 4)
        self.assertEqual(stats.histogram["counts"][1], 1)
        self.assertEqual(stats.histogram["counts"][4], 1)
        self.assertEqual(stats.quantiles["p50"], 2.0)
        self.assertEqual(stats.quantile_kind, "complete-exact-histogram")
        self.assertEqual(stats.quantile_sampled_value_count, 4)

    def test_uint16_histogram_edges_span_the_full_range(self) -> None:
        stats = statistics_for(np.array([0, 65535], dtype=np.uint16), "uint16")
        self.assertEqual(stats.histogram["binCount"], 65536)
        self.assertEqual(stats.quantiles["p01"], 0.0)
        self.assertEqual(stats.quantiles["p99"], 65535.0)
        self.assertEqual(stats.histogram["effectiveSampleCount"], 2)

    def test_constant_uint16_volume_has_exact_quantiles(self) -> None:
        stats = statistics_for(np.full(5, 4096, dtype=np.uint16), "uint16")
        self.assertEqual(stats.minimum, 4096.0)
        self.assertEqual(stats.maximum, 4096.0)
        for key in ("p01", "p05", "p50", "p95", "p99"):
            self.assertEqual(stats.quantiles[key], 4096.0)


class SampledQuantileTests(unittest.TestCase):
    def test_float_uses_a_deterministic_sample_and_reports_it(self) -> None:
        values = np.arange(1, 11, dtype=np.float64)
        stats = statistics_for(values, "float64", quantile_sample_limit=4)
        self.assertEqual(stats.quantile_kind, "deterministic-uniform-sample")
        self.assertEqual(stats.quantile_sampled_value_count, 4)
        self.assertEqual(stats.quantile_effective_sample_count, 4)
        # sampled original indices: 0, 3, 6, 9 -> values 1, 4, 7, 10
        self.assertEqual(stats.quantiles["p50"], 4.0)
        self.assertEqual(stats.quantiles["p01"], 1.0)
        self.assertEqual(stats.quantiles["p99"], 10.0)

    def test_sampled_histogram_has_32_uniform_bins(self) -> None:
        values = np.linspace(0.0, 1.0, 64)
        stats = statistics_for(values, "float64", quantile_sample_limit=64)
        self.assertEqual(stats.histogram["method"], "uniform-bins-from-range")
        self.assertEqual(stats.histogram["binCount"], 32)
        self.assertEqual(len(stats.histogram["edges"]), 33)
        self.assertEqual(sum(stats.histogram["counts"]), stats.histogram["effectiveSampleCount"])
        self.assertEqual(stats.histogram["edges"][0], 0.0)
        self.assertEqual(stats.histogram["edges"][-1], 1.0)

    def test_constant_sampled_volume_gets_one_degenerate_bin(self) -> None:
        stats = statistics_for(np.full(8, 2.5, dtype=np.float64), "float64", quantile_sample_limit=8)
        self.assertEqual(stats.histogram["method"], "degenerate-single-bin")
        self.assertEqual(stats.histogram["binCount"], 1)
        self.assertEqual(stats.quantiles["p50"], 2.5)

    def test_sampled_volume_all_nonfinite_leaves_quantiles_unknown(self) -> None:
        values = np.array([np.nan, np.inf, -np.inf, np.nan, np.inf, -np.inf], dtype=np.float64)
        stats = statistics_for(values, "float64", quantile_sample_limit=6)
        self.assertEqual(stats.finite_count, 0)
        for key in ("p01", "p50", "p99"):
            self.assertIsNone(stats.quantiles[key])
            self.assertEqual(stats.quantile_unknown_reason, "no_finite_values")
        self.assertEqual(stats.histogram["state"], "unknown")

    def test_sample_without_finite_values_is_unknown_even_when_finite_values_exist(self) -> None:
        # 2000 voxels; the deterministic 4-sample plan lands on indices
        # 0, 666, 1333, 1999 which are all NaN, while other voxels are finite.
        values = np.full(2000, np.nan, dtype=np.float64)
        values[[7, 1000, 1500]] = 5.0
        stats = statistics_for(values, "float64", quantile_sample_limit=4)
        self.assertEqual(stats.finite_count, 3)
        self.assertEqual(stats.nan_count, 1997)
        self.assertIsNone(stats.quantiles["p50"])
        self.assertEqual(stats.quantile_unknown_reason, "sample_had_no_finite_values")
        self.assertEqual(stats.minimum, 5.0)
        self.assertEqual(stats.maximum, 5.0)


class MomentTests(unittest.TestCase):
    def test_hand_computed_mean_and_population_variance(self) -> None:
        values = np.array([1.0, 2.0, 3.0, 6.0], dtype=np.float64)
        stats = statistics_for(values, "float64", quantile_sample_limit=4)
        self.assertAlmostEqual(stats.mean, 3.0, places=12)
        # population variance: ((4+1+0+9)/4) = 3.5
        self.assertAlmostEqual(stats.population_variance, 3.5, places=12)

    def test_statistics_are_stable_across_chunk_sizes(self) -> None:
        rng = np.random.default_rng(20261003)
        values = rng.normal(1_000.0, 5.0, size=5_000)
        reference = statistics_for(values, "float64", chunk_bytes=1 << 20, quantile_sample_limit=5_000)
        for chunk_bytes in (64, 128, 4_096, 16_384):
            with self.subTest(chunk_bytes=chunk_bytes):
                other = statistics_for(
                    values, "float64", chunk_bytes=chunk_bytes, quantile_sample_limit=5_000
                )
                # The tolerance follows from float64 relative rounding on
                # 5000 terms, not from a fitted number.
                self.assertAlmostEqual(other.mean, reference.mean, delta=abs(reference.mean) * 1e-12)
                self.assertAlmostEqual(
                    other.population_variance,
                    reference.population_variance,
                    delta=abs(reference.population_variance) * 1e-10,
                )
                self.assertEqual(other.minimum, reference.minimum)
                self.assertEqual(other.maximum, reference.maximum)
                self.assertEqual(other.quantiles, reference.quantiles)

    def test_arithmetic_overflow_is_unknown_not_zero(self) -> None:
        values = np.array([1e308, -1e308, 1e308], dtype=np.float64)
        stats = statistics_for(values, "float64", quantile_sample_limit=3)
        self.assertTrue(stats.overflowed)
        self.assertIsNone(stats.mean)
        self.assertIsNone(stats.population_variance)
        self.assertEqual(stats.minimum, -1e308)
        self.assertEqual(stats.maximum, 1e308)
        self.assertEqual(stats.diagnostics[0]["code"], "statistics_overflow")
        # Bin edges over an unrepresentable range are unknown, not NaN.
        self.assertEqual(stats.histogram["state"], "unknown")
        self.assertEqual(stats.histogram["reason"], "arithmetic_overflow")
        self.assertIsNotNone(stats.quantiles["p50"])

    def test_large_offset_keeps_a_usable_variance(self) -> None:
        # A naive sum of squares would lose the variance entirely at this offset.
        values = np.array([1e8, 1e8 + 2.0, 1e8 - 2.0, 1e8], dtype=np.float64)
        stats = statistics_for(values, "float64", quantile_sample_limit=4)
        self.assertFalse(stats.overflowed)
        self.assertAlmostEqual(stats.mean, 1e8, places=6)
        # deviations 0, +2, -2, 0 -> sum of squares 8 over 4 values
        self.assertAlmostEqual(stats.population_variance, 2.0, places=6)

    def test_uint32_statistics_do_not_overflow(self) -> None:
        values = np.array([0, 4_294_967_295], dtype=np.uint32)
        stats = statistics_for(values, "uint32", quantile_sample_limit=2)
        self.assertFalse(stats.overflowed)
        self.assertEqual(stats.minimum, 0.0)
        self.assertEqual(stats.maximum, 4_294_967_295.0)
        self.assertAlmostEqual(stats.mean, 2_147_483_647.5, places=6)

    def test_nonfinite_accounting_sums_to_the_voxel_count(self) -> None:
        values = np.array([1.0, np.nan, np.inf, -np.inf, 2.0], dtype=np.float64)
        stats = statistics_for(values, "float64", quantile_sample_limit=5)
        total = (
            stats.finite_count
            + stats.nan_count
            + stats.positive_infinity_count
            + stats.negative_infinity_count
        )
        self.assertEqual(total, stats.voxel_count)
        self.assertEqual(stats.nan_count, 1)
        self.assertEqual(stats.positive_infinity_count, 1)
        self.assertEqual(stats.negative_infinity_count, 1)


if __name__ == "__main__":
    unittest.main()
