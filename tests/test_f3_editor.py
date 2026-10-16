"""Tests de F3: medios (PyAV), preview/reproducción y el editor completo con Pilot (sin ventana).

Los medios se generan con el FFmpeg del wheel (`testsrc2` + `sine`), como los de integración.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from recstudio import state
from recstudio.core import store as store_mod
from recstudio.core import timeline as tl
from recstudio.core.project import Project
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import media, playback


def _make_video(path: Path, *, seconds: float = 4.0, size: str = "640x360", fps: int = 30) -> Path:
    exe = F.ffmpeg_exe()
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-g", str(fps), "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    try:
        return _make_video(tmp_path_factory.mktemp("media") / "demo.mp4")
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")


def _wait(predicate, timeout: float = 10.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _three_clips(asset) -> Project:
    """3 clips del mismo asset con cortes en frontera de frame (30 fps)."""
    p = Project(fps=30, width=asset.width, height=asset.height)
    p, _ = tl.append_clip(p, asset)
    p = tl.split(p, 1.0)
    p = tl.split(p, 2.5)
    return p


# -- servicios ---------------------------------------------------------------------

def test_probe_waveform_y_poster(video: Path) -> None:
    asset = media.probe(video)
    assert asset.duration == pytest.approx(4.0, abs=0.05)
    assert (asset.width, asset.height) == (640, 360)
    assert asset.fps == pytest.approx(30.0)
    assert asset.has_audio

    peaks = media.waveform(video)
    assert peaks.shape[1] == 2 and len(peaks) == pytest.approx(4.0 * media.BUCKETS_PER_S, abs=5)
    assert peaks[:, 1].max() > 0.05 and peaks[:, 0].min() < -0.05  # el seno se ve
    assert media.cached_waveform(video) is peaks
    part = media.waveform_slice(peaks, 1.0, 2.0, 50)
    assert part.shape == (50, 2)
    assert media.poster(video)[:2] == b"\xff\xd8"  # JPEG


def test_probe_de_algo_que_no_es_video(tmp_path: Path) -> None:
    from recstudio.core.errors import MediaError

    bogus = tmp_path / "x.mp4"
    bogus.write_bytes(b"no soy un video")
    with pytest.raises(MediaError):
        media.probe(bogus)


def test_renderer_cortes_y_transicion(video: Path) -> None:
    asset = media.probe(video)
    p = _three_clips(asset)
    renderer = playback.FrameRenderer(max_height=180)
    try:
        frame = renderer.render(p, 0.5)
        assert frame is not None and frame.shape == (180, 320, 3)
        assert renderer.render_jpeg(p, 1.2)[:2] == b"\xff\xd8"
        # transición: el frame del medio es mezcla de los dos clips
        p2, _ = tl.set_transition(p, 0, "fadeblack", 0.4)
        start = tl.clip_starts(p2.video)[1]
        mid = renderer.render(p2, start + 0.2)
        assert renderer.last_transition == "fadeblack"
        assert mid.mean() < 30  # en el centro de un fundido a negro
        assert renderer.render(Project(), 0.0) is None
    finally:
        renderer.close()


def test_blend_extremos() -> None:
    a = np.full((4, 6, 3), 200, np.uint8)
    b = np.full((4, 6, 3), 10, np.uint8)
    for kind in ("fade", "fadeblack", "fadewhite", "wipeleft", "wiperight", "slideleft", "circleopen", "dissolve"):
        assert np.array_equal(playback.blend(kind, a, b, 0.0), a), kind
        assert np.array_equal(playback.blend(kind, a, b, 1.0), b), kind


def test_segmentos_de_audio_duran_lo_mismo_que_el_video(video: Path) -> None:
    asset = media.probe(video)
    p, _ = tl.set_transition(_three_clips(asset), 0, "fade", 0.4)
    segments = playback.audio_segments(p)
    assert sum(s.end - s.start for s in segments) == pytest.approx(tl.total_duration(p))
    assert segments[1].local == pytest.approx(0.4)  # el entrante empieza tras el solape


def test_reproduccion_a_24_fps_o_mas(video: Path) -> None:
    asset = media.probe(video)
    p = _three_clips(asset)
    frames: list[bytes] = []
    times: list[float] = []
    states: list[bool] = []
    player = playback.Player(frames.append, times.append, states.append, audio=None)
    player.set_project(p)
    player.start()
    try:
        player.play(0.0)
        time.sleep(1.6)
        player.pause()
        played = times[-1] - times[0] if len(times) > 1 else 0.0
        fps = (len(times) - 1) / played if played else 0.0
        assert fps >= 24, f"preview a {fps:.1f} fps"
        assert states[:2] == [True, False]
        # al llegar al final se para solo
        player.play(tl.total_duration(p) - 0.2)
        assert _wait(lambda: not player.playing, 3)
    finally:
        player.stop()


# -- UI con Pilot ---------------------------------------------------------------------


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
        state.import_open.set(False)
        state.screen.set("record")


def _settle(pilot, frames: int = 3) -> None:
    for _ in range(frames):
        pilot.frame()
    pilot.settle()


def _open_three_clips(controller, pilot, video: Path) -> None:
    editor = controller.editor
    editor.import_paths([video])
    assert _wait(lambda: state.project_open() and state.editor_project().video.clips), "no se importó"
    editor.seek(1.0)
    pilot.press("s")
    editor.seek(2.5)
    pilot.press("s")
    _settle(pilot)


def test_editor_monta_tres_clips_con_cortes_exactos(ui, video: Path) -> None:
    _, controller, pilot = ui
    from recstudio.ui.timeline import TL_X0

    _open_three_clips(controller, pilot, video)
    project = state.editor_project()
    clips = project.video.clips
    assert len(clips) == 3
    assert [(c.in_, c.out_) for c in clips] == [(0.0, 1.0), (1.0, 2.5), (2.5, clips[2].out_)]
    assert tl.total_duration(project) == pytest.approx(media.probe(video).duration)
    # la constante de layout coincide con el layout real (de ella depende el clic en la regla)
    assert pilot.rect("tl-scroll")[0] == TL_X0
    # el preview llega
    assert _wait(lambda: state.preview_frame()[:2] == b"\xff\xd8", 5)


def test_drags_de_trim_y_mover_con_undo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = controller.editor
    _open_three_clips(controller, pilot, video)
    before = state.editor_project()
    c1, c2, c3 = before.video.clips
    zoom = state.tl_zoom()

    # recortar el final del clip 2 medio segundo
    pilot.drag(f"trim-r-{c2.id}", dx=-0.5 * zoom, steps=6)
    _settle(pilot)
    trimmed = state.editor_project()
    assert trimmed.clip(c2.id).out_ == pytest.approx(2.0, abs=1 / 30 + 1e-6)
    assert state.undo_label() == "recortar"
    editor.undo()
    assert state.editor_project() == before
    editor.redo()
    assert state.editor_project() == trimmed
    editor.undo()

    # recortar el inicio del clip 1 (el borde sigue al ratón, se cierra el hueco al soltar)
    pilot.drag(f"trim-l-{c1.id}", dx=0.3 * zoom, steps=5)
    _settle(pilot)
    assert state.editor_project().clip(c1.id).in_ == pytest.approx(0.3, abs=1 / 30 + 1e-6)
    assert state.tl_drag() is None
    editor.undo()

    # mover el clip 1 al final
    pilot.drag(f"clip-{c1.id}", dx=3.5 * zoom, steps=8)
    _settle(pilot)
    assert [c.id for c in state.editor_project().video.clips] == [c2.id, c3.id, c1.id]
    assert state.undo_label() == "mover clip"
    editor.undo()
    assert state.editor_project() == before
    _settle(pilot)  # `click` usa los rects del último frame pintado

    # un clic en un clip solo lo selecciona (no apila nada)
    pilot.click(f"clip-{c3.id}")
    _settle(pilot)
    assert state.selection() == ("clip", c3.id)
    assert state.editor_project() == before
    assert state.redo_label() == "mover clip"  # el clic no invalidó el redo


def test_esc_cancela_el_drag(ui, video: Path) -> None:
    _, controller, pilot = ui
    _open_three_clips(controller, pilot, video)
    before = state.editor_project()
    undo_before = state.undo_label()
    c1 = before.video.clips[0]
    rect = pilot.rect(f"clip-{c1.id}")
    cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    pilot.move_to(cx, cy)
    pilot.mouse_down()
    pilot.move_to(cx + 300, cy)
    pilot.move_to(cx + 600, cy)
    assert state.editor_project() != before  # el drag ya reordenó en vivo
    pilot.press("escape")
    assert state.editor_project() == before
    pilot.move_to(cx + 650, cy)  # el resto del gesto se ignora
    pilot.mouse_up()
    _settle(pilot)
    assert state.editor_project() == before
    assert state.undo_label() == undo_before


def test_clic_en_la_regla_mueve_el_playhead(ui, video: Path) -> None:
    _, controller, pilot = ui
    from recstudio.ui.timeline import RULER_H, TL_X0

    _open_three_clips(controller, pilot, video)
    controller.editor.seek(0.0)
    state.snap_on.set(False)
    _settle(pilot)
    zoom, scroll_x = state.tl_zoom(), state.tl_scroll()
    y = pilot.rect("tl-scroll")[1] + RULER_H / 2
    pilot.click_at(TL_X0 + 2.0 * zoom - scroll_x, y)
    _settle(pilot)
    assert state.playhead() == pytest.approx(2.0, abs=1 / 30 + 1e-6)
    state.snap_on.set(True)


def test_teclas_en_un_campo_no_disparan_atajos(ui, video: Path) -> None:
    _, controller, pilot = ui
    _open_three_clips(controller, pilot, video)
    clip = state.editor_project().video.clips[1]
    controller.editor.select_clip(clip.id)
    _settle(pilot)
    count = len(state.editor_project().video.clips)
    pilot.click("insp-in")
    pilot.press("s")
    pilot.press("space")
    _settle(pilot)
    assert len(state.editor_project().video.clips) == count  # «s» se escribió, no dividió
    assert not state.playing()
    # escribir un tiempo válido recorta
    pilot.press("ctrl+a")
    pilot.type("00:01.500\n")
    _settle(pilot)
    assert state.editor_project().clip(clip.id).in_ == pytest.approx(1.5)


def test_guardar_cerrar_y_reabrir_conserva_el_estado(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = controller.editor
    _open_three_clips(controller, pilot, video)
    editor.select_clip(state.editor_project().video.clips[0].id)
    editor.set_speed(2.0)
    editor.set_transition("wipeleft", 0.3)
    project = state.editor_project()
    path = Path(state.project_path())
    editor.save()
    assert _wait(lambda: not state.project_dirty())
    editor.close_project()
    assert _wait(lambda: not state.project_open())
    _settle(pilot)
    assert pilot.rect("empty-library") is not None  # estado vacío con acciones
    editor.open_project(path)
    assert _wait(lambda: state.project_open())
    assert state.editor_project() == project
    assert store_mod.load_project(path).project == project


def test_editar_desde_la_biblioteca_reabre_su_proyecto(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = controller.editor
    recordings = Path(state.out_dir())
    recordings.mkdir(parents=True, exist_ok=True)
    mp4 = recordings / "grabacion.mp4"
    mp4.write_bytes(video.read_bytes())
    editor.open_recording(mp4)
    assert _wait(lambda: state.project_open())
    first = Path(state.project_path())
    assert first.parent.name == "grabacion.rsproj"
    editor.close_project()
    assert _wait(lambda: not state.project_open())
    editor.open_recording(mp4)
    assert _wait(lambda: state.project_open())
    assert Path(state.project_path()) == first  # no crea «grabacion (2)»
    assert state.screen() == "editor"


def test_dibujo_del_timeline_con_100_clips_cabe_en_8_ms(ui, video: Path) -> None:
    """Regresión de rendimiento suave (docs/05): el `draw` del timeline con 100 clips."""
    _, controller, pilot = ui
    editor = controller.editor
    editor.import_paths([video])
    assert _wait(lambda: state.project_open())
    project = state.editor_project()
    asset = project.assets[0]
    for _ in range(99):
        project, _ = tl.append_clip(project, asset)
    editor.store.apply("100 clips", project)
    media.waveform(asset.path)
    state.waveforms.set(state.waveforms() + 1)
    worst = 0.0
    for zoom in (20.0, 60.0, 240.0):  # a 20 px/s se ven los 100 clips con waveform
        best = float("inf")
        for nudge in (0.0, 0.5, 1.0):  # el mejor de 3 redibujados (ruido del SO fuera)
            state.tl_zoom.set(zoom + nudge)
            pilot.frame()
            best = min(best, pilot.stats["draw"])
        worst = max(worst, best)
    assert worst < 8.0, f"draw del timeline: {worst:.2f} ms"
    # mover el playhead no redibuja el canvas
    state.playhead.set(1.0)
    pilot.frame()
    assert pilot.stats["draw"] < 0.5
