"""Cross-platform evidence helpers (read-only except verify output)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import subproc as subproc_mod


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


def copy_to_clipboard(text: str) -> str:
    """Put text on the system clipboard; returns a human message (no exception).

    Windows uses PowerShell's Set-Clipboard with the text on stdin (read as
    UTF-8), so multi-megabyte payloads survive intact — no command line
    length limit, no temporary file with the run log on disk.
    """
    if not text:
        return "Nothing to copy."
    payload = text.encode("utf-8")
    try:
        if sys.platform == "win32":
            command = ("[Console]::InputEncoding=[Text.Encoding]::UTF8; "
                       "$s=[Console]::In.ReadToEnd(); Set-Clipboard -Value $s")
            proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                                   "-Command", command],
                                  input=payload, capture_output=True, timeout=60,
                                  **subproc_mod.kwargs())
            if proc.returncode == 0:
                return "Copied %d characters to the clipboard." % len(text)
            detail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            return "Clipboard copy failed: %s" % (detail[-1] if detail
                                                  else "Set-Clipboard failed")
        if sys.platform == "darwin":
            proc = subprocess.run(["pbcopy"], input=payload, capture_output=True,
                                  timeout=30, **subproc_mod.kwargs())
            if proc.returncode == 0:
                return "Copied %d characters to the clipboard." % len(text)
            return "Clipboard copy failed: pbcopy failed"
        for command in (["wl-copy"], ["xclip", "-selection", "clipboard"],
                        ["xsel", "--clipboard", "--input"]):
            try:
                proc = subprocess.run(command, input=payload, capture_output=True,
                                      timeout=30, **subproc_mod.kwargs())
            except OSError:
                continue
            if proc.returncode == 0:
                return "Copied %d characters to the clipboard." % len(text)
        return ("Clipboard copy failed: no clipboard tool found "
                "(install wl-clipboard or xclip)")
    except Exception as error:  # noqa: BLE001 - reported as a message
        return "Clipboard copy failed: %s" % error


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
