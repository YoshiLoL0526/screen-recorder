"""UI de la fase 2 (docs/20): ajustes de lienzo, transformación del clip y reapertura."""

from __future__ import annotations

import io
import subprocess
import time
from pathlib import Path

import pytest

from recstudio import state
from recstudio.core import timeline as tl
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import playback


pytestmark = pytest.mark.usefixtures("ui")


def _wait(predicate, timeout: float = 15.0) -> bool:  # noqa: ANN001
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


def _video(path: Path, *, seconds: float = 4.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc2=size=640x360:rate=30:duration={seconds}", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-preset",
         "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


def _preview_size() -> tuple[int, int]:
    from PIL import Image

    data = state.preview_frame()
    if not data:
        return (0, 0)
    return Image.open(io.BytesIO(data)).size


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
    _settle(pilot)
    return editor


def test_lienzo_presets_personalizado_fit_fondo_y_preview(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, _video(tmp_path / "v.mp4"))
    project = state.editor_project()
    clips = project.video.clips
    _settle(pilot)
    for key in ("proj-canvas", "proj-width", "proj-height", "proj-fit", "proj-bg",
                "proj-bg-hex", "insp-fps"):
        assert pilot.rect(key) is not None, f"falta el control {key}"

    from recstudio.ui.editor_controller import CANVAS_SIZES

    for key, (width, height) in CANVAS_SIZES.items():
        state.proj_canvas.set(key)
        _settle(pilot)
        current = state.editor_project()
        assert (current.width, current.height) == (width, height)
        assert current.video.clips == clips  # el lienzo no mueve ni recorta clips
        assert state.undo_label() == "lienzo del proyecto"
    assert _wait(lambda: _preview_size() == playback.preview_size(state.editor_project()))

    state.proj_fit.set("fill")
    _settle(pilot)
    assert state.editor_project().default_fit == "fill"
    state.proj_background.set("#204060")
    _settle(pilot)
    assert state.editor_project().background == "#204060"
    assert state.proj_bg_hex() == "#204060"

    state.proj_width.set("1280")
    state.proj_height.set("720")
    editor.submit_project_size(state.proj_width(), state.proj_height())
    _settle(pilot)
    assert (state.editor_project().width, state.editor_project().height) == (1280, 720)
    assert state.proj_canvas() == "custom"
    assert _wait(lambda: _preview_size() == playback.preview_size(state.editor_project()))

    before = state.editor_project()
    editor.submit_project_size("0", "720")
    _settle(pilot)
    assert state.editor_project() == before  # tamaño inválido: no cambia nada
    editor.undo()
    _settle(pilot)
    assert (state.editor_project().width, state.editor_project().height) == (1080, 1350)


def test_transformacion_desde_el_inspector_undo_y_crop(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, _video(tmp_path / "v.mp4"))
    clip = state.editor_project().video.clips[0]
    editor.select_clip(clip.id)
    _settle(pilot)
    for key in ("insp-fit", "insp-scale", "insp-pos-x", "insp-pos-y", "insp-rotation",
                "insp-flip-h", "insp-flip-v", "insp-crop-l", "insp-crop-t", "insp-crop-r",
                "insp-crop-b", "insp-transform-reset"):
        assert pilot.rect(key) is not None, f"falta el control {key}"

    before = state.editor_project()
    state.insp_fit.set("fill")
    _settle(pilot)
    transform = state.editor_project().clip(clip.id).transform
    assert transform is not None and transform.fit == "fill"
    assert state.undo_label() == "transformación"
    editor.undo()
    _settle(pilot)
    assert state.editor_project() == before
    assert state.insp_fit() == "inherit"

    state.insp_scale.set(1.5)
    state.insp_pos_x.set(0.25)
    state.insp_pos_y.set(0.75)
    state.insp_rotation.set(90)
    state.insp_flip_h.set(True)
    _settle(pilot)
    transform = state.editor_project().clip(clip.id).transform
    assert transform is not None
    assert (transform.scale, transform.x, transform.y) == (1.5, 0.25, 0.75)
    assert (transform.rotation, transform.flip_h) == (90, True)

    editor.submit_transform_crop(0, "0.10")
    _settle(pilot)
    assert state.editor_project().clip(clip.id).transform.crop[0] == pytest.approx(0.10)
    before_invalid = state.editor_project()
    editor.submit_transform_crop(2, "0.05")  # r < l: inválido
    _settle(pilot)
    assert state.editor_project() == before_invalid

    editor.reset_transform()
    _settle(pilot)
    assert state.editor_project().clip(clip.id).transform is None
    assert state.undo_label() == "restablecer transformación"
    assert state.insp_fit() == "inherit" and state.insp_rotation() == 0


def test_guardar_y_reabrir_lienzo_y_transformacion(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, _video(tmp_path / "v.mp4"))
    clip = state.editor_project().video.clips[0]
    editor.set_project_size(1080, 1920)
    editor.set_project_fit("fill")
    editor.set_project_background("#204060")
    editor.select_clip(clip.id)
    editor.set_transform(fit="stretch", scale=0.5, x=0.25, y=0.75, rotation=270,
                         flip_v=True, crop=(0.0, 0.0, 0.5, 1.0))
    _settle(pilot)
    project = state.editor_project()
    path = Path(state.project_path())
    editor.save()
    assert _wait(lambda: not state.project_dirty())
    editor.close_project()
    assert _wait(lambda: not state.project_open())
    editor.open_project(path)
    assert _wait(lambda: state.project_open())
    assert state.editor_project() == project
    assert state.editor_project().background == "#204060"
    assert state.editor_project().default_fit == "fill"
    assert state.editor_project().video.clips[0].transform.rotation == 270
