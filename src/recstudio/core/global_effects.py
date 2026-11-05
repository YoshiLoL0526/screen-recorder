"""Efectos globales del proyecto (v6, docs/22 y docs/26).

`Project.effects` es una tupla de `Effect` que se aplica al video **ya compuesto** (tras cortes,
transiciones y transformaciones de clip) y **antes** de los elementos superpuestos y los textos.
El preview y el export comparten este módulo para que lo visto sea lo exportado:

- `apply_graph(project, video_out, chains, output_height=...)` añade los filtros FFmpeg al filter
  graph y devuelve la nueva etiqueta de video.
- `apply_frame(project, rgb)` aplica la misma cadena sobre un frame RGB del preview.

Orden exacto (el mismo en graph y preview):

1. **Color** (`eq`): brillo, contraste y saturación (curvas de `core/color.py`, las mismas del
   preview de los efectos por clip).
2. **Blur** (`gblur`): radio en píxeles del lienzo del proyecto; en una salida más pequeña (p. ej.
   el preset 720p) y en el preview se escala por `alto_salida / project.height`.
3. **Viñeta** (`vignette`): `strength` 0…1 mapeada linealmente a `angle` 0…π/2. La máscara del
   preview reproduce la fórmula de FFmpeg `cos(angle · d/dmax)^4` (ver `vignette_mask`).
4. **Blanco y negro** (`hue=s=0`): luma BT.601.

Un efecto neutro (eq por defecto, blur 0, viñeta 0) equivale a no tenerlo: `apply_graph` no añade
nada y el filter graph queda **byte a byte** como antes de esta feature (goldens de F4 intactos).

Solo se aplican los tipos soportados (`eq`, `blur`, `vignette`, `grayscale`); otros efectos
globales del modelo (p. ej. `sharpen`) se ignoran y se documentan como límite en docs/26.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from . import effects as fx
from .color import apply_eq, apply_vignette, to_grayscale
from .errors import ProjectError
from .project import Effect, Project

#: Radio máximo del blur global, en píxeles del lienzo del proyecto.
BLUR_MAX = 50.0
#: Intensidad máxima de la viñeta (0 = sin viñeta, 1 = esquinas negras).
VIGNETTE_MAX = 1.0
_NEUTRAL_EPS = 1e-6
_VIGNETTE_CACHE: dict[tuple[int, int, float], np.ndarray] = {}
_VIGNETTE_CACHE_MAX = 8


def _num(value: float) -> str:
    """Número compacto para el graph, igual que `core.graph.num` (no se importa para no crear
    un ciclo: `graph` ya importa este módulo)."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _clamp(value: float, lo: float, hi: float, fallback: float) -> float:
    """Acota a `[lo, hi]`; un valor no numérico o no finito cae en `fallback`."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(number):
        return fallback
    return max(lo, min(hi, number))


# -- lectura de parámetros -------------------------------------------------------------


def color_eq(project: Project) -> dict[str, float] | None:
    """Parámetros efectivos de `eq` (o `None` si no hay color o es neutro)."""
    values: dict[str, float] = {}
    for name, (lo, hi) in fx.EQ_RANGES.items():
        default = fx.EQ_DEFAULTS[name]
        values[name] = _clamp(fx.param_in(project.effects, "eq", name, default), lo, hi, default)
    if all(abs(values[name] - fx.EQ_DEFAULTS[name]) < _NEUTRAL_EPS for name in values):
        return None
    return values


def blur_radius(project: Project) -> float:
    return _clamp(fx.param_in(project.effects, "blur", "radius", 0.0), 0.0, BLUR_MAX, 0.0)


def vignette_strength(project: Project) -> float:
    return _clamp(
        fx.param_in(project.effects, "vignette", "strength", 0.0), 0.0, VIGNETTE_MAX, 0.0
    )


def grayscale_on(project: Project) -> bool:
    return fx.find_effect(project.effects, "grayscale") is not None


def has_effects(project: Project) -> bool:
    """¿Hay algún efecto global que cambie la imagen? (los neutros no cuentan)."""
    return (
        color_eq(project) is not None
        or blur_radius(project) > _NEUTRAL_EPS
        or vignette_strength(project) > _NEUTRAL_EPS
        or grayscale_on(project)
    )


# -- edición --------------------------------------------------------------------------


def _is_neutral(kind: str, params: dict[str, float]) -> bool:
    if kind == "eq":
        return all(abs(params.get(k, d) - d) < _NEUTRAL_EPS for k, d in fx.EQ_DEFAULTS.items())
    if kind == "blur":
        return params.get("radius", 0.0) <= _NEUTRAL_EPS
    if kind == "vignette":
        return params.get("strength", 0.0) <= _NEUTRAL_EPS
    return False  # grayscale: su presencia ya es el efecto


def _clean(kind: str, params: dict[str, float]) -> dict[str, float]:
    out = dict(params)
    if kind == "eq":
        for key, (lo, hi) in fx.EQ_RANGES.items():
            if key in out:
                out[key] = _clamp(out[key], lo, hi, fx.EQ_DEFAULTS[key])
    elif kind == "blur":
        out["radius"] = _clamp(out.get("radius", 0.0), 0.0, BLUR_MAX, 0.0)
    elif kind == "vignette":
        out["strength"] = _clamp(out.get("strength", 0.0), 0.0, VIGNETTE_MAX, 0.0)
    return {k: round(float(v), 4) for k, v in out.items()}


def with_effect(project: Project, kind: str, **params: float) -> Project:
    """Proyecto con el efecto global `kind` fusionado con `params` (se quita si queda neutro)."""
    if kind not in ("eq", "blur", "vignette", "grayscale"):
        raise ProjectError(f"efecto global no soportado: {kind}")
    current = fx.find_effect(project.effects, kind)
    if kind == "grayscale":
        if current is not None:
            return project
        return replace(project, effects=(*project.effects, Effect("grayscale")))
    merged = dict(current.params) if current is not None else {}
    merged.update({k: float(v) for k, v in params.items()})
    merged = _clean(kind, merged)
    others = tuple(e for e in project.effects if e.kind != kind)
    if _is_neutral(kind, merged):
        return replace(project, effects=others) if current is not None else project
    return replace(project, effects=(*others, Effect(kind=kind, params=tuple(sorted(merged.items())))))


def set_effect(project: Project, kind: str, **params: float) -> Project:
    """`with_effect` devolviendo el mismo proyecto si no cambia nada."""
    changed = with_effect(project, kind, **params)
    return project if changed == project else changed


def set_grayscale(project: Project, enabled: bool) -> Project:
    if enabled == grayscale_on(project):
        return project
    if enabled:
        return with_effect(project, "grayscale")
    return replace(project, effects=tuple(e for e in project.effects if e.kind != "grayscale"))


def clear_effects(project: Project) -> Project:
    return project if not project.effects else replace(project, effects=())


# -- export ---------------------------------------------------------------------------


def apply_graph(
    project: Project, video_out: str, chains: list[str], *, output_height: int | None = None
) -> str:
    """Añade una cadena con los efectos globales al video ya compuesto y devuelve su etiqueta.

    `output_height` es el alto de la salida del graph (puede ser menor que el del proyecto por el
    preset 720p); el radio del blur se escala igual que en el preview. Sin efectos (o con efectos
    neutros) no toca `chains` y devuelve `video_out` sin cambios.
    """
    if not has_effects(project):
        return video_out
    filters: list[str] = []
    eq = color_eq(project)
    if eq is not None:
        filters.append(
            f"eq=brightness={_num(eq['brightness'])}:contrast={_num(eq['contrast'])}"
            f":saturation={_num(eq['saturation'])}"
        )
    radius = blur_radius(project)
    if radius > _NEUTRAL_EPS:
        height = int(output_height or project.height)
        sigma = radius * height / project.height if project.height > 0 else radius
        if sigma > 0.05:
            filters.append(f"gblur=sigma={_num(sigma)}")
    strength = vignette_strength(project)
    if strength > _NEUTRAL_EPS:
        angle = strength * math.pi / 2.0
        filters.append(f"vignette=angle={_num(angle)}")
    if grayscale_on(project):
        filters.append("hue=s=0")
    if not filters:
        return video_out
    chains.append(f"[{video_out}]{','.join(filters)}[vglobal]")
    return "vglobal"


# -- preview --------------------------------------------------------------------------


def vignette_mask(height: int, width: int, strength: float) -> np.ndarray:
    """Máscara radial `(h, w, 1)` float32 con la fórmula del filtro `vignette` de FFmpeg."""
    strength = round(max(0.0, min(VIGNETTE_MAX, float(strength))), 4)
    key = (int(height), int(width), strength)
    cached = _VIGNETTE_CACHE.get(key)
    if cached is not None:
        return cached
    angle = strength * math.pi / 2.0
    yy, xx = np.ogrid[:height, :width]
    dmax = math.hypot(width / 2.0, height / 2.0)
    dnorm = np.hypot(xx - width / 2.0, yy - height / 2.0) / dmax
    factor = np.cos(angle * dnorm) ** 4
    mask = np.where(dnorm > 1.0, 0.0, factor).astype(np.float32)[..., None]
    if len(_VIGNETTE_CACHE) >= _VIGNETTE_CACHE_MAX:
        _VIGNETTE_CACHE.pop(next(iter(_VIGNETTE_CACHE)))
    _VIGNETTE_CACHE[key] = mask
    return mask


def apply_frame(project: Project, rgb):
    """Aplica los efectos globales a un frame RGB (numpy) con el mismo orden que el graph."""
    if not has_effects(project):
        return rgb
    out = rgb
    eq = color_eq(project)
    if eq is not None:
        out = apply_eq(out, eq)
    radius = blur_radius(project)
    if radius > _NEUTRAL_EPS and project.height > 0:
        sigma = radius * out.shape[0] / project.height
        if sigma > 0.05:
            from PIL import Image, ImageFilter

            image = Image.fromarray(np.ascontiguousarray(out))
            out = np.asarray(image.filter(ImageFilter.GaussianBlur(sigma)))
    strength = vignette_strength(project)
    if strength > _NEUTRAL_EPS:
        out = apply_vignette(out, vignette_mask(out.shape[0], out.shape[1], strength))
    if grayscale_on(project):
        out = to_grayscale(out)
    return out
