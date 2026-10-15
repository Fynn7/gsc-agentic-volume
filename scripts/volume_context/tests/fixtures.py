"""Generated small fixtures for the volume-context tests.

Every fixture is tiny and asymmetric on purpose, so the expected values can be
computed by hand and an axis-order mistake cannot hide. The reference phantom is
2x3x4 (width x height x depth) and is never symmetric in any axis, so a silent
reshape or transposition changes at least one voxel that a test checks.

Storage order is x fastest:
    index = x + y * width + z * width * height
"""

from __future__ import annotations

import gzip
import struct
from pathlib import Path

import numpy as np

# 2 x 3 x 4, values chosen so every (x, y, z) triple is distinguishable.
WIDTH, HEIGHT, DEPTH = 2, 3, 4
PHANTOM_U8 = np.array(
    [
        1, 2,
        3, 4,
        5, 6,
        7, 8,
        9, 10,
        11, 12,
        13, 14,
        15, 16,
        17, 18,
        19, 20,
        21, 22,
        23, 24,
    ],
    dtype=np.uint8,
)
PHANTOM_U16 = (PHANTOM_U8.astype(np.uint16) * 101) + 7
PHANTOM_I16 = (PHANTOM_U8.astype(np.int16) - 12) * 3  # signed, crosses zero
PHANTOM_F32 = PHANTOM_U8.astype(np.float32) * 0.5 - 3.0


def voxel_index(x: int, y: int, z: int) -> int:
    """The contract's storage order: x fastest, then y, then z."""
    return x + y * WIDTH + z * WIDTH * HEIGHT


def write_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def write_qvis(
    directory: Path,
    values: np.ndarray,
    *,
    scalar_type: str = "uint8",
    dtype: str | None = None,
    payload_name: str | None = None,
    format_token: str | None = None,
    spacing: tuple[float, float, float] | None = (1.0, 1.0, 1.0),
    endian_token: str | None = "little",
    compression: str | None = None,
    omit_format: bool = False,
    extra_lines: tuple[str, ...] = (),
) -> tuple[Path, Path]:
    """Write a QVis text header plus its sibling payload."""
    directory.mkdir(parents=True, exist_ok=True)
    payload_name = payload_name or (f"{scalar_type}-phantom.raw")
    payload_dtype = dtype or {"uint8": "<u1", "uint16": "<u2"}[scalar_type]
    payload = directory / payload_name
    payload.write_bytes(values.astype(np.dtype(payload_dtype), copy=False).tobytes())
    header = directory / "phantom.dat"
    lines = [f"ObjectFileName: {payload_name}", f"Resolution: {WIDTH} {HEIGHT} {DEPTH}"]
    if spacing is not None:
        lines.append(f"SliceThickness: {spacing[0]} {spacing[1]} {spacing[2]}")
    if not omit_format:
        lines.append(f"Format: {format_token or ('uchar' if scalar_type == 'uint8' else 'ushort')}")
    if endian_token is not None:
        lines.append(f"Endianess: {endian_token}")
    if compression is not None:
        lines.append(f"Compression: {compression}")
    lines.extend(extra_lines)
    # utf-8: a `data file` reference may legitimately contain non-ASCII characters.
    header.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return header, payload


def write_nhdr(
    directory: Path,
    values: np.ndarray,
    *,
    nrrd_type: str = "uint8",
    dtype: str = "<u1",
    endian: str | None = "little",
    encoding: str = "raw",
    payload_name: str | None = None,
    space_directions: str | None = None,
    space_origin: str | None = None,
    space_units: str | None = None,
    extra_fields: tuple[str, ...] = (),
    omit_data_file: bool = False,
    magic: str = "NRRD0004",
) -> tuple[Path, Path]:
    """Write a detached NHDR header plus its sibling payload."""
    directory.mkdir(parents=True, exist_ok=True)
    payload_name = payload_name or "nhdr-phantom.raw"
    raw = values.astype(np.dtype(dtype), copy=False).tobytes()
    payload = directory / payload_name
    if encoding == "gzip":
        # mtime=0 keeps the fixture bytes reproducible.
        payload.write_bytes(gzip.compress(raw, mtime=0))
    else:
        payload.write_bytes(raw)

    lines = [magic, f"type: {nrrd_type}", f"dimension: 3", f"sizes: {WIDTH} {HEIGHT} {DEPTH}"]
    if endian is not None:
        lines.append(f"endian: {endian}")
    lines.append(f"encoding: {encoding}")
    if space_directions is not None:
        lines.append(f"space directions: {space_directions}")
    if space_origin is not None:
        lines.append(f"space origin: {space_origin}")
    if space_units is not None:
        lines.append(f"space units: {space_units}")
    lines.extend(extra_fields)
    if not omit_data_file:
        lines.append(f"data file: {payload_name}")
    header = directory / "phantom.nhdr"
    # utf-8: a `data file` reference may legitimately contain non-ASCII characters.
    header.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return header, payload


def write_raw_only(directory: Path, values: np.ndarray, dtype: str = "<u1") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "orphan.raw"
    path.write_bytes(values.astype(np.dtype(dtype), copy=False).tobytes())
    return path


def expected_mean(values: np.ndarray) -> float:
    return float(np.mean(values.astype(np.float64)))


def expected_population_variance(values: np.ndarray) -> float:
    data = values.astype(np.float64)
    return float(np.sum((data - data.mean()) ** 2) / data.size)


def nearest_rank_reference(sorted_values: list[float], quantile: float) -> float:
    import math

    total = len(sorted_values)
    rank = min(max(math.ceil(quantile * total), 1), total)
    return sorted_values[rank - 1]


def truncated_gzip(directory: Path, values: np.ndarray, dtype: str = "<u1") -> Path:
    """A gzip stream cut before its trailer, so CRC/length cannot be satisfied."""
    directory.mkdir(parents=True, exist_ok=True)
    blob = gzip.compress(values.astype(np.dtype(dtype), copy=False).tobytes(), mtime=0)
    path = directory / "truncated.nhdr.raw"
    path.write_bytes(blob[:-4])
    return path


def corrupt_gzip(directory: Path) -> Path:
    """Valid gzip framing with a damaged deflate payload."""
    directory.mkdir(parents=True, exist_ok=True)
    payload = bytes(range(256)) * 4
    blob = bytearray(gzip.compress(payload, mtime=0))
    blob[12] ^= 0xFF
    path = directory / "corrupt.nhdr.raw"
    path.write_bytes(bytes(blob))
    return path


def big_endian_u16_payload(values: np.ndarray) -> bytes:
    return values.astype(np.dtype(">u2"), copy=False).tobytes()


def struct_floats(values: np.ndarray) -> bytes:
    return struct.pack(f"<{values.size}d", *values.tolist())
