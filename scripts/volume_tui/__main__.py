"""Entry point: `python -B scripts/volume_tui` — no model call on startup."""
import sys

if "--help" in sys.argv or "-h" in sys.argv:
    print("Volume Agent TUI (Textual IDE-style; control/display over Codex CLI).")
    print("Usage: python -B scripts/volume_tui [--help]")
    print("Run .\\Start-VolumeTUI.ps1 for venv bootstrap + checks + launch.")
    print("History/verify are read-only; evidence is never deleted.")
    raise SystemExit(0)

from app import main

if __name__ == "__main__":
    raise SystemExit(main())
