"""Núcleo puro: modelo, configuración y errores. Sin UI ni acceso al sistema."""

from __future__ import annotations

from .config import PRESETS, AudioConfig, RecordingConfig, SourceConfig
from .errors import (
    AudioError,
    CaptureError,
    ConfigError,
    DiskSpaceError,
    FfmpegError,
    RecStudioError,
)

__all__ = [
    "PRESETS",
    "AudioConfig",
    "AudioError",
    "CaptureError",
    "ConfigError",
    "DiskSpaceError",
    "FfmpegError",
    "RecStudioError",
    "RecordingConfig",
    "SourceConfig",
]
