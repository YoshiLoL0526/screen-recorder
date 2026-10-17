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


# -- selector de carpeta (F5) ----------------------------------------------------------

_BIF_RETURNONLYFSDIRS = 0x0001
_BIF_NEWDIALOGSTYLE = 0x0040
_BFFM_INITIALIZED = 1
_BFFM_SETSELECTIONW = 0x0467

_CALLBACK_TYPE = ctypes.WINFUNCTYPE if sys.platform == "win32" else ctypes.CFUNCTYPE
_BFFCALLBACK = _CALLBACK_TYPE(ctypes.c_int, wintypes.HWND, wintypes.UINT, wintypes.LPARAM, wintypes.LPARAM)


class _BROWSEINFOW(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("hwndOwner", wintypes.HWND),
        ("pidlRoot", ctypes.c_void_p),
        ("pszDisplayName", wintypes.LPWSTR),
        ("lpszTitle", wintypes.LPCWSTR),
        ("ulFlags", wintypes.UINT),
        ("lpfn", _BFFCALLBACK),
        ("lParam", wintypes.LPARAM),
        ("iImage", ctypes.c_int),
    ]


def pick_folder(initial_dir: str | Path | None = None, *, title: str = "Elegir carpeta") -> Path | None:
    """Diálogo «Buscar carpeta» de Windows (`SHBrowseForFolderW`); `None` si se cancela.

    El estilo nuevo (con «Crear carpeta») exige COM inicializado en el hilo que lo abre: el
    controlador siempre lo llama desde un hilo propio, así que se inicializa aquí.
    """
    if sys.platform != "win32":
        return None
    ole32, shell32, user32 = ctypes.windll.ole32, ctypes.windll.shell32, ctypes.windll.user32
    ole32.CoInitialize(None)
    try:
        start = str(initial_dir) if initial_dir and Path(initial_dir).is_dir() else None
        user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR)

        def on_event(hwnd, msg, _lparam, _data):  # noqa: ANN001, ANN202
            if msg == _BFFM_INITIALIZED and start:
                user32.SendMessageW(hwnd, _BFFM_SETSELECTIONW, 1, start)
            return 0

        callback = _BFFCALLBACK(on_event)  # referencia viva mientras dure el diálogo
        display = ctypes.create_unicode_buffer(260)
        info = _BROWSEINFOW()
        info.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
        info.lpszTitle = title
        info.ulFlags = _BIF_RETURNONLYFSDIRS | _BIF_NEWDIALOGSTYLE
        info.lpfn = callback
        shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(info))
        if not pidl:
            return None
        try:
            path = ctypes.create_unicode_buffer(1024)
            shell32.SHGetPathFromIDListW.argtypes = (ctypes.c_void_p, wintypes.LPWSTR)
            if not shell32.SHGetPathFromIDListW(pidl, path):
                return None
            return Path(path.value) if path.value else None
        finally:
            ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
    finally:
        ole32.CoUninitialize()
