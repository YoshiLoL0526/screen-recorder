"""Rutas de la aplicación (configuración, cache, videos, temporales)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "RecStudio"


def _env_path(var: str, fallback: Path) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else fallback


def app_dir() -> Path:
    """Configuración: %APPDATA%/RecStudio (o ~/.config/RecStudio fuera de Windows)."""
    base = _env_path("APPDATA", Path.home() / ".config")
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    base = _env_path("LOCALAPPDATA", Path.home() / ".cache")
    path = base / APP_NAME / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    base = _env_path("LOCALAPPDATA", Path.home() / ".local" / "state")
    path = base / APP_NAME / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def temp_root() -> Path:
    """Sesiones de grabación en curso (recuperables si hay crash)."""
    if sys.platform == "win32":
        base = _env_path("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        path = base / "Temp" / APP_NAME.lower()
    else:
        import tempfile

        path = Path(tempfile.gettempdir()) / APP_NAME.lower()
    path.mkdir(parents=True, exist_ok=True)
    return path


def videos_dir() -> Path:
    """Destino por defecto de las grabaciones."""
    if sys.platform == "win32":
        path = Path.home() / "Videos" / APP_NAME
    else:
        path = Path.home() / "Videos" / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return app_dir() / "settings.json"
