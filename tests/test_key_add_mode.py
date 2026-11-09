"""Modo «añadir clave» desde el timeline (docs/42).

Cubre la conversión x → tiempo local, el valor base/interpolado por canal, el clic que crea
una clave, el clic sobre una clave existente que actualiza su easing, el no-op sin selección o
con un canal inválido, la exclusividad con razor/marquesina/Env, `Esc` y que el clic no mueve
la capa ni deja una transacción abierta. La UI se prueba con `pilot` sobre un elemento y un texto.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from recstudio import state
from recstudio.core import animation as anim
from recstudio.core.project import Element, TextOverlay
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


def _settle(pilot, frames: int = 4) -> None:  # noqa: ANN001
    for _ in range(frames):
        pilot.frame()
    pilot.settle()


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("key_add") / "v.mp4"
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
        state.keyframe_channel.set("x")
        state.keyframe_key.set(None)
        state.key_add_on.set(False)
        state.env_edit_on.set(False)
        state.envelope_key.set(None)
        state.razor_on.set(False)
        state.marquee_on.set(False)
        state.marquee.set(None)
        state.tl_drag.set(None)
        state.insp_easing.set("linear")
        state.preview_area.set(state.PreviewArea())
        state.screen.set("record")


def _open(controller, pilot, path: Path):  # noqa: ANN001
    editor = controller.editor
    editor.import_paths([path])
    assert _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))
    _settle(pilot)
    return editor


def _click_bar(pilot, rect, fraction: float) -> None:  # noqa: ANN001
    """Clic dentro de una zona de barra; cerca del borde superior para no tocar un asa."""
    pilot.click_at(rect[0] + rect[2] * fraction, rect[1] + 2.0)
    _settle(pilot, 8)


def _animation(item, channel: str):  # noqa: ANN001, ANN202
    return next((animation for animation in item.animations if animation.channel == channel), None)


# -- geometría y valores ---------------------------------------------------------------


def test_x_a_tiempo_local_y_valor_base_o_interpolado_por_canal() -> None:
    # Inversa de `keyframe_x`: la x del contenido se convierte en tiempo local al elemento/texto.
    assert ui_tl.keyframe_time(2.0, 4.0, 3.0 * 60.0, 60.0) == pytest.approx(1.0)
    assert ui_tl.keyframe_time(2.0, 4.0, 0.0, 60.0) == pytest.approx(0.0)
    assert ui_tl.keyframe_time(2.0, 4.0, 9999.0, 60.0) == pytest.approx(4.0)

    element = Element(id="e1", kind="rect", start=1.0, end=5.0, x=0.25, y=0.7, rotation=30.0)
    assert ui_tl.keyframe_default(element, "x") == pytest.approx(0.25)
    assert ui_tl.keyframe_default(element, "y") == pytest.approx(0.7)
    assert ui_tl.keyframe_default(element, "scale") == pytest.approx(1.0)
    assert ui_tl.keyframe_default(element, "rotation") == pytest.approx(30.0)

    element = replace(element, animations=(
        anim.Animation("x", (anim.Keyframe(0.0, 0.0), anim.Keyframe(2.0, 1.0))),
    ))
    assert ui_tl.keyframe_value_at(element, "x", 0.5) == pytest.approx(0.25)
    assert ui_tl.keyframe_value_at(element, "x", 1.0) == pytest.approx(0.5)
    assert ui_tl.keyframe_value_at(element, "x", 2.0) == pytest.approx(1.0)
    # Un canal sin animación devuelve su base en cualquier instante.
    assert ui_tl.keyframe_value_at(element, "y", 3.0) == pytest.approx(0.7)

    text = TextOverlay(id="t1", text="Hola", start=2.0, end=6.0, x=0.3, y=0.6, size=42,
                       opacity=0.8)
    assert ui_tl.keyframe_default(text, "x") == pytest.approx(0.3)
    assert ui_tl.keyframe_default(text, "y") == pytest.approx(0.6)
    assert ui_tl.keyframe_default(text, "opacity") == pytest.approx(0.8)
    assert ui_tl.keyframe_default(text, "size") == pytest.approx(42.0)
    assert ui_tl.keyframe_default(text, "scale") == pytest.approx(1.0)
    assert ui_tl.keyframe_default(text, "rotation") == pytest.approx(0.0)

    text = replace(text, animations=(
        anim.Animation("opacity", (anim.Keyframe(0.0, 0.2), anim.Keyframe(2.0, 0.8))),
    ))
    assert ui_tl.keyframe_value_at(text, "opacity", 1.0) == pytest.approx(0.5)
    assert ui_tl.keyframe_value_at(text, "size", 1.0) == pytest.approx(42.0)


# -- UI ---------------------------------------------------------------------------------


def test_clic_en_la_barra_del_elemento_crea_clave_con_undo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_element("rect")
    _settle(pilot)
    element = state.editor_project().elements[-1]
    editor.select_element(element.id)
    state.keyframe_channel.set("x")
    state.insp_easing.set("ease_out")
    editor.toggle_key_add()
    _settle(pilot)
    assert state.key_add_on()

    rect = pilot.rect(f"anim-add-element-{element.id}")
    assert rect is not None
    duration = element.end - element.start
    _click_bar(pilot, rect, 0.25)

    after = state.editor_project().element(element.id)
    animation = _animation(after, "x")
    assert animation is not None and len(animation.points) == 1
    assert animation.points[0].t == pytest.approx(0.25 * duration, abs=0.05)
    assert animation.points[0].value == pytest.approx(element.x, abs=1e-6)
    assert animation.points[0].easing == "ease_out"
    selected = state.keyframe_key()
    assert selected is not None
    assert (selected.scope, selected.item, selected.channel) == ("element", element.id, "x")
    assert selected.t == pytest.approx(animation.points[0].t)
    assert state.undo_label() == "añadir clave X"
    # El clic no mueve la capa ni deja una transacción de arrastre abierta.
    assert after.start == pytest.approx(element.start)
    assert not editor.store.in_transaction
    assert state.tl_drag() is None

    editor.undo()
    _settle(pilot)
    assert _animation(state.editor_project().element(element.id), "x") is None
    editor.redo()
    _settle(pilot)
    redone = _animation(state.editor_project().element(element.id), "x")
    assert redone is not None and redone.points[0].easing == "ease_out"


def test_clic_en_clave_existente_actualiza_easing_sin_duplicar(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_element("rect")
    _settle(pilot)
    element = state.editor_project().elements[-1]
    editor.select_element(element.id)
    state.keyframe_channel.set("x")
    state.insp_easing.set("linear")
    editor.toggle_key_add()
    _settle(pilot)
    rect = pilot.rect(f"anim-add-element-{element.id}")
    assert rect is not None
    _click_bar(pilot, rect, 0.4)
    first = _animation(state.editor_project().element(element.id), "x")
    assert first is not None and first.points[0].easing == "linear"
    instant = first.points[0].t

    state.insp_easing.set("ease_in")
    _settle(pilot)
    _click_bar(pilot, pilot.rect(f"anim-add-element-{element.id}"), 0.4)

    updated = _animation(state.editor_project().element(element.id), "x")
    assert updated is not None and len(updated.points) == 1
    assert updated.points[0].t == pytest.approx(instant)
    assert updated.points[0].value == pytest.approx(first.points[0].value)
    assert updated.points[0].easing == "ease_in"
    assert state.undo_label() == "añadir clave X"


def test_no_op_sin_seleccion_o_con_canal_invalido(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_element("rect")
    _settle(pilot)
    element = state.editor_project().elements[-1]
    state.keyframe_channel.set("x")
    editor.toggle_key_add()
    _settle(pilot)
    window_x = ui_tl.TL_X0 + element.start * state.tl_zoom() - state.tl_scroll()

    editor.clear_selection()
    _settle(pilot)
    editor.begin_keyframe_add("element", element.id, window_x)
    assert _animation(state.editor_project().element(element.id), "x") is None
    assert not editor.store.in_transaction
    assert any(toast.kind == "warning" for toast in state.toasts())

    editor.select_element(element.id)
    _settle(pilot)
    state.keyframe_channel.set("size")  # `size` solo vale en textos
    _settle(pilot)
    editor.begin_keyframe_add("element", element.id, window_x)
    assert _animation(state.editor_project().element(element.id), "size") is None
    assert not editor.store.in_transaction
    assert state.keyframe_key() is None


def test_exclusividad_con_razor_marquesina_env_y_escape(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_element("rect")
    _settle(pilot)

    editor.toggle_key_add()
    _settle(pilot)
    assert state.key_add_on()
    assert not state.razor_on() and not state.marquee_on() and not state.env_edit_on()

    editor.toggle_razor()
    _settle(pilot)
    assert state.razor_on() and not state.key_add_on()

    editor.toggle_key_add()
    _settle(pilot)
    assert state.key_add_on() and not state.razor_on()

    editor.toggle_marquee()
    _settle(pilot)
    assert state.marquee_on() and not state.key_add_on()

    editor.toggle_key_add()
    _settle(pilot)
    assert state.key_add_on() and not state.marquee_on()

    editor.toggle_envelope_edit()
    _settle(pilot)
    assert state.env_edit_on() and not state.key_add_on()

    editor.toggle_key_add()
    _settle(pilot)
    assert state.key_add_on() and not state.env_edit_on()

    pilot.press("escape")
    _settle(pilot)
    assert not state.key_add_on()


def test_pilot_texto_crea_clave_de_tamano_y_queda_arrastrable(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_text()
    _settle(pilot)
    overlay = state.editor_project().texts[-1]
    editor.select_text(overlay.id)
    state.keyframe_channel.set("size")
    state.insp_easing.set("ease_in")
    editor.toggle_key_add()
    _settle(pilot)
    assert state.key_add_on()

    rect = pilot.rect(f"anim-add-text-{overlay.id}")
    assert rect is not None
    duration = overlay.end - overlay.start
    _click_bar(pilot, rect, 0.5)

    after = state.editor_project().text(overlay.id)
    animation = _animation(after, "size")
    assert animation is not None and len(animation.points) == 1
    assert animation.points[0].t == pytest.approx(0.5 * duration, abs=0.05)
    assert animation.points[0].value == pytest.approx(overlay.size)
    assert animation.points[0].easing == "ease_in"
    assert state.undo_label() == "añadir clave tamaño"
    assert after.start == pytest.approx(overlay.start)

    # La clave queda seleccionada para arrastrarla; mover conserva el easing.
    key = f"anim-key-text-{overlay.id}-0"
    assert pilot.rect(key) is not None
    before = animation.points[0]
    pilot.drag(key, dx=18.0, dy=-4.0, steps=5)
    _settle(pilot, 8)
    moved = _animation(state.editor_project().text(overlay.id), "size")
    assert moved is not None
    assert moved.points[0].t == pytest.approx(before.t + 18.0 / state.tl_zoom(), abs=0.03)
    assert moved.points[0].easing == "ease_in"
    assert state.undo_label() == "mover clave tamaño"
