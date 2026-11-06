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

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from . import animation as anim
from . import audio_envelope as env
from . import effects as fx
from . import elements as el
from . import global_effects
from . import overlays
from . import timeline as tl
from . import transform as tx
from .errors import ProjectError
from .project import (
    Clip,
    Element,
    MediaAsset,
    Project,
    Track,
    audio_solo_active,
    audio_track_audible,
    video_audio_gain,
)

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


def range_frames(project: Project, start: float, end: float) -> tuple[int, int]:
    """Frames `[a, b)` del timeline compuesto para exportar el rango `start`–`end` (s).

    Se redondea a frame y se acota a los frames reales del timeline (`timeline_frames`), así el
    rango es un número entero de frames y coincide con lo que se ve en el preview.
    """
    total = timeline_frames(project)[2]
    a = max(0, min(total, int(round(start * project.fps))))
    b = max(0, min(total, int(round(end * project.fps))))
    if b - a < 1:
        raise ProjectError("el rango In/Out está vacío: marca una entrada antes de la salida")
    return a, b


def crop_tail(project: Project, end: float) -> Project:
    """Quita los clips de video que empiezan en `end` o después (no se ven en el rango).

    Es solo una optimización del export de rango (FFmpeg no tiene que abrir ni decodificar lo
    que hay detrás). Lo anterior a `end` no se toca, así que el graph de esa parte es idéntico.
    Un audio separado cuyo video desaparece pierde el enlace (sigue sonando donde estaba).
    """
    track = project.video
    starts = tl.clip_starts(track)
    keep = sum(1 for s in starts if s < end - 1e-9)
    if keep >= len(track.clips) or keep == 0:
        return project
    dropped = {c.id for c in track.clips[keep:]}
    video = replace(track, clips=track.clips[:keep], transitions=track.transitions[: keep - 1])
    audio = tuple(
        replace(t, clips=tuple(replace(c, link_id=None) if c.link_id in dropped else c for c in t.clips))
        for t in project.audio
    )
    return replace(project, video=video, audio=audio)


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
    custom = tx.needs_custom_render(project, clip)
    if custom:
        lay = tx.layout(project, clip, size)
        # La auto-rotación del asset la aplica FFmpeg al abrir la entrada (display matrix); PyAV
        # no la aplica y el preview la hace explícita. No se añade `transpose` por asset: si se
        # desactiva la auto-rotación, FFmpeg copia la display matrix al MP4 y el reproductor
        # volvería a girarlo (doble rotación). Ver docs/20.
        steps += tx.rotation_filters(lay.rotation, clockwise=True)
        if lay.flip_h:
            steps.append("hflip")
        if lay.flip_v:
            steps.append("vflip")
        if lay.crop != tx.FULL_CROP:
            steps.append(
                f"crop=w={lay.crop_width}:h={lay.crop_height}:x={lay.crop_x}:y={lay.crop_y}"
            )
        factor, cx, cy = fx.zoom(clip)
        if factor > 1.0 + 1e-6:
            steps.append(
                f"crop=w=iw/{num(factor)}:h=ih/{num(factor)}:x=(iw-ow)*{num(cx)}:y=(ih-oh)*{num(cy)}"
            )
        steps.append(f"scale={lay.out_width}:{lay.out_height}")
        steps += tx.compose_filters(lay)
        eq = fx.eq(clip)  # después de la composición (el fondo no se recorta)
        if eq is not None:
            steps.append(f"eq=brightness={num(eq['brightness'])}:contrast={num(eq['contrast'])}"
                         f":saturation={num(eq['saturation'])}")
    else:
        # Cadena histórica de F4: se conserva byte a byte para los golden y el corte rápido.
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
        ]
    steps += [
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
    if clip.envelope:
        # La envolvente es local al clip: el tramo audible de un clip entrante empieza `skip` s
        # después, así que las claves se desplazan con `start=-skip`.
        expression = env.expr(clip.envelope, start=-skip)
        steps.append(f"volume=volume='{expression}':eval=frame")
    steps += _fade_steps(clip, duration, micro_in, micro_out)
    steps += [f"apad=whole_dur={num(duration)}", f"atrim=duration={num(duration)}"]
    return steps


def _fade_steps(clip: Clip, duration: float, micro_in: bool, micro_out: bool) -> list[str]:
    fin, fout = fx.audio_fades(clip, duration)
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
    return video_audio_gain(project) > 0 and any(
        by_id[c.asset_id].has_audio and c.audio_enabled
        for c in project.video.clips if c.asset_id in by_id
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


def _absolute_track_audio(
    project: Project, inputs: list[str], chains: list[str], *, track: Track, track_index: int,
    total: float, next_input: int, by_id: dict,
) -> tuple[str | None, int]:
    """Mezcla una pista absoluta: cada clip se retrasa con `adelay` hasta su `start`."""
    labels: list[str] = []
    for j, clip in enumerate(track.clips):
        asset = by_id[clip.asset_id]
        label = f"m{track_index}_{j}"
        if not asset.has_audio:
            chains.append(f"{_silence(clip.duration)}[{label}]")
        else:
            inputs += ["-ss", num(clip.in_), "-t", num(clip.source_duration + _INPUT_SLACK),
                       "-i", asset.path]
            steps = _audio_steps(
                clip, skip=0.0, duration=clip.duration, gain=fx.gain(clip) * track.gain,
                micro_in=False, micro_out=False,
            )
            delay = int(round(max(0.0, float(clip.start or 0.0)) * AUDIO_RATE))
            if delay > 0:
                steps.append(f"adelay=delays={delay}S:all=1")
            chains.append(f"[{next_input}:a]{','.join(steps)}[{label}]")
            next_input += 1
        labels.append(label)
    if not labels:
        return None, next_input
    if len(labels) == 1:
        source = labels[0]
    else:
        source = f"m{track_index}m"
        chains.append(
            f"{''.join(f'[{x}]' for x in labels)}amix=inputs={len(labels)}:normalize=0"
            f":duration=longest[{source}]"
        )
    joined = f"m{track_index}"
    chains.append(f"[{source}]apad=whole_dur={num(total)},atrim=duration={num(total)}[{joined}]")
    return joined, next_input


def _element_audio_mixes(
    project: Project, inputs: list[str], chains: list[str], *, total: float, next_input: int,
    element_inputs: Mapping[str, int] | None = None,
) -> tuple[list[str], int]:
    """Mezcla el audio de los videos superpuestos (PiP) y devuelve sus etiquetas.

    Si `element_inputs` trae la entrada que ya abrió `overlays.apply_graph` para el video, se
    reutiliza (`[N:a]`) y no se abre el archivo dos veces. El resto se añade aquí, como en
    `build_audio`, que no compone video.
    """
    labels: list[str] = []
    for index, (element, asset, start, end) in enumerate(element_audio(project)):
        duration = end - start
        source = (element_inputs or {}).get(element.id)
        if source is None:
            inputs += [
                "-ss", num(element.in_), "-t", num(duration + _INPUT_SLACK), "-i", asset.path,
            ]
            source = next_input
            next_input += 1
        gain = el.audio_gain(element)
        steps = [
            "asetpts=PTS-STARTPTS",
            f"aresample={AUDIO_RATE}",
            "aformat=sample_fmts=fltp:channel_layouts=stereo",
        ]
        if abs(gain - 1.0) > 1e-6:
            steps.append(f"volume={num(gain)}")
        # Microfundidos en los bordes del intervalo: evitan el chasquido de entrar a mitad de
        # una onda. No son los fundidos del usuario (el PiP todavía no tiene handles propios).
        fade = min(fx.MICRO_FADE, duration / 2)
        if fade > 1e-6:
            steps += [
                f"afade=t=in:st=0:d={num(fade)}",
                f"afade=t=out:st={num(duration - fade)}:d={num(fade)}",
            ]
        steps += [f"apad=whole_dur={num(duration)}", f"atrim=duration={num(duration)}"]
        delay = int(round(start * AUDIO_RATE))
        if delay > 0:
            steps.append(f"adelay=delays={delay}S:all=1")
        steps += [f"apad=whole_dur={num(total)}", f"atrim=duration={num(total)}"]
        label = f"pip{index}"
        chains.append(f"[{source}:a]{','.join(steps)}[{label}]")
        labels.append(label)
    return labels, next_input


def _audio_section(
    project: Project, inputs: list[str], chains: list[str], *, first_extra_input: int,
    total_frames: int, frames: list[int], overlaps: list[int],
    element_inputs: Mapping[str, int] | None = None,
) -> str:
    """Añade a `chains` el audio del video, el de los PiP y las pistas extra.

    `first_extra_input` es el índice de la primera entrada libre después de las de video (y de
    los overlays que ya están en `inputs`); las entradas de video se numeran desde 0.

    Reglas de la fase 1 (docs/19):
    - `Clip.audio_enabled=False` deja el tramo del video en silencio (el sonido vive en la pista
      absoluta separada).
    - Un solo de audio silencia las demás pistas **y** el audio del video.
    - Las pistas `absolute` se mezclan con `adelay` + `amix`; las `sequence` mantienen el
      `concat`/`acrossfade` de la música.

    El audio de un video superpuesto se suma como una fuente más (docs/28). También alimenta el
    sidechain del ducking: si el PiP lleva una voz, la música baja con ella.
    """
    track = project.video
    by_id = {a.id: a for a in project.assets}
    n = len(track.clips)
    labels: list[str] = []
    track_gain = video_audio_gain(project)
    for k, clip in enumerate(track.clips):
        skip = overlaps[k - 1] / project.fps if k > 0 else 0.0
        duration = (frames[k] - (overlaps[k - 1] if k > 0 else 0)) / project.fps
        gain = fx.gain(clip) * track_gain
        label = f"a{k}"
        if clip.audio_enabled and by_id[clip.asset_id].has_audio and gain > 0:
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

    total = total_frames / project.fps
    next_input = first_extra_input
    element_labels, next_input = _element_audio_mixes(
        project, inputs, chains, total=total, next_input=next_input,
        element_inputs=element_inputs,
    )
    mixes = [main, *element_labels]

    # Sidechain del ducking: video base + PiP (una voz superpuesta también baja la música).
    sidechain = main
    if element_labels and any(
        audio_track_audible(project, extra) and extra.clips and extra.ducking > 0
        for extra in project.audio
    ):
        sidechain = "aside"
        sources = "".join(f"[{label}]" for label in (main, *element_labels))
        chains.append(
            f"{sources}amix=inputs={1 + len(element_labels)}:normalize=0:duration=first[aside]"
        )

    for t_index, extra in enumerate(project.audio):
        if not audio_track_audible(project, extra) or not extra.clips:
            continue
        if extra.mode == "absolute":
            joined, next_input = _absolute_track_audio(
                project, inputs, chains, track=extra, track_index=t_index, total=total,
                next_input=next_input, by_id=by_id,
            )
            if joined is None:
                continue
        else:
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
        if extra.ducking > 0 and (video_has_audio(project) or element_labels):
            # El audio del video o del PiP (voz/sistema) es el sidechain; la música baja.
            ratio = 2.0 + 10.0 * extra.ducking
            ducked = f"m{t_index}d"
            chains.append(
                f"[{joined}][{sidechain}]sidechaincompress=threshold=0.05:ratio={num(ratio)}"
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
        x_animated = anim.is_animated(overlay.animations, "x")
        y_animated = anim.is_animated(overlay.animations, "y")
        x_value = anim.expr(overlay.animations, "x", overlay.start, overlay.x)
        y_value = anim.expr(overlay.animations, "y", overlay.start, overlay.y)
        x_part = f"x=(w*{x_value})-(text_w/2)"
        y_part = f"y=(h*{y_value})-(text_h/2)"
        if x_animated:
            x_part = f"x='(w*{x_value})-(text_w/2)'"
        if y_animated:
            y_part = f"y='(h*{y_value})-(text_h/2)'"
        if anim.is_animated(overlay.animations, "opacity"):
            opacity = anim.expr(overlay.animations, "opacity", overlay.start, overlay.opacity)
            alpha = f":alpha='{opacity}'"
        elif overlay.opacity < 0.999999:
            alpha = f":alpha={num(overlay.opacity)}"
        else:
            alpha = ""
        filters.append(
            f"drawtext=fontfile='{_escape_path(font)}':text='{_escape_text(overlay.text)}':"
            f"fontsize={size}:fontcolor={color}:expansion=none:"
            f"{x_part}:{y_part}:"
            f"borderw=2:bordercolor=black@0.55{alpha}:"
            f"enable='between(t,{num(overlay.start)},{num(overlay.end)})'"
        )
    return filters


# -- builder ----------------------------------------------------------------------------


def element_audio(project: Project) -> list[tuple[Element, MediaAsset, float, float]]:
    """Videos superpuestos con audio audible: `(elemento, asset, inicio, fin)`.

    El intervalo se recorta al timeline: un PiP que termina después del último clip no alarga
    la mezcla. Los elementos con `opacity=0` sí suenan si su volumen no es 0 (la opacidad es
    visual), pero un `gain=0` equivale a silencio.
    """
    total = tl.total_duration(project)
    if total <= 0 or audio_solo_active(project):
        return []  # un solo de audio silencia también las voces superpuestas
    by_id = {a.id: a for a in project.assets}
    items: list[tuple[Element, MediaAsset, float, float]] = []
    for element in project.elements:
        if not el.has_audio(element):
            continue
        asset = by_id.get(element.asset_id or "")
        if asset is None or not asset.has_audio:
            continue
        start = max(0.0, float(element.start))
        end = min(float(element.end), total)
        if end - start > 1e-6:
            items.append((element, asset, start, end))
    return items


def has_element_audio(project: Project) -> bool:
    """¿Hay algún video superpuesto cuyo audio haya que mezclar?"""
    return bool(element_audio(project))


def has_audio_envelope(project: Project) -> bool:
    """¿Algún clip de audio (video o pista extra) tiene envolvente de volumen? (docs/32)"""
    if any(clip.envelope for clip in project.video.clips):
        return True
    return any(clip.envelope for track in project.audio for clip in track.clips)


def project_has_audio(project: Project) -> bool:
    by_id = {a.id: a for a in project.assets}
    video_audio = video_audio_gain(project) > 0 and any(
        by_id[c.asset_id].has_audio and c.audio_enabled
        for c in project.video.clips if c.asset_id in by_id
    )
    extra = any(audio_track_audible(project, t) and t.clips for t in project.audio)
    return video_audio or extra or has_element_audio(project)


def build(
    project: Project,
    preset: ExportPreset,
    out_path: str | Path,
    *,
    encoder: str = "libx264",
    exe: str = "ffmpeg",
    progress: bool = True,
    span: tuple[float, float] | None = None,
    element_sources: Mapping[str, str] | None = None,
    element_clips: Mapping[str, overlays.ElementClip] | None = None,
) -> GraphSpec:
    """Comando de render completo. `ProjectError` si el proyecto no se puede exportar así.

    `span=(inicio, final)` exporta solo ese rango del timeline (fase 3, docs/21): el graph es el
    mismo y al final se recorta el video compuesto con `trim` por frames y el audio con `atrim`
    a los mismos instantes. Sin `span` el comando es idéntico al de siempre (golden de F4).

    `element_sources` (v6, docs/25) son los PNG ya materializados de los elementos. Solo hace
    falta cuando `project.elements` no está vacío; el export los prepara con
    `services.element_assets.materialize` antes de construir el graph.

    `element_clips` (docs/33) son los vídeos RGBA intermedios de los elementos con escala o
    rotación animadas; los prepara `services.element_animation.materialize`. Sin animación de
    geometría no se usan y las cadenas quedan como siempre.
    """
    if preset.video == "copy":
        raise ProjectError("el preset «sin recodificar» no usa filter graph (ver core.keyframes)")
    if not project.video.clips:
        raise ProjectError("el timeline está vacío: no hay nada que exportar")
    project.validate()
    window: tuple[int, int] | None = None
    if span is not None:
        window = range_frames(project, *span)
        project = crop_tail(project, window[1] / project.fps)
    track = project.video
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

    element_inputs: dict[str, int] = {}
    video_layer = tuple(e for e in project.elements if e.video_track > 0)
    generic_layer = tuple(e for e in project.elements if e.video_track <= 0)

    # -- V2/V3: capas de video antes de los efectos globales -----------------------------
    if video_layer:
        video_out, _ = overlays.apply_graph(
            replace(project, elements=video_layer), video_out, chains, inputs, size,
            first_input=inputs.count("-i"), sources=element_sources,
            video_inputs=element_inputs, element_clips=element_clips, prefix="vt",
        )

    # -- efectos globales: al video base y a las pistas V2/V3 ----------------------------
    video_out = global_effects.apply_graph(project, video_out, chains, output_height=size[1])

    # -- capa «Elementos» (imágenes, figuras y PiP suelto), debajo de los textos ---------
    if generic_layer:
        video_out, _ = overlays.apply_graph(
            replace(project, elements=generic_layer), video_out, chains, inputs, size,
            first_input=inputs.count("-i"), sources=element_sources,
            video_inputs=element_inputs, element_clips=element_clips,
        )

    # -- textos: se queman sobre el video ya compuesto (tiempo global) --------------------
    filters = text_filters(project, size[1])
    if filters:
        chains.append(f"[{video_out}]{','.join(filters)}[vtext]")
        video_out = "vtext"

    # -- audio --------------------------------------------------------------------------
    audio_out = ""
    if has_audio:
        audio_out = _audio_section(
            project, inputs, chains, first_extra_input=inputs.count("-i"),
            total_frames=total_frames, frames=frames, overlaps=overlaps,
            element_inputs=element_inputs,
        )

    # -- rango In/Out: recorte final por frames (mismo número entero de frames que el preview) --
    if window is not None:
        a, b = window
        chains.append(f"[{video_out}]trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS[vrange]")
        video_out = "vrange"
        if audio_out:
            chains.append(
                f"[{audio_out}]atrim=start={num(a / fps)}:end={num(b / fps)},asetpts=PTS-STARTPTS[arange]"
            )
            audio_out = "arange"
        total_frames = b - a

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
    )  # sin element_inputs: build_audio no compone video y abre sus propias entradas
    return tuple([
        exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *inputs,
        "-filter_complex", ";".join(chains), "-map", f"[{audio_out}]",
        "-c:a", "pcm_s16le", "-ar", str(AUDIO_RATE), "-ac", "2",
        "-f", "wav", str(out_path),
    ])
