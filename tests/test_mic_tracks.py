"""Pistas de audio separadas: micrófono como WAV hermano y pista absoluta (docs/50).

Sin hardware: los WAV se generan con numpy/`wave`, el video con FFmpeg real y las sesiones se
montan con `AudioTrack` sintéticas. Los tests de UI usan la app real con `pilot`.
"""

from __future__ import annotations

import json
import math
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from recstudio import state
from recstudio.core import graph as G
from recstudio.core import timeline as tl
from recstudio.core.config import AudioConfig, RecordingConfig
from recstudio.core.errors import ConfigError
from recstudio.platform import audio as audio_mod
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import library
from recstudio.services import recording as rec_mod
from recstudio.services.settings import Settings


# -- configuración -------------------------------------------------------------

def test_audio_config_valida_el_modo() -> None:
    assert AudioConfig().mode == "mix"
    assert AudioConfig(mode="separate").validate().mode == "separate"
    assert AudioConfig(mode="separate", mic_device_index=3).separate_mic
    assert not AudioConfig(mode="mix", mic_device_index=3).separate_mic
    assert not AudioConfig(mode="separate").separate_mic  # sin micrófono no hay nada que separar

    with pytest.raises(ConfigError, match="modo de audio"):
        AudioConfig(mode="otro").validate()  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="micrófono"):
        AudioConfig(mic_device_index=-2).validate()
    with pytest.raises(ConfigError, match="modo de audio"):
        RecordingConfig(audio=AudioConfig(mode="otro")).validate()  # type: ignore[arg-type]


def test_settings_normaliza_el_modo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    settings = Settings(audio_mode="separate").normalized()
    assert settings.audio_mode == "separate"
    assert Settings(audio_mode="raro").normalized().audio_mode == "mix"


def test_mux_cmd_separado_solo_abre_la_primera_pista() -> None:
    cmd = F.mux_cmd(
        "ff", video="v.mkv",
        tracks=[F.MuxTrack(Path("sys.wav"), 0.1), F.MuxTrack(Path("mic.wav"), -0.05)],
        out_path="o.mp4", duration_s=12.5, mix=False,
    )
    assert "sys.wav" in cmd and "mic.wav" not in cmd
    assert cmd.count("-i") == 2  # video + sistema
    chain = cmd[cmd.index("-filter_complex") + 1]
    assert "amix" not in chain
    assert chain.endswith("[aout]")


# -- ayuda: medios reales ------------------------------------------------------

def _wav(
    path: Path, *, seconds: float = 1.2, rate: int = 48000, channels: int = 2,
    freq: float = 440.0,
) -> Path:
    frames = int(seconds * rate)
    t = np.arange(frames, dtype=np.float64) / rate
    tone = (np.sin(2 * math.pi * freq * t) * 1800).astype("<i2")
    data = np.repeat(tone[:, None], channels, axis=1)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(data.tobytes())
    return path


def _mkv(path: Path, *, duration: float = 1.2) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"color=c=blue:size=160x90:rate=30:duration={duration}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-f", "matroska", str(path)],
        check=True, capture_output=True,
    )
    return path


def _video_with_audio(path: Path, *, seconds: float = 2.0, freq: float = 440.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


def _audio_streams(path: Path) -> int:
    text = subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stderr
    return text.count("Audio:")


def _track(path: Path, device: str) -> audio_mod.AudioTrack:
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        channels = wav.getnchannels()
        frames = wav.getnframes()
    return audio_mod.AudioTrack(
        path=path, device=device, rate=rate, channels=channels, frames=frames,
        first_callback_t=None,
    )


def _session(
    tmp_path: Path, mode: str, *, system: bool = True,
) -> tuple[rec_mod.RecordingSession, list[audio_mod.AudioTrack], dict[str, float]]:
    config = RecordingConfig(
        fps=30,
        audio=AudioConfig(system=system, mic_device_index=4, mode=mode),  # type: ignore[arg-type]
    )
    session = rec_mod.RecordingSession(config, out_dir=tmp_path / "out", temp_dir=tmp_path / "tmp")
    session._backend_name = "fake"
    session._encoder = "libx264"
    session._size = (160, 90)
    session._mic_device = "Mic de prueba"
    _mkv(session._mkv)
    tracks: list[audio_mod.AudioTrack] = []
    offsets: dict[str, float] = {}
    if system:
        sys_wav = _wav(session.temp_dir / "audio_system.wav", freq=440.0)
        tracks.append(_track(sys_wav, "Altavoces"))
        offsets[sys_wav.name] = 0.1
    mic_wav = _wav(session.temp_dir / "audio_mic.wav", freq=660.0)
    tracks.append(_track(mic_wav, "Mic de prueba"))
    offsets[mic_wav.name] = -0.2
    return session, tracks, offsets


# -- mux: WAV hermano y sidecar ------------------------------------------------

def test_mux_separado_guarda_microfono_y_sidecar(tmp_path: Path) -> None:
    session, tracks, offsets = _session(tmp_path, "separate")
    result = session._mux(tracks, offsets, recover=False)

    assert result.mic is not None and result.mic.is_file()
    assert result.mic.name == f"{result.mp4.stem}-microfono.wav"
    assert result.mic_offset_s == pytest.approx(-0.2)
    assert result.mic.read_bytes()[:4] == b"RIFF"

    # El MP4 conserva una sola pista de audio (el sistema); el micrófono va aparte.
    assert _audio_streams(result.mp4) == 1
    info = F.probe_media(result.mp4)
    assert info.audio_codec == "aac"

    data = json.loads(result.sidecar.read_text(encoding="utf-8"))
    assert data["mic"]["file"] == result.mic.name
    assert data["mic"]["device"] == "Mic de prueba"
    assert data["mic"]["offset_s"] == pytest.approx(-0.2)
    assert data["mic"]["duration_s"] == pytest.approx(1.2, abs=0.05)
    assert data["mic"]["rate"] == 48000
    assert "drift_ppm" in data["mic"]


def test_mux_mezcla_no_crea_wav_hermano(tmp_path: Path) -> None:
    session, tracks, offsets = _session(tmp_path, "mix")
    result = session._mux(tracks, offsets, recover=False)

    assert result.mic is None
    assert not list((tmp_path / "out").glob("*-microfono.wav"))
    data = json.loads(result.sidecar.read_text(encoding="utf-8"))
    assert data["mic"] is None
    assert F.probe_media(result.mp4).audio_codec == "aac"


def test_mux_separado_sin_sistema_deja_el_mp4_sin_audio(tmp_path: Path) -> None:
    session, tracks, offsets = _session(tmp_path, "separate", system=False)
    mic = next(track for track in tracks if track.path.name == "audio_mic.wav")
    result = session._mux([mic], {"audio_mic.wav": 0.0}, recover=False)

    assert F.probe_media(result.mp4).audio_codec is None
    assert result.mic is not None and result.mic.is_file()


def test_mux_separado_sin_microfono_se_comporta_como_mix(tmp_path: Path) -> None:
    session, tracks, offsets = _session(tmp_path, "separate")
    system_only = [track for track in tracks if track.path.name == "audio_system.wav"]
    result = session._mux(system_only, offsets, recover=False)

    assert result.mic is None
    assert F.probe_media(result.mp4).audio_codec == "aac"
    assert json.loads(result.sidecar.read_text(encoding="utf-8"))["mic"] is None


# -- recuperación --------------------------------------------------------------

def test_recuperacion_conserva_el_microfono(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rec_mod.paths, "temp_root", lambda: tmp_path)
    folder = tmp_path / "rec_2026-01-01_10-00-00"
    folder.mkdir()
    _mkv(folder / "video.mkv")
    _wav(folder / "audio_system.wav")
    _wav(folder / "audio_mic.wav", freq=660.0)
    (folder / "session.json").write_text(
        json.dumps({
            "status": "recording",
            "started_at": "2026-01-01 10:00:00",
            "backend": "fake",
            "encoder": "libx264",
            "offsets": {"audio_system.wav": 0.05, "audio_mic.wav": -0.15},
            "mic": {"enabled": True, "mode": "separate", "device": "Mic recuperada"},
            "config": {
                "fps": 30, "preset": "media",
                "audio": {
                    "system": True, "mic_device_index": 4, "keepalive": True,
                    "mode": "separate",
                },
            },
        }),
        encoding="utf-8",
    )

    result = rec_mod.recover_session(folder, out_dir=tmp_path / "out")

    assert result.mic is not None and result.mic.is_file()
    assert result.mic.name.endswith("-microfono.wav")
    assert result.mic_offset_s == pytest.approx(-0.15)
    data = json.loads(result.sidecar.read_text(encoding="utf-8"))
    assert data["mic"]["file"] == result.mic.name
    assert data["mic"]["device"] == "Mic recuperada"
    assert not folder.exists()  # el temporal se limpió después de copiar el micrófono


# -- biblioteca ----------------------------------------------------------------

def test_biblioteca_lee_y_borra_el_microfono(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mp4 = tmp_path / "2026-01-01_10-00-00.mp4"
    mp4.write_bytes(b"mp4")
    mic = _wav(tmp_path / "2026-01-01_10-00-00-microfono.wav")
    mp4.with_suffix(".json").write_text(
        json.dumps({
            "duration_s": 5.0, "width": 160, "height": 90, "fps": 30,
            "mic": {
                "file": mic.name, "device": "Mic", "offset_s": 0.3,
                "duration_s": 5.2, "drift_ppm": 12.5, "rate": 48000,
            },
        }),
        encoding="utf-8",
    )

    item = library.list_recordings(tmp_path)[0]
    assert item.mic == mic and item.has_mic
    assert item.mic_offset_s == pytest.approx(0.3)
    assert item.mic_duration_s == pytest.approx(5.2)
    assert item.mic_drift_ppm == pytest.approx(12.5)
    assert item.mic_rate == 48000
    assert library.mic_declared(mp4)

    info = library.mic_info(mp4)
    assert info is not None and info.path == mic and info.offset_s == pytest.approx(0.3)

    deleted: list[Path] = []
    monkeypatch.setattr(library, "move_to_trash", lambda path: deleted.append(path) or True)
    removed = library.delete_recording_files(item)
    assert mic in removed and mic in deleted


def test_biblioteca_ignora_microfono_ausente_o_vacio(tmp_path: Path) -> None:
    mp4 = tmp_path / "x.mp4"
    mp4.write_bytes(b"mp4")
    sidecar = mp4.with_suffix(".json")
    sidecar.write_text(
        json.dumps({"mic": {"file": "no-existe.wav", "offset_s": 0.2}}), encoding="utf-8"
    )
    assert library.mic_info(mp4) is None
    assert library.mic_declared(mp4)
    assert library.list_recordings(tmp_path)[0].mic is None

    empty = tmp_path / "vacio.wav"
    empty.write_bytes(b"")
    sidecar.write_text(json.dumps({"mic": {"file": empty.name}}), encoding="utf-8")
    assert library.mic_info(mp4) is None
    assert not library.list_recordings(tmp_path)[0].has_mic


# -- editor: importación automática -------------------------------------------

def _wait(predicate, timeout: float = 5.0) -> None:  # noqa: ANN001
    deadline = time.perf_counter() + timeout
    while not predicate():
        assert time.perf_counter() < deadline, "timeout esperando la condición"
        time.sleep(0.05)


@pytest.fixture()
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    fake_devices = (
        audio_mod.AudioDevice(index=1, name="Micrófono falso", rate=48000, channels=2, kind="input"),
    )
    monkeypatch.setattr(audio_mod, "list_devices", lambda: list(fake_devices))
    # Los Signals de sistema sobreviven a las apps de otros tests: vaciarlos obliga a esperar
    # a que esta app cargue sus dispositivos (si no, el `select` se pulsa a medio construir).
    state.audio_devices.set(())
    state.monitors.set(())
    state.camera_devices.set(())
    state.toasts.set(())
    state.confirm.set(None)
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
        state.audio_mode.set("mix")
        state.mic_index.set(-1)
        state.toasts.set(())
        state.confirm.set(None)


def _recording_with_mic(tmp_path: Path, *, offset: float = 0.25) -> tuple[Path, Path]:
    base = _video_with_audio(tmp_path / "grabacion.mp4", seconds=2.0)
    mic = _wav(tmp_path / "grabacion-microfono.wav", seconds=1.8, freq=660.0)
    base.with_suffix(".json").write_text(
        json.dumps({
            "duration_s": 2.0,
            "width": 320, "height": 180, "fps": 30,
            "mic": {
                "file": mic.name, "device": "Micrófono falso", "offset_s": offset,
                "duration_s": 1.8, "drift_ppm": 9.0, "rate": 48000,
            },
        }),
        encoding="utf-8",
    )
    return base, mic


def test_importacion_crea_la_pista_microfono_en_su_offset(ui, tmp_path: Path) -> None:
    controller, _pilot = ui
    base, mic = _recording_with_mic(tmp_path, offset=0.25)

    controller.editor.import_paths([base], new_project=True)
    _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))

    project = state.editor_project()
    mic_tracks = [track for track in project.audio if track.name == "Micrófono"]
    assert len(mic_tracks) == 1
    track = mic_tracks[0]
    assert track.mode == "absolute" and len(track.clips) == 1
    clip = track.clips[0]
    assert clip.start == pytest.approx(0.25, abs=0.02)
    assert clip.in_ == pytest.approx(0.0)
    assert project.asset(clip.asset_id).path == str(mic.resolve())
    # El audio del sistema sigue en su pista enlazada («Audio del video»).
    assert any(track.name == "Audio del video" and track.clips for track in project.audio)


def test_importacion_con_desfase_negativo_recorta_el_arranque(ui, tmp_path: Path) -> None:
    controller, _pilot = ui
    base, _mic = _recording_with_mic(tmp_path, offset=-0.2)

    controller.editor.import_paths([base], new_project=True)
    _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))

    project = state.editor_project()
    track = next(track for track in project.audio if track.name == "Micrófono")
    clip = track.clips[0]
    assert clip.start == pytest.approx(0.0, abs=0.01)
    assert clip.in_ == pytest.approx(0.2, abs=0.02)
    assert clip.out_ == pytest.approx(1.8, abs=0.02)


def test_importacion_sin_wav_no_crea_pista_ni_rompe(ui, tmp_path: Path) -> None:
    controller, _pilot = ui
    base = _video_with_audio(tmp_path / "grabacion.mp4", seconds=1.5)
    base.with_suffix(".json").write_text(
        json.dumps({"mic": {"file": "grabacion-microfono.wav", "offset_s": 0.2}}),
        encoding="utf-8",
    )

    controller.editor.import_paths([base], new_project=True)
    _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))

    project = state.editor_project()
    assert not any(track.name == "Micrófono" for track in project.audio)
    assert any("micrófono" in toast.text.lower() for toast in state.toasts())


def test_export_y_preview_mezclan_sistema_y_microfono(ui, tmp_path: Path) -> None:
    controller, _pilot = ui
    base, _mic = _recording_with_mic(tmp_path, offset=0.25)
    controller.editor.import_paths([base], new_project=True)
    _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))
    project = state.editor_project()

    out = tmp_path / "export.mp4"
    spec = G.build(
        project, G.PRESETS[0], out, encoder="libx264", exe=F.ffmpeg_exe()
    )
    assert str(tmp_path / "grabacion-microfono.wav") in spec.args
    result = subprocess.run(spec.args, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=120)
    assert result.returncode == 0, result.stderr[-1200:]
    assert F.probe_media(out).audio_codec is not None

    # La mezcla del preview (`build_audio`) usa las mismas dos fuentes y produce audio.
    preview = tmp_path / "preview.wav"
    args = G.build_audio(project, preview, exe=F.ffmpeg_exe())
    mixed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
    assert mixed.returncode == 0, mixed.stderr[-1200:]
    assert preview.is_file() and preview.stat().st_size > 1000
    assert F.probe_media(preview).audio_codec is not None


# -- UI de Grabación -----------------------------------------------------------

def test_ui_selector_de_modo_y_aviso_sin_microfono(ui) -> None:
    _controller, pilot = ui
    _wait(lambda: bool(state.audio_devices()))
    pilot.settle()
    assert state.audio_mode() == "mix"
    assert pilot.rect("sel-audio-mode") is not None

    textos = [node.get("text", "") for node in pilot.layout() if node.get("kind") == "text"]
    assert any("Sin micrófono" in text and "Pistas separadas" in text for text in textos)

    pilot.click("sel-audio-mode")
    pilot.settle()
    assert pilot.rect("sel-audio-mode-opt-1") is not None
    pilot.click("sel-audio-mode-opt-1")
    pilot.settle()
    assert state.audio_mode() == "separate"

    # Con micrófono elegido, la ayuda cambia al archivo que se generará. Se cambia el Signal
    # directamente: encadenar dos desplegables en el mismo pilot es frágil en la suite completa.
    state.mic_index.set(1)
    pilot.settle()
    assert state.mic_index() == 1
    textos = [node.get("text", "") for node in pilot.layout() if node.get("kind") == "text"]
    assert any("microfono.wav" in text for text in textos)
