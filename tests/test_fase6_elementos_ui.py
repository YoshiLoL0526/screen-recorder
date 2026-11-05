"""UI de la feature C (docs/25): elementos, imágenes, undo y caja del preview."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image

from recstudio import state
from recstudio.core import elements as el
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths


pytestmark = pytest.mark.usefixtures("ui")


def _wait(predicate, timeout: float = 20.0) -> bool:  # noqa: ANN001
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


def _video(path: Path, *, seconds: float = 3.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc2=size=640x360:rate=30:duration={seconds}", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-preset",
         "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )
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
        state.screen.set("record")


def _open(controller, pilot, video: Path):  # noqa: ANN001, ANN202
    editor = controller.editor
    editor.import_paths([video])
    assert _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))
    editor.clear_selection()
    _settle(pilot)
    return editor


def _click_inspector(pilot, key: str) -> None:
    """El inspector hace scroll; los botones de Elementos quedan debajo del pliegue."""
    pilot.scroll("insp-scroll", dy=2000)
    pilot.settle()
    pilot.click(key)
    _settle(pilot)


def test_inspector_elementos_editar_undo_zorder_y_guardar(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, _video(tmp_path / "v.mp4"))
    for key in ("insp-el-rect", "insp-el-ellipse", "insp-el-triangle", "insp-el-image"):
        assert pilot.rect(key) is not None, f"falta el control {key}"

    _click_inspector(pilot, "insp-el-rect")
    project = state.editor_project()
    assert len(project.elements) == 1
    rect = project.elements[0]
    assert state.selection() == ("element", rect.id)
    for key in ("insp-el-start", "insp-el-end", "insp-el-x", "insp-el-y", "insp-el-width",
                "insp-el-height", "insp-el-rotation", "insp-el-opacity", "insp-el-color",
                "insp-el-blur", "insp-el-delete"):
        assert pilot.rect(key) is not None, f"falta el control {key}"
    assert pilot.rect("insp-el-fit") is None  # una figura no tiene encaje de imagen

    state.insp_el_color.set("#00FF00")
    state.insp_el_opacity.set(0.5)
    state.insp_el_blur.set(8.0)
    _settle(pilot)
    current = state.editor_project().element(rect.id)
    assert current.color == "#00FF00" and current.opacity == 0.5
    assert el.blur_radius(current) == 8.0
    assert state.undo_label() == "desenfoque del elemento"
    editor.undo()
    _settle(pilot)
    assert el.blur_radius(state.editor_project().element(rect.id)) == 0.0
    assert state.editor_project().element(rect.id).color == "#00FF00"
    editor.undo()
    _settle(pilot)
    assert state.editor_project().element(rect.id).opacity == 1.0
    editor.undo()
    _settle(pilot)
    assert state.editor_project().element(rect.id).color == "#FF5470"

    editor.clear_selection()
    _settle(pilot)
    _click_inspector(pilot, "insp-el-ellipse")
    project = state.editor_project()
    assert len(project.elements) == 2
    first, second = project.elements
    assert first.id == rect.id and second.kind == "ellipse"
    editor.select_element(first.id)
    _settle(pilot)
    _click_inspector(pilot, "insp-el-back")
    assert [e.id for e in state.editor_project().elements] == [first.id, second.id]
    # `first` ya estaba al fondo: el clic no crea una entrada nueva de undo
    editor.select_element(second.id)
    _settle(pilot)
    _click_inspector(pilot, "insp-el-back")
    assert [e.id for e in state.editor_project().elements] == [second.id, first.id]
    _click_inspector(pilot, "insp-el-front")
    assert [e.id for e in state.editor_project().elements] == [first.id, second.id]

    _click_inspector(pilot, "insp-el-delete")
    assert len(state.editor_project().elements) == 1
    editor.undo()
    _settle(pilot)
    assert len(state.editor_project().elements) == 2

    project = state.editor_project()
    path = Path(state.project_path())
    editor.save()
    assert _wait(lambda: not state.project_dirty())
    editor.close_project()
    assert _wait(lambda: not state.project_open())
    editor.open_project(path)
    assert _wait(lambda: state.project_open() and len(state.editor_project().elements) == 2)
    assert state.editor_project() == project


def test_importar_imagen_y_encaje(ui, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, _video(tmp_path / "v.mp4"))
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (120, 80), (0, 255, 0, 200)).save(logo)

    from recstudio.ui import file_dialogs

    monkeypatch.setattr(file_dialogs, "open_images", lambda *_a, **_k: [logo])
    clips_before = len(state.editor_project().video.clips)
    pilot.click("ed-image")
    assert _wait(
        lambda: any(a.kind == "image" for a in state.editor_project().assets)
        and any(e.kind == "image" for e in state.editor_project().elements)
    )
    _settle(pilot)
    project = state.editor_project()
    assert len(project.video.clips) == clips_before  # una imagen no se convierte en clip
    image_element = next(e for e in project.elements if e.kind == "image")
    assert state.selection() == ("element", image_element.id)
    assert pilot.rect("insp-el-fit") is not None
    state.insp_el_fit.set("fill")
    _settle(pilot)
    assert state.editor_project().element(image_element.id).fit == "fill"
    assert state.editor_project().asset(image_element.asset_id).kind == "image"
    assert state.undo_label() == "encaje del elemento"
    editor.undo()
    _settle(pilot)
    assert state.editor_project().element(image_element.id).fit == "fit"


def test_caja_del_preview_mueve_el_elemento_y_undo(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, _video(tmp_path / "v.mp4"))
    _click_inspector(pilot, "insp-el-rect")
    assert _wait(lambda: state.preview_box() != (0.0, 0.0), 10)
    _settle(pilot, 6)
    assert pilot.rect("el-box") is not None
    element = state.editor_project().elements[0]
    before = (element.x, element.y)
    pilot.drag("el-box", dx=50, dy=25, steps=6)
    _settle(pilot)
    after = state.editor_project().element(element.id)
    assert (after.x, after.y) != before
    assert after.x > before[0] and after.y > before[1]
    assert state.undo_label() == "mover elemento"
    editor.undo()
    _settle(pilot)
    restored = state.editor_project().element(element.id)
    assert (restored.x, restored.y) == pytest.approx(before)
