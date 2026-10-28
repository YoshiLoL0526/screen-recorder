"""Builder del comando de export con FFmpeg (puro: modelo → argumentos; ver docs/04 y docs/13).

`build(project, preset, out_path)` genera el comando completo (`GraphSpec.args`) y el
`filter_complex`, 100 % testeable con golden strings. Reglas de oro (docs/04):

1. Todo igual antes de `xfade`/`concat`: resolución, fps, formato de píxel, SAR y timebase.
2. Cada clip arranca en 0 (`setpts=PTS-STARTPTS`) y su duración es un **número entero de
   frames** del proyecto (`tpad` + `trim=end_frame`): así los offsets de `xfade` y la duración
   final son exactos y el audio (recortado a esa misma duración) no deriva clip a clip.
3. `atempo` solo admite 0.5–2 por instancia: 0.25× y 4× encadenan dos.
4. Offsets de `xfade` relativos a la cadena acumulada: `offset = frames_acumulados − T`.
5. Clip sin audio (o pista silenciada) ⇒ silencio de la misma duración; `concat` exige tramos.

Cada clip es **una entrada propia** con `-ss IN -t LEN` (seek preciso en la entrada): FFmpeg
solo decodifica el tramo útil y no se acumulan frames en memoria cuando un mismo archivo aparece
varias veces (o en otro orden) en el timeline.

Audio de las transiciones (convención de F3, `services.playback.audio_segments`): suena el clip
saliente hasta su final y el entrante empieza en `t_local = duración de la transición`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import effects as fx
from .errors import ProjectError
from .project import Clip, Project, Track

AUDIO_RATE = 48_000
_INPUT_SLACK = 0.1  # s de margen al leer cada clip (lo sobrante lo corta `trim`/`atrim`)
_PAD_FRAMES_S = 1.0  # relleno máximo (clonando el último frame) si la fuente se queda corta


@dataclass(frozen=True)
class ExportPreset:
    id: str
    label: str
    description: str
    video: str  # hw | x264 | copy
    quality: int = 21  # cq (hw) o crf (x264)
    x264_preset: str = "medium"
    audio_bitrate: str = "192k"
    max_height: int | None = None


PRESETS: tuple[ExportPreset, ...] = (
    ExportPreset("rapido", "Rápido (GPU)", "Encoder por hardware si lo hay (NVENC/AMF/QSV); si no, x264 veryfast.",
                 "hw", 21, "veryfast", "192k"),
    ExportPreset("calidad", "Calidad", "x264 medium, CRF 18: el archivo definitivo.", "x264", 18, "medium", "256k"),
    ExportPreset("ligero", "Ligero (720p)", "x264 veryfast, CRF 24, a 720p: para compartir rápido.",
                 "x264", 24, "veryfast", "160k", 720),
    ExportPreset("copia", "Sin recodificar", "Solo cortes: copia los paquetes; los inicios se imantan a keyframes.",
                 "copy", 0),
)
PRESET_IDS = tuple(p.id for p in PRESETS)
HW_ENCODERS = ("h264_nvenc", "h264_amf", "h264_qsv")


def preset_by_id(preset_id: str) -> ExportPreset:
    for preset in PRESETS:
        if preset.id == preset_id:
            return preset
    raise ProjectError(f"preset de export desconocido: {preset_id!r}")


@dataclass(frozen=True)
class GraphSpec:
    args: tuple[str, ...]
    filter_complex: str
    duration: float  # duración esperada del archivo (s) = frames / fps
    frames: int
    width: int
    height: int
    fps: int
    has_audio: bool
    encoder: str


# -- utilidades -----------------------------------------------------------------------


def num(value: float) -> str:
    """Número compacto para el graph: `10`, `3.2`, `0.033333` (6 decimales como mucho)."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def even(value: float) -> int:
    v = max(2, int(round(value)))
    return v - v % 2


def output_size(project: Project, preset: ExportPreset) -> tuple[int, int]:
    width, height = project.width, project.height
    if preset.max_height and height > preset.max_height:
        width, height = width * preset.max_height / height, preset.max_height
    return even(width), even(height)


def clip_frames(clip: Clip, fps: int) -> int:
    return max(1, int(round(clip.duration * fps)))


def transition_frames(track: Track, index: int, frames: list[int], fps: int) -> int:
    """Frames del solape `index` (0 si es un corte), acotado para dejar ≥ 1 frame propio."""
    transition = track.transitions[index]
    if transition is None:
        return 0
    wanted = int(round(transition.duration * fps))
    return max(0, min(wanted, frames[index] - 1, frames[index + 1] - 1))


def timeline_frames(project: Project) -> tuple[list[int], list[int], int]:
    """`(frames por clip, frames por transición, total)` del track de video."""
    fps = project.fps
    track = project.video
    frames = [clip_frames(c, fps) for c in track.clips]
    overlaps = [transition_frames(track, i, frames, fps) for i in range(len(track.transitions))]
    return frames, overlaps, sum(frames) - sum(overlaps)


def atempo_chain(speed: float) -> list[str]:
    """`atempo` en cadena para cubrir 0.25–4× (cada instancia admite 0.5–2)."""
    steps: list[str] = []
    remaining = speed
    while remaining > 2.0 + 1e-9:
        steps.append("atempo=2")
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        steps.append("atempo=0.5")
        remaining /= 0.5
    if abs(remaining - 1.0) > 1e-9:
        steps.append(f"atempo={num(remaining)}")
    return steps


def video_codec_args(encoder: str, preset: ExportPreset) -> list[str]:
    q = preset.quality
    if encoder == "h264_nvenc":
        return ["-c:v", encoder, "-preset", "p5", "-rc", "vbr", "-cq", str(q), "-b:v", "0"]
    if encoder == "h264_amf":
        return ["-c:v", encoder, "-quality", "balanced", "-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q + 2)]
    if encoder == "h264_qsv":
        return ["-c:v", encoder, "-preset", "veryfast", "-global_quality", str(q)]
    if encoder == "libx264":
        return ["-c:v", encoder, "-preset", preset.x264_preset, "-crf", str(q)]
    raise ProjectError(f"encoder desconocido: {encoder!r}")


def resolve_encoder(preset: ExportPreset, available: dict[str, bool]) -> str:
    """Encoder efectivo del preset con los encoders que funcionan en este equipo."""
    if preset.video == "hw":
        for name in HW_ENCODERS:
            if available.get(name):
                return name
    return "libx264"


# -- cadenas por clip ----------------------------------------------------------------


def _video_chain(project: Project, clip: Clip, frames: int, size: tuple[int, int]) -> list[str]:
    fps = project.fps
    w, h = size
    steps = [f"setpts=(PTS-STARTPTS)/{num(clip.speed)}" if clip.speed != 1.0 else "setpts=PTS-STARTPTS"]
    factor, cx, cy = fx.zoom(clip)
    if factor > 1.0 + 1e-6:
        steps.append(f"crop=w=iw/{num(factor)}:h=ih/{num(factor)}:x=(iw-ow)*{num(cx)}:y=(ih-oh)*{num(cy)}")
    eq = fx.eq(clip)
    if eq is not None:
        steps.append(f"eq=brightness={num(eq['brightness'])}:contrast={num(eq['contrast'])}"
                     f":saturation={num(eq['saturation'])}")
    steps += [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:-1:-1:color=black",
        f"fps={fps}",
        f"tpad=stop_mode=clone:stop={int(fps * _PAD_FRAMES_S)}",
        f"trim=end_frame={frames}",
    ]
    duration = frames / fps
    fin, fout = fx.fades(clip, duration)
    if fin > 0:
        steps.append(f"fade=t=in:st=0:d={num(fin)}")
    if fout > 0:
        steps.append(f"fade=t=out:st={num(duration - fout)}:d={num(fout)}")
    steps += ["format=yuv420p", "setsar=1"]
    return steps


def _audio_steps(clip: Clip, *, skip: float, duration: float, gain: float,
                 micro_in: bool, micro_out: bool) -> list[str]:
    """Cadena de audio de un tramo de `duration` s que empieza `skip` s (timeline) tras el
    inicio del clip."""
    steps: list[str] = []
    if skip > 0:
        steps.append(f"atrim=start={num(skip * clip.speed)}")
    steps += [
        "asetpts=PTS-STARTPTS",
        f"aresample={AUDIO_RATE}",
        "aformat=sample_fmts=fltp:channel_layouts=stereo",
        *atempo_chain(clip.speed),
    ]
    if abs(gain - 1.0) > 1e-6:
        steps.append(f"volume={num(gain)}")
    steps += _fade_steps(clip, duration, micro_in, micro_out)
    steps += [f"apad=whole_dur={num(duration)}", f"atrim=duration={num(duration)}"]
    return steps


def _fade_steps(clip: Clip, duration: float, micro_in: bool, micro_out: bool) -> list[str]:
    fin, fout = fx.fades(clip, duration)
    if fin <= 0 and micro_in:
        fin = min(fx.MICRO_FADE, duration / 2)
    if fout <= 0 and micro_out:
        fout = min(fx.MICRO_FADE, duration / 2)
    steps = []
    if fin > 0:
        steps.append(f"afade=t=in:st=0:d={num(fin)}")
    if fout > 0:
        steps.append(f"afade=t=out:st={num(duration - fout)}:d={num(fout)}")
    return steps


def _silence(duration: float) -> str:
    return (f"anullsrc=r={AUDIO_RATE}:cl=stereo,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"atrim=duration={num(duration)}")


def _concat(labels: list[str], out: str, *, video: bool) -> str:
    v, a = (1, 0) if video else (0, 1)
    return f"{''.join(f'[{x}]' for x in labels)}concat=n={len(labels)}:v={v}:a={a}[{out}]"


def video_has_audio(project: Project) -> bool:
    """¿El audio propio del video (voz/sistema) suena? Es el sidechain del ducking (B1)."""
    by_id = {a.id: a for a in project.assets}
    return not project.video.muted and project.video.gain > 0 and any(
        by_id[c.asset_id].has_audio for c in project.video.clips if c.asset_id in by_id
    )


def _join_audio_track(chains: list[str], labels: list[str], transitions, prefix: str) -> str:  # noqa: ANN001
    """Une los clips de una pista de audio con `concat` (corte) o `acrossfade` (transición)."""
    if len(labels) == 1:
        return labels[0]
    current = labels[0]
    for index in range(1, len(labels)):
        out = f"{prefix}{index}"
        transition = transitions[index - 1] if index - 1 < len(transitions) else None
        if transition is not None:
            chains.append(
                f"[{current}][{labels[index]}]acrossfade=d={num(transition.duration)}:c1=tri:c2=tri[{out}]"
            )
        else:
            chains.append(f"[{current}][{labels[index]}]concat=n=2:v=0:a=1[{out}]")
        current = out
    return current


def _audio_section(
    project: Project, inputs: list[str], chains: list[str], *, first_extra_input: int,
    total_frames: int, frames: list[int], overlaps: list[int],
) -> str:
    """Añade a `chains` el audio del video y las pistas extra; devuelve la etiqueta de salida.

    `first_extra_input` es el índice de la primera entrada de las pistas de audio; las entradas
    de video ya están en `inputs` (una por clip) y se numeran desde 0.
    """
    track = project.video
    by_id = {a.id: a for a in project.assets}
    n = len(track.clips)
    labels: list[str] = []
    track_gain = 0.0 if track.muted else track.gain
    for k, clip in enumerate(track.clips):
        skip = overlaps[k - 1] / project.fps if k > 0 else 0.0
        duration = (frames[k] - (overlaps[k - 1] if k > 0 else 0)) / project.fps
        gain = fx.gain(clip) * track_gain
        label = f"a{k}"
        if by_id[clip.asset_id].has_audio and gain > 0:
            steps = _audio_steps(clip, skip=skip, duration=duration, gain=gain,
                                 micro_in=k > 0, micro_out=k < n - 1)
            chains.append(f"[{k}:a]{','.join(steps)}[{label}]")
        else:
            chains.append(f"{_silence(duration)}[{label}]")
        labels.append(label)
    main = labels[0]
    if len(labels) > 1:
        main = "amain"
        chains.append(_concat(labels, main, video=False))

    mixes = [main]
    total = total_frames / project.fps
    next_input = first_extra_input
    for t_index, extra in enumerate(project.audio):
        if extra.muted or extra.gain <= 0 or not extra.clips:
            continue
        extra_labels: list[str] = []
        for j, clip in enumerate(extra.clips):
            asset = by_id[clip.asset_id]
            label = f"m{t_index}_{j}"
            if not asset.has_audio:
                chains.append(f"{_silence(clip.duration)}[{label}]")
            else:
                inputs += ["-ss", num(clip.in_), "-t", num(clip.source_duration + _INPUT_SLACK),
                           "-i", asset.path]
                transition_in = j > 0 and extra.transitions[j - 1] is not None
                transition_out = j < len(extra.clips) - 1 and extra.transitions[j] is not None
                steps = _audio_steps(clip, skip=0.0, duration=clip.duration,
                                     gain=fx.gain(clip) * extra.gain,
                                     micro_in=j > 0 and not transition_in,
                                     micro_out=j < len(extra.clips) - 1 and not transition_out)
                chains.append(f"[{next_input}:a]{','.join(steps)}[{label}]")
                next_input += 1
            extra_labels.append(label)
        source = _join_audio_track(chains, extra_labels, extra.transitions, f"m{t_index}c")
        joined = f"m{t_index}"
        chains.append(f"[{source}]apad=whole_dur={num(total)},atrim=duration={num(total)}[{joined}]")
        if extra.ducking > 0 and video_has_audio(project):
            # El audio del video (voz/sistema) es el sidechain; la música baja cuando suena.
            ratio = 2.0 + 10.0 * extra.ducking
            ducked = f"m{t_index}d"
            chains.append(
                f"[{joined}][{main}]sidechaincompress=threshold=0.05:ratio={num(ratio)}"
                f":attack=20:release=350[{ducked}]"
            )
            joined = ducked
        mixes.append(joined)
    if len(mixes) > 1:
        chains.append(f"{''.join(f'[{m}]' for m in mixes)}amix=inputs={len(mixes)}:normalize=0"
                      f":duration=first[aout]")
        return "aout"
    return main


# -- textos (B2) ------------------------------------------------------------------------

#: Fuente por defecto de los textos. Windows es la plataforma objetivo (docs/01); si falta,
#: FFmpeg devuelve un error explicable al exportar.
FONT_REGULAR = "C:/Windows/Fonts/arial.ttf"
FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"


def _escape_text(value: str) -> str:
    """Escapa el texto para `drawtext=text='…'` (comillas, barras y dos puntos)."""
    return (value.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
            .replace(",", "\\,").replace(";", "\\;").replace("\n", " "))


def _escape_path(value: str) -> str:
    return value.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def text_filters(project: Project, output_height: int) -> list[str]:
    """Filtros `drawtext` de los textos del proyecto, escalados al alto de salida."""
    if project.height <= 0:
        return []
    filters: list[str] = []
    for overlay in project.texts:
        size = max(1, int(round(overlay.size * output_height / project.height)))
        font = FONT_BOLD if overlay.bold else FONT_REGULAR
        color = "0x" + overlay.color.lstrip("#")
        filters.append(
            f"drawtext=fontfile='{_escape_path(font)}':text='{_escape_text(overlay.text)}':"
            f"fontsize={size}:fontcolor={color}:expansion=none:"
            f"x=(w*{num(overlay.x)})-(text_w/2):y=(h*{num(overlay.y)})-(text_h/2):"
            f"borderw=2:bordercolor=black@0.55:"
            f"enable='between(t,{num(overlay.start)},{num(overlay.end)})'"
        )
    return filters


# -- builder ----------------------------------------------------------------------------


def project_has_audio(project: Project) -> bool:
    by_id = {a.id: a for a in project.assets}
    video_audio = not project.video.muted and project.video.gain > 0 and any(
        by_id[c.asset_id].has_audio for c in project.video.clips if c.asset_id in by_id
    )
    extra = any(not t.muted and t.gain > 0 and t.clips for t in project.audio)
    return video_audio or extra


def build(
    project: Project,
    preset: ExportPreset,
    out_path: str | Path,
    *,
    encoder: str = "libx264",
    exe: str = "ffmpeg",
    progress: bool = True,
) -> GraphSpec:
    """Comando de render completo. `ProjectError` si el proyecto no se puede exportar así."""
    if preset.video == "copy":
        raise ProjectError("el preset «sin recodificar» no usa filter graph (ver core.keyframes)")
    track = project.video
    if not track.clips:
        raise ProjectError("el timeline está vacío: no hay nada que exportar")
    project.validate()
    fps = project.fps
    size = output_size(project, preset)
    frames, overlaps, total_frames = timeline_frames(project)
    by_id = {a.id: a for a in project.assets}
    has_audio = project_has_audio(project)

    inputs: list[str] = []
    chains: list[str] = []
    n = len(track.clips)

    # -- video: una entrada por clip ------------------------------------------------
    for k, clip in enumerate(track.clips):
        asset = by_id[clip.asset_id]
        inputs += ["-ss", num(clip.in_), "-t", num(clip.source_duration + _INPUT_SLACK), "-i", asset.path]
        chains.append(f"[{k}:v]{','.join(_video_chain(project, clip, frames[k], size))}[v{k}]")

    # composición: cortes consecutivos → un `concat`; transiciones → `xfade` por parejas
    pending: list[str] = ["v0"]  # bloque en curso (se une con `concat`)
    pending_frames = frames[0]
    step = 0

    def flush() -> str:
        nonlocal step
        if len(pending) == 1:
            return pending[0]
        step += 1
        label = f"c{step}"
        chains.append(_concat(pending, label, video=True))
        return label

    for k in range(1, n):
        overlap = overlaps[k - 1]
        if overlap == 0:
            pending.append(f"v{k}")
            pending_frames += frames[k]
            continue
        head = flush()
        transition = track.transitions[k - 1]
        assert transition is not None
        step += 1
        label = f"x{step}"
        chains.append(
            f"[{head}][v{k}]xfade=transition={transition.kind}:duration={num(overlap / fps)}"
            f":offset={num((pending_frames - overlap) / fps)}[{label}]"
        )
        pending, pending_frames = [label], pending_frames + frames[k] - overlap
    video_out = flush()

    # -- textos: se queman sobre el video ya compuesto (tiempo global) --------------------
    filters = text_filters(project, size[1])
    if filters:
        chains.append(f"[{video_out}]{','.join(filters)}[vtext]")
        video_out = "vtext"

    # -- audio --------------------------------------------------------------------------
    audio_out = ""
    if has_audio:
        audio_out = _audio_section(
            project, inputs, chains, first_extra_input=len(track.clips), total_frames=total_frames,
            frames=frames, overlaps=overlaps,
        )

    filter_complex = ";".join(chains)
    args: list[str] = [exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *inputs,
                       "-filter_complex", filter_complex, "-map", f"[{video_out}]"]
    if audio_out:
        args += ["-map", f"[{audio_out}]"]
    args += [*video_codec_args(encoder, preset), "-pix_fmt", "yuv420p"]
    if audio_out:
        args += ["-c:a", "aac", "-b:a", preset.audio_bitrate, "-ar", str(AUDIO_RATE)]
    else:
        args += ["-an"]
    args += ["-movflags", "+faststart"]
    if progress:
        args += ["-progress", "pipe:1", "-nostats"]
    args += [str(out_path)]
    return GraphSpec(
        args=tuple(args),
        filter_complex=filter_complex,
        duration=total_frames / fps,
        frames=total_frames,
        width=size[0],
        height=size[1],
        fps=fps,
        has_audio=bool(audio_out),
        encoder=encoder,
    )


def build_audio(
    project: Project, out_path: str | Path, *, exe: str = "ffmpeg"
) -> tuple[str, ...]:
    """Comando FFmpeg **solo audio** (WAV PCM) para el preview de B1, con la misma mezcla,
    crossfades y ducking que el export. Devuelve los argumentos completos."""
    track = project.video
    if not track.clips:
        raise ProjectError("el timeline está vacío: no hay audio que previsualizar")
    project.validate()
    if not project_has_audio(project):
        raise ProjectError("el proyecto no tiene audio")
    frames, overlaps, total_frames = timeline_frames(project)
    by_id = {a.id: a for a in project.assets}
    inputs: list[str] = []
    for clip in track.clips:
        asset = by_id[clip.asset_id]
        inputs += ["-ss", num(clip.in_), "-t", num(clip.source_duration + _INPUT_SLACK),
                   "-i", asset.path]
    chains: list[str] = []
    audio_out = _audio_section(
        project, inputs, chains, first_extra_input=len(track.clips), total_frames=total_frames,
        frames=frames, overlaps=overlaps,
    )
    return tuple([
        exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *inputs,
        "-filter_complex", ";".join(chains), "-map", f"[{audio_out}]",
        "-c:a", "pcm_s16le", "-ar", str(AUDIO_RATE), "-ac", "2",
        "-f", "wav", str(out_path),
    ])
