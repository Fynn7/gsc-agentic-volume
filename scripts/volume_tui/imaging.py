"""Pillow -> Rich Text half-block preview (Textual-native truecolor).

A Textual Static never interprets raw ANSI escapes: printing them shows the
literal codes on screen (e.g. "[48;2;0;0;0m"). Each half-block cell is
therefore a Rich styled character instead (fg=top pixel, bg=bottom pixel),
which Textual renders as truecolor.

The preview never crops and never distorts: the frame is fitted into the
requested cell box with its aspect ratio intact, so the whole image is
visible instead of a cut-off corner.
"""
from __future__ import annotations

from pathlib import Path


def fit_box(src_w: int, src_h: int, max_w: int, max_h: int) -> tuple[int, int]:
    """Largest box <= (max_w, max_h) that keeps the source aspect ratio.

    Everything is in pixels: one text row shows two pixel rows, so a cell box
    of (cols, rows) is a pixel canvas of (cols, rows * 2).
    """
    src_w = max(1, int(src_w))
    src_h = max(1, int(src_h))
    max_w = max(1, int(max_w))
    max_h = max(1, int(max_h))
    scale = min(max_w / src_w, max_h / src_h)
    return (max(1, int(src_w * scale)), max(1, int(src_h * scale)))


def _resample_filter():
    """LANCZOS when the installed Pillow has it, else Pillow's default."""
    try:
        from PIL import Image
    except Exception:
        return None
    return getattr(Image, "LANCZOS", None)


def render_frame(image_path: str | Path, width: int = 60, height: int = 28):
    """Downsample an image to half-block cells with per-cell fg/bg colors.

    The image is scaled to fit (width, height) cells while keeping its aspect
    ratio; the remaining area is black padding, never a crop.

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
        canvas_w = max(1, int(width))
        canvas_h = max(2, int(height) * 2)
        box_w, box_h = fit_box(img.width, img.height, canvas_w, canvas_h)
        resample = _resample_filter()
        if resample is None:
            img = img.resize((box_w, box_h))
        else:
            img = img.resize((box_w, box_h), resample)
        canvas = Image.new("RGB", (canvas_w, canvas_h), (0, 0, 0))
        canvas.paste(img, ((canvas_w - box_w) // 2, (canvas_h - box_h) // 2))
        pixels = canvas.load()
        text = Text(no_wrap=True)
        for y in range(0, canvas.height, 2):
            if y:
                text.append("\n")
            for x in range(canvas.width):
                top = pixels[x, y]
                bottom = pixels[x, min(y + 1, canvas.height - 1)]
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


def preview_fitted_or_message(image_path: str | Path | None, width: int = 60,
                              height: int = 28):
    """Frame preview at the picture's own size - no black padding around it.

    The frame is scaled into the cell box with its aspect ratio intact; the
    preview is then drawn at exactly that size, so the conversation shows the
    picture and not a canvas with empty columns beside it.
    """
    if not image_path:
        return preview_or_message(image_path, width, height)
    try:
        from PIL import Image
        with Image.open(image_path) as image:
            src_w, src_h = image.size
    except Exception:
        return preview_or_message(image_path, width, height)
    box_w, box_h = fit_box(src_w, src_h, max(1, int(width)), max(2, int(height) * 2))
    return render_frame(image_path, box_w, max(1, box_h // 2))
