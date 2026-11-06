"""Efectos por clip (puro): lectura de parámetros, edición y las curvas que comparten el preview
y el export (ver docs/04 → «Alcance funcional» y docs/13).

Un `Clip` lleva como mucho **un** `Effect` de cada tipo. Parámetros y rangos:

| kind       | params                                   | neutro              |
|------------|------------------------------------------|---------------------|
| `eq`       | brightness −1…1, contrast 0…3, saturation 0…3 | 0, 1, 1        |
| `volume`   | gain 0…4 (multiplicador)                 | 1                   |
| `fade_in`  | seconds > 0 (video desde negro + audio)  | sin efecto          |
| `fade_out` | seconds > 0 (video a negro + audio)      | sin efecto          |
| `audio_fade_in`  | seconds > 0 (solo audio; ver docs/19) | sin efecto       |
| `audio_fade_out` | seconds > 0 (solo audio)              | sin efecto       |
| `zoom`     | factor 1…4, x/y 0…1 (centro del recorte) | 1                   |

`speed` sigue en `EFFECT_KINDS` por compatibilidad con el esquema, pero la velocidad vive en
`Clip.speed` (es la que mueve el timeline); un `Effect("speed")` se ignora.

Los fundidos son **del clip** (empiezan en su `t_local = 0` y acaban en su final), así que en
una transición entrante el fundido de video empieza dentro del solape; el de audio va sobre el
tramo audible del clip (que empieza tras el solape, convención de F3).
"""

from __future__ import annotations

from dataclasses import replace

from .audio_envelope import GAIN_MAX
from .errors import ProjectError
from .project import Clip, Effect, Project

EQ_DEFAULTS = {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0}
EQ_RANGES = {"brightness": (-1.0, 1.0), "contrast": (0.0, 3.0), "saturation": (0.0, 3.0)}
# `GAIN_MAX` se comparte con las envolventes de volumen (docs/32); se reexporta aquí para no
# romper el resto del código, que ya lo lee como `fx.GAIN_MAX`.
ZOOM_MAX = 4.0
MICRO_FADE = 0.03  # anti-click en las fronteras internas del audio (s)


def find_effect(effects: tuple[Effect, ...], kind: str) -> Effect | None:
    """Primer efecto `kind` de una tupla (sirve para clips, elementos y efectos globales)."""
    for effect in effects:
        if effect.kind == kind:
            return effect
    return None


def param_in(effects: tuple[Effect, ...], kind: str, name: str, default: float) -> float:
    """Parámetro `name` del efecto `kind` de una tupla (o `default` si falta)."""
    effect = find_effect(effects, kind)
    if effect is None:
        return default
    for key, value in effect.params:
        if key == name:
            return float(value)
    return default


def find(clip: Clip, kind: str) -> Effect | None:
    return find_effect(clip.effects, kind)


def param(clip: Clip, kind: str, name: str, default: float) -> float:
    return param_in(clip.effects, kind, name, default)


def eq(clip: Clip) -> dict[str, float] | None:
    """Parámetros de `eq` (o `None` si es neutro)."""
    values = {name: param(clip, "eq", name, default) for name, default in EQ_DEFAULTS.items()}
    if all(abs(values[k] - EQ_DEFAULTS[k]) < 1e-6 for k in values):
        return None
    return values


def gain(clip: Clip) -> float:
    return max(0.0, min(GAIN_MAX, param(clip, "volume", "gain", 1.0)))


def zoom(clip: Clip) -> tuple[float, float, float]:
    """`(factor, cx, cy)`: factor ≥ 1 y centro del recorte en fracciones del frame."""
    factor = max(1.0, min(ZOOM_MAX, param(clip, "zoom", "factor", 1.0)))
    cx = max(0.0, min(1.0, param(clip, "zoom", "x", 0.5)))
    cy = max(0.0, min(1.0, param(clip, "zoom", "y", 0.5)))
    return factor, cx, cy


def fade_in(clip: Clip) -> float:
    return max(0.0, param(clip, "fade_in", "seconds", 0.0))


def fade_out(clip: Clip) -> float:
    return max(0.0, param(clip, "fade_out", "seconds", 0.0))


def audio_fade_in(clip: Clip) -> float:
    """Fundido de audio propio; si no existe, hereda el `fade_in` histórico (video + audio)."""
    own = find(clip, "audio_fade_in")
    return max(0.0, param(clip, "audio_fade_in", "seconds", 0.0)) if own else fade_in(clip)


def audio_fade_out(clip: Clip) -> float:
    own = find(clip, "audio_fade_out")
    return max(0.0, param(clip, "audio_fade_out", "seconds", 0.0)) if own else fade_out(clip)


def fades(clip: Clip, duration: float) -> tuple[float, float]:
    """Fundidos acotados a `duration` (si no caben los dos, se reparten a partes iguales)."""
    fin, fout = fade_in(clip), fade_out(clip)
    if fin + fout > duration > 0:
        scale = duration / (fin + fout)
        fin, fout = fin * scale, fout * scale
    return fin, fout


def audio_fades(clip: Clip, duration: float) -> tuple[float, float]:
    """Igual que `fades` pero para la cadena de audio (audio_fade_* con fallback histórico)."""
    fin, fout = audio_fade_in(clip), audio_fade_out(clip)
    if fin + fout > duration > 0:
        scale = duration / (fin + fout)
        fin, fout = fin * scale, fout * scale
    return fin, fout


def fade_gain(t: float, duration: float, fin: float, fout: float) -> float:
    """Multiplicador lineal 0…1 de un fundido de entrada/salida en el instante `t` del tramo."""
    k = 1.0
    if fin > 0 and t < fin:
        k = min(k, max(0.0, t / fin))
    if fout > 0 and t > duration - fout:
        k = min(k, max(0.0, (duration - t) / fout))
    return k


def has_video_effects(clip: Clip) -> bool:
    return eq(clip) is not None or zoom(clip)[0] > 1.0 + 1e-6 or fade_in(clip) > 0 or fade_out(clip) > 0


def has_effects(clip: Clip) -> bool:
    return (
        has_video_effects(clip)
        or abs(gain(clip) - 1.0) > 1e-6
        or find(clip, "audio_fade_in") is not None
        or find(clip, "audio_fade_out") is not None
    )


# -- edición -------------------------------------------------------------------------


def _is_neutral(kind: str, params: dict[str, float]) -> bool:
    if kind == "eq":
        return all(abs(params.get(k, d) - d) < 1e-6 for k, d in EQ_DEFAULTS.items())
    if kind == "volume":
        return abs(params.get("gain", 1.0) - 1.0) < 1e-6
    if kind in ("fade_in", "fade_out", "audio_fade_in", "audio_fade_out"):
        return params.get("seconds", 0.0) <= 0
    if kind == "zoom":
        return params.get("factor", 1.0) <= 1.0 + 1e-6
    return False


def _clean(kind: str, params: dict[str, float]) -> dict[str, float]:
    out = dict(params)
    if kind == "eq":
        for key, (lo, hi) in EQ_RANGES.items():
            if key in out:
                out[key] = max(lo, min(hi, out[key]))
    elif kind == "volume":
        out["gain"] = max(0.0, min(GAIN_MAX, out.get("gain", 1.0)))
    elif kind in ("fade_in", "fade_out", "audio_fade_in", "audio_fade_out"):
        out["seconds"] = max(0.0, out.get("seconds", 0.0))
    elif kind == "zoom":
        out["factor"] = max(1.0, min(ZOOM_MAX, out.get("factor", 1.0)))
    return {k: round(float(v), 4) for k, v in out.items()}


def with_effect(clip: Clip, kind: str, **params: float) -> Clip:
    """Clip con el efecto `kind` fusionado con `params` (se quita si queda neutro)."""
    if kind == "speed":
        raise ProjectError("la velocidad se cambia con `timeline.set_speed`")
    current = find(clip, kind)
    merged = dict(current.params) if current is not None else {}
    merged.update({k: float(v) for k, v in params.items()})
    merged = _clean(kind, merged)
    others = tuple(e for e in clip.effects if e.kind != kind)
    if _is_neutral(kind, merged):
        return replace(clip, effects=others)
    effect = Effect(kind=kind, params=tuple(sorted(merged.items())))  # type: ignore[arg-type]
    return replace(clip, effects=(*others, effect))


def set_effect(p: Project, clip_id: str, kind: str, **params: float) -> Project:
    """`with_effect` sobre el clip `clip_id` del track de video."""
    index = p.video.index_of(clip_id)
    clip = p.video.clips[index]
    new = with_effect(clip, kind, **params)
    if new == clip:
        return p
    clips = list(p.video.clips)
    clips[index] = new
    return replace(p, video=replace(p.video, clips=tuple(clips)))


def clear_effects(p: Project, clip_id: str) -> Project:
    index = p.video.index_of(clip_id)
    clip = p.video.clips[index]
    if not clip.effects:
        return p
    clips = list(p.video.clips)
    clips[index] = replace(clip, effects=())
    return replace(p, video=replace(p.video, clips=tuple(clips)))
