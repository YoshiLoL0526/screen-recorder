"""Geometría pura del recorte interactivo sobre el preview (docs/43).

El modo de recorte muestra el clip **sin recortar** (el preview sustituye solo en memoria su
`Transform.crop` por `(0, 0, 1, 1)`) y superpone el rectángulo de recorte con ocho asas. Este
módulo no conoce Aurora ni el store: convierte entre el `crop` normalizado del modelo y píxeles
del escenario, y aplica los deltas de un arrastre con las mismas reglas que `core/transform.py`.

Decisiones:

- El `crop` se aplica **después** de la rotación/flip del clip, así que el rectángulo visible se
  mapea directamente sobre la imagen ya rotada: la geometría funciona con 0/90/180/270 y con los
  dos volteos sin casos especiales.
- Cada lado tiene un mínimo del 5 % (`MIN_CROP`) y el resultado siempre queda en `0…1`.
- Al mover el rectángulo completo no cambia su tamaño: si el desplazamiento lo sacaría del clip,
  se acota y el rectángulo se queda pegado al borde.
- El zoom heredado (`Effect("zoom")`) se aplica después del crop en el render; aquí se edita el
  rectángulo del modelo, no el encuadre adicional del efecto (documentado en docs/43).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from . import transform as tx
from .errors import ProjectError
from .project import Clip, Project

FULL_CROP = tx.FULL_CROP
MIN_CROP = 0.05
HANDLES = ("tl", "tr", "bl", "br", "t", "b", "l", "r")
MOVE = "move"
HANDLE_CURSORS = {
    "tl": "nwse-resize",
    "tr": "nesw-resize",
    "bl": "nesw-resize",
    "br": "nwse-resize",
    "t": "ns-resize",
    "b": "ns-resize",
    "l": "ew-resize",
    "r": "ew-resize",
    MOVE: "grab",
}


@dataclass(frozen=True)
class CropBox:
    """Rectángulo del recorte en píxeles lógicos del escenario."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def full_layout(project: Project, clip: Clip, stage: tuple[int, int]) -> tx.Layout:
    """Layout del clip con el recorte completo (la imagen que se ve en modo recorte).

    Coincide con el proyecto de preview que arma el controlador (`uncropped_project`): si el
    clip no tenía transformación, el layout ya es el completo y no se toca nada.
    """
    if clip.transform is not None and clip.transform.crop != FULL_CROP:
        clip = replace(clip, transform=replace(clip.transform, crop=FULL_CROP))
    return tx.layout(project, clip, stage)


def uncropped_project(project: Project, clip_id: str) -> Project:
    """Copia del proyecto con el `crop` del clip a `(0, 0, 1, 1)`, **solo para el preview**.

    No se guarda ni se aplica al store: el modelo conserva el recorte real mientras el modo está
    activo. Si el clip no existe o ya estaba completo, devuelve el mismo proyecto.
    """
    try:
        index = project.video.index_of(clip_id)
    except ProjectError:
        return project
    clip = project.video.clips[index]
    transform = clip.transform
    if transform is None or transform.crop == FULL_CROP:
        return project
    clips = list(project.video.clips)
    clips[index] = replace(clip, transform=replace(transform, crop=FULL_CROP))
    return replace(project, video=replace(project.video, clips=tuple(clips)))


def crop_box(layout: tx.Layout, crop: tuple[float, float, float, float]) -> CropBox:
    """Rectángulo del crop normalizado dentro del layout **sin recortar**.

    `layout.out_width/out_height` son el tamaño de la imagen completa ya escalada; el crop es una
    fracción de esa imagen, así que el mapeo es lineal e independiente de `fit`, `scale`, posición,
    rotación y volteo.
    """
    left, top, right, bottom = (float(value) for value in crop)
    if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
        raise ValueError(f"recorte inválido ({crop})")
    return CropBox(
        layout.x + left * layout.out_width,
        layout.y + top * layout.out_height,
        layout.x + right * layout.out_width,
        layout.y + bottom * layout.out_height,
    )


def visible_box(box: CropBox, stage: tuple[int, int]) -> CropBox | None:
    """Intersección del rectángulo con el escenario (`None` si no se ve nada)."""
    width, height = float(stage[0]), float(stage[1])
    left = max(0.0, box.left)
    top = max(0.0, box.top)
    right = min(width, box.right)
    bottom = min(height, box.bottom)
    if right - left < 1.0 or bottom - top < 1.0:
        return None
    return CropBox(left, top, right, bottom)


def handle_centres(
    box: CropBox, stage: tuple[int, int], handle_size: float
) -> dict[str, tuple[float, float]]:
    """Centros de las ocho asas, metidos dentro del escenario para que sigan siendo pulsables."""
    width, height = float(stage[0]), float(stage[1])
    half = max(0.0, min(float(handle_size) / 2.0, width / 2.0, height / 2.0))
    points = {
        "tl": (box.left, box.top),
        "tr": (box.right, box.top),
        "bl": (box.left, box.bottom),
        "br": (box.right, box.bottom),
        "t": (box.center_x, box.top),
        "b": (box.center_x, box.bottom),
        "l": (box.left, box.center_y),
        "r": (box.right, box.center_y),
    }
    low_x, high_x = half, max(half, width - half)
    low_y, high_y = half, max(half, height - half)
    return {
        name: (min(max(low_x, float(x)), high_x), min(max(low_y, float(y)), high_y))
        for name, (x, y) in points.items()
    }


def move_crop(
    crop: tuple[float, float, float, float], dx: float, dy: float
) -> tuple[float, float, float, float]:
    """Desplaza el rectángulo completo `dx`/`dy` (fracciones del clip), sin cambiar su tamaño."""
    left, top, right, bottom = (float(value) for value in crop)
    offset_x = _clamp(float(dx), -left, 1.0 - right)
    offset_y = _clamp(float(dy), -top, 1.0 - bottom)
    return (left + offset_x, top + offset_y, right + offset_x, bottom + offset_y)


def resize_crop(
    crop: tuple[float, float, float, float], handle: str, dx: float, dy: float
) -> tuple[float, float, float, float]:
    """Ajusta los lados del `handle` con mínimo `MIN_CROP` por lado y clamp a `0…1`."""
    if handle not in HANDLES:
        raise ValueError(f"asa de recorte desconocida ({handle!r}); usa {', '.join(HANDLES)}")
    left, top, right, bottom = (float(value) for value in crop)
    if handle in ("tl", "l", "bl"):
        left = _clamp(left + float(dx), 0.0, max(0.0, right - MIN_CROP))
    if handle in ("tr", "r", "br"):
        right = _clamp(right + float(dx), min(1.0, left + MIN_CROP), 1.0)
    if handle in ("tl", "t", "tr"):
        top = _clamp(top + float(dy), 0.0, max(0.0, bottom - MIN_CROP))
    if handle in ("bl", "b", "br"):
        bottom = _clamp(bottom + float(dy), min(1.0, top + MIN_CROP), 1.0)
    return (left, top, right, bottom)


def drag_crop(
    crop: tuple[float, float, float, float], handle: str, dx: float, dy: float
) -> tuple[float, float, float, float]:
    """Aplica un arrastre acumulado: `move` para el interior o el nombre de un asa."""
    if handle == MOVE:
        return move_crop(crop, dx, dy)
    return resize_crop(crop, handle, dx, dy)
