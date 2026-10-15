"""TU Wien binary DAT: three little-endian uint16 dimensions, then uint16 voxels.

The format has no spacing, origin or orientation metadata. Do not infer these
from the filename or from the canonical renderer's defaults.
"""

import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import InputError
from .common import Geometry, ReaderDefaults, parse_dimensions


@dataclass
class TuWienHeader:
    path: Path
    payload_path: Path
    dimensions: tuple[int, int, int]
    scalar_type: str
    endianness: str
    encoding: str
    geometry: Geometry
    unit_token: str | None = None
    defaults: ReaderDefaults = field(default_factory=ReaderDefaults)
    payload_offset: int = 6


def read_tuwien_header(path: Path) -> TuWienHeader:
    with path.open("rb") as handle:
        prefix = handle.read(6)
    if len(prefix) != 6:
        raise InputError("malformed_header", "binary DAT needs a 6-byte dimension header", stage="inspect")
    dimensions = parse_dimensions(" ".join(map(str, struct.unpack("<3H", prefix))), "tu-wien-dat")
    defaults = ReaderDefaults()
    defaults.add("PayloadOffsetBytes", "6", "format-spec-default")
    defaults.add("ScalarType", "uint16", "format-spec-default")
    defaults.add("Endianess", "little", "format-spec-default")
    return TuWienHeader(
        path=path, payload_path=path, dimensions=dimensions,
        scalar_type="uint16", endianness="little", encoding="raw",
        geometry=Geometry(None, None, None, None, None, None, None), defaults=defaults,
    )
