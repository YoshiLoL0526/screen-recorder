"""Elementos superpuestos al video (v6, docs/22): geometría pura y edición del modelo.

Un `Element` (ver `core/project.py`) es una imagen o una figura con un intervalo de tiempo y una
caja **normalizada**: `x`/`y` son el centro en fracciones del lienzo y `width`/`height` el tamaño
en fracciones del lienzo. Esta es la única implementación de la geometría: la usan el preview
(`services/playback.py`), el export (`core/graph.py` + `core/overlays.py`) y la capa de
interacción del preview.

Orden de una imagen de elemento:

    asset (o PNG de figura) → encaje en la caja (fit/fill/stretch) → blur → rotación → opacidad
    → overlay centrado en (x, y)

`z-order` = orden de `Project.elements`: el último de la tupla va **encima**. Las operaciones de
edición devuelven proyectos nuevos (todo es frozen) y no tocan clips, audio ni textos.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal

from .errors import ProjectError
from .project import (
    ELEMENT_SIZE_MAX,
    ELEMENT_SIZE_MIN,
    Element,
    Effect,
    MediaAsset,
    Project,
    new_id,
)
from .timeline import total_duration

ELEMENT_LABELS = {
    "rect": "Rectángulo",
    "ellipse": "Elipse",
    "triangle": "Triángulo",
    "image": "Imagen",
}
SHAPE_KINDS = ("rect", "ellipse", "triangle")
BLUR_KIND = "blur"
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
    if element.kind == "image":
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
        fit=element.fit if element.kind == "image" else "stretch",
    )


def blur_radius(element: Element) -> float:
    """Radio de blur del elemento (0…50 px del lienzo); 0 si no tiene efecto."""
    for effect in element.effects:
        if effect.kind == BLUR_KIND:
            for key, value in effect.params:
                if key == "radius":
                    return max(0.0, min(BLUR_MAX, float(value)))
    return 0.0


def active(project: Project, t: float) -> list[Element]:
    """Elementos visibles en `t`, en orden de apilado (el último va encima)."""
    return [element for element in project.elements if element.start <= t < element.end]


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
    kind: Literal["image", "rect", "ellipse", "triangle"],
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
) -> tuple[Project, Element]:
    """Añade un elemento al final (encima) y devuelve `(proyecto, elemento)`."""
    if kind not in ("image", *SHAPE_KINDS):
        raise ProjectError(f"tipo de elemento desconocido ({kind!r})")
    if kind == "image":
        if not asset_id:
            raise ProjectError("una imagen necesita un asset")
        asset = project.asset(asset_id)
        if asset.kind != "image":
            raise ProjectError(f"el asset {asset_id} no es una imagen ({asset.kind})")
        auto_w, auto_h = default_image_box(project, asset)
        if width == 0.35 and height == 0.25:
            width, height = auto_w, auto_h
    start, end = _fit_interval(project, start, duration)
    effects: tuple[Effect, ...] = ()
    if blur > 0:
        effects = (Effect(BLUR_KIND, (("radius", round(_clamp(blur, 0.0, BLUR_MAX), 3)),)),)
    element = Element(
        id=new_id("e"), kind=kind, start=start, end=end,
        x=_clamp(x, 0.0, 1.0), y=_clamp(y, 0.0, 1.0),
        width=_clamp(width, ELEMENT_SIZE_MIN, ELEMENT_SIZE_MAX),
        height=_clamp(height, ELEMENT_SIZE_MIN, ELEMENT_SIZE_MAX),
        rotation=float(rotation), opacity=_clamp(opacity, 0.0, 1.0),
        color=color, asset_id=asset_id, fit=fit, effects=effects,
    )
    return replace(project, elements=(*project.elements, element)), element


def update_element(project: Project, element_id: str, **changes) -> Project:  # noqa: ANN003
    """Cambia campos de un elemento (valida el resultado y respeta el z-order)."""
    current = project.element(element_id)
    allowed = {
        "start", "end", "x", "y", "width", "height", "rotation", "opacity", "color",
        "fit", "effects",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ProjectError(f"campos de elemento desconocidos: {', '.join(sorted(unknown))}")
    element = replace(current, **changes)
    return replace(
        project,
        elements=tuple(element if item.id == element_id else item for item in project.elements),
    )


def set_blur(project: Project, element_id: str, radius: float) -> Project:
    """Fija el blur (0…50 px del lienzo); 0 quita el efecto."""
    current = project.element(element_id)
    radius = _clamp(radius, 0.0, BLUR_MAX)
    others = tuple(effect for effect in current.effects if effect.kind != BLUR_KIND)
    if radius <= 0:
        return update_element(project, element_id, effects=others)
    effect = Effect(BLUR_KIND, (("radius", round(radius, 3)),))
    return update_element(project, element_id, effects=(*others, effect))


def remove_element(project: Project, element_id: str) -> Project:
    project.element(element_id)  # valida que existe
    return replace(project, elements=tuple(e for e in project.elements if e.id != element_id))


def _move_z(project: Project, element_id: str, offset: int) -> Project:
    items = list(project.elements)
    index = next((i for i, e in enumerate(items) if e.id == element_id), None)
    if index is None:
        raise ProjectError(f"no existe el elemento {element_id}")
    target = max(0, min(len(items) - 1, index + offset))
    if target == index:
        return project
    item = items.pop(index)
    items.insert(target, item)
    return replace(project, elements=tuple(items))


def raise_element(project: Project, element_id: str) -> Project:
    """Sube un paso el elemento (más cerca del espectador)."""
    return _move_z(project, element_id, 1)


def lower_element(project: Project, element_id: str) -> Project:
    """Baja un paso el elemento (más al fondo)."""
    return _move_z(project, element_id, -1)


def bring_to_front(project: Project, element_id: str) -> Project:
    project.element(element_id)
    items = [e for e in project.elements if e.id != element_id]
    items.append(project.element(element_id))
    return replace(project, elements=tuple(items))


def send_to_back(project: Project, element_id: str) -> Project:
    project.element(element_id)
    items = [e for e in project.elements if e.id != element_id]
    items.insert(0, project.element(element_id))
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
    "BLUR_MAX",
    "ELEMENT_COLOR",
    "ELEMENT_DURATION",
    "ELEMENT_LABELS",
    "ELEMENT_SIZE_MAX",
    "ELEMENT_SIZE_MIN",
    "ElementLayout",
    "SHAPE_KINDS",
    "active",
    "add_element",
    "blur_radius",
    "bring_to_front",
    "default_image_box",
    "image_padding",
    "layout",
    "lower_element",
    "prepared_size",
    "raise_element",
    "remove_element",
    "rotated_size",
    "send_to_back",
    "set_blur",
    "source_size",
    "update_element",
    "visible_in_timeline",
    "with_effect",
    "z_index",
]
