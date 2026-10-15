"""NHDR / detached NRRD reader (M1a subset).

Format reference: the NRRD format specification (Teem):
https://teem.sourceforge.net/nrrd/format.html

Supported subset, explicitly:
  * detached headers only, one payload file (`data file: <name>`)
  * one 3D scalar volume; `encoding: raw` or `encoding: gzip`
  * `type` of 8/16/32-bit signed and unsigned integers, float and double
  * `endian` declared explicitly for every multi-byte type
  * `space directions` with any orthogonal orientation, `space origin`,
    `space units`; `byte skip`/`line skip` only when absent or zero

Everything else is refused with a specific code: inline data, `LIST`, a
multi-file payload, ASCII/hex/ascii encoding, non-scalar kinds, vector spaces,
repeated or conflicting fields, and any missing byte-order declaration.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import InputError
from .common import (
    Geometry,
    HeaderFields,
    MAX_HEADER_BYTES,
    read_header_bytes,
    ReaderDefaults,
    check_skip_field,
    parse_dimensions,
    parse_direction_vectors,
    parse_number_vector3,
    parse_positive_number_vector3,
    resolve_scalar_type,
    resolve_sibling_path,
    validate_basis,
    spacing_from_basis,
    interpret_unit,
)

READER_FAMILY = "nhdr"
MAGIC_RE = re.compile(r"NRRD000[1-5]")

SUPPORTED_ENCODINGS = {"raw", "gzip"}
UNSUPPORTED_ENCODINGS = {
    "ascii": "ascii text encoding",
    "text": "ASCII text encoding",
    "hex": "hexadecimal encoding",
    "base64": "base64 encoding",
    "bz2": "bzip2 encoding",
    "bzip2": "bzip2 encoding",
    "lzma": "lzma encoding",
    "xz": "lzma encoding",
}


@dataclass
class NhdrHeader:
    path: Path
    payload_path: Path
    dimensions: tuple[int, int, int]
    scalar_type: str
    endianness: str
    encoding: str
    geometry: Geometry
    unit_token: str | None = None
    defaults: ReaderDefaults = field(default_factory=ReaderDefaults)

    @property
    def voxel_count(self) -> int:
        width, height, depth = self.dimensions
        return width * height * depth


def _read_text(path: Path) -> str:
    try:
        raw = read_header_bytes(path)
    except OSError as exc:
        raise InputError("input_unreadable", f"cannot read {path.name}: {exc.strerror or exc}", stage="inspect") from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError(
            "malformed_header",
            f"{path.name} is not a detached NRRD text header",
            stage="inspect",
        ) from exc


def _check_magic(text: str, path: Path) -> None:
    lines = [line.strip() for line in text.splitlines()]
    if not lines or not MAGIC_RE.fullmatch(lines[0] or ""):
        raise InputError(
            "missing_nrrd_magic",
            f"{path.name} does not start with an NRRD magic line; a detached NRRD header must",
            stage="inspect",
        )


def _reject_inline_and_lists(fields: HeaderFields) -> str:
    """Return the declared payload reference, refusing inline and `LIST` layouts.

    A whitespace-separated list of payload files is refused, but a *single* file
    name containing spaces is not: the NRRD field is ambiguous in that case, so
    the reader resolves the whole value as one name first and only reports the
    list layout when that resolution fails.
    """
    data_file = fields.get("data file")
    if data_file is None:
        raise InputError(
            "unsupported_data_layout",
            "this reader accepts detached NHDR headers with a `data file` reference only; "
            "inline .nrrd data is out of scope for this milestone",
            stage="inspect",
        )
    stripped = data_file.strip()
    if stripped.upper() == "LIST":
        raise InputError(
            "unsupported_data_layout",
            "NHDR `data file: LIST` describes a multi-file payload, which is not supported in this milestone",
            stage="inspect",
        )
    if not stripped:
        raise InputError(
            "unsupported_data_layout",
            "NHDR `data file` is empty",
            stage="inspect",
        )
    return stripped


def _check_scalar_layout(fields: HeaderFields) -> None:
    fields.require("dimension", READER_FAMILY)
    for key, expected in (("dimension", "3"), ("space dimension", "3")):
        raw = fields.get(key)
        if raw is None:
            continue
        if raw.strip() != expected:
            raise InputError(
                "unsupported_dimensions",
                f"NHDR {key}: {raw!r} is not supported; this reader handles one 3D scalar volume",
                stage="inspect",
            )
    kinds = fields.get("kinds")
    if kinds is not None:
        tokens = kinds.split()
        if len(tokens) != 3 or any(token not in ("domain", "space") for token in tokens):
            raise InputError(
                "unsupported_component_layout",
                f"NHDR kinds: {kinds!r} is not a scalar volume; channels and vectors are out of scope for this milestone",
                stage="inspect",
            )


def _check_endian(fields: HeaderFields, item_bytes: int, scalar_type: str) -> str:
    raw = fields.get("endian")
    if raw is None:
        if item_bytes == 1:
            return "not-applicable"
        raise InputError(
            "ambiguous_endianness",
            f"NHDR type {scalar_type} is multi-byte but no `endian` field is declared; "
            "the NRRD default (writer native order) is ambiguous here",
            stage="inspect",
        )
    lowered = raw.strip().lower()
    if lowered == "little":
        return "little"
    if lowered == "big":
        return "big"
    raise InputError(
        "unsupported_endianness",
        f"NHDR endian: {raw!r} is not `little` or `big`",
        stage="inspect",
    )


def _unit_token(fields: HeaderFields) -> str | None:
    """Read three equal world-axis units, or the single-token shorthand.

    The `space` name is not a unit declaration or a semantic orientation claim.
    Mixed units require normalization and are outside this milestone.
    """
    raw_units = fields.get("space units")
    if raw_units is None:
        return None
    try:
        tokens = shlex.split(raw_units)
    except ValueError as exc:
        raise InputError("invalid_geometry", "invalid space units quoting", stage="inspect") from exc
    # A single token is retained as a compatibility shorthand. The standard
    # spelling has one quoted unit per world-space axis, not a space name.
    if len(tokens) == 1:
        return tokens[0]
    if len(tokens) != 3:
        raise InputError("unsupported_space_units", "space units requires three axis units", stage="inspect")
    normalized = [interpret_unit(token)[0] for token in tokens]
    if len(set(normalized)) > 1 or (normalized[0] is None and len(set(tokens)) > 1):
        raise InputError("unsupported_space_units", "mixed axis units require geometry normalization", stage="inspect")
    return tokens[0]


def _resolve_payload(header_path: Path, declared: str) -> Path:
    """Resolve the payload reference, disambiguating a name that contains spaces.

    The whole value is tried as one file name first. Only when that fails and the
    value really does hold several tokens is it reported as a multi-file payload
    layout, which this milestone does not support.
    """
    try:
        return resolve_sibling_path(header_path, declared, READER_FAMILY)
    except InputError as first_error:
        if len(declared.split()) <= 1:
            raise
        raise InputError(
            "unsupported_data_layout",
            f"NHDR `data file` names more than one payload ({declared!r}); "
            "a multi-file payload is not supported in this milestone "
            f"(single-name resolution failed: {first_error.code})",
            stage="inspect",
        ) from first_error


def read_nhdr_header(path: Path) -> NhdrHeader:
    text = _read_text(path)
    _check_magic(text, path)
    body = "\n".join(text.splitlines()[1:])
    fields = HeaderFields.parse(body, READER_FAMILY)
    defaults = ReaderDefaults()

    declared_payload = _reject_inline_and_lists(fields)
    _check_scalar_layout(fields)
    check_skip_field(fields, "byte skip", READER_FAMILY)
    check_skip_field(fields, "line skip", READER_FAMILY)

    scalar_type, item_bytes, _is_float = resolve_scalar_type(
        fields.require("type", READER_FAMILY), READER_FAMILY
    )
    endianness = _check_endian(fields, item_bytes, scalar_type)

    dims = parse_dimensions(fields.require("sizes", READER_FAMILY), READER_FAMILY)

    encoding_raw = fields.require("encoding", READER_FAMILY)
    lowered = encoding_raw.strip().lower()
    if lowered in UNSUPPORTED_ENCODINGS:
        raise InputError(
            "unsupported_encoding",
            f"NHDR encoding {encoding_raw!r} ({UNSUPPORTED_ENCODINGS[lowered]}) is not supported in this milestone",
            stage="inspect",
        )
    if lowered not in SUPPORTED_ENCODINGS:
        raise InputError(
            "unsupported_encoding",
            f"NHDR encoding {encoding_raw!r} is not supported; accepted: raw, gzip",
            stage="inspect",
        )
    encoding = lowered

    raw_directions = fields.get("space directions")
    raw_origin = fields.get("space origin")
    directions = None
    directions_declared_by = None
    origin = None
    origin_declared_by = None
    spacing = None
    spacing_declared_by = None
    max_dot = None

    if raw_directions is not None:
        vectors = parse_direction_vectors(raw_directions, READER_FAMILY)
        max_dot = validate_basis(vectors, READER_FAMILY)
        directions = vectors
        directions_declared_by = "source-header-declared"
        spacing = spacing_from_basis(vectors)
        spacing_declared_by = "source-header-derived"
    elif fields.get("spacings") is not None:
        spacing = parse_positive_number_vector3(
            fields.get("spacings") or "", READER_FAMILY, "spacings"
        )
        spacing_declared_by = "source-header-declared"
        directions = None
        directions_declared_by = None
        defaults.add("spaceBasis", "absent; spacing declared without direction vectors", "no-headless-equivalent")
    else:
        spacing = None
        spacing_declared_by = None
        defaults.add("spacing", "absent; left unknown", "no-headless-equivalent")

    if raw_origin is not None:
        origin = parse_number_vector3(raw_origin, READER_FAMILY, "space origin")
        origin_declared_by = "source-header-declared"
    else:
        origin = None
        origin_declared_by = None
        defaults.add("space origin", "absent; left unknown", "no-headless-equivalent")

    payload_path = _resolve_payload(path, declared_payload)

    geometry = Geometry(
        directions=directions,
        directions_declared_by=directions_declared_by,
        origin=origin,
        origin_declared_by=origin_declared_by,
        spacing=spacing,
        spacing_declared_by=spacing_declared_by,
        max_abs_normalized_dot=max_dot,
    )

    return NhdrHeader(
        path=path,
        payload_path=payload_path,
        dimensions=dims,
        scalar_type=scalar_type,
        endianness=endianness,
        encoding=encoding,
        geometry=geometry,
        unit_token=_unit_token(fields),
        defaults=defaults,
    )
