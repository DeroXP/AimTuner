"""Colours shared by the renderer, the menus and the scenarios (no GL imports here)."""

WHITE = (0.94, 0.96, 0.98, 1.0)
DIM = (0.60, 0.65, 0.72, 1.0)
FAINT = (0.40, 0.44, 0.50, 1.0)
ACCENT = (0.10, 0.86, 0.76, 1.0)
GOOD = (0.30, 0.92, 0.45, 1.0)
BAD = (1.00, 0.36, 0.32, 1.0)
WARN = (1.00, 0.78, 0.25, 1.0)
PANEL = (0.035, 0.045, 0.060, 0.84)
BLACK = (0.0, 0.0, 0.0, 1.0)


def alpha(c, a):
    return (c[0], c[1], c[2], c[3] * a)
