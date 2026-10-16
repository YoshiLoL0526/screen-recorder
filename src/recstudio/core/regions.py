"""Geometría de regiones (puro): mover y redimensionar rectángulos con límites.

El editor de región de la UI trabaja con **deltas** (F0/S5: los `DragEvent` no dan
coordenadas absolutas del nodo), así que aquí viven las operaciones sobre el rectángulo.
"""

from __future__ import annotations

Region = tuple[int, int, int, int]  # x, y, ancho, alto
MIN_SIZE = 64
MIN_SIDE = 32


def clamp_region(region: Region, bounds: tuple[int, int], *, min_size: int = MIN_SIZE) -> Region:
    """Recorta la región al rectángulo (ancho, alto) del monitor."""
    width, height = bounds
    x, y, w, h = region
    w = max(min_size, min(int(w), width))
    h = max(min_size, min(int(h), height))
    x = max(0, min(int(x), width - w))
    y = max(0, min(int(y), height - h))
    return x, y, w, h


def move_region(region: Region, dx: int, dy: int, bounds: tuple[int, int]) -> Region:
    x, y, w, h = region
    return clamp_region((x + int(round(dx)), y + int(round(dy)), w, h), bounds)


def resize_region(
    region: Region,
    corner: str,
    dx: int,
    dy: int,
    bounds: tuple[int, int],
    *,
    min_side: int = MIN_SIDE,
) -> Region:
    """Redimensiona desde una esquina (`tl`, `tr`, `bl`, `br`) con un delta en px del monitor."""
    x, y, w, h = region
    dx = int(round(dx))
    dy = int(round(dy))
    left, top, right, bottom = x, y, x + w, y + h

    if "l" in corner:
        left = min(max(0, left + dx), right - min_side)
    if "r" in corner:
        right = max(min(bounds[0], right + dx), left + min_side)
    if "t" in corner:
        top = min(max(0, top + dy), bottom - min_side)
    if "b" in corner:
        bottom = max(min(bounds[1], bottom + dy), top + min_side)

    return left, top, right - left, bottom - top
