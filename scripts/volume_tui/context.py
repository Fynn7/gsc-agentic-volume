"""Injectable context for the Textual TUI (testability first).

Production wires real volume_tui modules; Textual Pilot tests inject fakes
pointing at a temporary ROOT so no real runtime/services are touched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class AppContext:
    root: Path
    settings: dict = field(default_factory=dict)
    # Injectable callables (defaults wired in app.py to real modules).
    load_settings: Callable[[], dict] | None = None
    save_settings: Callable[[dict], None] | None = None
    discover_runs: Callable[..., list[dict]] | None = None
    discover_volumes: Callable[..., list[dict]] | None = None
    check_env: Callable[[dict], dict] | None = None
    ensure_services: Callable[[dict], dict] | None = None
    extra: dict = field(default_factory=dict)

    def get_settings(self) -> dict:
        if self.load_settings is not None:
            return self.load_settings()
        return dict(self.settings)

    def put_settings(self, settings: dict) -> None:
        self.settings = dict(settings)
        if self.save_settings is not None:
            self.save_settings(settings)
