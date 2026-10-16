"""FFmpeg: binario, prueba real de encoders, comandos de captura y mux.

Decisiones de F0 (ver `docs/08-f0-resultados.md`):
- El binario sale del wheel de `imageio-ffmpeg` (7.1 essentials, GPL, con x264); se puede
  sobrescribir con la variable de entorno `RECSTUDIO_FFMPEG`.
- Los encoders se prueban **codificando de verdad** (0.5 s de testsrc): en F0, AMF y QSV
  aparecían en `-encoders` pero no funcionaban sin hardware.
- El mux final usa `-c:v copy` + AAC 48 kHz; `-t <duración>` recorta al video y `aresample`
  compensa el reloj del dispositivo.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import FfmpegError
from . import paths

FFMPEG_ENV = "RECSTUDIO_FFMPEG"
ENCODER_ORDER = ("h264_nvenc", "h264_amf", "h264_qsv", "libx264")

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_RE = re.compile(r"Video:\s*([\w-]+).*?(\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_AUDIO_RE = re.compile(r"Audio:\s*([\w-]+)")
_AUDIO_HZ_RE = re.compile(r",\s*(\d{4,6})\s*Hz")


def ffmpeg_exe() -> str:
    override = os.environ.get(FFMPEG_ENV)
    if override and Path(override).is_file():
        return override
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def ffmpeg_version(exe: str | None = None) -> str:
    exe = exe or ffmpeg_exe()
    proc = subprocess.run([exe, "-version"], capture_output=True, text=True)
    return proc.stdout.splitlines()[0] if proc.stdout else "?"


def probe_encoders(*, force: bool = False) -> dict[str, bool]:
    """Prueba real de encode por encoder; cachea el resultado por binario."""
    exe = ffmpeg_exe()
    cache_path = paths.cache_dir() / "encoders.json"
    if not force and cache_path.is_file():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if data.get("ffmpeg") == exe and all(k in data["encoders"] for k in ENCODER_ORDER):
                return {k: bool(v) for k, v in data["encoders"].items()}
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    available: dict[str, bool] = {}
    for name in ENCODER_ORDER:
        proc = subprocess.run(
            [
                exe, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=0.5",
                "-c:v", name, "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
        )
        available[name] = proc.returncode == 0

    cache_path.write_text(
        json.dumps({"ffmpeg": exe, "version": ffmpeg_version(exe), "checked": time.time(),
                    "encoders": available}, indent=2),
        encoding="utf-8",
    )
    return available


def pick_encoder(preferred: str | None = None, available: dict[str, bool] | None = None) -> str:
    available = available or probe_encoders()
    if preferred:
        if preferred not in available:
            raise FfmpegError(f"encoder desconocido: {preferred!r} (opciones: {ENCODER_ORDER})")
        if not available[preferred]:
            raise FfmpegError(f"el encoder {preferred} no está disponible en este equipo")
        return preferred
    for name in ENCODER_ORDER:
        if available.get(name):
            return name
    raise FfmpegError("no hay ningún encoder H.264 disponible")


def encoder_args(encoder: str, *, cq: int, crf: int) -> list[str]:
    if encoder == "h264_nvenc":
        return ["-preset", "p5", "-rc", "vbr", "-cq", str(cq), "-b:v", "0"]
    if encoder == "h264_amf":
        return ["-quality", "balanced", "-rc", "cqp", "-qp_i", str(cq), "-qp_p", str(cq + 2)]
    if encoder == "h264_qsv":
        return ["-preset", "veryfast", "-global_quality", str(cq), "-look_ahead", "0"]
    if encoder == "libx264":
        return ["-preset", "veryfast", "-crf", str(crf)]
    raise FfmpegError(f"encoder desconocido: {encoder!r}")


# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration_s: float
    width: int
    height: int
    fps: float
    video_codec: str | None
    audio_codec: str | None
    audio_rate: int | None
    size_bytes: int

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None


def probe_media(path: str | Path, exe: str | None = None) -> MediaInfo:
    """Lee duración/streams con `ffmpeg -i` (imageio-ffmpeg no trae ffprobe)."""
    exe = exe or ffmpeg_exe()
    path = Path(path)
    proc = subprocess.run([exe, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    text = proc.stderr
    if not path.exists():
        raise FfmpegError(f"no existe el archivo: {path}")

    duration = 0.0
    match = _DURATION_RE.search(text)
    if match:
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    video_codec = width = height = None
    fps = 0.0
    audio_codec = None
    audio_rate = None
    for line in text.splitlines():
        if "Video:" in line and video_codec is None:
            vmatch = _VIDEO_RE.search(line)
            if vmatch:
                video_codec, width, height = vmatch.group(1), int(vmatch.group(2)), int(vmatch.group(3))
            fmatch = _FPS_RE.search(line)
            if fmatch:
                fps = float(fmatch.group(1))
        elif "Audio:" in line and audio_codec is None:
            amatch = _AUDIO_RE.search(line)
            if amatch:
                audio_codec = amatch.group(1)
            hmatch = _AUDIO_HZ_RE.search(line)
            if hmatch:
                audio_rate = int(hmatch.group(1))

    return MediaInfo(
        path=path,
        duration_s=duration,
        width=width or 0,
        height=height or 0,
        fps=fps,
        video_codec=video_codec,
        audio_codec=audio_codec,
        audio_rate=audio_rate,
        size_bytes=path.stat().st_size,
    )


# ---------------------------------------------------------------------------

def capture_cmd(
    exe: str,
    *,
    encoder: str,
    size: tuple[int, int],
    fps: int,
    pixel_format: str,
    out_path: str | Path,
    cq: int,
    crf: int,
    max_height: int | None = None,
) -> list[str]:
    """Comando de captura: rawvideo por stdin → MKV (resiliente a crashes)."""
    width, height = size
    args = [
        exe, "-hide_banner", "-loglevel", "warning", "-y",
        "-thread_queue_size", "512",
        "-f", "rawvideo", "-pixel_format", pixel_format,
        "-video_size", f"{width}x{height}", "-framerate", str(fps),
        "-i", "-",
        "-an",
        "-c:v", encoder, *encoder_args(encoder, cq=cq, crf=crf),
        "-pix_fmt", "yuv420p",
    ]
    if max_height and height > max_height:
        args += ["-vf", f"scale=-2:{max_height}"]
    args += ["-flush_packets", "1", "-f", "matroska", str(out_path)]
    return args


@dataclass(frozen=True)
class MuxTrack:
    """Pista de audio para el mux con su desfase respecto al primer frame de video.

    `offset_s` > 0: el audio arrancó después (se retrasa con `-itsoffset`).
    `offset_s` < 0: arrancó antes (se recorta con `atrim`).
    """

    path: Path
    offset_s: float = 0.0


def mux_cmd(
    exe: str,
    *,
    video: str | Path,
    tracks: list[MuxTrack],
    out_path: str | Path,
    duration_s: float,
    mix: bool = True,
) -> list[str]:
    """Mux final: video copiado + audio AAC 48 kHz, con desfases compensados.

    El audio pasa siempre por `aresample=async=1000`, que rellena huecos de timestamp (pausas
    largas, callbacks perdidos) y remuestrea a 48 kHz. OJO: `async` **no** corrige la deriva
    continua del reloj del dispositivo (F1: ~90 ppm ⇒ ~53 ms/10 min), porque los timestamps de
    cada archivo son internamente coherentes; esa corrección (ratio con `atempo`/`aresample`)
    llega en F1.1 — ver docs/09.
    """
    args = [exe, "-hide_banner", "-loglevel", "warning", "-y", "-i", str(video)]
    chains: list[str] = []
    labels: list[str] = []

    for i, track in enumerate(tracks, start=1):
        if track.offset_s > 0.0005:
            args += ["-itsoffset", f"{track.offset_s:.3f}"]
        args += ["-i", str(track.path)]
        if track.offset_s < -0.0005:
            chains.append(
                f"[{i}:a]atrim=start={abs(track.offset_s):.3f},asetpts=PTS-STARTPTS[t{i}]"
            )
            labels.append(f"[t{i}]")
        else:
            labels.append(f"[{i}:a]")

    if not tracks:
        args += ["-map", "0:v", "-an"]
    else:
        if len(tracks) == 1 or not mix:  # una sola pista, o modo "separadas" (entra la primera)
            source = labels[0]
        else:
            chains.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest[amixout]")
            source = "[amixout]"
        chains.append(f"{source}aresample=async=1000[aout]")
        args += ["-map", "0:v", "-map", "[aout]"]

    if chains:
        args += ["-filter_complex", ";".join(chains)]

    args += ["-c:v", "copy"]
    if tracks:
        args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    args += ["-t", f"{max(duration_s, 0.05):.3f}", "-movflags", "+faststart", str(out_path)]
    return args


def make_thumbnail(exe: str, video: str | Path, out_jpg: str | Path, *, at_s: float = 1.0) -> bool:
    """Miniatura JPEG del segundo `at_s` (best-effort)."""
    proc = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at_s:.2f}", "-i", str(video),
         "-frames:v", "1", "-vf", "scale=-2:180", "-q:v", "5", str(out_jpg)],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and Path(out_jpg).exists()


def run_capture_process(cmd: list[str], stderr_path: str | Path) -> subprocess.Popen[bytes]:
    """Lanza el proceso de captura con stdin por pipe y stderr a un archivo."""
    stderr_file = open(stderr_path, "wb")
    try:
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr_file)
    except Exception as exc:
        stderr_file.close()
        raise FfmpegError(f"no se pudo lanzar ffmpeg: {exc}") from exc


def stderr_tail(path: str | Path, *, lines: int = 30) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])
