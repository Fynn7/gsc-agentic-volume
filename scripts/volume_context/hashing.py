"""Streaming hashing.

Source files are hashed before and after decoding so a file that changes while
it is being read is detected instead of producing facts from two different
states.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .errors import InputError
from .progress import report

_READ_BLOCK = 1024 * 1024


def sha256_file(path: Path, progress_phase: str | None = None, progress_label: str = "") -> str:
    digest = hashlib.sha256()
    completed = 0
    total = path.stat().st_size if progress_phase else None
    if progress_phase:
        report(progress_phase, progress_label, 0, total, "bytes", True)
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(_READ_BLOCK)
                if not block:
                    break
                digest.update(block)
                completed += len(block)
                if progress_phase:
                    report(progress_phase, progress_label, completed, total, "bytes")
    except OSError as exc:
        raise InputError("input_unreadable", f"cannot read {path.name}: {exc.strerror or exc}") from exc
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_record(path: Path, role: str, progress_phase: str | None = None, progress_label: str = "") -> dict:
    """Hash one input file. The absolute path stays local to the audit records."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise InputError("input_unreadable", f"cannot stat {path}: {exc.strerror or exc}") from exc
    return {
        "role": role,
        "path": str(path),
        "byteLength": int(size),
        "sha256": sha256_file(path, progress_phase, progress_label),
    }
