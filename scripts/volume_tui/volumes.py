"""Discover volume headers from the project's fixed volume folder (read-only).

The TUI does not hard-wire one dataset: every header that the launcher can hand
to ``prepare`` inside ``volumes/`` is offered. Scope is deliberately bounded so
a menu refresh never walks the whole project:

- every supported header under ``volumes/`` (``.dat``, ``.nhdr``, ``.nrrd``;
  prepared ``render.dat`` + ``volume.json``),
- the currently selected path,
- the gate volume (system fallback default).

Nothing is converted, imported, or written here. Identical payloads (same
``volumeHash``) are collapsed into one entry so copies do not flood the menu.
"""
from __future__ import annotations

import json
import os
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
GATE = RUNTIME / "gate-volume" / "render.dat"
VOLUME_DIR = ROOT / "volumes"
SOURCE_SUFFIXES = (".dat", ".nhdr", ".nrrd")
EXCLUDED_DIRS = {".git", "__pycache__", ".venv", "node_modules",
                 ".mypy_cache", ".pytest_cache", ".ruff_cache"}
MAX_ITEMS = 200


def _dimensions_from_volume_json(header: Path):
    """Dimensions + content hash from the import record beside a prepared entry."""
    try:
        data = json.loads(header.with_name("volume.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    digest = data.get("volumeHash") or data.get("rendererPayloadHash")
    dims = data.get("dimensions")
    if isinstance(dims, list) and len(dims) == 3:
        try:
            return tuple(int(v) for v in dims), digest if isinstance(digest, str) else None
        except (TypeError, ValueError):
            pass
    return None, digest if isinstance(digest, str) else None


def _binary_dat_dimensions(header: Path):
    """TU Wien binary DAT: three little-endian uint16 dimensions, then uint16 voxels."""
    if header.suffix.lower() != ".dat":
        return None
    try:
        with header.open("rb") as handle:
            prefix = handle.read(6)
    except OSError:
        return None
    if len(prefix) != 6:
        return None
    # QVis text headers start with printable ASCII; binary DAT does not.
    if not any(byte < 32 and byte not in (9, 10, 13) for byte in prefix):
        return None
    dimensions = struct.unpack("<3H", prefix)
    if all(0 < value <= 32768 for value in dimensions):
        return dimensions
    return None


def _header_dimensions(header: Path):
    """Parse binary DAT / QVis ``Resolution`` / NRRD ``sizes`` without importing NumPy."""
    binary = _binary_dat_dimensions(header)
    if binary is not None:
        return binary
    try:
        with header.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(4096)
    except OSError:
        return None
    for line in head.splitlines()[:80]:
        key, sep, value = line.partition(":")
        if not sep or key.strip().lower() not in ("resolution", "sizes"):
            continue
        parts = [part for part in re.split(r"[\s,]+", value.strip()) if part]
        try:
            numbers = [int(part) for part in parts]
        except ValueError:
            return None
        if len(numbers) == 3 and all(number > 0 for number in numbers):
            return tuple(numbers)
        # NRRD may carry a trailing scalar channel axis; other shapes are not
        # a single 3D scalar volume.
        if len(numbers) == 4 and numbers[-1] == 1:
            return tuple(numbers[:3])
        return None
    return None


def _unsupported_reason(header: Path):
    """Reject only what the reader refuses up front (today: QVis lz4 payloads)."""
    if header.suffix.lower() != ".dat":
        return None
    if _binary_dat_dimensions(header) is not None:  # TU Wien DAT has no compression field
        return None
    try:
        with header.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(4096)
    except OSError:
        return "unreadable"
    for line in head.splitlines()[:40]:
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "compression":
            compression = value.strip().lower()
            if compression not in ("", "none"):
                return "unsupported %s compression" % value.strip()
    return None


def _relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def describe(path) -> dict:
    """Facts for one candidate; never converts or validates the payload bytes."""
    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError:
        pass
    if not candidate.is_file():
        return {"ok": False, "path": str(candidate), "detail": "file not found"}
    dimensions, digest = _dimensions_from_volume_json(candidate)
    dimensions = dimensions or _header_dimensions(candidate)
    reason = _unsupported_reason(candidate)
    return {
        "ok": reason is None,
        "path": str(candidate),
        "label": _relative(candidate),
        "dimensions": list(dimensions) if dimensions else None,
        "hash": digest,
        "kind": "renderer" if candidate.name.lower() == "render.dat" else "source",
        "detail": reason or "",
    }


def _folder_headers() -> list[Path]:
    """Every supported header inside the fixed volume folder, recursively."""
    found: list[Path] = []
    if not VOLUME_DIR.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(VOLUME_DIR):
        dirnames[:] = [name for name in dirnames if name not in EXCLUDED_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            # canonical.dat is the same volume as the prepared render.dat beside it.
            if path.name.lower() == "canonical.dat" and (path.parent / "render.dat").is_file():
                continue
            found.append(path)
    return found


def discover_volumes(selected=None) -> list[dict]:
    """Return unique volumes from ``volumes/``, current selection first."""
    candidates: list[Path] = []
    if selected:
        candidates.append(Path(selected))
    candidates.append(GATE)
    candidates.extend(sorted(_folder_headers(), key=lambda path: str(path).lower()))

    by_key: dict[str, dict] = {}
    order: list[str] = []
    seen: set[str] = set()
    gate = GATE.resolve() if GATE.is_file() else None
    gate_digest = _dimensions_from_volume_json(gate)[1] if gate else None
    for candidate in candidates:
        try:
            path = candidate.expanduser().resolve()
        except OSError:
            continue
        if not path.is_file() or _unsupported_reason(path) is not None:
            continue
        marker = str(path)
        if marker in seen:  # same file reached via several configs/sources
            continue
        seen.add(marker)
        dimensions, digest = _dimensions_from_volume_json(path)
        dimensions = dimensions or _header_dimensions(path)
        key = digest or marker
        if key in by_key:
            by_key[key]["copies"] += 1
            continue
        by_key[key] = {
            "path": marker,
            "label": _relative(path),
            "dimensions": list(dimensions) if dimensions else None,
            "hash": digest,
            "kind": "renderer" if path.name.lower() == "render.dat" else "source",
            "default": bool(gate and (path == gate or (digest and digest == gate_digest))),
            "copies": 1,
        }
        order.append(key)
        if len(order) >= MAX_ITEMS:
            break
    return [by_key[key] for key in order]


def option_text(item: dict) -> str:
    """One menu row: path + dimensions + default/copy markers."""
    dims = "x".join(str(v) for v in item["dimensions"]) if item.get("dimensions") else "dims unknown"
    text = "%s  [%s]" % (item["label"], dims)
    if item.get("default"):
        text += "  [default]"
    if item.get("copies", 1) > 1:
        text += "  (+%d same copy)" % (item["copies"] - 1)
    return text


def prepared_render_raw(header: Path) -> Path | None:
    """render.raw beside a prepared header (render.dat + volume.json), else None."""
    candidate = Path(header)
    try:
        candidate = candidate.resolve()
    except OSError:
        pass
    if candidate.name.lower() == "render.dat":
        raw = candidate.with_name("render.raw")
        volume_json = candidate.with_name("volume.json")
        if raw.is_file() and volume_json.is_file():
            return raw
        return None
    return None


def slice_preview_path(header: str | Path, axis: str, index: int) -> dict:
    """Read-only grayscale slice preview cached under runtime/tui/slices.

    Only works for prepared volumes (render.dat beside render.raw/volume.json);
    never converts, never consumes agent budget, never touches run evidence.
    Returns {"ok", "image", "detail", ...}.
    """
    from pathlib import Path as _Path
    axis = str(axis).lower()
    if axis not in ("x", "y", "z"):
        return {"ok": False, "detail": "axis must be x, y or z"}
    header_path = _Path(header)
    raw = prepared_render_raw(header_path)
    if raw is None:
        return {"ok": False, "detail": "slice preview needs a prepared volume (render.dat + render.raw + volume.json)"}
    try:
        import json as _json
        meta = _json.loads(raw.with_name("volume.json").read_text(encoding="utf-8"))
        dims = meta.get("dimensions")
        if not (isinstance(dims, list) and len(dims) == 3):
            return {"ok": False, "detail": "volume.json has no 3D dimensions"}
        dims = [int(v) for v in dims]
    except (OSError, ValueError, TypeError) as error:
        return {"ok": False, "detail": "cannot read volume.json: %s" % error}
    ai = "xyz".index(axis)
    if not 0 <= int(index) < dims[ai]:
        return {"ok": False, "detail": "index %s out of range for %s (size %d)" % (index, axis, dims[ai])}
    try:
        index = int(index)
    except (TypeError, ValueError):
        return {"ok": False, "detail": "index must be an integer"}
    try:
        cache = ROOT / "runtime" / "tui" / "slices"
        cache.mkdir(parents=True, exist_ok=True)
        import hashlib as _hashlib
        key = _hashlib.sha1(str(raw).encode()).hexdigest()[:12]
        out = cache / ("slice-%s-%s-%d.png" % (key, axis, index))
        if out.is_file():
            return {"ok": True, "image": str(out), "axis": axis, "index": index,
                    "dimensions": dims, "cached": True}
        from PIL import Image as _Image
        import numpy as _np
        raw_bytes = raw.read_bytes()
        expected = dims[0] * dims[1] * dims[2]
        if len(raw_bytes) < expected:
            return {"ok": False, "detail": "render.raw too small (%d < %d)" % (len(raw_bytes), expected)}
        kind = meta.get("canonicalType") or "uint8"
        dtype = "<u2" if kind == "uint16" else "<u1"
        arr = _np.frombuffer(raw_bytes[:expected * (2 if dtype == "<u2" else 1)], dtype=dtype)
        try:
            arr = arr.reshape((dims[2], dims[1], dims[0]))
        except ValueError as error:
            return {"ok": False, "detail": "reshape failed: %s" % error}
        pixels = [lambda: arr[:, :, index], lambda: arr[:, index, :], lambda: arr[index, :, :]][ai]()
        if dtype == "<u2":
            lo, hi = (int(pixels.min()), int(pixels.max()))
            span = max(1, hi - lo)
            pixels = ((pixels.astype("float32") - lo) * 255.0 / span).astype("uint8")
        else:
            pixels = pixels.astype("uint8")
        _Image.fromarray(pixels).save(out)
        return {"ok": True, "image": str(out), "axis": axis, "index": index,
                "dimensions": dims, "cached": False}
    except Exception as error:
        return {"ok": False, "detail": "slice failed: %s" % error}


__all__ = ["describe", "discover_volumes", "option_text", "prepared_render_raw", "slice_preview_path"]
