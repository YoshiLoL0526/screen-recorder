"""Matemática pura de la edición directa sobre el lienzo del preview (docs/23).

La feature A permite mover y redimensionar el clip seleccionado arrastrando sobre el preview.
Este módulo no conoce Aurora ni el store: solo convierte deltas de píxeles del escenario en
`Transform` (centro y escala), con las mismas reglas que `core/transform.py`.

Decisiones:

- El **cuerpo** mueve el centro en fracciones del lienzo (`x`, `y`) y lo mantiene en 0…1.
- Las **esquinas** cambian `scale` manteniendo el aspecto y anclan la esquina opuesta. El factor
  se proyecta sobre la diagonal del rectángulo inicial, para que arrastrar en horizontal o en
  vertical responda igual.
- `scale` se acota a 0.25…4 (el rango que valida `Transform`). Si al crecer el centro anclado
  tuviera que salir del lienzo, se recorta a 0…1: el ancla se mueve lo justo para no invalidar
  la transformación (el caso solo aparece con clips que ya sobresalen por un borde).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from . import transform as tx
from .project import Clip, Project, Transform

MIN_SCALE = 0.25
MAX_SCALE = 4.0
HANDLES = ("tl", "tr", "bl", "br")
HANDLE_CURSORS = {
    "tl": "nwse-resize",
    "tr": "nesw-resize",
    "bl": "nesw-resize",
    "br": "nwse-resize",
}


@dataclass(frozen=True)
class Rect:
    """Rectángulo en píxeles lógicos del escenario."""

    left: float
    top: float
    width: float
    height: float


def fit_stage(
    project_width: int, project_height: int, available_width: float, available_height: float
) -> tuple[int, int]:
    """Tamaño del escenario que encaja el aspecto del proyecto en el hueco medido.

    Devuelve `(0, 0)` mientras el hueco no se ha medido (primer frame) y como mínimo `2×2`
    cuando hay sitio, para no pedir una imagen de tamaño cero.
    """
    if project_width <= 0 or project_height <= 0:
        return (0, 0)
    if available_width < 2 or available_height < 2:
        return (0, 0)
    scale = min(available_width / project_width, available_height / project_height)
    # Hacia abajo: el escenario nunca debe pasarse del hueco medido por redondeo.
    width = max(2, int(project_width * scale))
    height = max(2, int(project_height * scale))
    return (width, height)


def visible_rect(layout: tx.Layout) -> Rect | None:
    """Intersección del rectángulo real del clip con el escenario (`None` si no se ve)."""
    left = max(0.0, float(layout.x))
    top = max(0.0, float(layout.y))
    right = min(float(layout.canvas_width), float(layout.x + layout.out_width))
    bottom = min(float(layout.canvas_height), float(layout.y + layout.out_height))
    width, height = right - left, bottom - top
    if width < 1.0 or height < 1.0:
        return None
    return Rect(left, top, width, height)


def handle_centres(layout: tx.Layout, handle_size: float) -> dict[str, tuple[float, float]]:
    """Centros de las cuatro asas, metidos dentro del escenario para que sigan siendo pulsables.

    El clip puede sobresalir del lienzo (un `fill` o `scale > 1`); las asas se dibujan en su
    esquina real, pero si esa esquina cae fuera se desplazan lo justo hacia dentro. La escala
    sigue anclada a la esquina **real** opuesta, no a la desplazada.
    """
    width, height = float(layout.canvas_width), float(layout.canvas_height)
    half = max(0.0, min(float(handle_size) / 2.0, width / 2.0, height / 2.0))
    points = {
        "tl": (layout.x, layout.y),
        "tr": (layout.x + layout.out_width, layout.y),
        "bl": (layout.x, layout.y + layout.out_height),
        "br": (layout.x + layout.out_width, layout.y + layout.out_height),
    }
    low_x, high_x = half, max(half, width - half)
    low_y, high_y = half, max(half, height - half)
    return {
        name: (min(max(low_x, float(x)), high_x), min(max(low_y, float(y)), high_y))
        for name, (x, y) in points.items()
    }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _stage(stage: tuple[int, int]) -> tuple[float, float]:
    width, height = float(stage[0]), float(stage[1])
    if width <= 0 or height <= 0:
        raise ValueError("el escenario del preview no tiene tamaño")
    return width, height


def move_transform(
    project: Project, clip: Clip, stage: tuple[int, int], dx: float, dy: float
) -> Transform:
    """Traslada el centro del clip `dx`/`dy` píxeles, recortándolo al lienzo (0…1)."""
    width, height = _stage(stage)
    base = clip.transform or Transform()
    return replace(
        base,
        x=_clamp(base.x + float(dx) / width, 0.0, 1.0),
        y=_clamp(base.y + float(dy) / height, 0.0, 1.0),
    )


def scale_transform(
    project: Project,
    clip: Clip,
    stage: tuple[int, int],
    handle: str,
    dx: float,
    dy: float,
) -> Transform:
    """Escala el clip desde `handle` (`tl`/`tr`/`bl`/`br`) anclando la esquina opuesta.

    El delta se proyecta sobre la diagonal del rectángulo de partida; así el gesto responde
    aunque se arrastre solo en horizontal o solo en vertical. Mantiene el aspecto porque
    `Transform.scale` multiplica el encaje en ambos ejes.
    """
    if handle not in HANDLES:
        raise ValueError(f"asa desconocida ({handle!r}); usa {', '.join(HANDLES)}")
    width, height = _stage(stage)
    base = clip.transform or Transform()
    start = tx.layout(project, clip, (int(width), int(height)))
    diagonal = float(start.out_width**2 + start.out_height**2)
    if diagonal <= 0:
        return base
    sign_x = 1.0 if handle in ("tr", "br") else -1.0
    sign_y = 1.0 if handle in ("bl", "br") else -1.0
    factor = 1.0 + (sign_x * float(dx) * start.out_width
                    + sign_y * float(dy) * start.out_height) / diagonal
    scale = _clamp(base.scale * factor, MIN_SCALE, MAX_SCALE)
    probe = replace(base, scale=scale)
    lay = tx.layout(project, replace(clip, transform=probe), (int(width), int(height)))

    # Esquina opuesta a la que se arrastra, en píxeles del escenario.
    anchor_x = start.x + (start.out_width if handle in ("tl", "bl") else 0)
    anchor_y = start.y + (start.out_height if handle in ("tl", "tr") else 0)
    center_x = anchor_x + (-lay.out_width / 2 if handle in ("tl", "bl") else lay.out_width / 2)
    center_y = anchor_y + (-lay.out_height / 2 if handle in ("tl", "tr") else lay.out_height / 2)
    return replace(
        probe,
        x=_clamp(center_x / width, 0.0, 1.0),
        y=_clamp(center_y / height, 0.0, 1.0),
    )


def as_changes(transform: Transform) -> dict[str, object]:
    """Campos listos para `core.timeline.set_transform` (que normaliza el neutro a `None`)."""
    return {
        "fit": transform.fit,
        "scale": transform.scale,
        "x": transform.x,
        "y": transform.y,
        "rotation": transform.rotation,
        "flip_h": transform.flip_h,
        "flip_v": transform.flip_v,
        "crop": transform.crop,
    }
