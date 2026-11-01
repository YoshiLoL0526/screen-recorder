"""UI de la fase 3 (docs/21): atajos y modos con la app real sin ventana (`build_app` + `pilot`)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from recstudio import state
from recstudio.core import timeline as tl
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.ui.timeline import TL_X0

pytestmark = pytest.mark.usefixtures("ui")


def _wait(predicate, timeout: float = 10.0) -> bool:  # noqa: ANN001
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _settle(pilot, frames: int = 3) -> None:
    for _ in range(frames):
        pilot.frame()
    pilot.settle()


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("fase3ui") / "v.mp4"
    try:
        subprocess.run(
            [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "testsrc2=size=320x180:rate=30:duration=6", "-f", "lavfi",
             "-i", "sine=frequency=440:duration=6", "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
            check=True, capture_output=True,
        )
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")
    return path


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
        state.multi_selection.set(frozenset())
        state.marquee_on.set(False)
        state.razor_on.set(False)
        state.loop_on.set(False)
        state.play_rate.set(1.0)
        state.marquee.set(None)
        state.export_open.set(False)
        state.export_range.set("all")
        state.screen.set("record")


def _three_clips(controller, pilot, video: Path):  # noqa: ANN001, ANN202
    """Abre el video (6 s) y lo divide en 0–2 · 2–4 · 4–6."""
    editor = controller.editor
    editor.import_paths([video])
    assert _wait(lambda: state.project_open() and state.editor_project().video.clips)
    editor.seek(2.0)
    pilot.press("s")
    editor.seek(4.0)
    pilot.press("s")
    _settle(pilot)
    assert len(state.editor_project().video.clips) == 3
    return editor


def _x(t: float) -> float:
    """x de ventana de un instante del timeline."""
    return TL_X0 - state.tl_scroll() + t * state.tl_zoom()


def test_copiar_pegar_y_duplicar_con_atajos_y_un_solo_undo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _three_clips(controller, pilot, video)
    first = state.editor_project().video.clips[0]
    editor.select_clip(first.id)
    pilot.press("ctrl+c")
    assert state.clipboard_count() == 1
    editor.seek(6.0)
    pilot.press("ctrl+v")
    _settle(pilot)
    project = state.editor_project()
    assert len(project.video.clips) == 4 and state.undo_label() == "pegar"
    pasted = project.video.clips[3]
    assert pasted.id != first.id and (pasted.in_, pasted.out_) == (first.in_, first.out_)
    assert state.selection() == ("clip", pasted.id)

    pilot.press("ctrl+d")  # duplica lo pegado justo detrás
    _settle(pilot)
    assert len(state.editor_project().video.clips) == 5 and state.undo_label() == "duplicar"
    editor.undo()
    editor.undo()
    assert len(state.editor_project().video.clips) == 3

    editor.select_clip(first.id)
    pilot.press("ctrl+x")  # corta el clip seleccionado
    _settle(pilot)
    assert len(state.editor_project().video.clips) == 2
    editor.seek(0.0)
    pilot.press("ctrl+v")
    _settle(pilot)
    assert len(state.editor_project().video.clips) == 3 and state.editor_project().video.clips[0].in_ == first.in_


def test_marquesina_y_mover_el_bloque_con_un_solo_undo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _three_clips(controller, pilot, video)
    c1, c2, c3 = state.editor_project().video.clips
    pilot.click("tl-marquee")
    assert state.marquee_on()
    editor.begin_marquee(_x(0.1))
    editor.drag_marquee(_x(3.9))
    editor.end_marquee()
    assert state.multi_selection() == {c1.id, c2.id}
    pilot.click("tl-marquee")  # fuera de la marquesina: arrastrar mueve
    _settle(pilot)
    before = state.editor_project()
    pilot.drag(f"clip-{c1.id}", dx=2.2 * state.tl_zoom(), steps=8)
    _settle(pilot)
    assert [c.id for c in state.editor_project().video.clips] == [c3.id, c1.id, c2.id]
    assert state.undo_label() == "mover selección"
    editor.undo()
    assert state.editor_project() == before
    # Con la pista bloqueada el bloque no se mueve.
    editor.toggle_video_lock()
    pilot.drag(f"clip-{c1.id}", dx=2.2 * state.tl_zoom(), steps=8)
    _settle(pilot)
    assert [c.id for c in state.editor_project().video.clips] == [c1.id, c2.id, c3.id]


def test_razor_es_un_modo_excluyente_que_corta_donde_se_pulsa(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _three_clips(controller, pilot, video)
    pilot.click("tl-marquee")
    pilot.press("c")
    _settle(pilot)
    assert state.razor_on() and not state.marquee_on()
    clip = state.editor_project().video.clips[1]
    rect = pilot.rect(f"clip-{clip.id}")
    assert rect is not None and pilot.rect(f"trim-l-{clip.id}") is None  # sin asas en modo razor
    pilot.click_at(_x(3.0), rect[1] + rect[3] / 2)
    _settle(pilot)
    project = state.editor_project()
    assert len(project.video.clips) == 4 and state.undo_label() == "cortar con razor"
    assert tl.clip_starts(project.video)[2] == pytest.approx(3.0)
    pilot.click("tl-marquee")
    assert state.marquee_on() and not state.razor_on()
    pilot.press("v")
    assert not state.marquee_on() and not state.razor_on()
    editor.undo()
    assert len(state.editor_project().video.clips) == 3


def test_marcadores_con_b_navegar_renombrar_y_borrar(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _three_clips(controller, pilot, video)
    editor.seek(1.0)
    pilot.press("b")
    editor.seek(3.5)
    pilot.press("b")
    _settle(pilot)
    markers = state.editor_project().markers
    assert [m.t for m in markers] == [pytest.approx(1.0), pytest.approx(3.5)]
    assert pilot.rect(f"marker-{markers[0].id}") is not None
    editor.seek(5.0)
    pilot.press("up")
    assert state.playhead() == pytest.approx(3.5) and state.selection() == ("marker", markers[1].id)
    pilot.press("up")
    assert state.playhead() == pytest.approx(1.0)
    pilot.press("down")
    assert state.playhead() == pytest.approx(3.5)
    _settle(pilot)
    assert pilot.rect("insp-marker-name") is not None
    editor.rename_marker("Clímax")
    assert state.editor_project().marker(markers[1].id).name == "Clímax"
    pilot.click(f"marker-{markers[0].id}")
    assert state.playhead() == pytest.approx(1.0)
    pilot.press("delete")
    assert [m.id for m in state.editor_project().markers] == [markers[1].id]
    # El imán engancha al marcador al arrastrar el playhead por la regla.
    editor.scrub_to_window_x(_x(3.52))
    assert state.playhead() == pytest.approx(3.5)


def test_in_out_loop_e_inspector_con_p(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _three_clips(controller, pilot, video)
    editor.seek(1.0)
    pilot.press("i")
    editor.seek(2.5)
    pilot.press("o")
    _settle(pilot)
    project = state.editor_project()
    assert (project.range_in, project.range_out) == (pytest.approx(1.0), pytest.approx(2.5))
    assert state.inspector_open()  # la I ya no oculta el inspector
    assert pilot.rect("tl-range") is not None
    pilot.press("shift+i")
    assert state.playhead() == pytest.approx(1.0)
    pilot.press("ctrl+l")
    assert state.loop_on() and editor.player.loop == (pytest.approx(1.0), pytest.approx(2.5))
    pilot.click("tp-loop")
    assert not state.loop_on() and editor.player.loop is None
    pilot.press("alt+x")
    assert not tl.has_range(state.editor_project())
    pilot.press("p")
    _settle(pilot)
    assert not state.inspector_open()


def test_jkl_q_y_w(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _three_clips(controller, pilot, video)
    editor.seek(1.0)
    pilot.press("l")
    assert editor.player.playing and state.play_rate() == 1.0
    pilot.press("l")
    assert state.play_rate() == 2.0
    pilot.press("j")
    assert state.play_rate() == -1.0
    pilot.press("k")
    assert not editor.player.playing and state.play_rate() == 1.0

    editor.seek(2.5)  # dentro del clip 2 (2–4)
    pilot.press("q")
    project = state.editor_project()
    assert tl.clip_range(project, 1) == (pytest.approx(2.0), pytest.approx(3.5))  # ripple
    assert state.playhead() == pytest.approx(2.0) and state.undo_label() == "recortar inicio"
    editor.seek(3.0)
    pilot.press("w")
    assert tl.clip_range(state.editor_project(), 1) == (pytest.approx(2.0), pytest.approx(3.0))
    assert tl.total_duration(state.editor_project()) == pytest.approx(5.0)


def test_exportar_solo_el_rango_desde_el_modal(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _three_clips(controller, pilot, video)
    pilot.press("ctrl+e")
    _settle(pilot)
    assert pilot.rect("export-range-hint") is not None  # sin In/Out solo se explica
    editor.close_export()
    editor.seek(1.0)
    pilot.press("i")
    editor.seek(2.0)
    pilot.press("o")
    pilot.press("ctrl+e")
    _settle(pilot)
    assert state.export_range() == "range" and pilot.rect("export-range-range") is not None
    assert editor.export_span() == (pytest.approx(1.0), pytest.approx(2.0))
    pilot.click("export-range-all")
    assert editor.export_span() is None
    pilot.click("export-range-range")
    pilot.click("export-start")
    assert _wait(lambda: state.export_progress() is not None and state.export_progress().state == "done", 120)
    info = F.probe_media(Path(state.export_progress().out_path))
    assert info.duration_s == pytest.approx(1.0, abs=0.07)
