"""UI de la fase 1 (docs/19): separar, mover/recortar, dividir, pistas y cabeceras."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from recstudio import state
from recstudio.core import timeline as tl
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths


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


def _open(controller, pilot, video: Path):
    editor = controller.editor
    editor.import_paths([video])
    assert _wait(lambda: state.project_open() and state.editor_project().video.clips)
    _settle(pilot)
    return editor


def test_separar_audio_desde_el_inspector_y_deshacer_en_un_paso(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, _video(tmp_path / "v.mp4"))
    before = state.editor_project()
    editor.select_clip(state.editor_project().video.clips[0].id)
    _settle(pilot)
    assert pilot.rect("insp-detach") is not None
    pilot.click("insp-detach")
    _settle(pilot)
    project = state.editor_project()
    assert project.video.clips[0].audio_enabled is False
    assert project.audio[0].mode == "absolute" and project.audio[0].clips[0].link_id
    assert state.selection()[0] == "audio"
    assert state.undo_label() == "separar audio"
    assert pilot.rect("insp-audio-start") is not None

    editor.undo()
    assert state.editor_project() == before


def test_mover_recortar_y_borrar_audio_con_drags_y_undo(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, _video(tmp_path / "v.mp4"))
    editor.select_clip(state.editor_project().video.clips[0].id)
    editor.detach_audio()
    _settle(pilot)
    project = state.editor_project()
    audio_id = project.audio[0].clips[0].id
    assert pilot.rect(f"audio-{audio_id}") is not None
    editor.set_zoom(100.0)
    _settle(pilot)

    zoom = state.tl_zoom()
    pilot.drag(f"audio-{audio_id}", dx=1.0 * zoom, steps=6)
    _settle(pilot)
    assert state.editor_project().audio[0].clips[0].start == pytest.approx(1.0, abs=1 / 30 + 1e-6)
    assert state.undo_label() == "mover audio"
    editor.undo()
    assert state.editor_project().audio[0].clips[0].start == pytest.approx(0.0)
    _settle(pilot)

    before_trim = state.editor_project().audio[0].clips[0]
    pilot.drag(f"audio-trim-r-{audio_id}", dx=-0.5 * zoom, steps=6)
    _settle(pilot)
    trimmed = state.editor_project().audio[0].clips[0]
    assert trimmed.duration == pytest.approx(before_trim.duration - 0.5, abs=1 / 30 + 1e-6)
    assert state.undo_label() == "recortar audio"
    editor.undo()
    _settle(pilot)

    # asa izquierda en absoluto: mueve el inicio y deja fijo el borde derecho
    left_trim = state.editor_project().audio[0].clips[0]
    pilot.drag(f"audio-trim-l-{audio_id}", dx=0.5 * zoom, steps=6)
    _settle(pilot)
    shifted = state.editor_project().audio[0].clips[0]
    assert shifted.start == pytest.approx(0.5, abs=1 / 30 + 1e-6)
    assert shifted.duration == pytest.approx(left_trim.duration - 0.5, abs=1 / 30 + 1e-6)
    assert shifted.start + shifted.duration == pytest.approx(left_trim.duration)
    editor.undo()
    _settle(pilot)

    pilot.press("delete")
    _settle(pilot)
    assert state.editor_project().audio[0].clips == ()
    editor.undo()
    assert len(state.editor_project().audio[0].clips) == 1


def test_dividir_todo_y_dividir_solo_audio(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, _video(tmp_path / "v.mp4"))
    editor.select_clip(state.editor_project().video.clips[0].id)
    editor.detach_audio()
    editor.seek(2.0)
    _settle(pilot)

    pilot.press("s")
    _settle(pilot)
    project = state.editor_project()
    assert len(project.video.clips) == 2
    assert len(project.audio[0].clips) == 2
    assert project.audio[0].clips[1].link_id == project.video.clips[1].id
    assert state.undo_label() == "dividir"

    editor.seek(3.0)
    pilot.press("shift+s")
    _settle(pilot)
    project = state.editor_project()
    assert len(project.video.clips) == 2  # el video no cambia
    assert len(project.audio[0].clips) == 3
    assert state.undo_label() == "dividir audio"


def test_varias_pistas_y_cabeceras_mute_solo_lock(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, _video(tmp_path / "v.mp4"))
    editor.select_clip(state.editor_project().video.clips[0].id)
    editor.detach_audio()
    editor.import_music([_audio(tmp_path / "m.wav")])
    assert _wait(lambda: len(state.editor_project().audio) == 2)
    _settle(pilot)
    project = state.editor_project()
    assert project.audio[0].mode == "absolute"
    assert project.audio[1].mode == "sequence" and project.audio[1].name == "Música"
    for track in project.audio:
        clip = track.clips[0]
        assert pilot.rect(f"audio-{clip.id}") is not None, "no se dibujó una pista de audio"

    pilot.click("trk-solo-1")
    _settle(pilot)
    assert state.editor_project().audio[1].solo is True
    pilot.click("trk-mute-0")
    _settle(pilot)
    assert state.editor_project().audio[0].muted is True
    pilot.click("trk-lock-0")
    _settle(pilot)
    assert state.editor_project().audio[0].locked is True

    locked_clip = state.editor_project().audio[0].clips[0]
    before = state.editor_project()
    pilot.drag(f"audio-{locked_clip.id}", dx=1.0 * state.tl_zoom(), steps=4)
    _settle(pilot)
    assert state.editor_project() == before
    state.selection.set(("audio", 0, 0))
    editor.delete_selected()
    assert state.editor_project() == before


def test_anadir_y_quitar_pista_desde_la_barra(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    _open(controller, pilot, _video(tmp_path / "v.mp4"))
    assert len(state.editor_project().audio) == 0
    pilot.click("tl-track-add")
    _settle(pilot)
    assert len(state.editor_project().audio) == 1
    assert state.editor_project().audio[0].mode == "absolute"

    editor = controller.editor
    editor.select_clip(state.editor_project().video.clips[0].id)
    editor.detach_audio()
    _settle(pilot)
    pilot.click("tl-track-remove")
    _settle(pilot)
    # la pista del clip seleccionado (Audio del video) se quita; queda la pista vacía
    assert all(track.name != "Audio del video" for track in state.editor_project().audio)


def test_limite_de_pistas_de_audio(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, _video(tmp_path / "v.mp4"))
    for _ in range(8):
        editor.add_audio_track()
    _settle(pilot)
    assert len(state.editor_project().audio) == 6
    # la barra crece con las pistas y no hay scroll vertical anidado (alternativa documentada)
    assert pilot.rect("tl-vscroll") is None
    assert pilot.rect("tl-content") is not None


def test_guardar_y_reabrir_conserva_audio_pistas_y_ajustes(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, _video(tmp_path / "v.mp4"))
    editor.select_clip(state.editor_project().video.clips[0].id)
    editor.detach_audio()
    editor.set_audio_effect("audio_fade_in", "fundido", seconds=0.5)
    editor.toggle_audio_solo(0)
    editor.toggle_audio_lock(0)
    editor.set_audio_track_gain(0, 0.5)
    project = state.editor_project()
    path = Path(state.project_path())
    editor.save()
    assert _wait(lambda: not state.project_dirty())
    editor.close_project()
    assert _wait(lambda: not state.project_open())
    editor.open_project(path)
    assert _wait(lambda: state.project_open())
    assert state.editor_project() == project
