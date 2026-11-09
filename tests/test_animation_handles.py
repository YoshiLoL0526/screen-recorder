"""Asas de las animaciones visuales en el timeline (docs/38).

Cubre la geometría pura valor/tiempo ↔ y/x con los cuatro rangos (0…1, 0.05…4, -360…360 y
8…200), el canal elegido desde el inspector, el arrastre de claves con undo/redo, el clic sin
undo y el borrado con `Supr`.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from recstudio import state
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
    path = tmp_path_factory.mktemp("anim_handles") / "v.mp4"
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


def _ensure_visible(pilot, key: str) -> None:  # noqa: ANN001
    pilot.scroll("insp-scroll", dy=2000)
    pilot.settle()
    pilot.a11y_action(key, "scroll_into_view")
    pilot.settle()


def _element_with_x_keys(editor, pilot):  # noqa: ANN001
    """Elemento con dos claves de X: 0.8 en 0.5 s y 0.2 en 1.5 s (locales)."""
    editor.add_element("rect")
    _settle(pilot)
    element = state.editor_project().elements[-1]
    editor.select_element(element.id)
    _settle(pilot)
    for offset, value in ((0.5, 0.8), (1.5, 0.2)):
        editor.seek(element.start + offset)
        _settle(pilot)
        state.insp_el_x.set(value)
        _settle(pilot)
        editor.add_element_keyframe("x")
        _settle(pilot)
    return state.editor_project().element(element.id)


# -- geometría pura --------------------------------------------------------------------


def test_geometria_valor_y_de_los_cuatro_rangos() -> None:
    top, height = 100.0, 40.0
    # x/y/opacity: 0…1.
    assert ui_tl.keyframe_value_y(top, height, "x", 0.0) == pytest.approx(138.0)
    assert ui_tl.keyframe_value_y(top, height, "x", 0.5) == pytest.approx(120.0)
    assert ui_tl.keyframe_value_y(top, height, "x", 1.0) == pytest.approx(102.0)
    assert ui_tl.keyframe_value_y(top, height, "x", -1.0) == pytest.approx(138.0)
    assert ui_tl.keyframe_value_y(top, height, "x", 2.0) == pytest.approx(102.0)
    # scale: 0.05…4.0 (el punto medio es 2.025).
    assert ui_tl.keyframe_value_y(top, height, "scale", 0.05) == pytest.approx(138.0)
    assert ui_tl.keyframe_value_y(top, height, "scale", 4.0) == pytest.approx(102.0)
    assert ui_tl.keyframe_value_y(top, height, "scale", 2.025) == pytest.approx(120.0)
    # rotation: -360…360.
    assert ui_tl.keyframe_value_y(top, height, "rotation", -360.0) == pytest.approx(138.0)
    assert ui_tl.keyframe_value_y(top, height, "rotation", 0.0) == pytest.approx(120.0)
    assert ui_tl.keyframe_value_y(top, height, "rotation", 360.0) == pytest.approx(102.0)
    # size: 8…200.
    assert ui_tl.keyframe_value_y(top, height, "size", 8.0) == pytest.approx(138.0)
    assert ui_tl.keyframe_value_y(top, height, "size", 104.0) == pytest.approx(120.0)
    assert ui_tl.keyframe_value_y(top, height, "size", 200.0) == pytest.approx(102.0)

    for channel, values in {
        "x": (0.0, 0.3, 1.0),
        "scale": (0.05, 1.0, 4.0),
        "rotation": (-360.0, -45.0, 360.0),
        "size": (8.0, 42.0, 200.0),
    }.items():
        for value in values:
            y = ui_tl.keyframe_value_y(top, height, channel, value)
            assert ui_tl.keyframe_value_at_y(top, height, channel, y) == pytest.approx(
                value, abs=1e-6,
            )
    assert ui_tl.keyframe_value_at_y(top, height, "scale", top - 50) == pytest.approx(4.0)
    assert ui_tl.keyframe_value_at_y(top, height, "scale", top + height + 50) == pytest.approx(0.05)


def test_geometria_x_y_tiempo_local() -> None:
    assert ui_tl.keyframe_x(2.0, 4.0, 1.0, 60.0) == pytest.approx(180.0)
    assert ui_tl.keyframe_x(2.0, 4.0, -3.0, 60.0) == pytest.approx(120.0)
    assert ui_tl.keyframe_x(2.0, 4.0, 99.0, 60.0) == pytest.approx(360.0)
    assert ui_tl.keyframe_time(2.0, 4.0, 180.0, 60.0) == pytest.approx(1.0)
    assert ui_tl.keyframe_time(2.0, 4.0, 0.0, 60.0) == pytest.approx(0.0)
    assert ui_tl.keyframe_time(2.0, 4.0, 9999.0, 60.0) == pytest.approx(4.0)


# -- UI ---------------------------------------------------------------------------------


def test_sin_claves_o_sin_seleccion_no_hay_asas(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_element("rect")
    _settle(pilot)
    element = state.editor_project().elements[-1]
    editor.select_element(element.id)
    _settle(pilot)
    key = f"anim-key-element-{element.id}-0"
    assert pilot.rect(key) is None

    editor.seek(element.start)
    _settle(pilot)
    editor.add_element_keyframe("x")
    _settle(pilot)
    assert pilot.rect(key) is not None

    editor.clear_selection()
    _settle(pilot)
    assert pilot.rect(key) is None


def test_arrastrar_asa_de_x_cambia_tiempo_y_valor_con_undo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    element = _element_with_x_keys(editor, pilot)
    before = element.animations[0]
    assert [p.t for p in before.points] == pytest.approx([0.5, 1.5])
    assert [p.value for p in before.points] == pytest.approx([0.8, 0.2])

    lay = ui_tl.tracks()
    row = lay.overlay_for("element")
    inner_h = row.height - 4.0
    zoom = state.tl_zoom()
    key = f"anim-key-element-{element.id}-0"
    assert pilot.rect(key) is not None
    pilot.drag(key, dx=30.0, dy=4.0, steps=6)
    _settle(pilot, 8)

    after = state.editor_project().element(element.id).animations[0]
    assert after.points[0].t == pytest.approx(before.points[0].t + 30.0 / zoom, abs=0.03)
    expected_value = before.points[0].value - 4.0 / inner_h
    assert after.points[0].value == pytest.approx(expected_value, abs=0.01)
    assert after.points[1] == before.points[1]
    assert state.undo_label() == "mover clave X"
    selected = state.keyframe_key()
    assert selected is not None and selected.t == pytest.approx(after.points[0].t)

    editor.undo()
    _settle(pilot)
    restored = state.editor_project().element(element.id).animations[0]
    assert [p.t for p in restored.points] == pytest.approx([0.5, 1.5])
    assert [p.value for p in restored.points] == pytest.approx([0.8, 0.2])

    editor.redo()
    _settle(pilot)
    redone = state.editor_project().element(element.id).animations[0]
    assert redone.points[0].value == pytest.approx(expected_value, abs=0.01)


def test_clic_en_el_asa_sin_mover_no_crea_undo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    element = _element_with_x_keys(editor, pilot)
    key = f"anim-key-element-{element.id}-0"
    label = state.undo_label()
    before = state.editor_project().element(element.id).animations[0].points

    pilot.click(key)
    _settle(pilot)
    selected = state.keyframe_key()
    assert selected is not None and selected.item == element.id
    assert selected.channel == "x" and selected.t == pytest.approx(0.5)
    assert state.undo_label() == label
    after = state.editor_project().element(element.id).animations[0].points
    assert after == before


def test_supr_borra_la_clave_seleccionada(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    element = _element_with_x_keys(editor, pilot)
    key = f"anim-key-element-{element.id}-0"
    pilot.click(key)
    _settle(pilot)
    assert state.keyframe_key() is not None

    pilot.press("delete")
    _settle(pilot)
    points = state.editor_project().element(element.id).animations[0].points
    assert [p.t for p in points] == pytest.approx([1.5])
    assert state.keyframe_key() is None
    assert state.undo_label() == "eliminar clave X"

    editor.undo()
    _settle(pilot)
    assert len(state.editor_project().element(element.id).animations[0].points) == 2


def test_selector_de_canal_y_tamano_de_texto(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_text()
    _settle(pilot)
    overlay = state.editor_project().texts[-1]
    editor.select_text(overlay.id)
    _settle(pilot)

    # El texto permite `size`; el elemento no (el controlador lo corrige a X).
    state.keyframe_channel.set("size")
    _settle(pilot)
    assert state.keyframe_channel() == "size"
    _ensure_visible(pilot, "insp-text-anim-channel")
    assert pilot.rect("insp-text-anim-channel") is not None

    for offset, value in ((0.5, 20), (1.5, 80)):
        editor.seek(overlay.start + offset)
        _settle(pilot)
        state.insp_text_size.set(value)
        _settle(pilot)
        editor.add_text_keyframe("size")
        _settle(pilot)
    key = f"anim-key-text-{overlay.id}-0"
    assert pilot.rect(key) is not None

    lay = ui_tl.tracks()
    row = lay.overlay_for("text")
    inner_h = row.height - 4.0
    pilot.drag(key, dx=0.0, dy=-11.0, steps=5)
    _settle(pilot, 8)
    curve = next(
        a for a in state.editor_project().text(overlay.id).animations if a.channel == "size"
    )
    expected = 20.0 + 11.0 / inner_h * (200.0 - 8.0)
    assert curve.points[0].value == pytest.approx(expected, abs=0.2)
    assert state.undo_label() == "mover clave tamaño"

    editor.add_element("rect")
    _settle(pilot)
    assert state.keyframe_channel() == "x"
    _ensure_visible(pilot, "insp-element-anim-channel")
    assert pilot.rect("insp-element-anim-channel") is not None


def test_asas_solo_en_la_capa_seleccionada(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_element("rect")
    _settle(pilot)
    first = state.editor_project().elements[-1]
    editor.add_element("rect")
    _settle(pilot)
    second = state.editor_project().elements[-1]
    editor.select_element(second.id)
    editor.seek(second.start)
    _settle(pilot)
    editor.add_element_keyframe("x")
    _settle(pilot)

    assert pilot.rect(f"anim-key-element-{second.id}-0") is not None
    pilot.click(f"anim-key-element-{second.id}-0")
    _settle(pilot)
    assert state.keyframe_key() is not None
    editor.select_element(first.id)
    _settle(pilot)
    assert state.keyframe_key() is None
    assert pilot.rect(f"anim-key-element-{second.id}-0") is None
    assert pilot.rect(f"anim-key-element-{first.id}-0") is None

    editor.select_element(second.id)
    _settle(pilot)
    state.keyframe_channel.set("scale")  # no hay claves de escala: no se muestran asas
    _settle(pilot)
    assert pilot.rect(f"anim-key-element-{second.id}-0") is None
