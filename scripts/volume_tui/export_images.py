"""Export every frame PNG of a run into one zip (a download, not evidence).

The run folder is never written to: the zip lands in the user's Downloads
folder, like the chat-log export. Sources are the session folder that holds
the frames (runtime/<sessionId>/frame-*/image.png), the run directory
itself (frame-*/image.png fallback), plus any PNG the run's session.json
events point at. Missing files are skipped; duplicates are stored once.

Pure functions plus one small writer - no model, no network, no run.
"""
from __future__ import annotations

import re
import sys
import time
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _extra in (_ROOT / "scripts", Path(__file__).resolve().parent):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

# Where an exported zip lands (the run folder stays read-only):
# the user's Downloads folder (a direct download, not the project folder).
LOG_DIR = Path.home() / "Downloads"


def _safe_name(text: str) -> str:
    """A file name every file system accepts."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or "").strip())
    return cleaned.strip("-.") or "run"


def collect_run_images(run_dir: str | Path | None = None,
                       session_dir: str | Path | None = None,
                       extra: list[str | Path] | None = None,
                       limit: int = 500) -> list[Path]:
    """Every frame PNG of the run, oldest first, existing files only.

    session_dir is runtime/<sessionId> (the frames live there as
    frame-N/image.png); run_dir is the run folder (fallback + session.json
    event paths). extra carries caller-known paths (e.g. verification
    final image). Duplicates (same resolved file) are kept once.
    """
    seen: set[str] = set()
    out: list[Path] = []

    def _add(path: str | Path | None) -> None:
        if not path:
            return
        try:
            candidate = Path(path)
        except (TypeError, ValueError):
            return
        if candidate.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            return
        if not candidate.is_file():
            return
        try:
            key = str(candidate.resolve())
        except OSError:
            key = str(candidate)
        if key in seen:
            return
        seen.add(key)
        out.append(candidate)

    for path in list(extra or []):
        _add(path)

    if session_dir is not None:
        try:
            base = Path(session_dir)
            if base.is_dir():
                for png in sorted(base.glob("frame-*/image.png")):
                    _add(png)
                # Any other PNG the session folder holds (slices, previews).
                for png in sorted(base.rglob("*.png")):
                    _add(png)
        except OSError:
            pass

    if run_dir is not None:
        try:
            base = Path(run_dir)
            if base.is_dir():
                for png in sorted(base.glob("frame-*/image.png")):
                    _add(png)
                # Paths the run's own session.json events point at: the
                # authoritative list of proven frames (may live outside
                # both folders when the session moved).
                try:
                    import json as _json
                    data = _json.loads((base / "session.json").read_text(encoding="utf-8"))
                    events = data.get("events") if isinstance(data, dict) else None
                    if isinstance(events, list):
                        for event in events:
                            if not isinstance(event, dict):
                                continue
                            if event.get("kind") not in ("initial_render", "render"):
                                continue
                            result = event.get("result") or {}
                            if isinstance(result, dict):
                                _add(result.get("image"))
                except (OSError, ValueError):
                    pass
        except OSError:
            pass

    # Oldest first so frame-1 sorts before frame-10 (numeric when possible).
    def _sort_key(path: Path):
        match = re.search(r"frame-(\d+)", str(path))
        if match:
            return (0, int(match.group(1)), str(path))
        try:
            return (1, 0, str(path))
        except Exception:
            return (1, 0, "")

    out.sort(key=_sort_key)
    if limit and len(out) > limit:
        del out[:-limit]
    return out


def export_run_images_zip(images: list[str | Path], *, run_id=None) -> Path:
    """Zip the given images into Downloads; returns the path of the zip.

    Every export keeps its own file: a second export in the same second gets
    its own number. Zip entry names are frame file names (frame-N-image.png),
    deduplicated, so the zip opens flat in the file explorer.
    """
    folder = Path(LOG_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = "%s-images-%s" % (_safe_name(run_id or "run"), stamp)
    path = folder / ("%s.zip" % base)
    number = 2
    while path.exists():
        path = folder / ("%s-%d.zip" % (base, number))
        number += 1
    used: set[str] = set()

    def _arcname(src: Path) -> str:
        parent = src.parent.name
        if parent.startswith("frame-"):
            name = "%s-%s" % (parent, src.name)
        else:
            name = src.name
        candidate = name
        index = 2
        while candidate.lower() in used:
            stem, suffix = src.stem, src.suffix
            candidate = "%s-%d%s" % (stem, index, suffix)
            index += 1
        used.add(candidate.lower())
        return candidate

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in images or []:
            src = Path(str(item))
            if not src.is_file():
                continue
            try:
                archive.write(src, arcname=_arcname(src))
            except OSError:
                continue
    return path
