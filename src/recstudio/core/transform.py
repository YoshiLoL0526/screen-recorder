"""Matemática pura del lienzo y la transformación de clip (fase 2, docs/20).

El preview (`services/playback.py`) y el export (`core/graph.py`) consumen **este** módulo, así
que el encaje, el recorte, la escala, la posición, la rotación y el volteo no pueden divergir.

Cadena de una imagen:

    auto-rotación del asset → rotación/flip del clip → crop normalizado →
    fit/fill/stretch + `scale` → posición (x, y) sobre el lienzo → fondo → eq → fundidos → texto

`fit` escala manteniendo el aspecto hasta que el clip quepa (puede quedar fondo); `fill` escala
hasta cubrir el lienzo (el excedente se recorta contra los bordes); `stretch` escala exactamente
a las proporciones del lienzo. `scale` multiplica el resultado del encaje.

Los valores de `Layout` están en píxeles del lienzo pedido (el del proyecto o el del preset de
export). Para el preview se pide el mismo `Layout` con el tamaño reducido del preview, de modo
que las fracciones y el orden son idénticos.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ProjectError
from .project import Clip, Project, Transform, normalize_rotation  # noqa: F401  (reexport)

FULL_CROP = (0.0, 0.0, 1.0, 1.0)


def _even(value: float) -> int:
    """Entero par ≥ 2 (yuv420p pide dimensiones pares para escalar con seguridad)."""
    number = max(2, int(round(value)))
    return number - number % 2


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    """`#RRGGBB` → `(r, g, b)`; `ProjectError` si no es un color válido."""
    text = str(color).strip()
    if len(text) != 7 or not text.startswith("#"):
        raise ProjectError(f"color inválido ({color!r}); usa #RRGGBB")
    try:
        return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)
    except ValueError as exc:
        raise ProjectError(f"color inválido ({color!r}); usa #RRGGBB") from exc


def ffmpeg_color(color: str) -> str:
    """Color de FFmpeg (`0xRRGGBB`) a partir del color del proyecto."""
    hex_to_rgb(color)  # valida
    return "0x" + str(color).strip().lstrip("#").upper()


def source_size(project: Project, clip: Clip) -> tuple[int, int]:
    """Dimensiones de la imagen que entra al `crop`, tras auto-rotación y rotación del clip.

    `MediaAsset.width/height` ya son las dimensiones **mostradas** (probe intercambia ancho y
    alto si el stream trae rotación 90/270), así que aquí solo hay que aplicar la rotación
    propia del clip.
    """
    asset = project.asset(clip.asset_id)
    width, height = int(asset.width), int(asset.height)
    rotation = int(clip.transform.rotation) if clip.transform is not None else 0
    if normalize_rotation(rotation) in (90, 270):
        width, height = height, width
    return max(1, width), max(1, height)


def rotation_filters(rotation: int, *, clockwise: bool) -> list[str]:
    """Filtros FFmpeg (`transpose`/flips) equivalentes a `rotation`."""
    rotation = normalize_rotation(rotation)
    if rotation == 0:
        return []
    if rotation == 180:
        return ["hflip", "vflip"]
    if clockwise:
        # transpose=1 gira 90° horario; transpose=2, 90° antihorario.
        return ["transpose=1"] if rotation == 90 else ["transpose=2"]
    # La auto-rotación de FFmpeg (display matrix) es antihoraria para +90.
    return ["transpose=2"] if rotation == 90 else ["transpose=1"]


@dataclass(frozen=True)
class Layout:
    """Geometría calculada de un clip sobre un lienzo concreto (píxeles)."""

    canvas_width: int
    canvas_height: int
    background: str
    fit: str
    asset_rotation: int
    rotation: int
    flip_h: bool
    flip_v: bool
    crop: tuple[float, float, float, float]
    source_width: int  # después de auto-rotación y rotación del clip
    source_height: int
    crop_x: int
    crop_y: int
    crop_width: int
    crop_height: int
    scale: float
    center_x: float
    center_y: float
    out_width: int
    out_height: int
    x: int  # esquina superior izquierda del clip en el lienzo (puede ser negativa)
    y: int

    @property
    def is_full_frame(self) -> bool:
        return (
            self.out_width == self.canvas_width
            and self.out_height == self.canvas_height
            and self.x == 0
            and self.y == 0
        )


def layout(
    project: Project, clip: Clip, canvas: tuple[int, int] | None = None
) -> Layout:
    """Geometría del clip en `canvas` (por defecto, el lienzo del proyecto)."""
    canvas_width, canvas_height = canvas or (project.width, project.height)
    canvas_width, canvas_height = int(canvas_width), int(canvas_height)
    if canvas_width <= 0 or canvas_height <= 0:
        raise ProjectError("el lienzo del proyecto debe tener ancho y alto positivos")
    asset = project.asset(clip.asset_id)
    transform = clip.transform or Transform()
    fit = project.default_fit if transform.fit == "inherit" else transform.fit
    if fit not in ("fit", "fill", "stretch"):
        raise ProjectError(f"encaje desconocido ({fit!r})")

    source_width, source_height = source_size(project, clip)
    left, top, right, bottom = (float(v) for v in transform.crop)
    left = max(0.0, min(left, 1.0))
    right = max(0.0, min(right, 1.0))
    top = max(0.0, min(top, 1.0))
    bottom = max(0.0, min(bottom, 1.0))
    if not (left < right and top < bottom):
        raise ProjectError(f"recorte inválido ({transform.crop})")

    crop_x = min(source_width - 1, max(0, int(round(source_width * left))))
    crop_y = min(source_height - 1, max(0, int(round(source_height * top))))
    crop_right = min(source_width, max(crop_x + 1, int(round(source_width * right))))
    crop_bottom = min(source_height, max(crop_y + 1, int(round(source_height * bottom))))
    crop_width, crop_height = crop_right - crop_x, crop_bottom - crop_y

    scale = max(0.01, float(transform.scale))
    if fit == "stretch":
        out_width = _even(canvas_width * scale)
        out_height = _even(canvas_height * scale)
    else:
        if fit == "fill":
            base = max(canvas_width / crop_width, canvas_height / crop_height)
        else:  # fit
            base = min(canvas_width / crop_width, canvas_height / crop_height)
        out_width = _even(crop_width * base * scale)
        out_height = _even(crop_height * base * scale)
    x = int(round(float(transform.x) * canvas_width - out_width / 2.0))
    y = int(round(float(transform.y) * canvas_height - out_height / 2.0))
    return Layout(
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        background=project.background,
        fit=fit,
        asset_rotation=normalize_rotation(asset.rotation),
        rotation=normalize_rotation(transform.rotation),
        flip_h=bool(transform.flip_h),
        flip_v=bool(transform.flip_v),
        crop=transform.crop,
        source_width=source_width,
        source_height=source_height,
        crop_x=crop_x,
        crop_y=crop_y,
        crop_width=crop_width,
        crop_height=crop_height,
        scale=scale,
        center_x=float(transform.x),
        center_y=float(transform.y),
        out_width=out_width,
        out_height=out_height,
        x=x,
        y=y,
    )


def needs_custom_render(project: Project, clip: Clip) -> bool:
    """¿Hace falta la cadena nueva? Si no, se mantiene el graph histórico de F4 (golden)."""
    if project.default_fit != "fit" or project.background.upper() != "#000000":
        return True
    if clip.transform is not None:
        return True
    return normalize_rotation(project.asset(clip.asset_id).rotation) != 0


def compose_filters(lay: Layout) -> list[str]:
    """Filtros `pad`/`crop` que colocan el clip en el lienzo y rellenan con el fondo.

    Se calcula el rectángulo que cubre el lienzo y el clip; `pad` lo pinta y `crop` deja
    exactamente el lienzo. Funciona con offsets negativos (el clip se sale por cualquier lado).
    """
    canvas_w, canvas_h = lay.canvas_width, lay.canvas_height
    if lay.is_full_frame:
        return []
    origin_x = min(0, lay.x)
    origin_y = min(0, lay.y)
    end_x = max(canvas_w, lay.x + lay.out_width)
    end_y = max(canvas_h, lay.y + lay.out_height)
    pad_w, pad_h = end_x - origin_x, end_y - origin_y
    pad_x, pad_y = lay.x - origin_x, lay.y - origin_y
    steps = [
        f"pad={pad_w}:{pad_h}:{pad_x}:{pad_y}:color={ffmpeg_color(lay.background)}"
    ]
    if (origin_x, origin_y) != (0, 0) or pad_w != canvas_w or pad_h != canvas_h:
        steps.append(f"crop={canvas_w}:{canvas_h}:{-origin_x}:{-origin_y}")
    return steps
