"""Capa de sistema operativo (captura, audio, FFmpeg, rutas).

Subpaquete de `recstudio`: no interfiere con el módulo `platform` de la biblioteca estándar
(los imports absolutos de terceros siguen resolviendo el stdlib).
"""

from __future__ import annotations

__all__ = ["audio", "capture", "ffmpeg", "paths", "screens"]
