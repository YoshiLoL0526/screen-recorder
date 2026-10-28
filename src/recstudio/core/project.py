"""Modelo del proyecto del editor (puro y `frozen`, ver docs/04-editor.md).

Todo es inmutable: comparable con `==` (lo exige `Signal.set`) y apto para el undo por
snapshots. Las operaciones de edición viven en `core/timeline.py` y devuelven proyectos nuevos.

Convenciones:
- Tiempos en segundos (float). `Clip.in_`/`out_` son tiempos **de la fuente**; la duración en el
  timeline es `(out_ - in_) / speed`.
- `Track.transitions[k]` une el clip `k` con el `k + 1` (transición *saliente* del clip `k`).
- El track de video es una secuencia sin huecos (timeline magnético): el audio propio de cada
  clip va enlazado a él. Las pistas de `audio` (música/segunda voz) se modelan y el export las
  mezcla (`core/graph.py`), pero aún no tienen UI (backlog post-v1).
- Efectos por clip: ver `core/effects.py` (como mucho un `Effect` de cada tipo).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from .errors import ProjectError

SCHEMA_VERSION = 2  # v2: textos y ducking de pistas de audio (B1/B2)
FPS_CHOICES = (24, 30, 60)
SPEED_MIN, SPEED_MAX = 0.25, 4.0
TRANSITION_MIN, TRANSITION_MAX = 0.2, 2.0
TRANSITION_KINDS = (
    "fade", "fadeblack", "fadewhite", "wipeleft", "wiperight", "slideleft", "circleopen", "dissolve",
)
EFFECT_KINDS = ("eq", "volume", "fade_in", "fade_out", "speed", "zoom")
TEXT_SIZE_MIN, TEXT_SIZE_MAX = 8, 200
_TEXT_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
# Holgura al comparar con la duración del asset (los probes redondean a µs).
_EPS = 1e-6


def new_id(prefix: str = "") -> str:
    """Identificador corto (8 hex) con prefijo opcional: `c-1a2b3c4d`."""
    short = uuid.uuid4().hex[:8]
    return f"{prefix}-{short}" if prefix else short


@dataclass(frozen=True)
class MediaAsset:
    id: str
    path: str
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    audio_channels: int = 2
    rotation: int = 0

    @property
    def name(self) -> str:
        from pathlib import PurePath

        return PurePath(self.path).stem


@dataclass(frozen=True)
class Effect:
    kind: Literal["eq", "volume", "fade_in", "fade_out", "speed", "zoom"]
    params: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in EFFECT_KINDS:
            raise ProjectError(f"efecto desconocido: {self.kind}")


@dataclass(frozen=True)
class Transition:
    kind: Literal[
        "fade", "fadeblack", "fadewhite", "wipeleft", "wiperight", "slideleft", "circleopen", "dissolve"
    ] = "fade"
    duration: float = 0.5

    def __post_init__(self) -> None:
        if self.kind not in TRANSITION_KINDS:
            raise ProjectError(f"transición desconocida: {self.kind}")
        if not self.duration > 0:
            raise ProjectError("la transición debe durar más de 0 s")


@dataclass(frozen=True)
class TextOverlay:
    """Texto quemado sobre el video (B2). `x`/`y` son el **centro** en fracciones del frame."""

    id: str
    text: str
    start: float
    end: float
    x: float = 0.5
    y: float = 0.82
    size: int = 42
    color: str = "#FFFFFF"
    bold: bool = False

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ProjectError("el texto no puede estar vacío")
        if self.start < 0 or not self.end > self.start:
            raise ProjectError(f"texto {self.id}: intervalo inválido ({self.start}–{self.end})")
        if not 0.0 <= self.x <= 1.0 or not 0.0 <= self.y <= 1.0:
            raise ProjectError(f"texto {self.id}: posición fuera del frame ({self.x}, {self.y})")
        if not TEXT_SIZE_MIN <= int(self.size) <= TEXT_SIZE_MAX:
            raise ProjectError(f"texto {self.id}: tamaño fuera de [{TEXT_SIZE_MIN}, {TEXT_SIZE_MAX}]")
        if not _TEXT_COLOR.match(self.color):
            raise ProjectError(f"texto {self.id}: color inválido ({self.color!r})")

    @property
    def seconds(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Clip:
    id: str
    asset_id: str
    in_: float
    out_: float
    speed: float = 1.0
    effects: tuple[Effect, ...] = ()

    def __post_init__(self) -> None:
        if self.in_ < 0:
            raise ProjectError(f"clip {self.id}: entrada negativa ({self.in_})")
        if not self.out_ > self.in_:
            raise ProjectError(f"clip {self.id}: la salida ({self.out_}) debe ser mayor que la entrada ({self.in_})")
        if not SPEED_MIN - _EPS <= self.speed <= SPEED_MAX + _EPS:
            raise ProjectError(f"clip {self.id}: velocidad fuera de rango ({self.speed})")

    @property
    def duration(self) -> float:
        """Duración en el timeline (con la velocidad aplicada)."""
        return (self.out_ - self.in_) / self.speed

    @property
    def source_duration(self) -> float:
        return self.out_ - self.in_


@dataclass(frozen=True)
class Track:
    kind: Literal["video", "audio"] = "video"
    clips: tuple[Clip, ...] = ()
    transitions: tuple[Transition | None, ...] = ()
    muted: bool = False
    gain: float = 1.0
    ducking: float = 0.0  # 0…1: cuánto baja la música cuando hay voz (solo pistas de audio)

    def __post_init__(self) -> None:
        if len(self.transitions) != max(0, len(self.clips) - 1):
            raise ProjectError(
                f"pista {self.kind}: {len(self.transitions)} transiciones para {len(self.clips)} clips"
            )
        if not 0.0 <= self.ducking <= 1.0:
            raise ProjectError(f"pista {self.kind}: ducking fuera de [0, 1] ({self.ducking})")

    def index_of(self, clip_id: str) -> int:
        for index, clip in enumerate(self.clips):
            if clip.id == clip_id:
                return index
        raise ProjectError(f"no existe el clip {clip_id}")


@dataclass(frozen=True)
class Project:
    schema_version: int = SCHEMA_VERSION
    name: str = "Sin título"
    assets: tuple[MediaAsset, ...] = ()
    video: Track = field(default_factory=lambda: Track("video"))
    audio: tuple[Track, ...] = ()
    texts: tuple[TextOverlay, ...] = ()
    fps: int = 60
    width: int = 1920
    height: int = 1080

    # -- consultas -------------------------------------------------------------

    def asset(self, asset_id: str) -> MediaAsset:
        for asset in self.assets:
            if asset.id == asset_id:
                return asset
        raise ProjectError(f"no existe el asset {asset_id}")

    def asset_for_path(self, path: str) -> MediaAsset | None:
        from pathlib import Path

        target = Path(path)
        for asset in self.assets:
            if Path(asset.path) == target:
                return asset
        return None

    def clip(self, clip_id: str) -> Clip:
        return self.video.clips[self.video.index_of(clip_id)]

    def has_clip(self, clip_id: str) -> bool:
        return any(c.id == clip_id for c in self.video.clips)

    def text(self, text_id: str) -> TextOverlay:
        for overlay in self.texts:
            if overlay.id == text_id:
                return overlay
        raise ProjectError(f"no existe el texto {text_id}")

    def has_text(self, text_id: str) -> bool:
        return any(t.id == text_id for t in self.texts)

    # -- validación ------------------------------------------------------------

    def validate(self) -> Project:
        """Comprueba los invariantes de docs/04 y devuelve el propio proyecto (o `ProjectError`)."""
        if self.fps <= 0 or self.width <= 0 or self.height <= 0:
            raise ProjectError("fps y resolución del proyecto deben ser positivos")
        ids = [a.id for a in self.assets]
        if len(set(ids)) != len(ids):
            raise ProjectError("ids de asset duplicados")
        by_id = {a.id: a for a in self.assets}
        clip_ids: list[str] = []
        for track in (self.video, *self.audio):
            for clip in track.clips:
                clip_ids.append(clip.id)
                asset = by_id.get(clip.asset_id)
                if asset is None:
                    raise ProjectError(f"el clip {clip.id} usa un asset inexistente ({clip.asset_id})")
                if clip.out_ > asset.duration + _EPS:
                    raise ProjectError(
                        f"clip {clip.id}: salida {clip.out_:.3f}s más allá del final del asset ({asset.duration:.3f}s)"
                    )
            for index, transition in enumerate(track.transitions):
                if transition is None:
                    continue
                limit = transition_limit(track, index)
                if transition.duration < TRANSITION_MIN - _EPS or transition.duration > limit + _EPS:
                    raise ProjectError(
                        f"transición {index}: {transition.duration:.2f}s fuera de [{TRANSITION_MIN}, {limit:.2f}]"
                    )
        if len(set(clip_ids)) != len(clip_ids):
            raise ProjectError("ids de clip duplicados")
        text_ids = [t.id for t in self.texts]
        if len(set(text_ids)) != len(text_ids):
            raise ProjectError("ids de texto duplicados")
        return self


def transition_limit(track: Track, index: int) -> float:
    """Duración máxima de `transitions[index]`: `min(dur_a, dur_b) / 2` (y el tope de 2 s)."""
    a, b = track.clips[index], track.clips[index + 1]
    return min(TRANSITION_MAX, min(a.duration, b.duration) / 2)


# -- serialización (JSON del .rsproj) ------------------------------------------


def to_dict(project: Project) -> dict[str, Any]:
    def clip_dict(c: Clip) -> dict[str, Any]:
        return {
            "id": c.id, "asset_id": c.asset_id, "in_": c.in_, "out_": c.out_, "speed": c.speed,
            "effects": [{"kind": e.kind, "params": [list(p) for p in e.params]} for e in c.effects],
        }

    def track_dict(t: Track) -> dict[str, Any]:
        return {
            "kind": t.kind,
            "clips": [clip_dict(c) for c in t.clips],
            "transitions": [None if tr is None else {"kind": tr.kind, "duration": tr.duration} for tr in t.transitions],
            "muted": t.muted,
            "gain": t.gain,
            "ducking": t.ducking,
        }

    return {
        "schema_version": project.schema_version,
        "name": project.name,
        "fps": project.fps,
        "width": project.width,
        "height": project.height,
        "assets": [
            {
                "id": a.id, "path": a.path, "duration": a.duration, "width": a.width, "height": a.height,
                "fps": a.fps, "has_audio": a.has_audio, "audio_channels": a.audio_channels,
                "rotation": a.rotation,
            }
            for a in project.assets
        ],
        "video": track_dict(project.video),
        "audio": [track_dict(t) for t in project.audio],
        "texts": [
            {
                "id": t.id, "text": t.text, "start": t.start, "end": t.end,
                "x": t.x, "y": t.y, "size": t.size, "color": t.color, "bold": t.bold,
            }
            for t in project.texts
        ],
    }


def from_dict(data: dict[str, Any]) -> Project:
    """Inverso de `to_dict`; `ProjectError` si falta algo o los tipos no cuadran."""
    try:
        def clip_of(d: dict[str, Any]) -> Clip:
            return Clip(
                id=str(d["id"]),
                asset_id=str(d["asset_id"]),
                in_=float(d["in_"]),
                out_=float(d["out_"]),
                speed=float(d.get("speed", 1.0)),
                effects=tuple(
                    Effect(kind=e["kind"], params=tuple((str(k), float(v)) for k, v in e.get("params", ())))
                    for e in d.get("effects", ())
                ),
            )

        def track_of(d: dict[str, Any], kind: str) -> Track:
            return Track(
                kind=d.get("kind", kind),
                clips=tuple(clip_of(c) for c in d.get("clips", ())),
                transitions=tuple(
                    None if t is None else Transition(kind=t["kind"], duration=float(t["duration"]))
                    for t in d.get("transitions", ())
                ),
                muted=bool(d.get("muted", False)),
                gain=float(d.get("gain", 1.0)),
                ducking=float(d.get("ducking", 0.0)),
            )

        assets = tuple(
            MediaAsset(
                id=str(a["id"]),
                path=str(a["path"]),
                duration=float(a["duration"]),
                width=int(a["width"]),
                height=int(a["height"]),
                fps=float(a["fps"]),
                has_audio=bool(a["has_audio"]),
                audio_channels=int(a.get("audio_channels", 2)),
                rotation=int(a.get("rotation", 0)),
            )
            for a in data.get("assets", ())
        )
        texts = tuple(
            TextOverlay(
                id=str(t["id"]), text=str(t["text"]), start=float(t["start"]), end=float(t["end"]),
                x=float(t.get("x", 0.5)), y=float(t.get("y", 0.82)), size=int(t.get("size", 42)),
                color=str(t.get("color", "#FFFFFF")), bold=bool(t.get("bold", False)),
            )
            for t in data.get("texts", ())
        )
        return Project(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            name=str(data.get("name", "Sin título")),
            assets=assets,
            video=track_of(data.get("video", {}), "video"),
            audio=tuple(track_of(t, "audio") for t in data.get("audio", ())),
            texts=texts,
            fps=int(data.get("fps", 60)),
            width=int(data.get("width", 1920)),
            height=int(data.get("height", 1080)),
        )
    except ProjectError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectError(f"proyecto ilegible: {exc}") from exc


def with_video(project: Project, track: Track) -> Project:
    return replace(project, video=track)
