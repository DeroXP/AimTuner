"""The app's icon, drawn rather than shipped: a crosshair on a dark tile, in the accent colour.

app.py gives it to the window (so the taskbar shows it), and build_exe.py writes it out as
assets/icon.ico for Core.exe.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from .theme import ACCENT


def image(size: int = 256) -> Image.Image:
    n = size * 4                                    # drawn large, then scaled down: smooth edges
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, n - 1, n - 1), radius=round(n * 0.2), fill=(14, 18, 24, 255))
    acc = tuple(round(c * 255) for c in ACCENT[:3]) + (255,)
    c = n / 2
    ring, w = n * 0.29, round(n * 0.065)
    d.ellipse((c - ring, c - ring, c + ring, c + ring), outline=acc, width=w)
    gap, out, half = n * 0.11, n * 0.41, n * 0.034  # four ticks crossing the ring, a gap in the middle
    for sign in (1, -1):
        lo, hi = sorted((c + sign * gap, c + sign * out))
        d.rectangle((lo, c - half, hi, c + half), fill=acc)        # left / right
        d.rectangle((c - half, lo, c + half, hi), fill=acc)        # top / bottom
    dot = n * 0.045
    d.ellipse((c - dot, c - dot, c + dot, c + dot), fill=(240, 245, 250, 255))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def save_ico(path: str) -> None:
    """A Windows .ico with the sizes Explorer and the taskbar ask for."""
    image(256).save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
