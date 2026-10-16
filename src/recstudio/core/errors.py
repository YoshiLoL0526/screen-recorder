"""Errores de dominio.

`platform/` traduce las excepciones del sistema a esta jerarquía; la UI (F2+) los convierte en
estados y avisos. Nunca se muestra un traceback crudo al usuario final.
"""

from __future__ import annotations

from typing import Any


class RecStudioError(Exception):
    """Base de todos los errores de dominio."""

    code = "recstudio"

    def __init__(self, message: str, *, details: str | None = None, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.extra = extra

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class ConfigError(RecStudioError):
    code = "config"


class CaptureError(RecStudioError):
    code = "capture"


class AudioError(RecStudioError):
    code = "audio"


class FfmpegError(RecStudioError):
    code = "ffmpeg"


class DiskSpaceError(RecStudioError):
    code = "disk"
