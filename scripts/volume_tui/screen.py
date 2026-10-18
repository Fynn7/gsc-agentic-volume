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
import re
import shutil
import sys
import threading
import time

# --- Per-category colors (stdlib ANSI only) ---------------------------------
# 16-color codes stay readable on legacy conhost and Windows Terminal alike.
# Warning orange uses 256-color slot 208 where the terminal supports it
# (Windows Terminal / 256color TERM) and falls back to bright yellow.
RESET = "\x1b[0m"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _supports_256() -> bool:
    term = (os.environ.get("TERM") or "")
    return bool(
        os.environ.get("WT_SESSION")
        or os.environ.get("COLORTERM")
        or "256color" in term
        or "truecolor" in term
    )


COLORS = {
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue": "\x1b[34m",
    "magenta": "\x1b[35m",
    "cyan": "\x1b[36m",
    "gray": "\x1b[90m",
    "bright_red": "\x1b[91m",
    "bright_green": "\x1b[92m",
    "bright_yellow": "\x1b[93m",
    "bright_blue": "\x1b[94m",
    "bright_magenta": "\x1b[95m",
    "bright_cyan": "\x1b[96m",
    "bold": "\x1b[1m",
    "orange": "\x1b[38;5;208m" if _supports_256() else "\x1b[93m",
}

# Event category -> color name. Every live-trace category has its own color;
# warnings are orange, errors red, finished runs green.
CATEGORY_COLORS = {
    "MODEL": "cyan",
    "TOOL": "bright_blue",
    "IMAGE": "green",
    "INFER": "magenta",
    "ERROR": "red",
    "WARN": "orange",
    "FINISH": "bright_green",
}


def _color_enabled(stream=None) -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    target = stream or sys.stdout
    try:
        return bool(target.isatty())
    except Exception:
        return False


def colored(text: str, name: str, stream=None) -> str:
    """Wrap text in a named color; plain text when color is unavailable."""
    code = COLORS.get(name, "")
    if not code or not _color_enabled(stream):
        return text
    return "%s%s%s" % (code, text, RESET)


def visible_len(text: str) -> int:
    """Printable width ignoring ANSI escape sequences."""
    return len(_ANSI_RE.sub("", text))


def _truncate_ansi(text: str, width: int) -> str:
    """Cut to `width` visible cells without splitting an escape sequence."""
    out: list[str] = []
    shown = 0
    i = 0
    while i < len(text) and shown < width:
        match = _ANSI_RE.match(text, i)
        if match:
            out.append(match.group(0))
            i = match.end()
        else:
            out.append(text[i])
            i += 1
            shown += 1
    return "".join(out)

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
            # ANSI-aware: truncate by visible cells, pad by visible width, and
            # always reset at end of line so no color bleeds into the next row.
            text = _truncate_ansi(line, max(1, width))
            out.append(text + RESET + " " * max(0, width - visible_len(text)) + "\n")
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
