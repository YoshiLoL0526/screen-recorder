"""Keyframes de animación (v9, docs/31, docs/33 y docs/35): posición, escala, rotación y opacidad.

Un `Animation` es una lista de puntos `(t, value)` **en segundos locales del elemento/texto**
(`t = 0` es su `start`). La interpolación es lineal y se evalúa en el preview y en el export:

- **Textos**: `x`, `y` y `opacity` se traducen a expresiones de `drawtext` (`x=`, `y=`, `alpha=`),
  que FFmpeg evalúa por frame. `scale` y `rotation` no se pueden evaluar en `drawtext` con el
  rasterizado de PIL: el export hornea el texto a un vídeo RGBA intermedio (docs/35), igual que
  los elementos.
- **Elementos** (PiP, imágenes, figuras y V2/V3): `x`, `y`, `scale` y `rotation`. El export
  hornea escala/rotación en un vídeo RGBA intermedio (docs/33); x/y siguen siendo expresiones de
  `overlay`.

Los valores válidos dependen del canal (`CHANNEL_RANGES`): x/y/opacity van de 0 a 1, `scale` es
un multiplicador de 0.05 a 4.0 y `rotation` son grados horarios absolutos de -360 a 360. Las
curvas se acotan en los extremos (antes del primer punto vale el primero; después del último, el
último), igual que `value_at`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .errors import ProjectError

#: Rango válido de cada canal. `scale` es un multiplicador sobre la caja del elemento o el tamaño
#: del texto (1 = base); `rotation` son grados horarios absolutos (en el texto no hay base: 0° es
#: sin rotación).
CHANNEL_RANGES: dict[str, tuple[float, float]] = {
    "x": (0.0, 1.0),
    "y": (0.0, 1.0),
    "opacity": (0.0, 1.0),
    "scale": (0.05, 4.0),
    "rotation": (-360.0, 360.0),
}
CHANNELS = tuple(CHANNEL_RANGES)
TEXT_CHANNELS = ("x", "y", "opacity", "scale", "rotation")
ELEMENT_CHANNELS = ("x", "y", "scale", "rotation")
#: Canales que obligan a hornear un vídeo RGBA intermedio (docs/33 y docs/35).
TRANSFORM_CHANNELS = ("scale", "rotation")


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


@dataclass(frozen=True)
class Keyframe:
    t: float  # segundos locales (0 = inicio del elemento/texto)
    value: float

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.t)) or self.t < 0:
            raise ProjectError(f"clave de animación: tiempo inválido ({self.t})")
        if not math.isfinite(float(self.value)):
            raise ProjectError(f"clave de animación: valor inválido ({self.value})")


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
                k = (t - a.t) / span
                return a.value + (b.value - a.value) * k
        return default

    def expr(self, start: float, default: float) -> str:
        """Expresión FFmpeg en `t` global (la clave local 0 equivale a `start`)."""
        points = self.points
        if len(points) == 1:
            return _num(points[0].value)
        expression = _num(points[-1].value)
        for a, b in reversed(list(zip(points, points[1:]))):
            span = b.t - a.t
            if span <= 1e-9:
                continue
            segment = (
                f"({_num(a.value)}+({_num(b.value)}-{_num(a.value)})"
                f"*(t-{_num(start + a.t)})/{_num(span)})"
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
) -> tuple[Animation, ...]:
    """Añade/actualiza una clave en `t` (local) y devuelve la tupla ordenada por canal."""
    if channel not in CHANNELS:
        raise ProjectError(f"canal de animación desconocido ({channel!r})")
    point = Keyframe(round(float(t), 4), round(float(value), 4))
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
    "ELEMENT_CHANNELS",
    "Keyframe",
    "TEXT_CHANNELS",
    "TRANSFORM_CHANNELS",
    "channel_range",
    "clear_channel",
    "expr",
    "find",
    "is_animated",
    "set_keyframe",
    "validate",
    "validate_value",
    "value_at",
]
