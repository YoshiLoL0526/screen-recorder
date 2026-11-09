"""Fondo del lienzo (v16, docs/47): color, desenfoque del propio video o imagen.

Módulo **puro**: decide el modo efectivo y la geometría del recorte «cover». El preview
(`services/playback.py`) y el export (`core/graph.py`) consumen estas mismas reglas, así que el
fondo no puede divergir entre lo que se ve y lo que se exporta.

- `color`: se pinta `Project.background` (comportamiento histórico).
- `blur`: una copia del frame de V1 escalada a cover y desenfocada, detrás del clip normal.
- `image`: la imagen de `Project.background_asset` escalada a cover, detrás del clip normal.

Si el modo es `image` pero el asset no existe o no es una imagen, el fondo efectivo cae a
`color`; los proyectos antiguos y los que pierden el archivo siguen abriendo.
"""

from __future__ import annotations

from .errors import ProjectError
from .project import (
    BACKGROUND_BLUR_MAX,
    BACKGROUND_BLUR_MIN,
    BACKGROUND_MODES,
    MediaAsset,
    Project,
)


def image_asset(project: Project) -> MediaAsset | None:
    """Asset de imagen del fondo, o `None` si falta o no es una imagen."""
    if project.background_mode != "image" or not project.background_asset:
        return None
    try:
        asset = project.asset(project.background_asset)
    except ProjectError:
        return None
    return asset if asset.kind == "image" else None


def effective_mode(project: Project) -> str:
    """Modo que se pinta de verdad: `image` sin asset válido cae a `color`."""
    if project.background_mode == "blur":
        return "blur"
    if project.background_mode == "image" and image_asset(project) is not None:
        return "image"
    return "color"


def has_special_background(project: Project) -> bool:
    """¿El fondo necesita la composición nueva (blur/imagen)? El color es el camino histórico."""
    return effective_mode(project) != "color"


def blur_radius(project: Project, canvas_height: int) -> float:
    """Radio de desenfoque en píxeles del lienzo de salida.

    `Project.background_blur` se guarda en píxeles del **proyecto** (0…100); al cambiar el
    tamaño del preview o del preset de export el radio se escala con el alto para que la
    intensidad se vea igual. Devuelve `0.0` si no hay blur.
    """
    if canvas_height <= 0 or project.height <= 0:
        return 0.0
    try:
        value = int(project.background_blur)
    except (TypeError, ValueError):
        value = 0
    value = max(BACKGROUND_BLUR_MIN, min(BACKGROUND_BLUR_MAX, value))
    return value * float(canvas_height) / float(project.height)


def cover_crop(
    source_width: int, source_height: int, target_width: int, target_height: int
) -> tuple[int, int, int, int]:
    """Recorte centrado `(x, y, ancho, alto)` de una imagen escalada para cubrir el lienzo.

    El ancho/alto devueltos son los de la imagen escalada (≥ lienzo) y `x`/`y` el punto del
    recorte. Es la misma geometría que `scale=…:force_original_aspect_ratio=increase,crop=…`.
    """
    target_width = max(1, int(target_width))
    target_height = max(1, int(target_height))
    if source_width <= 0 or source_height <= 0:
        return 0, 0, target_width, target_height
    scale = max(target_width / source_width, target_height / source_height)
    width = max(target_width, int(round(source_width * scale)))
    height = max(target_height, int(round(source_height * scale)))
    return (width - target_width) // 2, (height - target_height) // 2, width, height


def validate_mode(mode: str) -> str:
    """Modo de fondo válido (`color`/`blur`/`image`) o `ProjectError`."""
    text = str(mode)
    if text not in BACKGROUND_MODES:
        raise ProjectError(f"modo de fondo desconocido ({mode!r})")
    return text


def validate_blur(value: int | float) -> int:
    """Intensidad de blur entera dentro de `[0, 100]` o `ProjectError`."""
    if isinstance(value, bool):
        raise ProjectError(f"intensidad de fondo inválida ({value!r})")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ProjectError(f"intensidad de fondo inválida ({value!r})") from exc
    if not BACKGROUND_BLUR_MIN <= number <= BACKGROUND_BLUR_MAX:
        raise ProjectError(
            f"la intensidad del fondo debe estar entre {BACKGROUND_BLUR_MIN} y "
            f"{BACKGROUND_BLUR_MAX} ({value})"
        )
    return number
