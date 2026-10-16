"""Construcción de comandos de FFmpeg (puro, sin ejecutar nada)."""

from __future__ import annotations

from pathlib import Path

import pytest

from recstudio.core.errors import FfmpegError
from recstudio.platform import ffmpeg as F


def test_capture_cmd_bgra_y_sin_escala() -> None:
    cmd = F.capture_cmd(
        "ff", encoder="h264_nvenc", size=(1920, 1080), fps=60, pixel_format="bgra",
        out_path="o.mkv", cq=21, crf=20, max_height=1080,
    )
    assert cmd[0] == "ff"
    assert cmd[cmd.index("-pixel_format") + 1] == "bgra"
    assert cmd[cmd.index("-video_size") + 1] == "1920x1080"
    assert cmd[cmd.index("-framerate") + 1] == "60"
    assert "-vf" not in cmd
    assert cmd[cmd.index("-c:v") + 1] == "h264_nvenc"
    assert cmd[-2:] == ["matroska", "o.mkv"]


def test_capture_cmd_wgc_bgr24_y_escala() -> None:
    cmd = F.capture_cmd(
        "ff", encoder="libx264", size=(2560, 1440), fps=30, pixel_format="bgr24",
        out_path="o.mkv", cq=24, crf=21, max_height=1080,
    )
    assert cmd[cmd.index("-pixel_format") + 1] == "bgr24"
    assert cmd[cmd.index("-vf") + 1] == "scale=-2:1080"
    assert "libx264" in cmd and "-crf" in cmd


def test_encoder_args_por_familia() -> None:
    assert "-cq" in F.encoder_args("h264_nvenc", cq=21, crf=20)
    assert "-crf" in F.encoder_args("libx264", cq=21, crf=20)
    assert "-qp_i" in F.encoder_args("h264_amf", cq=21, crf=20)
    assert "-global_quality" in F.encoder_args("h264_qsv", cq=21, crf=20)
    with pytest.raises(FfmpegError):
        F.encoder_args("h265_magic", cq=1, crf=1)


def test_mux_offset_positivo_usa_itsoffset() -> None:
    cmd = F.mux_cmd(
        "ff", video="v.mkv", tracks=[F.MuxTrack(Path("a.wav"), 0.250)], out_path="o.mp4", duration_s=10.0
    )
    itso = cmd.index("-itsoffset")
    assert cmd[itso + 1] == "0.250"
    assert cmd.index("a.wav") > itso  # el offset precede a su entrada
    chain = cmd[cmd.index("-filter_complex") + 1]
    assert "aresample=async=1000" in chain and "[aout]" in chain  # anti-deriva del reloj
    assert cmd[cmd.index("-t") + 1] == "10.000"
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"


def test_mux_offset_negativo_usa_atrim() -> None:
    cmd = F.mux_cmd(
        "ff", video="v.mkv", tracks=[F.MuxTrack(Path("a.wav"), -0.100)], out_path="o.mp4", duration_s=5.0
    )
    assert "-itsoffset" not in cmd
    chain = cmd[cmd.index("-filter_complex") + 1]
    assert "atrim=start=0.100" in chain
    assert "aresample=async=1000" in chain
    assert cmd[cmd.index("-map") + 1] == "0:v"


def test_mux_mezcla_dos_pistas() -> None:
    cmd = F.mux_cmd(
        "ff", video="v.mkv",
        tracks=[F.MuxTrack(Path("sys.wav"), 0.1), F.MuxTrack(Path("mic.wav"), -0.05)],
        out_path="o.mp4", duration_s=12.5,
    )
    chain = cmd[cmd.index("-filter_complex") + 1]
    assert "amix=inputs=2" in chain
    assert "atrim=start=0.050" in chain
    assert "aresample=async=1000" in chain
    assert chain.endswith("[aout]")
    assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == "48000"


def test_mux_sin_audio() -> None:
    cmd = F.mux_cmd("ff", video="v.mkv", tracks=[], out_path="o.mp4", duration_s=3.0)
    assert "-an" in cmd
    assert "-filter_complex" not in cmd
    assert "-t" in cmd
