"""Tests de integración de F4: export real con FFmpeg, corte rápido, preview con efectos y la UI
de export con Pilot (sin ventana). Medios generados con `testsrc2` + `sine` (como en F3).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from recstudio import state
from recstudio.core import effects as fx
from recstudio.core import graph as G
from recstudio.core import timeline as tl
from recstudio.core.project import Project
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import export as export_mod
from recstudio.services import media, playback


def _make_video(path: Path, *, seconds: float = 4.0, size: str = "320x240", fps: int = 30,
                audio: bool = True, gop: int = 30) -> Path:
    exe = F.ffmpeg_exe()
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-g", str(gop), "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-shortest"] if audio else ["-an"]
    subprocess.run([*cmd, str(path)], check=True, capture_output=True)
    return path


@pytest.fixture(scope="module")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("f4")
    try:
        _make_video(folder / "a.mp4", seconds=6.0)
        _make_video(folder / "b.mp4", seconds=3.0, size="640x360", fps=60, audio=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")
    return folder


def _frames_and_audio(path: Path) -> tuple[int, float | None, tuple[int, int]]:
    import av

    with av.open(str(path)) as container:
        video = container.streams.video[0]
        size = (video.codec_context.width, video.codec_context.height)
        frames = sum(1 for _ in container.decode(video))
    with av.open(str(path)) as container:
        if not container.streams.audio:
            return frames, None, size
        audio = container.streams.audio[0]
        samples = sum(f.samples for f in container.decode(audio))
        return frames, samples / audio.rate, size


def _export(project: Project, preset: str, out: Path) -> export_mod.ExportProgress:
    exporter = export_mod.Exporter()
    exporter.start(project, preset, out)
    return exporter.wait(120)


def _edited(asset) -> Project:  # noqa: ANN001
    """3 clips (el 3.º antes en la fuente que el 2.º), 2 transiciones, velocidad y efectos."""
    p = Project(fps=30, width=asset.width, height=asset.height)
    p, _ = tl.append_clip(p, asset)
    p = tl.split(p, 2.0)
    p = tl.split(p, 4.0)
    c1, c2, c3 = p.video.clips
    p = tl.move(p, c3.id, 1)  # reordenado: el mismo archivo en otro orden
    p = tl.set_speed(p, c3.id, 2.0)
    p, _ = tl.set_transition(p, 0, "fade", 0.4)
    p, _ = tl.set_transition(p, 1, "slideleft", 0.3)
    p = fx.set_effect(p, c1.id, "fade_in", seconds=0.5)
    p = fx.set_effect(p, c2.id, "eq", brightness=0.1, saturation=1.5)
    p = fx.set_effect(p, c3.id, "zoom", factor=1.5)
    return fx.set_effect(p, c3.id, "volume", gain=0.5)


# -- export real ---------------------------------------------------------------------------


def test_export_con_transiciones_velocidad_y_efectos_es_exacto(media_dir: Path, tmp_path: Path) -> None:
    project = _edited(media.probe(media_dir / "a.mp4"))
    expected = G.timeline_frames(project)[2]
    result = _export(project, "calidad", tmp_path / "out.mp4")
    assert result.state == "done", result.message
    frames, audio_s, size = _frames_and_audio(tmp_path / "out.mp4")
    assert frames == expected  # frame exacto, no ±1
    assert expected / 30 == pytest.approx(tl.total_duration(project), abs=1 / 30)
    assert audio_s == pytest.approx(expected / 30, abs=0.025)  # audio = video (un frame AAC)
    assert size == (320, 240)
    assert not list(tmp_path.glob("*.part.mp4"))


def test_export_mezcla_resoluciones_fps_y_clips_sin_audio(media_dir: Path, tmp_path: Path) -> None:
    a, b = media.probe(media_dir / "a.mp4"), media.probe(media_dir / "b.mp4")
    p = Project(fps=30, width=640, height=360)
    p, _ = tl.append_clip(p, a)
    p, _ = tl.append_clip(p, b)
    p, _ = tl.set_transition(p, 0, "circleopen", 0.5)
    result = _export(p, "ligero", tmp_path / "mix.mp4")
    assert result.state == "done", result.message
    frames, audio_s, size = _frames_and_audio(tmp_path / "mix.mp4")
    assert frames == G.timeline_frames(p)[2] == round((6 + 3 - 0.5) * 30)
    assert audio_s == pytest.approx(frames / 30, abs=0.025)  # el clip mudo aporta silencio
    assert size == (640, 360)


def test_corte_rapido_copia_los_paquetes(media_dir: Path, tmp_path: Path) -> None:
    import av

    asset = media.probe(media_dir / "a.mp4")
    assert media.keyframes(asset.path)[:3] == (0.0, 1.0, 2.0)  # -g 30 a 30 fps
    p = Project(fps=30, width=320, height=240)
    p, _ = tl.append_clip(p, asset)
    p = tl.split(p, 2.0)
    p = tl.split(p, 4.0)
    p = tl.ripple_delete(p, p.video.clips[1].id)
    p = tl.trim(p, p.video.clips[1].id, 4.2, 6.0)  # el inicio se imanta al keyframe de 4.0
    result = _export(p, "copia", tmp_path / "copy.mp4")
    assert result.state == "done", result.message
    assert "0.20 s" in result.message  # avisa de cuánto se movió el inicio

    def packets(path: Path) -> list[tuple[float, str]]:
        with av.open(str(path)) as c:
            s = c.streams.video[0]
            return [(float(x.pts * s.time_base), hashlib.sha1(bytes(x)).hexdigest()) for x in c.demux(s) if x.size]

    source = packets(media_dir / "a.mp4")
    kept = [h for t, h in source if t < 2.0 - 1e-6 or 4.0 - 1e-6 <= t]
    assert [h for _t, h in packets(tmp_path / "copy.mp4")] == kept


def test_cancelar_borra_el_parcial_y_offline_falla(media_dir: Path, tmp_path: Path) -> None:
    long_src = _make_video(tmp_path / "long.mp4", seconds=30.0, size="1280x720")
    p = Project(fps=30, width=1280, height=720)
    p, _ = tl.append_clip(p, media.probe(long_src))
    exporter = export_mod.Exporter()
    exporter.start(p, "calidad", tmp_path / "x.mp4")
    deadline = time.perf_counter() + 20
    while exporter.progress.state != "rendering" and time.perf_counter() < deadline:
        time.sleep(0.02)
    exporter.cancel()
    assert exporter.wait(20).state == "cancelled"
    assert not (tmp_path / "x.mp4").exists() and not (tmp_path / "x.part.mp4").exists()

    moved = tmp_path / "moved.mp4"
    shutil.copy(media_dir / "a.mp4", moved)
    q = Project(fps=30, width=320, height=240)
    q, _ = tl.append_clip(q, media.probe(moved))
    moved.unlink()
    result = _export(q, "calidad", tmp_path / "y.mp4")
    assert result.state == "error" and "faltan archivos" in result.message


# -- preview con efectos -----------------------------------------------------------------------


def test_preview_aplica_fundido_eq_y_zoom(media_dir: Path) -> None:
    asset = media.probe(media_dir / "a.mp4")
    p = Project(fps=30, width=320, height=240)
    p, clip = tl.append_clip(p, asset)
    renderer = playback.FrameRenderer(max_height=240)
    try:
        plain = renderer.render(p, 1.0)
        faded = renderer.render(fx.set_effect(p, clip.id, "fade_in", seconds=1.0), 0.0)
        assert faded.max() == 0  # t=0 de un fundido de entrada: negro
        half = renderer.render(fx.set_effect(p, clip.id, "fade_in", seconds=2.0), 1.0)
        assert half.mean() == pytest.approx(plain.mean() * 0.5, rel=0.05)
        gray = renderer.render(fx.set_effect(p, clip.id, "eq", saturation=0.0), 1.0).astype(int)
        assert np.abs(gray[..., 0] - gray[..., 1]).max() <= 2  # sin saturación: gris
        zoomed = renderer.render(fx.set_effect(p, clip.id, "zoom", factor=2.0), 1.0)
        assert zoomed.shape == plain.shape and not np.array_equal(zoomed, plain)
    finally:
        renderer.close()


def test_envolvente_de_audio_del_preview() -> None:
    env = playback.fade_envelope(0.0, 48_000 * 2, 48_000, 2.0, 0.5, 0.5)
    assert env[0] == 0 and env[48_000] == 1.0 and env[-1] < 0.001
    assert env[12_000] == pytest.approx(0.5, abs=1e-3)


# -- UI ---------------------------------------------------------------------------------------


@pytest.fixture()
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    from recstudio.app import build_app

    app, controller = build_app(size=(1280, 800))
    state.out_dir.set(str(tmp_path / "videos"))
    pilot = app.pilot(size=(1280, 800))
    state.screen.set("editor")
    pilot.settle()
    try:
        yield app, controller, pilot
    finally:
        controller.shutdown()
        state.project_open.set(False)
        state.selection.set(None)
        state.export_open.set(False)
        state.export_progress.set(None)
        state.screen.set("record")


def _wait(predicate, timeout: float = 30.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _open_new(editor, path: Path) -> None:  # noqa: ANN001
    """Importa `path` como proyecto nuevo y espera a que sea **ese** el abierto (un hilo del test
    anterior puede dejar `project_open` a True con otro proyecto)."""
    target = str(path.resolve())
    editor.import_paths([path], new_project=True)
    assert _wait(lambda: state.project_open() and any(a.path == target for a in state.editor_project().assets)
                 and state.project_path() != "")


def _settle(pilot, frames: int = 3) -> None:
    for _ in range(frames):
        pilot.frame()
    pilot.settle()


def test_inspector_de_efectos_con_undo(ui, media_dir: Path) -> None:
    _, controller, pilot = ui
    editor = controller.editor
    _open_new(editor, media_dir / "a.mp4")
    clip = state.editor_project().video.clips[0]
    editor.select_clip(clip.id)
    _settle(pilot)
    assert pilot.rect("insp-volume") is not None
    state.insp_volume.set(0.5)
    state.insp_brightness.set(0.1)
    current = state.editor_project().clip(clip.id)
    assert fx.gain(current) == 0.5 and fx.eq(current)["brightness"] == 0.1
    assert state.undo_label() == "brillo"
    editor.undo()
    assert fx.eq(state.editor_project().clip(clip.id)) is None
    assert state.insp_brightness() == 0.0  # el inspector vuelve con el undo
    editor.reset_effects()
    assert state.editor_project().clip(clip.id).effects == ()
    state.selection.set(None)
    _settle(pilot)
    state.insp_track_gain.set(0.0)
    assert state.editor_project().video.muted


def test_exportar_desde_el_modal(ui, media_dir: Path) -> None:
    _, controller, pilot = ui
    editor = controller.editor
    _open_new(editor, media_dir / "a.mp4")
    _settle(pilot)
    pilot.press("ctrl+e")
    _settle(pilot)
    assert state.export_open() and state.export_blockers() == ()
    pilot.click("preset-copia")
    _settle(pilot)
    assert state.export_preset() == "copia"
    pilot.click("preset-calidad")
    _settle(pilot)
    state.export_name.set("prueba")
    pilot.click("export-start")
    assert _wait(lambda: state.export_progress() is not None and state.export_progress().state == "done", 60)
    out = Path(state.export_progress().out_path)
    assert out == state.exports_dir() / "prueba.mp4" and out.exists()
    _settle(pilot)
    assert pilot.rect("export-done") is not None and pilot.rect("export-play") is not None
    pilot.press("escape")
    _settle(pilot)
    assert not state.export_open() and state.export_progress() is None
    # un segundo export con el mismo nombre no pisa el primero
    editor.open_export()
    state.export_name.set("prueba")
    editor.start_export()
    assert _wait(lambda: state.export_progress() is not None and state.export_progress().state == "done", 60)
    assert Path(state.export_progress().out_path).name == "prueba (2).mp4"


def test_reubicar_un_archivo_offline(ui, media_dir: Path, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = controller.editor
    from dataclasses import replace

    _open_new(editor, media_dir / "a.mp4")
    # el archivo «se movió»: el proyecto apunta a una ruta que ya no existe (en Windows no se
    # puede mover de verdad mientras el preview lo tiene abierto)
    project = state.editor_project()
    asset = replace(project.assets[0], path=str(tmp_path / "desaparecido.mp4"))
    editor.store.apply("simular", replace(project, assets=(asset,)))
    moved = tmp_path / "otra" / "orig.mp4"
    moved.parent.mkdir()
    shutil.copy(media_dir / "a.mp4", moved)
    editor._refresh_offline(state.editor_project())
    assert asset.id in state.offline_assets()
    editor.start_export()
    assert state.export_progress() is None  # bloqueado con aviso
    assert editor.relocate_to(asset.id, moved)
    assert state.editor_project().asset(asset.id).path == str(moved.resolve())
    assert not state.offline_assets()
    # un archivo demasiado corto no vale: el proyecto no cambia
    short = _make_video(tmp_path / "corto.mp4", seconds=1.0)
    before = state.editor_project()
    assert not editor.relocate_to(asset.id, short)
    assert state.editor_project() == before
