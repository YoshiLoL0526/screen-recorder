"""Cursor propio («software cursor»).

Motivo (F0/F1): el cursor compuesto por Windows a través de WGC tiene una latencia **variable**
(12–119 ms medidos) que domina el error A/V. Aquí lo dibujamos nosotros:
- la posición se lee con `GetCursorPos` **en el instante de la captura** (coherente con el frame),
- la forma se extrae del sistema una vez por cambio de cursor (cacheada por `hCursor`),
- el compuesto es una mezcla alfa de un parche pequeño (~32×32): coste despreciable.

El compuesto (`draw_cursor`) es una función pura sobre numpy y se testea sin hardware.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass

import numpy as np

CURSOR_SHOWING = 0x00000001
DI_NORMAL = 0x0003


@dataclass(frozen=True)
class CursorSnapshot:
    """Posición (coordenadas de pantalla virtual) y forma del cursor del sistema."""

    x: int
    y: int
    hx: int  # hotspot
    hy: int
    pixels: np.ndarray  # (h, w, 4) BGRA premultiplicado no; alfa normal 0..255


class CursorProvider:
    """Lee el cursor del sistema con cache de forma (Win32)."""

    def __init__(self) -> None:
        self._shapes: dict[int, CursorSnapshot | None] = {}
        self.failures = 0

    def snapshot(self) -> CursorSnapshot | None:
        position = self.position()
        if position is None:
            return None
        shape = self._shape()
        if shape is None:
            return None
        return CursorSnapshot(position[0], position[1], shape.hx, shape.hy, shape.pixels)

    def position(self) -> tuple[int, int] | None:
        """Posición global del cursor si es visible; `None` si está oculto o falla."""
        try:
            info = _CURSORINFO()
            info.cbSize = ctypes.sizeof(_CURSORINFO)
            if not ctypes.windll.user32.GetCursorInfo(ctypes.byref(info)):
                return None
            if not info.flags & CURSOR_SHOWING or not info.hCursor:
                return None
            return int(info.ptScreenPos.x), int(info.ptScreenPos.y)
        except Exception:
            self.failures += 1
            return None

    def _shape(self) -> CursorSnapshot | None:
        handle = None
        try:
            info = _CURSORINFO()
            info.cbSize = ctypes.sizeof(_CURSORINFO)
            if not ctypes.windll.user32.GetCursorInfo(ctypes.byref(info)) or not info.hCursor:
                return None
            handle = int(info.hCursor)
            if handle in self._shapes:
                return self._shapes[handle]
            shape = _extract_shape(handle)
            self._shapes[handle] = shape
            if len(self._shapes) > 64:  # evita crecer sin límite si el cursor parpadea
                self._shapes.pop(next(iter(self._shapes)))
            return shape
        except Exception:
            self.failures += 1
            return None


class _CURSORINFO(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("flags", wt.DWORD),
        ("hCursor", ctypes.c_void_p),
        ("ptScreenPos", wt.POINT),
    ]


class _ICONINFO(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("fIcon", wt.BOOL),
        ("xHotspot", wt.DWORD),
        ("yHotspot", wt.DWORD),
        ("hbmMask", ctypes.c_void_p),
        ("hbmColor", ctypes.c_void_p),
    ]


class _BITMAP(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("bmType", ctypes.c_long),
        ("bmWidth", ctypes.c_long),
        ("bmHeight", ctypes.c_long),
        ("bmWidthBytes", ctypes.c_long),
        ("bmPlanes", wt.WORD),
        ("bmBitsPixel", wt.WORD),
        ("bmBits", ctypes.c_void_p),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


def _extract_shape(handle: int) -> CursorSnapshot | None:
    """BGRA del cursor del sistema (solo el camino 32bpp con color; si no, None)."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    icon = _ICONINFO()
    if not user32.GetIconInfo(handle, ctypes.byref(icon)):
        return None
    try:
        if not icon.hbmColor:
            return None  # cursor monocromo (raro en Windows moderno): sin forma propia
        bitmap = _BITMAP()
        if not gdi32.GetObjectW(icon.hbmColor, ctypes.sizeof(bitmap), ctypes.byref(bitmap)):
            return None
        width, height = int(bitmap.bmWidth), int(bitmap.bmHeight)
        if not (1 <= width <= 256 and 1 <= height <= 256):
            return None

        header = _BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height  # de arriba a abajo
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0  # BI_RGB

        buffer = (ctypes.c_uint8 * (width * height * 4))()
        hdc = user32.GetDC(None)
        try:
            lines = gdi32.GetDIBits(
                hdc, icon.hbmColor, 0, height, ctypes.byref(buffer), ctypes.byref(header), 0
            )
        finally:
            user32.ReleaseDC(None, hdc)
        if lines != height:
            return None

        pixels = np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 4).copy()
        return CursorSnapshot(0, 0, int(icon.xHotspot), int(icon.yHotspot), pixels)
    finally:
        if icon.hbmColor:
            ctypes.windll.gdi32.DeleteObject(icon.hbmColor)
        if icon.hbmMask:
            ctypes.windll.gdi32.DeleteObject(icon.hbmMask)


def draw_cursor(
    frame: np.ndarray,
    snapshot: CursorSnapshot,
    origin: tuple[int, int] = (0, 0),
    *,
    on_copy: bool = False,
) -> np.ndarray:
    """Compone el cursor sobre el frame BGRA (in place si se puede; devuelve el array usado).

    `origin` = esquina superior izquierda del frame en coordenadas de pantalla virtual.
    """
    out = frame.copy() if (on_copy or not frame.flags.writeable) else frame
    if frame.ndim != 3 or frame.shape[2] < 4:
        return out

    x = snapshot.x - origin[0] - snapshot.hx
    y = snapshot.y - origin[1] - snapshot.hy
    cursor = snapshot.pixels
    ch, cw = cursor.shape[:2]
    fh, fw = out.shape[:2]

    # recorta el parche del cursor a lo visible
    sx0, sy0 = max(0, -x), max(0, -y)
    dx0, dy0 = max(0, x), max(0, y)
    dx1, dy1 = min(fw, x + cw), min(fh, y + ch)
    if dx1 <= dx0 or dy1 <= dy0:
        return out
    patch = cursor[sy0 : sy0 + (dy1 - dy0), sx0 : sx0 + (dx1 - dx0)]
    alpha = (patch[:, :, 3:4].astype(np.uint16))
    if not alpha.any():
        return out
    dst = out[dy0:dy1, dx0:dx1]
    blended = (patch[:, :, :3].astype(np.uint16) * alpha + dst[:, :, :3].astype(np.uint16) * (255 - alpha)) // 255
    dst[:, :, :3] = blended.astype(np.uint8)
    return out
