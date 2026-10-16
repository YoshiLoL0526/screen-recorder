"""Tests de los riesgos menores de la revisión: carpeta de salida, recuperación (guardia y
limpieza), cursores monocromos, FFmpeg sin consola y orden de monitores."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from recstudio import state
from recstudio.platform import capture, screens
from recstudio.platform import ffmpeg as F
from recstudio.platform.cursor import CursorSnapshot, draw_cursor, shape_from_color, shape_from_mono
from recstudio.services import recording as rec_mod


def _wait(predicate, timeout: float = 3.0) -> None:  # noqa: ANN001
    deadline = time.perf_counter() + timeout
    while not predicate():
        assert time.perf_counter() < deadline, "timeout esperando la condición"
        time.sleep(0.01)


@pytest.fixture
def controller(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    from recstudio.services.settings import Settings
    from recstudio.ui import controller as controller_mod

    state.recovering.set(())
    state.confirm.set(None)
    previous_out_dir = state.out_dir()
    ctl = controller_mod.Controller(Settings())
    monkeypatch.setattr(ctl, "refresh_library", lambda: None)
    yield ctl, controller_mod
    ctl._closing = True
    state.out_dir.set(previous_out_dir)
    state.confirm.set(None)


# -- 1) el botón de carpeta abre la carpeta configurada ------------------------------------

def test_abrir_carpeta_usa_la_configurada(controller, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    ctl, controller_mod = controller
    opened: list[Path] = []
    monkeypatch.setattr(controller_mod.library, "open_file", opened.append)
    state.out_dir.set(str(tmp_path))

    ctl.open_out_dir()
    _wait(lambda: opened)
    assert opened == [tmp_path]


def test_abrir_carpeta_inexistente_avisa_sin_abrir(controller, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    ctl, controller_mod = controller
    opened: list[Path] = []
    monkeypatch.setattr(controller_mod.library, "open_file", opened.append)
    state.out_dir.set(str(tmp_path / "no-existe"))
    state.toasts.set(())

    ctl.open_out_dir()
    _wait(lambda: state.toasts())
    assert not opened
    assert state.toasts()[-1].kind == "warning"


# -- 2) recuperar: con confirmación y una sola vez por carpeta -----------------------------

def test_recuperar_pide_confirmacion(controller, tmp_path: Path) -> None:  # noqa: ANN001
    ctl, _ = controller
    folder = tmp_path / "rec_2026-01-01_10-00-00"
    ctl.ask_recover(folder)
    confirm = state.confirm()
    assert confirm is not None and confirm.action == "recover" and confirm.target == str(folder)


def test_recuperar_dos_veces_lanza_un_solo_mux(controller, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    ctl, controller_mod = controller
    calls: list[Path] = []
    release = threading.Event()

    class Result:
        mp4 = tmp_path / "x-recuperada.mp4"
        duration_s = 1.0

    def fake_recover(folder: Path, *, out_dir: str) -> Result:
        calls.append(folder)
        release.wait(2)
        return Result()

    monkeypatch.setattr(controller_mod.rec_mod, "recover_session", fake_recover)
    folder = tmp_path / "rec_2026-01-01_10-00-00"

    ctl.recover(folder)
    ctl.recover(folder)  # segundo clic mientras la primera sigue en marcha
    _wait(lambda: calls)
    assert state.recovering() == (folder.name,)
    release.set()
    _wait(lambda: state.recovering() == ())
    assert len(calls) == 1


# -- 3) tras recuperar, el temporal se borra (FFmpeg real) ---------------------------------

@pytest.fixture(scope="module")
def exe() -> str:
    try:
        return F.ffmpeg_exe()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")


def _crashed_session(exe: str, folder: Path) -> None:
    folder.mkdir(parents=True)
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=320x240:rate=30:duration=2", "-c:v", "libx264", "-preset", "ultrafast",
         "-f", "matroska", str(folder / "video.mkv")],
        check=True, capture_output=True,
    )
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=2", "-ar", "48000", "-ac", "2",
         str(folder / "audio_system.wav")],
        check=True, capture_output=True,
    )
    (folder / "session.json").write_text(json.dumps({
        "status": "recording", "offsets": {"audio_system.wav": -0.2}, "config": {"fps": 30},
    }), encoding="utf-8")


def test_recuperar_borra_el_temporal(exe: str, tmp_path: Path) -> None:
    folder = tmp_path / "rec_2026-01-01_10-00-00"
    _crashed_session(exe, folder)

    result = rec_mod.recover_session(folder, out_dir=tmp_path / "out", base_name="prueba")
    assert result.mp4.exists() and result.mp4.name == "prueba-recuperada.mp4"
    assert not folder.exists()


def test_recuperar_fallida_conserva_el_temporal(tmp_path: Path) -> None:
    folder = tmp_path / "rec_2026-01-01_10-00-00"
    folder.mkdir()
    (folder / "video.mkv").write_bytes(b"no es un mkv" * 10)

    with pytest.raises(rec_mod.FfmpegError):
        rec_mod.recover_session(folder, out_dir=tmp_path / "out")
    assert (folder / "video.mkv").exists()  # sigue ahí para reintentar


# -- 4) cursores monocromos (I-beam, cruz) y sin canal alfa --------------------------------

def test_cursor_monocromo_and_xor() -> None:
    # 1×4: negro, blanco, transparente, invierte  (AND arriba, XOR abajo)
    and_mask = np.array([[False, False, True, True]])
    xor_mask = np.array([[False, True, False, True]])
    pixels, invert = shape_from_mono(np.vstack([and_mask, xor_mask]))

    assert pixels.shape == (1, 4, 4)
    assert pixels[0, :, 3].tolist() == [255, 255, 0, 0]
    assert pixels[0, 0, :3].tolist() == [0, 0, 0]
    assert pixels[0, 1, :3].tolist() == [255, 255, 255]
    assert invert is not None and invert[0].tolist() == [False, False, False, True]


def test_draw_cursor_invierte_el_fondo() -> None:
    frame = np.full((4, 4, 4), 40, np.uint8)
    pixels = np.zeros((2, 2, 4), np.uint8)  # todo transparente salvo el XOR
    invert = np.array([[True, False], [False, False]])
    snap = CursorSnapshot(x=1, y=1, hx=0, hy=0, pixels=pixels, invert=invert)

    out = draw_cursor(frame, snap, on_copy=True)
    assert out[1, 1, :3].tolist() == [215, 215, 215]  # 255 - 40
    assert out[1, 2, :3].tolist() == [40, 40, 40]
    assert frame[1, 1, 0] == 40  # on_copy: el original intacto


def test_cursor_solo_xor_no_se_descarta() -> None:
    """Antes, un parche sin ningún píxel opaco salía antes de tiempo: el I-beam no se veía."""
    frame = np.zeros((3, 3, 4), np.uint8)
    snap = CursorSnapshot(1, 1, 0, 0, np.zeros((1, 1, 4), np.uint8), np.array([[True]]))
    assert draw_cursor(frame, snap)[1, 1, :3].tolist() == [255, 255, 255]


def test_cursor_color_sin_alfa_usa_la_mascara() -> None:
    color = np.zeros((1, 3, 4), np.uint8)
    color[0, 0, :3] = 200  # opaco
    color[0, 2, :3] = 255  # transparente con color → XOR
    and_mask = np.array([[False, True, True]])
    pixels, invert = shape_from_color(color, and_mask)

    assert pixels[0, :, 3].tolist() == [255, 0, 0]
    assert invert is not None and invert[0].tolist() == [False, False, True]


def test_cursor_color_con_alfa_se_usa_tal_cual() -> None:
    color = np.zeros((2, 2, 4), np.uint8)
    color[0, 0] = (10, 20, 30, 128)
    pixels, invert = shape_from_color(color, np.ones((2, 2), bool))
    assert pixels is color and invert is None


# -- 5) FFmpeg sin ventana de consola y salida en UTF-8 -----------------------------------

def test_run_sin_consola_y_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003, ANN202
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "ffmpeg version x", "")

    monkeypatch.setattr(F.subprocess, "run", fake_run)
    assert F.ffmpeg_version("ffmpeg") == "ffmpeg version x"
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"
    if sys.platform == "win32":
        assert seen["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_proceso_de_captura_sin_consola(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}
    monkeypatch.setattr(F.subprocess, "Popen", lambda cmd, **kwargs: seen.update(kwargs) or "proc")
    assert F.run_capture_process(["ffmpeg"], tmp_path / "ffmpeg.log") == "proc"
    if sys.platform == "win32":
        assert seen["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_probe_media_con_salida_no_ascii(exe: str, tmp_path: Path) -> None:
    video = tmp_path / "grabación_ñandú.mkv"
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=320x240:rate=30:duration=1", "-c:v", "libx264", "-preset", "ultrafast",
         str(video)],
        check=True, capture_output=True,
    )
    info = F.probe_media(video, exe)
    assert info.width == 320 and info.duration_s > 0.5


# -- 6) monitores: principal real y salida DXGI por coordenadas ----------------------------

def test_principal_segun_mss() -> None:
    assert screens._is_primary({"left": -1920, "top": 0, "is_primary": True})
    assert not screens._is_primary({"left": 0, "top": 0, "is_primary": False})


def test_principal_sin_marca_es_el_del_origen() -> None:
    assert screens._is_primary({"left": 0, "top": 0})
    assert not screens._is_primary({"left": 1920, "top": 0})


def test_salida_dxgi_por_coordenadas() -> None:
    # GPU 0: monitor a la derecha (1920,0) y luego el principal; GPU 1: uno a la izquierda
    outputs = [[(1920, 0), (0, 0)], [(-1280, 0)]]
    assert capture.match_dxgi_output(outputs, 0, 0) == (0, 1)
    assert capture.match_dxgi_output(outputs, -1280, 0) == (1, 0)
    assert capture.match_dxgi_output(outputs, 5000, 0) is None
