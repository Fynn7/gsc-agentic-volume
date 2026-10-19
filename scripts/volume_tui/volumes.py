"""Discover volume headers already present on this machine (read-only, stdlib only).

The TUI no longer hard-wires one dataset: every header that the launcher can
hand to ``prepare`` is offered. Scope is deliberately bounded to what is
already on disk and cheap to inspect:

- every supported header in the project tree, root level included
  (``.dat``, ``.nhdr``, ``.nrrd``; prepared ``render.dat`` + ``volume.json``),
- volume paths recorded by previous run/TUI configs that still exist,
- the currently selected path.

Nothing is converted, imported, or written here. Identical payloads (same
``volumeHash``) are collapsed into one entry so copies from old sessions do
not flood the menu.
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


def _project_headers() -> list[Path]:
    """Every supported header in the project tree (root level included)."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
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


def _configured_headers() -> list[Path]:
    """Volume paths from configs of earlier runs/TUI launches that still exist."""
    found: list[Path] = []
    for pattern in ("*/config.json", "*.json", "tui-configs/*.json"):
        try:
            candidates = list(RUNTIME.glob(pattern))
        except OSError:
            continue
        for config_path in candidates:
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            raw = data.get("volumePath")
            if isinstance(raw, str) and raw.strip():
                path = Path(raw.strip().strip('"')).expanduser()
                if path.is_file():
                    found.append(path)
    return found


def discover_volumes(selected=None) -> list[dict]:
    """Return unique local volumes, current selection first, then the gate volume."""
    candidates: list[Path] = []
    if selected:
        candidates.append(Path(selected))
    candidates.append(GATE)
    candidates.extend(sorted(_project_headers(), key=lambda path: str(path).lower()))
    candidates.extend(sorted(_configured_headers(), key=lambda path: str(path).lower()))

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


__all__ = ["describe", "discover_volumes", "option_text"]
