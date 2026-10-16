"""Tests de los arreglos: geometría de regiones, cursor propio y corrección de deriva."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from recstudio.core.regions import clamp_region, move_region, resize_region
from recstudio.platform import ffmpeg as F
from recstudio.platform.cursor import CursorSnapshot, draw_cursor

BOUNDS = (1920, 1080)


# -- regiones -----------------------------------------------------------------

def test_clamp_dentro_del_monitor() -> None:
    assert clamp_region((-10, -10, 5000, 5000), BOUNDS) == (0, 0, 1920, 1080)
    assert clamp_region((1900, 1000, 320, 240), BOUNDS) == (1600, 840, 320, 240)


def test_clamp_tamano_minimo() -> None:
    x, y, w, h = clamp_region((100, 100, 4, 4), BOUNDS)
    assert w >= 64 and h >= 64


def test_move_respeta_limites() -> None:
    region = (100, 100, 800, 600)
    assert move_region(region, 50, -30, BOUNDS) == (150, 70, 800, 600)
    x, y, w, h = move_region(region, 5000, 5000, BOUNDS)
    assert (x, y, w, h) == (1120, 480, 800, 600)  # pegado a la esquina


def test_resize_esquinas() -> None:
    region = (100, 100, 400, 300)
    # esquina inferior derecha: crece
    assert resize_region(region, "br", 100, 50, BOUNDS) == (100, 100, 500, 350)
    # esquina superior izquierda: la esquina opuesta no se mueve
    assert resize_region(region, "tl", 40, 20, BOUNDS) == (140, 120, 360, 280)
    # esquina superior derecha
    assert resize_region(region, "tr", 60, 30, BOUNDS) == (100, 130, 460, 270)


def test_resize_minimo_y_limites() -> None:
    region = (100, 100, 400, 300)
    x, y, w, h = resize_region(region, "br", -10_000, -10_000, BOUNDS)
    assert w >= 32 and h >= 32
    x, y, w, h = resize_region(region, "br", 10_000, 10_000, BOUNDS)
    assert x + w <= 1920 and y + h <= 1080


# -- cursor propio ------------------------------------------------------------

def _solid_cursor(size: int = 4, value: int = 200) -> CursorSnapshot:
    pixels = np.zeros((size, size, 4), np.uint8)
    pixels[:, :, :3] = value
    pixels[:, :, 3] = 255
    return CursorSnapshot(x=10, y=20, hx=1, hy=1, pixels=pixels)


def test_cursor_se_compone_en_el_hotspot() -> None:
    frame = np.zeros((100, 100, 4), np.uint8)
    out = draw_cursor(frame, _solid_cursor())
    assert out is frame  # in place cuando es escribible
    assert tuple(out[19, 9, :3]) == (200, 200, 200)  # hotspot (10,20) - (1,1)
    assert tuple(out[22, 12, :3]) == (200, 200, 200)
    assert tuple(out[18, 8, :3]) == (0, 0, 0)  # fuera del parche


def test_cursor_alfa_parcial() -> None:
    frame = np.zeros((50, 50, 4), np.uint8)
    snap = _solid_cursor()
    snap.pixels[:, :, 3] = 128
    out = draw_cursor(frame, snap)
    assert 95 <= out[19, 9, 0] <= 105  # ~200 * 128/255


def test_cursor_recorta_en_los_bordes() -> None:
    frame = np.zeros((30, 30, 4), np.uint8)
    # cursor en (0,0) con hotspot (2,2): el parche de 4×4 empieza en (-2,-2) ⇒ se ven 2×2 px
    snap = CursorSnapshot(x=0, y=0, hx=2, hy=2, pixels=_solid_cursor().pixels)
    out = draw_cursor(frame, snap)
    assert out[0, 0, 0] == 200
    assert out[1, 1, 0] == 200
    assert out[2, 2, 0] == 0  # fuera del parche


def test_cursor_fuera_del_frame_no_hace_nada() -> None:
    frame = np.zeros((30, 30, 4), np.uint8)
    snap = CursorSnapshot(x=500, y=500, hx=0, hy=0, pixels=_solid_cursor().pixels)
    assert not draw_cursor(frame, snap).any()


def test_cursor_copia_si_el_frame_no_es_escribible() -> None:
    frame = np.zeros((50, 50, 4), np.uint8)
    frame.setflags(write=False)  # como los frames de mss (frombuffer)
    out = draw_cursor(frame, _solid_cursor())
    assert out is not frame
    assert out[19, 9, 0] == 200
    assert frame[19, 9, 0] == 0  # el original no se toca


def test_cursor_original_intacto_con_on_copy() -> None:
    frame = np.zeros((50, 50, 4), np.uint8)
    out = draw_cursor(frame, _solid_cursor(), on_copy=True)
    assert out is not frame
    assert frame[19, 9, 0] == 0


# -- deriva en el mux ---------------------------------------------------------

def test_mux_corrige_deriva_con_asetrate() -> None:
    cmd = F.mux_cmd(
        "ff",
        video="v.mkv",
        tracks=[F.MuxTrack(Path("a.wav"), 0.0, rate=44100, drift_ppm=90.0)],
        out_path="o.mp4",
        duration_s=10.0,
    )
    chain = cmd[cmd.index("-filter_complex") + 1]
    assert "asetrate=44104" in chain  # 44100 * (1 + 90e-6)
    assert "aresample=44100" in chain
    assert "aresample=async=1000" in chain


def test_mux_ignora_deriva_insignificante() -> None:
    cmd = F.mux_cmd(
        "ff",
        video="v.mkv",
        tracks=[F.MuxTrack(Path("a.wav"), 0.0, rate=44100, drift_ppm=2.0)],
        out_path="o.mp4",
        duration_s=10.0,
    )
    chain = cmd[cmd.index("-filter_complex") + 1]
    assert "asetrate" not in chain


def test_mux_deriva_y_offset_negativo_conviven() -> None:
    cmd = F.mux_cmd(
        "ff",
        video="v.mkv",
        tracks=[F.MuxTrack(Path("a.wav"), -0.2, rate=48000, drift_ppm=-120.0)],
        out_path="o.mp4",
        duration_s=5.0,
    )
    chain = cmd[cmd.index("-filter_complex") + 1]
    assert "atrim=start=0.200" in chain
    assert "asetrate=47994" in chain  # 48000 * (1 - 120e-6)
