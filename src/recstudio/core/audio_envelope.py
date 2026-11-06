"""Envolventes de volumen de audio (v10, docs/32).

Una **envolvente** es una lista de puntos `(t, gain)` en **segundos locales del clip** (`t = 0`
es el inicio del clip en el timeline). La interpolación es lineal y se acota en los extremos:
antes del primer punto vale el primero y después del último, el último. Igual que
`core/animation.py`, pero este modelo es independiente: solo afecta al audio y sus ganancias van
de 0 a `GAIN_MAX` (4.0), no de 0 a 1.

El preview y el export comparten `value_at` y `expr`:

- **Export** (`core/graph.py`): `expr` se traduce a una expresión del filtro `volume` con
  `eval=frame`; el tiempo `t` de FFmpeg es el del tramo audible del clip.
- **Preview**: la mezcla exacta se renderiza con `core.graph.build_audio`, así que no hay una
  segunda fórmula de interpolación.

Una envolvente **multiplica** el volumen estático del clip (`Effect("volume")`) y el de su
pista; no los sustituye. Un punto con `gain=1.0` deja el sonido como está y `gain=0` lo silencia.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .errors import ProjectError

GAIN_MAX = 4.0  # mismo límite que `Effect("volume")` (core/effects.py)
_EPS = 1e-9


def _num(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _offset(value: float) -> str:
    """`t` menos un instante, sin dobles signos (`t+0.5` en vez de `t--0.5`)."""
    if value < 0:
        return f"(t+{_num(-value)})"
    return f"(t-{_num(value)})"


@dataclass(frozen=True)
class VolumePoint:
    """Un punto de la envolvente: `t` local al clip (s) y `gain` 0…4."""

    t: float
    gain: float

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.t)) or self.t < 0:
            raise ProjectError(f"clave de volumen: tiempo inválido ({self.t})")
        if not math.isfinite(float(self.gain)) or not 0.0 <= self.gain <= GAIN_MAX:
            raise ProjectError(
                f"clave de volumen: ganancia fuera de [0, {GAIN_MAX:g}] ({self.gain})"
            )


def validate(points: tuple[VolumePoint, ...]) -> None:
    """Comprueba tipos y que los tiempos sean estrictamente crecientes."""
    previous = -1.0
    for point in points:
        if not isinstance(point, VolumePoint):
            raise ProjectError("clave de volumen inválida")
        if point.t <= previous + _EPS:
            raise ProjectError("las claves de volumen deben tener tiempos crecientes")
        previous = point.t


def value_at(
    points: tuple[VolumePoint, ...], t: float, default: float = 1.0,
) -> float:
    """Ganancia interpolada en el instante local `t` (acotada a la primera/última clave)."""
    if not points:
        return default
    if t <= points[0].t:
        return points[0].gain
    if t >= points[-1].t:
        return points[-1].gain
    for a, b in zip(points, points[1:]):
        if a.t <= t <= b.t:
            span = b.t - a.t
            if span <= _EPS:
                return b.gain
            k = (t - a.t) / span
            return a.gain + (b.gain - a.gain) * k
    return default


def expr(
    points: tuple[VolumePoint, ...], start: float = 0.0, default: float = 1.0,
) -> str:
    """Expresión FFmpeg en `t` para el filtro `volume` (`eval=frame`).

    `start` es el instante que corresponde a la clave local 0 dentro del tramo donde se evalúa
    la expresión. Para el audio de un clip entrante tras una transición, el tramo audible empieza
    en `t_local = solape`, así que se pasa `start=-solape` y las claves se desplazan.

    Sin puntos devuelve `default` (una constante); con un solo punto, su ganancia.
    """
    if not points:
        return _num(default)
    if len(points) == 1:
        return _num(points[0].gain)
    expression = _num(points[-1].gain)
    for a, b in reversed(list(zip(points, points[1:]))):
        span = b.t - a.t
        if span <= _EPS:
            continue
        segment = (
            f"({_num(a.gain)}+({_num(b.gain)}-{_num(a.gain)})"
            f"*{_offset(start + a.t)}/{_num(span)})"
        )
        expression = f"if(lt(t,{_num(start + b.t)}),{segment},{expression})"
    return f"if(lt(t,{_num(start + points[0].t)}),{_num(points[0].gain)},{expression})"


def set_point(
    points: tuple[VolumePoint, ...], t: float, gain: float,
) -> tuple[VolumePoint, ...]:
    """Añade o actualiza una clave en `t` y devuelve la tupla ordenada por tiempo."""
    point = VolumePoint(round(float(t), 4), round(float(gain), 4))
    updated = [old for old in points if abs(old.t - point.t) > _EPS]
    updated.append(point)
    updated.sort(key=lambda item: item.t)
    return tuple(updated)


def clear(points: tuple[VolumePoint, ...]) -> tuple[VolumePoint, ...]:
    """Quita todas las claves de una envolvente."""
    return ()


def slice_envelope(
    points: tuple[VolumePoint, ...], start: float, end: float | None = None,
) -> tuple[VolumePoint, ...]:
    """Recorta una envolvente al tramo `[start, end)` y la reancla en `0`.

    Se usa al dividir un clip (los dos trozos conservan su parte de la curva) y al recortar o
    separar el audio (el nuevo clip empieza más tarde dentro del original). Los extremos se
    completan con `value_at`, así la curva no da un salto al cortar entre claves.
    """
    if not points:
        return ()
    start = max(0.0, float(start))
    if end is not None:
        end = float(end)
        if end <= start + _EPS:
            return ()
    result: list[VolumePoint] = []

    def add(t: float, gain: float) -> None:
        point = VolumePoint(round(max(0.0, t), 6), round(float(gain), 6))
        if result and abs(result[-1].t - point.t) <= _EPS:
            result[-1] = point
        else:
            result.append(point)

    add(0.0, value_at(points, start))
    for point in points:
        if point.t <= start + _EPS:
            continue
        if end is not None and point.t >= end - _EPS:
            continue
        add(point.t - start, point.gain)
    if end is not None:
        add(end - start, value_at(points, end))
    return tuple(result)


__all__ = [
    "GAIN_MAX",
    "VolumePoint",
    "clear",
    "expr",
    "set_point",
    "slice_envelope",
    "validate",
    "value_at",
]
