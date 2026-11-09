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

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from . import animation as anim
from . import audio_envelope as env
from . import background
from . import effects as fx
from . import elements as el
from . import global_effects
from . import overlays
from . import text_layout
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
    # Export personalizado (docs/44): si `width` y `height` están, mandan sobre `max_height`;
    # `fps` solo cambia la cadencia de salida y `video_bitrate` sustituye CRF/CQ por `-b:v`.
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    video_bitrate: str | None = None
    # Formato del contenedor (docs/48). `mp4` es el histórico y no cambia ninguna cadena; `webm`
    # usa VP9/Opus y `gif` una paleta de 256 colores sin audio.
    format: Literal["mp4", "webm", "gif"] = "mp4"


@dataclass(frozen=True)
class SocialPreset:
    """Acceso rápido a un tamaño social (solo rellena la configuración personalizada)."""

    id: str
    label: str
    width: int
    height: int


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

# -- formatos de export (docs/48) -----------------------------------------------------
EXPORT_FORMATS = ("mp4", "webm", "gif")
FORMAT_EXTENSIONS = {"mp4": ".mp4", "webm": ".webm", "gif": ".gif"}
#: GIF es un formato de intercambio: 20 fps y 720 px de lado máximo evitan archivos enormes sin
#: que el movimiento de una grabación de pantalla se vea a saltos.
GIF_MAX_FPS = 20
GIF_MAX_SIDE = 720

# -- export personalizado (docs/44) ---------------------------------------------------
CUSTOM_PRESET_ID = "personalizado"
CUSTOM_CODECS = ("auto", "x264")  # auto = GPU si hay; x264 = CPU
CUSTOM_MIN_DIMENSION = 16
CUSTOM_MAX_DIMENSION = 7680
CUSTOM_FPS_MIN, CUSTOM_FPS_MAX = 1, 240
VIDEO_BITRATE_MIN, VIDEO_BITRATE_MAX = 100_000, 200_000_000
AUDIO_BITRATE_MIN, AUDIO_BITRATE_MAX = 32_000, 512_000
DEFAULT_VIDEO_BITRATE = "6M"
DEFAULT_AUDIO_BITRATE = "192k"
SOCIAL_PRESETS: tuple[SocialPreset, ...] = (
    SocialPreset("9x16", "9:16", 1080, 1920),
    SocialPreset("1x1", "1:1", 1080, 1080),
    SocialPreset("4x5", "4:5", 1080, 1350),
    SocialPreset("16x9", "16:9", 1920, 1080),
)

_BITRATE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([kKmMgG]?)\s*$")


def parse_bitrate(value: str | None) -> int | None:
    """Bits/s de un texto como `6M`, `6000k` o `6000` (sin sufijo = kbps), o `None`.

    Se acepta coma decimal (`1,5M`) y mayúsculas. El rango lo valida quien llama: aquí solo se
    comprueba el formato para que la UI y `Settings` compartan el mismo criterio.
    """
    if value is None:
        return None
    match = _BITRATE_RE.match(str(value))
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    suffix = match.group(2).lower()
    factor = {"": 1_000, "k": 1_000, "m": 1_000_000, "g": 1_000_000_000}[suffix]
    bits = int(round(number * factor))
    return bits if bits > 0 else None


def bitrate_arg(bits: int) -> str:
    """Argumento de FFmpeg (`192k`, `6M`) para unos bits/s."""
    if bits % 1_000_000 == 0:
        return f"{bits // 1_000_000}M"
    if bits % 1_000 == 0:
        return f"{bits // 1_000}k"
    return str(bits)


def format_bitrate(bits: int) -> str:
    """Texto corto para el resumen: `6 Mbps`, `192 kbps`."""
    if bits >= 1_000_000:
        value = f"{bits / 1_000_000:.3f}".rstrip("0").rstrip(".")
        return f"{value} Mbps"
    value = f"{bits / 1_000:.3f}".rstrip("0").rstrip(".")
    return f"{value} kbps"


def _dimension_error(label: str, value: str) -> str | None:
    text = str(value).strip()
    if not text.isdigit():
        return f"{label} debe ser un número entero entre {CUSTOM_MIN_DIMENSION} y {CUSTOM_MAX_DIMENSION}."
    number = int(text)
    if not CUSTOM_MIN_DIMENSION <= number <= CUSTOM_MAX_DIMENSION:
        return f"{label} debe estar entre {CUSTOM_MIN_DIMENSION} y {CUSTOM_MAX_DIMENSION} px."
    return None


def custom_error(
    width: str | int,
    height: str | int,
    fps: str | int,
    video_bitrate: str | None,
    audio_bitrate: str | None,
) -> str | None:
    """Primer error de la configuración personalizada, o `None` si es válida.

    Los pares son obligatorios porque H.264 4:2:0 no admite dimensiones impares; el resto se acota
    a rangos razonables. La UI y `Settings.normalized()` usan este mismo criterio.
    """
    error = _dimension_error("El ancho", width)
    if error:
        return error
    error = _dimension_error("El alto", height)
    if error:
        return error
    if int(str(width).strip()) % 2 or int(str(height).strip()) % 2:
        return "El ancho y el alto deben ser pares (H.264 4:2:0)."
    fps_text = str(fps).strip()
    if not fps_text.isdigit() or not CUSTOM_FPS_MIN <= int(fps_text) <= CUSTOM_FPS_MAX:
        return f"Los fps deben ser un número entero entre {CUSTOM_FPS_MIN} y {CUSTOM_FPS_MAX}."
    video_bits = parse_bitrate(video_bitrate)
    if video_bits is None:
        return "Bitrate de video no válido: usa por ejemplo 6000k o 6M."
    if not VIDEO_BITRATE_MIN <= video_bits <= VIDEO_BITRATE_MAX:
        return (f"El bitrate de video debe estar entre {format_bitrate(VIDEO_BITRATE_MIN)} "
                f"y {format_bitrate(VIDEO_BITRATE_MAX)}.")
    audio_bits = parse_bitrate(audio_bitrate)
    if audio_bits is None:
        return "Bitrate de audio no válido: usa por ejemplo 192k."
    if not AUDIO_BITRATE_MIN <= audio_bits <= AUDIO_BITRATE_MAX:
        return (f"El bitrate de audio debe estar entre {format_bitrate(AUDIO_BITRATE_MIN)} "
                f"y {format_bitrate(AUDIO_BITRATE_MAX)}.")
    return None


def custom_preset(
    width: str | int,
    height: str | int,
    fps: str | int,
    video_bitrate: str,
    audio_bitrate: str,
    *,
    codec: str = "x264",
    format: str = "mp4",
) -> ExportPreset:
    """Preset de export personalizado ya validado (`ProjectError` si algo no cuadra)."""
    error = custom_error(width, height, fps, video_bitrate, audio_bitrate)
    if error:
        raise ProjectError(error)
    if codec not in CUSTOM_CODECS:
        raise ProjectError(f"códec personalizado desconocido: {codec!r}")
    if format not in EXPORT_FORMATS:
        raise ProjectError(f"formato de export desconocido: {format!r}")
    video_bits = parse_bitrate(video_bitrate)
    audio_bits = parse_bitrate(audio_bitrate)
    assert video_bits is not None and audio_bits is not None  # lo garantiza custom_error
    return ExportPreset(
        CUSTOM_PRESET_ID,
        "Personalizado",
        "Tamaño, fps y bitrate elegidos a mano; GPU si existe o x264.",
        "hw" if codec == "auto" else "x264",
        21,
        "medium",
        bitrate_arg(audio_bits),
        width=even(int(str(width).strip())),
        height=even(int(str(height).strip())),
        fps=int(str(fps).strip()),
        video_bitrate=bitrate_arg(video_bits),
        format=format,  # type: ignore[arg-type]
    )


def preset_has_custom_stream(preset: ExportPreset) -> bool:
    """¿El preset lleva tamaño, fps o bitrate personalizados? («copia» no los admite)."""
    return any(value is not None for value in (preset.width, preset.height, preset.fps,
                                               preset.video_bitrate))


def export_fps(project: Project, preset: ExportPreset) -> int:
    """FPS efectivos de salida (el del preset personalizado o el del proyecto).

    GIF se limita a `GIF_MAX_FPS`: es un formato de intercambio y a 50–60 fps el peso y el tiempo
    de codificación se disparan sin ganancia visible.
    """
    fps = int(preset.fps) if preset.fps else int(project.fps)
    return min(fps, GIF_MAX_FPS) if preset.format == "gif" else fps


def format_error(preset: ExportPreset) -> str | None:
    """Primer problema del formato/contenedor, o `None` si la combinación es válida."""
    if preset.format not in EXPORT_FORMATS:
        return f"formato de export desconocido: {preset.format!r}"
    if preset.format != "mp4" and preset.video == "copy":
        return (f"{preset.format.upper()} no admite el preset «sin recodificar»: "
                "elige MP4 o un preset que recodifique")
    return None


def check_format(preset: ExportPreset) -> None:
    """`ProjectError` si el formato del preset no es válido (lo usan `build` y `Exporter`)."""
    error = format_error(preset)
    if error:
        raise ProjectError(error)


def format_extension(fmt: str) -> str:
    """Extensión con punto del formato (`.mp4`, `.webm`, `.gif`)."""
    try:
        return FORMAT_EXTENSIONS[fmt]
    except KeyError as exc:
        raise ProjectError(f"formato de export desconocido: {fmt!r}") from exc


def with_format(preset: ExportPreset, fmt: str) -> ExportPreset:
    """Copia del preset con otro contenedor (la UI elige el formato aparte del preset)."""
    if fmt not in EXPORT_FORMATS:
        raise ProjectError(f"formato de export desconocido: {fmt!r}")
    return replace(preset, format=fmt)  # type: ignore[arg-type]


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
    if preset.width is not None or preset.height is not None:
        if preset.width is None or preset.height is None:
            raise ProjectError("el tamaño personalizado necesita ancho y alto")
        width = min(CUSTOM_MAX_DIMENSION, max(CUSTOM_MIN_DIMENSION, int(preset.width)))
        height = min(CUSTOM_MAX_DIMENSION, max(CUSTOM_MIN_DIMENSION, int(preset.height)))
    else:
        width, height = project.width, project.height
        if preset.max_height and height > preset.max_height:
            width, height = width * preset.max_height / height, preset.max_height
    if preset.format == "gif" and max(width, height) > GIF_MAX_SIDE:
        # Lado mayor acotado (720 px): un GIF de 1080p multiplica el peso y el tiempo de paleta.
        factor = GIF_MAX_SIDE / max(width, height)
        width, height = width * factor, height * factor
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


def _bitrate_codec_args(encoder: str, preset: ExportPreset) -> list[str]:
    """Argumentos de bitrate: `-b:v` (+ maxrate/bufsize) en lugar de CRF/CQ (docs/44)."""
    bits = parse_bitrate(preset.video_bitrate)
    if bits is None:
        raise ProjectError(f"bitrate de video no válido: {preset.video_bitrate!r}")
    rate = bitrate_arg(bits)
    extra = ["-b:v", rate, "-maxrate", bitrate_arg(int(bits * 1.5)),
             "-bufsize", bitrate_arg(bits * 2)]
    if encoder == "h264_nvenc":
        return ["-c:v", encoder, "-preset", "p5", "-rc", "vbr", *extra]
    if encoder == "h264_amf":
        return ["-c:v", encoder, "-quality", "balanced", "-rc", "vbr", *extra]
    if encoder == "h264_qsv":
        return ["-c:v", encoder, "-preset", "veryfast", *extra]
    if encoder == "libx264":
        return ["-c:v", encoder, "-preset", preset.x264_preset, *extra]
    raise ProjectError(f"encoder desconocido: {encoder!r}")


def video_codec_args(encoder: str, preset: ExportPreset) -> list[str]:
    if preset.format == "webm":
        if encoder != "libvpx-vp9":
            raise ProjectError(
                f"WebM usa VP9 (libvpx-vp9): el encoder {encoder!r} no vale para este formato"
            )
        common = ["-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", "2", "-row-mt", "1"]
        bits = parse_bitrate(preset.video_bitrate)
        if preset.video_bitrate:
            if bits is None:
                raise ProjectError(f"bitrate de video no válido: {preset.video_bitrate!r}")
            return [*common, "-b:v", bitrate_arg(bits), "-maxrate", bitrate_arg(int(bits * 1.5)),
                    "-bufsize", bitrate_arg(bits * 2)]
        return [*common, "-crf", str(preset.quality), "-b:v", "0"]
    if preset.format == "gif":
        if encoder != "gif":
            raise ProjectError(
                f"GIF se codifica con su propio encoder de paleta: {encoder!r} no vale"
            )
        return ["-c:v", "gif"]
    if preset.video_bitrate:
        return _bitrate_codec_args(encoder, preset)
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
    if preset.format == "webm":
        if not available.get("libvpx-vp9"):
            raise ProjectError(
                "WebM necesita el encoder VP9 (libvpx-vp9), que no está disponible en este FFmpeg"
            )
        return "libvpx-vp9"
    if preset.format == "gif":
        return "gif"
    if preset.video == "hw":
        for name in HW_ENCODERS:
            if available.get(name):
                return name
    return "libx264"


# -- cadenas por clip ----------------------------------------------------------------


def _setpts(clip: Clip) -> str:
    return f"setpts=(PTS-STARTPTS)/{num(clip.speed)}" if clip.speed != 1.0 else "setpts=PTS-STARTPTS"


def _eq_step(clip: Clip) -> str | None:
    eq = fx.eq(clip)
    if eq is None:
        return None
    return (f"eq=brightness={num(eq['brightness'])}:contrast={num(eq['contrast'])}"
            f":saturation={num(eq['saturation'])}")


def _clip_finish(project: Project, clip: Clip, frames: int) -> list[str]:
    """Cola común: fps, duración entera de frames, fundidos y formato de salida."""
    fps = project.fps
    steps = [
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


def _custom_steps(project: Project, clip: Clip, size: tuple[int, int]) -> tuple[list[str], tx.Layout]:
    """Pasos de la cadena nueva desde el `setpts` hasta el `scale` del clip (sin componer)."""
    steps = [_setpts(clip)]
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
        steps.append(f"crop=w={lay.crop_width}:h={lay.crop_height}:x={lay.crop_x}:y={lay.crop_y}")
    factor, cx, cy = fx.zoom(clip)
    if factor > 1.0 + 1e-6:
        steps.append(
            f"crop=w=iw/{num(factor)}:h=ih/{num(factor)}:x=(iw-ow)*{num(cx)}:y=(ih-oh)*{num(cy)}"
        )
    steps.append(f"scale={lay.out_width}:{lay.out_height}")
    return steps, lay


def _video_chain(project: Project, clip: Clip, frames: int, size: tuple[int, int]) -> list[str]:
    w, h = size
    custom = tx.needs_custom_render(project, clip)
    if custom:
        steps, lay = _custom_steps(project, clip, size)
        steps += tx.compose_filters(lay)
        eq = _eq_step(clip)  # después de la composición (el fondo no se recorta)
        if eq is not None:
            steps.append(eq)
        steps += _clip_finish(project, clip, frames)
        return steps
    # Cadena histórica de F4: se conserva byte a byte para los golden y el corte rápido.
    steps = [_setpts(clip)]
    factor, cx, cy = fx.zoom(clip)
    if factor > 1.0 + 1e-6:
        steps.append(f"crop=w=iw/{num(factor)}:h=ih/{num(factor)}:x=(iw-ow)*{num(cx)}:y=(ih-oh)*{num(cy)}")
    eq = _eq_step(clip)
    if eq is not None:
        steps.append(eq)
    steps += [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:-1:-1:color=black",
        *_clip_finish(project, clip, frames),
    ]
    return steps


def _blur_background(project: Project, source: str, out: str, size: tuple[int, int]) -> str:
    """Copia de V1 escalada a cover, recortada al lienzo y desenfocada (fondo blur)."""
    w, h = size
    radius = int(round(background.blur_radius(project, h)))
    step = ""
    if radius >= 1:
        radius = min(radius, max(1, min(w, h) // 2 - 1))
        step = f",boxblur=luma_radius={radius}:luma_power=2"
    return (
        f"[{source}]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h}{step}[{out}]"
    )


def _background_clip_chains(
    project: Project,
    clip: Clip,
    frames: int,
    size: tuple[int, int],
    source: int,
    out_label: str,
    *,
    image_label: str | None,
) -> list[str]:
    """Cadenas de un clip con el fondo blur/imagen y el clip normal encima.

    Blur: `split` del clip, copia a cover + `boxblur` de fondo y `overlay` del clip encima.
    Imagen: la imagen ya preparada (`image_label`) de fondo y el clip encima. El `overlay`
    sustituye al `pad`/`crop` histórico; el resto de la cola (eq, fps, frames, fundidos y
    formato) es la misma.
    """
    steps, lay = _custom_steps(project, clip, size)
    tail = _clip_finish(project, clip, frames)
    eq = _eq_step(clip)  # después de componer el fondo y el clip
    if eq is not None:
        tail.insert(0, eq)
    if image_label is None:
        background_label = f"bg{source}"
        foreground = f"fg{source}"
        chains = [
            f"[{source}:v]{','.join(steps)},split=2[bs{source}][{foreground}]",
            _blur_background(project, f"bs{source}", background_label, size),
        ]
    else:
        background_label = image_label
        foreground = f"fg{source}"
        chains = [f"[{source}:v]{','.join(steps)}[{foreground}]"]
    chains.append(
        f"[{background_label}][{foreground}]overlay=x={lay.x}:y={lay.y}:format=auto"
        f":shortest=1,{','.join(tail)}[{out_label}]"
    )
    return chains


def _image_background_chains(
    project: Project, size: tuple[int, int], count: int, input_index: int
) -> list[str]:
    """Prepara la imagen de fondo una vez y la reparte a los `count` clips (`split`)."""
    w, h = size
    steps = [
        f"scale={w}:{h}:force_original_aspect_ratio=increase",
        f"crop={w}:{h}",
        "setsar=1",
        f"fps={project.fps}",
        "setpts=PTS-STARTPTS",
    ]
    if count == 1:
        return [f"[{input_index}:v]{','.join(steps)}[bg0]"]
    labels = "".join(f"[bg{index}]" for index in range(count))
    return [f"[{input_index}:v]{','.join(steps)},split={count}{labels}"]


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
        if element.envelope:
            # La fuente se lee desde `in_` y se recorta al intervalo: `t=0` es el inicio del
            # elemento, igual que la curva local. El `adelay` hasta `start` va después.
            expression = env.expr(element.envelope)
            steps.append(f"volume=volume='{expression}':eval=frame")
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


def text_filter(overlay, project_height: int, output_height: int) -> str:  # noqa: ANN001
    """Un filtro `drawtext` para un texto, escalado al alto de salida."""
    size = max(1, int(round(overlay.size * output_height / project_height)))
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
    return (
        f"drawtext=fontfile='{_escape_path(font)}':text='{_escape_text(overlay.text)}':"
        f"fontsize={size}:fontcolor={color}:expansion=none:"
        f"{x_part}:{y_part}:"
        f"borderw=2:bordercolor=black@0.55{alpha}:"
        f"enable='between(t,{num(overlay.start)},{num(overlay.end)})'"
    )


def text_filters(project: Project, output_height: int) -> list[str]:
    """Filtros `drawtext` de los textos del proyecto, escalados al alto de salida."""
    if project.height <= 0:
        return []
    return [
        text_filter(overlay, project.height, output_height) for overlay in project.texts
    ]


def _text_needs_bake(overlay) -> bool:  # noqa: ANN001
    """¿Necesita el vídeo RGBA intermedio? Animaciones no dibujables o texto multilínea."""
    return (
        bool(getattr(overlay, "color_keys", ()))
        or text_layout.breaks_lines(overlay)
        or any(anim.is_animated(overlay.animations, channel) for channel in anim.TEXT_BAKE_CHANNELS)
    )


def _text_visible(project: Project, overlay) -> bool:  # noqa: ANN001
    total = tl.total_duration(project)
    return total > 0 and float(overlay.start) < total and float(overlay.end) > 0


def _text_overlays(
    project: Project,
    video_out: str,
    chains: list[str],
    inputs: list[str],
    size: tuple[int, int],
    *,
    first_input: int,
    text_clips: Mapping[str, overlays.TextClip],
) -> str:
    """Compone los textos en orden: `drawtext` o `overlay` de un `.mov` horneado (docs/35/36).

    Se recorre `Project.texts` de principio a fin y cada texto se aplica sobre la etiqueta
    anterior, así que el orden de apilado es el mismo que el del preview aunque unos textos usen
    `drawtext` y otros el intermedio RGBA.
    """
    label = video_out
    next_input = int(first_input)
    fps = max(1, int(project.fps))
    for index, overlay in enumerate(project.texts):
        clip = text_clips.get(overlay.id) if _text_needs_bake(overlay) else None
        if clip is None and _text_needs_bake(overlay) and _text_visible(project, overlay):
            raise ProjectError(
                f"falta el vídeo animado del texto {overlay.id}: "
                "usa services.text_animation.materialize"
            )
        if clip is None:
            if project.height <= 0:
                continue
            out = f"vtext{index}"
            chains.append(f"[{label}]{text_filter(overlay, project.height, size[1])}[{out}]")
            label = out
            continue
        inputs += ["-i", str(clip.path)]
        source = next_input
        next_input += 1
        prepared = f"tx{index}"
        chains.append(
            f"[{source}:v]format=rgba,setpts=PTS-STARTPTS+{num(overlay.start)}/TB,"
            f"fps={fps}[{prepared}]"
        )
        x_value = anim.expr(overlay.animations, "x", overlay.start, overlay.x)
        y_value = anim.expr(overlay.animations, "y", overlay.start, overlay.y)
        x_part = f"main_w*{x_value}-overlay_w/2"
        y_part = f"main_h*{y_value}-overlay_h/2"
        if anim.is_animated(overlay.animations, "x"):
            x_part = f"'{x_part}'"
        if anim.is_animated(overlay.animations, "y"):
            y_part = f"'{y_part}'"
        enable = (
            f"between(t,{num(overlay.start)},{num(overlay.end)})"
            f"*lt(t,{num(overlay.end)})"
        )
        out = f"txo{index}"
        chains.append(
            f"[{label}][{prepared}]overlay=x={x_part}:y={y_part}:format=auto"
            f":eof_action=pass:enable='{enable}'[{out}]"
        )
        label = out
    return label


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
    text_clips: Mapping[str, overlays.TextClip] | None = None,
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

    `text_clips` (docs/35 y docs/36) son los vídeos RGBA intermedios de los textos con escala,
    rotación, tamaño o color animados; los prepara `services.text_animation.materialize`. Los
    textos sin esos canales siguen usando `drawtext`; si un proyecto no tiene ninguno, las
    cadenas no cambian.
    """
    check_format(preset)
    if preset.video == "copy":
        if preset_has_custom_stream(preset):
            raise ProjectError(
                "«Sin recodificar» no admite tamaño, fps ni bitrate personalizados: "
                "elige otro preset o quita esas opciones"
            )
        raise ProjectError("el preset «sin recodificar» no usa filter graph (ver core.keyframes)")
    if not project.video.clips:
        raise ProjectError("el timeline está vacío: no hay nada que exportar")
    project.validate()
    # El fps personalizado se aplica antes de calcular frames/transiciones: `fps=<objetivo>` entra
    # en la cadena de cada clip (el punto que exige la regla 1: todo igual antes de concat/xfade) y
    # las duraciones, offsets y `GraphSpec` quedan en la cadencia de salida (docs/44).
    if preset.fps is not None and not CUSTOM_FPS_MIN <= int(preset.fps) <= CUSTOM_FPS_MAX:
        raise ProjectError(f"fps de export no válido: {preset.fps!r} (usa {CUSTOM_FPS_MIN}–{CUSTOM_FPS_MAX})")
    target_fps = export_fps(project, preset)
    if target_fps != project.fps:
        project = replace(project, fps=target_fps)
    window: tuple[int, int] | None = None
    if span is not None:
        window = range_frames(project, *span)
        project = crop_tail(project, window[1] / project.fps)
    track = project.video
    fps = project.fps
    size = output_size(project, preset)
    frames, overlaps, total_frames = timeline_frames(project)
    by_id = {a.id: a for a in project.assets}
    has_audio = project_has_audio(project) and preset.format != "gif"

    inputs: list[str] = []
    chains: list[str] = []
    n = len(track.clips)

    # -- video: una entrada por clip ------------------------------------------------
    for clip in track.clips:
        asset = by_id[clip.asset_id]
        inputs += ["-ss", num(clip.in_), "-t", num(clip.source_duration + _INPUT_SLACK), "-i", asset.path]

    # -- fondo del lienzo (v16, docs/47): antes de la composición de V1 ------------------
    background_mode = background.effective_mode(project)
    background_asset = background.image_asset(project) if background_mode == "image" else None
    background_input: int | None = None
    if background_asset is not None:
        # Una sola entrada de imagen, repetida con `-loop 1` durante todo el timeline y
        # repartida por `split` entre los clips (cada uno la usa como fondo). Va después de
        # los clips para no cambiar sus índices de entrada.
        background_input = n  # los clips ocupan las entradas 0…n−1
        inputs += [
            "-loop", "1", "-framerate", str(fps),
            "-t", num(total_frames / fps + _PAD_FRAMES_S),
            "-i", background_asset.path,
        ]
        chains += _image_background_chains(project, size, n, background_input)

    for k, clip in enumerate(track.clips):
        if background_mode == "blur":
            chains += _background_clip_chains(project, clip, frames[k], size, k, f"v{k}",
                                              image_label=None)
        elif background_mode == "image":
            chains += _background_clip_chains(project, clip, frames[k], size, k, f"v{k}",
                                              image_label=f"bg{k}")
        else:
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
    baked_texts = text_clips or {}
    transformed = any(_text_needs_bake(overlay) for overlay in project.texts)
    if transformed:
        # Orden de `Project.texts` con una mezcla de `drawtext` y overlays horneados.
        video_out = _text_overlays(
            project, video_out, chains, inputs, size,
            first_input=inputs.count("-i"), text_clips=baked_texts,
        )
    else:
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

    if preset.format == "gif":
        # Una pasada: `split` alimenta a la vez el video y `palettegen`; `paletteuse` aplica la
        # paleta al flujo original. Es el patrón recomendado por FFmpeg para GIF de calidad.
        chains.append(f"[{video_out}]split=2[gifv][gifp]")
        chains.append("[gifp]palettegen=stats_mode=diff[gifpal]")
        chains.append(
            "[gifv][gifpal]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle[vgif]"
        )
        video_out = "vgif"

    filter_complex = ";".join(chains)
    args: list[str] = [exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *inputs,
                       "-filter_complex", filter_complex, "-map", f"[{video_out}]"]
    if audio_out:
        args += ["-map", f"[{audio_out}]"]
    if preset.format == "webm":
        args += [*video_codec_args(encoder, preset), "-pix_fmt", "yuv420p"]
        if audio_out:
            args += ["-c:a", "libopus", "-b:a", preset.audio_bitrate, "-ar", str(AUDIO_RATE)]
        else:
            args += ["-an"]
    elif preset.format == "gif":
        args += [*video_codec_args(encoder, preset), "-loop", "0", "-an"]
    else:
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
