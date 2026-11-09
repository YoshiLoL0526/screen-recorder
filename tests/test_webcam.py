"""Webcam junto a la pantalla y PiP automático (docs/49).

No necesita hardware: la enumeración y DirectShow se prueban con salidas reales de FFmpeg
parseadas, y el grabador con un `Popen` falso. Los tests de integración generan MKV/MP4 con
FFmpeg real (también en CI) y el resto son puros.
"""

from __future__ import annotations

import io
import json
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from recstudio import state
from recstudio.core import elements as el
from recstudio.core import graph as G
from recstudio.core import timeline as tl
from recstudio.core.config import AudioConfig, CameraConfig, RecordingConfig
from recstudio.core.errors import CameraError, ConfigError
from recstudio.core.project import MediaAsset, Project
from recstudio.platform import camera as cam
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import library
from recstudio.services import media
from recstudio.services import recording as rec_mod
from recstudio.services.settings import Settings

LISTADO = (
    '[dshow @ 000001] "Integrated Camera" (video)\n'
    '[dshow @ 000001]   Alternative name "@device_pnp_\\\\?\\usb#vid_123&pid_456"\n'
    '[dshow @ 000001] "Microphone (Realtek(R) Audio)" (audio)\n'
    '[dshow @ 000001] "OBS Virtual Camera" (video)\n'
    '[dshow @ 000001]   Alternative name "@device_sw_{860BB310-5D01-11D0-BD3B-00A0C911CE86}"\n'
)


# -- configuración -------------------------------------------------------------

def test_camera_config_desactivada_no_exige_dispositivo() -> None:
    assert CameraConfig().validate() == CameraConfig()


def test_camera_config_valida_dispositivo_rangos_y_paridad() -> None:
    enabled = CameraConfig(enabled=True, device_name="Cam", width=1280, height=720, fps=30)
    assert enabled.validate() is enabled

    with pytest.raises(ConfigError, match="sin dispositivo"):
        CameraConfig(enabled=True).validate()
    with pytest.raises(ConfigError, match="par"):
        CameraConfig(enabled=True, device_name="Cam", width=1279).validate()
    with pytest.raises(ConfigError, match="fuera de rango"):
        CameraConfig(enabled=True, device_name="Cam", width=100).validate()
    with pytest.raises(ConfigError, match="fps de cámara"):
        CameraConfig(enabled=True, device_name="Cam", fps=1).validate()


def test_recording_config_valida_la_camara() -> None:
    config = RecordingConfig(camera=CameraConfig(enabled=True, device_name="Cam"))
    assert config.validate().camera.enabled
    with pytest.raises(ConfigError):
        RecordingConfig(camera=CameraConfig(enabled=True)).validate()


def test_settings_normaliza_la_camara(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    settings = Settings(
        camera_enabled=True, camera_device="  Cam  ", camera_width=321,
        camera_height=99_999, camera_fps=999,
    ).normalized()
    assert settings.camera_enabled and settings.camera_device == "Cam"
    assert settings.camera_width % 2 == 0 and 160 <= settings.camera_width <= 4096
    assert settings.camera_height == 4096
    assert settings.camera_fps == 120

    disabled = Settings(camera_enabled=True, camera_device="").normalized()
    assert not disabled.camera_enabled  # sin dispositivo no se queda encendida


# -- enumeración y comandos (puro) --------------------------------------------

def test_parse_camera_list_ignora_audio_y_conserva_el_id() -> None:
    devices = cam.parse_camera_list(LISTADO)
    assert [d.name for d in devices] == ["Integrated Camera", "OBS Virtual Camera"]
    assert devices[0].id.startswith("@device_pnp_")
    assert devices[1].id.startswith("@device_sw_")


def test_parse_camera_list_tolera_basura() -> None:
    assert cam.parse_camera_list("") == ()
    assert cam.parse_camera_list("no hay nada que parsear") == ()


def test_camera_cmd_pide_tamano_fps_y_sin_audio() -> None:
    cmd = cam.camera_cmd("ff", "Cámara con espacios", "cam.mkv", width=1280, height=720, fps=30)
    assert cmd[cmd.index("-f") + 1] == "dshow"
    assert cmd[cmd.index("-i") + 1] == "video=Cámara con espacios"
    assert cmd[cmd.index("-video_size") + 1] == "1280x720"
    assert cmd[cmd.index("-framerate") + 1] == "30"
    assert "-an" in cmd
    assert cmd[-2:] == ["matroska", "cam.mkv"]


def test_camera_cmd_sin_tamano_es_el_reintento_nativo() -> None:
    cmd = cam.camera_cmd("ff", "Cam", "cam.mkv", width=None, height=None, fps=None)
    assert "-video_size" not in cmd and "-framerate" not in cmd


def test_remux_y_snapshot() -> None:
    cmd = cam.remux_cmd("ff", "camera.mkv", "camera.mp4")
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-an" in cmd and cmd[-1] == "camera.mp4"
    transcode = cam.remux_cmd("ff", "camera.mkv", "camera.mp4", copy=False)
    assert transcode[transcode.index("-c:v") + 1] == "libx264"

    snap = cam.snapshot_cmd("ff", "Cam", width=320)
    assert snap[snap.index("-frames:v") + 1] == "1"
    assert snap[snap.index("-vcodec") + 1] == "mjpeg"
    assert snap[-1] == "pipe:1"


class _FakePopen:
    """`Popen` falso: crea el archivo de salida para que `start()` lo acepte."""

    def __init__(self, cmd, **kwargs) -> None:  # noqa: ANN001
        Path(cmd[-1]).write_bytes(b"matroska-falso" * 100)
        self.stdin = io.BytesIO()
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0


def test_camera_recorder_arranca_y_para_limpio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cam.ffmpeg_mod, "run_capture_process", lambda cmd, log: _FakePopen(cmd)
    )
    recorder = cam.CameraRecorder("Cam", tmp_path / "camera.mkv", exe="ff", start_timeout=1.0)
    recorder.start()
    assert recorder.running and recorder.started_at is not None
    track = recorder.stop()
    assert track is not None and track.path.exists() and track.failed is False
    assert not recorder.running


class _DeadPopen:
    def __init__(self, cmd, **kwargs) -> None:  # noqa: ANN001
        self.stdin = None
        self.returncode = 1

    def poll(self) -> int:
        return 1

    def wait(self, timeout: float | None = None) -> int:
        return 1

    def kill(self) -> None:
        pass


def test_camera_recorder_falla_con_mensaje_claro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cam.ffmpeg_mod, "run_capture_process", lambda cmd, log: _DeadPopen(cmd)
    )
    recorder = cam.CameraRecorder("Cam rota", tmp_path / "camera.mkv", exe="ff", start_timeout=0.3)
    with pytest.raises(CameraError, match="Cam rota"):
        recorder.start()


# -- sesión de grabación -------------------------------------------------------

class _FakeBackend:
    name = "fake"
    pixel_format = "bgra"

    def __init__(self) -> None:
        self.lost_source = False
        self.stopped = False

    def grab(self) -> np.ndarray:
        return np.zeros((16, 16, 4), np.uint8)

    def stop(self) -> None:
        self.stopped = True


class _FakeStdin:
    def __init__(self) -> None:
        self.closed = False

    def write(self, data: bytes) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    def __init__(self) -> None:
        self.stdin = _FakeStdin()

    def poll(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0


class _CamaraQueFalla:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        raise CameraError("dispositivo ocupado")


class _CamaraOk:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self.running = True
        self.stopped = False
        self.path = Path(kwargs.get("out_path", "camera.mkv"))

    def start(self) -> None:
        pass

    def stop(self) -> cam.CameraTrack:
        self.running = False
        self.stopped = True
        return cam.CameraTrack(self.path, "Cam")


def test_camara_arranca_y_se_para_con_la_sesion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = RecordingConfig(
        camera=CameraConfig(enabled=True, device_name="Cam", width=320, height=180),
    )
    session = rec_mod.RecordingSession(config, out_dir=tmp_path / "out", temp_dir=tmp_path / "tmp")
    monkeypatch.setattr(rec_mod.camera_mod, "CameraRecorder", _CamaraOk)
    session._start_camera()
    assert session.camera_active and session.stats().camera_active
    fake = session._camera
    session._release(join_timeout=0.1)
    assert fake is not None and fake.stopped
    assert session._camera is None and not session.camera_active


def test_si_la_camara_falla_la_pantalla_sigue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = RecordingConfig(
        fps=30, audio=AudioConfig(system=False),
        camera=CameraConfig(enabled=True, device_name="Cam", width=320, height=180, fps=30),
    )
    session = rec_mod.RecordingSession(config, out_dir=tmp_path / "out", temp_dir=tmp_path / "tmp")
    session._backend = _FakeBackend()  # type: ignore[assignment]
    session._backend_name = "fake"
    session._proc = _FakeProc()  # type: ignore[assignment]
    session._size = (16, 16)
    monkeypatch.setattr(rec_mod.camera_mod, "CameraRecorder", _CamaraQueFalla)

    session._start_camera()

    assert session._camera is None and not session.camera_active
    assert any("se graba solo la pantalla" in w for w in session._warnings)
    assert session.state is rec_mod.RecState.IDLE  # no se ha tocado la sesión


def test_pausa_deshabilitada_con_camara(tmp_path: Path) -> None:
    config = RecordingConfig(
        camera=CameraConfig(enabled=True, device_name="Cam"),
    )
    session = rec_mod.RecordingSession(config, out_dir=tmp_path / "out", temp_dir=tmp_path / "tmp")
    session._state = rec_mod.RecState.RECORDING
    assert session.pause() is False
    assert session.state is rec_mod.RecState.RECORDING
    assert any("pausa no está disponible" in w for w in session._warnings)


def test_pausa_normal_sigue_funcionando(tmp_path: Path) -> None:
    session = rec_mod.RecordingSession(
        RecordingConfig(), out_dir=tmp_path / "out", temp_dir=tmp_path / "tmp"
    )
    session._state = rec_mod.RecState.RECORDING
    assert session.pause() is True
    assert session.state is rec_mod.RecState.PAUSED
    session.resume()
    assert session.state is rec_mod.RecState.RECORDING


# -- archivos, sidecar, biblioteca y recuperación ------------------------------

def _mkv(path: Path, *, color: str = "blue", duration: float = 1.2) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"color=c={color}:size=160x90:rate=30:duration={duration}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-f", "matroska", str(path)],
        check=True, capture_output=True,
    )
    return path


def _mp4(path: Path, *, color: str = "blue", duration: float = 1.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"color=c={color}:size=160x90:rate=30:duration={duration}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def _session(tmp_path: Path, *, offset: float = 0.25) -> rec_mod.RecordingSession:
    config = RecordingConfig(
        fps=30, audio=AudioConfig(system=False),
        camera=CameraConfig(enabled=True, device_name="FakeCam", width=320, height=180, fps=30),
    )
    session = rec_mod.RecordingSession(config, out_dir=tmp_path / "out", temp_dir=tmp_path / "tmp")
    session._backend_name = "fake"
    session._encoder = "libx264"
    session._size = (160, 90)
    session._camera_device = "FakeCam"
    session._camera_offset = offset
    _mkv(session._mkv)
    _mkv(session._camera_temp, color="red")
    return session


def test_mux_guarda_camara_y_sidecar(tmp_path: Path) -> None:
    session = _session(tmp_path)
    result = session._mux([], {}, recover=False)

    assert result.camera is not None and result.camera.exists()
    assert result.camera.name.endswith("-camara.mp4")
    assert result.camera_offset_s == pytest.approx(0.25)
    info = F.probe_media(result.camera)
    assert info.video_codec and info.duration_s > 0.5

    data = json.loads(result.sidecar.read_text(encoding="utf-8"))
    assert data["camera"]["file"] == result.camera.name
    assert data["camera"]["device"] == "FakeCam"
    assert data["camera"]["offset_s"] == pytest.approx(0.25)
    assert data["camera"]["duration_s"] > 0.5
    assert data["camera"]["width"] == 160 and data["camera"]["height"] == 90


def test_recuperacion_conserva_la_camara(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rec_mod.paths, "temp_root", lambda: tmp_path)
    folder = tmp_path / "rec_2026-01-01_10-00-00"
    folder.mkdir()
    _mkv(folder / "video.mkv")
    _mkv(folder / "camera.mkv", color="red")
    (folder / "session.json").write_text(
        json.dumps({
            "status": "recording",
            "started_at": "2026-01-01 10:00:00",
            "backend": "fake",
            "encoder": "libx264",
            "offsets": {},
            "camera": {
                "enabled": True, "device": "FakeCam", "offset_s": 0.5,
                "file": "camera.mkv", "failed": False,
            },
            "config": {"fps": 30, "preset": "media"},
        }),
        encoding="utf-8",
    )

    assert folder in rec_mod.scan_recoverable()
    result = rec_mod.recover_session(folder, out_dir=tmp_path / "out")

    assert result.camera is not None and result.camera.exists()
    assert result.camera.name.endswith("-camara.mp4")
    assert result.camera_offset_s == pytest.approx(0.5)
    data = json.loads(result.sidecar.read_text(encoding="utf-8"))
    assert data["camera"]["file"] == result.camera.name
    assert data["camera"]["offset_s"] == pytest.approx(0.5)
    assert not folder.exists()  # el temporal (incluida camera.mkv) se limpió


def test_biblioteca_lee_y_borra_la_camara(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mp4 = tmp_path / "2026-01-01_10-00-00.mp4"
    mp4.write_bytes(b"mp4")
    camera = tmp_path / "2026-01-01_10-00-00-camara.mp4"
    camera.write_bytes(b"cam")
    mp4.with_suffix(".json").write_text(
        json.dumps({
            "duration_s": 5.0, "width": 160, "height": 90, "fps": 30,
            "camera": {
                "file": camera.name, "device": "FakeCam", "offset_s": 0.3,
                "duration_s": 5.2, "width": 160, "height": 90, "fps": 30,
            },
        }),
        encoding="utf-8",
    )

    item = library.list_recordings(tmp_path)[0]
    assert item.camera == camera and item.has_camera
    assert item.camera_duration_s == pytest.approx(5.2)
    assert item.camera_offset_s == pytest.approx(0.3)
    info = library.camera_info(mp4)
    assert info is not None and info.path == camera and info.offset_s == pytest.approx(0.3)

    deleted: list[Path] = []
    monkeypatch.setattr(library, "move_to_trash", lambda path: deleted.append(path) or True)
    removed = library.delete_recording_files(item)
    assert camera in removed and camera in deleted


def test_sidecar_sin_archivo_de_camara_no_revienta(tmp_path: Path) -> None:
    mp4 = tmp_path / "x.mp4"
    mp4.write_bytes(b"mp4")
    mp4.with_suffix(".json").write_text(
        json.dumps({"camera": {"file": "no-existe.mp4"}}), encoding="utf-8"
    )
    item = library.list_recordings(tmp_path)[0]
    assert item.camera is None and not item.has_camera


# -- PiP automático y export ---------------------------------------------------

def _project(tmp_path: Path) -> tuple[Project, object]:
    base = _mp4(tmp_path / "base.mp4", color="blue", duration=2.0)
    face = _mp4(tmp_path / "face.mp4", color="red", duration=2.0)
    project = Project(width=320, height=180, fps=30)
    project, _ = tl.insert_clip(project, media.probe(base))
    camera_asset = media.probe(face)
    project, element = el.add_camera_pip(project, camera_asset, 0.0, offset_s=0.4)
    project.validate()
    return project, element


def test_pip_automatico_abajo_a_la_derecha_y_sin_audio(tmp_path: Path) -> None:
    project, element = _project(tmp_path)
    assert element.kind == "video" and element.video_track == 0
    assert element.x > 0.7 and element.y > 0.6
    assert 0.1 < element.width <= 0.3 and 0.1 < element.height <= 0.3
    assert element.in_ == pytest.approx(0.4)
    assert el.audio_gain(element) == 0.0
    # El elemento está dentro del lienzo y no tapa el centro.
    assert element.x + element.width / 2 <= 1.0
    assert element.y + element.height / 2 <= 1.0


def test_pip_automatico_con_desfase_negativo_empieza_mas_tarde(tmp_path: Path) -> None:
    base = _mp4(tmp_path / "base.mp4", color="blue", duration=3.0)
    face = _mp4(tmp_path / "face.mp4", color="red", duration=2.0)
    project = Project(width=320, height=180, fps=30)
    project, _ = tl.insert_clip(project, media.probe(base))
    project, element = el.add_camera_pip(
        project, media.probe(face), 0.5, offset_s=-0.25
    )
    assert element.in_ == pytest.approx(0.0)
    assert element.start == pytest.approx(0.75)
    assert element.end - element.start <= 2.0


def test_export_real_con_pip_de_camara(tmp_path: Path) -> None:
    project, _element = _project(tmp_path)
    out = tmp_path / "out.mp4"
    spec = G.build(project, G.PRESETS[0], out, encoder="libx264", exe=F.ffmpeg_exe())
    result = subprocess.run(spec.args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-800:]
    assert out.is_file() and out.stat().st_size > 0
    assert F.probe_media(out).duration_s == pytest.approx(2.0, abs=0.2)

    # Frame a mitad del intervalo: la cara roja está compuesta abajo a la derecha.
    raw = subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", "1.0", "-i", str(out),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    frame = np.frombuffer(raw, np.uint8).reshape(180, 320, 3)
    cx, cy = int(0.82 * 320), int(0.78 * 180)
    patch = frame[cy - 6 : cy + 6, cx - 6 : cx + 6]
    assert patch[:, :, 0].mean() > 150  # rojo de la cámara
    assert patch[:, :, 2].mean() < 100


# -- UI (pilot) ----------------------------------------------------------------

def _wait(predicate, timeout: float = 5.0) -> None:  # noqa: ANN001
    deadline = time.perf_counter() + timeout
    while not predicate():
        assert time.perf_counter() < deadline, "timeout esperando la condición"
        time.sleep(0.05)


@pytest.fixture()
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    fake = (
        cam.CameraDevice("Cámara falsa", "@device_pnp_fake_1"),
        cam.CameraDevice("Cámara dos", "@device_pnp_fake_2"),
    )
    monkeypatch.setattr(cam, "list_cameras", lambda **_: fake)
    monkeypatch.setattr(cam, "capture_snapshot", lambda *a, **k: b"")
    from recstudio.app import build_app

    app, controller = build_app(size=(1280, 800))
    state.out_dir.set(str(tmp_path / "videos"))
    pilot = app.pilot(size=(1280, 800))
    pilot.settle()
    try:
        yield controller, pilot
    finally:
        controller.shutdown()
        state.screen.set("record")
        state.project_open.set(False)
        state.camera_enabled.set(False)
        state.camera_devices.set(())
        state.camera_preview.set(b"")


def test_ui_camara_activar_y_elegir_dispositivo(ui) -> None:
    _, pilot = ui
    _wait(lambda: len(state.camera_devices()) == 2)
    pilot.settle()
    assert state.camera_device() == "Cámara falsa"  # se elige la primera al detectarla

    pilot.click("sw-camera")
    pilot.settle()
    assert state.camera_enabled() is True

    pilot.click("sel-camera")
    pilot.settle()
    pilot.click("sel-camera-opt-1")
    pilot.settle()
    assert state.camera_device() == "Cámara dos"


def test_pip_automatico_al_importar_una_grabacion(ui, tmp_path: Path) -> None:
    controller, _pilot = ui
    base = _mp4(tmp_path / "grabacion.mp4", duration=1.0)
    face = _mp4(tmp_path / "grabacion-camara.mp4", color="red", duration=1.0)
    base.with_suffix(".json").write_text(
        json.dumps({
            "duration_s": 1.0,
            "camera": {
                "file": face.name, "device": "FakeCam", "offset_s": 0.3, "duration_s": 1.0,
            },
        }),
        encoding="utf-8",
    )

    controller.editor.import_paths([base], new_project=True)
    _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))
    project = state.editor_project()
    elements = [element for element in project.elements if element.kind == "video"]
    assert len(elements) == 1
    element = elements[0]
    assert element.in_ == pytest.approx(0.3)
    assert element.x > 0.7 and element.y > 0.6
    assert el.audio_gain(element) == 0.0
    assert any(asset.name == "grabacion-camara" for asset in project.assets)


def test_ui_sin_camaras_avisa_y_no_rompe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(cam, "list_cameras", lambda **_: ())
    monkeypatch.setattr(cam, "capture_snapshot", lambda *a, **k: b"")
    from recstudio.app import build_app

    app, controller = build_app(size=(1280, 800))
    state.out_dir.set(str(tmp_path / "videos"))
    pilot = app.pilot(size=(1280, 800))
    pilot.settle()
    try:
        _wait(lambda: state.camera_devices() == ())
        pilot.settle()
        assert pilot.rect("cam-refresh") is not None
        textos = [node.get("text", "") for node in pilot.layout() if node.get("kind") == "text"]
        assert any("No hay cámaras" in text for text in textos)
        pilot.click("sw-camera")  # se puede activar, pero la app no se rompe
        pilot.settle()
        assert pilot.rect("preview-panel") is not None
    finally:
        controller.shutdown()
        state.screen.set("record")
        state.camera_enabled.set(False)
