"""Tests de los riesgos menores de la revisión: carpeta de salida, recuperación (guardia y
limpieza), cursores monocromos, FFmpeg sin consola y orden de monitores."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from recstudio import state
from recstudio.platform import capture, paths, screens
from recstudio.platform import ffmpeg as F
from recstudio.platform.cursor import CursorSnapshot, draw_cursor, shape_from_color, shape_from_mono
from recstudio.services import library
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


# -- 7) Windows bloquea la escritura en ~/Videos (Acceso controlado a carpetas) -------------

def _bloquear_escritura(monkeypatch: pytest.MonkeyPatch, bloqueada: Path) -> None:
    """Simula el bloqueo de Defender: `mkdir` falla con WinError 2 por debajo de esa ruta.

    Es lo que hace el minifiltro del «Acceso controlado a carpetas»: en vez de «acceso denegado»
    devuelve «no se puede encontrar el archivo especificado», y `pathlib` con `parents=True` lo
    interpreta como un padre que falta y reintenta en vano.
    """
    real = Path.mkdir
    prefix = str(bloqueada).lower()

    def fake(self: Path, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if str(self).lower().startswith(prefix):
            raise FileNotFoundError(2, "El sistema no puede encontrar el archivo especificado")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fake)


def test_ensure_dir_no_lanza(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _bloquear_escritura(monkeypatch, tmp_path / "protegida")
    assert paths.ensure_dir(tmp_path / "protegida" / "sub") is False
    assert paths.ensure_dir(tmp_path / "libre") is True
    assert (tmp_path / "libre").is_dir()


def test_videos_dir_usa_la_carpeta_de_reserva(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _bloquear_escritura(monkeypatch, Path.home() / "Videos")
    paths.take_blocked_videos_dir()  # descartar avisos de otros tests

    out = paths.videos_dir()
    assert out == tmp_path / paths.APP_NAME / "videos" and out.is_dir()
    assert paths.take_blocked_videos_dir() == (Path.home() / "Videos" / paths.APP_NAME, out)
    assert paths.take_blocked_videos_dir() is None  # el aviso se da una sola vez


def test_ajustes_con_carpeta_bloqueada_no_revientan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Antes, `Settings.load()` propagaba el error y la app moría al importar `state`."""
    from recstudio.services.settings import Settings

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    _bloquear_escritura(monkeypatch, Path.home() / "Videos")

    loaded = Settings.load()
    assert loaded.out_dir == str(tmp_path / paths.APP_NAME / "videos")


def test_arranque_avisa_de_la_carpeta_bloqueada(controller, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:  # noqa: ANN001
    from recstudio.services.settings import Settings

    ctl, _ = controller
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _bloquear_escritura(monkeypatch, Path.home() / "Videos")
    paths.take_blocked_videos_dir()
    ctl.settings = Settings().normalized()  # resuelve la carpeta por defecto (bloqueada)
    state.toasts.set(())

    ctl._apply_settings()

    assert state.out_dir() == str(tmp_path / paths.APP_NAME / "videos")
    assert state.toasts() and state.toasts()[-1].kind == "warning"
    assert "bloqueó" in state.toasts()[-1].text
    ctl._apply_settings()  # el aviso no se repite
    assert len(state.toasts()) == 1


def test_grabar_avisa_si_la_carpeta_esta_bloqueada(controller, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:  # noqa: ANN001
    ctl, _ = controller
    bloqueada = tmp_path / "protegida"
    bloqueada.mkdir()
    _bloquear_escritura(monkeypatch, bloqueada)
    state.out_dir.set(str(bloqueada))
    state.source_kind.set("monitor")
    state.monitor_index.set(1)
    state.fps_target.set(60)
    state.preset.set("media")
    state.rec_state.set("idle")
    state.toasts.set(())

    assert ctl._build_config() is None
    assert state.rec_state() == "error"
    assert state.toasts()[-1].kind == "danger"
    assert "no se puede escribir" in state.toasts()[-1].text


def test_la_sesion_no_escribe_fuera_de_su_carpeta(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Carpeta de salida imposible: error de dominio (no un `OSError` crudo en un hilo)."""
    from recstudio.core.config import RecordingConfig
    from recstudio.core.errors import DiskSpaceError

    bloqueada = tmp_path / "protegida"
    bloqueada.mkdir()
    _bloquear_escritura(monkeypatch, bloqueada)

    with pytest.raises(DiskSpaceError):
        rec_mod.RecordingSession(RecordingConfig(), out_dir=bloqueada, temp_dir=tmp_path / "t")


def test_la_biblioteca_ignora_una_carpeta_sin_configurar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "suelto.mp4").write_bytes(b"no es un mp4")
    assert library.list_recordings("") == []


def test_el_cierre_cancela_el_guardado_pendiente(controller, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """El debounce no debe escribir los ajustes después de cerrar (ni en el `settings.json` real)."""
    ctl, _ = controller
    calls: list[int] = []

    class EditorStub:
        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(ctl, "editor", EditorStub())
    monkeypatch.setattr(ctl, "_save_settings", lambda: calls.append(1))
    ctl._schedule_save()
    assert ctl._save_timer is not None

    ctl.shutdown()
    assert ctl._save_timer is None
    time.sleep(1.2)  # el Timer ya estaría disparado
    assert calls == [1]  # solo el guardado explícito del cierre


def test_importar_el_estado_no_depende_de_crear_videos(tmp_path: Path) -> None:
    """Regresión del traceback original: importar `state` tocaba el disco y tumbaba la app."""
    perfil = tmp_path / "perfil"
    perfil.mkdir()
    (perfil / "Videos").write_text("un fichero con ese nombre impide crear la carpeta")
    env = {**os.environ, "USERPROFILE": str(perfil), "HOME": str(perfil),
           "LOCALAPPDATA": str(tmp_path / "local")}

    proc = subprocess.run(
        [sys.executable, "-c", "import recstudio.state as s; print(s.out_dir())"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ""
