"""Raw source statistics.

Statistics always describe the **original stored source values**, computed
before any replacement or quantization. A canonical zero must never be read back
as "empty background".

Method notes that belong with the numbers:
  * min/max/finite counts/mean/population variance cover every finite source
    value. Per-chunk moments use a scaled two-pass form and Chan's parallel
    combination, so the result does not depend on how the data was split into
    chunks and a large common offset does not destroy the variance.
  * a moment that still overflows in float64 is reported as unknown with a
    diagnostic, never as zero.
  * uint8/uint16 use a complete exact histogram and nearest-rank quantiles.
  * every other type uses at most one million deterministic, uniformly indexed
    samples: for ``k = min(voxelCount, limit)`` the sampled original linear
    indices are ``floor(j * (voxelCount - 1) / max(k - 1, 1))``.
  * nonfinite samples are discarded and the effective sample count is reported.
  * a sample with no finite value leaves the quantiles unknown, even when the
    whole volume contains finite values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .budgets import MODEL_HISTOGRAM_BINS, QUANTILES, QUANTILE_SAMPLE_LIMIT
from .errors import InputError

EXACT_HISTOGRAM_TYPES = {"uint8", "uint16"}
METHOD_VERSION = "volume-context-stats-2"


def quantile_key(quantile: float) -> str:
    return {0.01: "p01", 0.05: "p05", 0.5: "p50", 0.95: "p95", 0.99: "p99"}[quantile]


@dataclass
class RunningMoments:
    """Chunk-mergeable finite-value moments (count, mean, M2)."""

    count: int = 0
    mean: float = 0.0
    m2: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    overflowed: bool = False

    def merge(self, other: "RunningMoments") -> None:
        self.overflowed = self.overflowed or other.overflowed
        if other.minimum is not None and (self.minimum is None or other.minimum < self.minimum):
            self.minimum = other.minimum
        if other.maximum is not None and (self.maximum is None or other.maximum > self.maximum):
            self.maximum = other.maximum
        if other.count == 0:
            return
        if self.count == 0:
            self.count = other.count
            self.mean = other.mean
            self.m2 = other.m2
            return
        delta = other.mean - self.mean
        combined = self.count + other.count
        mean = self.mean + delta * (other.count / combined)
        m2 = self.m2 + other.m2 + delta * delta * ((self.count * other.count) / combined)
        self.count = combined
        self.mean = mean
        self.m2 = m2
        if not (math.isfinite(mean) and math.isfinite(m2)):
            self.overflowed = True

    @property
    def variance(self) -> float | None:
        if self.count == 0 or self.overflowed:
            return None
        value = self.m2 / self.count
        if not math.isfinite(value):
            return None
        return 0.0 if value < 0 else value


def chunk_moments(values: np.ndarray) -> RunningMoments:
    """Stable (count, mean, M2) for one chunk of finite float64 values."""
    size = int(values.size)
    if size == 0:
        return RunningMoments()
    scale = float(np.max(np.abs(values)))
    if scale == 0.0 or not math.isfinite(scale):
        return RunningMoments(
            count=size,
            mean=0.0,
            m2=0.0,
            minimum=0.0 if scale == 0.0 else float(np.min(values)),
            maximum=0.0 if scale == 0.0 else float(np.max(values)),
        )
    scaled = values / scale
    scaled_mean = float(np.mean(scaled, dtype=np.float64))
    mean = scale * scaled_mean
    deviations = scaled - scaled_mean
    # m2 = scale^2 * sum(dev^2); an infinite product is an honest overflow.
    spread = scale * math.sqrt(float(np.sum(deviations * deviations, dtype=np.float64)))
    m2 = spread * spread
    overflowed = not (math.isfinite(mean) and math.isfinite(m2))
    if overflowed:
        mean = 0.0
        m2 = 0.0
    return RunningMoments(
        count=size,
        mean=mean,
        m2=m2,
        minimum=float(np.min(values)),
        maximum=float(np.max(values)),
        overflowed=overflowed,
    )


@dataclass
class SourceStatistics:
    """Every statistic the Card and the model packet may report about the source."""

    voxel_count: int
    scalar_type: str
    dimensions: tuple[int, int, int]
    chunk_bytes: int
    finite_count: int
    nan_count: int
    positive_infinity_count: int
    negative_infinity_count: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    population_variance: float | None
    overflowed: bool
    histogram: dict
    quantiles: dict[str, float | None]
    quantile_kind: str
    quantile_method_version: str
    quantile_sample_limit: int
    quantile_sampled_value_count: int
    quantile_effective_sample_count: int
    quantile_unknown_reason: str | None = None
    diagnostics: list[dict] = field(default_factory=list)


def _chunk_voxel_count(item_bytes: int, chunk_bytes: int) -> int:
    return max(1, chunk_bytes // max(1, item_bytes))


def sample_indices(voxel_count: int, limit: int) -> np.ndarray:
    """The deterministic uniform index plan from docs/design.md."""
    k = min(voxel_count, limit)
    if k >= voxel_count:
        return np.arange(voxel_count, dtype=np.int64)
    denominator = max(k - 1, 1)
    return np.floor(
        np.arange(k, dtype=np.float64) * (voxel_count - 1) / denominator
    ).astype(np.int64)


def nearest_rank(sorted_values: np.ndarray, quantile: float) -> float:
    """Nearest rank on an ascending array: rank = ceil(q * N), one-based."""
    total = int(sorted_values.size)
    if total == 0:
        raise InputError("empty_sample", "nearest rank needs at least one finite value")
    rank = math.ceil(quantile * total)
    rank = min(max(rank, 1), total)
    return float(sorted_values[rank - 1])


def _exact_histogram(counts: np.ndarray) -> dict:
    """Complete, exact histogram over the full integer value range."""
    return {
        "state": "known",
        "factId": "fact.histogram",
        "method": "complete-exact-bins",
        "sampleKind": "all-finite-values",
        "binCount": int(counts.size),
        "edges": list(range(0, int(counts.size) + 1)),
        "counts": [int(value) for value in counts.tolist()],
        "effectiveSampleCount": int(counts.sum()),
    }


def safe_normalized(value: np.ndarray, low: float, high: float) -> np.ndarray:
    """Map values onto [0, 1] without ever forming an overflowing `high - low`.

    A float64 pair such as (-1e308, 1e308) has a difference that is not
    representable. When the direct span overflows, both ends and the values are
    divided by a common scale first, which keeps the result finite.
    """
    span = high - low
    if math.isfinite(span) and span > 0.0:
        return (value - low) / span
    scale = max(abs(low), abs(high))
    if scale == 0.0 or not math.isfinite(scale):
        raise InputError(
            "quantization_range_invalid",
            "cannot normalize a finite range for canonical quantization",
        )
    scaled_low = low / scale
    scaled_span = high / scale - scaled_low
    if scaled_span <= 0.0 or not math.isfinite(scaled_span):
        raise InputError(
            "quantization_range_invalid",
            "cannot normalize a finite range for canonical quantization",
        )
    return (value / scale - scaled_low) / scaled_span


def uniform_histogram(minimum: float, maximum: float, values: np.ndarray, effective: int) -> dict:
    """`MODEL_HISTOGRAM_BINS` uniform bins over the reported finite range."""
    if not math.isfinite(maximum - minimum):
        # The finite range itself is not representable in float64, so bin edges
        # cannot be written honestly. Report that instead of emitting NaN edges.
        return {
            "state": "unknown",
            "factId": "fact.histogram",
            "reason": "arithmetic_overflow",
            "detail": "the finite value range is not representable in float64, so bin edges are unknown",
        }
    if maximum <= minimum:
        return {
            "state": "known",
            "factId": "fact.histogram",
            "method": "degenerate-single-bin",
            "sampleKind": "deterministic-sample",
            "binCount": 1,
            "edges": [float(minimum), float(minimum)],
            "counts": [int(effective)],
            "effectiveSampleCount": int(effective),
        }
    bin_count = MODEL_HISTOGRAM_BINS
    edges = np.linspace(minimum, maximum, bin_count + 1)
    index = np.floor(safe_normalized(values, minimum, maximum) * bin_count)
    index = np.clip(index, 0, bin_count - 1).astype(np.int64)
    counts = np.bincount(index, minlength=bin_count)
    return {
        "state": "known",
        "factId": "fact.histogram",
        "method": "uniform-bins-from-range",
        "sampleKind": "deterministic-sample",
        "binCount": int(bin_count),
        "edges": [float(value) for value in edges.tolist()],
        "counts": [int(value) for value in counts.tolist()],
        "effectiveSampleCount": int(effective),
    }


def compute_statistics(
    *,
    dtype: np.dtype,
    scalar_type: str,
    dimensions: tuple[int, int, int],
    chunk_reader,
    budgets,
) -> SourceStatistics:
    """Scan a decoded payload in chunks and derive every source statistic.

    `chunk_reader` yields whole-voxel byte blocks in storage order (x fastest).
    """
    is_float = scalar_type.startswith("float")
    voxel_count = dimensions[0] * dimensions[1] * dimensions[2]
    exact_counts = (
        np.zeros(256 if scalar_type == "uint8" else 65536, dtype=np.int64)
        if scalar_type in EXACT_HISTOGRAM_TYPES
        else None
    )
    sampled_plan: np.ndarray | None = (
        None if exact_counts is not None else sample_indices(voxel_count, budgets.quantile_sample_limit)
    )

    moments = RunningMoments()
    nan_count = 0
    positive_infinity_count = 0
    negative_infinity_count = 0
    sampled_values: list[np.ndarray] = []
    seen = 0
    plan_cursor = 0

    for block in chunk_reader:
        values = np.frombuffer(block.tobytes(), dtype=dtype)
        size = int(values.size)
        if size == 0:
            continue
        chunk_start = seen
        chunk_end = seen + size
        seen = chunk_end

        if is_float:
            as_float = values.astype(np.float64, copy=False)
            nan_count += int(np.count_nonzero(np.isnan(as_float)))
            positive_infinity_count += int(np.count_nonzero(np.isposinf(as_float)))
            negative_infinity_count += int(np.count_nonzero(np.isneginf(as_float)))
            finite = np.isfinite(as_float)
            usable = as_float[finite]
        else:
            usable = values.astype(np.float64, copy=False)
        moments.merge(chunk_moments(usable))

        if exact_counts is not None:
            exact_counts += np.bincount(values.astype(np.int64), minlength=exact_counts.size)
        elif sampled_plan is not None:
            end_cursor = int(np.searchsorted(sampled_plan, chunk_end, side="left"))
            local = sampled_plan[plan_cursor:end_cursor] - chunk_start
            if local.size:
                sampled_values.append(values[local].copy())
            plan_cursor = end_cursor

    if seen != voxel_count:
        raise InputError(
            "payload_short",
            f"decoded payload yielded {seen} voxels but the header declares {voxel_count}",
        )

    diagnostics: list[dict] = []
    if moments.overflowed:
        diagnostics.append(
            {
                "code": "statistics_overflow",
                "severity": "warning",
                "stage": "prepare",
                "message": "mean and population variance are unknown: finite-value moments overflowed float64",
            }
        )

    quantiles: dict[str, float | None] = {}
    quantile_unknown_reason: str | None = None
    quantile_effective = 0

    if exact_counts is not None:
        histogram = _exact_histogram(exact_counts)
        cumulative = np.cumsum(exact_counts)
        total_finite = int(cumulative[-1]) if cumulative.size else 0
        quantile_effective = total_finite
        for quantile in QUANTILES:
            key = quantile_key(quantile)
            if total_finite == 0:
                quantiles[key] = None
                quantile_unknown_reason = "no_finite_values"
                continue
            rank = min(max(math.ceil(quantile * total_finite), 1), total_finite)
            quantiles[key] = float(int(np.searchsorted(cumulative, rank, side="left")))
        quantile_kind = "complete-exact-histogram"
        quantile_sampled = voxel_count
    else:
        sampled = (
            np.concatenate(sampled_values)
            if sampled_values
            else np.zeros(0, dtype=dtype)
        )
        if is_float:
            finite_sample = sampled[np.isfinite(sampled.astype(np.float64, copy=False))]
        else:
            finite_sample = sampled
        quantile_effective = int(finite_sample.size)
        if quantile_effective == 0:
            # A sample without a finite value leaves the quantiles unknown even
            # when the full volume does contain finite values.
            quantile_unknown_reason = (
                "sample_had_no_finite_values" if moments.count > 0 else "no_finite_values"
            )
            for quantile in QUANTILES:
                quantiles[quantile_key(quantile)] = None
            histogram = {
                "state": "unknown",
                "factId": "fact.histogram",
                "reason": quantile_unknown_reason,
                "detail": "the deterministic quantile sample contained no finite value",
            }
        else:
            ascending = np.sort(finite_sample.astype(np.float64, copy=False))
            for quantile in QUANTILES:
                quantiles[quantile_key(quantile)] = nearest_rank(ascending, quantile)
            histogram = uniform_histogram(
                float(ascending[0]), float(ascending[-1]), ascending, quantile_effective
            )
        quantile_kind = "deterministic-uniform-sample"
        quantile_sampled = int(sampled_plan.size if sampled_plan is not None else 0)

    return SourceStatistics(
        voxel_count=voxel_count,
        scalar_type=scalar_type,
        dimensions=dimensions,
        chunk_bytes=budgets.chunk_bytes,
        finite_count=moments.count,
        nan_count=nan_count,
        positive_infinity_count=positive_infinity_count,
        negative_infinity_count=negative_infinity_count,
        minimum=moments.minimum,
        maximum=moments.maximum,
        mean=moments.mean if not moments.overflowed else None,
        population_variance=moments.variance,
        overflowed=moments.overflowed,
        histogram=histogram,
        quantiles=quantiles,
        quantile_kind=quantile_kind,
        quantile_method_version=METHOD_VERSION,
        quantile_sample_limit=budgets.quantile_sample_limit or QUANTILE_SAMPLE_LIMIT,
        quantile_sampled_value_count=quantile_sampled,
        quantile_effective_sample_count=quantile_effective,
        quantile_unknown_reason=quantile_unknown_reason,
        diagnostics=diagnostics,
    )
