"""UI del bloque B: música, textos, selección múltiple y reubicar carpeta."""

from __future__ import annotations

import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from recstudio import state
from recstudio.core.project import Clip, Project, Track
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import media
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


def _video(path: Path, *, seconds: float = 4.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc2=size=640x360:rate=30:duration={seconds}", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-preset",
         "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


def _audio(path: Path, *, seconds: float = 3.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=330:duration={seconds}", "-c:a", "pcm_s16le", str(path)],
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
        state.marquee_on.set(False)
        state.marquee.set(None)
        state.screen.set("record")


def test_importar_musica_crea_la_pista_y_su_inspector(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = controller.editor
    editor.import_paths([_video(tmp_path / "v.mp4")])
    assert _wait(lambda: state.project_open() and state.editor_project().video.clips)

    editor.import_music([_audio(tmp_path / "m.wav")])
    assert _wait(lambda: state.editor_project().audio), "no se creó la pista de música"
    _settle(pilot)
    clip = state.editor_project().audio[0].clips[0]
    assert pilot.rect(f"music-{clip.id}") is not None, "no se dibujó la pista M"
    editor.select_music()
    _settle(pilot)
    assert pilot.rect("insp-music-gain") is not None
    assert state.editor_project().audio[0].gain == 1.0


def test_add_text_edita_y_aparece_en_el_inspector(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = controller.editor
    editor.import_paths([_video(tmp_path / "v.mp4")])
    assert _wait(lambda: state.project_open() and state.editor_project().video.clips)
    editor.seek(0.5)

    editor.add_text()
    assert _wait(lambda: bool(state.editor_project().texts)), "no se añadió el texto"
    overlay = state.editor_project().texts[0]
    assert state.selection() == ("text", overlay.id)
    _settle(pilot)
    assert pilot.rect("insp-text") is not None

    editor.set_text_text("Hola B2")
    assert _wait(lambda: state.editor_project().texts[0].text == "Hola B2")
    assert state.insp_text() == "Hola B2"


def test_marquesina_selecciona_y_borra_varios_con_un_solo_undo(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = controller.editor
    editor.import_paths([_video(tmp_path / "v.mp4")])
    assert _wait(lambda: state.project_open() and state.editor_project().video.clips)
    editor.seek(1.0)
    pilot.press("s")
    editor.seek(2.5)
    pilot.press("s")
    _settle(pilot)
    assert len(state.editor_project().video.clips) == 3

    zoom = state.tl_zoom()
    scroll = state.tl_scroll()
    editor.begin_marquee(TL_X0 - scroll)
    editor.drag_marquee(TL_X0 - scroll + 4.0 * zoom)
    editor.end_marquee()

    assert len(state.multi_selection()) == 3
    editor.delete_selected()
    assert state.editor_project().video.clips == ()
    editor.undo()
    assert len(state.editor_project().video.clips) == 3


def test_reubicar_una_carpeta_resuelve_varios_assets(ui, tmp_path: Path) -> None:
    _, controller, _ = ui
    editor = controller.editor
    moved = tmp_path / "movidos"
    moved.mkdir()
    first = _video(moved / "uno.mp4")
    second = _video(moved / "dos.mp4")
    missing1 = replace(media.probe(first), path=str(tmp_path / "faltan" / "uno.mp4"))
    missing2 = replace(media.probe(second), path=str(tmp_path / "faltan" / "dos.mp4"))
    project = Project(
        assets=(missing1, missing2),
        video=Track("video", (Clip("c1", missing1.id, 0, 1), Clip("c2", missing2.id, 0, 1)), (None,)),
        fps=30,
    )
    editor.store.reset(project, None)
    state.project_open.set(True)
    editor._refresh_offline(project)
    assert len(state.offline_assets()) == 2

    assert editor.relocate_folder_to(moved) is True
    assert state.offline_assets() == frozenset()
    assert all(Path(a.path).is_file() for a in state.editor_project().assets)
