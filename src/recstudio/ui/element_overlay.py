"""Geometría de la caja de manipulación de elementos sobre el preview (docs/25).

El panel del preview pinta el frame con `fit="contain"`; esta capa calcula, a partir del tamaño
medido del panel (`state.preview_box`) y del aspecto del proyecto, el rectángulo real del lienzo y
la caja del elemento seleccionado. Es puro (sin Aurora) para poder testearlo sin ventana.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import elements as el
from ..core.project import Element, Project, TextOverlay
from ..services import playback


@dataclass(frozen=True)
class PreviewFrame:
    """Rectángulo del lienzo dentro del panel y factor px de panel por px de frame."""

    left: float
    top: float
    width: float
    height: float
    scale: float
    frame_width: int
    frame_height: int


@dataclass(frozen=True)
class ElementBox:
    left: float
    top: float
    width: float
    height: float
    center_x: float
    center_y: float
    rotation: float


@dataclass(frozen=True)
class TextBox:
    """Caja de un texto activo en píxeles del panel del preview."""

    left: float
    top: float
    width: float
    height: float
    center_x: float
    center_y: float


def preview_frame(project: Project, panel: tuple[float, float]) -> PreviewFrame:
    panel_w, panel_h = float(panel[0]), float(panel[1])
    frame_w, frame_h = playback.preview_size(project)
    if panel_w <= 0 or panel_h <= 0 or frame_w <= 0 or frame_h <= 0:
        return PreviewFrame(0.0, 0.0, 0.0, 0.0, 0.0, frame_w, frame_h)
    scale = min(panel_w / frame_w, panel_h / frame_h)
    width, height = frame_w * scale, frame_h * scale
    return PreviewFrame(
        left=(panel_w - width) / 2.0,
        top=(panel_h - height) / 2.0,
        width=width,
        height=height,
        scale=scale,
        frame_width=frame_w,
        frame_height=frame_h,
    )


def element_box(project: Project, element: Element, panel: tuple[float, float]) -> ElementBox | None:
    frame = preview_frame(project, panel)
    if frame.scale <= 0:
        return None
    layout = el.layout(project, element, (frame.frame_width, frame.frame_height))
    width = layout.rotated_width * frame.scale
    height = layout.rotated_height * frame.scale
    center_x = frame.left + layout.center_x * frame.scale
    center_y = frame.top + layout.center_y * frame.scale
    return ElementBox(
        left=center_x - width / 2.0,
        top=center_y - height / 2.0,
        width=width,
        height=height,
        center_x=center_x,
        center_y=center_y,
        rotation=layout.rotation,
    )


def text_box(project: Project, overlay: TextOverlay, panel: tuple[float, float]) -> TextBox | None:
    """Caja del texto en el panel, con la misma fuente y escala que `_draw_texts`.

    `drawtext` del export usa la misma fuente y el centro normalizado; la caja del preview es
    una aproximación con las métricas reales de la fuente para poder seleccionar y arrastrar.
    """
    frame = preview_frame(project, panel)
    if frame.scale <= 0 or project.height <= 0:
        return None
    size = max(8, int(round(overlay.size * frame.frame_height / project.height)))
    font = playback.text_font(overlay.bold, size)
    try:
        left, top, right, bottom = font.getbbox(overlay.text, anchor="mm")
    except (AttributeError, TypeError, ValueError):
        left, top, right, bottom = font.getbbox(overlay.text)
    width = max(2, right - left)
    height = max(2, bottom - top)
    # Un pequeño margen para que el borde y las asas no se coman el texto ni el clic.
    pad = max(3.0, 4.0 * frame.scale)
    center_x = frame.left + overlay.x * frame.frame_width * frame.scale
    center_y = frame.top + overlay.y * frame.frame_height * frame.scale
    return TextBox(
        left=center_x + left * frame.scale - pad,
        top=center_y + top * frame.scale - pad,
        width=width * frame.scale + 2 * pad,
        height=height * frame.scale + 2 * pad,
        center_x=center_x,
        center_y=center_y,
    )
