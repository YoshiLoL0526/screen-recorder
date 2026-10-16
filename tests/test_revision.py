"""Tests de los bugs de la revisión: deriva con pausa, doble arranque, offsets de recuperación,
parada tras error/auto-parada y grabaciones recuperadas en la biblioteca."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from recstudio.core.config import AudioConfig, RecordingConfig
from recstudio.platform import audio as audio_mod
from recstudio.services import recording as rec_mod

RATE = 48000
BLOCK = 1024


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _feed(recorder: audio_mod.AudioRecorder, clock: FakeClock, seconds: float, ppm: float = 0.0) -> None:
    """Simula callbacks de un dispositivo cuyo reloj va `ppm` más rápido que el del sistema."""
    data = bytes(BLOCK * 2 * recorder.channels)
    step = BLOCK / (RATE * (1.0 + ppm / 1e6))  # segundos reales entre bloques
    end = clock.t + seconds
    while clock.t < end:
        recorder._callback(data, BLOCK, None, 0)
        clock.t += step


def _recorder(tmp_path: Path) -> audio_mod.AudioRecorder:
    device = audio_mod.AudioDevice(0, "fake", RATE, 2, "loopback")
    return audio_mod.AudioRecorder(device, tmp_path / "a.wav")


# -- bug 1: la pausa no debe parecer deriva del reloj ------------------------------

@pytest.mark.parametrize("ppm", [0.0, 90.0, -120.0])
def test_deriva_ignora_el_tiempo_en_pausa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ppm: float) -> None:
    clock = FakeClock()
    monkeypatch.setattr(audio_mod.time, "perf_counter", clock)
    recorder = _recorder(tmp_path)

    _feed(recorder, clock, 45, ppm)
    recorder.pause()
    _feed(recorder, clock, 30, ppm)  # en pausa: los bloques se descartan
    recorder.resume()
    _feed(recorder, clock, 45, ppm)

    assert recorder._measure_drift() == pytest.approx(ppm, abs=3.0)


def test_deriva_con_pausa_corta_no_cuenta_como_grabacion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """40 s grabados + 40 s de pausa: no llega a los 60 s mínimos, así que no corrige."""
    clock = FakeClock()
    monkeypatch.setattr(audio_mod.time, "perf_counter", clock)
    recorder = _recorder(tmp_path)

    _feed(recorder, clock, 20)
    recorder.pause()
    _feed(recorder, clock, 40)
    recorder.resume()
    _feed(recorder, clock, 20)

    assert recorder._measure_drift() == 0.0


# -- dobles para el motor (sin captura, audio ni FFmpeg reales) ------------------------

class FakeBackend:
    name = "fake"
    pixel_format = "bgra"

    def __init__(self) -> None:
        self.lost_source = False
        self.stopped = False

    def grab(self) -> np.ndarray:
        return np.zeros((16, 16, 4), np.uint8)

    def stop(self) -> None:
        self.stopped = True


class FakeStdin:
    def __init__(self, broken: bool) -> None:
        self.broken = broken
        self.closed = False

    def write(self, data: bytes) -> None:
        if self.broken:
            raise BrokenPipeError

    def close(self) -> None:
        self.closed = True


class FakeProc:
    def __init__(self, broken: bool = False) -> None:
        self.stdin = FakeStdin(broken)

    def poll(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0


class FakeRecorder:
    def __init__(self, path: Path, first_callback_t: float) -> None:
        self.path = path
        self.first_callback_t = first_callback_t
        self.level = 0.0
        self.stopped = False

    def stop(self) -> audio_mod.AudioTrack:
        self.stopped = True
        return audio_mod.AudioTrack(self.path, "fake", RATE, 2, RATE, self.first_callback_t)


class FakeKeepAlive:
    stopped = False

    def stop(self) -> None:
        self.stopped = True


def _session(tmp_path: Path, *, broken: bool = False) -> rec_mod.RecordingSession:
    config = RecordingConfig(fps=60, audio=AudioConfig(system=False))
    session = rec_mod.RecordingSession(config, out_dir=tmp_path / "out", temp_dir=tmp_path / "tmp")
    session._backend = FakeBackend()
    session._backend_name = "fake"
    session._proc = FakeProc(broken)  # type: ignore[assignment]
    session._size = (16, 16)
    session._recorders = [  # el audio arrancó 0,2 s antes que el primer frame
        FakeRecorder(session.temp_dir / "audio_system.wav", time.perf_counter() - 0.2)  # type: ignore[list-item]
    ]
    session._keepalive = FakeKeepAlive()  # type: ignore[assignment]
    session._state = rec_mod.RecState.RECORDING
    session._capture_thread = threading.Thread(target=session._capture_loop, daemon=True)
    session._capture_thread.start()
    return session


def _wait(predicate, timeout: float = 3.0) -> None:  # noqa: ANN001
    deadline = time.perf_counter() + timeout
    while not predicate():
        assert time.perf_counter() < deadline, "timeout esperando la condición"
        time.sleep(0.01)


# -- bug 3: session.json debe tener los offsets reales para recuperar -------------------

def test_session_json_guarda_offsets_reales_tras_el_primer_frame(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session_file = session.temp_dir / "session.json"
    try:
        _wait(lambda: session._t0_video is not None and session_file.exists())
        data = json.loads(session_file.read_text(encoding="utf-8"))
    finally:
        session._stop.set()
        session._capture_thread.join(timeout=2)  # type: ignore[union-attr]
    assert data["status"] == "recording"
    assert data["offsets"]["audio_system.wav"] == pytest.approx(-0.2, abs=0.1)


# -- bug 4: parar una sesión que se detuvo sola o con error ------------------------------

def test_stop_tras_error_de_ffmpeg_libera_todo_y_muxea(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path, broken=True)  # FFmpeg "muere": la escritura al pipe falla
    _wait(lambda: session.stopped_by_itself)
    assert session.state is rec_mod.RecState.ERROR

    (session.temp_dir / "video.mkv").write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    sentinel = FakeResult(tmp_path / "out" / "x.mp4")
    monkeypatch.setattr(session, "_mux", lambda tracks, offsets, *, recover: sentinel)
    recorder, backend, keepalive = session._recorders[0], session._backend, session._keepalive

    assert session.stop() is sentinel
    assert session.state is rec_mod.RecState.DONE
    assert recorder.stopped and backend.stopped and keepalive.stopped  # type: ignore[union-attr]
    assert any("interrumpió" in w for w in session._warnings)


def test_stop_tras_error_sin_frames_libera_recursos(tmp_path: Path) -> None:
    session = _session(tmp_path, broken=True)
    _wait(lambda: session.stopped_by_itself)
    recorder, backend = session._recorders[0], session._backend

    with pytest.raises(rec_mod.FfmpegError):
        session.stop()  # no hay MKV: error, pero ya sin audio ni captura vivos
    assert recorder.stopped and backend.stopped  # type: ignore[union-attr]


def test_stop_de_sesion_no_iniciada_sigue_fallando(tmp_path: Path) -> None:
    session = rec_mod.RecordingSession(RecordingConfig(), out_dir=tmp_path, temp_dir=tmp_path / "t")
    session._state = rec_mod.RecState.ERROR  # falló en start(): no hay nada que parar
    with pytest.raises(rec_mod.RecStudioError):
        session.stop()


# -- bug 2 y bug 4 en el controlador: doble pulsación y auto-parada ----------------------

class FakeResult:
    def __init__(self, mp4: Path) -> None:
        self.mp4 = mp4
        self.duration_s = 1.0


class FakeSession:
    instances: list["FakeSession"] = []
    mp4: Path

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        FakeSession.instances.append(self)
        self.backend_name = self.encoder_name = "fake"
        self.stops = 0
        self.stopped_by_itself = False

    def start(self) -> None:
        time.sleep(0.3)  # «Preparando…»

    def stats(self) -> rec_mod.RecordingStats:
        error = "FFmpeg salió con código 1" if self.stopped_by_itself else None
        return rec_mod.RecordingStats(error=error)

    def stop(self) -> FakeResult:
        self.stops += 1
        time.sleep(0.2)
        return FakeResult(FakeSession.mp4)

    def abort(self) -> None:
        pass


@pytest.fixture
def controller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    from recstudio import state
    from recstudio.services.settings import Settings
    from recstudio.ui import controller as controller_mod

    FakeSession.instances = []
    FakeSession.mp4 = tmp_path / "x.mp4"
    FakeSession.mp4.write_bytes(b"\x00" * 10)
    monkeypatch.setattr(controller_mod.rec_mod, "RecordingSession", FakeSession)
    monkeypatch.setattr(controller_mod, "_POLL_INTERVAL", 0.02)
    state.countdown_s.set(0)
    state.countdown.set(0)
    state.rec_state.set("idle")
    ctl = controller_mod.Controller(Settings())
    monkeypatch.setattr(ctl, "_build_config", lambda: object())
    monkeypatch.setattr(ctl, "refresh_library", lambda: None)
    yield ctl
    ctl._closing = True


def _started(ctl) -> bool:  # noqa: ANN001
    return ctl.session is not None and not ctl._busy


def _stopped(ctl) -> bool:  # noqa: ANN001
    return ctl.session is None and not ctl._busy


def test_doble_pulsacion_al_arrancar_crea_una_sola_sesion(controller) -> None:  # noqa: ANN001
    controller.toggle_record()
    time.sleep(0.05)
    controller.toggle_record()  # F9 otra vez durante «Preparando…»
    _wait(lambda: _started(controller))
    time.sleep(0.1)
    assert len(FakeSession.instances) == 1


def test_doble_pulsacion_al_parar_para_una_sola_vez(controller) -> None:  # noqa: ANN001
    controller.toggle_record()
    _wait(lambda: _started(controller))
    session = controller.session
    controller.toggle_record()
    controller.toggle_record()
    _wait(lambda: _stopped(controller))
    assert session.stops == 1


def test_auto_parada_del_motor_la_completa_el_controlador(controller) -> None:  # noqa: ANN001
    from recstudio import state

    controller.toggle_record()
    _wait(lambda: _started(controller))
    session = controller.session
    session.stopped_by_itself = True  # p. ej. FFmpeg murió o el disco se llenó
    _wait(lambda: _stopped(controller))
    assert session.stops == 1
    assert state.rec_state() == "idle"
