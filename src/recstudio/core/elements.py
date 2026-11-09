"""Elementos superpuestos al video (v8, docs/28 y docs/29): geometría pura y edición del modelo.

Un `Element` (ver `core/project.py`) es una imagen, un video PiP o una figura con un intervalo de
tiempo y una caja **normalizada**: `x`/`y` son el centro en fracciones del lienzo y
`width`/`height` el tamaño en fracciones del lienzo. Esta es la única implementación de la
geometría: la usan el preview (`services/playback.py`), el export (`core/graph.py` +
`core/overlays.py`) y la capa de interacción del preview.

Orden de una imagen de elemento:

    asset (o PNG de figura) → encaje en la caja (fit/fill/stretch) → blur → rotación → opacidad
    → overlay centrado en (x, y)

`z-order` = orden de `Project.elements`: el último de la tupla va **encima**. Las operaciones de
edición devuelven proyectos nuevos (todo es frozen) y no tocan clips, audio ni textos.

El audio de un video superpuesto usa `Element.effects` con el efecto `volume` (el mismo de los
clips): `audio_gain()` lo lee y `set_audio_gain()` lo edita. Su envolvente opcional vive en
`Element.envelope` (docs/34) y se edita con `update_element(..., envelope=…)`.

`video_track` agrupa los videos en V2/V3 sin crear `Track` nuevas: `_insert_ordered` mantiene el
z-order V2 < V3 < Elementos y `set_video_track` recoloca el elemento al cambiar de capa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal

from . import animation as anim
from . import effects as fx
from .errors import ProjectError
from .project import (
    ELEMENT_SIZE_MAX,
    ELEMENT_SIZE_MIN,
    VIDEO_TRACKS_MAX,
    Element,
    Effect,
    MediaAsset,
    Project,
    new_id,
)
from .timeline import add_asset, total_duration

ELEMENT_LABELS = {
    "rect": "Rectángulo",
    "ellipse": "Elipse",
    "triangle": "Triángulo",
    "image": "Imagen",
    "video": "Vídeo",
}
SHAPE_KINDS = ("rect", "ellipse", "triangle")
BLUR_KIND = "blur"
AUDIO_KIND = "volume"  # ganancia de audio del PiP (mismo efecto que en los clips)
AUDIO_GAIN_MAX = fx.GAIN_MAX  # mismo tope que el volumen de un clip
BLUR_MAX = 50.0  # px en el espacio del lienzo del proyecto (docs/22)
ELEMENT_DURATION = 3.0
ELEMENT_COLOR = "#FF5470"


@dataclass(frozen=True)
class ElementLayout:
    """Rectángulo de un elemento en píxeles de un lienzo concreto (preview o export)."""

    canvas_width: int
    canvas_height: int
    center_x: float  # centro en píxeles del lienzo
    center_y: float
    box_width: int  # caja normalizada, antes de encajar la imagen
    box_height: int
    draw_width: int  # imagen ya encajada, antes de rotar
    draw_height: int
    scale_width: int  # para `fill`: tamaño antes del recorte; en el resto == draw
    scale_height: int
    source_width: int
    source_height: int
    rotation: float  # grados horarios
    opacity: float
    blur: float  # radio en píxeles del lienzo pedido
    fit: str

    @property
    def x(self) -> int:
        """Esquina superior izquierda de la imagen sin rotar."""
        return int(round(self.center_x - self.draw_width / 2.0))

    @property
    def y(self) -> int:
        return int(round(self.center_y - self.draw_height / 2.0))

    @property
    def rotated_width(self) -> int:
        """Ancho del rectángulo que envuelve la imagen rotada (misma fórmula que FFmpeg)."""
        return _rotated_extent(self.draw_width, self.draw_height, self.rotation)[0]

    @property
    def rotated_height(self) -> int:
        return _rotated_extent(self.draw_width, self.draw_height, self.rotation)[1]


def image_padding(blur: float) -> int:
    """Margen transparente alrededor del PNG para que el blur pueda vertir alfa fuera.

    Sin margen, `GaussianBlur` sobre un PNG del tamaño exacto de la caja deja el borde duro: el
    desenfoque no tendría píxeles transparentes donde extenderse. Se usa 3× el radio (cubre la
    práctica totalidad de la gaussiana) y el contenido queda centrado, así que el overlay sigue
    centrado en `(x, y)` sin tocar la geometría.
    """
    return 0 if blur <= 0 else max(1, int(math.ceil(float(blur) * 3.0)))


def prepared_size(layout: ElementLayout) -> tuple[int, int]:
    """Tamaño del PNG materializado (con el margen del blur, si lo hay)."""
    pad = image_padding(layout.blur)
    return layout.draw_width + 2 * pad, layout.draw_height + 2 * pad


def _rotated_extent(width: int, height: int, degrees: float) -> tuple[int, int]:
    """`(ancho, alto)` del rectángulo que envuelve `width×height` girado `degrees` horario.

    Se redondea al entero más cercano, que es lo que hacen `rotw()`/`roth()` de FFmpeg
    (comprobado con FFmpeg 7.1: 100×50 a 30° da 112×93, no 111×93).
    """
    radians = math.radians(float(degrees))
    cosine, sine = abs(math.cos(radians)), abs(math.sin(radians))
    return (
        max(1, int(round(width * cosine + height * sine))),
        max(1, int(round(width * sine + height * cosine))),
    )


def rotated_size(width: int, height: int, degrees: float) -> tuple[int, int]:
    """Tamaño de una imagen tras rotarla con expansión (helper público para la UI)."""
    return _rotated_extent(width, height, degrees)


def _pixel(value: float) -> int:
    number = max(1, int(round(value)))
    return number


def source_size(project: Project, element: Element) -> tuple[int, int]:
    """Dimensiones de la fuente del elemento (imagen real o caja de la figura)."""
    if element.kind != "image":
        return (0, 0)
    asset = project.asset(element.asset_id or "")
    return max(1, int(asset.width)), max(1, int(asset.height))


def layout(project: Project, element: Element, canvas: tuple[int, int] | None = None) -> ElementLayout:
    """Geometría del elemento en `canvas` (por defecto, el lienzo del proyecto)."""
    canvas_width, canvas_height = canvas or (project.width, project.height)
    canvas_width, canvas_height = int(canvas_width), int(canvas_height)
    if canvas_width <= 0 or canvas_height <= 0:
        raise ProjectError("el lienzo del proyecto debe tener ancho y alto positivos")

    box_width = _pixel(element.width * canvas_width)
    box_height = _pixel(element.height * canvas_height)
    if element.kind in ("image", "video"):
        asset: MediaAsset = project.asset(element.asset_id or "")
        source_width = max(1, int(asset.width))
        source_height = max(1, int(asset.height))
        if element.fit == "fill":
            factor = max(box_width / source_width, box_height / source_height)
            scale_width = max(box_width, _pixel(source_width * factor))
            scale_height = max(box_height, _pixel(source_height * factor))
            draw_width, draw_height = box_width, box_height
        elif element.fit == "stretch":
            scale_width, scale_height = box_width, box_height
            draw_width, draw_height = box_width, box_height
        else:  # fit
            factor = min(box_width / source_width, box_height / source_height)
            draw_width = max(1, _pixel(source_width * factor))
            draw_height = max(1, _pixel(source_height * factor))
            scale_width, scale_height = draw_width, draw_height
    else:
        source_width = box_width
        source_height = box_height
        draw_width, draw_height = box_width, box_height
        scale_width, scale_height = draw_width, draw_height

    scale = canvas_height / project.height if project.height > 0 else 1.0
    blur = blur_radius(element) * scale
    return ElementLayout(
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        center_x=float(element.x) * canvas_width,
        center_y=float(element.y) * canvas_height,
        box_width=box_width,
        box_height=box_height,
        draw_width=draw_width,
        draw_height=draw_height,
        scale_width=scale_width,
        scale_height=scale_height,
        source_width=source_width,
        source_height=source_height,
        rotation=float(element.rotation),
        opacity=float(element.opacity),
        blur=blur,
        fit=element.fit if element.kind in ("image", "video") else "stretch",
    )


def blur_radius(element: Element) -> float:
    """Radio de blur del elemento (0…50 px del lienzo); 0 si no tiene efecto."""
    for effect in element.effects:
        if effect.kind == BLUR_KIND:
            for key, value in effect.params:
                if key == "radius":
                    return max(0.0, min(BLUR_MAX, float(value)))
    return 0.0


def audio_gain(element: Element) -> float:
    """Ganancia del audio de un video superpuesto (0 = silencio, 1 = original)."""
    if element.kind != "video":
        return 0.0
    return max(0.0, min(AUDIO_GAIN_MAX, fx.param_in(element.effects, AUDIO_KIND, "gain", 1.0)))


def has_audio(element: Element) -> bool:
    """¿El elemento puede sonar? (video con ganancia > 0; el asset se valida aparte)."""
    return element.kind == "video" and audio_gain(element) > 0


def active(project: Project, t: float) -> list[Element]:
    """Elementos visibles en `t`, en orden de apilado (el último va encima)."""
    return [element for element in project.elements if element.start <= t < element.end]


def effective(element: Element, t: float) -> Element:
    """Elemento con la geometría animada en `t` (los canales sin clave usan la base).

    `scale` multiplica `width`/`height` (1 = tamaño base) y `rotation` es absoluta en grados
    horarios (su valor base es `Element.rotation`). La caja resultante se acota al rango del
    modelo (`ELEMENT_SIZE_MIN…ELEMENT_SIZE_MAX`) para que un elemento ya enorme no genere una
    caja inválida.
    """
    local = max(0.0, float(t) - element.start)
    x = anim.value_at(element.animations, "x", local, element.x)
    y = anim.value_at(element.animations, "y", local, element.y)
    scale = anim.value_at(element.animations, "scale", local, 1.0)
    rotation = anim.value_at(element.animations, "rotation", local, element.rotation)
    width = _clamp(element.width * scale, ELEMENT_SIZE_MIN, ELEMENT_SIZE_MAX)
    height = _clamp(element.height * scale, ELEMENT_SIZE_MIN, ELEMENT_SIZE_MAX)
    if (
        abs(x - element.x) < 1e-9
        and abs(y - element.y) < 1e-9
        and abs(width - element.width) < 1e-9
        and abs(height - element.height) < 1e-9
        and abs(rotation - element.rotation) < 1e-9
    ):
        return element
    return replace(
        element, x=x, y=y, width=width, height=height, rotation=rotation,
    )


def visible_in_timeline(project: Project, element: Element) -> bool:
    """¿El elemento llega a verse en algún frame del timeline?"""
    total = total_duration(project)
    return total > 0 and element.end > 0 and element.start < total and element.opacity > 0


def default_image_box(project: Project, asset: MediaAsset, fraction: float = 0.4) -> tuple[float, float]:
    """Caja normalizada que encaja la imagen en un `fraction` del lienzo sin deformarla."""
    if project.width <= 0 or project.height <= 0 or asset.width <= 0 or asset.height <= 0:
        return 0.4, 0.3
    # h_frac = w_frac · (W/H) · (alto/ancho de la imagen)
    width = min(fraction, fraction * (project.height / project.width) * (asset.width / asset.height))
    width = max(ELEMENT_SIZE_MIN, min(0.95, width))
    height = width * (project.width / project.height) * (asset.height / asset.width)
    height = max(ELEMENT_SIZE_MIN, min(0.95, height))
    return round(width, 4), round(height, 4)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def video_track_label(track: int) -> str:
    """Nombre de la capa: «Elementos» (0) o «V2»/«V3» (1/2)."""
    return "Elementos" if int(track) <= 0 else f"V{int(track) + 1}"


def _track_key(element: Element) -> int:
    """Clave de z-order: V2 < V3 < Elementos (los gráficos van encima de las pistas)."""
    return element.video_track if element.video_track > 0 else VIDEO_TRACKS_MAX + 1


def _insert_ordered(elements: tuple[Element, ...], element: Element) -> tuple[Element, ...]:
    """Inserta el elemento respetando el orden de capas (misma capa: al final)."""
    key = _track_key(element)
    index = next((i for i, item in enumerate(elements) if _track_key(item) > key), len(elements))
    return (*elements[:index], element, *elements[index:])


def _fit_interval(project: Project, start: float, duration: float) -> tuple[float, float]:
    total = total_duration(project)
    if total <= 0:
        raise ProjectError("el timeline está vacío: no hay dónde poner el elemento")
    shortest = max(0.2, 1.0 / max(1, project.fps))
    start = _clamp(start, 0.0, max(0.0, total - shortest))
    end = min(total, start + max(shortest, float(duration)))
    if end - start < shortest - 1e-9:
        raise ProjectError("el elemento no cabe en el timeline")
    return round(start, 4), round(end, 4)


def add_element(
    project: Project,
    kind: Literal["image", "video", "rect", "ellipse", "triangle"],
    start: float,
    duration: float = ELEMENT_DURATION,
    *,
    x: float = 0.5,
    y: float = 0.5,
    width: float = 0.35,
    height: float = 0.25,
    color: str = ELEMENT_COLOR,
    asset_id: str | None = None,
    fit: Literal["fit", "fill", "stretch"] = "fit",
    opacity: float = 1.0,
    rotation: float = 0.0,
    blur: float = 0.0,
    audio_gain: float = 1.0,
    in_: float = 0.0,
    video_track: int = 0,
    animations: tuple[anim.Animation, ...] = (),
) -> tuple[Project, Element]:
    """Añade un elemento en su capa y devuelve `(proyecto, elemento)`."""
    if kind not in ("image", "video", *SHAPE_KINDS):
        raise ProjectError(f"tipo de elemento desconocido ({kind!r})")
    video_track = int(video_track)
    if not 0 <= video_track <= VIDEO_TRACKS_MAX:
        raise ProjectError(f"pista de video inválida ({video_track})")
    if video_track and kind != "video":
        raise ProjectError(f"solo un video puede ir en V{video_track + 1}")
    if kind in ("image", "video"):
        if not asset_id:
            raise ProjectError(f"un {kind} necesita un asset")
        asset = project.asset(asset_id)
        expected = "imagen" if kind == "image" else "video"
        if asset.kind != kind:
            raise ProjectError(f"el asset {asset_id} no es un {expected} ({asset.kind})")
        auto_w, auto_h = default_image_box(project, asset)
        if width == 0.35 and height == 0.25:
            if video_track > 0:
                # Un clip de una pista V2/V3 ocupa el lienzo salvo que se pida otra caja.
                width, height, fit = 1.0, 1.0, "fill"
            else:
                width, height = auto_w, auto_h
        if kind == "video":
            in_ = max(0.0, float(in_))
            available = max(0.0, asset.duration - in_)
            if available <= 0:
                raise ProjectError("el video no tiene metraje desde esa entrada")
            duration = min(float(duration), available)
    start, end = _fit_interval(project, start, duration)
    if kind == "video":
        asset = project.asset(asset_id or "")
        if in_ + (end - start) > asset.duration + 1e-6:
            end = start + max(0.2, asset.duration - in_)
    effects: list[Effect] = []
    if blur > 0:
        effects.append(Effect(BLUR_KIND, (("radius", round(_clamp(blur, 0.0, BLUR_MAX), 3)),)))
    if kind == "video" and abs(float(audio_gain) - 1.0) > 1e-6:
        effects.append(
            Effect(AUDIO_KIND, (("gain", round(_clamp(audio_gain, 0.0, AUDIO_GAIN_MAX), 3)),))
        )
    element = Element(
        id=new_id("e"), kind=kind, start=start, end=end,
        x=_clamp(x, 0.0, 1.0), y=_clamp(y, 0.0, 1.0),
        width=_clamp(width, ELEMENT_SIZE_MIN, ELEMENT_SIZE_MAX),
        height=_clamp(height, ELEMENT_SIZE_MIN, ELEMENT_SIZE_MAX),
        rotation=float(rotation), opacity=_clamp(opacity, 0.0, 1.0),
        color=color, asset_id=asset_id, fit=fit, in_=max(0.0, float(in_)),
        video_track=video_track, animations=animations, effects=tuple(effects),
    )
    return replace(project, elements=_insert_ordered(project.elements, element)), element


CAMERA_PIP_FRACTION = 0.28  # ancho del PiP de la cara respecto al lienzo (docs/49)


def add_camera_pip(
    project: Project,
    camera: "MediaAsset",
    start: float,
    *,
    offset_s: float = 0.0,
) -> tuple[Project, Element]:
    """Añade la cámara de una grabación como PiP abajo a la derecha (docs/49).

    `offset_s` es `t0_video − t0_cámara`: si es positivo, la cámara lleva grabando más tiempo y
    el elemento avanza `in_`; si es negativo, el PiP empieza más tarde. Sin audio (`gain=0`).
    Es un `Element` normal: se puede mover, redimensionar, animar o borrar.
    """
    project, asset = add_asset(project, camera)
    if asset.kind != "video" or asset.duration <= 0:
        raise ProjectError("la cámara no es un vídeo válido")
    delay = max(0.0, -float(offset_s))
    in_ = min(max(0.0, float(offset_s)), max(0.0, asset.duration - 0.2))
    at = max(0.0, float(start)) + delay
    total = total_duration(project)
    duration = max(0.2, total - at)
    width, height = default_image_box(project, asset, CAMERA_PIP_FRACTION)
    x = min(0.82, 1.0 - width / 2 - 0.02)
    y = min(0.78, 1.0 - height / 2 - 0.02)
    return add_element(
        project,
        "video",
        at,
        duration,
        x=max(width / 2 + 0.02, x),
        y=max(height / 2 + 0.02, y),
        width=width,
        height=height,
        asset_id=asset.id,
        audio_gain=0.0,
        in_=in_,
    )


def update_element(project: Project, element_id: str, **changes) -> Project:  # noqa: ANN003
    """Cambia campos de un elemento (valida el resultado y respeta el z-order)."""
    current = project.element(element_id)
    allowed = {
        "start", "end", "x", "y", "width", "height", "rotation", "opacity", "color",
        "fit", "in_", "video_track", "animations", "effects", "envelope",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ProjectError(f"campos de elemento desconocidos: {', '.join(sorted(unknown))}")
    element = replace(current, **changes)
    if element.kind == "video":
        asset = project.asset(element.asset_id or "")
        if element.in_ < 0:
            raise ProjectError("la entrada de fuente no puede ser negativa")
        if element.in_ + element.duration > asset.duration + 1e-6:
            raise ProjectError("el video superpuesto se sale del final del archivo")
    if "video_track" in changes and element.video_track != current.video_track:
        others = tuple(item for item in project.elements if item.id != element_id)
        return replace(project, elements=_insert_ordered(others, element))
    return replace(
        project,
        elements=tuple(element if item.id == element_id else item for item in project.elements),
    )


def set_video_track(project: Project, element_id: str, track: int) -> Project:
    """Mueve un video entre «Elementos» y V2/V3, recolocando su z-order."""
    current = project.element(element_id)
    if current.kind != "video":
        raise ProjectError("solo un video puede cambiar de pista")
    track = int(track)
    if not 0 <= track <= VIDEO_TRACKS_MAX:
        raise ProjectError(f"pista de video inválida ({track})")
    if track == current.video_track:
        return project
    return update_element(project, element_id, video_track=track)


def set_blur(project: Project, element_id: str, radius: float) -> Project:
    """Fija el blur (0…50 px del lienzo); 0 quita el efecto."""
    current = project.element(element_id)
    radius = _clamp(radius, 0.0, BLUR_MAX)
    others = tuple(effect for effect in current.effects if effect.kind != BLUR_KIND)
    if radius <= 0:
        return update_element(project, element_id, effects=others)
    effect = Effect(BLUR_KIND, (("radius", round(radius, 3)),))
    return update_element(project, element_id, effects=(*others, effect))


def set_audio_gain(project: Project, element_id: str, gain: float) -> Project:
    """Fija el volumen del audio de un video superpuesto; 1 (neutro) quita el efecto."""
    current = project.element(element_id)
    if current.kind != "video":
        raise ProjectError("solo un video superpuesto tiene audio")
    gain = _clamp(gain, 0.0, AUDIO_GAIN_MAX)
    others = tuple(effect for effect in current.effects if effect.kind != AUDIO_KIND)
    if abs(gain - 1.0) < 1e-6:
        return update_element(project, element_id, effects=others)
    effect = Effect(AUDIO_KIND, (("gain", round(gain, 3)),))
    return update_element(project, element_id, effects=(*others, effect))


def remove_element(project: Project, element_id: str) -> Project:
    project.element(element_id)  # valida que existe
    return replace(project, elements=tuple(e for e in project.elements if e.id != element_id))


def _move_z(project: Project, element_id: str, offset: int) -> Project:
    """Mueve un paso **dentro de su capa** (V2, V3 o Elementos), sin cruzar de capa."""
    items = list(project.elements)
    index = next((i for i, e in enumerate(items) if e.id == element_id), None)
    if index is None:
        raise ProjectError(f"no existe el elemento {element_id}")
    key = _track_key(items[index])
    layer = [i for i, item in enumerate(items) if _track_key(item) == key]
    pos = layer.index(index)
    target_pos = pos + (1 if offset > 0 else -1)
    if not 0 <= target_pos < len(layer):
        return project
    target = layer[target_pos]
    items[index], items[target] = items[target], items[index]
    return replace(project, elements=tuple(items))


def raise_element(project: Project, element_id: str) -> Project:
    """Sube un paso el elemento dentro de su capa (más cerca del espectador)."""
    return _move_z(project, element_id, 1)


def lower_element(project: Project, element_id: str) -> Project:
    """Baja un paso el elemento dentro de su capa (más al fondo)."""
    return _move_z(project, element_id, -1)


def bring_to_front(project: Project, element_id: str) -> Project:
    """Pone el elemento al frente de su capa; no lo saca de V2/V3/Elementos."""
    current = project.element(element_id)
    key = _track_key(current)
    items = list(project.elements)
    index = next(i for i, item in enumerate(items) if item.id == element_id)
    layer = [i for i, item in enumerate(items) if _track_key(item) == key]
    if layer[-1] == index:  # ya está al frente de su capa
        return project
    others = [i for i in layer if i != index]
    item = items.pop(index)
    items.insert(others[-1], item)  # la última de su capa ya se desplazó una posición
    return replace(project, elements=tuple(items))


def send_to_back(project: Project, element_id: str) -> Project:
    """Envía el elemento al fondo de su capa; no lo saca de V2/V3/Elementos."""
    current = project.element(element_id)
    key = _track_key(current)
    items = list(project.elements)
    index = next(i for i, item in enumerate(items) if item.id == element_id)
    layer = [i for i, item in enumerate(items) if _track_key(item) == key]
    if layer[0] == index:  # ya está al fondo de su capa
        return project
    others = [i for i in layer if i != index]
    item = items.pop(index)
    items.insert(others[0], item)
    return replace(project, elements=tuple(items))


def z_index(project: Project, element_id: str) -> int:
    for index, element in enumerate(project.elements):
        if element.id == element_id:
            return index
    raise ProjectError(f"no existe el elemento {element_id}")


def with_effect(element: Element, kind: str, **params: float) -> Element:
    """Elemento con el efecto `kind` fusionado (p. ej. blur); neutro se elimina."""
    if kind != BLUR_KIND:
        raise ProjectError(f"efecto de elemento no soportado: {kind!r}")
    radius = _clamp(params.get("radius", 0.0), 0.0, BLUR_MAX)
    others = tuple(effect for effect in element.effects if effect.kind != kind)
    if radius <= 0:
        return replace(element, effects=others)
    return replace(element, effects=(*others, Effect(kind, (("radius", round(radius, 3)),))))


__all__ = [
    "AUDIO_GAIN_MAX",
    "BLUR_MAX",
    "CAMERA_PIP_FRACTION",
    "ELEMENT_COLOR",
    "ELEMENT_DURATION",
    "ELEMENT_LABELS",
    "ELEMENT_SIZE_MAX",
    "ELEMENT_SIZE_MIN",
    "ElementLayout",
    "SHAPE_KINDS",
    "active",
    "add_camera_pip",
    "add_element",
    "audio_gain",
    "blur_radius",
    "bring_to_front",
    "default_image_box",
    "effective",
    "image_padding",
    "layout",
    "lower_element",
    "prepared_size",
    "raise_element",
    "remove_element",
    "rotated_size",
    "send_to_back",
    "set_audio_gain",
    "set_blur",
    "set_video_track",
    "source_size",
    "update_element",
    "video_track_label",
    "visible_in_timeline",
    "with_effect",
    "z_index",
]
