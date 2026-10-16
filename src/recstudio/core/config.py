"""Configuración de grabación (frozen: comparable y hashable, como exige `Signal`).

Los presets definen la calidad de codificación (cq/crf) y un límite de altura; la resolución
nativa se mantiene salvo que supere ese límite (entonces FFmpeg escala en el pipe).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from .errors import ConfigError

PRESETS: dict[str, dict[str, int]] = {
    "baja": {"max_height": 720, "cq": 26, "crf": 23},
    "media": {"max_height": 1080, "cq": 24, "crf": 21},
    "alta": {"max_height": 1080, "cq": 21, "crf": 20},
    "insane": {"max_height": 1440, "cq": 18, "crf": 18},
}

FPS_CHOICES = (15, 24, 30, 48, 60, 120)
BACKENDS = (None, "wgc", "dxgi", "dxcam", "mss")

SourceKind = Literal["monitor", "window", "region"]


CursorMode = Literal["software", "native", "off"]


@dataclass(frozen=True)
class SourceConfig:
    """Qué se graba.

    `region` es (x, y, ancho, alto) en píxeles físicos **relativos al monitor** elegido.
    `monitor_index` es 1-based (como `windows-capture` y `mss`). `cursor`:
    `"software"` (propio: sin la latencia variable de WGC), `"native"` (lo compone Windows)
    u `"off"`.
    """

    kind: SourceKind = "monitor"
    monitor_index: int = 1
    window_title: str | None = None
    region: tuple[int, int, int, int] | None = None
    cursor: CursorMode = "software"
    backend: str | None = None  # None = automático (ver platform.capture.create_backend)

    def validate(self) -> "SourceConfig":
        if self.backend not in BACKENDS:
            raise ConfigError(f"backend desconocido: {self.backend!r}")
        if self.cursor not in ("software", "native", "off"):
            raise ConfigError(f"cursor desconocido: {self.cursor!r}")
        if self.kind == "monitor":
            if self.monitor_index < 1:
                raise ConfigError("monitor_index es 1-based (1 = primario)")
        elif self.kind == "window":
            if not self.window_title:
                raise ConfigError("kind='window' requiere window_title")
        elif self.kind == "region":
            if self.region is None:
                raise ConfigError("kind='region' requiere region=(x, y, w, h)")
            _, _, w, h = self.region
            if w < 8 or h < 8:
                raise ConfigError(f"región demasiado pequeña: {w}x{h}")
        else:
            raise ConfigError(f"kind desconocido: {self.kind!r}")
        return self

    @property
    def is_region(self) -> bool:
        return self.kind == "region" and self.region is not None


@dataclass(frozen=True)
class AudioConfig:
    """Audio a capturar: sistema (loopback), micrófono, o ambos.

    `keepalive` reproduce silencio por la salida por defecto mientras se graba loopback:
    sin él, WASAPI deja de entregar datos cuando no suena nada (F0/S3b: cobertura 14.7 % → 99.96 %).
    """

    system: bool = True
    mic_device_index: int | None = None
    keepalive: bool = True

    @property
    def any(self) -> bool:
        return self.system or self.mic_device_index is not None


@dataclass(frozen=True)
class RecordingConfig:
    fps: int = 60
    preset: str = "media"
    encoder: str | None = None  # None = automático (nvenc → amf → qsv → libx264)
    source: SourceConfig = field(default_factory=SourceConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    max_size_gb: float | None = None
    min_free_disk_mb: int = 500

    def validate(self) -> "RecordingConfig":
        if self.fps not in FPS_CHOICES:
            raise ConfigError(f"fps no soportado: {self.fps} (opciones: {FPS_CHOICES})")
        if self.preset not in PRESETS:
            raise ConfigError(f"preset desconocido: {self.preset!r} (opciones: {tuple(PRESETS)})")
        self.source.validate()
        if self.max_size_gb is not None and self.max_size_gb <= 0:
            raise ConfigError("max_size_gb debe ser positivo")
        return self

    @property
    def quality(self) -> dict[str, int]:
        return PRESETS[self.preset]

    def with_source(self, **changes: object) -> "RecordingConfig":
        return replace(self, source=replace(self.source, **changes))  # type: ignore[arg-type]
