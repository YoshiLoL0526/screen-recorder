"""Copiar/pegar claves de animación y puntos de envolvente (docs/41).

Cubre el modelo del portapapeles, el pegado visual en el mismo canal y en otro válido, el
reemplazo en el mismo instante, el recorte del tiempo al rango del destino, los puntos de
envolvente (clips y elementos), el canal incompatible, la falta de destino, undo/redo y que el
portapapeles de clips siga funcionando. Los atajos se prueban con la app real (`pilot`).
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from recstudio import state
from recstudio.core import animation as anim
from recstudio.core import timeline as tl
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths


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
    path = tmp_path_factory.mktemp("key_clipboard") / "v.mp4"
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
        state.insp_easing.set("linear")
        state.clipboard_count.set(0)
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


def _element_with_x_keys(
    editor, pilot, keys=((0.5, 0.8, "linear"), (1.5, 0.2, "linear")),  # noqa: ANN001
):  # noqa: ANN202
    """Elemento con claves de X en tiempos locales al elemento."""
    editor.seek(0.0)
    _settle(pilot)
    editor.add_element("rect")
    _settle(pilot)
    element = state.editor_project().elements[-1]
    editor.select_element(element.id)
    _settle(pilot)
    for offset, value, easing in keys:
        state.insp_easing.set(easing)
        editor.seek(element.start + offset)
        _settle(pilot)
        state.insp_el_x.set(value)
        _settle(pilot)
        editor.add_element_keyframe("x")
        _settle(pilot)
    state.insp_easing.set("linear")
    return state.editor_project().element(element.id)


def _select_key(editor, element, channel: str, t: float) -> None:  # noqa: ANN001
    editor.select_keyframe_key("element", element.id, channel, t)


def _audio_key(editor, pilot, clip, offset: float, gain: float) -> None:  # noqa: ANN001
    project = state.editor_project()
    start = tl.clip_starts(project.video)[project.video.index_of(clip.id)]
    editor.select_clip(clip.id)
    _settle(pilot)
    editor.seek(start + offset)
    _settle(pilot)
    state.insp_volume.set(gain)
    _settle(pilot)
    editor.add_audio_keyframe()
    _settle(pilot)


def _last_toast_has(text: str) -> bool:
    return any(text.lower() in toast.text.lower() for toast in state.toasts())


# -- modelo ------------------------------------------------------------------------------


def test_modelo_del_portapapeles_es_frozen_y_comparable() -> None:
    key = state.KeyClipboard(
        kind="visual", channel="x", value=0.4, easing="ease_in", source_label="X · 0.50 s",
    )
    assert key.kind == "visual" and key.channel == "x"
    assert key == state.KeyClipboard(
        kind="visual", channel="x", value=0.4, easing="ease_in", source_label="X · 0.50 s",
    )
    with pytest.raises(FrozenInstanceError):
        key.value = 0.5  # type: ignore[misc]


# -- claves visuales ---------------------------------------------------------------------


def test_copiar_y_pegar_clave_visual_en_el_mismo_canal(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    element = _element_with_x_keys(editor, pilot)
    _select_key(editor, element, "x", 0.5)
    _settle(pilot)

    pilot.press("ctrl+c")
    _settle(pilot)
    clipboard = editor._key_clipboard
    assert clipboard is not None
    assert (clipboard.kind, clipboard.channel, clipboard.easing) == ("visual", "x", "linear")
    assert clipboard.value == pytest.approx(0.8)
    assert state.clipboard_count() == 0  # el portapapeles de clips queda vacío

    editor.seek(element.start + 2.0)
    _settle(pilot)
    pilot.press("ctrl+v")
    _settle(pilot)

    curve = anim.find(state.editor_project().element(element.id).animations, "x")
    assert curve is not None
    assert [p.t for p in curve.points] == pytest.approx([0.5, 1.5, 2.0])
    assert curve.points[-1].value == pytest.approx(0.8)
    assert state.undo_label() == "pegar clave X"
    selected = state.keyframe_key()
    assert selected is not None and selected.t == pytest.approx(2.0)

    editor.undo()
    _settle(pilot)
    assert len(anim.find(state.editor_project().element(element.id).animations, "x").points) == 2
    editor.redo()
    _settle(pilot)
    assert len(anim.find(state.editor_project().element(element.id).animations, "x").points) == 3


def test_pegar_en_otro_canal_valido_conserva_el_easing(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    element = _element_with_x_keys(
        editor, pilot,
        keys=((0.5, 0.8, "ease_in"), (1.5, 0.2, "linear")),
    )
    _select_key(editor, element, "x", 0.5)
    _settle(pilot)
    pilot.press("ctrl+c")
    _settle(pilot)

    state.keyframe_channel.set("scale")  # canal válido del mismo elemento
    editor.seek(element.start + 2.2)
    _settle(pilot)
    pilot.press("ctrl+v")
    _settle(pilot)

    curve = anim.find(state.editor_project().element(element.id).animations, "scale")
    assert curve is not None and len(curve.points) == 1
    assert curve.points[0].t == pytest.approx(2.2)
    assert curve.points[0].value == pytest.approx(0.8)
    assert curve.points[0].easing == "ease_in"
    assert state.undo_label() == "pegar clave escala"


def test_pegar_reemplaza_la_clave_del_mismo_instante(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    element = _element_with_x_keys(editor, pilot)
    _select_key(editor, element, "x", 0.5)
    _settle(pilot)
    pilot.press("ctrl+c")
    _settle(pilot)

    editor.seek(element.start + 1.5)  # ya hay una clave con 0.2
    _settle(pilot)
    pilot.press("ctrl+v")
    _settle(pilot)

    curve = anim.find(state.editor_project().element(element.id).animations, "x")
    assert curve is not None and len(curve.points) == 2
    assert [p.value for p in curve.points] == pytest.approx([0.8, 0.8])
    assert state.undo_label() == "pegar clave X"

    editor.undo()
    _settle(pilot)
    restored = anim.find(state.editor_project().element(element.id).animations, "x")
    assert [p.value for p in restored.points] == pytest.approx([0.8, 0.2])


def test_pegar_acota_al_final_y_fuera_del_destino_es_no_op(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    element = _element_with_x_keys(editor, pilot)
    _select_key(editor, element, "x", 0.5)
    _settle(pilot)
    pilot.press("ctrl+c")
    _settle(pilot)

    editor.seek(element.end)  # el final exacto se acota a la duración
    _settle(pilot)
    pilot.press("ctrl+v")
    _settle(pilot)
    curve = anim.find(state.editor_project().element(element.id).animations, "x")
    assert curve is not None
    assert curve.points[-1].t == pytest.approx(element.end - element.start)

    editor.undo()
    _settle(pilot)
    before = state.editor_project().element(element.id).animations
    label = state.undo_label()
    editor.seek(element.end + 0.2)  # fuera del elemento: no-op con aviso
    _settle(pilot)
    pilot.press("ctrl+v")
    _settle(pilot)
    assert state.editor_project().element(element.id).animations == before
    assert state.undo_label() == label
    assert not editor.store.in_transaction
    assert _last_toast_has("fuera")


def test_canal_incompatible_no_hace_nada(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_text()
    _settle(pilot)
    overlay = state.editor_project().texts[-1]
    editor.select_text(overlay.id)
    _settle(pilot)
    editor.seek(overlay.start + 0.5)
    _settle(pilot)
    state.insp_text_size.set(80)
    _settle(pilot)
    editor.add_text_keyframe("size")
    _settle(pilot)
    editor.select_keyframe_key("text", overlay.id, "size", 0.5)
    _settle(pilot)
    pilot.press("ctrl+c")
    _settle(pilot)

    editor.add_element("rect")  # el controlador corrige `size` a `x` para un elemento
    _settle(pilot)
    element = state.editor_project().elements[-1]
    editor.seek(element.start + 0.5)
    _settle(pilot)
    before = state.editor_project().element(element.id).animations
    label = state.undo_label()
    pilot.press("ctrl+v")
    _settle(pilot)

    assert state.editor_project().element(element.id).animations == before
    assert state.undo_label() == label
    assert not editor.store.in_transaction
    assert _last_toast_has("no se puede pegar")


def test_sin_destino_valido_no_hace_nada(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    element = _element_with_x_keys(editor, pilot)
    _select_key(editor, element, "x", 0.5)
    _settle(pilot)
    pilot.press("ctrl+c")
    _settle(pilot)

    editor.clear_selection()
    _settle(pilot)
    before = state.editor_project()
    label = state.undo_label()
    pilot.press("ctrl+v")
    _settle(pilot)

    assert state.editor_project() == before
    assert state.undo_label() == label
    assert not editor.store.in_transaction
    assert _last_toast_has("selecciona")


# -- puntos de envolvente ----------------------------------------------------------------


def test_copiar_y_pegar_punto_de_envolvente_en_un_clip(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    clip = state.editor_project().video.clips[0]
    _audio_key(editor, pilot, clip, 0.5, 0.4)
    editor.select_envelope_key("video", clip.id, -1, 0.5)
    _settle(pilot)

    pilot.press("ctrl+c")
    _settle(pilot)
    clipboard = editor._key_clipboard
    assert clipboard is not None
    assert (clipboard.kind, clipboard.channel, clipboard.easing) == ("audio", "", "linear")
    assert clipboard.value == pytest.approx(0.4)

    editor.seek(1.5)
    _settle(pilot)
    pilot.press("ctrl+v")
    _settle(pilot)
    envelope = state.editor_project().clip(clip.id).envelope
    assert [p.t for p in envelope] == pytest.approx([0.5, 1.5])
    assert [p.gain for p in envelope] == pytest.approx([0.4, 0.4])
    assert state.undo_label() == "pegar punto de volumen"

    editor.undo()
    _settle(pilot)
    assert len(state.editor_project().clip(clip.id).envelope) == 1
    editor.redo()
    _settle(pilot)
    assert len(state.editor_project().clip(clip.id).envelope) == 2


def test_pegar_punto_de_envolvente_reemplaza_y_funciona_en_un_elemento(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    clip = state.editor_project().video.clips[0]
    _audio_key(editor, pilot, clip, 0.5, 0.4)
    _audio_key(editor, pilot, clip, 1.5, 0.7)
    editor.select_envelope_key("video", clip.id, -1, 0.5)
    _settle(pilot)
    pilot.press("ctrl+c")
    _settle(pilot)

    editor.seek(1.5)
    _settle(pilot)
    pilot.press("ctrl+v")
    _settle(pilot)
    envelope = state.editor_project().clip(clip.id).envelope
    assert [p.t for p in envelope] == pytest.approx([0.5, 1.5])
    assert [p.gain for p in envelope] == pytest.approx([0.4, 0.4])
    editor.undo()
    _settle(pilot)
    assert [p.gain for p in state.editor_project().clip(clip.id).envelope] == pytest.approx(
        [0.4, 0.7]
    )

    asset = state.editor_project().assets[0]
    editor.add_video_element(asset.id)
    _settle(pilot)
    element = state.editor_project().elements[-1]
    editor.seek(element.start + 1.0)
    _settle(pilot)
    pilot.press("ctrl+v")
    _settle(pilot)
    envelope = state.editor_project().element(element.id).envelope
    assert len(envelope) == 1
    assert envelope[0].t == pytest.approx(1.0)
    assert envelope[0].gain == pytest.approx(0.4)
    assert state.undo_label() == "pegar punto de volumen"


# -- portapapeles de clips y campo de texto ---------------------------------------------


def test_el_portapapeles_de_clips_sigue_intacto(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    clip = state.editor_project().video.clips[0]
    editor.select_clip(clip.id)
    _settle(pilot)
    pilot.press("ctrl+c")
    _settle(pilot)
    assert state.clipboard_count() == 1
    assert editor._key_clipboard is None

    editor.seek(6.0)
    _settle(pilot)
    pilot.press("ctrl+v")
    _settle(pilot)
    assert len(state.editor_project().video.clips) == 2
    assert state.undo_label() == "pegar"

    editor.undo()
    _settle(pilot)
    element = _element_with_x_keys(editor, pilot)
    _select_key(editor, element, "x", 0.5)
    _settle(pilot)
    pilot.press("ctrl+c")
    _settle(pilot)
    assert editor._key_clipboard is not None
    assert editor._clipboard is None and state.clipboard_count() == 0

    editor.select_clip(clip.id)  # copiar un clip vuelve a dejar el portapapeles de clips
    _settle(pilot)
    pilot.press("ctrl+c")
    _settle(pilot)
    assert editor._key_clipboard is None
    assert state.clipboard_count() == 1


def test_atajos_con_pilot_y_campo_de_texto(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    element = _element_with_x_keys(editor, pilot)
    key = f"anim-key-element-{element.id}-0"
    pilot.click(key)
    _settle(pilot)
    assert state.keyframe_key() is not None

    pilot.press("ctrl+c")
    _settle(pilot)
    editor.seek(element.start + 2.0)
    _settle(pilot)
    pilot.press("ctrl+v")
    _settle(pilot)
    curve = anim.find(state.editor_project().element(element.id).animations, "x")
    assert curve is not None and len(curve.points) == 3
    assert state.undo_label() == "pegar clave X"

    # Un Ctrl+C dentro de un campo de texto no debe copiar la capa ni la clave.
    editor.add_element("rect")
    _settle(pilot)
    other = state.editor_project().elements[-1]
    editor.select_element(other.id)
    _settle(pilot)
    _ensure_visible(pilot, "insp-el-start")
    pilot.click("insp-el-start")
    _settle(pilot)
    label = state.undo_label()
    before = state.editor_project().element(other.id).animations
    pilot.press("ctrl+c")
    pilot.press("ctrl+v")
    _settle(pilot)
    assert editor._key_clipboard is not None and editor._key_clipboard.kind == "visual"
    assert state.clipboard_count() == 0
    assert state.editor_project().element(other.id).animations == before
    assert state.undo_label() == label
