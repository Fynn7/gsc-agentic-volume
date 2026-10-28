"""Materialize a built-in preview dataset into a runnable volume file.

The Wasm preview embeds five datasets from ``VirtualSampling/Raycaster/Datasets``
as QVis headers with **custom-frame LZ4** payloads (``LZ4F`` + u64 original size
+ an LZ4 block stream, see ``VirtualSampling/Utils/Compression.cpp``). The Python
import chain deliberately refuses lz4 sources, so those datasets could be shown
in the preview but never handed to the agent renderer.

This script decodes one such dataset once and writes a plain QVis header plus a
raw uint8 payload into ``volumes/``, where the normal import path picks it up:

    python scripts/materialize_dataset.py VirtualSampling/Raycaster/Datasets/head512.dat

Output: ``volumes/head512_<w>x<h>x<d>_uint8.dat`` + ``.raw`` (gitignored).
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "volumes"
MAGIC = b"LZ4F"
MIN_MATCH = 4


def decode_frame(data: bytes) -> bytes:
    """Decode the project's LZ4 frame: magic, u64 size, then an LZ4 block stream."""
    if len(data) < 12 or data[:4] != MAGIC:
        raise ValueError("not an LZ4F frame (bad magic or truncated header)")
    expected = struct.unpack_from("<Q", data, 4)[0]
    src = memoryview(data)
    ip = 12
    end = len(src)
    out = bytearray()
    while ip < end:
        token = src[ip]
        ip += 1
        literals = token >> 4
        if literals == 15:
            while True:
                if ip >= end:
                    raise ValueError("truncated literal length")
                extra = src[ip]
                ip += 1
                literals += extra
                if extra != 255:
                    break
        if literals:
            if ip + literals > end:
                raise ValueError("truncated literals")
            out += src[ip:ip + literals]
            ip += literals
        if ip >= end:
            break
        if ip + 2 > end:
            raise ValueError("truncated match offset")
        offset = src[ip] | (src[ip + 1] << 8)
        ip += 2
        if offset == 0 or offset > len(out):
            raise ValueError("invalid match offset")
        length = token & 0x0F
        if length == 15:
            while True:
                if ip >= end:
                    raise ValueError("truncated match length")
                extra = src[ip]
                ip += 1
                length += extra
                if extra != 255:
                    break
        length += MIN_MATCH
        start = len(out) - offset
        if offset >= length:
            out += out[start:start + length]
        else:
            # Overlapping copy: the match source advances with the output.
            for _ in range(length):
                out.append(out[start])
                start += 1
    if len(out) != expected:
        raise ValueError(f"decoded {len(out)} bytes, frame declares {expected}")
    return bytes(out)


def read_header(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip().lower()] = value.strip()
    return fields


def materialize(source: Path) -> Path:
    fields = read_header(source)
    for required in ("objectfilename", "resolution", "format"):
        if required not in fields:
            raise ValueError(f"{source.name} is missing {required!r}")
    if fields.get("format", "").strip().lower() not in ("uchar", "uint8", "byte"):
        raise ValueError(f"{source.name}: only uint8 datasets are materialized, got {fields['format']!r}")
    if fields.get("compression", "none").strip().lower() not in ("lz4", "none", ""):
        raise ValueError(f"{source.name}: unsupported compression {fields['compression']!r}")
    dims = [int(part) for part in fields["resolution"].replace(",", " ").split()]
    if len(dims) != 3:
        raise ValueError(f"{source.name}: expected three dimensions, got {dims}")
    payload_path = source.with_name(fields["objectfilename"])
    voxels = dims[0] * dims[1] * dims[2]
    payload = payload_path.read_bytes()
    if fields.get("compression", "none").strip().lower() == "lz4":
        payload = decode_frame(payload)
    if len(payload) != voxels:
        raise ValueError(f"{source.name}: payload is {len(payload)} bytes, expected {voxels}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{source.stem}_{dims[0]}x{dims[1]}x{dims[2]}_uint8"
    raw_path = OUT_DIR / f"{stem}.raw"
    dat_path = OUT_DIR / f"{stem}.dat"
    raw_path.write_bytes(payload)
    spacing = fields.get("slicethickness", "1 1 1")
    dat_path.write_text(
        "ObjectFileName: {raw}\nResolution: {dims}\nSliceThickness: {spacing}\n"
        "Format: uchar\nEndianess: little\n".format(
            raw=raw_path.name, dims=" ".join(str(value) for value in dims), spacing=spacing,
        ),
        encoding="utf-8",
    )
    return dat_path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[0])
        print("usage: python scripts/materialize_dataset.py <dataset.dat>")
        return 2
    source = Path(argv[1])
    if not source.is_absolute():
        source = (ROOT / source).resolve()
    if not source.is_file():
        print(f"missing dataset header: {source}")
        return 2
    if not (source.parent / read_header(source)["objectfilename"]).is_file():
        print(f"missing payload beside {source}")
        return 2
    out = materialize(source)
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
