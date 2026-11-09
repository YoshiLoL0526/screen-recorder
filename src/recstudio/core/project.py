"""Modelo del proyecto del editor (puro y `frozen`, ver docs/04-editor.md).

Todo es inmutable: comparable con `==` (lo exige `Signal.set`) y apto para el undo por
snapshots. Las operaciones de edición viven en `core/timeline.py` y devuelven proyectos nuevos.

Convenciones:
- Tiempos en segundos (float). `Clip.in_`/`out_` son tiempos **de la fuente**; la duración en el
  timeline es `(out_ - in_) / speed`.
- `Track.transitions[k]` une el clip `k` con el `k + 1` (transición *saliente* del clip `k`).
- El track de video es una secuencia sin huecos (timeline magnético): el audio propio de cada
  clip va enlazado a él. Las pistas de `audio`, en cambio, pueden ser `sequence` (música, sin
  huecos y en orden) o `absolute` (audio separado/voz: cada `Clip.start` fija su posición y
  puede haber huecos). Ver docs/19.
- Efectos por clip: ver `core/effects.py` (como mucho un `Effect` de cada tipo).
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from . import animation as anim
from . import audio_envelope as env
from . import text_color as tc
from .errors import ProjectError

SCHEMA_VERSION = 14  # v14: easing de las claves de volumen (docs/40)
FPS_CHOICES = (24, 30, 60)
SPEED_MIN, SPEED_MAX = 0.25, 4.0
TRANSITION_MIN, TRANSITION_MAX = 0.2, 2.0
TRANSITION_KINDS = (
    "fade", "fadeblack", "fadewhite", "wipeleft", "wiperight", "slideleft", "circleopen", "dissolve",
)
EFFECT_KINDS = (
    "eq", "volume", "fade_in", "fade_out", "audio_fade_in", "audio_fade_out", "speed", "zoom",
    # v6 (docs/22): efectos de clip, de elemento y globales.
    "blur", "vignette", "sharpen", "grayscale",
)
ASSET_KINDS = ("video", "audio", "image")
ELEMENT_KINDS = ("image", "video", "rect", "ellipse", "triangle")
ELEMENT_FITS = ("fit", "fill", "stretch")
ELEMENT_SIZE_MIN, ELEMENT_SIZE_MAX = 0.01, 4.0
VIDEO_TRACKS_MAX = 2  # V2 y V3; 0 = capa «Elementos» (imágenes, figuras y PiP suelto)
TRACK_MODES = ("sequence", "absolute")
AUDIO_VIDEO_TRACK_NAME = "Audio del video"
TEXT_SIZE_MIN, TEXT_SIZE_MAX = 8, 200
FIT_MODES = ("fit", "fill", "stretch")
TRANSFORM_FITS = ("inherit", *FIT_MODES)
TRANSFORM_ROTATIONS = (0, 90, 180, 270)
TRANSFORM_SCALE_MIN, TRANSFORM_SCALE_MAX = 0.25, 4.0
DEFAULT_BACKGROUND = "#000000"
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
# Holgura al comparar con la duración del asset (los probes redondean a µs).
_EPS = 1e-6


def new_id(prefix: str = "") -> str:
    """Identificador corto (8 hex) con prefijo opcional: `c-1a2b3c4d`."""
    short = uuid.uuid4().hex[:8]
    return f"{prefix}-{short}" if prefix else short


def normalize_rotation(rotation: float | int) -> int:
    """Grados normalizados a 0/90/180/270 (redondea al cuarto de vuelta más cercano)."""
    try:
        value = float(rotation)
    except (TypeError, ValueError):
        return 0
    if value != value:  # NaN
        return 0
    return (int(round(value / 90.0)) * 90) % 360


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
    # v6 (docs/22): los elementos superpuestos pueden usar imágenes. `duration` es 0 para una
    # imagen; los assets de solo audio mantienen `width=height=0` y `fps=0` (docs/19).
    kind: Literal["video", "audio", "image"] = "video"

    def __post_init__(self) -> None:
        if self.kind not in ASSET_KINDS:
            raise ProjectError(f"asset {self.id}: tipo desconocido ({self.kind!r})")
        if self.kind == "image" and (self.width <= 0 or self.height <= 0):
            raise ProjectError(f"asset {self.id}: una imagen necesita ancho y alto positivos")

    @property
    def name(self) -> str:
        from pathlib import PurePath

        return PurePath(self.path).stem


@dataclass(frozen=True)
class Effect:
    kind: Literal[
        "eq", "volume", "fade_in", "fade_out", "audio_fade_in", "audio_fade_out", "speed", "zoom"
    ]
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
    opacity: float = 1.0  # v9: opacidad base (los keyframes la sustituyen)
    subtitle: bool = False  # v9: cue importada de SRT/VTT (docs/30)
    animations: tuple[anim.Animation, ...] = ()  # v9: x, y, opacity (docs/31); escala/rotación/tamaño (docs/35 y docs/36)
    color_keys: tuple[tc.ColorKeyframe, ...] = ()  # v12: color animado (docs/36)

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ProjectError("el texto no puede estar vacío")
        if self.start < 0 or not self.end > self.start:
            raise ProjectError(f"texto {self.id}: intervalo inválido ({self.start}–{self.end})")
        if not 0.0 <= self.x <= 1.0 or not 0.0 <= self.y <= 1.0:
            raise ProjectError(f"texto {self.id}: posición fuera del frame ({self.x}, {self.y})")
        if not TEXT_SIZE_MIN <= int(self.size) <= TEXT_SIZE_MAX:
            raise ProjectError(f"texto {self.id}: tamaño fuera de [{TEXT_SIZE_MIN}, {TEXT_SIZE_MAX}]")
        if not _HEX_COLOR.match(self.color):
            raise ProjectError(f"texto {self.id}: color inválido ({self.color!r})")
        if not math.isfinite(float(self.opacity)) or not 0.0 <= self.opacity <= 1.0:
            raise ProjectError(f"texto {self.id}: opacidad fuera de [0, 1] ({self.opacity})")
        anim.validate(self.animations, anim.TEXT_CHANNELS)
        tc.validate(self.color_keys)

    @property
    def seconds(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Element:
    """Elemento superpuesto al video (v8, docs/28 y docs/29): imagen, video o figura.

    `x`/`y` son el **centro** en fracciones del lienzo y `width`/`height` el tamaño en fracciones
    del lienzo (no píxeles: así el mismo proyecto se ve igual en preview y export, y al cambiar de
    resolución). `rotation` son grados horarios; `opacity` va de 0 a 1. Las figuras usan `color`;
    las imágenes y los videos usan `asset_id` y `fit` para encajar en la caja. En un video,
    `in_` es el segundo de la fuente por el que empieza el intervalo (el resto se reproduce a 1×).
    `effects` reutiliza `Effect` (`blur` para la imagen y `volume` para el audio del PiP), con
    parámetros float. `envelope` es la envolvente de volumen del audio de un video (claves
    locales al elemento, 0 = su `start`; docs/34). `video_track` agrupa los videos superpuestos
    en las pistas V2/V3 (0 = capa «Elementos»); el orden de `Project.elements` sigue siendo el
    z-order.
    """

    id: str
    kind: Literal["image", "video", "rect", "ellipse", "triangle"]
    start: float
    end: float
    x: float = 0.5
    y: float = 0.5
    width: float = 0.3
    height: float = 0.2
    rotation: float = 0.0
    opacity: float = 1.0
    color: str = "#FFFFFF"
    asset_id: str | None = None
    fit: Literal["fit", "fill", "stretch"] = "fit"
    in_: float = 0.0  # solo video: offset de la fuente
    video_track: int = 0  # 0 = Elementos; 1..VIDEO_TRACKS_MAX = V2/V3
    animations: tuple[anim.Animation, ...] = ()  # x, y, scale, rotation (docs/31 y docs/33)
    effects: tuple[Effect, ...] = ()
    envelope: tuple[env.VolumePoint, ...] = ()  # v11: volumen del audio (docs/34)

    def __post_init__(self) -> None:
        if self.kind not in ELEMENT_KINDS:
            raise ProjectError(f"elemento {self.id}: tipo desconocido ({self.kind!r})")
        if self.start != self.start or self.start < 0 or not self.end > self.start:
            raise ProjectError(f"elemento {self.id}: intervalo inválido ({self.start}–{self.end})")
        if not 0.0 <= self.x <= 1.0 or not 0.0 <= self.y <= 1.0:
            raise ProjectError(f"elemento {self.id}: posición fuera del lienzo ({self.x}, {self.y})")
        for axis, value in (("ancho", self.width), ("alto", self.height)):
            if not ELEMENT_SIZE_MIN <= float(value) <= ELEMENT_SIZE_MAX:
                raise ProjectError(
                    f"elemento {self.id}: {axis} fuera de "
                    f"[{ELEMENT_SIZE_MIN}, {ELEMENT_SIZE_MAX}] ({value})"
                )
        if not math.isfinite(float(self.rotation)):
            raise ProjectError(f"elemento {self.id}: rotación inválida ({self.rotation})")
        if not 0.0 <= self.opacity <= 1.0:
            raise ProjectError(f"elemento {self.id}: opacidad fuera de [0, 1] ({self.opacity})")
        if self.kind in ("image", "video"):
            if not self.asset_id:
                raise ProjectError(f"elemento {self.id}: un {self.kind} necesita `asset_id`")
            if self.fit not in ELEMENT_FITS:
                raise ProjectError(f"elemento {self.id}: encaje desconocido ({self.fit!r})")
        elif not _HEX_COLOR.match(self.color):
            raise ProjectError(f"elemento {self.id}: color inválido ({self.color!r})")
        if self.in_ != self.in_ or self.in_ < 0:
            raise ProjectError(f"elemento {self.id}: entrada de fuente inválida ({self.in_})")
        if not isinstance(self.video_track, int) or not 0 <= self.video_track <= VIDEO_TRACKS_MAX:
            raise ProjectError(
                f"elemento {self.id}: pista de video inválida ({self.video_track!r})"
            )
        if self.video_track and self.kind != "video":
            raise ProjectError(
                f"elemento {self.id}: solo un video puede ir en V{self.video_track + 1}"
            )
        anim.validate(self.animations, anim.ELEMENT_CHANNELS)
        env.validate(self.envelope)
        if self.envelope and self.kind != "video":
            raise ProjectError(
                f"elemento {self.id}: solo un video superpuesto tiene envolvente de audio"
            )
        for effect in self.effects:
            if not isinstance(effect, Effect):
                raise ProjectError(f"elemento {self.id}: efecto inválido")

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Marker:
    """Marcador del timeline (fase 3, docs/21): un instante con nombre, dibujado en la regla."""

    id: str
    t: float
    name: str = ""

    def __post_init__(self) -> None:
        if self.t != self.t or self.t < 0:  # NaN o negativo
            raise ProjectError(f"marcador {self.id}: tiempo inválido ({self.t})")


@dataclass(frozen=True)
class Transform:
    """Transformación opcional de un clip de video (fase 2, docs/20).

    Orden: auto-rotación del asset → rotación/flip del clip → `crop` → encaje (`fit`) →
    `scale` → posición (`x`, `y`) sobre el lienzo. `None` en `Clip.transform` significa «sin
    transformación propia»: se usa el encaje por defecto del proyecto y el resto neutro.
    """

    fit: Literal["inherit", "fit", "fill", "stretch"] = "inherit"
    scale: float = 1.0  # relativa al encaje (0.25…4)
    x: float = 0.5  # centro en fracción del lienzo
    y: float = 0.5
    rotation: int = 0  # grados horarios: 0, 90, 180, 270
    flip_h: bool = False
    flip_v: bool = False
    crop: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)  # l, t, r, b

    def __post_init__(self) -> None:
        if self.fit not in TRANSFORM_FITS:
            raise ProjectError(f"transformación: encaje desconocido ({self.fit!r})")
        if not (TRANSFORM_SCALE_MIN - _EPS <= self.scale <= TRANSFORM_SCALE_MAX + _EPS):
            raise ProjectError(
                f"transformación: escala fuera de [{TRANSFORM_SCALE_MIN}, {TRANSFORM_SCALE_MAX}] "
                f"({self.scale})"
            )
        if not 0.0 <= self.x <= 1.0 or not 0.0 <= self.y <= 1.0:
            raise ProjectError(f"transformación: posición fuera del lienzo ({self.x}, {self.y})")
        if int(self.rotation) not in TRANSFORM_ROTATIONS:
            raise ProjectError(f"transformación: rotación no permitida ({self.rotation})")
        try:
            if len(self.crop) != 4:
                raise ValueError
            left, top, right, bottom = (float(v) for v in self.crop)
        except (TypeError, ValueError) as exc:
            raise ProjectError("transformación: el recorte necesita 4 valores (l, t, r, b)") from exc
        if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
            raise ProjectError(f"transformación: recorte inválido ({self.crop})")


@dataclass(frozen=True)
class Clip:
    id: str
    asset_id: str
    in_: float
    out_: float
    speed: float = 1.0
    effects: tuple[Effect, ...] = ()
    start: float | None = None  # solo en pistas `absolute` (s)
    audio_enabled: bool = True  # solo con sentido en la pista de video
    link_id: str | None = None  # clip de audio separado → clip de video enlazado
    transform: Transform | None = None  # solo con sentido en la pista de video (fase 2)
    envelope: tuple[env.VolumePoint, ...] = ()  # v10: volumen animado (docs/32)

    def __post_init__(self) -> None:
        if self.in_ < 0:
            raise ProjectError(f"clip {self.id}: entrada negativa ({self.in_})")
        if not self.out_ > self.in_:
            raise ProjectError(f"clip {self.id}: la salida ({self.out_}) debe ser mayor que la entrada ({self.in_})")
        if not SPEED_MIN - _EPS <= self.speed <= SPEED_MAX + _EPS:
            raise ProjectError(f"clip {self.id}: velocidad fuera de rango ({self.speed})")
        if self.start is not None and self.start < 0:
            raise ProjectError(f"clip {self.id}: inicio negativo ({self.start})")
        if self.start is not None and self.start != self.start:  # NaN
            raise ProjectError(f"clip {self.id}: inicio inválido ({self.start})")
        if self.transform is not None and not isinstance(self.transform, Transform):
            raise ProjectError(f"clip {self.id}: transformación inválida")
        env.validate(self.envelope)

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
    mode: Literal["sequence", "absolute"] = "sequence"
    name: str = ""
    solo: bool = False
    locked: bool = False

    def __post_init__(self) -> None:
        if len(self.transitions) != max(0, len(self.clips) - 1):
            raise ProjectError(
                f"pista {self.kind}: {len(self.transitions)} transiciones para {len(self.clips)} clips"
            )
        if not 0.0 <= self.ducking <= 1.0:
            raise ProjectError(f"pista {self.kind}: ducking fuera de [0, 1] ({self.ducking})")
        if self.mode not in TRACK_MODES:
            raise ProjectError(f"pista {self.kind}: modo desconocido ({self.mode})")
        if self.mode != "absolute" and any(c.start is not None for c in self.clips):
            raise ProjectError(f"pista {self.kind}: un clip con `start` exige modo absoluto")
        if self.mode == "absolute" and any(t is not None for t in self.transitions):
            raise ProjectError(f"pista {self.kind}: las pistas absolutas no admiten transiciones")

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
    background: str = DEFAULT_BACKGROUND  # color del lienzo (#RRGGBB)
    default_fit: Literal["fit", "fill", "stretch"] = "fit"
    markers: tuple[Marker, ...] = ()  # ordenados por tiempo (fase 3)
    range_in: float | None = None  # In/Out del timeline (loop y export de rango); None = sin punto
    range_out: float | None = None
    # v6 (docs/22): composición. El orden de `elements` es el z-order (el último va encima).
    elements: tuple[Element, ...] = ()
    effects: tuple[Effect, ...] = ()  # efectos globales, se aplican al video ya compuesto

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

    def element(self, element_id: str) -> Element:
        for element in self.elements:
            if element.id == element_id:
                return element
        raise ProjectError(f"no existe el elemento {element_id}")

    def has_element(self, element_id: str) -> bool:
        return any(e.id == element_id for e in self.elements)

    def marker(self, marker_id: str) -> Marker:
        for marker in self.markers:
            if marker.id == marker_id:
                return marker
        raise ProjectError(f"no existe el marcador {marker_id}")

    def has_marker(self, marker_id: str) -> bool:
        return any(m.id == marker_id for m in self.markers)

    # -- validación ------------------------------------------------------------

    def validate(self) -> Project:
        """Comprueba los invariantes de docs/04 y docs/19 y devuelve el proyecto (o `ProjectError`)."""
        if self.fps <= 0 or self.width <= 0 or self.height <= 0:
            raise ProjectError("fps y resolución del proyecto deben ser positivos")
        if self.default_fit not in FIT_MODES:
            raise ProjectError(f"encaje del proyecto desconocido ({self.default_fit!r})")
        if not isinstance(self.background, str) or not _HEX_COLOR.match(self.background):
            raise ProjectError(f"color de fondo inválido ({self.background!r}); usa #RRGGBB")
        ids = [a.id for a in self.assets]
        if len(set(ids)) != len(ids):
            raise ProjectError("ids de asset duplicados")
        by_id = {a.id: a for a in self.assets}
        if self.video.mode != "sequence":
            raise ProjectError("la pista de video debe ser secuencial (modo absoluto es solo para audio)")
        clip_ids: list[str] = []
        all_clips: list[tuple[Track, Clip]] = []
        for track in (self.video, *self.audio):
            if track.kind != ("video" if track is self.video else "audio"):
                raise ProjectError(f"pista con tipo incoherente ({track.kind})")
            for clip in track.clips:
                clip_ids.append(clip.id)
                all_clips.append((track, clip))
                asset = by_id.get(clip.asset_id)
                if asset is None:
                    raise ProjectError(f"el clip {clip.id} usa un asset inexistente ({clip.asset_id})")
                if clip.out_ > asset.duration + _EPS:
                    raise ProjectError(
                        f"clip {clip.id}: salida {clip.out_:.3f}s más allá del final del asset ({asset.duration:.3f}s)"
                    )
            if track.mode == "absolute":
                _validate_absolute_track(track)
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
        video_clips = {c.id: c for c in self.video.clips}
        for track, clip in all_clips:
            if track is self.video:
                if clip.link_id is not None:
                    raise ProjectError(f"clip de video {clip.id}: `link_id` solo vale en pistas de audio")
                continue
            if not clip.audio_enabled:
                raise ProjectError(f"clip de audio {clip.id}: `audio_enabled` solo vale en la pista de video")
            if clip.link_id is not None and clip.link_id not in video_clips:
                raise ProjectError(f"clip de audio {clip.id}: enlace a un clip inexistente ({clip.link_id})")
        # Varios segmentos de un audio dividido pueden compartir el mismo `link_id`.
        text_ids = [t.id for t in self.texts]
        if len(set(text_ids)) != len(text_ids):
            raise ProjectError("ids de texto duplicados")
        element_ids = [e.id for e in self.elements]
        if len(set(element_ids)) != len(element_ids):
            raise ProjectError("ids de elemento duplicados")
        for element in self.elements:
            if element.kind in ("image", "video"):
                asset = by_id.get(element.asset_id or "")
                expected = "imagen" if element.kind == "image" else "video"
                if asset is None:
                    raise ProjectError(
                        f"el elemento {element.id} usa un {expected} inexistente ({element.asset_id})"
                    )
                if asset.kind != element.kind:
                    raise ProjectError(
                        f"el elemento {element.id} debe usar un asset de tipo {element.kind} ({asset.kind})"
                    )
                if element.kind == "video" and element.in_ + element.duration > asset.duration + _EPS:
                    raise ProjectError(
                        f"el elemento {element.id} se sale del final del video "
                        f"({element.in_ + element.duration:.3f}s > {asset.duration:.3f}s)"
                    )
        global_kinds = [e.kind for e in self.effects]
        if len(set(global_kinds)) != len(global_kinds):
            raise ProjectError("efectos globales duplicados (como mucho uno de cada tipo)")
        marker_ids = [m.id for m in self.markers]
        if len(set(marker_ids)) != len(marker_ids):
            raise ProjectError("ids de marcador duplicados")
        for value in (self.range_in, self.range_out):
            if value is not None and (value != value or value < 0):
                raise ProjectError(f"punto In/Out inválido ({value})")
        if (
            self.range_in is not None and self.range_out is not None
            and not self.range_out > self.range_in
        ):
            raise ProjectError("el punto de salida debe ir después del de entrada")
        return self


def transition_limit(track: Track, index: int) -> float:
    """Duración máxima de `transitions[index]`: `min(dur_a, dur_b) / 2` (y el tope de 2 s)."""
    a, b = track.clips[index], track.clips[index + 1]
    return min(TRANSITION_MAX, min(a.duration, b.duration) / 2)


def _validate_absolute_track(track: Track) -> None:
    """Sin solapes dentro de una pista absoluta (los huecos sí valen)."""
    ranges = sorted(
        (clip.start or 0.0, (clip.start or 0.0) + clip.duration, clip.id) for clip in track.clips
    )
    for (start_a, end_a, id_a), (start_b, _end_b, id_b) in zip(ranges, ranges[1:]):
        if start_b < end_a - _EPS:
            raise ProjectError(f"clips solapados en la pista absoluta: {id_a} y {id_b}")


def audio_solo_active(project: Project) -> bool:
    """¿Hay alguna pista de audio en solo? Entonces aísla: lo demás (incluido el video) se silencia."""
    return any(track.solo for track in project.audio)


def video_audio_gain(project: Project) -> float:
    """Ganancia efectiva del audio del video (mute/solo incluidos; ver docs/19)."""
    if project.video.muted or project.video.gain <= 0 or audio_solo_active(project):
        return 0.0
    return project.video.gain


def audio_track_audible(project: Project, track: Track) -> bool:
    """Pista de audio audible: ni mute/ganancia cero ni silenciada por un solo de otra pista."""
    if track.muted or track.gain <= 0:
        return False
    return track.solo or not audio_solo_active(project)


# -- serialización (JSON del .rsproj) ------------------------------------------


def to_dict(project: Project) -> dict[str, Any]:
    def clip_dict(c: Clip) -> dict[str, Any]:
        return {
            "id": c.id, "asset_id": c.asset_id, "in_": c.in_, "out_": c.out_, "speed": c.speed,
            "effects": [{"kind": e.kind, "params": [list(p) for p in e.params]} for e in c.effects],
            "start": c.start, "audio_enabled": c.audio_enabled, "link_id": c.link_id,
            "transform": None if c.transform is None else {
                "fit": c.transform.fit,
                "scale": c.transform.scale,
                "x": c.transform.x,
                "y": c.transform.y,
                "rotation": c.transform.rotation,
                "flip_h": c.transform.flip_h,
                "flip_v": c.transform.flip_v,
                "crop": list(c.transform.crop),
            },
            "envelope": [
                {"t": point.t, "gain": point.gain, "easing": point.easing}
                for point in c.envelope
            ],
        }

    def animation_dict(a: anim.Animation) -> dict[str, Any]:
        return {
            "channel": a.channel,
            "points": [
                {"t": point.t, "value": point.value, "easing": point.easing}
                for point in a.points
            ],
        }

    def track_dict(t: Track) -> dict[str, Any]:
        return {
            "kind": t.kind,
            "clips": [clip_dict(c) for c in t.clips],
            "transitions": [None if tr is None else {"kind": tr.kind, "duration": tr.duration} for tr in t.transitions],
            "muted": t.muted,
            "gain": t.gain,
            "ducking": t.ducking,
            "mode": t.mode,
            "name": t.name,
            "solo": t.solo,
            "locked": t.locked,
        }

    return {
        "schema_version": project.schema_version,
        "name": project.name,
        "fps": project.fps,
        "width": project.width,
        "height": project.height,
        "background": project.background,
        "default_fit": project.default_fit,
        "assets": [
            {
                "id": a.id, "path": a.path, "duration": a.duration, "width": a.width, "height": a.height,
                "fps": a.fps, "has_audio": a.has_audio, "audio_channels": a.audio_channels,
                "rotation": a.rotation, "kind": a.kind,
            }
            for a in project.assets
        ],
        "video": track_dict(project.video),
        "audio": [track_dict(t) for t in project.audio],
        "texts": [
            {
                "id": t.id, "text": t.text, "start": t.start, "end": t.end,
                "x": t.x, "y": t.y, "size": t.size, "color": t.color, "bold": t.bold,
                "opacity": t.opacity, "subtitle": t.subtitle,
                "animations": [animation_dict(a) for a in t.animations],
                "color_keys": [{"t": key.t, "color": key.color} for key in t.color_keys],
            }
            for t in project.texts
        ],
        "markers": [{"id": m.id, "t": m.t, "name": m.name} for m in project.markers],
        "range_in": project.range_in,
        "range_out": project.range_out,
        "elements": [
            {
                "id": e.id, "kind": e.kind, "start": e.start, "end": e.end,
                "x": e.x, "y": e.y, "width": e.width, "height": e.height,
                "rotation": e.rotation, "opacity": e.opacity, "color": e.color,
                "asset_id": e.asset_id, "fit": e.fit, "in": e.in_,
                "video_track": e.video_track,
                "animations": [animation_dict(a) for a in e.animations],
                "effects": [{"kind": fx.kind, "params": [list(p) for p in fx.params]} for fx in e.effects],
                "envelope": [
                    {"t": point.t, "gain": point.gain, "easing": point.easing}
                    for point in e.envelope
                ],
            }
            for e in project.elements
        ],
        "effects": [
            {"kind": fx.kind, "params": [list(p) for p in fx.params]} for fx in project.effects
        ],
    }


def from_dict(data: dict[str, Any]) -> Project:
    """Inverso de `to_dict`; `ProjectError` si falta algo o los tipos no cuadran."""
    try:
        def transform_of(d: dict[str, Any] | None) -> Transform | None:
            if d is None:
                return None
            if not isinstance(d, dict):
                raise ProjectError("transformación ilegible en el proyecto")
            crop = d.get("crop", (0.0, 0.0, 1.0, 1.0))
            return Transform(
                fit=str(d.get("fit", "inherit")),  # type: ignore[arg-type]
                scale=float(d.get("scale", 1.0)),
                x=float(d.get("x", 0.5)),
                y=float(d.get("y", 0.5)),
                rotation=int(d.get("rotation", 0)),
                flip_h=bool(d.get("flip_h", False)),
                flip_v=bool(d.get("flip_v", False)),
                crop=tuple(float(v) for v in crop),  # type: ignore[arg-type]
            )

        def effect_of(e: dict[str, Any]) -> Effect:
            return Effect(
                kind=e["kind"],
                params=tuple((str(k), float(v)) for k, v in e.get("params", ())),
            )

        def animation_of(a: dict[str, Any]) -> anim.Animation:
            return anim.Animation(
                channel=str(a["channel"]),
                points=tuple(
                    anim.Keyframe(
                        t=float(p["t"]),
                        value=float(p["value"]),
                        easing=str(p.get("easing", "linear")),  # type: ignore[arg-type]
                    )
                    for p in a.get("points", ())
                ),
            )

        def clip_of(d: dict[str, Any]) -> Clip:
            start = d.get("start")
            return Clip(
                id=str(d["id"]),
                asset_id=str(d["asset_id"]),
                in_=float(d["in_"]),
                out_=float(d["out_"]),
                speed=float(d.get("speed", 1.0)),
                effects=tuple(effect_of(e) for e in d.get("effects", ())),
                start=None if start is None else float(start),
                audio_enabled=bool(d.get("audio_enabled", True)),
                link_id=None if d.get("link_id") is None else str(d["link_id"]),
                transform=transform_of(d.get("transform")),
                envelope=tuple(
                    env.VolumePoint(
                        t=float(p["t"]), gain=float(p["gain"]),
                        easing=str(p.get("easing", "linear")),  # type: ignore[arg-type]
                    )
                    for p in d.get("envelope", ())
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
                mode=d.get("mode", "sequence"),
                name=str(d.get("name", "")),
                solo=bool(d.get("solo", False)),
                locked=bool(d.get("locked", False)),
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
                kind=str(a.get("kind", "video")),  # type: ignore[arg-type]
            )
            for a in data.get("assets", ())
        )
        texts = tuple(
            TextOverlay(
                id=str(t["id"]), text=str(t["text"]), start=float(t["start"]), end=float(t["end"]),
                x=float(t.get("x", 0.5)), y=float(t.get("y", 0.82)), size=int(t.get("size", 42)),
                color=str(t.get("color", "#FFFFFF")), bold=bool(t.get("bold", False)),
                opacity=float(t.get("opacity", 1.0)),
                subtitle=bool(t.get("subtitle", False)),
                animations=tuple(animation_of(a) for a in t.get("animations", ())),
                color_keys=tuple(
                    tc.ColorKeyframe(t=float(key["t"]), color=str(key["color"]))
                    for key in t.get("color_keys", ())
                ),
            )
            for t in data.get("texts", ())
        )
        markers = tuple(
            sorted(
                (Marker(id=str(m["id"]), t=float(m["t"]), name=str(m.get("name", "")))
                 for m in data.get("markers", ())),
                key=lambda m: (m.t, m.id),
            )
        )
        elements = tuple(sorted(
            (
                Element(
                    id=str(e["id"]), kind=str(e["kind"]), start=float(e["start"]),
                    end=float(e["end"]), x=float(e.get("x", 0.5)), y=float(e.get("y", 0.5)),
                    width=float(e.get("width", 0.3)), height=float(e.get("height", 0.2)),
                    rotation=float(e.get("rotation", 0.0)), opacity=float(e.get("opacity", 1.0)),
                    color=str(e.get("color", "#FFFFFF")),
                    asset_id=None if e.get("asset_id") is None else str(e["asset_id"]),
                    fit=str(e.get("fit", "fit")),  # type: ignore[arg-type]
                    in_=float(e.get("in", 0.0)),
                    video_track=int(e.get("video_track", 0)),
                    animations=tuple(animation_of(a) for a in e.get("animations", ())),
                    effects=tuple(effect_of(fx) for fx in e.get("effects", ())),
                    envelope=tuple(
                        env.VolumePoint(
                            t=float(p["t"]), gain=float(p["gain"]),
                            easing=str(p.get("easing", "linear")),  # type: ignore[arg-type]
                        )
                        for p in e.get("envelope", ())
                    ),
                )
                for e in data.get("elements", ())
            ),
            key=lambda e: e.video_track if e.video_track > 0 else VIDEO_TRACKS_MAX + 1,
        ))
        effects = tuple(effect_of(e) for e in data.get("effects", ()))
        range_in, range_out = data.get("range_in"), data.get("range_out")
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
            background=str(data.get("background", DEFAULT_BACKGROUND)),
            default_fit=str(data.get("default_fit", "fit")),  # type: ignore[arg-type]
            markers=markers,
            range_in=None if range_in is None else float(range_in),
            range_out=None if range_out is None else float(range_out),
            elements=elements,
            effects=effects,
        )
    except ProjectError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectError(f"proyecto ilegible: {exc}") from exc


def with_video(project: Project, track: Track) -> Project:
    return replace(project, video=track)
