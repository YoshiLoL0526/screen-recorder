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
    AUDIO_VIDEO_TRACK_NAME,
    SPEED_MAX,
    SPEED_MIN,
    TRANSITION_MIN,
    Clip,
    MediaAsset,
    Project,
    TextOverlay,
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
    """Inicio global de cada clip.

    `sequence`: fórmula actual (aplicando los solapes de las transiciones).
    `absolute`: el `start` explícito de cada clip (`None` se trata como 0), con huecos.
    """
    if track.mode == "absolute":
        return [max(0.0, float(clip.start or 0.0)) for clip in track.clips]
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
    if track.mode == "absolute":
        return max(start + clip.duration for start, clip in zip(clip_starts(track), track.clips))
    starts = clip_starts(track)
    return starts[-1] + track.clips[-1].duration


def project_extent(p: Project) -> float:
    """Duración que hay que dibujar: el video manda, pero el audio absoluto puede sobresalir."""
    return max([total_duration(p), *(track_duration(t) for t in p.audio)])


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


def _ensure_unlocked(track: Track) -> None:
    if track.locked:
        label = track.name or ("video" if track.kind == "video" else track.kind)
        raise ProjectError(f"la pista «{label}» está bloqueada")


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


def _audio_index(p: Project, track) -> int | None:  # noqa: ANN001
    """`None` para el video o el índice de `p.audio` para una pista de audio."""
    if track is None:
        return None
    if isinstance(track, int):
        if not 0 <= track < len(p.audio):
            raise ProjectError(f"no existe la pista de audio {track}")
        return track
    if track is p.video or track == p.video:
        return None
    for index, candidate in enumerate(p.audio):
        if track is candidate or track == candidate:
            return index
    raise ProjectError("la pista no pertenece al proyecto")


def _track_of(p: Project, index: int | None) -> Track:
    return p.video if index is None else p.audio[index]


def _with_track_index(p: Project, index: int | None, track: Track) -> Project:
    if index is None:
        return replace(p, video=fit_transitions(track))
    tracks = list(p.audio)
    tracks[index] = fit_transitions(track)
    return replace(p, audio=tuple(tracks))


def _spans(track: Track, index: int, t: float) -> bool:
    start = clip_starts(track)[index]
    return start <= t < start + track.clips[index].duration


def _split_track_impl(
    p: Project, index: int | None, t: float, *, relink: str | None = None, keep_link: bool = False,
) -> tuple[Project, Clip, Clip]:
    """Parte el clip de la pista `index` (`None` = video) bajo `t`. Devuelve `(proyecto, izq, der)`."""
    track = _track_of(p, index)
    _ensure_unlocked(track)
    t = frame_round(t, p.fps)
    starts = clip_starts(track)
    hit = next(
        ((i, clip, start) for i, (clip, start) in enumerate(zip(track.clips, starts))
         if start <= t < start + clip.duration),
        None,
    )
    if hit is None:
        raise ProjectError("no hay ningún clip que dividir en ese punto")
    clip_index, clip, start = hit
    local = t - start
    frame = 1.0 / p.fps
    if local < frame - 1e-9 or clip.duration - local < frame - 1e-9:
        raise ProjectError("el playhead está en el borde del clip: no hay nada que dividir")
    grid = _source_grid(p, clip)
    cut = frame_round(clip.in_ + local * clip.speed, grid)
    min_len = min_source_length(p, clip)
    cut = max(clip.in_ + min_len, min(cut, clip.out_ - min_len))
    left_effects = tuple(e for e in clip.effects if e.kind not in ("fade_out", "audio_fade_out"))
    right_effects = tuple(e for e in clip.effects if e.kind not in ("fade_in", "audio_fade_in"))
    left = replace(clip, out_=cut, effects=left_effects)
    right = replace(
        clip,
        id=new_id("a" if index is not None else "c"),
        in_=cut,
        effects=right_effects,
        start=round(t, 6) if track.mode == "absolute" else None,
        link_id=relink if relink is not None else (clip.link_id if keep_link else None),
    )
    clips = list(track.clips)
    transitions = list(track.transitions)
    clips[clip_index : clip_index + 1] = [left, right]
    transitions.insert(clip_index, None)
    project = _with_track_index(p, index, replace(track, clips=tuple(clips), transitions=tuple(transitions)))
    return project, left, right


def split(p: Project, t: float) -> Project:
    """Corta el clip de video bajo `t` (redondeado a frame). El izquierdo conserva la transición
    entrante; el derecho, la saliente; la frontera nueva nace sin transición.

    No toca el audio separado: para eso están `split_all` (video + audio enlazado) y
    `split_audio` (solo audio), que es lo que usa la UI.
    """
    project, _left, _right = _split_track_impl(p, None, t)
    return project


def split_track(p: Project, track, t: float) -> Project:  # noqa: ANN001
    """Corta una pista concreta: `None`/`p.video` para el video, un índice o `Track` de audio."""
    project, _left, _right = _split_track_impl(p, _audio_index(p, track), t)
    return project


def split_all(p: Project, t: float) -> Project:
    """Un solo undo: corta el video en `t` y el audio enlazado al clip que cae bajo el playhead.

    El trozo derecho del audio queda enlazado al trozo derecho del video (`link_id`), así
    `attach_audio` puede deshacer el corte por parejas.
    """
    t = frame_round(t, p.fps)
    pos = locate(p, t)
    if pos is None:
        raise ProjectError("no hay clips que dividir")
    project, left, right = _split_track_impl(p, None, t)
    for track_index in range(len(project.audio)):
        track = project.audio[track_index]
        starts = clip_starts(track)
        if any(
            clip.link_id == left.id and start <= t < start + clip.duration
            for clip, start in zip(track.clips, starts)
        ):
            project, _audio_left, _audio_right = _split_track_impl(
                project, track_index, t, relink=right.id
            )
    return project


def split_audio(p: Project, t: float) -> Project:
    """Corta solo las pistas de audio que cruzan `t` (la música secuencial incluida).

    Los trozos de un clip enlazado conservan el mismo `link_id`: un audio dividido puede tener
    varios segmentos enlazados al mismo video (docs/19). Así «Vincular audio» los borra todos.
    """
    t = frame_round(t, p.fps)
    project = p
    cuts = 0
    for track_index in range(len(p.audio)):
        original_clips = p.audio[track_index].clips
        for original in original_clips:
            track = project.audio[track_index]
            try:
                clip_index = track.index_of(original.id)
            except ProjectError:
                continue
            if _spans(track, clip_index, t):
                project, _left, _right = _split_track_impl(project, track_index, t, keep_link=True)
                cuts += 1
    if not cuts:
        raise ProjectError("no hay audio que dividir en ese punto")
    return project


def _trim_clip(p: Project, track: Track, clip: Clip, new_in: float, new_out: float) -> Clip | None:
    """Nuevos `in_`/`out_` (fuente) acotados al asset, a frame y a ≥ 1 frame de duración."""
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
        return None
    return replace(clip, in_=new_in, out_=new_out)


def trim(p: Project, clip_id: str, new_in: float, new_out: float) -> Project:
    """Trim del clip de video `clip_id` (ver `_trim_clip`)."""
    _ensure_unlocked(p.video)
    index = p.video.index_of(clip_id)
    clip = p.video.clips[index]
    trimmed = _trim_clip(p, p.video, clip, new_in, new_out)
    if trimmed is None:
        return p
    clips = list(p.video.clips)
    clips[index] = trimmed
    return _with_track(p, clips, list(p.video.transitions))


def trim_audio(p: Project, track_index: int, clip_id: str, new_in: float, new_out: float) -> Project:
    """Trim de un clip de audio.

    En pistas absolutas, recortar solo por la izquierda desplaza `start` y mantiene fijo el borde
    derecho; recortar por la derecha (o ambos bordes) no mueve `start`.
    """
    track = p.audio[track_index]
    _ensure_unlocked(track)
    index = track.index_of(clip_id)
    clip = track.clips[index]
    trimmed = _trim_clip(p, track, clip, new_in, new_out)
    if trimmed is None:
        return p
    moved_in = abs(new_in - clip.in_) > 1e-9
    moved_out = abs(new_out - clip.out_) > 1e-9
    if track.mode == "absolute" and moved_in and not moved_out and trimmed.in_ != clip.in_:
        # El asa izquierda de una pista absoluta mueve el inicio en el timeline y deja fijo el
        # borde derecho (a diferencia de una pista secuencial, donde manda el orden).
        delta = (trimmed.in_ - clip.in_) / clip.speed
        new_start = frame_round(float(clip.start or 0.0) + delta, p.fps)
        if new_start < -1e-9:
            raise ProjectError("el recorte se sale del timeline (inicio negativo)")
        if _absolute_overlap(track, new_start, trimmed.duration, exclude=clip.id):
            raise ProjectError("el recorte se solaparía con otro clip de la misma pista")
        trimmed = replace(trimmed, start=round(max(0.0, new_start), 6))
    clips = list(track.clips)
    clips[index] = trimmed
    return _with_track_index(p, track_index, replace(track, clips=tuple(clips)))


def move(p: Project, clip_id: str, to_index: int) -> Project:
    """Reordena: el clip pasa a ocupar `to_index` (índice final). Sus transiciones se pierden."""
    _ensure_unlocked(p.video)
    clips = list(p.video.clips)
    transitions = list(p.video.transitions)
    index = p.video.index_of(clip_id)
    to_index = max(0, min(to_index, len(clips) - 1))
    if to_index == index:
        return p
    clip = _remove_at(clips, transitions, index)
    _insert_at(clips, transitions, to_index, clip)
    return _with_track(p, clips, transitions)


def _drop_index(track: Track, clip_id: str, ghost_start: float) -> int:
    """Índice final de un clip arrastrado dentro de una pista secuencial (regla del centro)."""
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


def drop_index(p: Project, clip_id: str, ghost_start: float) -> int:
    """Índice final de un clip de video arrastrado cuyo fantasma empieza en `ghost_start`."""
    return _drop_index(p.video, clip_id, ghost_start)


def audio_drop_index(p: Project, track_index: int, clip_id: str, ghost_start: float) -> int:
    """Como `drop_index`, para una pista de audio secuencial."""
    return _drop_index(p.audio[track_index], clip_id, ghost_start)


def _remove_linked_audio(p: Project, video_clip_id: str) -> Project:
    """Borra el audio enlazado a un clip de video (cascada al borrar el video)."""
    tracks = list(p.audio)
    changed = False
    for track_index, track in enumerate(tracks):
        indexes = [i for i, c in enumerate(track.clips) if c.link_id == video_clip_id]
        if not indexes:
            continue
        _ensure_unlocked(track)
        clips = list(track.clips)
        transitions = list(track.transitions)
        for index in reversed(indexes):
            _remove_at(clips, transitions, index)
        tracks[track_index] = fit_transitions(
            replace(track, clips=tuple(clips), transitions=tuple(transitions))
        )
        changed = True
    return replace(p, audio=tuple(tracks)) if changed else p


def ripple_delete(p: Project, clip_id: str) -> Project:
    """Quita el clip y cierra el hueco. Los assets se conservan (siguen en el proyecto).

    Si el clip tenía audio separado enlazado, se borra también (en la misma operación).
    """
    _ensure_unlocked(p.video)
    clips = list(p.video.clips)
    transitions = list(p.video.transitions)
    _remove_at(clips, transitions, p.video.index_of(clip_id))
    project = _with_track(p, clips, transitions)
    return _remove_linked_audio(project, clip_id)


def ripple_delete_many(p: Project, clip_ids) -> Project:  # noqa: ANN001
    """Quita varios clips con **un solo** resultado (una entrada de undo, B3).

    Se borran de derecha a izquierda para que los índices no se muevan; los ids que no existen
    se ignoran. Una selección vacía devuelve el mismo proyecto.
    """
    ids = set(clip_ids)
    if not ids:
        return p
    order = [i for i, clip in enumerate(p.video.clips) if clip.id in ids]
    project = p
    for index in reversed(order):
        project = ripple_delete(project, project.video.clips[index].id)
    return project


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


# -- audio editable (fase 1, docs/19) ------------------------------------------------


def _absolute_overlap(track: Track, start: float, duration: float, *, exclude: str | None = None) -> bool:
    end = start + duration
    for clip in track.clips:
        if clip.id == exclude:
            continue
        other = max(0.0, float(clip.start or 0.0))
        if start < other + clip.duration - 1e-9 and other < end - 1e-9:
            return True
    return False


def _ordered_absolute_clips(clips: list[Clip]) -> tuple[Clip, ...]:
    ordered = tuple(sorted(clips, key=lambda c: (float(c.start or 0.0), c.id)))
    return ordered


def move_audio(
    p: Project, track_index: int, clip_id: str, *, start: float | None = None,
    to_index: int | None = None,
) -> Project:
    """Mueve un clip de audio.

    - Pista `absolute`: `start` es la nueva posición (redondeada a frame); no se permite solapar.
    - Pista `sequence`: se reordena a `to_index` (la música sigue pegada, sin huecos).
    """
    track = p.audio[track_index]
    _ensure_unlocked(track)
    index = track.index_of(clip_id)
    clip = track.clips[index]
    if track.mode == "absolute":
        if start is None:
            raise ProjectError("una pista absoluta necesita `start` para mover un clip")
        new_start = frame_round(max(0.0, float(start)), p.fps)
        if _absolute_overlap(track, new_start, clip.duration, exclude=clip.id):
            raise ProjectError("el clip se solaparía con otro de la misma pista")
        if abs(new_start - float(clip.start or 0.0)) < 1e-9:
            return p
        clips = list(track.clips)
        clips[index] = replace(clip, start=round(new_start, 6))
        return _with_track_index(p, track_index, replace(track, clips=tuple(clips)))
    if to_index is None:
        return p
    clips = list(track.clips)
    transitions = list(track.transitions)
    to_index = max(0, min(int(to_index), len(clips) - 1))
    if to_index == index:
        return p
    moved = _remove_at(clips, transitions, index)
    _insert_at(clips, transitions, to_index, moved)
    return _with_track_index(p, track_index, replace(track, clips=tuple(clips), transitions=tuple(transitions)))


def remove_audio(p: Project, track_index: int, clip_id: str) -> Project:
    """Borra un clip de audio. En pistas absolutas deja hueco; en secuenciales cierra el hueco."""
    track = p.audio[track_index]
    _ensure_unlocked(track)
    clips = list(track.clips)
    transitions = list(track.transitions)
    _remove_at(clips, transitions, track.index_of(clip_id))
    return _with_track_index(p, track_index, replace(track, clips=tuple(clips), transitions=tuple(transitions)))


def add_audio_track(
    p: Project, name: str = "", *, mode: str = "absolute",
) -> tuple[Project, int]:
    """Añade una pista de audio vacía y devuelve `(proyecto, índice)`."""
    if mode not in ("sequence", "absolute"):
        raise ProjectError(f"modo de pista desconocido: {mode}")
    label = name.strip() or f"Audio {len(p.audio) + 1}"
    track = Track(kind="audio", mode=mode)  # type: ignore[arg-type]
    track = replace(track, name=label)
    return replace(p, audio=(*p.audio, track)), len(p.audio)


def remove_audio_track(p: Project, track_index: int) -> Project:
    """Quita la pista y reactiva el audio del video de los clips que estaban enlazados en ella."""
    if not 0 <= track_index < len(p.audio):
        raise ProjectError(f"no existe la pista de audio {track_index}")
    removed = p.audio[track_index]
    _ensure_unlocked(removed)
    reenable = {clip.link_id for clip in removed.clips if clip.link_id}
    video = p.video
    if reenable:
        clips = tuple(
            replace(clip, audio_enabled=True) if clip.id in reenable else clip for clip in p.video.clips
        )
        video = replace(p.video, clips=clips)
    return replace(p, video=video, audio=tuple(t for i, t in enumerate(p.audio) if i != track_index))


def set_audio_track(p: Project, track_index: int, **changes) -> Project:  # noqa: ANN003
    """Cambia nombre/mute/solo/lock/ganancia/ducking de una pista de audio (validado)."""
    allowed = {"name", "muted", "solo", "locked", "gain", "ducking"}
    unknown = set(changes) - allowed
    if unknown:
        raise ProjectError(f"campos de pista desconocidos: {', '.join(sorted(unknown))}")
    track = p.audio[track_index]
    updated = replace(track, **changes)
    tracks = list(p.audio)
    tracks[track_index] = updated
    return replace(p, audio=tuple(tracks))


def find_audio_clip(p: Project, clip_id: str) -> tuple[int, int, Clip] | None:
    for track_index, track in enumerate(p.audio):
        for clip_index, clip in enumerate(track.clips):
            if clip.id == clip_id:
                return track_index, clip_index, clip
    return None


def linked_audio_clip(p: Project, video_clip_id: str) -> tuple[int, int, Clip] | None:
    """Primer clip de audio enlazado a un clip de video (puede haber varios si se dividió)."""
    for track_index, track in enumerate(p.audio):
        for clip_index, clip in enumerate(track.clips):
            if clip.link_id == video_clip_id:
                return track_index, clip_index, clip
    return None


def linked_audio_clips(p: Project, video_clip_id: str) -> list[tuple[int, int, Clip]]:
    """Todos los clips de audio enlazados a un video (trozos de un audio dividido)."""
    return [
        (track_index, clip_index, clip)
        for track_index, track in enumerate(p.audio)
        for clip_index, clip in enumerate(track.clips)
        if clip.link_id == video_clip_id
    ]


def detach_audio(p: Project, clip_id: str) -> tuple[Project, str]:
    """Separa el audio de un clip de video en una pista absoluta (un solo undo en la UI).

    Crea o reutiliza la pista «Audio del video», copia el clip con su posición, enlaza ambos
    (`link_id`) y marca el video con `audio_enabled=False`. Devuelve `(proyecto, id_audio)`.
    """
    index = p.video.index_of(clip_id)
    clip = p.video.clips[index]
    if not clip.audio_enabled:
        raise ProjectError("el audio de este clip ya está separado")
    if not p.asset(clip.asset_id).has_audio:
        raise ProjectError("el archivo de este clip no tiene audio")
    start = frame_round(clip_starts(p.video)[index], p.fps)
    overlap = 0.0
    if index > 0:
        previous = p.video.transitions[index - 1] if index - 1 < len(p.video.transitions) else None
        overlap = previous.duration if previous is not None else 0.0
    start = frame_round(start + overlap, p.fps)
    track_index = next(
        (i for i, t in enumerate(p.audio)
         if t.mode == "absolute" and t.name == AUDIO_VIDEO_TRACK_NAME),
        None,
    )
    tracks = list(p.audio)
    if track_index is None:
        track_index = len(tracks)
        tracks.append(Track(kind="audio", mode="absolute", name=AUDIO_VIDEO_TRACK_NAME))
    track = tracks[track_index]
    _ensure_unlocked(track)
    audio_duration = clip.duration - overlap
    if _absolute_overlap(track, start, audio_duration):
        raise ProjectError("la pista «Audio del video» ya tiene un clip en ese tramo")
    audio_effects = tuple(
        e for e in clip.effects
        if e.kind in ("volume", "fade_in", "fade_out", "audio_fade_in", "audio_fade_out")
    )
    audio = Clip(
        id=new_id("a"), asset_id=clip.asset_id, in_=clip.in_ + overlap * clip.speed,
        out_=clip.out_, speed=clip.speed, effects=audio_effects, start=start, link_id=clip.id,
    )
    clips = _ordered_absolute_clips([*track.clips, audio])
    tracks[track_index] = replace(track, clips=clips, transitions=tuple(None for _ in range(len(clips) - 1)))
    video_clips = list(p.video.clips)
    video_clips[index] = replace(clip, audio_enabled=False)
    return replace(p, video=replace(p.video, clips=tuple(video_clips)), audio=tuple(tracks)), audio.id


def attach_audio(p: Project, video_clip_id: str) -> Project:
    """Deshace `detach_audio`: borra el/los clip(s) enlazado(s) y reactiva el audio del video."""
    index = p.video.index_of(video_clip_id)
    clip = p.video.clips[index]
    tracks = list(p.audio)
    removed = False
    for track_index, track in enumerate(tracks):
        keep = [c for c in track.clips if c.link_id != video_clip_id]
        if len(keep) == len(track.clips):
            continue
        _ensure_unlocked(track)
        removed = True
        # Un corte limpio entre los clips que quedan (el enlace separado no tenía transiciones).
        transitions = tuple(None for _ in range(max(0, len(keep) - 1)))
        tracks[track_index] = replace(track, clips=tuple(keep), transitions=transitions)
    if not removed and clip.audio_enabled:
        raise ProjectError("este clip no tiene audio separado")
    video_clips = list(p.video.clips)
    video_clips[index] = replace(clip, audio_enabled=True)
    return replace(p, video=replace(p.video, clips=tuple(video_clips)), audio=tuple(tracks))



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


# -- textos (B2) -------------------------------------------------------------------


def add_text(
    p: Project, text: str, start: float, duration: float = 3.0, *,
    x: float = 0.5, y: float = 0.82, size: int = 42, color: str = "#FFFFFF",
    bold: bool = False,
) -> tuple[Project, TextOverlay]:
    """Añade un texto en `start` (acotado al timeline) y devuelve `(proyecto, overlay)`."""
    total = total_duration(p)
    if total <= 0:
        raise ProjectError("el timeline está vacío: no hay dónde poner el texto")
    min_len = max(0.2, 1.0 / p.fps)
    duration = max(min_len, float(duration))
    start = max(0.0, min(float(start), max(0.0, total - min_len)))
    end = min(total, start + duration)
    if end - start < min_len - 1e-9:
        raise ProjectError("el texto no cabe en el timeline")
    overlay = TextOverlay(
        id=new_id("t"), text=str(text), start=round(start, 4), end=round(end, 4),
        x=x, y=y, size=int(size), color=color, bold=bold,
    )
    return replace(p, texts=(*p.texts, overlay)), overlay


def update_text(p: Project, text_id: str, **changes) -> Project:  # noqa: ANN003
    """Cambia campos de un texto (valida el resultado)."""
    current = p.text(text_id)
    allowed = {"text", "start", "end", "x", "y", "size", "color", "bold"}
    unknown = set(changes) - allowed
    if unknown:
        raise ProjectError(f"campos de texto desconocidos: {', '.join(sorted(unknown))}")
    overlay = replace(current, **{k: (int(v) if k == "size" else v) for k, v in changes.items()})
    total = total_duration(p)
    if overlay.end > total + 1e-6 or overlay.start < 0:
        raise ProjectError("el texto se sale del timeline")
    return replace(p, texts=tuple(overlay if t.id == text_id else t for t in p.texts))


def remove_text(p: Project, text_id: str) -> Project:
    p.text(text_id)  # valida que existe
    return replace(p, texts=tuple(t for t in p.texts if t.id != text_id))


# -- snap --------------------------------------------------------------------------


def audio_snap_candidates(
    p: Project, exclude: str | None, source_times: dict[str, list[float]], *, limit: int = 400
) -> list[float]:
    """Instantes del timeline donde suena un pico de audio de otro clip (B4).

    `source_times` son tiempos **de la fuente** por asset (los saca `services.media`). Se mapean
    con el `in_` y la velocidad de cada clip; se incluyen los picos de las pistas de música.
    """
    times: list[float] = []
    for track in (p.video, *p.audio):
        starts = clip_starts(track)
        for clip, start in zip(track.clips, starts):
            if clip.id == exclude or clip.asset_id not in source_times:
                continue
            for source_t in source_times[clip.asset_id]:
                local = (float(source_t) - clip.in_) / clip.speed
                if 0.0 <= local <= clip.duration:
                    times.append(start + local)
    return sorted(set(round(t, 4) for t in times))[:limit]


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
    """Bordes de clips (video y audio, menos los de `exclude`), playhead, 0 y segundos enteros."""
    candidates = {0.0}
    for track in (p.video, *p.audio):
        starts = clip_starts(track)
        for clip, start in zip(track.clips, starts):
            if clip.id == exclude:
                continue
            candidates.add(start)
            candidates.add(start + clip.duration)
    if playhead is not None:
        candidates.add(playhead)
    if zoom is not None and zoom < 40:
        candidates.update(float(s) for s in range(int(project_extent(p)) + 1))
    return sorted(candidates)
