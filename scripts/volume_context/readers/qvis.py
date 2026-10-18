"""QVis text `.dat` reader.

Declared fields decide everything. The renderer's "any Format that is not
8-bit is 16-bit" fallback is deliberately not inherited: an unrecognised `Format`
is an explicit error, because silently changing the byte width of a volume is
exactly the kind of silent reinterpretation this milestone refuses.

Reference for the accepted tags: VirtualSampling/Raycaster QVis.cpp `load`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..errors import InputError
from .common import (
    Geometry,
    HeaderFields,
    read_header_bytes,
    ReaderDefaults,
    diagonal_basis,
    parse_dimensions,
    parse_number_vector3,
    resolve_sibling_path,
)

READER_FAMILY = "qvis"

# Explicit unsigned formats only. Anything else is unsupported, never guessed.
FORMAT_UINT8 = {"uchar", "byte", "uint8"}
FORMAT_UINT16 = {"ushort", "uint16"}


@dataclass
class QvisHeader:
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
            f"{path.name} is not a QVis text header (this reader does not accept TU Wien binary .dat)",
            stage="inspect",
        ) from exc


def read_qvis_header(path: Path) -> QvisHeader:
    text = _read_text(path)
    if "\x00" in text or "objectfilename" not in text.lower():
        raise InputError(
            "malformed_header",
            f"{path.name} is not a QVis text header",
            stage="inspect",
        )
    fields = HeaderFields.parse(text, READER_FAMILY)
    defaults = ReaderDefaults()

    # The whole value is the payload name. A QVis header names exactly one file,
    # so spaces and non-ASCII characters are part of the name, not separators.
    # (NHDR is the format that lists several files in one field.)
    object_name = fields.require("objectfilename", READER_FAMILY)
    dims = parse_dimensions(fields.require("resolution", READER_FAMILY), READER_FAMILY)

    format_token = fields.require("format", READER_FAMILY)
    lowered = format_token.strip().lower()
    if lowered in FORMAT_UINT8:
        scalar_type = "uint8"
    elif lowered in FORMAT_UINT16:
        scalar_type = "uint16"
    else:
        raise InputError(
            "unsupported_qvis_format",
            f"QVis Format {format_token!r} is not an explicitly supported unsigned format "
            f"(accepted: {sorted(FORMAT_UINT8 | FORMAT_UINT16)})",
            stage="inspect",
        )

    endian_token = fields.get("endianess")
    if endian_token is None:
        endianness = "little"
        defaults.add("Endianess", "little", "renderer-limitation")
    else:
        lowered_endian = endian_token.strip().lower()
        if lowered_endian != "little":
            raise InputError(
                "unsupported_qvis_endian",
                f"QVis Endianess {endian_token!r} is not supported; the canonical renderer reads little endian only",
                stage="inspect",
            )
        endianness = "little"

    compression = fields.get("compression")
    if compression is None:
        encoding = "raw"
        defaults.add("Compression", "none", "format-spec-default")
    elif compression.strip().lower() in ("", "none"):
        encoding = "raw"
    elif compression.strip().lower() == "lz4":
        raise InputError(
            "unsupported_qvis_compression",
            "QVis lz4 payloads are not supported in this milestone",
            stage="inspect",
        )
    else:
        raise InputError(
            "unsupported_qvis_compression",
            f"QVis Compression {compression!r} is not supported",
            stage="inspect",
        )

    slice_thickness = fields.get("slicethickness")
    if slice_thickness is None:
        spacing: tuple[float, float, float] | None = None
        spacing_declared_by = None
        defaults.add("SliceThickness", "1 1 1", "renderer-limitation")
    else:
        spacing = parse_number_vector3(slice_thickness, READER_FAMILY, "SliceThickness")
        if any(value <= 0 for value in spacing):
            raise InputError(
                "invalid_geometry",
                f"QVis SliceThickness must be positive: {slice_thickness!r}",
                stage="inspect",
            )
        spacing_declared_by = "qvis-slice-thickness"

    payload_path = resolve_sibling_path(path, object_name, READER_FAMILY)

    geometry = Geometry(
        # QVis declares three direction magnitudes only. The diagonal basis below
        # is that declaration, not an inferred world orientation.
        directions=diagonal_basis(spacing) if spacing else None,
        directions_declared_by="qvis-slice-thickness" if spacing else None,
        origin=None,
        origin_declared_by=None,
        spacing=spacing,
        spacing_declared_by=spacing_declared_by,
        max_abs_normalized_dot=0.0 if spacing else None,
    )

    return QvisHeader(
        path=path,
        payload_path=payload_path,
        dimensions=dims,
        scalar_type=scalar_type,
        endianness=endianness,
        encoding=encoding,
        geometry=geometry,
        unit_token=None,
        defaults=defaults,
    )
