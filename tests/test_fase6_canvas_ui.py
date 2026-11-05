"""Feature A (docs/23): mover y redimensionar el clip sobre el canvas del preview.

App real sin ventana (`build_app()` + `pilot`) con un video sintético, como en
`tests/test_fase2_frame_ui.py`.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from recstudio import state
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths


pytestmark = pytest.mark.usefixtures("ui")

HANDLES = ("canvas-handle-tl", "canvas-handle-tr", "canvas-handle-bl", "canvas-handle-br")


def _wait(predicate, timeout: float = 15.0) -> bool:  # noqa: ANN001
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _settle(pilot, frames: int = 4) -> None:
    for _ in range(frames):
        pilot.frame()
    pilot.settle()


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("fase6ui") / "v.mp4"
    try:
        subprocess.run(
            [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "testsrc2=size=640x360:rate=30:duration=6", "-f", "lavfi",
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
        state.preview_area.set(state.PreviewArea())
        state.inspector_open.set(True)
        state.screen.set("record")


def _open(controller, pilot, video: Path):  # noqa: ANN001, ANN202
    editor = controller.editor
    editor.import_paths([video])
    assert _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))
    _settle(pilot)
    return editor


def _select(editor, pilot, *, t: float | None = None):  # noqa: ANN001, ANN202
    clip = state.editor_project().video.clips[0]
    editor.select_clip(clip.id)
    if t is not None:
        editor.seek(t)
    _settle(pilot)
    assert _wait(lambda: state.preview_area().width > 0)
    return clip


def test_las_asas_aparecen_al_seleccionar_y_escalan_con_un_solo_undo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    clip = _select(editor, pilot, t=1.0)
    for key in (*HANDLES, "canvas-body"):
        assert pilot.rect(key) is not None, f"falta {key}"

    before = state.editor_project()
    _wait(lambda: state.preview_frame() != b"")
    frame = state.preview_frame()
    pilot.drag("canvas-handle-br", dx=80, dy=0, steps=8)
    _settle(pilot)
    transform = state.editor_project().clip(clip.id).transform
    assert transform is not None and transform.scale > 1.0
    assert state.undo_label() == "redimensionar clip en el lienzo"
    assert _wait(lambda: state.preview_frame() != frame), "el frame del preview no cambió"

    editor.undo()
    _settle(pilot)
    assert state.editor_project() == before
    assert pilot.rect("canvas-handle-br") is not None


def test_arrastrar_el_cuerpo_mueve_el_clip_y_un_clic_no_crea_undo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    clip = _select(editor, pilot, t=1.0)
    before = state.editor_project()
    pilot.drag("canvas-body", dx=60, dy=30, steps=8)
    _settle(pilot)
    transform = state.editor_project().clip(clip.id).transform
    assert transform is not None
    assert transform.x > 0.5 and transform.y > 0.5
    assert state.undo_label() == "mover clip en el lienzo"

    editor.undo()
    _settle(pilot)
    assert state.editor_project() == before
    label = state.undo_label()
    pilot.click("canvas-body")
    _settle(pilot)
    assert state.editor_project() == before
    assert state.undo_label() == label  # el clic solo selecciona: ni undo ni cambios


def test_escape_cancela_el_gesto_y_restaura_el_proyecto(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    clip = _select(editor, pilot, t=1.0)
    before = state.editor_project()
    label = state.undo_label()

    editor.begin_canvas_drag("move")
    editor.drag_canvas(80, 40)
    _settle(pilot)
    assert state.editor_project() != before
    pilot.press("escape")
    _settle(pilot)
    assert state.editor_project() == before
    assert state.undo_label() == label
    # La caja vuelve a estar disponible para el siguiente gesto.
    assert pilot.rect("canvas-handle-br") is not None
    assert state.editor_project().clip(clip.id).transform is None


def test_funciona_en_9_16_y_con_el_inspector_oculto(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    clip = state.editor_project().video.clips[0]
    editor.set_project_size(1080, 1920)
    editor.select_clip(clip.id)
    editor.seek(1.0)
    pilot.press("p")
    _settle(pilot)
    assert not state.inspector_open()
    assert (state.editor_project().width, state.editor_project().height) == (1080, 1920)
    assert _wait(lambda: state.preview_area().width > 0)
    assert pilot.rect("canvas-handle-br") is not None

    pilot.drag("canvas-handle-br", dx=40, dy=12, steps=6)
    _settle(pilot)
    transform = state.editor_project().clip(clip.id).transform
    assert transform is not None and transform.scale > 1.0
    assert state.undo_label() == "redimensionar clip en el lienzo"


def test_sin_seleccion_o_en_reproduccion_no_hay_capa(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    clip = _select(editor, pilot, t=1.0)
    assert pilot.rect("canvas-body") is not None

    editor.clear_selection()
    _settle(pilot)
    assert pilot.rect("canvas-body") is None
    assert pilot.rect("canvas-handle-br") is None

    editor.select_clip(clip.id)
    _settle(pilot)
    assert pilot.rect("canvas-body") is not None
    editor.toggle_play()
    _settle(pilot)
    assert state.playing()
    assert pilot.rect("canvas-body") is None  # la capa se oculta al reproducir
    editor.toggle_play()
    _settle(pilot)
    assert not state.playing()
    assert _wait(lambda: pilot.rect("canvas-body") is not None)

    # Seleccionar el segundo clip con el playhead en el primero también muestra su caja
    # (la geometría es la del clip seleccionado; el inspector da los valores exactos).
    editor.import_paths([video])
    assert _wait(lambda: len(state.editor_project().video.clips) == 2)
    _settle(pilot)
    second = state.editor_project().video.clips[1]
    editor.seek(1.0)
    editor.select_clip(second.id)
    _settle(pilot)
    assert _wait(lambda: pilot.rect("canvas-body") is not None)
