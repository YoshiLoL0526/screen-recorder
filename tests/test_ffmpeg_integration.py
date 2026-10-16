"""Integración real con FFmpeg (usa el binario del wheel de imageio-ffmpeg).

Genera medios con `lavfi`, los sonda y ejecuta el mux completo (video + WAV a 44.1 kHz con
offset), que es exactamente el paso final del grabador.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from recstudio.platform import ffmpeg as F


@pytest.fixture(scope="module")
def exe() -> str:
    try:
        return F.ffmpeg_exe()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")


def make_video(exe: str, path: Path, *, seconds: float = 2.0, size: str = "320x240", fps: int = 30) -> None:
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", str(path)],
        check=True, capture_output=True,
    )


def make_wav(exe: str, path: Path, *, seconds: float = 2.0, rate: int = 44100) -> None:
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-ar", str(rate), "-ac", "2", str(path)],
        check=True, capture_output=True,
    )


def test_probe_media(exe: str, tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    make_video(exe, video, seconds=1.0)
    info = F.probe_media(video, exe)
    assert info.video_codec == "h264"
    assert (info.width, info.height) == (320, 240)
    assert info.fps == pytest.approx(30, abs=0.1)
    assert 0.9 <= info.duration_s <= 1.3
    assert not info.has_audio
    assert info.size_bytes > 0


def test_mux_con_offset_y_remuestreo(exe: str, tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    wav = tmp_path / "a.wav"
    out = tmp_path / "o.mp4"
    make_video(exe, video, seconds=2.0)
    make_wav(exe, wav, seconds=2.0, rate=44100)
    duration = F.probe_media(video, exe).duration_s

    cmd = F.mux_cmd(
        exe, video=video, tracks=[F.MuxTrack(wav, 0.200)], out_path=out, duration_s=duration
    )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-800:]

    final = F.probe_media(out, exe)
    assert final.has_audio
    assert final.audio_rate == 48000  # el mux fuerza AAC 48 kHz
    assert abs(final.duration_s - duration) < 0.35


def test_mux_mezcla_dos_pistas(exe: str, tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    wav1 = tmp_path / "sys.wav"
    wav2 = tmp_path / "mic.wav"
    out = tmp_path / "o.mp4"
    make_video(exe, video, seconds=1.5)
    make_wav(exe, wav1, seconds=1.5, rate=44100)
    make_wav(exe, wav2, seconds=1.5, rate=48000)
    duration = F.probe_media(video, exe).duration_s

    cmd = F.mux_cmd(
        exe, video=video,
        tracks=[F.MuxTrack(wav1, 0.15), F.MuxTrack(wav2, -0.1)],
        out_path=out, duration_s=duration,
    )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-800:]

    final = F.probe_media(out, exe)
    assert final.has_audio
    assert abs(final.duration_s - duration) < 0.35


def test_miniatura(exe: str, tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    make_video(exe, video, seconds=1.5)
    thumb = tmp_path / "t.jpg"
    assert F.make_thumbnail(exe, video, thumb, at_s=0.2)
    assert thumb.stat().st_size > 0
