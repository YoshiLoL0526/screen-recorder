"""Color animado de textos y subtítulos (v12, docs/36).

`Animation` solo puede llevar floats, así que el color se guarda en un modelo aparte:
`ColorKeyframe(t, color)` con el instante **local al texto** (`t = 0` es su `start`) y un color
`#RRGGBB`. La interpolación es lineal componente a componente en **sRGB** (los bytes 0…255 de
cada canal): es la misma que hace PIL al pintar el texto horneado y `drawtext` no participa en
esta ruta (el export hornea los textos con color animado).

Los textos sin `color_keys` conservan su `TextOverlay.color` y el camino histórico de preview y
export. El core es puro: aquí no se rasteriza ni se toca disco.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .errors import ProjectError

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _hex(red: int, green: int, blue: int) -> str:
    return f"#{red:02X}{green:02X}{blue:02X}"


@dataclass(frozen=True)
class ColorKeyframe:
    """Una clave de color en `t` segundos locales al texto (0 = su inicio)."""

    t: float
    color: str  # #RRGGBB

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.t)) or self.t < 0:
            raise ProjectError(f"clave de color: tiempo inválido ({self.t})")
        if not isinstance(self.color, str) or not _HEX_COLOR.match(self.color):
            raise ProjectError(f"clave de color: color inválido ({self.color!r}); usa #RRGGBB")
        # Normaliza a mayúsculas para que el JSON sea determinista y `==` no dependa del caso.
        object.__setattr__(self, "color", self.color.upper())


def validate(keys: tuple[ColorKeyframe, ...]) -> None:
    """Comprueba tipos, tiempos crecientes, `t >= 0` y colores hex."""
    previous = -1.0
    for key in keys:
        if not isinstance(key, ColorKeyframe):
            raise ProjectError("clave de color inválida")
        if key.t <= previous + 1e-9:
            raise ProjectError("las claves de color deben tener tiempos crecientes")
        previous = key.t


def value_at(keys: tuple[ColorKeyframe, ...], t: float, default: str) -> str:
    """Color `#RRGGBB` interpolado en el instante local `t` (acotado a la primera/última clave).

    La interpolación es lineal en sRGB y redondea cada componente al byte más cercano.
    """
    if not keys:
        return default
    if t <= keys[0].t:
        return keys[0].color
    if t >= keys[-1].t:
        return keys[-1].color
    for a, b in zip(keys, keys[1:]):
        if a.t <= t <= b.t:
            span = b.t - a.t
            if span <= 1e-9:
                return b.color
            k = (t - a.t) / span
            ar, ag, ab = _rgb(a.color)
            br, bg, bb = _rgb(b.color)
            return _hex(
                max(0, min(255, int(round(ar + (br - ar) * k)))),
                max(0, min(255, int(round(ag + (bg - ag) * k)))),
                max(0, min(255, int(round(ab + (bb - ab) * k)))),
            )
    return default


def rgb_at(keys: tuple[ColorKeyframe, ...], t: float, default: str) -> tuple[int, int, int]:
    """Igual que `value_at`, pero devuelve la tupla `(r, g, b)` que consume PIL."""
    return _rgb(value_at(keys, t, default))


def set_keyframe(
    keys: tuple[ColorKeyframe, ...], t: float, color: str,
) -> tuple[ColorKeyframe, ...]:
    """Añade/actualiza una clave en `t` local y devuelve la tupla ordenada."""
    point = ColorKeyframe(round(float(t), 4), color)
    updated = [old for old in keys if abs(old.t - point.t) > 1e-9]
    updated.append(point)
    updated.sort(key=lambda item: item.t)
    return tuple(updated)


def clear(keys: tuple[ColorKeyframe, ...]) -> tuple[ColorKeyframe, ...]:
    """Quita todas las claves de color (el texto vuelve a su color base)."""
    return ()


__all__ = [
    "ColorKeyframe",
    "clear",
    "rgb_at",
    "set_keyframe",
    "validate",
    "value_at",
]
