"""UI de la feature B (docs/22/docs/24): import con audio separado y enlace A/V con `pilot`."""

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


def _video(path: Path, *, seconds: float = 6.0, audio: bool = True) -> Path:
    cmd = [
        F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30:duration={seconds}",
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-shortest"] if audio else ["-an"]
    subprocess.run([*cmd, str(path)], check=True, capture_output=True)
    return path


@pytest.fixture(scope="module")
def media(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    folder = tmp_path_factory.mktemp("fase6ui")
    try:
        return _video(folder / "con_audio.mp4"), _video(folder / "mudo.mp4", audio=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")


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
        state.marquee.set(None)
        state.screen.set("record")


def _open(controller, pilot, video: Path):  # noqa: ANN001, ANN202
    editor = controller.editor
    editor.import_paths([video])
    assert _wait(lambda: state.project_open() and state.editor_project().video.clips)
    _settle(pilot)
    return editor


def test_importar_video_con_audio_crea_la_pista_enlazada(ui, media: tuple[Path, Path]) -> None:
    _, controller, pilot = ui
    video, mudo = media
    editor = _open(controller, pilot, video)
    project = state.editor_project()

    assert len(project.audio) == 1
    track = project.audio[0]
    assert (track.mode, track.name) == ("absolute", "Audio del video")
    assert all(not clip.audio_enabled for clip in project.video.clips)
    audio = track.clips[0]
    assert audio.link_id == project.video.clips[0].id
    assert (audio.start, audio.in_, audio.out_) == (0.0, 0.0, 6.0)
    assert pilot.rect(f"audio-{audio.id}") is not None

    # Un video mudo añadido después no crea clips de audio.
    editor.import_paths([mudo])
    assert _wait(lambda: len(state.editor_project().video.clips) == 2)
    project = state.editor_project()
    assert len(project.audio[0].clips) == 1
    assert project.video.clips[1].audio_enabled is True
    assert state.undo_label() == "importar"

    # Importar otro video con audio reutiliza la misma pista y es un solo undo.
    before = project
    editor.import_paths([video])
    assert _wait(lambda: len(state.editor_project().video.clips) == 3)
    project = state.editor_project()
    assert len(project.audio) == 1
    assert len(project.audio[0].clips) == 2
    assert state.undo_label() == "importar"
    editor.undo()
    assert state.editor_project() == before


def test_arrastrar_video_mueve_el_audio_y_es_un_solo_undo(ui, media: tuple[Path, Path]) -> None:
    _, controller, pilot = ui
    video, _ = media
    editor = _open(controller, pilot, video)
    editor.seek(2.0)
    pilot.press("s")
    editor.seek(4.0)
    pilot.press("s")
    _settle(pilot)
    before = state.editor_project()
    assert len(before.video.clips) == 3 and len(before.audio[0].clips) == 3

    c1, c2, c3 = before.video.clips
    zoom = state.tl_zoom()
    pilot.drag(f"clip-{c1.id}", dx=4.5 * zoom, steps=8)
    _settle(pilot)
    project = state.editor_project()
    assert [c.id for c in project.video.clips] == [c2.id, c3.id, c1.id]
    assert state.undo_label() == "mover clip"
    starts = tl.clip_starts(project.video)
    by_link = {clip.link_id: clip for clip in project.audio[0].clips}
    assert by_link[c2.id].start == pytest.approx(starts[0])
    assert by_link[c3.id].start == pytest.approx(starts[1])
    assert by_link[c1.id].start == pytest.approx(starts[2])
    assert state.editor_project().validate() == project

    editor.undo()
    assert state.editor_project() == before


def test_recortar_video_ajusta_el_audio_y_deshace_en_un_paso(ui, media: tuple[Path, Path]) -> None:
    _, controller, pilot = ui
    video, _ = media
    editor = _open(controller, pilot, video)
    before = state.editor_project()
    clip = before.video.clips[0]
    audio_id = before.audio[0].clips[0].id
    zoom = state.tl_zoom()

    pilot.drag(f"trim-r-{clip.id}", dx=-1.0 * zoom, steps=6)
    _settle(pilot)
    trimmed = state.editor_project()
    video_clip = trimmed.clip(clip.id)
    audio = trimmed.audio[0].clips[0]
    assert video_clip.out_ == pytest.approx(5.0, abs=1 / 30 + 1e-6)
    assert (audio.start, audio.in_, audio.out_) == (0.0, 0.0, 5.0)
    assert audio.duration == pytest.approx(video_clip.duration)
    assert state.undo_label() == "recortar"
    editor.undo()
    assert state.editor_project() == before

    pilot.drag(f"trim-l-{clip.id}", dx=0.5 * zoom, steps=6)
    _settle(pilot)
    left = state.editor_project()
    video_clip = left.clip(clip.id)
    audio = left.audio[0].clips[0]
    assert video_clip.in_ == pytest.approx(0.5, abs=1 / 30 + 1e-6)
    assert (audio.start, audio.in_, audio.out_) == (0.0, 0.5, 6.0)
    assert audio.duration == pytest.approx(video_clip.duration)
    editor.undo()
    assert state.editor_project() == before


def test_desvincular_desde_los_inspectores_y_deshacer(ui, media: tuple[Path, Path]) -> None:
    _, controller, pilot = ui
    video, _ = media
    editor = _open(controller, pilot, video)
    before = state.editor_project()
    editor.select_clip(before.video.clips[0].id)
    _settle(pilot)
    assert pilot.rect("insp-unlink") is not None
    pilot.click("insp-unlink")
    _settle(pilot)
    project = state.editor_project()
    assert [c.link_id for c in project.audio[0].clips] == [None]
    assert project.video.clips[0].audio_enabled is False
    assert state.undo_label() == "desvincular audio"
    editor.undo()
    assert state.editor_project() == before

    editor.select_audio(0, 0)
    _settle(pilot)
    assert pilot.rect("insp-audio-unlink") is not None
    pilot.click("insp-audio-unlink")
    _settle(pilot)
    project = state.editor_project()
    assert project.audio[0].clips[0].link_id is None
    assert len(project.audio[0].clips) == 1  # el audio se conserva
    unlinked = project

    # «Vincular al video» recupera el enlace sin borrar ni duplicar el audio editado.
    assert pilot.rect("insp-audio-relink") is not None
    pilot.click("insp-audio-relink")
    _settle(pilot)
    project = state.editor_project()
    assert project.audio[0].clips[0].link_id == project.video.clips[0].id
    assert project.video.clips[0].audio_enabled is False
    assert len(project.audio[0].clips) == 1
    assert state.undo_label() == "vincular audio"
    editor.undo()
    assert state.editor_project() == unlinked
    editor.undo()
    assert state.editor_project() == before


def test_mover_audio_enlazado_avisa_y_no_lo_mueve(
    ui, media: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, controller, pilot = ui
    video, _ = media
    editor = _open(controller, pilot, video)
    before = state.editor_project()
    audio_id = before.audio[0].clips[0].id
    editor.select_audio(0, 0)
    _settle(pilot)
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(state, "toast", lambda kind, text: seen.append((kind, text)))
    zoom = state.tl_zoom()
    pilot.drag(f"audio-{audio_id}", dx=1.0 * zoom, steps=6)
    _settle(pilot)
    assert state.editor_project() == before
    assert any("Desvincular audio" in text for _kind, text in seen)


def test_guardar_y_reabrir_conserva_la_pista_enlazada(ui, media: tuple[Path, Path]) -> None:
    _, controller, pilot = ui
    video, _ = media
    editor = _open(controller, pilot, video)
    editor.seek(2.0)
    pilot.press("s")
    _settle(pilot)
    project = state.editor_project()
    assert len(project.audio[0].clips) == 2

    path = Path(state.project_path())
    editor.save()
    assert _wait(lambda: not state.project_dirty())
    editor.close_project()
    assert _wait(lambda: not state.project_open())
    editor.open_project(path)
    assert _wait(lambda: state.project_open())
    assert state.editor_project() == project
