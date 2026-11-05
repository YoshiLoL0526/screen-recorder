"""UI de los efectos globales (feature D, docs/26): controles, undo, reset y persistencia."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from recstudio import state
from recstudio.core import global_effects as ge
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths


pytestmark = pytest.mark.usefixtures("ui")


def _wait(predicate, timeout: float = 15.0) -> bool:  # noqa: ANN001
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _settle(pilot, frames: int = 3) -> None:  # noqa: ANN001
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
    editor.clear_selection()  # la sección «Proyecto» (sin selección) es la que edita los globales
    _settle(pilot)
    return editor


def _effect(project, kind: str):  # noqa: ANN001, ANN202
    return next((fx for fx in project.effects if fx.kind == kind), None)


def test_controles_y_undo_por_cada_cambio(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, _video(tmp_path / "v.mp4"))
    for key in ("proj-global-bright", "proj-global-contrast", "proj-global-sat",
                "proj-global-blur", "proj-global-vignette", "proj-global-gray",
                "proj-global-reset"):
        assert pilot.rect(key) is not None, f"falta el control {key}"

    state.glob_blur.set(5.0)
    _settle(pilot)
    project = state.editor_project()
    assert ge.blur_radius(project) == 5.0
    assert _effect(project, "blur") is not None
    assert state.undo_label() == "blur global"
    editor.undo()
    _settle(pilot)
    assert state.editor_project().effects == ()
    assert state.glob_blur() == 0.0

    state.glob_brightness.set(0.1)
    _settle(pilot)
    assert ge.color_eq(state.editor_project())["brightness"] == pytest.approx(0.1)
    assert state.undo_label() == "brillo global"
    state.glob_contrast.set(1.25)
    _settle(pilot)
    assert ge.color_eq(state.editor_project())["contrast"] == pytest.approx(1.25)
    assert state.undo_label() == "contraste global"
    editor.undo()  # un solo paso: vuelve el contraste, el brillo sigue
    _settle(pilot)
    eq = ge.color_eq(state.editor_project())
    assert eq["brightness"] == pytest.approx(0.1) and eq["contrast"] == pytest.approx(1.0)
    assert state.glob_contrast() == pytest.approx(1.0)

    state.glob_vignette.set(0.5)
    _settle(pilot)
    assert ge.vignette_strength(state.editor_project()) == pytest.approx(0.5)
    assert state.undo_label() == "viñeta global"
    state.glob_grayscale.set(True)
    _settle(pilot)
    assert ge.grayscale_on(state.editor_project())
    assert state.undo_label() == "blanco y negro"
    editor.undo()
    _settle(pilot)
    assert not ge.grayscale_on(state.editor_project())
    assert state.glob_grayscale() is False


def test_reset_y_undo_en_un_paso(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, _video(tmp_path / "v.mp4"))
    state.glob_blur.set(10.0)
    state.glob_vignette.set(0.25)
    state.glob_brightness.set(-0.1)
    state.glob_grayscale.set(True)
    _settle(pilot)
    before = state.editor_project()
    assert len(before.effects) == 4

    editor.reset_global_effects()
    _settle(pilot)
    assert state.editor_project().effects == ()
    assert state.undo_label() == "restablecer efectos globales"
    assert state.glob_blur() == 0.0 and state.glob_vignette() == 0.0
    assert state.glob_brightness() == 0.0 and state.glob_grayscale() is False

    editor.undo()
    _settle(pilot)
    assert state.editor_project().effects == before.effects
    assert ge.blur_radius(state.editor_project()) == 10.0
    assert state.glob_grayscale() is True


def test_guardar_y_reabrir_conserva_los_efectos(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, _video(tmp_path / "v.mp4"))
    state.glob_blur.set(5.0)
    state.glob_vignette.set(0.25)
    state.glob_contrast.set(1.5)
    state.glob_grayscale.set(True)
    _settle(pilot)
    project = state.editor_project()
    assert {fx.kind for fx in project.effects} == {"blur", "vignette", "eq", "grayscale"}

    path = Path(state.project_path())
    editor.save()
    assert _wait(lambda: not state.project_dirty())
    editor.close_project()
    assert _wait(lambda: not state.project_open())
    editor.open_project(path)
    assert _wait(lambda: state.project_open() and state.editor_project().effects == project.effects)
    reopened = state.editor_project()
    assert ge.blur_radius(reopened) == 5.0
    assert ge.vignette_strength(reopened) == pytest.approx(0.25)
    assert ge.color_eq(reopened)["contrast"] == pytest.approx(1.5)
    assert ge.grayscale_on(reopened)
    assert state.glob_blur() == 5.0 and state.glob_grayscale() is True
