"""Diálogos nativos del sistema (Windows: `GetOpenFileNameW` por ctypes).

Aurora 0.3 no tiene diálogos de archivo; el de Windows es modal respecto al escritorio pero no
bloquea la UI si se llama desde un hilo (el controlador lo lanza siempre fuera del hilo de UI).
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

VIDEO_FILTER = "Videos (*.mp4;*.mkv;*.mov;*.webm;*.avi)\0*.mp4;*.mkv;*.mov;*.webm;*.avi\0Todos los archivos\0*.*\0\0"

_OFN_EXPLORER = 0x00080000
_OFN_FILEMUSTEXIST = 0x00001000
_OFN_PATHMUSTEXIST = 0x00000800
_OFN_ALLOWMULTISELECT = 0x00000200
_OFN_NOCHANGEDIR = 0x00000008


class _OPENFILENAMEW(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


def _split_multi(raw: str) -> list[Path]:
    """`carpeta\\0a.mp4\\0b.mp4\\0\\0` (multi) o `ruta\\0\\0` (uno solo)."""
    parts = [p for p in raw.split("\0") if p]
    if len(parts) <= 1:
        return [Path(p) for p in parts]
    folder = Path(parts[0])
    return [folder / name for name in parts[1:]]


def open_videos(initial_dir: str | Path | None = None, *, title: str = "Importar video") -> list[Path]:
    """Diálogo «Abrir» de Windows con selección múltiple; `[]` si se cancela (o fuera de Windows)."""
    if sys.platform != "win32":
        return []
    size = 32_768
    buffer = ctypes.create_unicode_buffer(size)
    ofn = _OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(_OPENFILENAMEW)
    ofn.lpstrFilter = VIDEO_FILTER
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    ofn.nMaxFile = size
    ofn.lpstrInitialDir = str(initial_dir) if initial_dir else None
    ofn.lpstrTitle = title
    ofn.Flags = _OFN_EXPLORER | _OFN_FILEMUSTEXIST | _OFN_PATHMUSTEXIST | _OFN_ALLOWMULTISELECT | _OFN_NOCHANGEDIR
    if not ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return []
    raw = ctypes.wstring_at(ctypes.addressof(buffer), size)
    end = raw.find("\0\0")
    return _split_multi(raw[: end + 1] if end >= 0 else raw)
