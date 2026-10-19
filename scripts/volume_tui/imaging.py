"""Pillow -> ANSI truecolor half-block preview (read-only, no model call)."""
from __future__ import annotations

from pathlib import Path


def render_ansi(image_path: str | Path, width: int = 60, height: int = 28) -> str:
    """Downsample an image to ANSI truecolor half-blocks.

    Returns a multi-line string using upper half-block characters with
    foreground=top pixel, background=bottom pixel. Falls back to a short
    message when Pillow or the file is unavailable.
    """
    try:
        from PIL import Image
    except Exception:
        return "(preview unavailable: Pillow missing)"
    try:
        path = Path(image_path)
        if not path.is_file():
            return "(preview unavailable: file not found)"
        img = Image.open(path).convert("RGB")
        # Half-block: each terminal row shows two pixel rows.
        img = img.resize((max(1, width), max(1, height * 2)))
        pixels = img.load()
        lines: list[str] = []
        for y in range(0, img.height, 2):
            parts: list[str] = []
            for x in range(img.width):
                top = pixels[x, y]
                bottom = pixels[x, min(y + 1, img.height - 1)]
                parts.append("\x1b[38;2;%d;%d;%dm\x1b[48;2;%d;%d;%dm\u2580" % (
                    top[0], top[1], top[2], bottom[0], bottom[1], bottom[2]))
            lines.append("".join(parts) + "\x1b[0m")
        return "\n".join(lines)
    except Exception as error:
        return "(preview failed: %s)" % error


def preview_or_message(image_path: str | Path | None, width: int = 60, height: int = 28) -> str:
    if not image_path:
        return "(no frame yet)"
    return render_ansi(image_path, width, height)
