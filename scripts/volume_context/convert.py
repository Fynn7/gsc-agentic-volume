"""Canonical QVis conversion and its provenance.

Canonical output is little-endian uint8/uint16. The applied sequence, its
parameters, its rounding rule, its nonfinite substitution and its clipping counts
are recorded, and the invertibility limit is stated rather than glossed over.

Value mapping agreed for this milestone:

===================  ==========================================
source               canonical
===================  ==========================================
uint8 / uint16       values preserved
int8 / int16         ``v - source_min`` into the matching unsigned type
int32/uint32/f32/f64 finite-range linear quantization to uint16,
                     round-half-up
constant range       zeros, recorded as a degenerate range
partial NaN/Inf      finite values convert; invalid positions get 0
all nonfinite        no usable asset; the import fails
===================  ==========================================

Quantization never forms ``max - min`` or an intermediate product in the source
integer domain: values are normalized in float64 through a scale factor, so a
full-range uint32 or an extreme float64 pair cannot overflow the intermediate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .errors import InputError
from .stats import SourceStatistics, safe_normalized

METHOD_VERSION = "volume-context-convert-2"
CANONICAL_MAX = 65535


def input_range(low: float, high: float) -> list:
    """Keep finite endpoints; an unrepresentable span is explicitly unknown."""
    span = high - low
    return [low, high, span if math.isfinite(span) else None]


@dataclass
class CanonicalResult:
    scalar_type: str
    byte_length: int
    payload_path: Path
    header_path: Path
    transforms: list[dict] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)


def _noop_counts(stats: SourceStatistics) -> dict:
    return {
        "replacementValue": 0,
        "nanCount": int(stats.nan_count),
        "positiveInfinityCount": int(stats.positive_infinity_count),
        "negativeInfinityCount": int(stats.negative_infinity_count),
    }


def round_half_up(value: np.ndarray) -> np.ndarray:
    """Round half away from zero for the non-negative normalized values used here."""
    return np.floor(value + 0.5)


def _normalized(value: np.ndarray, low: float, high: float) -> np.ndarray:
    """Map finite values to [0, 1] without overflowing float64 intermediates."""
    return safe_normalized(value, low, high)


def _quantize_block(values: np.ndarray, dtype: np.dtype, low: float, high: float) -> tuple[np.ndarray, int]:
    """Quantize one block, returning the uint16 result and the out-of-range count."""
    normalized = _normalized(values.astype(np.float64, copy=False), low, high)
    below = int(np.count_nonzero(normalized < 0.0))
    above = int(np.count_nonzero(normalized > 1.0))
    clipped = np.clip(normalized, 0.0, 1.0)
    quantized = round_half_up(clipped * CANONICAL_MAX)
    quantized = np.clip(quantized, 0.0, float(CANONICAL_MAX))
    return quantized.astype(np.uint16), below + above


def _write_header(path: Path, payload_name: str, dimensions: tuple[int, int, int], spacing, scalar_type: str) -> None:
    width, height, depth = dimensions
    sx, sy, sz = spacing
    format_name = "uchar" if scalar_type == "uint8" else "ushort"
    text = (
        f"ObjectFileName: {payload_name}\n"
        f"Resolution: {width} {height} {depth}\n"
        f"SliceThickness: {sx} {sy} {sz}\n"
        f"Format: {format_name}\n"
        "Endianess: little\n"
    )
    with path.open("x", encoding="ascii", newline="\n") as handle:
        handle.write(text)


def convert_to_canonical(
    *,
    dtype: np.dtype,
    source_scalar_type: str,
    statistics: SourceStatistics,
    chunk_reader,
    staging_dir: Path,
    canonical_base_name: str = "canonical",
    spacing: tuple[float, float, float] | None,
) -> CanonicalResult:
    """Write the canonical `.dat` + `.raw` pair and return the full transform chain."""
    if statistics.finite_count == 0:
        raise InputError(
            "no_finite_values",
            "the volume has no finite value, so no usable canonical asset can be produced",
        )
    if statistics.minimum is None or statistics.maximum is None:
        raise InputError(
            "statistics_incomplete",
            "finite min/max are required for canonical conversion but were not computed",
        )

    header_name = f"{canonical_base_name}.dat"
    payload_name = f"{canonical_base_name}.raw"
    header_path = staging_dir / header_name
    payload_path = staging_dir / payload_name
    effective_spacing = spacing if spacing is not None else (1.0, 1.0, 1.0)

    transforms: list[dict] = []
    diagnostics: list[dict] = []

    if source_scalar_type in ("uint8", "uint16"):
        canonical_type = source_scalar_type
        transforms.append(
            {
                "transformId": "transform.identity-copy",
                "order": 0,
                "kind": "identity-copy",
                "inputDomain": "source-stored",
                "outputDomain": "canonical-qvis",
                "formula": "canonical values = source values; serialize little endian",
                "rounding": "none",
                "nonfiniteReplacement": _noop_counts(statistics),
                "clipping": {"count": 0, "reason": "none"},
                "invertibility": "invertible",
                "methodVersion": METHOD_VERSION,
            }
        )
        with payload_path.open("xb") as sink:
            written = 0
            for block in chunk_reader:
                values = np.frombuffer(block.tobytes(), dtype=dtype)
                encoded = values.astype(np.dtype("<u1" if canonical_type == "uint8" else "<u2"), copy=False)
                sink.write(encoded.tobytes())
                written += int(encoded.nbytes)
            expected = statistics.voxel_count * np.dtype(canonical_type).itemsize
            if written != expected:
                raise InputError(
                    "payload_short",
                    f"canonical copy wrote {written} bytes, expected {expected}",
                )
        _write_header(header_path, payload_name, statistics.dimensions, effective_spacing, canonical_type)
        return CanonicalResult(
            scalar_type=canonical_type,
            byte_length=written,
            payload_path=payload_path,
            header_path=header_path,
            transforms=transforms,
            diagnostics=diagnostics,
        )

    low = float(statistics.minimum)
    high = float(statistics.maximum)
    degenerate = not (high > low)
    nonfinite_total = (
        statistics.nan_count + statistics.positive_infinity_count + statistics.negative_infinity_count
    )

    if source_scalar_type in ("int8", "int16"):
        canonical_type = "uint8" if source_scalar_type == "int8" else "uint16"
        # Integer offsets cannot overflow: int8 spans 255, int16 spans 32767.
        offset = low
        canonical_max = 255 if canonical_type == "uint8" else 65_535
        transforms.append(
            {
                "transformId": "transform.signed-offset",
                "order": 0,
                "kind": "signed-offset",
                "inputDomain": "source-stored",
                "outputDomain": "canonical-qvis",
                "formula": "canonical = v - offset",
                "rounding": "none",
                "offset": offset,
                "inputRange": input_range(low, high),
                "nonfiniteReplacement": _noop_counts(statistics),
                "clipping": {"count": 0, "reason": "none"},
                "invertibility": "invertible",
                "methodVersion": METHOD_VERSION,
            }
        )
        out_dtype = np.dtype("<u1" if canonical_type == "uint8" else "<u2")
        with payload_path.open("xb") as sink:
            written = 0
            for block in chunk_reader:
                values = np.frombuffer(block.tobytes(), dtype=dtype).astype(np.int64, copy=False)
                canonical = values - int(offset)
                if canonical.size and (int(canonical.min()) < 0 or int(canonical.max()) > canonical_max):
                    raise InputError(
                        "signed_offset_out_of_range",
                        "signed offset would leave the canonical unsigned range",
                    )
                encoded = canonical.astype(out_dtype, copy=False)
                sink.write(encoded.tobytes())
                written += int(encoded.nbytes)
        _write_header(header_path, payload_name, statistics.dimensions, effective_spacing, canonical_type)
        return CanonicalResult(
            scalar_type=canonical_type,
            byte_length=written,
            payload_path=payload_path,
            header_path=header_path,
            transforms=transforms,
            diagnostics=diagnostics,
        )

    # int32 / uint32 / float32 / float64 -> finite-range linear quantization.
    canonical_type = "uint16"
    if degenerate:
        transforms.append(
            {
                "transformId": "transform.degenerate-range-zero",
                "order": 0,
                "kind": "degenerate-range-zero",
                "inputDomain": "source-stored",
                "outputDomain": "canonical-qvis",
                "formula": "range = max - min = 0; every finite value maps to 0",
                "rounding": "none",
                "inputRange": [low, high, 0.0],
                "outputMax": 0,
                "nonfiniteReplacement": _noop_counts(statistics),
                "clipping": {"count": 0, "reason": "degenerate-range"},
                "invertibility": "constant-degenerate",
                "methodVersion": METHOD_VERSION,
            }
        )
        clipping_reason = "degenerate-range"
        clipping_count = 0
    else:
        clipping_reason = "none"
        clipping_count = 0
        transforms.append(
            {
                "transformId": "transform.linear-quantize-to-uint16",
                "order": 0,
                "kind": "linear-quantize-to-uint16",
                "inputDomain": "source-stored",
                "outputDomain": "canonical-qvis",
                "formula": "canonical = round_half_up(clamp((v - min) / (max - min), 0, 1) * 65535)",
                "rounding": "round-half-up",
                "inputRange": input_range(low, high),
                "outputMax": CANONICAL_MAX,
                "nonfiniteReplacement": {**_noop_counts(statistics), "nanCount": 0, "positiveInfinityCount": 0, "negativeInfinityCount": 0},
                "clipping": {"count": 0, "reason": "none"},
                "invertibility": "lossy-range-and-precision",
                "methodVersion": METHOD_VERSION,
            }
        )

    if degenerate:
        transforms[0]["clipping"] = {"count": clipping_count, "reason": clipping_reason}

    if nonfinite_total > 0:
        transforms.append(
            {
                "transformId": "transform.nonfinite-replace",
                "order": len(transforms),
                "kind": "nonfinite-replace",
                "inputDomain": "source-stored",
                "outputDomain": "canonical-qvis",
                "formula": "nonfinite source value -> canonical 0",
                "rounding": "none",
                "nonfiniteReplacement": _noop_counts(statistics),
                "clipping": {"count": 0, "reason": "none"},
                "invertibility": "lossy-nonfinite-replaced",
                "methodVersion": METHOD_VERSION,
            }
        )
        diagnostics.append(
            {
                "code": "nonfinite_values_replaced",
                "severity": "info",
                "stage": "prepare",
                "message": (
                    f"{statistics.nan_count} NaN, {statistics.positive_infinity_count} +Inf and "
                    f"{statistics.negative_infinity_count} -Inf values were written as canonical 0; "
                    "a canonical 0 is not a statement about the source data"
                ),
            }
        )

    with payload_path.open("xb") as sink:
        written = 0
        observed_clipped = 0
        for block in chunk_reader:
            values = np.frombuffer(block.tobytes(), dtype=dtype)
            if degenerate:
                encoded = np.zeros(values.size, dtype=np.uint16)
            elif source_scalar_type.startswith("float"):
                as_float = values.astype(np.float64, copy=False)
                finite_mask = np.isfinite(as_float)
                encoded = np.zeros(values.size, dtype=np.uint16)
                if np.any(finite_mask):
                    quantized, clipped = _quantize_block(as_float[finite_mask], dtype, low, high)
                    encoded[finite_mask] = quantized
                    observed_clipped += clipped
            else:
                quantized, clipped = _quantize_block(
                    values.astype(np.float64, copy=False), dtype, low, high
                )
                encoded = quantized
                observed_clipped += clipped
            sink.write(encoded.astype("<u2", copy=False).tobytes())
            written += int(encoded.nbytes)

    if not degenerate and observed_clipped > 0:
        target = transforms[0]
        target["clipping"] = {
            "count": observed_clipped,
            "reason": "below-quantized-min" if statistics.minimum == low else "above-quantized-max",
        }
        diagnostics.append(
            {
                "code": "canonical_values_clipped",
                "severity": "info",
                "stage": "prepare",
                "message": f"{observed_clipped} finite values were clipped to the canonical 0..65535 range",
            }
        )

    _write_header(header_path, payload_name, statistics.dimensions, effective_spacing, canonical_type)
    return CanonicalResult(
        scalar_type=canonical_type,
        byte_length=written,
        payload_path=payload_path,
        header_path=header_path,
        transforms=transforms,
        diagnostics=diagnostics,
    )


def renderer_uint8(value: np.ndarray) -> np.ndarray:
    """Reproduce the current QVis uint16 to uint8 rule, exactly as the renderer does.

    ``floor(((v - min) * 255 + floor(range / 2)) / range)`` in integer arithmetic;
    a constant volume maps to zero. uint8 input is copied, never renormalized.
    """
    data = np.asarray(value)
    if data.dtype == np.uint8:
        return data.copy()
    if data.dtype != np.uint16:
        raise InputError("unsupported_scalar_type", "the renderer uint8 rule applies to uint16 canonical values")
    minimum = int(data.min())
    maximum = int(data.max())
    value_range = maximum - minimum
    if value_range == 0:
        return np.zeros(data.shape, dtype=np.uint8)
    shifted = data.astype(np.int64) - minimum
    scaled = (shifted * 255 + value_range // 2) // value_range
    return scaled.astype(np.uint8)
