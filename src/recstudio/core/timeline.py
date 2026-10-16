"""Matemática y operaciones del timeline (funciones puras sobre `Project`, ver docs/04).

Notación: clips `1..n` con duraciones `d` (con velocidad), transiciones `t` (`t[k]` une `k` y
`k+1`):

    D₀ = 0;  Dₖ = Dₖ₋₁ + dₖ − tₖ₋₁        total = Σd − Σt
    start(clip k+1) = Dₖ − tₖ

Dentro del solape de una transición manda el clip **saliente** (`locate` devuelve el clip `k`
hasta `Dₖ`); el entrante se consulta con `transition_at`. El audio del preview sigue esa misma
convención, así que el audio dura lo mismo que el video.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .errors import ProjectError
from .project import (
    SPEED_MAX,
    SPEED_MIN,
    TRANSITION_MIN,
    Clip,
    MediaAsset,
    Project,
    Track,
    Transition,
    new_id,
    transition_limit,
)

ZOOM_MIN, ZOOM_MAX = 5.0, 600.0  # px por segundo
SNAP_PX = 8.0


@dataclass(frozen=True)
class ClipPos:
    index: int
    clip: Clip
    t_local: float  # segundos desde el inicio del clip en el timeline
    t_source: float  # segundos dentro del asset


@dataclass(frozen=True)
class TransitionPos:
    index: int  # transitions[index] une el clip index con index + 1
    transition: Transition
    progress: float  # 0 → 1 a lo largo del solape
    incoming: ClipPos


# -- tiempos ---------------------------------------------------------------------


def clip_starts(track: Track) -> list[float]:
    """Inicio global de cada clip (aplicando los solapes de las transiciones)."""
    starts: list[float] = []
    cursor = 0.0
    for index, clip in enumerate(track.clips):
        starts.append(cursor)
        overlap = 0.0
        if index < len(track.transitions) and track.transitions[index] is not None:
            overlap = track.transitions[index].duration
        cursor += clip.duration - overlap
    return starts


def track_duration(track: Track) -> float:
    if not track.clips:
        return 0.0
    starts = clip_starts(track)
    return starts[-1] + track.clips[-1].duration


def total_duration(p: Project) -> float:
    return track_duration(p.video)


def clip_start(p: Project, track: Track, index: int) -> float:
    return clip_starts(track)[index]


def clip_range(p: Project, index: int) -> tuple[float, float]:
    start = clip_starts(p.video)[index]
    return start, start + p.video.clips[index].duration


def _pos(clip: Clip, index: int, t_local: float) -> ClipPos:
    t_local = max(0.0, min(t_local, clip.duration))
    return ClipPos(index, clip, t_local, clip.in_ + t_local * clip.speed)


def locate(p: Project, t: float) -> ClipPos | None:
    """Clip que se ve en el instante global `t` (el saliente dentro de una transición)."""
    clips = p.video.clips
    if not clips:
        return None
    total = total_duration(p)
    t = max(0.0, min(t, total))
    starts = clip_starts(p.video)
    for index, (clip, start) in enumerate(zip(clips, starts)):
        if t < start + clip.duration:
            return _pos(clip, index, t - start)
    last = len(clips) - 1
    return _pos(clips[last], last, clips[last].duration)


def transition_at(p: Project, t: float) -> TransitionPos | None:
    """Transición activa en `t` (si `t` cae en el solape de dos clips)."""
    track = p.video
    starts = clip_starts(track)
    for index, transition in enumerate(track.transitions):
        if transition is None:
            continue
        begin = starts[index + 1]
        end = begin + transition.duration
        if begin <= t < end:
            incoming = _pos(track.clips[index + 1], index + 1, t - begin)
            return TransitionPos(index, transition, (t - begin) / transition.duration, incoming)
    return None


def frame_round(t: float, fps: float) -> float:
    """Frontera de frame más cercana (`round(t * fps) / fps`)."""
    return round(t * fps) / fps if fps > 0 else t


def frame_index(t: float, fps: float) -> int:
    return int(round(t * fps))


def time_at_x(x: float, zoom: float, scroll: float = 0.0) -> float:
    return (x + scroll) / zoom


def x_at_time(t: float, zoom: float, scroll: float = 0.0) -> float:
    return t * zoom - scroll


def clamp_zoom(zoom: float) -> float:
    return max(ZOOM_MIN, min(ZOOM_MAX, zoom))


def fit_zoom(duration: float, width_px: float) -> float:
    """Zoom que encaja `duration` en `width_px` (con un margen del 4 %)."""
    if duration <= 0 or width_px <= 0:
        return 60.0
    return clamp_zoom(width_px * 0.96 / duration)


def format_time(t: float, *, millis: bool = True) -> str:
    """`mm:ss.mmm` (o `h:mm:ss.mmm` a partir de una hora)."""
    t = max(0.0, t)
    total_ms = int(round(t * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, ms = divmod(rem, 1000)
    base = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
    return f"{base}.{ms:03d}" if millis else base


def parse_time(text: str) -> float:
    """Inverso de `format_time`; acepta `ss`, `ss.mmm`, `mm:ss.mmm` y `h:mm:ss.mmm` (y coma)."""
    raw = text.strip().replace(",", ".")
    if not raw:
        raise ValueError("tiempo vacío")
    parts = raw.split(":")
    if len(parts) > 3:
        raise ValueError(f"tiempo inválido: {text!r}")
    total = 0.0
    for part in parts:
        value = float(part)
        if value < 0 or math.isnan(value):
            raise ValueError(f"tiempo inválido: {text!r}")
        total = total * 60 + value
    return total


# -- transiciones ------------------------------------------------------------------


def fit_transitions(track: Track) -> Track:
    """Acota cada transición a `min(dur_a, dur_b) / 2`; quita las que no llegan al mínimo."""
    if not track.transitions:
        return track
    fitted: list[Transition | None] = []
    for index, transition in enumerate(track.transitions):
        if transition is None:
            fitted.append(None)
            continue
        limit = transition_limit(track, index)
        if limit < TRANSITION_MIN:
            fitted.append(None)
        elif transition.duration > limit:
            fitted.append(replace(transition, duration=limit))
        else:
            fitted.append(transition)
    new = tuple(fitted)
    return track if new == track.transitions else replace(track, transitions=new)


def set_transition(p: Project, index: int, kind: str | None, duration: float = 0.5) -> tuple[Project, bool]:
    """Pone (o quita con `kind=None`) la transición `index`. Devuelve `(proyecto, acotada)`."""
    track = p.video
    if not 0 <= index < len(track.transitions):
        raise ProjectError(f"no hay hueco {index} entre clips")
    transitions = list(track.transitions)
    clamped = False
    if kind is None:
        transitions[index] = None
    else:
        limit = transition_limit(track, index)
        if limit < TRANSITION_MIN:
            raise ProjectError("los clips son demasiado cortos para una transición (mínimo 0.4 s cada uno)")
        wanted = max(TRANSITION_MIN, duration)
        if wanted > limit:
            wanted, clamped = limit, True
        transitions[index] = Transition(kind=kind, duration=round(wanted, 3))  # type: ignore[arg-type]
    return replace(p, video=replace(track, transitions=tuple(transitions))), clamped


# -- edición -----------------------------------------------------------------------


def _with_track(p: Project, clips: list[Clip], transitions: list[Transition | None]) -> Project:
    track = replace(p.video, clips=tuple(clips), transitions=tuple(transitions))
    return replace(p, video=fit_transitions(track))


def _remove_at(clips: list[Clip], transitions: list[Transition | None], i: int) -> Clip:
    n = len(clips)
    clip = clips.pop(i)
    if n == 1:
        transitions.clear()
    elif i == 0:
        transitions.pop(0)
    elif i == n - 1:
        transitions.pop(n - 2)
    else:  # los vecinos quedan unidos por un corte limpio
        transitions[i - 1 : i + 1] = [None]
    return clip


def _insert_at(clips: list[Clip], transitions: list[Transition | None], j: int, clip: Clip) -> None:
    clips.insert(j, clip)
    n = len(clips)
    if n == 1:
        return
    if j == 0:
        transitions.insert(0, None)
    elif j == n - 1:
        transitions.append(None)
    else:  # el hueco donde cae pierde su transición: nacen dos cortes limpios
        transitions[j - 1] = None
        transitions.insert(j, None)


def _source_grid(p: Project, clip: Clip) -> float:
    """fps de la rejilla de la fuente (frames reales del asset; el del proyecto si no se sabe)."""
    try:
        fps = p.asset(clip.asset_id).fps
    except ProjectError:
        fps = 0.0
    return fps if fps > 0 else float(p.fps)


def min_source_length(p: Project, clip: Clip, speed: float | None = None) -> float:
    """Longitud mínima en la fuente: 1 frame del proyecto en el timeline."""
    return (speed or clip.speed) / p.fps


def split(p: Project, t: float) -> Project:
    """Corta el clip bajo `t` (redondeado a frame). El izquierdo conserva la transición entrante;
    el derecho, la saliente; la frontera nueva nace sin transición."""
    t = frame_round(t, p.fps)
    pos = locate(p, t)
    if pos is None:
        raise ProjectError("no hay clips que dividir")
    clip = pos.clip
    frame = 1.0 / p.fps
    if pos.t_local < frame - 1e-9 or clip.duration - pos.t_local < frame - 1e-9:
        raise ProjectError("el playhead está en el borde del clip: no hay nada que dividir")
    grid = _source_grid(p, clip)
    cut = frame_round(pos.t_source, grid)
    min_len = min_source_length(p, clip)
    cut = max(clip.in_ + min_len, min(cut, clip.out_ - min_len))
    left_effects = tuple(e for e in clip.effects if e.kind != "fade_out")
    right_effects = tuple(e for e in clip.effects if e.kind != "fade_in")
    left = replace(clip, out_=cut, effects=left_effects)
    right = replace(clip, id=new_id("c"), in_=cut, effects=right_effects)
    clips = list(p.video.clips)
    transitions = list(p.video.transitions)
    clips[pos.index : pos.index + 1] = [left, right]
    transitions.insert(pos.index, None)
    return _with_track(p, clips, transitions)


def trim(p: Project, clip_id: str, new_in: float, new_out: float) -> Project:
    """Nuevos `in_`/`out_` (fuente), acotados al asset, a frame y a ≥ 1 frame de duración."""
    index = p.video.index_of(clip_id)
    clip = p.video.clips[index]
    asset = p.asset(clip.asset_id)
    grid = _source_grid(p, clip)
    min_len = min_source_length(p, clip)
    moved_in = abs(new_in - clip.in_) > 1e-9
    moved_out = abs(new_out - clip.out_) > 1e-9
    # Solo se redondea a frame el borde que se mueve (el otro se respeta tal cual).
    new_in = frame_round(max(0.0, new_in), grid) if moved_in else clip.in_
    if moved_out:
        new_out = min(asset.duration, frame_round(new_out, grid))
        if new_out >= asset.duration - 0.5 / grid:
            new_out = asset.duration
    else:
        new_out = clip.out_
    if new_out - new_in < min_len:
        if moved_in and not moved_out:
            new_in = new_out - min_len
        else:
            new_out = new_in + min_len
    new_in = max(0.0, new_in)
    new_out = min(asset.duration, max(new_out, new_in + min_len))
    if new_in == clip.in_ and new_out == clip.out_:
        return p
    clips = list(p.video.clips)
    clips[index] = replace(clip, in_=new_in, out_=new_out)
    return _with_track(p, clips, list(p.video.transitions))


def move(p: Project, clip_id: str, to_index: int) -> Project:
    """Reordena: el clip pasa a ocupar `to_index` (índice final). Sus transiciones se pierden."""
    clips = list(p.video.clips)
    transitions = list(p.video.transitions)
    index = p.video.index_of(clip_id)
    to_index = max(0, min(to_index, len(clips) - 1))
    if to_index == index:
        return p
    clip = _remove_at(clips, transitions, index)
    _insert_at(clips, transitions, to_index, clip)
    return _with_track(p, clips, transitions)


def drop_index(p: Project, clip_id: str, ghost_start: float) -> int:
    """Índice final de un clip arrastrado cuyo fantasma empieza en `ghost_start`.

    Regla clásica: el clip adelanta a un vecino cuando el borde delantero del fantasma pasa el
    centro de ese vecino (posiciones originales, con el clip arrastrado aún en su sitio).
    """
    track = p.video
    index = track.index_of(clip_id)
    dragged = track.clips[index]
    starts = clip_starts(track)
    centers = [s + c.duration / 2 for s, c in zip(starts, track.clips)]
    if ghost_start >= starts[index]:
        ghost_end = ghost_start + dragged.duration
        passed = sum(1 for j in range(index + 1, len(centers)) if centers[j] < ghost_end)
        return index + passed
    passed = sum(1 for j in range(index) if centers[j] > ghost_start)
    return index - passed


def ripple_delete(p: Project, clip_id: str) -> Project:
    """Quita el clip y cierra el hueco. Los assets se conservan (siguen en el proyecto)."""
    clips = list(p.video.clips)
    transitions = list(p.video.transitions)
    _remove_at(clips, transitions, p.video.index_of(clip_id))
    return _with_track(p, clips, transitions)


def set_speed(p: Project, clip_id: str, speed: float) -> Project:
    speed = max(SPEED_MIN, min(SPEED_MAX, float(speed)))
    index = p.video.index_of(clip_id)
    clip = p.video.clips[index]
    if speed == clip.speed:
        return p
    clips = list(p.video.clips)
    out = max(clip.out_, clip.in_ + min_source_length(p, clip, speed))
    clips[index] = replace(clip, speed=speed, out_=min(out, p.asset(clip.asset_id).duration))
    return _with_track(p, clips, list(p.video.transitions))


def add_asset(p: Project, asset: MediaAsset) -> tuple[Project, MediaAsset]:
    """Añade el asset si su ruta no estaba ya; devuelve el asset efectivo del proyecto."""
    existing = p.asset_for_path(asset.path)
    if existing is not None:
        return p, existing
    return replace(p, assets=(*p.assets, asset)), asset


def append_clip(p: Project, asset: MediaAsset, index: int | None = None) -> tuple[Project, Clip]:
    """Añade el asset (si falta) y un clip con el asset entero al final (o en `index`)."""
    p, asset = add_asset(p, asset)
    clip = Clip(id=new_id("c"), asset_id=asset.id, in_=0.0, out_=asset.duration)
    clips = list(p.video.clips)
    transitions = list(p.video.transitions)
    _insert_at(clips, transitions, len(clips) if index is None else index, clip)
    return _with_track(p, clips, transitions), clip


# -- snap --------------------------------------------------------------------------


def snap(t: float, candidates: list[float] | tuple[float, ...], zoom: float, threshold_px: float = SNAP_PX) -> float:
    """El candidato más cercano a `t` si está a menos de `threshold_px` píxeles (umbral en px:
    el imán se siente igual a cualquier zoom); si no, `t`."""
    if not candidates or zoom <= 0:
        return t
    best = min(candidates, key=lambda c: abs(c - t))
    return best if abs(best - t) * zoom <= threshold_px else t


def snap_candidates(
    p: Project, *, playhead: float | None = None, exclude: str | None = None, zoom: float | None = None
) -> list[float]:
    """Bordes de clips (menos los de `exclude`), playhead, 0 y segundos enteros a zoom bajo."""
    candidates = {0.0}
    starts = clip_starts(p.video)
    for clip, start in zip(p.video.clips, starts):
        if clip.id == exclude:
            continue
        candidates.add(start)
        candidates.add(start + clip.duration)
    if playhead is not None:
        candidates.add(playhead)
    if zoom is not None and zoom < 40:
        candidates.update(float(s) for s in range(int(total_duration(p)) + 1))
    return sorted(candidates)
