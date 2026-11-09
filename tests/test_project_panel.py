"""Panel de medios/capas y selección directa sobre el preview.

Cubre lo que faltaba en la revisión: ver los assets del proyecto, insertarlos en el playhead,
seleccionar textos/elementos con el ratón y ver las capas aunque haya una selección.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from recstudio import state
from recstudio.core import elements as el
from recstudio.core import timeline as tl
from recstudio.core.errors import ProjectError
from recstudio.core.project import MediaAsset, Project, TextOverlay
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.ui import element_overlay


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
    path = tmp_path_factory.mktemp("panel") / "v.mp4"
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
        state.project_panel_open.set(True)
        state.project_panel_tab.set("media")
        state.video_track_target.set(0)
        state.preview_area.set(state.PreviewArea())
        state.screen.set("record")


def _open(controller, pilot, path: Path):  # noqa: ANN001
    editor = controller.editor
    editor.import_paths([path])
    assert _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))
    _settle(pilot)
    assert _wait(lambda: state.preview_area().width > 0)
    return editor


def _click_inspector(pilot, key: str) -> None:
    """El inspector hace scroll; asegura que el control quede visible antes de pulsarlo."""
    pilot.scroll("insp-scroll", dy=2000)
    pilot.settle()
    pilot.a11y_action(key, "scroll_into_view")
    pilot.settle()
    pilot.click(key)
    _settle(pilot)


# -- puro -------------------------------------------------------------------------


def _asset(path: str, kind: str = "video", duration: float = 4.0) -> MediaAsset:
    return MediaAsset(
        id=Path(path).stem, path=path, duration=duration, width=640, height=360, fps=30,
        has_audio=kind != "image", kind=kind,
    )


def test_insertar_clip_y_audio_en_posicion_y_quitar_asset(tmp_path: Path) -> None:
    project = Project(fps=30)
    project, _ = tl.insert_clip(project, _asset(str(tmp_path / "a.mp4")))
    project, clip = tl.insert_clip(project, _asset(str(tmp_path / "b.mp4")), 0)
    assert [c.id for c in project.video.clips][0] == clip.id

    project, track_index = tl.add_audio_track(project, "Música", mode="sequence")
    project, audio = tl.insert_audio_clip(project, track_index, _asset(str(tmp_path / "m.mp3"), "audio", 3.0), 1.0)
    assert project.audio[track_index].clips == (audio,)

    # El asset del audio está en uso: no se puede quitar; uno no usado, sí.
    with pytest.raises(ProjectError):
        tl.remove_asset(project, audio.asset_id)
    project, _ = tl.add_asset(project, _asset(str(tmp_path / "suelto.png"), "image"))
    project = tl.remove_asset(project, "suelto")
    assert all(asset.id != "suelto" for asset in project.assets)


def test_text_box_tiene_geometria_y_crece_con_el_tamano() -> None:
    project = Project(width=1920, height=1080)
    overlay = TextOverlay("t1", "Hola", 0.0, 2.0, x=0.5, y=0.8, size=42)
    small = element_overlay.text_box(project, overlay, (960, 540))
    big = element_overlay.text_box(project, TextOverlay("t2", "Hola", 0.0, 2.0, size=84), (960, 540))
    assert small is not None and big is not None
    assert small.width > 0 and small.height > 0
    assert big.width > small.width and big.height > small.height


def test_copiar_y_pegar_elemento_con_su_imagen(tmp_path: Path) -> None:
    project = Project(width=640, height=360, fps=30)
    project, clip = tl.insert_clip(project, _asset(str(tmp_path / "v.mp4")))
    image = _asset(str(tmp_path / "logo.png"), "image", 0.0)
    project, image = tl.add_asset(project, image)
    project, element = el.add_element(
        project, "image", 0.5, 1.0, asset_id=image.id, width=0.3, height=0.2,
    )
    clipboard = tl.copy_items(project, {element.id})
    assert clipboard.items[0].kind == "element"
    pasted, ids = tl.paste(project, clipboard, 0.0)
    assert len(pasted.elements) == 2 and len(ids) == 1
    assert pasted.elements[-1].asset_id == image.id


# -- UI ---------------------------------------------------------------------------


def test_panel_muestra_el_medio_y_lo_inserta_en_el_playhead(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    asset = state.editor_project().assets[0]
    assert pilot.rect("ed-project-panel") is not None
    assert pilot.rect(f"media-{asset.id}") is not None
    pilot.press("ctrl+b")
    _settle(pilot)
    assert pilot.rect("ed-project-panel") is None
    pilot.press("ctrl+b")
    _settle(pilot)
    assert pilot.rect("ed-project-panel") is not None
    editor.seek(2.0)
    pilot.click(f"media-add-{asset.id}")
    assert _wait(lambda: len(state.editor_project().video.clips) == 2)
    assert state.selection()[0] == "clip"
    editor.undo()
    assert _wait(lambda: len(state.editor_project().video.clips) == 1)


def test_clic_selecciona_clip_y_texto_y_lo_mueve(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.clear_selection()
    _settle(pilot)
    pilot.click("canvas-hit")
    _settle(pilot)
    assert state.selection()[0] == "clip"

    editor.add_text()
    _settle(pilot)
    overlay = state.editor_project().texts[0]
    assert pilot.rect("text-box") is not None
    before = (overlay.x, overlay.y)
    pilot.drag("text-box", dx=30, dy=15, steps=4)
    _settle(pilot)
    after = state.editor_project().texts[0]
    assert (after.x, after.y) != before
    assert state.undo_label() == "mover texto"

    editor.clear_selection()
    _settle(pilot)
    assert pilot.rect(f"text-hit-{overlay.id}") is not None
    pilot.click(f"text-hit-{overlay.id}")
    _settle(pilot)
    assert state.selection() == ("text", overlay.id)


def test_clic_selecciona_un_elemento_sin_pasar_por_el_inspector(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_element("rect")
    _settle(pilot)
    element = state.editor_project().elements[0]
    editor.clear_selection()
    _settle(pilot)
    assert pilot.rect(f"el-hit-{element.id}") is not None
    pilot.click(f"el-hit-{element.id}")
    _settle(pilot)
    assert state.selection() == ("element", element.id)
    assert pilot.rect("el-box") is not None


def test_asas_del_texto_cambian_el_tamano_y_undo(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_text()
    _settle(pilot)
    overlay = state.editor_project().texts[0]
    assert pilot.rect("text-handle-se") is not None
    before = overlay.size
    pilot.drag("text-handle-se", dx=40, dy=20, steps=4)
    _settle(pilot)
    after = state.editor_project().texts[0]
    assert after.size > before
    assert state.undo_label() == "escalar texto"
    editor.undo()
    _settle(pilot)
    assert state.editor_project().texts[0].size == before


def test_capas_siguen_visibles_con_seleccion(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_element("rect")
    _settle(pilot)
    element = state.editor_project().elements[0]
    state.project_panel_tab.set("layers")
    _settle(pilot)
    assert pilot.rect(f"layer-element-{element.id}") is not None
    assert pilot.rect("panel-add-rect") is not None
    assert pilot.rect("panel-add-pip") is not None
    # La lista de capas no desaparece al seleccionar: es su razón de ser.
    pilot.click(f"layer-element-{element.id}")
    _settle(pilot)
    assert state.selection() == ("element", element.id)
    assert pilot.rect(f"layer-element-{element.id}") is not None


def test_capa_de_elemento_en_el_timeline_se_mueve(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_element("rect")
    _settle(pilot)
    element = state.editor_project().elements[0]
    bar = f"tl-element-{element.id}"
    assert pilot.rect(bar) is not None
    before = element.start
    pilot.drag(bar, dx=60, dy=0, steps=4)
    _settle(pilot)
    after = state.editor_project().element(element.id)
    assert after.start > before
    assert state.undo_label() == "mover elemento"
    editor.undo()
    _settle(pilot)
    assert state.editor_project().element(element.id).start == pytest.approx(before)


def test_asas_de_la_capa_en_el_timeline_recortan(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_element("rect")
    _settle(pilot)
    element = state.editor_project().elements[0]
    handle = f"tl-trim-r-{element.id}"
    assert pilot.rect(handle) is not None
    before = element.end
    pilot.drag(handle, dx=-40, dy=0, steps=4)
    _settle(pilot)
    after = state.editor_project().element(element.id)
    assert after.end < before
    assert state.undo_label() == "recortar elemento"


def test_arrastrar_medio_del_panel_al_timeline(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    asset = state.editor_project().assets[0]
    before = len(state.editor_project().video.clips)
    pilot.drag_to(f"media-{asset.id}", "tl-content", steps=8)
    _settle(pilot)
    assert len(state.editor_project().video.clips) == before + 1
    editor.undo()
    assert _wait(lambda: len(state.editor_project().video.clips) == before)


def test_importar_imagen_al_panel_y_add_como_elemento(
    ui, video: Path, tmp_path: Path
) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    logo = tmp_path / "logo.png"
    from PIL import Image

    Image.new("RGBA", (120, 80), (0, 255, 0, 200)).save(logo)
    editor.add_media([logo])
    assert _wait(lambda: any(a.kind == "image" for a in state.editor_project().assets))
    _settle(pilot)
    asset = next(a for a in state.editor_project().assets if a.kind == "image")
    assert pilot.rect(f"media-{asset.id}") is not None
    pilot.click(f"media-add-{asset.id}")
    _settle(pilot)
    assert any(e.kind == "image" and e.asset_id == asset.id for e in state.editor_project().elements)
    assert state.selection()[0] == "element"


def test_soltar_archivo_en_el_panel_lo_importa(ui, video: Path, tmp_path: Path) -> None:
    _, controller, pilot = ui
    _open(controller, pilot, video)
    dropped = tmp_path / "drop.png"
    from PIL import Image

    Image.new("RGB", (64, 48), (255, 0, 0)).save(dropped)
    assert pilot.drop_files([dropped], key="ed-project-panel")
    assert _wait(lambda: any(a.name == "drop" for a in state.editor_project().assets))


def test_pip_desde_el_panel_y_trim_izquierdo_ajusta_la_fuente(ui, video: Path, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    pip = tmp_path / "pip.mp4"
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=320x240:rate=30:duration=4", "-c:v", "libx264", "-preset",
         "ultrafast", "-pix_fmt", "yuv420p", str(pip)],
        check=True, capture_output=True,
    )
    editor.add_media([pip])
    assert _wait(lambda: any(a.name == "pip" for a in state.editor_project().assets))
    _settle(pilot)
    asset = next(a for a in state.editor_project().assets if a.name == "pip")
    assert pilot.rect(f"media-pip-{asset.id}") is not None
    pilot.click(f"media-pip-{asset.id}")
    _settle(pilot)
    element = next(e for e in state.editor_project().elements if e.kind == "video")
    assert state.selection() == ("element", element.id)
    assert pilot.rect("el-box") is not None
    state.project_panel_tab.set("layers")
    _settle(pilot)
    assert pilot.rect(f"layer-element-{element.id}") is not None

    handle = f"tl-trim-l-{element.id}"
    assert pilot.rect(handle) is not None
    before = state.editor_project().element(element.id)
    pilot.drag(handle, dx=40, dy=0, steps=4)
    _settle(pilot)
    after = state.editor_project().element(element.id)
    assert after.start > before.start
    assert after.in_ > before.in_
    assert state.undo_label() == "recortar elemento"

    # La vía «Video PiP…» (diálogo) crea el mismo elemento sin pasar por el panel.
    editor.import_pip([pip])
    assert _wait(lambda: len([e for e in state.editor_project().elements if e.kind == "video"]) == 2)


def test_pistas_v2_v3_desde_el_panel_y_cambio_de_pista(
    ui, video: Path, tmp_path: Path,
) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    for name, color in (("v2", "red"), ("v3", "green")):
        subprocess.run(
            [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"color=c={color}:size=320x180:rate=30:duration=2",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             str(tmp_path / f"{name}.mp4")],
            check=True, capture_output=True,
        )
    editor.add_media([tmp_path / "v2.mp4", tmp_path / "v3.mp4"])
    assert _wait(lambda: {"v2", "v3"} <= {a.name for a in state.editor_project().assets})
    _settle(pilot)
    assets = {a.name: a for a in state.editor_project().assets}

    # El destino del panel decide la pista; V2/V3 se añaden a pantalla completa.
    state.project_panel_tab.set("layers")
    _settle(pilot)
    for track in (0, 1, 2):
        assert pilot.rect(f"panel-video-target-{track}") is not None
    pilot.click("panel-video-target-1")
    _settle(pilot)
    assert state.video_track_target() == 1
    state.project_panel_tab.set("media")
    _settle(pilot)
    pilot.click(f"media-pip-{assets['v2'].id}")
    _settle(pilot)
    v2 = next(e for e in state.editor_project().elements if e.video_track == 1)
    assert (v2.width, v2.height, v2.fit) == (1.0, 1.0, "fill")
    assert pilot.rect(f"tl-element-{v2.id}") is not None
    assert pilot.rect("trk-add-1") is not None

    state.video_track_target.set(2)
    pilot.click(f"media-pip-{assets['v3'].id}")
    _settle(pilot)
    assert any(e.video_track == 2 for e in state.editor_project().elements)
    assert pilot.rect("trk-add-2") is not None

    # El inspector mueve el video entre V2, V3 y Elementos.
    editor.select_element(v2.id)
    _settle(pilot)
    assert state.insp_el_track() == 1
    pilot.scroll("insp-scroll", dy=2000)
    pilot.settle()
    assert pilot.rect("insp-el-track") is not None
    state.insp_el_track.set(2)
    _settle(pilot)
    moved = state.editor_project().element(v2.id)
    assert moved.video_track == 2
    assert state.undo_label() == "mover video a V3"
    assert pilot.rect(f"tl-element-{v2.id}") is not None
    state.insp_el_track.set(0)
    _settle(pilot)
    assert state.editor_project().element(v2.id).video_track == 0


def test_importar_subtitulos_estilo_y_exportar_srt(
    ui, video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    srt = tmp_path / "subs.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,500\nHola\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nAdiós\n",
        encoding="utf-8",
    )
    from recstudio.ui import file_dialogs

    monkeypatch.setattr(file_dialogs, "open_subtitles", lambda *a, **k: srt)
    editor.browse_subtitles()
    assert _wait(
        lambda: sum(t.subtitle for t in state.editor_project().texts) == 2
        and state.selection() is not None
        and state.selection()[0] == "text"
    )
    project = state.editor_project()
    subs = [t for t in project.texts if t.subtitle]
    assert [(t.start, t.end, t.text) for t in subs] == [
        (1.0, 2.5, "Hola"),
        (3.0, 4.0, "Adiós"),
    ]
    assert state.selection() == ("text", subs[0].id)
    _settle(pilot)
    assert pilot.rect(f"tl-text-{subs[0].id}") is not None
    state.project_panel_tab.set("layers")
    _settle(pilot)
    assert pilot.rect("panel-add-subtitles") is not None
    assert pilot.rect("panel-export-subtitles") is not None

    # El estilo del subtítulo seleccionado se copia al resto.
    editor.select_text(subs[0].id)
    _settle(pilot)
    state.insp_text_size.set(60)
    state.insp_text_color.set("#FFFF00")
    _settle(pilot)
    editor.apply_subtitle_style()
    _settle(pilot)
    styled = [t for t in state.editor_project().texts if t.subtitle]
    assert all(t.size == 60 and t.color == "#FFFF00" for t in styled)
    assert state.undo_label() == "aplicar estilo a los subtítulos"

    out = tmp_path / "salida.srt"
    monkeypatch.setattr(file_dialogs, "save_subtitles", lambda *a, **k: out)
    editor.export_subtitles()
    assert _wait(lambda: out.is_file() and out.stat().st_size > 0)
    text = out.read_text(encoding="utf-8")
    assert "00:00:01,000 --> 00:00:02,500" in text
    assert "Hola" in text and "Adiós" in text


def test_keyframes_de_elemento_y_texto_desde_el_inspector(
    ui, video: Path,
) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_element("rect")
    _settle(pilot)
    element = state.editor_project().elements[-1]
    editor.select_element(element.id)
    _settle(pilot)
    editor.seek(element.start)
    _settle(pilot)
    _click_inspector(pilot, "insp-element-key-x")
    current = state.editor_project().element(element.id)
    assert len(current.animations) == 1 and current.animations[0].channel == "x"
    assert len(current.animations[0].points) == 1

    editor.seek(element.start + 1.0)
    _settle(pilot)
    state.insp_el_x.set(0.8)
    _settle(pilot)
    _click_inspector(pilot, "insp-element-key-x")
    curve = state.editor_project().element(element.id).animations[0]
    assert [p.t for p in curve.points] == [0.0, 1.0]
    assert [p.value for p in curve.points] == [0.5, 0.8]
    assert state.undo_label() == "añadir clave X"
    _click_inspector(pilot, "insp-element-clear-x")
    assert state.editor_project().element(element.id).animations == ()

    # Escala: el campo (1.0 por defecto) se usa como valor de la clave.
    state.insp_el_scale.set(2.5)
    _settle(pilot)
    _click_inspector(pilot, "insp-element-key-scale")
    curve = state.editor_project().element(element.id).animations[0]
    assert curve.channel == "scale" and curve.points[0].value == pytest.approx(2.5)
    assert state.undo_label() == "añadir clave escala"
    _click_inspector(pilot, "insp-element-clear-scale")
    assert state.editor_project().element(element.id).animations == ()

    # Rotación: usa el campo de rotación del inspector (grados absolutos).
    state.insp_el_rotation.set(30.0)
    _settle(pilot)
    _click_inspector(pilot, "insp-element-key-rotation")
    curve = state.editor_project().element(element.id).animations[0]
    assert curve.channel == "rotation" and curve.points[0].value == pytest.approx(30.0)
    _click_inspector(pilot, "insp-element-clear-rotation")
    assert state.editor_project().element(element.id).animations == ()

    # Texto: opacidad animada desde el inspector.
    editor.add_text()
    _settle(pilot)
    text = state.editor_project().texts[-1]
    editor.select_text(text.id)
    _settle(pilot)
    editor.seek(text.start)
    _settle(pilot)
    _click_inspector(pilot, "insp-text-key-opacity")
    assert state.editor_project().text(text.id).animations[0].channel == "opacity"
    state.insp_text_opacity.set(0.25)
    _settle(pilot)
    editor.seek(text.start + 1.0)
    _settle(pilot)
    _click_inspector(pilot, "insp-text-key-opacity")
    curve = state.editor_project().text(text.id).animations[0]
    assert [p.value for p in curve.points] == [1.0, 0.25]

    # Escala del texto: usa el campo no persistido `insp_text_scale`.
    state.insp_text_scale.set(2.5)
    _settle(pilot)
    _click_inspector(pilot, "insp-text-key-scale")
    curve = next(a for a in state.editor_project().text(text.id).animations
                 if a.channel == "scale")
    assert curve.points[0].value == pytest.approx(2.5)
    assert state.undo_label() == "añadir clave escala"
    _click_inspector(pilot, "insp-text-clear-scale")
    assert all(a.channel != "scale" for a in state.editor_project().text(text.id).animations)

    # Rotación del texto: grados absolutos desde `insp_text_rotation`.
    state.insp_text_rotation.set(-30.0)
    _settle(pilot)
    _click_inspector(pilot, "insp-text-key-rotation")
    curve = next(a for a in state.editor_project().text(text.id).animations
                 if a.channel == "rotation")
    assert curve.points[0].value == pytest.approx(-30.0)
    _click_inspector(pilot, "insp-text-clear-rotation")
    assert all(a.channel != "rotation" for a in state.editor_project().text(text.id).animations)


def test_volumen_del_pip_en_el_inspector(ui, video: Path, tmp_path: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    pip = tmp_path / "pip_audio.mp4"
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:size=320x240:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
         "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(pip)],
        check=True, capture_output=True,
    )
    editor.add_media([pip])
    assert _wait(lambda: any(a.name == "pip_audio" for a in state.editor_project().assets))
    _settle(pilot)
    asset = next(a for a in state.editor_project().assets if a.name == "pip_audio")
    pilot.click(f"media-pip-{asset.id}")
    _settle(pilot)
    element = next(e for e in state.editor_project().elements if e.kind == "video")
    assert el.audio_gain(element) == 1.0
    pilot.scroll("insp-scroll", dy=2000)
    pilot.settle()
    assert pilot.rect("insp-el-gain") is not None
    assert pilot.rect("insp-el-mute") is not None

    _click_inspector(pilot, "insp-el-mute")
    muted = state.editor_project().element(element.id)
    assert el.audio_gain(muted) == 0.0
    assert state.insp_el_gain() == 0.0
    assert state.undo_label() == "volumen del video superpuesto"

    _click_inspector(pilot, "insp-el-unmute")
    restored = state.editor_project().element(element.id)
    assert el.audio_gain(restored) == 1.0
    assert state.insp_el_gain() == 1.0
