"""Shared reader plumbing: header fields, scalar types, geometry and payload decode.

Everything a reader must do explicitly is encoded here and covered by tests:
dimensions are validated before any allocation, the declared encoding decides the
interpretation (no magic-number sniffing), decoded length must match the declared
length exactly, and the payload must resolve inside the header directory.
"""

from __future__ import annotations

from ..progress import report
import gzip
import math
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import numpy as np

from ..budgets import MAX_DIMENSION, Budgets
from ..errors import InputError

# ----------------------------------------------------------- scalar types

# token -> (canonical scalar type, byte width, is float)
SCALAR_TYPES: dict[str, tuple[str, int, bool]] = {
    "uint8": ("uint8", 1, False),
    "uchar": ("uint8", 1, False),
    "unsigned char": ("uint8", 1, False),
    "byte": ("uint8", 1, False),
    "uint8_t": ("uint8", 1, False),
    "int8": ("int8", 1, False),
    "char": ("int8", 1, False),
    "signed char": ("int8", 1, False),
    "int8_t": ("int8", 1, False),
    "uint16": ("uint16", 2, False),
    "ushort": ("uint16", 2, False),
    "unsigned short": ("uint16", 2, False),
    "unsigned short int": ("uint16", 2, False),
    "uint16_t": ("uint16", 2, False),
    "int16": ("int16", 2, False),
    "short": ("int16", 2, False),
    "signed short": ("int16", 2, False),
    "signed short int": ("int16", 2, False),
    "int16_t": ("int16", 2, False),
    "uint32": ("uint32", 4, False),
    "uint": ("uint32", 4, False),
    "unsigned int": ("uint32", 4, False),
    "uint32_t": ("uint32", 4, False),
    "int32": ("int32", 4, False),
    "int": ("int32", 4, False),
    "signed int": ("int32", 4, False),
    "int32_t": ("int32", 4, False),
    "float": ("float32", 4, True),
    "float32": ("float32", 4, True),
    "double": ("float64", 8, True),
    "float64": ("float64", 8, True),
}

# Canonical scalar type -> NumPy kind/size code, so the byte order prefix is the
# only endianness-dependent part.
NUMPY_CODES = {
    "uint8": "u1",
    "int8": "i1",
    "uint16": "u2",
    "int16": "i2",
    "uint32": "u4",
    "int32": "i4",
    "float32": "f4",
    "float64": "f8",
}


def normalize_scalar_token(token: str) -> str:
    return " ".join(token.strip().lower().split())


def resolve_scalar_type(token: str, family: str) -> tuple[str, int, bool]:
    key = normalize_scalar_token(token)
    if key not in SCALAR_TYPES:
        raise InputError(
            "unsupported_scalar_type",
            f"{family} scalar type {token.strip()!r} is not supported in this milestone",
        )
    return SCALAR_TYPES[key]


def numpy_dtype(scalar_type: str, endianness: str) -> np.dtype:
    """Single-byte types have no byte order; the reported fact says so explicitly."""
    code = NUMPY_CODES.get(scalar_type)
    if code is None:
        raise InputError("unsupported_scalar_type", f"no NumPy mapping for {scalar_type}")
    if code in ("u1", "i1"):
        return np.dtype(code)
    prefix = "<" if endianness == "little" else ">"
    return np.dtype(f"{prefix}{code}")


# ------------------------------------------------------------ header fields

MAX_HEADER_BYTES = 1 << 20


def read_header_bytes(path: Path) -> bytes:
    with path.open("rb") as handle:
        raw = handle.read(MAX_HEADER_BYTES + 1)
    if len(raw) > MAX_HEADER_BYTES:
        raise InputError("header_too_large", "header exceeds the 1 MiB limit", stage="inspect")
    return raw


@dataclass
class ReaderDefaults:
    """Every applied reader default, item by item.

    A default must never be reported as a header declaration, so each entry is
    recorded with the reason it was applied and travels into the asset record.
    """

    entries: list[dict] = field(default_factory=list)

    def add(self, name: str, applied_value: str, reason: str) -> None:
        self.entries.append({"field": name, "appliedValue": applied_value, "reason": reason})

    def as_json(self) -> list[dict]:
        return list(self.entries)



@dataclass
class HeaderFields:
    """Case-insensitive single-value header map. A repeated field is an error."""

    values: dict[str, str] = field(default_factory=dict)
    source: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, text: str, family: str) -> "HeaderFields":
        fields = cls()
        for lineno, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                raise InputError(
                    "malformed_header",
                    f"{family} header line {lineno} has no 'key: value' separator",
                )
            key = line.split(":", 1)[0].strip().lower()
            value = line.split(":", 1)[1].strip()
            if not key:
                raise InputError("malformed_header", f"{family} header line {lineno} has an empty key")
            if key in fields.values:
                previous = fields.values[key]
                kind = "conflicting" if previous != value else "repeated"
                raise InputError(
                    "duplicate_header_field",
                    f"{family} header repeats field {key!r} ({kind}); a header field may appear once",
                )
            fields.values[key] = value
            fields.source[key] = f"{family}-header:line{lineno}"
        return fields

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def require(self, key: str, family: str) -> str:
        value = self.values.get(key)
        if value is None or value == "":
            raise InputError("missing_header_field", f"{family} header is missing required field {key!r}")
        return value


def parse_dimensions(raw: str, family: str) -> tuple[int, int, int]:
    parts = raw.split()
    if len(parts) != 3:
        raise InputError(
            "invalid_dimensions",
            f"{family} supports one 3D scalar volume; got {len(parts)} sizes: {raw!r}",
        )
    dims: list[int] = []
    for token in parts:
        if not re.fullmatch(r"\d+", token):
            raise InputError("invalid_dimensions", f"{family} size {token!r} is not a non-negative integer")
        value = int(token)
        if value <= 0:
            raise InputError("invalid_dimensions", f"{family} size {token!r} must be positive")
        if value > MAX_DIMENSION:
            raise InputError("invalid_dimensions", f"{family} size {value} exceeds the contract maximum {MAX_DIMENSION}")
        dims.append(value)
    return dims[0], dims[1], dims[2]


def parse_number_vector3(raw: str, family: str, field_name: str) -> tuple[float, float, float]:
    inner = raw.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    parts = [token for token in re.split(r"[,\s]+", inner.strip()) if token]
    if len(parts) != 3:
        raise InputError("invalid_geometry", f"{family} {field_name} must have three components: {raw!r}")
    values: list[float] = []
    for token in parts:
        try:
            value = float(token)
        except ValueError as exc:
            raise InputError("invalid_geometry", f"{family} {field_name} component {token!r} is not a number") from exc
        if not math.isfinite(value):
            raise InputError("invalid_geometry", f"{family} {field_name} component {token!r} is not finite")
        values.append(value)
    return values[0], values[1], values[2]


def parse_positive_number_vector3(raw: str, family: str, field_name: str) -> tuple[float, float, float]:
    values = parse_number_vector3(raw, family, field_name)
    if any(value <= 0 for value in values):
        raise InputError("invalid_geometry", f"{family} {field_name} must be positive: {raw!r}")
    return values


def parse_direction_vectors(raw: str, family: str) -> tuple[tuple[float, float, float], ...]:
    vectors = re.findall(r"\(([^()]*)\)", raw)
    if len(vectors) != 3 or re.sub(r"\([^()]*\)", "", raw).strip():
        raise InputError(
            "invalid_geometry",
            f"{family} space directions must contain exactly three vectors: {raw!r}",
        )
    return tuple(parse_number_vector3(f"({inner})", family, "space directions") for inner in vectors)


def check_skip_field(fields: HeaderFields, key: str, family: str) -> None:
    """Only the absent or zero value is supported. Any other skip is refused."""
    raw = fields.get(key)
    if raw is None:
        return
    token = raw.strip().lower()
    if token not in ("0", "none", "no"):
        raise InputError(
            "unsupported_data_skip",
            f"{family} {key}: {raw!r} is not supported; only the default or zero is supported",
        )


# -------------------------------------------------------------- unit table

# Technical unit allowlist. An unlisted token stays unknown; nothing is inferred.
UNIT_TABLE: dict[str, tuple[str, str]] = {
    "mm": ("millimetre", "length"),
    "millimeter": ("millimetre", "length"),
    "millimeters": ("millimetre", "length"),
    "millimetre": ("millimetre", "length"),
    "millimetres": ("millimetre", "length"),
    "cm": ("centimetre", "length"),
    "centimeter": ("centimetre", "length"),
    "centimeters": ("centimetre", "length"),
    "m": ("metre", "length"),
    "meter": ("metre", "length"),
    "meters": ("metre", "length"),
    "metre": ("metre", "length"),
    "metres": ("metre", "length"),
    "um": ("micrometre", "length"),
    "µm": ("micrometre", "length"),
    "micrometer": ("micrometre", "length"),
    "micrometers": ("micrometre", "length"),
    "micrometre": ("micrometre", "length"),
    "micrometres": ("micrometre", "length"),
    "voxel": ("voxel", "none"),
    "voxels": ("voxel", "none"),
    "pixel": ("voxel", "none"),
    "pixels": ("voxel", "none"),
    "none": ("none", "none"),
    "unitless": ("none", "none"),
    "1": ("none", "none"),
    "ratio": ("ratio", "ratio"),
    "count": ("count", "count"),
    "counts": ("count", "count"),
}


def interpret_unit(raw: str | None) -> tuple[str | None, str | None, str | None]:
    """Map a declared unit token to the technical allowlist.

    Returns ``(measurement_unit, dimension, declared_symbol)`` or a reason code
    when the token cannot be interpreted. An unknown unit is never millimetre.
    """
    if raw is None:
        return None, None, "not_declared_in_source"
    token = raw.strip()
    if not token:
        return None, None, "not_declared_in_source"
    if token in UNIT_TABLE:
        unit, dimension = UNIT_TABLE[token]
        return unit, dimension, token
    lowered = token.lower()
    if lowered in UNIT_TABLE:
        unit, dimension = UNIT_TABLE[lowered]
        return unit, dimension, lowered
    return None, None, "uninterpretable_unit_symbol"


# ---------------------------------------------------------------- geometry

ORTHOGONALITY_METHOD_VERSION = "volume-context-geometry-1"
ORTHOGONALITY_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Geometry:
    """Declared storage geometry. No anatomical or named-view interpretation."""

    directions: tuple[tuple[float, float, float], ...] | None
    directions_declared_by: str | None
    origin: tuple[float, float, float] | None
    origin_declared_by: str | None
    spacing: tuple[float, float, float] | None
    spacing_declared_by: str | None
    max_abs_normalized_dot: float | None


def diagonal_basis(spacing: tuple[float, float, float]) -> tuple[tuple[float, float, float], ...]:
    return (
        (spacing[0], 0.0, 0.0),
        (0.0, spacing[1], 0.0),
        (0.0, 0.0, spacing[2]),
    )


def max_abs_normalized_dot(directions: tuple[tuple[float, float, float], ...]) -> float:
    """Largest |cos| between direction pairs; zero means orthogonal."""
    norms = [math.hypot(*vector) for vector in directions]
    worst = 0.0
    for i in range(3):
        for j in range(i + 1, 3):
            cosine = abs(sum((directions[i][k] / norms[i]) * (directions[j][k] / norms[j]) for k in range(3)))
            worst = max(worst, cosine)
    return worst


def validate_basis(
    directions: tuple[tuple[float, float, float], ...], family: str
) -> float:
    norms = [math.hypot(*vector) for vector in directions]
    for index, norm in enumerate(norms):
        if not math.isfinite(norm) or norm <= 0:
            raise InputError(
                "degenerate_space_basis",
                f"{family} space direction {index} is a zero vector",
            )
    worst = max_abs_normalized_dot(directions)
    if worst > ORTHOGONALITY_TOLERANCE:
        raise InputError(
            "sheared_space_basis",
            f"{family} space directions are not orthogonal within {ORTHOGONALITY_TOLERANCE:g} "
            f"(max |cos| = {worst:g}); a sheared grid is not supported in this milestone",
        )
    return worst


def spacing_from_basis(directions: tuple[tuple[float, float, float], ...]) -> tuple[float, float, float]:
    return tuple(math.hypot(*vector) for vector in directions)  # type: ignore[return-value]


# ------------------------------------------------------- payload resolution


def resolve_sibling_path(header_path: Path, declared: str, family: str) -> Path:
    """Resolve a declared sibling payload inside the header directory.

    Absolute paths, ``..`` traversal and symlink escapes are refused. Filenames
    with spaces and Unicode are preserved verbatim.
    """
    name = declared.strip()
    if not name:
        raise InputError("missing_data_file", f"{family} header has an empty payload reference")
    candidate = Path(name)
    if candidate.is_absolute() or (len(name) > 1 and name[1] == ":"):
        raise InputError(
            "payload_path_not_relative",
            f"{family} payload reference must be relative to the header directory",
        )
    if any(part == ".." for part in candidate.parts):
        raise InputError(
            "payload_path_escapes",
            f"{family} payload reference escapes the header directory",
        )
    base = header_path.parent
    try:
        base_real = base.resolve(strict=True)
    except OSError as exc:
        raise InputError("input_unreadable", f"cannot resolve the header directory: {exc.strerror or exc}") from exc
    resolved = (base / candidate).resolve()
    try:
        relative = resolved.relative_to(base_real)
    except ValueError as exc:
        raise InputError(
            "payload_path_escapes",
            f"{family} payload resolves outside the header directory (symlink or traversal)",
        ) from exc
    if not relative.parts:
        raise InputError("payload_path_escapes", f"{family} payload reference resolves to the header directory")
    if not resolved.exists():
        raise InputError("payload_missing", f"{family} payload not found: {name}")
    if not resolved.is_file():
        raise InputError("payload_not_a_file", f"{family} payload is not a regular file: {name}")
    return resolved


# ---------------------------------------------------------- payload decode


@dataclass
class DecodedPayload:
    """A decoded payload on local disk, with the declared length it must match."""

    path: Path
    byte_length: int
    staging_copy: bool

    def iter_chunks(self, item_bytes: int, chunk_bytes: int) -> Iterator[np.ndarray]:
        if item_bytes <= 0:
            raise InputError("invalid_scalar_type", "a scalar type needs a positive byte width")
        per_chunk = max(1, chunk_bytes // item_bytes)
        with self.path.open("rb") as handle:
            while True:
                block = handle.read(per_chunk * item_bytes)
                if not block:
                    return
                if len(block) % item_bytes != 0:
                    raise InputError(
                        "payload_short",
                        f"payload ends mid-voxel ({len(block) % item_bytes} trailing bytes)",
                    )
                yield np.frombuffer(block, dtype=np.uint8)


def open_raw_payload(path: Path, expected_bytes: int) -> DecodedPayload:
    """Raw encoding: the stored length must equal the declared length exactly."""
    actual = path.stat().st_size
    if actual != expected_bytes:
        relation = "shorter than" if actual < expected_bytes else "longer than"
        raise InputError(
            "payload_length_mismatch",
            f"payload is {actual} bytes, {relation} the declared {expected_bytes} bytes",
        )
    return DecodedPayload(path=path, byte_length=actual, staging_copy=False)


def decode_inline_raw_payload(
    path: Path, expected_bytes: int, offset: int, staging_dir: Path, budgets: Budgets
) -> DecodedPayload:
    """Extract only voxels, so all downstream hashes and statistics exclude the header."""
    actual = path.stat().st_size - offset
    if actual != expected_bytes:
        raise InputError("payload_length_mismatch", f"binary DAT payload is {actual} bytes; expected {expected_bytes}")
    budgets.check_decoded_size(expected_bytes, "binary DAT payload")
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged = staging_dir / "decoded-payload.bin"
    written = 0
    with path.open("rb") as source, staged.open("xb") as sink:
        source.seek(offset)
        while written < expected_bytes:
            block = source.read(min(budgets.chunk_bytes, expected_bytes - written))
            if not block:
                raise InputError("payload_length_mismatch", "binary DAT payload was truncated during reading")
            sink.write(block)
            written += len(block)
            report("reading", "Reading binary DAT payload", written, expected_bytes, "bytes")
        if source.read(1):
            raise InputError("payload_length_mismatch", "binary DAT payload grew during reading")
    return DecodedPayload(path=staged, byte_length=written, staging_copy=True)


def decode_gzip_payload(
    path: Path, expected_bytes: int, staging_dir: Path, budgets: Budgets
) -> DecodedPayload:
    """Stream-decode gzip into bounded staging.

    A whole-file `gzip.decompress` is not used: the decoded size is bounded while
    streaming, truncation is detected because the stream ends early, and the CRC
    is checked because the gzip reader verifies it at end of stream.
    """
    budgets.check_decoded_size(expected_bytes, "declared payload")
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged = staging_dir / "decoded-payload.bin"
    written = 0
    limit = expected_bytes + 1
    try:
        with path.open("rb") as source, staged.open("xb") as sink:
            with gzip.GzipFile(fileobj=source, mode="rb") as stream:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    written += len(block)
                    if written > limit:
                        raise InputError(
                            "payload_length_mismatch",
                            f"gzip payload decodes to more than the declared {expected_bytes} bytes",
                        )
                    budgets.check_decoded_size(written, "gzip payload")
                    sink.write(block)
                    report("reading", "Decoding gzip payload", written, expected_bytes, "bytes")
    except gzip.BadGzipFile as exc:
        raise InputError("payload_gzip_corrupt", f"gzip payload is corrupt: {exc}") from exc
    except EOFError as exc:
        raise InputError("payload_gzip_truncated", f"gzip payload is truncated: {exc}") from exc
    except zlib.error as exc:
        raise InputError("payload_gzip_corrupt", f"gzip payload is corrupt: {exc}") from exc
    if written != expected_bytes:
        relation = "shorter than" if written < expected_bytes else "longer than"
        raise InputError(
            "payload_length_mismatch",
            f"gzip payload decodes to {written} bytes, {relation} the declared {expected_bytes} bytes",
        )
    return DecodedPayload(path=staged, byte_length=written, staging_copy=True)


def discard_staging_copy(payload: DecodedPayload) -> None:
    """Remove a decoded staging copy (and its directory) once its bytes are unneeded."""
    if not payload.staging_copy:
        return
    if payload.path.exists():
        try:
            payload.path.unlink()
        except OSError:
            pass
    parent = payload.path.parent
    try:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass
