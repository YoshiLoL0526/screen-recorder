"""Recorte interactivo sobre el preview (docs/43).

Cubre la geometría pura (mapeo del crop normalizado, mínimo por lado, clamp, movimiento y asas),
el proyecto de preview sin recortar (que no persiste nada), los arrastres de esquina/lado/interior
con `pilot`, undo/redo, el clic sin undo, `Esc`, la exclusividad con las otras herramientas y que
el modo no se activa con un elemento o un texto seleccionado.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from recstudio import state
from recstudio.core import crop_edit
from recstudio.core import transform as tx
from recstudio.core.project import Clip, MediaAsset, Project, Track, Transform, to_dict
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths


pytestmark = pytest.mark.usefixtures("ui")

HANDLES = tuple(f"crop-handle-{name}" for name in crop_edit.HANDLES)


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
    path = tmp_path_factory.mktemp("crop") / "v.mp4"
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
        state.crop_on.set(False)
        state.razor_on.set(False)
        state.marquee_on.set(False)
        state.env_edit_on.set(False)
        state.key_add_on.set(False)
        state.marquee.set(None)
        state.tl_drag.set(None)
        state.preview_area.set(state.PreviewArea())
        state.inspector_open.set(True)
        state.screen.set("record")


def _open(controller, pilot, path: Path):  # noqa: ANN001, ANN202
    editor = controller.editor
    editor.import_paths([path])
    assert _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))
    _settle(pilot)
    return editor


def _select(editor, pilot, t: float = 1.0):  # noqa: ANN001, ANN202
    clip = state.editor_project().video.clips[0]
    editor.select_clip(clip.id)
    editor.seek(t)
    _settle(pilot)
    assert _wait(lambda: state.preview_area().width > 0)
    return clip


def _enter_crop(editor, pilot):  # noqa: ANN001, ANN202
    clip = _select(editor, pilot)
    editor.toggle_crop()
    _settle(pilot)
    assert state.crop_on()
    return clip


# -- geometría pura -------------------------------------------------------------------


def _project(crop=(0.0, 0.0, 1.0, 1.0), *, rotation: int = 0, width: int = 640,
             height: int = 360, fit: str = "fit") -> tuple[Project, Clip]:
    asset = MediaAsset("a1", "v.mp4", 10.0, 640, 360, 30.0, False)
    clip = Clip(
        id="c1", asset_id="a1", in_=0.0, out_=10.0,
        transform=Transform(crop=tuple(crop), rotation=rotation),
    )
    project = Project(
        name="p", width=width, height=height, assets=(asset,),
        video=Track(kind="video", clips=(clip,)), default_fit=fit,
    )
    return project, clip


def test_mapeo_del_crop_al_rectangulo_del_escenario() -> None:
    project, clip = _project(crop=(0.25, 0.1, 0.75, 0.9))
    layout = crop_edit.full_layout(project, clip, (640, 360))
    box = crop_edit.crop_box(layout, clip.transform.crop)
    assert (box.left, box.top, box.right, box.bottom) == pytest.approx((160.0, 36.0, 480.0, 324.0))
    assert box.width == pytest.approx(320.0)
    assert box.height == pytest.approx(288.0)

    # El escenario puede ser más pequeño que el lienzo: el mapeo sigue en píxeles del escenario.
    small = crop_edit.full_layout(project, clip, (320, 180))
    small_box = crop_edit.crop_box(small, clip.transform.crop)
    assert (small_box.left, small_box.top, small_box.right, small_box.bottom) == pytest.approx(
        (80.0, 18.0, 240.0, 162.0)
    )


def test_mapeo_funciona_con_rotacion_y_volteo() -> None:
    # La rotación de 90 intercambia el tamaño de origen; el crop se aplica después y se mapea
    # sobre la imagen ya rotada, sin geometría especial.
    project, clip = _project(crop=(0.1, 0.2, 0.9, 0.8), rotation=90, width=360, height=640)
    layout = crop_edit.full_layout(project, clip, (360, 640))
    assert (layout.source_width, layout.source_height) == (360, 640)
    assert (layout.out_width, layout.out_height) == (360, 640)
    box = crop_edit.crop_box(layout, clip.transform.crop)
    assert (box.left, box.top, box.right, box.bottom) == pytest.approx((36.0, 128.0, 324.0, 512.0))

    flipped = replace(clip, transform=replace(clip.transform, rotation=0, flip_h=True, flip_v=True))
    flip_project = replace(project, width=640, height=360)
    layout_flip = crop_edit.full_layout(flip_project, flipped, (640, 360))
    assert (layout_flip.out_width, layout_flip.out_height) == (640, 360)
    box_flip = crop_edit.crop_box(layout_flip, flipped.transform.crop)
    assert (box_flip.left, box_flip.top, box_flip.right, box_flip.bottom) == pytest.approx(
        (64.0, 72.0, 576.0, 288.0)
    )


def test_proyecto_de_preview_sin_recortar_no_muta_el_modelo() -> None:
    project, clip = _project(crop=(0.2, 0.1, 0.8, 0.9))
    before = to_dict(project)
    display = crop_edit.uncropped_project(project, clip.id)
    assert display != project
    assert display.clip(clip.id).transform.crop == crop_edit.FULL_CROP
    # El modelo original conserva su recorte y su serialización.
    assert project.clip(clip.id).transform.crop == (0.2, 0.1, 0.8, 0.9)
    assert to_dict(project) == before
    # Un clip sin transformación ya se ve completo: no se crea una copia distinta.
    plain, plain_clip = _project()
    plain = replace(plain, video=replace(plain.video, clips=(replace(plain_clip, transform=None),)))
    assert crop_edit.uncropped_project(plain, "c1") is plain
    assert crop_edit.uncropped_project(project, "desconocido") is project


def test_minimo_por_lado_clamp_y_movimiento() -> None:
    crop = (0.1, 0.2, 0.9, 0.8)
    # Un lado no puede cruzar al opuesto ni salir de 0…1: se queda a 5 % del borde.
    assert crop_edit.resize_crop(crop, "l", 0.95, 0.0) == pytest.approx(
        (0.85, 0.2, 0.9, 0.8)
    )
    assert crop_edit.resize_crop(crop, "t", 0.0, -0.5) == pytest.approx(
        (0.1, 0.0, 0.9, 0.8)
    )
    assert crop_edit.resize_crop(crop, "br", 0.5, 0.5) == pytest.approx(
        (0.1, 0.2, 1.0, 1.0)
    )
    # Esquina: mueve los dos lados a la vez.
    assert crop_edit.resize_crop(crop, "tl", 0.05, 0.1) == pytest.approx(
        (0.15, 0.3, 0.9, 0.8)
    )
    # Mover conserva el tamaño y se pega al borde si no cabe.
    moved = crop_edit.move_crop(crop, 0.5, -0.5)
    assert moved == pytest.approx((0.2, 0.0, 1.0, 0.6))
    assert (moved[2] - moved[0], moved[3] - moved[1]) == pytest.approx((0.8, 0.6))
    assert crop_edit.move_crop(crop, -0.5, 0.0) == pytest.approx((0.0, 0.2, 0.8, 0.8))
    # Un recorte preexistente más estrecho que el mínimo sigue siendo válido al arrastrar.
    tiny = crop_edit.resize_crop((0.99, 0.0, 1.0, 1.0), "r", 0.5, 0.0)
    assert tiny[0] < tiny[2] <= 1.0
    with pytest.raises(ValueError):
        crop_edit.resize_crop(crop, "centro", 0.1, 0.1)


def test_visible_box_y_asas_siempre_dentro_del_escenario() -> None:
    box = crop_edit.CropBox(-20.0, -10.0, 120.0, 80.0)
    visible = crop_edit.visible_box(box, (100, 60))
    assert visible == crop_edit.CropBox(0.0, 0.0, 100.0, 60.0)
    assert crop_edit.visible_box(crop_edit.CropBox(200, 200, 300, 300), (100, 60)) is None

    centres = crop_edit.handle_centres(visible, (100, 60), 14.0)
    assert set(centres) == set(crop_edit.HANDLES)
    for x, y in centres.values():
        assert 7.0 <= x <= 93.0 and 7.0 <= y <= 53.0


# -- UI con pilot ----------------------------------------------------------------------


def test_activar_recorte_muestra_velo_ocho_asas_y_oculta_las_cajas_normales(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    _enter_crop(editor, pilot)
    assert pilot.rect("crop-body") is not None
    for key in HANDLES:
        assert pilot.rect(key) is not None, f"falta {key}"
    # Las capas normales de selección no se comen los clics del recorte.
    assert pilot.rect("canvas-body") is None
    assert pilot.rect("canvas-handle-br") is None


def test_el_switch_del_inspector_activa_y_desactiva_el_modo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    _select(editor, pilot)
    pilot.scroll("insp-scroll", dy=400)
    pilot.settle()
    assert pilot.rect("insp-crop-mode") is not None
    pilot.click("insp-crop-mode")
    _settle(pilot)
    assert state.crop_on()
    assert pilot.rect("crop-body") is not None
    pilot.click("insp-crop-mode")
    _settle(pilot)
    assert not state.crop_on()
    assert pilot.rect("crop-body") is None


def test_arrastres_de_esquina_lado_e_interior_con_undo_y_redo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    clip = _enter_crop(editor, pilot)
    editor.set_transform(crop=(0.2, 0.2, 0.8, 0.8))
    _settle(pilot)
    start = state.editor_project().clip(clip.id).transform
    assert start is not None

    pilot.drag("crop-handle-tl", dx=30, dy=18, steps=6)
    _settle(pilot)
    corner = state.editor_project().clip(clip.id).transform
    assert corner is not None and corner.crop[0] > start.crop[0] and corner.crop[1] > start.crop[1]
    assert state.undo_label() == "recortar clip"
    # Los campos del inspector se actualizan tras el arrastre.
    assert float(state.insp_crop_l()) == pytest.approx(corner.crop[0], abs=1e-3)
    assert float(state.insp_crop_t()) == pytest.approx(corner.crop[1], abs=1e-3)
    editor.undo()
    _settle(pilot)
    assert state.editor_project().clip(clip.id).transform.crop == pytest.approx(start.crop)

    pilot.drag("crop-handle-r", dx=-35, steps=6)
    _settle(pilot)
    side = state.editor_project().clip(clip.id).transform
    assert side is not None and side.crop[2] < start.crop[2]
    editor.undo()
    _settle(pilot)

    pilot.drag("crop-body", dx=20, dy=12, steps=6)
    _settle(pilot)
    moved = state.editor_project().clip(clip.id).transform
    assert moved is not None
    assert moved.crop[0] > start.crop[0] and moved.crop[1] > start.crop[1]
    assert moved.crop[2] - moved.crop[0] == pytest.approx(start.crop[2] - start.crop[0], abs=1e-3)
    assert moved.crop[3] - moved.crop[1] == pytest.approx(start.crop[3] - start.crop[1], abs=1e-3)
    editor.undo()
    _settle(pilot)
    assert state.editor_project().clip(clip.id).transform.crop == pytest.approx(start.crop)
    editor.redo()
    _settle(pilot)
    assert state.editor_project().clip(clip.id).transform.crop == pytest.approx(moved.crop)


def test_clic_sin_movimiento_no_crea_undo_ni_transaccion(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    clip = _enter_crop(editor, pilot)
    editor.set_transform(crop=(0.2, 0.2, 0.8, 0.8))
    _settle(pilot)
    before = state.editor_project()
    label = state.undo_label()
    pilot.click("crop-body")
    _settle(pilot)
    assert state.editor_project() == before
    assert state.undo_label() == label
    assert not editor.store.in_transaction
    assert state.crop_on()  # el clic no sale del modo


def test_escape_cancela_el_gesto_y_despues_sale_del_modo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    clip = _enter_crop(editor, pilot)
    before = state.editor_project()
    label = state.undo_label()

    editor.begin_crop_drag("l")
    editor.drag_crop(40, 0)
    _settle(pilot)
    assert state.editor_project() != before
    pilot.press("escape")
    _settle(pilot)
    assert state.editor_project() == before
    assert state.undo_label() == label
    assert state.crop_on()  # el primer Esc solo cancela el gesto

    pilot.press("escape")
    _settle(pilot)
    assert not state.crop_on()
    assert pilot.rect("crop-body") is None
    assert pilot.rect("canvas-body") is not None
    assert state.editor_project().clip(clip.id).transform is None


def test_el_modo_se_desactiva_al_cambiar_de_seleccion_y_con_las_herramientas(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)

    clip = _enter_crop(editor, pilot)
    editor.select_clip(None)
    _settle(pilot)
    assert not state.crop_on()

    for toggle in (editor.toggle_razor, editor.toggle_marquee, editor.toggle_envelope_edit,
                   editor.toggle_key_add):
        editor.select_clip(clip.id)
        _settle(pilot)
        editor.toggle_crop()
        _settle(pilot)
        assert state.crop_on()
        toggle()
        _settle(pilot)
        assert not state.crop_on(), f"{toggle.__name__} no apagó el recorte"
        editor.select_tool()
        _settle(pilot)


def test_no_se_activa_con_un_elemento_ni_con_un_texto_seleccionados(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_element("rect")
    _settle(pilot)
    editor.toggle_crop()
    _settle(pilot)
    assert not state.crop_on()
    assert any("Selecciona un clip de video" in item.text for item in state.toasts())

    editor.select_clip(state.editor_project().video.clips[0].id)
    editor.add_text()
    _settle(pilot)
    assert state.selection() is not None and state.selection()[0] == "text"
    state.crop_on.set(True)  # como si el switch se hubiera quedado activo
    _settle(pilot)
    assert not state.crop_on()
    assert any("Selecciona un clip de video" in item.text for item in state.toasts())


def test_el_preview_sin_recortar_no_persiste_nada_y_los_campos_siguen_funcionando(
    ui, video: Path
) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    clip = _select(editor, pilot)
    editor.set_transform(crop=(0.25, 0.1, 0.75, 0.9))
    _settle(pilot)
    before = state.editor_project()
    crop_before = before.clip(clip.id).transform.crop
    frame_before = state.preview_frame()

    editor.toggle_crop()
    _settle(pilot)
    assert state.crop_on()
    # El modelo y su serialización no cambian: el recorte completo es solo del reproductor.
    assert state.editor_project() == before
    assert state.editor_project().clip(clip.id).transform.crop == crop_before
    assert to_dict(state.editor_project())["video"]["clips"][0]["transform"]["crop"] == list(crop_before)
    display = crop_edit.uncropped_project(before, clip.id)
    assert display.clip(clip.id).transform.crop == crop_edit.FULL_CROP
    # El frame se vuelve a pintar sin el recorte del modelo.
    assert _wait(lambda: state.preview_frame() != frame_before), "el preview no se repintó"

    # Los campos L/T/R/B del inspector siguen funcionando con el modo activo.
    editor.submit_transform_crop(0, "0.3")
    _settle(pilot)
    assert state.crop_on()
    assert state.editor_project().clip(clip.id).transform.crop[0] == pytest.approx(0.3)
    assert to_dict(state.editor_project())["video"]["clips"][0]["transform"]["crop"][0] == pytest.approx(0.3)

    editor.toggle_crop()
    _settle(pilot)
    assert not state.crop_on()
    assert state.editor_project().clip(clip.id).transform.crop == pytest.approx(
        (0.3, 0.1, 0.75, 0.9)
    )


def test_la_geometria_del_recorte_usa_el_layout_completo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    clip = _enter_crop(editor, pilot)
    editor.set_transform(crop=(0.2, 0.2, 0.8, 0.8))
    _settle(pilot)
    project = state.editor_project()
    stage = (state.preview_area().width, state.preview_area().height)
    from recstudio.core import canvas_edit as ce

    stage = ce.fit_stage(project.width, project.height, *stage)
    layout = crop_edit.full_layout(project, project.clip(clip.id), stage)
    box = crop_edit.crop_box(layout, project.clip(clip.id).transform.crop)
    rect = pilot.rect("crop-body")
    stage_rect = pilot.rect("ed-stage")
    assert rect is not None and stage_rect is not None
    local = (rect[0] - stage_rect[0], rect[1] - stage_rect[1], rect[2], rect[3])
    assert local[0] == pytest.approx(box.left, abs=2.0)
    assert local[1] == pytest.approx(box.top, abs=2.0)
    assert local[2] == pytest.approx(box.width, abs=2.0)
    assert local[3] == pytest.approx(box.height, abs=2.0)
    # El recuadro es menor que el clip completo.
    full = crop_edit.crop_box(layout, crop_edit.FULL_CROP)
    assert box.width < full.width and box.height < full.height
