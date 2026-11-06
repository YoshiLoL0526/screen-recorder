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

from . import animation as anim
from . import audio_envelope as env
from .errors import ProjectError
from .project import (
    AUDIO_VIDEO_TRACK_NAME,
    FIT_MODES,
    SPEED_MAX,
    SPEED_MIN,
    TRANSITION_MIN,
    VIDEO_TRACKS_MAX,
    Clip,
    Element,
    Marker,
    MediaAsset,
    Project,
    TextOverlay,
    Track,
    Transform,
    Transition,
    new_id,
    transition_limit,
)
from .transform import hex_to_rgb

ZOOM_MIN, ZOOM_MAX = 5.0, 600.0  # px por segundo
SNAP_PX = 8.0
PROJECT_SIZE_MIN, PROJECT_SIZE_MAX = 16, 7680


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


# -- lienzo y transformación (fase 2, docs/20) ---------------------------------------


def set_project_size(p: Project, width: int, height: int) -> Project:
    """Cambia el lienzo sin mover clips ni transiciones.

    Exige un par entero dentro de `[16, 7680]`; las dimensiones impares se redondean hacia
    abajo a par (yuv420p). El render vuelve a normalizar los clips con el nuevo lienzo.
    """
    try:
        w, h = float(width), float(height)
    except (TypeError, ValueError) as exc:
        raise ProjectError(f"tamaño de lienzo inválido ({width!r}×{height!r})") from exc
    if not (w.is_integer() and h.is_integer()):
        raise ProjectError("el ancho y el alto del lienzo deben ser enteros")
    w, h = int(w), int(h)
    if not (PROJECT_SIZE_MIN <= w <= PROJECT_SIZE_MAX and PROJECT_SIZE_MIN <= h <= PROJECT_SIZE_MAX):
        raise ProjectError(
            f"el lienzo debe estar entre {PROJECT_SIZE_MIN} y {PROJECT_SIZE_MAX} px ({w}×{h})"
        )
    w -= w % 2
    h -= h % 2
    if w < PROJECT_SIZE_MIN or h < PROJECT_SIZE_MIN:
        raise ProjectError("el lienzo es demasiado pequeño")
    if (w, h) == (p.width, p.height):
        return p
    return replace(p, width=w, height=h)


def set_project_fit(p: Project, fit: str) -> Project:
    """Encaje por defecto de los clips (`fit`/`fill`/`stretch`)."""
    if fit not in FIT_MODES:
        raise ProjectError(f"encaje desconocido: {fit!r}")
    return p if p.default_fit == fit else replace(p, default_fit=fit)


def set_project_background(p: Project, color: str) -> Project:
    """Color del lienzo (`#RRGGBB`); acepta también `RRGGBB` sin almohadilla."""
    text = str(color).strip().upper()
    if len(text) == 6 and not text.startswith("#"):
        text = "#" + text
    hex_to_rgb(text)
    return p if p.background == text else replace(p, background=text)


def set_transform(p: Project, clip_id: str, **changes) -> Project:  # noqa: ANN003
    """Fusiona `changes` en la transformación del clip de video `clip_id`.

    Un `Transform` que queda neutro se guarda como `None` (comportamiento histórico), para no
    bloquear el corte rápido sin motivo. Un solo `store.apply` en la UI ⇒ un solo undo.
    """
    allowed = {"fit", "scale", "x", "y", "rotation", "flip_h", "flip_v", "crop"}
    unknown = set(changes) - allowed
    if unknown:
        raise ProjectError(f"campos de transformación desconocidos: {', '.join(sorted(unknown))}")
    index = p.video.index_of(clip_id)
    clip = p.video.clips[index]
    current = clip.transform or Transform()
    values = dict(changes)
    try:
        if "crop" in values:
            values["crop"] = tuple(float(v) for v in values["crop"])  # type: ignore[union-attr]
        if "rotation" in values:
            values["rotation"] = int(round(float(values["rotation"])))
        if "fit" in values:
            values["fit"] = str(values["fit"])
        for key in ("scale", "x", "y"):
            if key in values:
                values[key] = float(values[key])
        for key in ("flip_h", "flip_v"):
            if key in values:
                values[key] = bool(values[key])
        updated = replace(current, **values)  # valida en `Transform.__post_init__`
    except ProjectError:
        raise
    except (TypeError, ValueError) as exc:
        raise ProjectError(f"transformación inválida: {exc}") from exc
    transform = None if updated == Transform() else updated
    if transform == clip.transform:
        return p
    clips = list(p.video.clips)
    clips[index] = replace(clip, transform=transform)
    return replace(p, video=replace(p.video, clips=tuple(clips)))


def reset_transform(p: Project, clip_id: str) -> Project:
    """Quita la transformación propia del clip (vuelve al encaje por defecto del proyecto)."""
    index = p.video.index_of(clip_id)
    clip = p.video.clips[index]
    if clip.transform is None:
        return p
    clips = list(p.video.clips)
    clips[index] = replace(clip, transform=None)
    return replace(p, video=replace(p.video, clips=tuple(clips)))


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
    left_duration = (cut - clip.in_) / clip.speed
    left = replace(
        clip, out_=cut, effects=left_effects,
        envelope=env.slice_envelope(clip.envelope, 0.0, left_duration),
    )
    right = replace(
        clip,
        id=new_id("a" if index is not None else "c"),
        in_=cut,
        effects=right_effects,
        start=round(t, 6) if track.mode == "absolute" else None,
        link_id=relink if relink is not None else (clip.link_id if keep_link else None),
        envelope=env.slice_envelope(clip.envelope, left_duration),
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
    trimmed = replace(trimmed, envelope=env.slice_envelope(clip.envelope, 0.0, trimmed.duration))
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
    if (
        track.mode == "absolute" and moved_in and not moved_out
        and trimmed.in_ != clip.in_
    ):
        # El asa izquierda de una pista absoluta mueve el inicio en el timeline y deja fijo el
        # borde derecho (a diferencia de una pista secuencial, donde manda el orden).
        delta = (trimmed.in_ - clip.in_) / clip.speed
        new_start = frame_round(float(clip.start or 0.0) + delta, p.fps)
        if new_start < -1e-9:
            raise ProjectError("el recorte se sale del timeline (inicio negativo)")
        if _absolute_overlap(track, new_start, trimmed.duration, exclude=clip.id):
            raise ProjectError("el recorte se solaparía con otro clip de la misma pista")
        trimmed = replace(
            trimmed, start=round(max(0.0, new_start), 6),
            envelope=env.slice_envelope(clip.envelope, delta, clip.duration),
        )
    else:
        trimmed = replace(
            trimmed, envelope=env.slice_envelope(clip.envelope, 0.0, trimmed.duration),
        )
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
        envelope=env.slice_envelope(clip.envelope, overlap),
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


def separate_audio_all(p: Project) -> Project:
    """Separa el audio de **todos** los clips de video que todavía lo tengan activo.

    Reutiliza una única pista «Audio del video» (cada `detach_audio` la crea o la reutiliza) y
    devuelve el proyecto; la UI lo aplica con un solo `store.apply` (un undo por importación).
    Los clips mudos o ya separados no cambian y no se crea pista si no hay nada que separar.
    Es todo o nada: si un tramo no cabe en la pista absoluta (o está bloqueada), lanza
    `ProjectError` y el proyecto de entrada queda intacto.
    """
    project = p
    for clip_id in [clip.id for clip in p.video.clips]:
        clip = project.clip(clip_id)
        if not clip.audio_enabled:
            continue
        if not project.asset(clip.asset_id).has_audio:
            continue
        project, _audio_id = detach_audio(project, clip_id)
    return project


def unlink_audio(p: Project, video_clip_id: str) -> Project:
    """Corta el enlace de los clips de audio que apuntan a `video_clip_id`.

    El audio **se conserva** en su pista (y el video sigue con `audio_enabled=False`: no se
    reactiva su sonido original). Queda libre para moverlo o recortarlo por separado;
    `relink_audio` recupera el enlace sin borrarlo y `attach_audio` («Vincular audio») sigue
    siendo la vía para descartar el audio separado y recuperar el sonido original del video.
    """
    p.video.index_of(video_clip_id)  # valida que el video existe
    tracks = list(p.audio)
    changed = False
    for track_index, track in enumerate(tracks):
        indexes = [i for i, clip in enumerate(track.clips) if clip.link_id == video_clip_id]
        if not indexes:
            continue
        _ensure_unlocked(track)
        clips = list(track.clips)
        for index in indexes:
            clips[index] = replace(clips[index], link_id=None)
        tracks[track_index] = replace(track, clips=tuple(clips))
        changed = True
    return replace(p, audio=tuple(tracks)) if changed else p


def relink_audio(p: Project, audio_clip_id: str, video_clip_id: str | None = None) -> Project:
    """Vuelve a enlazar un audio libre con un clip de video (inverso de `unlink_audio`).

    El clip de audio **se conserva** tal cual (posición, recortes y efectos); solo recupera su
    `link_id` y el video vuelve a quedar con `audio_enabled=False`. Sin `video_clip_id` se elige
    el clip del mismo asset que más solapa con el audio (y, a igualdad, el más cercano). Es lo
    que permite desvincular, editar el audio y volver a vincularlo sin duplicar clips.
    """
    found = find_audio_clip(p, audio_clip_id)
    if found is None:
        raise ProjectError(f"no existe el clip de audio {audio_clip_id}")
    track_index, clip_index, clip = found
    track = p.audio[track_index]
    _ensure_unlocked(track)
    if track.mode != "absolute":
        raise ProjectError("solo se puede volver a vincular audio de una pista absoluta")
    if clip.link_id is not None:
        return p  # ya está enlazado
    if video_clip_id is None:
        starts = clip_starts(p.video)
        audio_start = float(clip.start or 0.0)
        audio_end = audio_start + clip.duration
        candidates = [
            (index, video) for index, video in enumerate(p.video.clips)
            if video.asset_id == clip.asset_id
        ]
        if not candidates:
            raise ProjectError("no hay un clip de video con el mismo archivo para vincular")

        def rank(item: tuple[int, Clip]) -> tuple[int, float, float, int]:
            index, video = item
            start, end = starts[index], starts[index] + video.duration
            overlap = max(0.0, min(end, audio_end) - max(start, audio_start))
            contains = 0 if start - 1e-9 <= audio_start < end - 1e-9 else 1
            return (contains, -overlap, abs((start + end) / 2 - (audio_start + audio_end) / 2), index)

        video = min(candidates, key=rank)[1]
    else:
        video = p.clip(video_clip_id)
    if video.asset_id != clip.asset_id:
        raise ProjectError("el audio y el video no usan el mismo archivo")
    _ensure_unlocked(p.video)
    clips = list(track.clips)
    clips[clip_index] = replace(clip, link_id=video.id)
    tracks = list(p.audio)
    tracks[track_index] = replace(track, clips=tuple(clips))
    video_clips = list(p.video.clips)
    video_clips[p.video.index_of(video.id)] = replace(video, audio_enabled=False)
    return replace(p, video=replace(p.video, clips=tuple(video_clips)), audio=tuple(tracks))


def _absolute_overlaps(clips) -> tuple[str, str] | None:  # noqa: ANN001
    """Primer par de clips solapados de una pista absoluta (las pistas ya se validan al aplicar)."""
    ordered = sorted(clips, key=lambda c: (float(c.start or 0.0), c.id))
    for a, b in zip(ordered, ordered[1:]):
        if float(b.start or 0.0) < float(a.start or 0.0) + a.duration - 1e-9:
            return a.id, b.id
    return None


def sync_linked_audio(p: Project, before: Project) -> Project:
    """Recoloca el audio enlazado tras una edición del video (`before` es la base del gesto).

    - Cada clip de audio con `link_id` sigue el **nuevo inicio del video**: `start` se calcula
      desde el inicio actual del video (más el solape de la transición entrante), no sumando
      deltas al estado intermedio; así llamar dos veces con el mismo `before` es idempotente y
      los drags no acumulan error.
    - Con **un solo segmento** enlazado también se ajustan `in_`, `out_` y `speed` a los del
      video: recortar o cambiar la velocidad mantiene la misma porción de fuente. Con varios
      segmentos (audio dividido con `split_audio`) solo se desplaza `start`, porque no hay una
      correspondencia 1:1 entre trozos (límite documentado en docs/24).
    - Si el resultado se solapa en una pista absoluta, lanza `ProjectError` para que la UI
      cancele el gesto (el proyecto del `before` sigue siendo válido).

    No toca el audio sin enlace ni las pistas secuenciales.
    """
    linked_counts: dict[str, int] = {}
    for track in p.audio:
        for clip in track.clips:
            if clip.link_id:
                linked_counts[clip.link_id] = linked_counts.get(clip.link_id, 0) + 1
    if not linked_counts:
        return p

    before_video = {
        clip.id: start for clip, start in zip(before.video.clips, clip_starts(before.video))
    }
    new_video = {clip.id: start for clip, start in zip(p.video.clips, clip_starts(p.video))}
    before_audio: dict[str, float] = {}
    for track in before.audio:
        for clip, start in zip(track.clips, clip_starts(track)):
            before_audio[clip.id] = start

    tracks = list(p.audio)
    changed = False
    for track_index, track in enumerate(p.audio):
        if track.mode != "absolute":
            continue
        updated: list[Clip] = []
        track_changed = False
        for clip in track.clips:
            if not clip.link_id:
                updated.append(clip)
                continue
            try:
                video = p.clip(clip.link_id)
                video_index = p.video.index_of(video.id)
            except ProjectError as exc:
                raise ProjectError(
                    f"el audio {clip.id} apunta a un video que no existe ({clip.link_id})"
                ) from exc
            new_start = new_video.get(video.id)
            if new_start is None:
                updated.append(clip)
                continue
            if linked_counts.get(video.id, 0) == 1:
                overlap = 0.0
                if video_index > 0 and video_index - 1 < len(p.video.transitions):
                    transition = p.video.transitions[video_index - 1]
                    overlap = transition.duration if transition is not None else 0.0
                target_start = frame_round(new_start + overlap, p.fps)
                target_in = video.in_ + overlap * video.speed
                try:
                    candidate = replace(
                        clip, start=round(max(0.0, target_start), 6),
                        in_=target_in, out_=video.out_, speed=video.speed,
                    )
                except ProjectError as exc:
                    raise ProjectError(
                        f"el audio enlazado al clip {video.id} no cabe con el nuevo recorte: {exc.message}"
                    ) from exc
            else:
                old_video_start = before_video.get(video.id)
                delta = 0.0 if old_video_start is None else new_start - old_video_start
                old_start = before_audio.get(clip.id, float(clip.start or 0.0))
                target_start = max(0.0, old_start + delta)
                candidate = replace(clip, start=round(target_start, 6))
            if candidate != clip:
                track_changed = True
                changed = True
            updated.append(candidate)
        if not track_changed:
            continue
        overlap = _absolute_overlaps(updated)
        if overlap is not None:
            label = track.name or f"Audio {track_index + 1}"
            raise ProjectError(
                f"el audio enlazado se solaparía con otro clip de «{label}» ({overlap[0]} y {overlap[1]})"
            )
        ordered = _ordered_absolute_clips(updated)
        tracks[track_index] = replace(
            track, clips=ordered, transitions=tuple(None for _ in range(max(0, len(ordered) - 1)))
        )
    return replace(p, audio=tuple(tracks)) if changed else p


def add_asset(p: Project, asset: MediaAsset) -> tuple[Project, MediaAsset]:
    """Añade el asset si su ruta no estaba ya; devuelve el asset efectivo del proyecto."""
    existing = p.asset_for_path(asset.path)
    if existing is not None:
        return p, existing
    return replace(p, assets=(*p.assets, asset)), asset


def insert_clip(p: Project, asset: MediaAsset, index: int | None = None) -> tuple[Project, Clip]:
    """Añade el asset (si falta) y un clip con el asset entero en `index` (0…N)."""
    if asset.kind == "image":
        raise ProjectError("una imagen no se añade como clip: usa un elemento superpuesto")
    p, asset = add_asset(p, asset)
    clip = Clip(id=new_id("c"), asset_id=asset.id, in_=0.0, out_=asset.duration)
    clips = list(p.video.clips)
    transitions = list(p.video.transitions)
    position = len(clips) if index is None else max(0, min(int(index), len(clips)))
    _insert_at(clips, transitions, position, clip)
    return _with_track(p, clips, transitions), clip


def append_clip(p: Project, asset: MediaAsset, index: int | None = None) -> tuple[Project, Clip]:
    """Compatibilidad: añade el clip al final (o en `index`). Ver `insert_clip`."""
    return insert_clip(p, asset, index)


def video_insert_index(p: Project, t: float) -> int:
    """Hueco de video más cercano a `t` (misma regla de centro que el pegado y el drag)."""
    return _sequence_index(p.video, frame_round(max(0.0, float(t)), p.fps))


def insert_audio_clip(
    p: Project, track_index: int, asset: MediaAsset, start: float = 0.0,
) -> tuple[Project, Clip]:
    """Añade un asset de audio a una pista existente.

    - En una pista `absolute`, el clip empieza en `start` (redondeado a frame) y no puede
      solaparse con otro.
    - En una pista `sequence`, se inserta en el hueco más cercano a `start` y el resto se
      desplaza, igual que la música.
    """
    if asset.kind == "image":
        raise ProjectError("una imagen no se añade como audio")
    if not asset.has_audio:
        raise ProjectError(f"«{asset.name}» no tiene pista de audio")
    if not 0 <= int(track_index) < len(p.audio):
        raise ProjectError(f"no existe la pista de audio {track_index}")
    p, asset = add_asset(p, asset)
    track = p.audio[int(track_index)]
    _ensure_unlocked(track)
    start = frame_round(max(0.0, float(start)), p.fps)
    if track.mode == "absolute":
        if _absolute_overlap(track, start, asset.duration):
            raise ProjectError("el audio se solaparía con otro clip de la misma pista")
        clip = Clip(
            id=new_id("a"), asset_id=asset.id, in_=0.0, out_=asset.duration,
            start=round(start, 6),
        )
        clips = _ordered_absolute_clips([*track.clips, clip])
        updated = replace(track, clips=clips, transitions=tuple(None for _ in range(len(clips) - 1)))
        return _with_track_index(p, int(track_index), updated), clip
    clip = Clip(id=new_id("a"), asset_id=asset.id, in_=0.0, out_=asset.duration)
    clips = list(track.clips)
    transitions = list(track.transitions)
    _insert_at(clips, transitions, _sequence_index(track, start), clip)
    updated = replace(track, clips=tuple(clips), transitions=tuple(transitions))
    return _with_track_index(p, int(track_index), updated), clip


def remove_asset(p: Project, asset_id: str) -> Project:
    """Quita un asset que ya no usa ningún clip ni elemento (los usados no se tocan)."""
    p.asset(asset_id)  # valida que existe
    used = any(clip.asset_id == asset_id for track in (p.video, *p.audio) for clip in track.clips)
    used = used or any(element.asset_id == asset_id for element in p.elements)
    if used:
        raise ProjectError("el medio todavía se usa en el proyecto")
    return replace(p, assets=tuple(asset for asset in p.assets if asset.id != asset_id))


# -- textos (B2) -------------------------------------------------------------------


def add_text(
    p: Project, text: str, start: float, duration: float = 3.0, *,
    x: float = 0.5, y: float = 0.82, size: int = 42, color: str = "#FFFFFF",
    bold: bool = False, opacity: float = 1.0, subtitle: bool = False,
    animations: tuple = (),
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
        opacity=opacity, subtitle=subtitle, animations=animations,
    )
    return replace(p, texts=(*p.texts, overlay)), overlay


def update_text(p: Project, text_id: str, **changes) -> Project:  # noqa: ANN003
    """Cambia campos de un texto (valida el resultado)."""
    current = p.text(text_id)
    allowed = {
        "text", "start", "end", "x", "y", "size", "color", "bold",
        "opacity", "subtitle", "animations",
    }
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


def text_values(overlay: TextOverlay, t: float) -> tuple[float, float, float]:
    """`(x, y, opacity)` del texto en `t`, con sus keyframes aplicados."""
    local = max(0.0, float(t) - overlay.start)
    x = anim.value_at(overlay.animations, "x", local, overlay.x)
    y = anim.value_at(overlay.animations, "y", local, overlay.y)
    opacity = anim.value_at(overlay.animations, "opacity", local, overlay.opacity)
    return x, y, opacity


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
    p: Project, *, playhead: float | None = None, exclude=None, zoom: float | None = None,  # noqa: ANN001
) -> list[float]:
    """Bordes de clips (video y audio, menos los de `exclude`: un id o varios), playhead,
    marcadores, In/Out, 0 y segundos enteros."""
    excluded = {exclude} if isinstance(exclude, str) else set(exclude or ())
    candidates = {0.0}
    for track in (p.video, *p.audio):
        starts = clip_starts(track)
        for clip, start in zip(track.clips, starts):
            if clip.id in excluded:
                continue
            candidates.add(start)
            candidates.add(start + clip.duration)
    if playhead is not None:
        candidates.add(playhead)
    # Marcadores e In/Out (fase 3): el imán engancha también a ellos.
    candidates.update(m.t for m in p.markers)
    candidates.update(v for v in (p.range_in, p.range_out) if v is not None)
    if zoom is not None and zoom < 40:
        candidates.update(float(s) for s in range(int(project_extent(p)) + 1))
    return sorted(candidates)


# -- flujo de edición (fase 3, docs/21) ----------------------------------------------
#
# Marcadores, In/Out del timeline, razor, Q/W, portapapeles y movimiento en bloque. Todas son
# funciones puras: la UI aplica cada una con un único `store.apply` (un solo undo).


def add_marker(p: Project, t: float, name: str = "") -> tuple[Project, Marker]:
    """Añade un marcador en `t` (redondeado a frame). Uno por fotograma como mucho."""
    t = frame_round(max(0.0, float(t)), p.fps)
    if any(abs(m.t - t) < 0.5 / p.fps for m in p.markers):
        raise ProjectError("ya hay un marcador en ese fotograma")
    label = str(name).strip() or f"Marcador {len(p.markers) + 1}"
    marker = Marker(id=new_id("k"), t=round(t, 6), name=label)
    markers = tuple(sorted((*p.markers, marker), key=lambda m: (m.t, m.id)))
    return replace(p, markers=markers), marker


def rename_marker(p: Project, marker_id: str, name: str) -> Project:
    current = p.marker(marker_id)
    label = str(name).strip()
    if not label:
        raise ProjectError("el marcador necesita un nombre")
    if label == current.name:
        return p
    return replace(p, markers=tuple(replace(m, name=label) if m.id == marker_id else m for m in p.markers))


def remove_marker(p: Project, marker_id: str) -> Project:
    p.marker(marker_id)  # valida que existe
    return replace(p, markers=tuple(m for m in p.markers if m.id != marker_id))


def marker_at(p: Project, t: float, tolerance: float) -> Marker | None:
    """Marcador más cercano a `t` si está a menos de `tolerance` segundos."""
    if not p.markers:
        return None
    best = min(p.markers, key=lambda m: abs(m.t - t))
    return best if abs(best.t - t) <= tolerance else None


def adjacent_marker(p: Project, t: float, direction: int) -> Marker | None:
    """Siguiente (`direction > 0`) o anterior marcador respecto a `t` (medio frame de holgura)."""
    eps = 0.5 / p.fps
    if direction > 0:
        return next((m for m in p.markers if m.t > t + eps), None)
    return next((m for m in reversed(p.markers) if m.t < t - eps), None)


def set_range_in(p: Project, t: float) -> Project:
    """Punto de entrada del timeline (I). Si queda después de la salida, la salida se quita."""
    t = round(frame_round(max(0.0, min(float(t), total_duration(p))), p.fps), 6)
    out = p.range_out
    if out is not None and out <= t + 1e-9:
        out = None
    if (p.range_in, p.range_out) == (t, out):
        return p
    return replace(p, range_in=t, range_out=out)


def set_range_out(p: Project, t: float) -> Project:
    """Punto de salida del timeline (O). Si queda antes de la entrada, la entrada se quita."""
    t = round(frame_round(max(0.0, min(float(t), total_duration(p))), p.fps), 6)
    if t <= 0:
        raise ProjectError("el punto de salida debe estar después del inicio del timeline")
    start = p.range_in
    if start is not None and start >= t - 1e-9:
        start = None
    if (p.range_in, p.range_out) == (start, t):
        return p
    return replace(p, range_in=start, range_out=t)


def clear_range(p: Project) -> Project:
    if p.range_in is None and p.range_out is None:
        return p
    return replace(p, range_in=None, range_out=None)


def has_range(p: Project) -> bool:
    return p.range_in is not None or p.range_out is not None


def play_range(p: Project) -> tuple[float, float]:
    """Rango efectivo In/Out acotado al timeline (sin puntos, o si queda vacío: todo)."""
    total = total_duration(p)
    start = min(p.range_in or 0.0, total)
    end = total if p.range_out is None else min(p.range_out, total)
    if end - start < 1.0 / p.fps - 1e-9:
        return 0.0, total
    return start, end


def razor(p: Project, t: float, track=None) -> Project:  # noqa: ANN001
    """Herramienta razor sobre una pista: el video (`None`, con su audio enlazado, como `S`) o
    una pista de audio (índice), cuyos trozos conservan el enlace como en `split_audio`."""
    index = _audio_index(p, track)
    if index is None:
        _ensure_unlocked(p.video)
        return split_all(p, t)
    project, _left, _right = _split_track_impl(p, index, frame_round(t, p.fps), keep_link=True)
    return project


def split_everything(p: Project, t: float) -> Project:
    """Razor sobre la regla: corta el video (con su audio enlazado) y todas las pistas de audio
    desbloqueadas que cruzan `t`. Un solo resultado (un solo undo)."""
    t = frame_round(t, p.fps)
    project = p
    cuts = 0
    if not p.video.locked and locate(p, t) is not None:
        try:
            project = split_all(project, t)
            cuts += 1
        except ProjectError:
            pass
    for track_index in range(len(project.audio)):
        if project.audio[track_index].locked:
            continue
        for clip_id in [c.id for c in project.audio[track_index].clips]:
            track = project.audio[track_index]
            if not _spans(track, track.index_of(clip_id), t):
                continue
            try:
                project, _left, _right = _split_track_impl(project, track_index, t, keep_link=True)
                cuts += 1
            except ProjectError:  # el trozo ya empieza en `t` (lo cortó el audio enlazado)
                pass
    if not cuts:
        raise ProjectError("no hay nada que cortar en ese punto")
    return project


def trim_to(p: Project, t: float, side: str, track=None) -> Project:  # noqa: ANN001
    """Q (`side="start"`) / W (`"end"`): recorta el clip bajo `t` hasta `t`.

    En el video (`track=None`) el timeline es magnético, así que recortar por la izquierda hace
    ripple: lo de después se desplaza. En una pista de audio absoluta el borde contrario queda
    fijo (`trim_audio`).
    """
    if side not in ("start", "end"):
        raise ProjectError(f"lado desconocido: {side!r}")
    t = frame_round(t, p.fps)
    frame = 1.0 / p.fps
    index = _audio_index(p, track)
    target = _track_of(p, index)
    _ensure_unlocked(target)
    hit = next(
        ((clip, start) for clip, start in zip(target.clips, clip_starts(target))
         if start <= t < start + clip.duration),
        None,
    )
    if hit is None:
        raise ProjectError("no hay ningún clip bajo el playhead")
    clip, start = hit
    local = t - start
    if (side == "start" and local < frame - 1e-9) or (side == "end" and clip.duration - local < frame - 1e-9):
        raise ProjectError("el playhead está en el borde del clip: no hay nada que recortar")
    source = clip.in_ + local * clip.speed
    new_in, new_out = (source, clip.out_) if side == "start" else (clip.in_, source)
    if index is None:
        return trim(p, clip.id, new_in, new_out)
    return trim_audio(p, index, clip.id, new_in, new_out)


# -- portapapeles (A7) ---------------------------------------------------------------


@dataclass(frozen=True)
class ClipboardItem:
    kind: str  # video | audio | text | element
    offset: float  # segundos desde el inicio del primer elemento copiado
    clip: Clip | None = None
    track_index: int = -1  # pista de audio de origen
    track_mode: str = "absolute"
    text: TextOverlay | None = None
    element: Element | None = None
    transition: Transition | None = None  # video: con el siguiente clip copiado, si eran contiguos


@dataclass(frozen=True)
class Clipboard:
    items: tuple[ClipboardItem, ...] = ()
    assets: tuple[MediaAsset, ...] = ()
    span: float = 0.0  # del primer inicio al último final (s)

    @property
    def empty(self) -> bool:
        return not self.items


def selection_bounds(p: Project, ids) -> tuple[float, float] | None:  # noqa: ANN001
    """`(inicio, final)` en el timeline de los clips/textos con esos ids (o `None`)."""
    ids = set(ids)
    spans: list[tuple[float, float]] = []
    for track in (p.video, *p.audio):
        for clip, start in zip(track.clips, clip_starts(track)):
            if clip.id in ids:
                spans.append((start, start + clip.duration))
    spans += [(t.start, t.end) for t in p.texts if t.id in ids]
    spans += [(e.start, e.end) for e in p.elements if e.id in ids]
    if not spans:
        return None
    return min(s for s, _ in spans), max(e for _, e in spans)


def copy_items(p: Project, ids) -> Clipboard:  # noqa: ANN001
    """Copia clips de video, de audio y textos (por id). Conserva efectos, transformación,
    velocidad, las transiciones entre clips de video contiguos y los enlaces audio↔video."""
    ids = set(ids)
    entries: list[tuple[float, ClipboardItem]] = []
    starts = clip_starts(p.video)
    video = [(i, c) for i, c in enumerate(p.video.clips) if c.id in ids]
    for position, (i, clip) in enumerate(video):
        following = video[position + 1][0] if position + 1 < len(video) else None
        transition = p.video.transitions[i] if following == i + 1 else None
        entries.append((starts[i], ClipboardItem("video", 0.0, clip=clip, transition=transition)))
    for track_index, track in enumerate(p.audio):
        for clip, start in zip(track.clips, clip_starts(track)):
            if clip.id in ids:
                entries.append((start, ClipboardItem(
                    "audio", 0.0, clip=clip, track_index=track_index, track_mode=track.mode,
                )))
    for overlay in p.texts:
        if overlay.id in ids:
            entries.append((overlay.start, ClipboardItem("text", 0.0, text=overlay)))
    for element in p.elements:
        if element.id in ids:
            entries.append((element.start, ClipboardItem("element", 0.0, element=element)))
    if not entries:
        raise ProjectError("no hay nada seleccionado que copiar")
    bounds = selection_bounds(p, ids)
    assert bounds is not None
    anchor = bounds[0]
    items = tuple(replace(item, offset=round(start - anchor, 6)) for start, item in entries)
    used = {item.clip.asset_id for item in items if item.clip is not None}
    used.update(item.element.asset_id for item in items if item.element and item.element.asset_id)
    assets = tuple(a for a in p.assets if a.id in used)
    return Clipboard(items=items, assets=assets, span=round(bounds[1] - anchor, 6))


def _sequence_index(track: Track, t: float) -> int:
    """Hueco de una pista secuencial más cercano a `t` (cuántos clips tienen el centro antes)."""
    starts = clip_starts(track)
    return sum(1 for s, c in zip(starts, track.clips) if s + c.duration / 2 < t)


def paste(p: Project, clipboard: Clipboard, t: float) -> tuple[Project, tuple[str, ...]]:
    """Pega el portapapeles en `t` (el playhead). Devuelve `(proyecto, ids nuevos)`.

    - Video (secuencial y magnético): se insertan juntos en el hueco más cercano a `t`; lo que
      había después se desplaza. Entre sí conservan sus transiciones; con los vecinos, corte.
    - Audio en pista absoluta: en `t` + su desfase (el audio enlazado a un video pegado se
      alinea con ese video). Si se solapa con otro clip de la pista, la operación falla entera.
      En pista secuencial (música) se inserta en el hueco más cercano.
    - Textos: en `t` + su desfase, acotados al timeline.

    Ids nuevos para todo. Un audio solo queda enlazado si su video se pegó a la vez; un video
    cuyo audio separado no viaja con él recupera su audio propio (`audio_enabled=True`).
    """
    if clipboard.empty:
        raise ProjectError("el portapapeles está vacío")
    project = p
    fps = project.fps
    asset_map: dict[str, str] = {}
    for asset in clipboard.assets:
        if any(a.id == asset.id for a in project.assets):
            asset_map[asset.id] = asset.id
            continue
        by_path = project.asset_for_path(asset.path)
        if by_path is not None:
            asset_map[asset.id] = by_path.id
            continue
        project = replace(project, assets=(*project.assets, asset))
        asset_map[asset.id] = asset.id
    t = frame_round(max(0.0, float(t)), fps)
    videos = [item for item in clipboard.items if item.kind == "video" and item.clip is not None]
    linked_in_clipboard = {
        item.clip.link_id for item in clipboard.items
        if item.kind == "audio" and item.clip is not None and item.clip.link_id
    }
    new_ids: list[str] = []
    link_map: dict[str, str] = {}
    video_start: dict[str, float] = {}  # id del video original → inicio del pegado
    video_offset: dict[str, float] = {}
    anchor = t
    if videos:
        track = project.video
        _ensure_unlocked(track)
        index = _sequence_index(track, t)
        starts = clip_starts(track)
        insert_t = starts[index] if index < len(starts) else track_duration(track)
        anchor = insert_t - videos[0].offset
        clips = list(track.clips)
        transitions = list(track.transitions)
        for k, item in enumerate(videos):
            clip = item.clip
            assert clip is not None
            new = replace(
                clip, id=new_id("c"), asset_id=asset_map.get(clip.asset_id, clip.asset_id),
                link_id=None, start=None,
                audio_enabled=clip.audio_enabled or clip.id not in linked_in_clipboard,
            )
            link_map[clip.id] = new.id
            video_offset[clip.id] = item.offset
            _insert_at(clips, transitions, index + k, new)
            new_ids.append(new.id)
        for k, item in enumerate(videos[:-1]):
            if item.transition is not None:
                transitions[index + k] = item.transition
        project = replace(project, video=fit_transitions(
            replace(track, clips=tuple(clips), transitions=tuple(transitions))
        ))
        pasted_starts = clip_starts(project.video)
        for k, item in enumerate(videos):
            video_start[item.clip.id] = pasted_starts[index + k]  # type: ignore[union-attr]
    track_map: dict[int, int] = {}
    for item in clipboard.items:
        if item.kind != "audio" or item.clip is None:
            continue
        source_index = item.track_index
        if source_index not in track_map:
            if 0 <= source_index < len(project.audio) and project.audio[source_index].mode == item.track_mode:
                track_map[source_index] = source_index
            else:
                project, track_map[source_index] = add_audio_track(project, mode=item.track_mode)
        target_index = track_map[source_index]
        track = project.audio[target_index]
        _ensure_unlocked(track)
        original_link = item.clip.link_id
        link = link_map.get(original_link) if original_link else None
        if link is not None:
            start = video_start[original_link] + (item.offset - video_offset[original_link])
        else:
            start = anchor + item.offset
        start = frame_round(max(0.0, start), fps)
        new = replace(
            item.clip, id=new_id("a"), asset_id=asset_map.get(item.clip.asset_id, item.clip.asset_id),
            link_id=link, audio_enabled=True,
        )
        if track.mode == "absolute":
            new = replace(new, start=round(start, 6))
            if _absolute_overlap(track, start, new.duration):
                label = track.name or f"Audio {target_index + 1}"
                raise ProjectError(f"el audio pegado se solaparía con otro clip de «{label}»")
            ordered = _ordered_absolute_clips([*track.clips, new])
            project = _with_track_index(project, target_index, replace(
                track, clips=ordered, transitions=tuple(None for _ in range(len(ordered) - 1))
            ))
        else:
            new = replace(new, start=None)
            seq_clips, seq_transitions = list(track.clips), list(track.transitions)
            _insert_at(seq_clips, seq_transitions, _sequence_index(track, start), new)
            project = _with_track_index(project, target_index, replace(
                track, clips=tuple(seq_clips), transitions=tuple(seq_transitions)
            ))
        new_ids.append(new.id)
    texts = [item for item in clipboard.items if item.kind == "text" and item.text is not None]
    if texts:
        total = total_duration(project)
        if total <= 0:
            raise ProjectError("el timeline está vacío: no hay dónde pegar el texto")
        min_len = max(0.2, 1.0 / fps)
        for item in texts:
            overlay = item.text
            assert overlay is not None
            start = min(max(0.0, anchor + item.offset), max(0.0, total - min_len))
            end = min(total, start + overlay.seconds)
            pasted = replace(overlay, id=new_id("t"), start=round(start, 4), end=round(end, 4))
            project = replace(project, texts=(*project.texts, pasted))
            new_ids.append(pasted.id)
    elements = [item for item in clipboard.items if item.kind == "element" and item.element is not None]
    if elements:
        total = total_duration(project)
        if total <= 0:
            raise ProjectError("el timeline está vacío: no hay dónde pegar el elemento")
        min_len = max(0.2, 1.0 / fps)
        for item in elements:
            element = item.element
            assert element is not None
            start = min(max(0.0, anchor + item.offset), max(0.0, total - min_len))
            end = min(total, start + element.duration)
            if end - start < min_len - 1e-9:
                continue
            pasted = replace(
                element,
                id=new_id("e"),
                asset_id=asset_map.get(element.asset_id, element.asset_id) if element.asset_id else None,
                start=round(start, 4),
                end=round(end, 4),
            )
            # Pegar respeta las capas: V2 < V3 < Elementos (el resto de elementos conserva su
            # orden relativo). Sin esto, un V2 pegado quedaría por encima de V3.
            key = pasted.video_track if pasted.video_track > 0 else VIDEO_TRACKS_MAX + 1
            elements_now = list(project.elements)
            index = next(
                (i for i, item in enumerate(elements_now)
                 if (item.video_track if item.video_track > 0 else VIDEO_TRACKS_MAX + 1) > key),
                len(elements_now),
            )
            elements_now.insert(index, pasted)
            project = replace(project, elements=tuple(elements_now))
            new_ids.append(pasted.id)
    return project, tuple(new_ids)


def duplicate(p: Project, ids) -> tuple[Project, tuple[str, ...]]:  # noqa: ANN001
    """Ctrl+D: copia y pega justo detrás de la selección (el video, tras su último clip)."""
    clipboard = copy_items(p, ids)
    ids = set(ids)
    starts = clip_starts(p.video)
    video_ends = [s + c.duration for s, c in zip(starts, p.video.clips) if c.id in ids]
    if video_ends:
        target = max(video_ends)
    else:
        bounds = selection_bounds(p, ids)
        assert bounds is not None
        target = bounds[1]
    return paste(p, clipboard, target)


def remove_items(p: Project, ids) -> Project:  # noqa: ANN001
    """Borra clips de video (ripple, con su audio enlazado), clips de audio y textos de una vez."""
    ids = set(ids)
    project = ripple_delete_many(p, [c.id for c in p.video.clips if c.id in ids])
    for track_index in range(len(project.audio)):
        for clip_id in [c.id for c in project.audio[track_index].clips if c.id in ids]:
            project = remove_audio(project, track_index, clip_id)
    if any(t.id in ids for t in project.texts):
        project = replace(project, texts=tuple(t for t in project.texts if t.id not in ids))
    if any(e.id in ids for e in project.elements):
        project = replace(project, elements=tuple(e for e in project.elements if e.id not in ids))
    return project


# -- mover en bloque (A8) ----------------------------------------------------------------


def move_many(p: Project, clip_ids, to_index: int) -> Project:  # noqa: ANN001
    """Mueve varios clips de video juntos a `to_index` (índice del primero entre los que quedan).

    Los clips quedan contiguos y en su orden; conservan las transiciones que había entre clips
    seleccionados que ya eran vecinos. Con los nuevos vecinos, corte limpio.
    """
    _ensure_unlocked(p.video)
    ids = set(clip_ids)
    order = [i for i, c in enumerate(p.video.clips) if c.id in ids]
    if not order:
        return p
    moved = [p.video.clips[i] for i in order]
    inner = [
        p.video.transitions[i] if order[j + 1] == i + 1 else None
        for j, i in enumerate(order[:-1])
    ]
    clips = list(p.video.clips)
    transitions = list(p.video.transitions)
    for i in reversed(order):
        _remove_at(clips, transitions, i)
    to_index = max(0, min(int(to_index), len(clips)))
    result_order = [c.id for c in clips[:to_index]] + [c.id for c in moved] + [c.id for c in clips[to_index:]]
    if result_order == [c.id for c in p.video.clips]:
        return p  # mismo orden: nada que hacer (y no se pierde ninguna transición)
    for k, clip in enumerate(moved):
        _insert_at(clips, transitions, to_index + k, clip)
    for k, transition in enumerate(inner):
        if transition is not None:
            transitions[to_index + k] = transition
    return _with_track(p, clips, transitions)


def block_drop_index(p: Project, clip_ids, ghost_start: float) -> int:  # noqa: ANN001
    """Hueco (entre los clips que no se mueven) donde cae un bloque de video que empieza en
    `ghost_start`: el hueco cuyo inicio queda más cerca del inicio del fantasma."""
    ids = set(clip_ids)
    gaps = [0.0]
    for clip in p.video.clips:
        if clip.id not in ids:
            gaps.append(gaps[-1] + clip.duration)
    return min(range(len(gaps)), key=lambda i: (abs(gaps[i] - ghost_start), i))


def shift_audio_many(p: Project, clip_ids, delta: float) -> Project:  # noqa: ANN001
    """Desplaza `delta` s los clips de audio **absolutos** con esos ids (los secuenciales no se
    mueven en bloque). Falla si alguno se solaparía con un clip no seleccionado."""
    ids = set(clip_ids)
    moved = [
        clip
        for track in p.audio if track.mode == "absolute"
        for clip in track.clips if clip.id in ids
    ]
    if not moved or abs(delta) < 1e-9:
        return p
    delta = max(float(delta), -min(float(c.start or 0.0) for c in moved))  # nunca antes de 0
    project = p
    for track_index, track in enumerate(p.audio):
        if track.mode != "absolute" or not any(c.id in ids for c in track.clips):
            continue
        _ensure_unlocked(track)
        others = [c for c in track.clips if c.id not in ids]
        rest = replace(track, clips=tuple(others), transitions=tuple(None for _ in range(max(0, len(others) - 1))))
        updated: list[Clip] = list(others)
        for clip in track.clips:
            if clip.id not in ids:
                continue
            start = round(frame_round(float(clip.start or 0.0) + delta, p.fps), 6)
            if _absolute_overlap(rest, start, clip.duration):
                raise ProjectError("el bloque se solaparía con otro clip de audio")
            updated.append(replace(clip, start=start))
        ordered = _ordered_absolute_clips(updated)
        project = _with_track_index(project, track_index, replace(
            track, clips=ordered, transitions=tuple(None for _ in range(len(ordered) - 1))
        ))
    return project


def move_block(p: Project, clip_ids, ghost_start: float) -> Project:  # noqa: ANN001
    """Arrastre de la selección múltiple: el bloque (video + audio absoluto) empieza en
    `ghost_start`. El video se reordena (magnético) y el audio se desplaza lo mismo que el video
    (o, sin video, lo que diga el fantasma). Un solo resultado ⇒ un solo undo."""
    ids = set(clip_ids)
    bounds = selection_bounds(p, ids)
    if bounds is None:
        return p
    block_start = bounds[0]
    video_ids = [c.id for c in p.video.clips if c.id in ids]
    project = p
    if video_ids:
        first = video_ids[0]
        old_video_start = clip_starts(p.video)[p.video.index_of(first)]
        ghost_video = ghost_start + (old_video_start - block_start)
        project = move_many(p, video_ids, block_drop_index(p, video_ids, ghost_video))
        delta = clip_starts(project.video)[project.video.index_of(first)] - old_video_start
    else:
        delta = ghost_start - block_start
    return shift_audio_many(project, ids, delta)
