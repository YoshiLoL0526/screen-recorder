"""Preferencias persistentes (`%APPDATA%/RecStudio/settings.json`)."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, fields
from typing import Any

from ..core import graph as graph_mod
from ..platform import paths

_SAVE_LOCK = threading.Lock()


@dataclass
class Settings:
    theme: str = "dark"
    source_kind: str = "monitor"
    monitor_index: int = 1
    window_title: str = ""
    region: tuple[int, int, int, int] = (0, 0, 1280, 720)
    fps: int = 60
    preset: str = "media"
    encoder: str = "auto"
    audio_system: bool = True
    keepalive: bool = True
    mic_index: int = -1
    out_dir: str = ""
    countdown_s: int = 0
    last_project: str = ""  # project.json abierto al cerrar (se reabre al arrancar)
    preview_audio: bool = True
    export_preset: str = "rapido"  # preset del último export (core.graph.PRESETS)
    export_format: str = "mp4"  # mp4 | webm | gif (docs/48)
    # Export personalizado (docs/44): última configuración usada en el modal. Vive aquí, no en
    # `Project`: cambiar la resolución de salida no altera el lienzo del proyecto.
    export_custom_width: int = 1920
    export_custom_height: int = 1080
    export_custom_fps: int = 60
    export_custom_video_bitrate: str = graph_mod.DEFAULT_VIDEO_BITRATE
    export_custom_audio_bitrate: str = graph_mod.DEFAULT_AUDIO_BITRATE
    export_custom_codec: str = "auto"  # auto | x264
    # Escritorio: bandeja, atajos globales y avisos (post-v1)
    global_hotkeys: bool = False  # hook del sistema: opt-in (antivirus, permisos)
    tray: bool = True
    notify_finished: bool = True
    # Disposición del editor (F5): divisores y paneles
    inspector_w: float = 290.0
    inspector_open: bool = True
    timeline_extra: float = 0.0

    def normalized(self) -> "Settings":
        if not self.out_dir:
            self.out_dir = str(paths.videos_dir())
        if not isinstance(self.region, (list, tuple)) or len(self.region) != 4:
            self.region = (0, 0, 1280, 720)
        else:
            self.region = tuple(int(v) for v in self.region)  # type: ignore[assignment]
        try:
            self.inspector_w = min(560.0, max(220.0, float(self.inspector_w)))
            self.timeline_extra = min(420.0, max(-14.0, float(self.timeline_extra)))
        except (TypeError, ValueError):
            self.inspector_w, self.timeline_extra = 290.0, 0.0
        self.inspector_open = bool(self.inspector_open)
        self.global_hotkeys = bool(self.global_hotkeys)
        self.tray = bool(self.tray)
        self.notify_finished = bool(self.notify_finished)
        self._normalize_export()
        return self

    def _normalize_export(self) -> None:
        """Valida la configuración personalizada (docs/44) sin tocar el proyecto."""
        if self.export_preset not in (*graph_mod.PRESET_IDS, graph_mod.CUSTOM_PRESET_ID):
            self.export_preset = "rapido"
        if self.export_format not in graph_mod.EXPORT_FORMATS:
            self.export_format = "mp4"
        self.export_custom_width = _clamp_int(
            self.export_custom_width, 1920, graph_mod.CUSTOM_MIN_DIMENSION,
            graph_mod.CUSTOM_MAX_DIMENSION, even=True,
        )
        self.export_custom_height = _clamp_int(
            self.export_custom_height, 1080, graph_mod.CUSTOM_MIN_DIMENSION,
            graph_mod.CUSTOM_MAX_DIMENSION, even=True,
        )
        self.export_custom_fps = _clamp_int(
            self.export_custom_fps, 60, graph_mod.CUSTOM_FPS_MIN, graph_mod.CUSTOM_FPS_MAX,
        )
        if not _bitrate_in_range(self.export_custom_video_bitrate,
                                 graph_mod.VIDEO_BITRATE_MIN, graph_mod.VIDEO_BITRATE_MAX):
            self.export_custom_video_bitrate = graph_mod.DEFAULT_VIDEO_BITRATE
        if not _bitrate_in_range(self.export_custom_audio_bitrate,
                                 graph_mod.AUDIO_BITRATE_MIN, graph_mod.AUDIO_BITRATE_MAX):
            self.export_custom_audio_bitrate = graph_mod.DEFAULT_AUDIO_BITRATE
        if self.export_custom_codec not in graph_mod.CUSTOM_CODECS:
            self.export_custom_codec = "auto"

    @classmethod
    def load(cls) -> "Settings":
        path = paths.settings_path()
        if not path.exists():
            return cls().normalized()
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            allowed = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in data.items() if k in allowed}).normalized()
        except (json.JSONDecodeError, OSError, TypeError):
            return cls().normalized()

    def save(self) -> None:
        """Escritura atómica (temporal + `os.replace`) y serializada.

        El guardado con debounce corre en un `Timer` y puede coincidir con otro (al cerrar, al
        restablecer): con `write_text` directo, un lector podía ver el archivo truncado (vacío)
        y un cierre a mitad de escritura dejaba los ajustes en blanco.
        """
        path = paths.settings_path()
        payload = json.dumps(asdict(self), indent=2, ensure_ascii=False)
        with _SAVE_LOCK:
            tmp = path.with_name(path.name + ".tmp")
            try:
                tmp.write_text(payload, encoding="utf-8")
                os.replace(tmp, path)
            except OSError:
                pass


def _clamp_int(value: Any, default: int, minimum: int, maximum: int, *, even: bool = False) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    number = min(maximum, max(minimum, number))
    if even:
        number -= number % 2
    return number


def _bitrate_in_range(value: Any, minimum: int, maximum: int) -> bool:
    bits = graph_mod.parse_bitrate(str(value) if value is not None else None)
    return bits is not None and minimum <= bits <= maximum
