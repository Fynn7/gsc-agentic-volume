"""Flicker-free terminal painting + loading spinners (stdlib only).

Root cause of the reported flicker: every refresh called ``os.system("cls")``,
which blanks the whole console and reprints from scratch. On each keypress or
each 1s live-trace poll the screen flashed.

This module paints in place instead:

- ``Screen.draw(lines)`` moves the cursor home (``ESC[H``), rewrites the same
  region, pads short lines with spaces, and clears only the leftover tail
  (``ESC[J``). No full clear, no cursor jump, no flash.
- The cursor is hidden while a screen is active and restored on exit.
- ``SPINNER_FRAMES`` + ``spinner(idx)`` give every blocking/waiting state an
  animated loading mark. ``run_with_spinner`` animates a single ``\\r`` line
  (no ``cls``) while a blocking callable runs in a worker thread.
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time

# Braille dots render in Windows Terminal; plain conhost falls back to ASCII.
# Probe once: try encoding with the stdout codec, else use ASCII.
try:
    "⠋".encode(sys.stdout.encoding or "utf-8")
    _BRAILLE_OK = True
except Exception:
    _BRAILLE_OK = False

SPINNER_FRAMES = (
    ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    if _BRAILLE_OK
    else ["|", "/", "-", "\\"]
)
# Dots runner used for longer waits (second animation style, same tick).
DOTS_FRAMES = ["●○○", "○●○", "○○●", "○●○"] if _BRAILLE_OK else [".  ", ".. ", "...", ".. "]


def spinner(index: int) -> str:
    return SPINNER_FRAMES[index % len(SPINNER_FRAMES)]


def dots(index: int) -> str:
    return DOTS_FRAMES[index % len(DOTS_FRAMES)]


def _enable_vt() -> None:
    """Enable ANSI escape processing on Windows conhost (no-op elsewhere)."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


class Screen:
    """In-place screen painter. Use as ``with Screen() as scr: scr.draw(...)``."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self._opened = False
        self._first = True

    def __enter__(self) -> "Screen":
        _enable_vt()
        try:
            self.stream.write("\x1b[?25l")  # hide cursor: no caret blink = less flicker
            self.stream.flush()
        except Exception:
            pass
        self._opened = True
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.stream.write("\x1b[?25h\n")  # restore cursor
            self.stream.flush()
        except Exception:
            pass
        self._opened = False

    def draw(self, lines: list[str]) -> None:
        """Repaint the screen region without clearing it first."""
        try:
            width = shutil.get_terminal_size((80, 24)).columns
        except Exception:
            width = 80
        out: list[str] = []
        if self._first:
            # One full clear at entry only; every later frame reuses the region.
            out.append("\x1b[?25l\x1b[H\x1b[J")
            self._first = False
        else:
            out.append("\x1b[H")
        for line in lines:
            text = line[: max(1, width)]
            out.append(text + " " * max(0, width - len(text)) + "\n")
        out.append("\x1b[J")
        try:
            self.stream.write("".join(out))
            self.stream.flush()
        except Exception:
            # Fallback for redirected pipes: plain print of the last frame.
            try:
                self.stream.write("\n".join(lines) + "\n")
            except Exception:
                pass


def run_with_spinner(label: str, func, *args, interval: float = 0.1, stream=None, **kwargs):
    """Run blocking ``func`` while animating ``<spinner> label`` on one line.

    No screen clear is used — only ``\\r`` rewrites — so there is no flicker.
    Returns ``func``'s result (or raises its exception). Every blocking TUI
    operation (service start, env check, verify, H200 probe) goes through here
    so a loading animation is always visible.
    """
    out = stream or sys.stdout
    _enable_vt()
    box: dict = {}
    done = threading.Event()

    def work():
        try:
            box["result"] = func(*args, **kwargs)
        except Exception as error:  # noqa: BLE001 - re-raised below
            box["error"] = error
        finally:
            done.set()

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    start = time.time()
    frame = 0
    try:
        while not done.wait(interval):
            elapsed = time.time() - start
            try:
                out.write("\r\x1b[K%s %s (%.0fs)" % (spinner(frame), label, elapsed))
                out.flush()
            except Exception:
                pass
            frame += 1
    finally:
        try:
            out.write("\r\x1b[K")
            out.flush()
        except Exception:
            pass
    thread.join(timeout=5)
    if "error" in box:
        raise box["error"]
    return box.get("result")
