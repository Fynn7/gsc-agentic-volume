"""Pillow -> Rich Text half-block preview (Textual-native truecolor).

A Textual Static never interprets raw ANSI escapes: printing them shows the
literal codes on screen (e.g. "[48;2;0;0;0m"). Each half-block cell is
therefore a Rich styled character instead (fg=top pixel, bg=bottom pixel),
which Textual renders as truecolor.
"""
from __future__ import annotations

from pathlib import Path


def render_frame(image_path: str | Path, width: int = 60, height: int = 28):
    """Downsample an image to half-block cells with per-cell fg/bg colors.

    Returns a Rich Text (pass it straight to Static.update), or a plain
    message string when Pillow is missing or the file is unreadable.
    """
    try:
        from PIL import Image
    except Exception:
        return "(preview unavailable: Pillow missing)"
    try:
        from rich.text import Text
        from rich.style import Style
        from rich.color import Color
    except Exception:
        return "(preview unavailable: rich missing)"
    try:
        path = Path(image_path)
        if not path.is_file():
            return "(preview unavailable: file not found)"
        img = Image.open(path).convert("RGB")
        # Half-block: each terminal row shows two pixel rows.
        img = img.resize((max(1, width), max(1, height * 2)))
        pixels = img.load()
        text = Text(no_wrap=True)
        for y in range(0, img.height, 2):
            if y:
                text.append("\n")
            for x in range(img.width):
                top = pixels[x, y]
                bottom = pixels[x, min(y + 1, img.height - 1)]
                text.append("\u2580", style=Style(
                    color=Color.from_rgb(int(top[0]), int(top[1]), int(top[2])),
                    bgcolor=Color.from_rgb(int(bottom[0]), int(bottom[1]), int(bottom[2]))))
        return text
    except Exception as error:
        return "(preview failed: %s)" % error


def preview_or_message(image_path: str | Path | None, width: int = 60, height: int = 28):
    """Renderable frame preview, or a short message when there is no frame."""
    if not image_path:
        return "(no frame yet)"
    return render_frame(image_path, width, height)
