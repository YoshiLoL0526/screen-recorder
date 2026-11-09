"""Asas de las envolventes de volumen en el timeline (docs/37).

Cubre la geometría pura tiempo/ganancia ↔ x/y, el arrastre de claves de un clip y de un
elemento, el modo «Env» para añadir, Supr para borrar y el undo/redo.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from recstudio import state
from recstudio.core import elements as el
from recstudio.core import timeline as tl
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.ui import timeline as ui_tl


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
    path = tmp_path_factory.mktemp("env_handles") / "v.mp4"
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
        state.env_edit_on.set(False)
        state.envelope_key.set(None)
        state.preview_area.set(state.PreviewArea())
        state.screen.set("record")


def _open(controller, pilot, path: Path):  # noqa: ANN001
    editor = controller.editor
    editor.import_paths([path])
    assert _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))
    _settle(pilot)
    return editor


def _envelope(pilot, clip_id: str):  # noqa: ANN001, ANN202
    return state.editor_project().clip(clip_id).envelope


# -- geometría pura --------------------------------------------------------------------


def test_geometria_tiempo_y_ganancia_de_la_fila() -> None:
    top, height = 100.0, 40.0
    assert ui_tl.envelope_gain_y(top, height, 4.0) == pytest.approx(102.0)
    assert ui_tl.envelope_gain_y(top, height, 0.0) == pytest.approx(138.0)
    assert ui_tl.envelope_gain_y(top, height, 2.0) == pytest.approx(120.0)
    for gain in (0.0, 0.3, 1.0, 2.5, 4.0):
        y = ui_tl.envelope_gain_y(top, height, gain)
        assert ui_tl.envelope_gain_at_y(top, height, y) == pytest.approx(gain, abs=1e-6)
    assert ui_tl.envelope_gain_at_y(top, height, top - 50) == pytest.approx(4.0)
    assert ui_tl.envelope_gain_at_y(top, height, top + height + 50) == pytest.approx(0.0)


def test_geometria_x_y_tiempo_local() -> None:
    assert ui_tl.envelope_x(2.0, 4.0, 1.0, 60.0) == pytest.approx(180.0)
    assert ui_tl.envelope_x(2.0, 4.0, -3.0, 60.0) == pytest.approx(120.0)
    assert ui_tl.envelope_x(2.0, 4.0, 99.0, 60.0) == pytest.approx(360.0)
    assert ui_tl.envelope_time(2.0, 4.0, 180.0, 60.0) == pytest.approx(1.0)
    assert ui_tl.envelope_time(2.0, 4.0, 0.0, 60.0) == pytest.approx(0.0)
    assert ui_tl.envelope_time(2.0, 4.0, 9999.0, 60.0) == pytest.approx(4.0)


# -- UI ---------------------------------------------------------------------------------


def test_sin_envolvente_no_hay_asas(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    clip_id = state.editor_project().video.clips[0].id
    editor.select_clip(clip_id)
    _settle(pilot)
    assert pilot.rect(f"env-key-video-{clip_id}-0") is None

    # Con una clave sí aparece el asa, y solo con el clip seleccionado.
    editor.add_audio_keyframe()
    _settle(pilot)
    assert pilot.rect(f"env-key-video-{clip_id}-0") is not None
    editor.clear_selection()
    _settle(pilot)
    assert pilot.rect(f"env-key-video-{clip_id}-0") is None


def test_arrastrar_asa_de_un_clip_de_video_con_undo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    project = state.editor_project()
    clip_id = project.video.clips[0].id
    start = tl.clip_starts(project.video)[0]
    editor.select_clip(clip_id)
    editor.seek(start + 0.5)
    _settle(pilot)
    editor.add_audio_keyframe()
    editor.seek(start + 1.5)
    _settle(pilot)
    editor.add_audio_keyframe()
    _settle(pilot)
    before = _envelope(pilot, clip_id)
    assert [p.t for p in before] == pytest.approx([0.5, 1.5], abs=0.03)
    zoom = state.tl_zoom()
    lay = ui_tl.tracks()
    dy = 9.0
    key = f"env-key-video-{clip_id}-0"
    assert pilot.rect(key) is not None
    pilot.drag(key, dx=30.0, dy=dy, steps=6)
    _settle(pilot, 8)

    after = _envelope(pilot, clip_id)
    assert after[0].t == pytest.approx(before[0].t + 30.0 / zoom, abs=0.03)
    expected_gain = before[0].gain - dy / max(1.0, lay.audio_h - 4.0) * 4.0
    assert after[0].gain == pytest.approx(expected_gain, abs=0.06)
    assert state.undo_label() == "mover clave de volumen"

    editor.undo()
    _settle(pilot)
    restored = _envelope(pilot, clip_id)
    assert restored[0].t == pytest.approx(before[0].t, abs=1e-6)
    assert restored[0].gain == pytest.approx(before[0].gain, abs=1e-6)

    editor.redo()
    _settle(pilot)
    assert _envelope(pilot, clip_id)[0].gain == pytest.approx(expected_gain, abs=0.06)


def test_modo_envolvente_anade_y_suprime_con_supr(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    project = state.editor_project()
    clip_id = project.video.clips[0].id
    clip = project.clip(clip_id)
    editor.select_clip(clip_id)
    _settle(pilot)
    editor.toggle_envelope_edit()
    _settle(pilot)

    rect = pilot.rect(f"clip-{clip_id}")
    assert rect is not None
    lay = ui_tl.tracks()
    zoom = state.tl_zoom()
    x = rect[0] + 0.25 * clip.duration * zoom
    y = rect[1] + (lay.audio_top - lay.video_top) + lay.audio_h / 2
    before = len(_envelope(pilot, clip_id))
    pilot.click_at(x, y)
    _settle(pilot, 8)

    points = _envelope(pilot, clip_id)
    assert len(points) == before + 1
    assert points[0].t == pytest.approx(0.25 * clip.duration, abs=0.05)
    assert state.undo_label() == "añadir clave de volumen"
    assert state.envelope_key() is not None

    pilot.press("delete")
    _settle(pilot)
    assert len(_envelope(pilot, clip_id)) == before
    assert state.envelope_key() is None
    assert state.undo_label() == "eliminar clave de volumen"


def test_clic_en_el_asa_sin_mover_no_crea_undo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    clip_id = state.editor_project().video.clips[0].id
    editor.select_clip(clip_id)
    editor.seek(tl.clip_starts(state.editor_project().video)[0] + 0.5)
    _settle(pilot)
    editor.add_audio_keyframe()
    _settle(pilot)
    key = f"env-key-video-{clip_id}-0"
    label = state.undo_label()
    pilot.click(key)
    _settle(pilot)
    selection = state.envelope_key()
    assert selection is not None and selection.item == clip_id
    assert state.undo_label() == label
    assert _envelope(pilot, clip_id)[0].t == pytest.approx(0.5, abs=0.03)


def test_asas_de_un_clip_de_audio_y_de_un_elemento(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    project = state.editor_project()
    assert project.audio and project.audio[0].clips
    audio_clip = project.audio[0].clips[0]
    audio_start = tl.clip_starts(project.audio[0])[0]
    editor.select_audio(0, 0)
    editor.seek(audio_start + 0.4)
    _settle(pilot)
    editor.add_audio_keyframe()
    editor.seek(audio_start + 1.1)
    _settle(pilot)
    editor.add_audio_keyframe()
    _settle(pilot)
    key = f"env-key-audio-0-{audio_clip.id}-0"
    assert pilot.rect(key) is not None
    before = state.editor_project().audio[0].clips[0].envelope
    pilot.drag(key, dx=18.0, dy=-4.0, steps=4)
    _settle(pilot, 8)
    after = state.editor_project().audio[0].clips[0].envelope
    assert after[0].t == pytest.approx(before[0].t + 18.0 / state.tl_zoom(), abs=0.03)
    assert after[0].gain > before[0].gain

    # Preview y export comparten la sección de audio: el graph lee el estado recién editado.
    from recstudio.core import graph as G

    args = G.build_audio(state.editor_project(), "salida.wav", exe="ffmpeg")
    chain = args[args.index("-filter_complex") + 1]
    assert "volume=volume=" in chain and ":eval=frame" in chain

    # Elemento de video superpuesto: sin waveform, las asas van sobre su barra.
    project = state.editor_project()
    asset = project.assets[0]
    project, element = el.add_element(
        project, "video", 0.5, 1.5, asset_id=asset.id, in_=0.0,
    )
    assert editor._apply("añadir elemento de prueba", project)
    editor.select_element(element.id)
    editor.seek(element.start + 0.5)
    _settle(pilot)
    state.insp_el_gain.set(1.0)
    editor.add_element_audio_keyframe()
    _settle(pilot)
    key = f"env-key-element-{element.id}-0"
    assert pilot.rect(key) is not None
    before = state.editor_project().element(element.id).envelope
    pilot.drag(key, dx=12.0, dy=3.0, steps=4)
    _settle(pilot, 8)
    after = state.editor_project().element(element.id).envelope
    assert after[0].t == pytest.approx(before[0].t + 12.0 / state.tl_zoom(), abs=0.03)
    assert after[0].gain == pytest.approx(before[0].gain - 3.0 / 18.0 * 4.0, abs=0.1)
