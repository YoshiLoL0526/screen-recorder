"""Biblioteca de grabaciones: listado, borrado (a la papelera) y acciones de sistema."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .recording import scan_recoverable


@dataclass(frozen=True)
class Recording:
    mp4: Path
    thumb: Path | None
    sidecar: Path | None
    duration_s: float
    width: int
    height: int
    fps: float
    size_bytes: int
    created_at: float  # epoch (mtime del mp4)
    encoder: str = "?"

    @property
    def name(self) -> str:
        return self.mp4.stem

    @property
    def size_mb(self) -> float:
        return self.size_bytes / 1e6

    @property
    def duration_label(self) -> str:
        total = int(round(self.duration_s))
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    @property
    def created_label(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.created_at))


def list_recordings(out_dir: str | Path) -> list[Recording]:
    """Todos los MP4 de la carpeta (con sidecar/miniatura si existen), el más nuevo primero."""
    folder = Path(out_dir)
    if not folder.exists():
        return []
    items: list[Recording] = []
    for mp4 in folder.glob("*.mp4"):
        if "-recuperada" in mp4.stem:
            continue  # las recuperadas se listan por su cuenta
        sidecar = mp4.with_suffix(".json")
        thumb = mp4.with_suffix(".jpg")
        data: dict = {}
        if sidecar.exists():
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        try:
            stat = mp4.stat()
        except OSError:
            continue
        items.append(
            Recording(
                mp4=mp4,
                thumb=thumb if thumb.exists() else None,
                sidecar=sidecar if sidecar.exists() else None,
                duration_s=float(data.get("duration_s") or 0.0),
                width=int(data.get("width") or 0),
                height=int(data.get("height") or 0),
                fps=float(data.get("fps") or 0.0),
                size_bytes=stat.st_size,
                created_at=stat.st_mtime,
                encoder=str(data.get("encoder") or "?"),
            )
        )
    items.sort(key=lambda r: r.created_at, reverse=True)
    return items


def list_recoverable() -> list[Path]:
    return scan_recoverable()


def open_file(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def reveal_in_explorer(path: Path) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def move_to_trash(path: Path) -> bool:
    """Manda a la papelera de reciclaje (Windows) o borra (otros sistemas)."""
    if not path.exists():
        return True
    if sys.platform == "win32":
        class SHFILEOPSTRUCTW(ctypes.Structure):  # noqa: N801
            _fields_ = [
                ("hwnd", ctypes.c_void_p),
                ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_uint16),
                ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p),
            ]

        FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT = 3, 0x0040, 0x0010, 0x0004
        op = SHFILEOPSTRUCTW(
            hwnd=None,
            wFunc=FO_DELETE,
            pFrom=str(path) + "\0\0",
            pTo=None,
            fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT,
            fAnyOperationsAborted=0,
            hNameMappings=None,
            lpszProgressTitle=None,
        )
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return result == 0
    try:
        path.unlink()
        return True
    except OSError:
        return False


def delete_recording_files(recording: Recording) -> list[Path]:
    """Borra (a la papelera) el mp4 y sus hermanos: miniatura y sidecar."""
    removed: list[Path] = []
    for path in (recording.mp4, recording.thumb, recording.sidecar):
        if path is not None and path.exists() and move_to_trash(path):
            removed.append(path)
    return removed
