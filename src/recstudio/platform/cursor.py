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
    # (h, w) bool: píxeles que invierten lo que hay debajo (XOR de los cursores monocromos,
    # p. ej. el I-beam de texto del esquema por defecto); None si no hay ninguno
    invert: np.ndarray | None = None


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
        return CursorSnapshot(position[0], position[1], shape.hx, shape.hy, shape.pixels, shape.invert)

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


def shape_from_color(color: np.ndarray, and_mask: np.ndarray | None) -> tuple[np.ndarray, np.ndarray | None]:
    """Cursor en color → (BGRA con alfa, invert).

    Con canal alfa (lo normal en los cursores modernos) se usa tal cual. Sin alfa (todo a 0:
    cursores antiguos de 24 bpp), la transparencia sale de la máscara AND (True = transparente)
    y los píxeles transparentes con color hacen XOR con el fondo: se aproximan invirtiendo.
    """
    if color[:, :, 3].any():
        return color, None
    pixels = color.copy()
    if and_mask is None:
        pixels[:, :, 3] = 255
        return pixels, None
    pixels[:, :, 3] = np.where(and_mask, 0, 255).astype(np.uint8)
    invert = and_mask & color[:, :, :3].any(axis=2)
    return pixels, (invert if invert.any() else None)


def shape_from_mono(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    """Cursor monocromo: `mask` (2h, w) bool con la máscara AND arriba y la XOR abajo.

    AND=0/XOR=0 → negro · AND=0/XOR=1 → blanco · AND=1/XOR=0 → transparente ·
    AND=1/XOR=1 → invierte el fondo (así el I-beam se ve sobre fondos claros y oscuros).
    """
    height = mask.shape[0] // 2
    and_mask, xor_mask = mask[:height], mask[height : height * 2]
    pixels = np.zeros((height, mask.shape[1], 4), np.uint8)
    pixels[:, :, :3] = np.where(xor_mask, 255, 0).astype(np.uint8)[:, :, None]
    pixels[:, :, 3] = np.where(and_mask, 0, 255).astype(np.uint8)
    invert = and_mask & xor_mask
    return pixels, (invert if invert.any() else None)


def _bitmap_size(hbitmap: int) -> tuple[int, int] | None:
    bitmap = _BITMAP()
    if not ctypes.windll.gdi32.GetObjectW(hbitmap, ctypes.sizeof(bitmap), ctypes.byref(bitmap)):
        return None
    return int(bitmap.bmWidth), int(bitmap.bmHeight)


def _dib_bgra(hbitmap: int, width: int, height: int) -> np.ndarray | None:
    """Bits de un bitmap GDI como BGRA 32 bpp de arriba a abajo (los monocromos salen B/N)."""
    user32 = ctypes.windll.user32
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
        lines = ctypes.windll.gdi32.GetDIBits(
            hdc, hbitmap, 0, height, ctypes.byref(buffer), ctypes.byref(header), 0
        )
    finally:
        user32.ReleaseDC(None, hdc)
    if lines != height:
        return None
    return np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 4).copy()


def _extract_shape(handle: int) -> CursorSnapshot | None:
    """Forma del cursor del sistema: en color (con o sin alfa) o monocromo (AND/XOR)."""
    icon = _ICONINFO()
    if not ctypes.windll.user32.GetIconInfo(handle, ctypes.byref(icon)):
        return None
    try:
        mask_size = _bitmap_size(icon.hbmMask) if icon.hbmMask else None
        if icon.hbmColor:
            size = _bitmap_size(icon.hbmColor)
            if size is None or not (1 <= size[0] <= 256 and 1 <= size[1] <= 256):
                return None
            width, height = size
            color = _dib_bgra(icon.hbmColor, width, height)
            if color is None:
                return None
            and_mask = None
            if mask_size is not None and mask_size[0] == width and mask_size[1] >= height:
                bits = _dib_bgra(icon.hbmMask, width, height)
                and_mask = bits[:, :, 0] > 127 if bits is not None else None
            pixels, invert = shape_from_color(color, and_mask)
        else:
            # monocromo (p. ej. el I-beam del esquema por defecto): AND y XOR apiladas
            if mask_size is None:
                return None
            width, double_height = mask_size
            if not (1 <= width <= 256 and 2 <= double_height <= 512):
                return None
            bits = _dib_bgra(icon.hbmMask, width, double_height)
            if bits is None:
                return None
            pixels, invert = shape_from_mono(bits[:, :, 0] > 127)
        return CursorSnapshot(0, 0, int(icon.xHotspot), int(icon.yHotspot), pixels, invert)
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
    dst = out[dy0:dy1, dx0:dx1]
    alpha = (patch[:, :, 3:4].astype(np.uint16))
    if alpha.any():
        blended = (patch[:, :, :3].astype(np.uint16) * alpha + dst[:, :, :3].astype(np.uint16) * (255 - alpha)) // 255
        dst[:, :, :3] = blended.astype(np.uint8)
    if snapshot.invert is not None:
        invert = snapshot.invert[sy0 : sy0 + (dy1 - dy0), sx0 : sx0 + (dx1 - dx0)]
        dst[invert, :3] = 255 - dst[invert, :3]
    return out
