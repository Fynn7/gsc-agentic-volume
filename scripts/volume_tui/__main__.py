"""Entry point: `python -B scripts/volume_tui` — no model call on startup."""
import sys

if "--help" in sys.argv or "-h" in sys.argv:
    print("Volume Agent TUI (control/display over Codex CLI; no agent loop here).")
    print("Usage: python -B scripts/volume_tui  [--help]")
    print("Run .\\Start-VolumeTUI.ps1 for checks + launch. History/verify are read-only.")
    raise SystemExit(0)

from ui import main

if __name__ == "__main__":
    raise SystemExit(main())
