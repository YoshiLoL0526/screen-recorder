"""Keyframes de animación (v9, docs/31, docs/33, docs/35 y docs/36): posición, escala, rotación,
opacidad y tamaño de texto.

Un `Animation` es una lista de puntos `(t, value)` **en segundos locales del elemento/texto**
(`t = 0` es su `start`). La interpolación es lineal y se evalúa en el preview y en el export:

- **Textos**: `x`, `y` y `opacity` se traducen a expresiones de `drawtext` (`x=`, `y=`, `alpha=`),
  que FFmpeg evalúa por frame. `scale`, `rotation` y `size` no se pueden evaluar en `drawtext`
  con el rasterizado de PIL: el export hornea el texto a un vídeo RGBA intermedio (docs/35 y
  docs/36), igual que los elementos.
- **Elementos** (PiP, imágenes, figuras y V2/V3): `x`, `y`, `scale` y `rotation`. El export
  hornea escala/rotación en un vídeo RGBA intermedio (docs/33); x/y siguen siendo expresiones de
  `overlay`.

Los valores válidos dependen del canal (`CHANNEL_RANGES`): x/y/opacity van de 0 a 1, `scale` es
un multiplicador de 0.05 a 4.0, `rotation` son grados horarios absolutos de -360 a 360 y `size`
es el tamaño de fuente base del texto, de 8 a 200 (el mismo rango que `TEXT_SIZE_MIN/MAX`; se
repite aquí para que el core no dependa de `project`). Las curvas se acotan en los extremos
(antes del primer punto vale el primero; después del último, el último), igual que `value_at`.

Cada clave tiene un **easing** (`linear`, `ease_in`, `ease_out` o `ease_in_out`) que se aplica al
tramo que **sale** de ella (docs/39). La última clave no tiene tramo saliente, así que su easing no
afecta a la curva; se conserva para que volver a grabar el mismo instante no pierda el ajuste.
`value_at` y `expr` comparten `ease`: la curva del preview y la expresión de FFmpeg son la misma
función, y `linear` mantiene la expresión histórica byte a byte.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .errors import ProjectError

Easing = Literal["linear", "ease_in", "ease_out", "ease_in_out"]
#: Curvas disponibles; el valor se valida en `Keyframe` y en `ease`.
EASING_KINDS: tuple[Easing, ...] = ("linear", "ease_in", "ease_out", "ease_in_out")

#: Rango válido de cada canal. `scale` es un multiplicador sobre la caja del elemento o el tamaño
#: del texto (1 = base); `rotation` son grados horarios absolutos (en el texto no hay base: 0° es
#: sin rotación); `size` es el tamaño de fuente base del texto (8…200, docs/36).
CHANNEL_RANGES: dict[str, tuple[float, float]] = {
    "x": (0.0, 1.0),
    "y": (0.0, 1.0),
    "opacity": (0.0, 1.0),
    "scale": (0.05, 4.0),
    "rotation": (-360.0, 360.0),
    "size": (8.0, 200.0),
}
CHANNELS = tuple(CHANNEL_RANGES)
TEXT_CHANNELS = ("x", "y", "opacity", "scale", "rotation", "size")
ELEMENT_CHANNELS = ("x", "y", "scale", "rotation")
#: Canales que obligan a hornear un vídeo RGBA intermedio (docs/33, docs/35 y docs/36).
TRANSFORM_CHANNELS = ("scale", "rotation")
#: Canales de texto que `drawtext` no puede evaluar (tamaño incluido, docs/36).
TEXT_BAKE_CHANNELS = (*TRANSFORM_CHANNELS, "size")


def channel_range(channel: str) -> tuple[float, float]:
    """Rango `(mínimo, máximo)` de un canal; `ProjectError` si no existe."""
    try:
        return CHANNEL_RANGES[channel]
    except KeyError as exc:
        raise ProjectError(f"canal de animación desconocido ({channel!r})") from exc


def validate_value(channel: str, value: float) -> None:
    """Valida un valor para `channel` (la comprobación es por canal, no global)."""
    low, high = channel_range(channel)
    if not math.isfinite(float(value)) or not low <= float(value) <= high:
        raise ProjectError(
            f"clave de animación: valor fuera de [{low:g}, {high:g}] para {channel} ({value})"
        )


def _num(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def validate_easing(kind: str) -> None:
    """`ProjectError` si `kind` no es una curva de `EASING_KINDS`."""
    if kind not in EASING_KINDS:
        raise ProjectError(f"easing desconocido ({kind!r})")


def ease(k: float, kind: Easing = "linear") -> float:
    """Curva de interpolación normalizada (`k` 0…1), compartida por preview y export.

    - `linear`: `k`.
    - `ease_in`: cúbica de entrada, `k³` (arranca despacio y acelera).
    - `ease_out`: cúbica de salida, `1-(1-k)³` (arranca rápido y frena).
    - `ease_in_out`: cúbica clásica por tramos; `4k³` hasta 0.5 y `1-(2-2k)³/2` después
      (despacio → rápido → despacio). En el punto medio vale 0.5, igual que la lineal.

    `k` se acota a 0…1; fuera de ese rango no se extrapola. La misma fórmula se escribe como
    multiplicaciones en `Animation.expr` para que FFmpeg evalúe exactamente la misma curva.
    """
    validate_easing(kind)
    k = min(1.0, max(0.0, float(k)))
    if kind == "linear":
        return k
    if kind == "ease_in":
        return k * k * k
    if kind == "ease_out":
        inverse = 1.0 - k
        return 1.0 - inverse * inverse * inverse
    if k < 0.5:
        return 4.0 * k * k * k
    inverse = 2.0 - 2.0 * k
    return 1.0 - inverse * inverse * inverse / 2.0


def _curve_expr(k: str, kind: Easing) -> str:
    """`ease(k, kind)` como expresión FFmpeg; `k` ya viene entre paréntesis."""
    if kind == "linear":
        return k
    if kind == "ease_in":
        return f"{k}*{k}*{k}"
    if kind == "ease_out":
        inverse = f"(1-{k})"
        return f"(1-{inverse}*{inverse}*{inverse})"
    inverse = f"(2-2*{k})"
    return (
        f"if(lt({k},0.5),(4*{k}*{k}*{k}),"
        f"(1-{inverse}*{inverse}*{inverse}/2))"
    )


@dataclass(frozen=True)
class Keyframe:
    t: float  # segundos locales (0 = inicio del elemento/texto)
    value: float
    easing: Easing = "linear"  # curva del tramo que sale de esta clave (docs/39)

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.t)) or self.t < 0:
            raise ProjectError(f"clave de animación: tiempo inválido ({self.t})")
        if not math.isfinite(float(self.value)):
            raise ProjectError(f"clave de animación: valor inválido ({self.value})")
        validate_easing(self.easing)


@dataclass(frozen=True)
class Animation:
    channel: str
    points: tuple[Keyframe, ...]

    def __post_init__(self) -> None:
        if self.channel not in CHANNELS:
            raise ProjectError(f"canal de animación desconocido ({self.channel!r})")
        if not self.points:
            raise ProjectError("una animación necesita al menos una clave")
        previous = -1.0
        for point in self.points:
            if point.t <= previous + 1e-9:
                raise ProjectError("las claves de animación deben tener tiempos crecientes")
            validate_value(self.channel, point.value)
            previous = point.t

    def value_at(self, t: float, default: float) -> float:
        """Valor interpolado en el instante local `t` (acotado a la primera/última clave)."""
        points = self.points
        if t <= points[0].t:
            return points[0].value
        if t >= points[-1].t:
            return points[-1].value
        for a, b in zip(points, points[1:]):
            if a.t <= t <= b.t:
                span = b.t - a.t
                if span <= 1e-9:
                    return b.value
                k = ease((t - a.t) / span, a.easing)
                return a.value + (b.value - a.value) * k
        return default

    def expr(self, start: float, default: float) -> str:
        """Expresión FFmpeg en `t` global (la clave local 0 equivale a `start`).

        Cada tramo usa el easing de su clave izquierda. La rama lineal es exactamente la
        expresión histórica (`a+(b-a)*(t-X)/S`); las curvas se escriben con multiplicaciones para
        no depender de `pow` y para que FFmpeg evalúe la misma fórmula que `ease`.
        """
        points = self.points
        if len(points) == 1:
            return _num(points[0].value)
        expression = _num(points[-1].value)
        for a, b in reversed(list(zip(points, points[1:]))):
            span = b.t - a.t
            if span <= 1e-9:
                continue
            k = f"(t-{_num(start + a.t)})/{_num(span)}"
            if a.easing == "linear":
                segment = (
                    f"({_num(a.value)}+({_num(b.value)}-{_num(a.value)})*{k})"
                )
            else:
                curve = _curve_expr(f"({k})", a.easing)
                segment = (
                    f"({_num(a.value)}+({_num(b.value)}-{_num(a.value)})*{curve})"
                )
            expression = f"if(lt(t,{_num(start + b.t)}),{segment},{expression})"
        return f"if(lt(t,{_num(start + points[0].t)}),{_num(points[0].value)},{expression})"


def find(animations: tuple[Animation, ...], channel: str) -> Animation | None:
    return next((animation for animation in animations if animation.channel == channel), None)


def value_at(
    animations: tuple[Animation, ...], channel: str, t: float, default: float,
) -> float:
    animation = find(animations, channel)
    return default if animation is None else animation.value_at(t, default)


def expr(
    animations: tuple[Animation, ...], channel: str, start: float, default: float,
) -> str:
    animation = find(animations, channel)
    return _num(default) if animation is None else animation.expr(start, default)


def is_animated(animations: tuple[Animation, ...], channel: str | None = None) -> bool:
    if channel is None:
        return bool(animations)
    return find(animations, channel) is not None


def set_keyframe(
    animations: tuple[Animation, ...], channel: str, t: float, value: float,
    easing: Easing = "linear",
) -> tuple[Animation, ...]:
    """Añade/actualiza una clave en `t` (local) y devuelve la tupla ordenada por canal.

    Volver a grabar en el mismo instante **sustituye** la clave, incluido su `easing` (el
    inspector pasa el valor elegido en el selector).
    """
    if channel not in CHANNELS:
        raise ProjectError(f"canal de animación desconocido ({channel!r})")
    validate_easing(easing)
    point = Keyframe(round(float(t), 4), round(float(value), 4), easing)
    current = find(animations, channel)
    points = list(current.points) if current is not None else []
    points = [old for old in points if abs(old.t - point.t) > 1e-9]
    points.append(point)
    points.sort(key=lambda item: item.t)
    updated = Animation(channel, tuple(points))
    others = tuple(animation for animation in animations if animation.channel != channel)
    return (*others, updated)


def clear_channel(animations: tuple[Animation, ...], channel: str) -> tuple[Animation, ...]:
    return tuple(animation for animation in animations if animation.channel != channel)


def validate(animations: tuple[Animation, ...], allowed: tuple[str, ...]) -> None:
    """Comprueba canales permitidos, sin duplicados y con valores válidos."""
    seen: set[str] = set()
    for animation in animations:
        if not isinstance(animation, Animation):
            raise ProjectError("animación inválida")
        if animation.channel not in allowed:
            raise ProjectError(f"canal de animación no válido aquí ({animation.channel!r})")
        if animation.channel in seen:
            raise ProjectError(f"canal de animación duplicado ({animation.channel})")
        seen.add(animation.channel)


__all__ = [
    "Animation",
    "CHANNELS",
    "CHANNEL_RANGES",
    "EASING_KINDS",
    "ELEMENT_CHANNELS",
    "Easing",
    "Keyframe",
    "TEXT_BAKE_CHANNELS",
    "TEXT_CHANNELS",
    "TRANSFORM_CHANNELS",
    "channel_range",
    "clear_channel",
    "ease",
    "expr",
    "find",
    "is_animated",
    "set_keyframe",
    "validate",
    "validate_easing",
    "validate_value",
    "value_at",
]
