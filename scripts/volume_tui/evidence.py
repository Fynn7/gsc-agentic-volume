"""Cross-platform evidence helpers (read-only except verify output)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def open_path(path: str | Path) -> str:
    """Open a path in the system viewer; returns a human message (no exception)."""
    target = Path(path)
    if not target.exists():
        return "Missing (not yet written or deleted): %s" % path
    try:
        if hasattr(__import__("os"), "startfile"):
            import os as _os
            _os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(target)])
        else:
            # Linux: xdg-open; Windows fallback when startfile missing.
            try:
                subprocess.run(["xdg-open", str(target)])
            except OSError:
                import os as _os
                if _os.name == "nt":
                    subprocess.run(["explorer", str(target)])
                else:
                    raise
        return "Opened: %s" % target
    except Exception as error:
        return "Viewer did not open: %s" % error


def failure_hint(run_dir: str | Path) -> str | None:
    """One-line cause from failure.json/run.json (read-only)."""
    import json
    for name in ("failure.json", "run.json"):
        try:
            data = json.loads((Path(run_dir) / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        error = data.get("error") if isinstance(data, dict) else None
        if error:
            return "cause: %s" % " ".join(str(error).split())[:200]
    return None
