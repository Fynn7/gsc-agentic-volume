"""Reader dispatch.

QVis text headers and TU Wien binary DAT volumes are distinguished by content.
Detached NHDR requires the NRRD magic line.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from ..errors import InputError
from .common import (
    DecodedPayload,
    Geometry,
    decode_gzip_payload,
    numpy_dtype,
    open_raw_payload,
    decode_inline_raw_payload,
)
from .nhdr import NhdrHeader, read_nhdr_header
from .qvis import QvisHeader, read_qvis_header
from .tuwien import read_tuwien_header

MAX_HEADER_PROBE_BYTES = 4096


@dataclass
class SourceHeader:
    """One reader's interpretation of an input, independent of file extension."""

    family: str
    path: Path
    payload_path: Path
    dimensions: tuple[int, int, int]
    scalar_type: str
    endianness: str
    encoding: str
    geometry: Geometry
    unit_token: str | None
    defaults: list[dict]
    payload_offset: int = 0

    @property
    def voxel_count(self) -> int:
        width, height, depth = self.dimensions
        return width * height * depth

    @property
    def item_bytes(self) -> int:
        return np.dtype(numpy_dtype(self.scalar_type, self.endianness)).itemsize

    @property
    def expected_decoded_byte_length(self) -> int:
        return self.voxel_count * self.item_bytes

    def open_payload(self, staging_dir: Path, budgets) -> DecodedPayload:
        expected = self.expected_decoded_byte_length
        budgets.check_decoded_size(expected, "declared payload")
        if self.encoding == "raw":
            if self.payload_offset:
                return decode_inline_raw_payload(self.payload_path, expected, self.payload_offset, staging_dir, budgets)
            return open_raw_payload(self.payload_path, expected)
        if self.encoding == "gzip":
            return decode_gzip_payload(self.payload_path, expected, staging_dir, budgets)
        raise InputError("unsupported_encoding", f"encoding {self.encoding!r} is not supported")


def _looks_like_nhdr(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(MAX_HEADER_PROBE_BYTES)
    except OSError as exc:
        raise InputError("input_unreadable", f"cannot read {path}: {exc.strerror or exc}", stage="inspect") from exc
    if head[:2] == b"PK":
        return False
    return head.lstrip()[:4] == b"NRRD"


def _looks_like_qvis(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(MAX_HEADER_PROBE_BYTES)
    except OSError as exc:
        raise InputError("input_unreadable", f"cannot read {path}: {exc.strerror or exc}", stage="inspect") from exc
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    lowered = text.lower()
    return "objectfilename" in lowered and "resolution" in lowered


def read_source_header(path: Path) -> SourceHeader:
    """Identify and parse a supported input header, or refuse it explicitly."""
    if not path.exists():
        raise InputError("input_not_found", f"input not found: {path}", stage="inspect")
    if not path.is_file():
        raise InputError("input_not_a_file", f"input is not a regular file: {path}", stage="inspect")

    if _looks_like_nhdr(path):
        header = read_nhdr_header(path)
        return _to_source(header, "nhdr")
    if _looks_like_qvis(path):
        header = read_qvis_header(path)
        return _to_source(header, "qvis")

    # Content sniffing found neither header. Route by suffix so the caller gets
    # the specific reason instead of a generic "unsupported input".
    suffix = path.suffix.lower()
    if suffix in (".nhdr", ".nrrd"):
        header = read_nhdr_header(path)
        return _to_source(header, "nhdr")
    if suffix == ".dat":
        with path.open("rb") as handle:
            prefix = handle.read(6)
        if any(byte < 32 and byte not in (9, 10, 13) for byte in prefix):
            return _to_source(read_tuwien_header(path), "tu-wien-dat")
        header = read_qvis_header(path)
        return _to_source(header, "qvis")
    raise InputError(
        "unsupported_input",
        f"{path.name} is neither a QVis .dat header nor a detached NHDR header; "
        "supported families: QVis text .dat, TU Wien binary .dat, NHDR",
        stage="inspect",
    )


def _to_source(header, family: str) -> SourceHeader:  # type: ignore[no-untyped-def]
    return SourceHeader(
        family=family,
        path=header.path,
        payload_path=header.payload_path,
        dimensions=header.dimensions,
        scalar_type=header.scalar_type,
        endianness=header.endianness,
        encoding=header.encoding,
        geometry=header.geometry,
        unit_token=header.unit_token,
        defaults=header.defaults.as_json(),
        payload_offset=getattr(header, "payload_offset", 0),
    )


__all__ = ["SourceHeader", "read_source_header"]
