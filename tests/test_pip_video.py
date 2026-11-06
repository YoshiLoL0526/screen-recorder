"""PiP de video como elemento superpuesto (docs/28).

Comprueba el modelo, la composición del preview y un export real de 1 s con un video encima de
otro. Usa FFmpeg real, como el resto de tests de la fase 6.
"""

from __future__ import annotations

import io
import subprocess
import threading
import wave
from pathlib import Path

import numpy as np
import pytest

from recstudio.core import elements as el
from recstudio.core import graph as G
from recstudio.core import timeline as tl
from recstudio.core.project import Project
from recstudio.platform import ffmpeg as F
from recstudio.services import element_assets, media, playback


def _video(path: Path, *, size: str, color: str, duration: float) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"color=c={color}:size={size}:rate=30:duration={duration}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def _video_audio(path: Path, *, size: str, color: str, duration: float, tone: bool) -> Path:
    """Video con audio: un tono de 440 Hz o silencio (para verificar la mezcla)."""
    audio = (
        f"sine=frequency=440:sample_rate=48000:duration={duration}"
        if tone else "anullsrc=r=48000:cl=stereo"
    )
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c={color}:size={size}:rate=30:duration={duration}",
         "-f", "lavfi", "-i", audio, "-t", str(duration),
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


def _rms(path: Path, start: float, end: float) -> float:
    """RMS del audio de `path` entre dos instantes (decodificado a WAV por FFmpeg)."""
    result = subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", str(start),
         "-t", str(end - start), "-i", str(path), "-vn", "-ac", "2", "-ar", "48000",
         "-f", "wav", "-"],
        check=True, capture_output=True,
    )
    with wave.open(io.BytesIO(result.stdout), "rb") as wav:
        raw = wav.readframes(wav.getnframes())
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    mono = data.reshape(-1, 2).mean(axis=1)
    return float(np.sqrt((mono ** 2).mean()))


def _project(tmp_path: Path) -> tuple[Project, object]:
    base = _video(tmp_path / "base.mp4", size="320x180", color="blue", duration=2.0)
    pip = _video(tmp_path / "pip.mp4", size="100x100", color="red", duration=2.0)
    project = Project(width=320, height=180, fps=30)
    project, _ = tl.insert_clip(project, media.probe(base))
    project, pip_asset = tl.add_asset(project, media.probe(pip))
    project, element = el.add_element(
        project, "video", 0.5, 1.0, asset_id=pip_asset.id,
        x=0.25, y=0.5, width=0.3, height=0.3, fit="stretch",
    )
    return project.validate(), element


def _project_with_audio(
    tmp_path: Path, *, gain: float = 1.0,
) -> tuple[Project, object, object]:
    base = _video_audio(tmp_path / "base.mp4", size="320x180", color="blue",
                        duration=2.0, tone=False)
    pip = _video_audio(tmp_path / "pip.mp4", size="100x100", color="red",
                       duration=2.0, tone=True)
    project = Project(width=320, height=180, fps=30)
    project, _ = tl.insert_clip(project, media.probe(base))
    project, pip_asset = tl.add_asset(project, media.probe(pip))
    project, element = el.add_element(
        project, "video", 0.5, 1.0, asset_id=pip_asset.id,
        x=0.25, y=0.5, width=0.3, height=0.3, fit="stretch", audio_gain=gain,
    )
    return project.validate(), element, pip_asset


def test_materialize_omite_video(tmp_path: Path) -> None:
    project, element = _project(tmp_path)
    sources = element_assets.materialize(project, (320, 180))
    assert element.id not in sources


def test_preview_compone_video_solo_en_su_intervalo(tmp_path: Path) -> None:
    project, element = _project(tmp_path)
    renderer = playback.FrameRenderer(max_height=180)
    try:
        before = renderer.render(project, 0.2)
        active = renderer.render(project, 1.0)
        after = renderer.render(project, 1.6)
    finally:
        renderer.close()
    assert before is not None and active is not None and after is not None
    # Centro del PiP (x=0.25, y=0.5) en rojo cuando está activo; azul fuera.
    cy, cx = 90, 80
    assert active[cy - 4:cy + 4, cx - 4:cx + 4, 0].mean() > 180
    assert active[cy - 4:cy + 4, cx - 4:cx + 4, 2].mean() < 80
    assert before[cy, cx, 2] > before[cy, cx, 0]
    assert after[cy, cx, 2] > after[cy, cx, 0]


def test_graph_y_export_real_con_pip(tmp_path: Path) -> None:
    project, element = _project(tmp_path)
    out = tmp_path / "out.mp4"
    spec = G.build(project, G.PRESETS[0], out, encoder="libx264", exe=F.ffmpeg_exe())
    assert "-ss" in spec.args and "eof_action=pass" in spec.filter_complex
    assert any("setpts=PTS-STARTPTS+0.5/TB" in chain for chain in spec.filter_complex.split(";"))
    result = subprocess.run(spec.args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-800:]
    assert out.is_file() and out.stat().st_size > 0


def test_graph_mezcla_el_audio_del_pip_y_reutiliza_la_entrada(tmp_path: Path) -> None:
    project, element, _ = _project_with_audio(tmp_path)
    assert G.project_has_audio(project)
    assert G.has_element_audio(project)
    assert [e.id for e, _asset, _start, _end in G.element_audio(project)] == [element.id]

    spec = G.build(project, G.PRESETS[0], tmp_path / "out.mp4",
                   encoder="libx264", exe=F.ffmpeg_exe())
    assert spec.has_audio
    # La entrada 1 es la del overlay del PiP; su audio se reutiliza ([1:a]) en vez de abrir
    # el archivo otra vez. El comando debe tener una sola entrada por asset.
    assert spec.args.count("-i") == 2
    assert "[1:a]" in spec.filter_complex
    assert "pip0" in spec.filter_complex
    assert "adelay=delays=24000S:all=1" in spec.filter_complex
    assert "amix=inputs=2:normalize=0:duration=first[aout]" in spec.filter_complex

    # build_audio (la ruta que usa el preview cuando hay PiP) abre su propia entrada.
    args = G.build_audio(project, tmp_path / "mix.wav", exe=F.ffmpeg_exe())
    audio_graph = args[args.index("-filter_complex") + 1]
    assert "pip0" in audio_graph and "adelay=delays=24000S:all=1" in audio_graph


def test_export_real_mezcla_audio_del_pip_y_respeta_el_silencio(tmp_path: Path) -> None:
    project, element, _ = _project_with_audio(tmp_path)
    out = tmp_path / "out.mp4"
    spec = G.build(project, G.PRESETS[0], out, encoder="libx264", exe=F.ffmpeg_exe())
    result = subprocess.run(spec.args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-800:]
    assert _rms(out, 0.0, 0.4) < 0.005  # el video base está en silencio
    assert _rms(out, 0.7, 1.3) > 0.02  # el tono del PiP suena en su intervalo
    assert _rms(out, 1.7, 2.0) < 0.005

    muted = el.set_audio_gain(project, element.id, 0.0)
    assert not G.has_element_audio(muted)
    out_mute = tmp_path / "mute.mp4"
    spec = G.build(muted, G.PRESETS[0], out_mute, encoder="libx264", exe=F.ffmpeg_exe())
    assert "pip0" not in spec.filter_complex
    result = subprocess.run(spec.args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-800:]
    assert _rms(out_mute, 0.7, 1.3) < 0.005


def test_audio_del_pip_con_video_base_sin_audio(tmp_path: Path) -> None:
    """Caso webcam: la grabación de pantalla no tiene micrófono, pero el PiP sí."""
    base = _video(tmp_path / "base.mp4", size="320x180", color="blue", duration=2.0)
    pip = _video_audio(tmp_path / "pip.mp4", size="100x100", color="red",
                       duration=2.0, tone=True)
    project = Project(width=320, height=180, fps=30)
    project, _ = tl.insert_clip(project, media.probe(base))
    project, pip_asset = tl.add_asset(project, media.probe(pip))
    project, _ = el.add_element(
        project, "video", 0.5, 1.0, asset_id=pip_asset.id,
        x=0.25, y=0.5, width=0.3, height=0.3, audio_gain=1.0,
    )
    project = project.validate()
    assert G.project_has_audio(project)
    out = tmp_path / "out.mp4"
    spec = G.build(project, G.PRESETS[0], out, encoder="libx264", exe=F.ffmpeg_exe())
    assert spec.has_audio and "anullsrc" in spec.filter_complex  # el hueco del video base
    result = subprocess.run(spec.args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-800:]
    assert _rms(out, 0.0, 0.4) < 0.005
    assert _rms(out, 0.7, 1.3) > 0.02
    assert _rms(out, 1.7, 2.0) < 0.005


def test_build_audio_del_preview_incluye_el_pip(tmp_path: Path) -> None:
    project, _element, _ = _project_with_audio(tmp_path)
    wav_path = tmp_path / "mix.wav"
    args = G.build_audio(project, wav_path, exe=F.ffmpeg_exe())
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-800:]
    assert wav_path.is_file() and wav_path.stat().st_size > 0
    assert _rms(wav_path, 0.0, 0.4) < 0.005
    assert _rms(wav_path, 0.7, 1.3) > 0.02
    assert _rms(wav_path, 1.7, 2.0) < 0.005


def test_preview_de_audio_usa_la_mezcla_cuando_hay_pip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, _element, _ = _project_with_audio(tmp_path)
    preview = playback.AudioPreview()
    calls: list[bool] = []
    monkeypatch.setattr(preview, "_stop_locked", lambda: None)
    monkeypatch.setattr(
        preview, "_start_locked",
        lambda _project, _t, *, mixed=False: calls.append(mixed),
    )
    preview.start(project, 0.0)
    assert calls == [True]


def test_feed_mixed_del_preview_entrega_el_audio_del_pip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La ruta real del preview (`_feed_mixed`) lee el WAV y entrega el tono en su intervalo."""
    project, _element, _ = _project_with_audio(tmp_path)
    monkeypatch.setattr(playback.paths, "cache_dir", lambda: tmp_path / "cache")
    preview = playback.AudioPreview()
    chunks: list[np.ndarray] = []
    monkeypatch.setattr(
        preview, "_push", lambda samples, _cancel: bool(chunks.append(samples)) or True,
    )
    preview._feed_mixed(project, 0.0, threading.Event())
    assert preview.error is None
    data = np.concatenate(chunks).mean(axis=1)
    rate = preview.RATE

    def rms(a: float, b: float) -> float:
        chunk = data[int(a * rate): int(b * rate)]
        return float(np.sqrt((chunk ** 2).mean()))

    assert rms(0.0, 0.4) < 0.005
    assert rms(0.7, 1.3) > 0.02
    assert rms(1.7, 2.0) < 0.005
