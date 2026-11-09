"""Asas de fundido y crossfade en el timeline (docs/46).

Cubre la geometría pura tiempo ↔ x y los límites, el arrastre de los fundidos de entrada/salida
(música y audio absoluto), el clamp y el undo/redo, el clic sin undo, `Esc`, la creación y el
ajuste del crossfade por corte y que preview/export lean el mismo modelo.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from recstudio import state
from recstudio.core import effects as fx
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
    path = tmp_path_factory.mktemp("fade_handles") / "v.mp4"
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
        state.razor_on.set(False)
        state.preview_area.set(state.PreviewArea())
        state.screen.set("record")


def _open(controller, pilot, path: Path):  # noqa: ANN001
    editor = controller.editor
    editor.import_paths([path])
    assert _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))
    _settle(pilot)
    return editor


def _audio(path: Path, *, seconds: float = 3.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=330:duration={seconds}", "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True,
    )
    return path


def _music(controller, pilot, video: Path):  # noqa: ANN001
    editor = _open(controller, pilot, video)
    editor.import_music([_audio(video.parent / "m1.wav"), _audio(video.parent / "m2.wav")])
    assert _wait(lambda: len(state.editor_project().audio) >= 2)
    assert _wait(lambda: len(state.editor_project().audio[1].clips) == 2)
    _settle(pilot)
    return editor


def _music_track():  # noqa: ANN201
    project = state.editor_project()
    return next(track for track in project.audio if track.mode == "sequence")


def _music_clip(index: int = 0):  # noqa: ANN201
    return _music_track().clips[index]


# -- geometría pura --------------------------------------------------------------------


def test_limites_de_los_fundidos_de_audio() -> None:
    assert fx.audio_fade_limit(6.0, 0.0) == pytest.approx(2.0)
    assert fx.audio_fade_limit(2.0, 0.0) == pytest.approx(1.0)
    assert fx.audio_fade_limit(6.0, 1.5) == pytest.approx(2.0)
    # El otro fundido no se cruza: el hueco que queda manda.
    assert fx.audio_fade_limit(3.0, 2.5) == pytest.approx(0.5)
    assert fx.audio_fade_limit(1.0, 2.0) == pytest.approx(0.0)
    assert fx.audio_fade_limit(0.0, 0.0) == pytest.approx(0.0)


def test_geometria_x_y_tiempo_de_los_fundidos() -> None:
    assert ui_tl.audio_fade_x(2.0, 4.0, 1.0, 60.0, side="in") == pytest.approx(180.0)
    assert ui_tl.audio_fade_x(2.0, 4.0, 1.0, 60.0, side="out") == pytest.approx(300.0)
    assert ui_tl.audio_fade_x(2.0, 4.0, -3.0, 60.0, side="in") == pytest.approx(120.0)
    assert ui_tl.audio_fade_x(2.0, 4.0, 99.0, 60.0, side="out") == pytest.approx(120.0)
    assert ui_tl.audio_fade_at_x(2.0, 4.0, 180.0, 60.0, side="in") == pytest.approx(1.0)
    assert ui_tl.audio_fade_at_x(2.0, 4.0, 300.0, 60.0, side="out") == pytest.approx(1.0)
    assert ui_tl.audio_fade_at_x(2.0, 4.0, 0.0, 60.0, side="in") == pytest.approx(0.0)
    assert ui_tl.audio_fade_at_x(2.0, 4.0, 9999.0, 60.0, side="in") == pytest.approx(4.0)
    assert ui_tl.audio_fade_at_x(2.0, 4.0, 100.0, 0.0, side="out") == pytest.approx(0.0)


# -- asas de fundido ------------------------------------------------------------------


def test_asas_de_fundido_solo_con_el_clip_de_audio_seleccionado(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _music(controller, pilot, video)
    clip = _music_clip()
    # `import_music` deja seleccionado el primer clip; sin selección no hay asas.
    editor.clear_selection()
    _settle(pilot)
    assert pilot.rect(f"audio-fade-in-1-{clip.id}") is None
    assert pilot.rect(f"audio-fade-out-1-{clip.id}") is None

    editor.select_audio(1, 0)
    _settle(pilot)
    assert pilot.rect(f"audio-fade-in-1-{clip.id}") is not None
    assert pilot.rect(f"audio-fade-out-1-{clip.id}") is not None
    # El crossfade del corte también está, aunque el clip seleccionado sea el primero.
    assert pilot.rect("audio-cross-1-0") is not None

    editor.clear_selection()
    _settle(pilot)
    assert pilot.rect(f"audio-fade-in-1-{clip.id}") is None


def test_arrastrar_fundidos_con_clamp_undo_y_redo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _music(controller, pilot, video)
    clip = _music_clip()
    editor.select_audio(1, 0)
    _settle(pilot)
    zoom = state.tl_zoom()
    key_in = f"audio-fade-in-1-{clip.id}"
    key_out = f"audio-fade-out-1-{clip.id}"

    pilot.drag(key_in, dx=0.8 * zoom, steps=6)
    _settle(pilot, 8)
    edited = _music_clip()
    assert fx.audio_fade_in(edited) == pytest.approx(0.8, abs=0.03)
    assert state.insp_audio_fade_in() == pytest.approx(0.8, abs=0.03)
    assert state.undo_label() == "fundido de audio"

    pilot.drag(key_out, dx=-0.6 * zoom, steps=6)
    _settle(pilot, 8)
    edited = _music_clip()
    assert fx.audio_fade_out(edited) == pytest.approx(0.6, abs=0.03)
    assert state.insp_audio_fade_out() == pytest.approx(0.6, abs=0.03)
    assert state.undo_label() == "fundido de audio"

    # Clamp: el asa de entrada no pasa de min(2 s, duración/2 = 1.5 s).
    pilot.drag(key_in, dx=4.0 * zoom, steps=8)
    _settle(pilot, 8)
    assert fx.audio_fade_in(_music_clip()) == pytest.approx(1.5, abs=0.02)
    # ...ni cruza el fundido de salida (1.5 s ya ocupa la otra mitad).
    pilot.drag(key_out, dx=-4.0 * zoom, steps=8)
    _settle(pilot, 8)
    assert fx.audio_fade_out(_music_clip()) == pytest.approx(1.5, abs=0.02)

    editor.undo()
    _settle(pilot)
    assert fx.audio_fade_out(_music_clip()) == pytest.approx(0.6, abs=0.03)
    editor.redo()
    _settle(pilot)
    assert fx.audio_fade_out(_music_clip()) == pytest.approx(1.5, abs=0.02)


def test_clic_sin_movimiento_y_escape_no_dejan_undo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _music(controller, pilot, video)
    clip = _music_clip()
    editor.select_audio(1, 0)
    _settle(pilot)
    key_in = f"audio-fade-in-1-{clip.id}"
    before = state.editor_project()
    label = state.undo_label()

    pilot.click(key_in)
    _settle(pilot)
    assert state.editor_project() == before
    assert state.undo_label() == label
    assert not editor.store.in_transaction

    # Esc durante un gesto: vuelve al estado anterior y no crea entrada de undo.
    editor.begin_audio_fade_drag(1, clip.id, "in")
    editor.drag_audio_fade(0.7 * state.tl_zoom())
    _settle(pilot)
    assert fx.audio_fade_in(_music_clip()) == pytest.approx(0.7, abs=0.05)
    pilot.press("escape")
    _settle(pilot)
    assert state.editor_project() == before
    assert state.undo_label() == label
    assert not editor.store.in_transaction


def test_el_inspector_sigue_editando_los_mismos_fundidos(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _music(controller, pilot, video)
    clip = _music_clip()
    editor.select_audio(1, 0)
    _settle(pilot)

    state.insp_audio_fade_in.set(0.35)
    _settle(pilot)
    edited = _music_clip()
    assert fx.audio_fade_in(edited) == pytest.approx(0.35, abs=0.01)
    # El asa sigue visible y el inspector refleja el valor efectivo.
    assert pilot.rect(f"audio-fade-in-1-{clip.id}") is not None
    assert state.insp_audio_fade_in() == pytest.approx(0.35, abs=0.01)
    assert fx.audio_fades(edited, edited.duration)[0] == pytest.approx(0.35, abs=0.01)


# -- crossfade por corte ---------------------------------------------------------------


def test_crear_y_ajustar_crossfade_con_acrossfade(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _music(controller, pilot, video)
    assert pilot.rect("audio-cross-1-0") is not None
    zoom = state.tl_zoom()
    assert _music_track().transitions == (None,)

    pilot.drag("audio-cross-1-0", dx=-0.6 * zoom, steps=6)
    _settle(pilot, 8)
    transition = _music_track().transitions[0]
    assert transition is not None
    assert transition.kind == "fade"
    assert transition.duration == pytest.approx(0.6, abs=0.04)
    assert state.undo_label() == "crossfade de audio"

    from recstudio.core import graph as G

    args = G.build_audio(state.editor_project(), "salida.wav", exe="ffmpeg")
    chain = args[args.index("-filter_complex") + 1]
    assert "acrossfade=d=0.6:c1=tri:c2=tri" in chain

    # Clamp al límite del corte (mitad del clip más corto).
    pilot.drag("audio-cross-1-0", dx=-5.0 * zoom, steps=8)
    _settle(pilot, 8)
    assert _music_track().transitions[0].duration == pytest.approx(1.5, abs=0.02)

    # Arrastrar más allá del límite no cambia nada ni deja otra entrada de undo.
    before = state.editor_project()
    label = state.undo_label()
    pilot.drag("audio-cross-1-0", dx=-1.0 * zoom, steps=4)
    _settle(pilot, 8)
    assert state.editor_project() == before
    assert state.undo_label() == label
    assert not editor.store.in_transaction

    editor.undo()
    _settle(pilot)
    assert _music_track().transitions[0].duration == pytest.approx(0.6, abs=0.04)
    editor.redo()
    _settle(pilot)
    assert _music_track().transitions[0].duration == pytest.approx(1.5, abs=0.02)

    # Hacia la derecha por debajo del mínimo: vuelve al corte limpio.
    pilot.drag("audio-cross-1-0", dx=5.0 * zoom, steps=8)
    _settle(pilot, 8)
    assert _music_track().transitions == (None,)
    assert state.undo_label() == "crossfade de audio"


def test_click_en_el_crossfade_sin_mover_no_crea_undo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _music(controller, pilot, video)
    before = state.editor_project()
    label = state.undo_label()
    pilot.click("audio-cross-1-0")
    _settle(pilot)
    assert state.editor_project() == before
    assert state.undo_label() == label
    assert not editor.store.in_transaction


def test_sin_asas_en_pistas_bloqueadas_o_absolutas(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    # La pista «Audio del video» es absoluta: aunque se divida en dos clips no hay crossfade.
    editor.select_audio(0, 0)
    editor.seek(2.0)
    _settle(pilot)
    pilot.press("shift+s")
    _settle(pilot)
    project = state.editor_project()
    assert project.audio[0].mode == "absolute" and len(project.audio[0].clips) == 2
    assert pilot.rect("audio-cross-0-0") is None
    # Los fundidos sí: son independientes del modo de pista.
    editor.select_audio(0, 0)
    _settle(pilot)
    clip = state.editor_project().audio[0].clips[0]
    assert pilot.rect(f"audio-fade-in-0-{clip.id}") is not None

    # Una pista secuencial bloqueada no muestra ni fundidos ni crossfade.
    editor.import_music([_audio(video.parent / "m.wav")])
    assert _wait(lambda: len(state.editor_project().audio) >= 2)
    _settle(pilot)
    music = _music_track()
    assert len(music.clips) == 1  # sin corte: el asa de crossfade no existe
    editor.toggle_audio_lock(1)
    editor.select_audio(1, 0)
    _settle(pilot)
    music_clip = _music_track().clips[0]
    assert pilot.rect(f"audio-fade-in-1-{music_clip.id}") is None
    assert pilot.rect("audio-cross-1-0") is None
