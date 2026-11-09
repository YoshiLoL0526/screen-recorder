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
class CameraInfo:
    """Archivo de cámara de una grabación (docs/49). `offset_s` alinea el PiP."""

    path: Path
    device: str = ""
    offset_s: float = 0.0
    duration_s: float = 0.0


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
    camera: Path | None = None
    camera_duration_s: float = 0.0
    camera_offset_s: float = 0.0

    @property
    def name(self) -> str:
        return self.mp4.stem

    @property
    def has_camera(self) -> bool:
        return self.camera is not None

    @property
    def recovered(self) -> bool:
        """MP4 rehecho desde una sesión interrumpida (`recover_session`)."""
        return "-recuperada" in self.mp4.stem

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


def _sidecar_camera(folder: Path, data: dict) -> CameraInfo | None:
    """Resuelve `camera.file` del sidecar (nombre relativo) a un archivo existente."""
    raw = data.get("camera")
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("file") or "").strip()
    if not name:
        return None
    path = folder / Path(name).name  # solo el nombre: el sidecar nunca apunta fuera
    if not path.is_file():
        return None
    try:
        offset = float(raw.get("offset_s") or 0.0)
        duration = float(raw.get("duration_s") or 0.0)
    except (TypeError, ValueError):
        offset, duration = 0.0, 0.0
    return CameraInfo(
        path=path,
        device=str(raw.get("device") or ""),
        offset_s=offset,
        duration_s=duration,
    )


def camera_info(mp4: str | Path) -> CameraInfo | None:
    """Cámara de una grabación a partir de su sidecar (o `None`)."""
    mp4 = Path(mp4)
    sidecar = mp4.with_suffix(".json")
    if not sidecar.is_file():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return _sidecar_camera(mp4.parent, data)


def list_recordings(out_dir: str | Path) -> list[Recording]:
    """Todos los MP4 de la carpeta (con sidecar/miniatura si existen), el más nuevo primero."""
    if not str(out_dir).strip():  # carpeta sin configurar: no listar el directorio actual
        return []
    folder = Path(out_dir)
    if not folder.exists():
        return []
    items: list[Recording] = []
    for mp4 in folder.glob("*.mp4"):
        if mp4.stem.endswith("-camara"):
            continue  # el vídeo de la cámara es un hermano de su grabación, no otra grabación
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
        camera = _sidecar_camera(folder, data)
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
                camera=camera.path if camera is not None else None,
                camera_duration_s=camera.duration_s if camera is not None else 0.0,
                camera_offset_s=camera.offset_s if camera is not None else 0.0,
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
    """Borra (a la papelera) el mp4 y sus hermanos: miniatura, sidecar y cámara."""
    removed: list[Path] = []
    for path in (recording.mp4, recording.thumb, recording.sidecar, recording.camera):
        if path is not None and path.exists() and move_to_trash(path):
            removed.append(path)
    return removed
