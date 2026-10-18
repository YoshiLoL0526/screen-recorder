"""Rutas de la aplicación (configuración, cache, videos, temporales)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "RecStudio"

# Windows bloqueó la carpeta de grabaciones por defecto: (bloqueada, de reserva).
_videos_blocked: tuple[Path, Path] | None = None


def _env_path(var: str, fallback: Path) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else fallback


def ensure_dir(path: Path) -> bool:
    """Crea `path` (con sus padres) y dice si quedó utilizable. Nunca lanza.

    El «Acceso controlado a carpetas» de Windows Defender deniega la escritura en Videos,
    Documentos, Imágenes, Música y Favoritos a los procesos que no considera de confianza, y lo
    hace devolviendo «no se puede encontrar el archivo especificado» (WinError 2) en vez de
    «acceso denegado». Tratar cualquier `OSError` como «esta carpeta no sirve» evita que un
    bloqueo de seguridad tumbe el arranque de la app (ver `docs/09`).
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return True


def app_dir() -> Path:
    """Configuración: %APPDATA%/RecStudio (o ~/.config/RecStudio fuera de Windows)."""
    base = _env_path("APPDATA", Path.home() / ".config")
    path = base / APP_NAME
    ensure_dir(path)
    return path


def cache_dir() -> Path:
    base = _env_path("LOCALAPPDATA", Path.home() / ".cache")
    path = base / APP_NAME / "cache"
    ensure_dir(path)
    return path


def logs_dir() -> Path:
    base = _env_path("LOCALAPPDATA", Path.home() / ".local" / "state")
    path = base / APP_NAME / "logs"
    ensure_dir(path)
    return path


def temp_root() -> Path:
    """Sesiones de grabación en curso (recuperables si hay crash)."""
    if sys.platform == "win32":
        base = _env_path("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        path = base / "Temp" / APP_NAME.lower()
    else:
        import tempfile

        path = Path(tempfile.gettempdir()) / APP_NAME.lower()
    ensure_dir(path)
    return path


def videos_dir() -> Path:
    """Destino por defecto de las grabaciones (`~/Videos/RecStudio`).

    Si Windows no deja crear esa carpeta (ver `ensure_dir`) se usa
    `%LOCALAPPDATA%/RecStudio/videos` y el bloqueo queda anotado en
    `take_blocked_videos_dir()` para que la UI avise al arrancar. Nunca lanza: se llamaba al
    importar `state` y una carpeta protegida impedía abrir la app.
    """
    global _videos_blocked
    preferred = Path.home() / "Videos" / APP_NAME
    if ensure_dir(preferred):
        _videos_blocked = None
        return preferred

    fallback = _env_path("LOCALAPPDATA", Path.home() / ".local" / "share") / APP_NAME / "videos"
    ensure_dir(fallback)
    _videos_blocked = (preferred, fallback)
    return fallback


def take_blocked_videos_dir() -> tuple[Path, Path] | None:
    """(carpeta bloqueada, carpeta de reserva) y olvida el aviso; None si no hubo bloqueo.

    Devuelve el aviso una sola vez para que el arranque no repita el mismo toast.
    """
    global _videos_blocked
    blocked, _videos_blocked = _videos_blocked, None
    return blocked


def settings_path() -> Path:
    return app_dir() / "settings.json"
