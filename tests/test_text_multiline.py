"""Texto multilínea con ancho máximo (docs/45): wrap, modelo, preview, export y UI."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from recstudio import state
from recstudio.core import graph as G
from recstudio.core import store
from recstudio.core import text_layout
from recstudio.core import timeline as tl
from recstudio.core.errors import ProjectError
from recstudio.core.project import (
    SCHEMA_VERSION,
    Clip,
    MediaAsset,
    Project,
    TextOverlay,
    Track,
    from_dict,
    to_dict,
)
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import media, playback, text_animation
from recstudio.ui import element_overlay

WIDTH, HEIGHT = 320, 180


# -- helper de wrap -------------------------------------------------------------------


def test_wrap_por_palabras_y_limites() -> None:
    # 0,6 × 100 px = 60 px: «hola» ocupa 36,2 y «hola mundo» 93,8 (estimación Arial).
    assert text_layout.wrap_text(
        "hola mundo", size=20, wrap_width=0.6, project_width=100,
    ) == ("hola", "mundo")
    # Con el ancho completo, la misma frase cabe en una línea.
    assert text_layout.wrap_text(
        "hola mundo", size=20, wrap_width=1.0, project_width=100,
    ) == ("hola mundo",)
    # Sin límite no hay corte.
    assert text_layout.wrap_text(
        "hola mundo", size=20, wrap_width=0.0, project_width=100,
    ) == ("hola mundo",)


def test_wrap_respeta_newlines_y_colapsa_espacios() -> None:
    assert text_layout.wrap_text(
        "  uno \n dos   tres ", size=20, wrap_width=0.0, project_width=100,
    ) == ("uno", "dos tres")
    assert text_layout.wrap_text(
        "uno\n\ndos", size=20, wrap_width=0.0, project_width=100,
    ) == ("uno", "", "dos")


def test_wrap_parte_palabras_largas() -> None:
    word = "supercalifragilisticoespialidoso"
    lines = text_layout.wrap_text(
        word, size=10, wrap_width=0.3, project_width=100,
    )
    assert len(lines) > 1
    assert "".join(lines) == word
    assert all(
        text_layout.estimate_width(line, 10) <= 30.0 + 1e-6 for line in lines
    )


# -- modelo ---------------------------------------------------------------------------


def _project_with_text(overlay: TextOverlay) -> Project:
    asset = MediaAsset("a", "x.mp4", 4.0, WIDTH, HEIGHT, 30, False)
    return Project(
        assets=(asset,), video=Track("video", (Clip("c1", "a", 0.0, 4.0),)),
        texts=(overlay,), width=WIDTH, height=HEIGHT, fps=30,
    ).validate()


def test_wrap_width_serializa_y_migra_v14_a_v15() -> None:
    overlay = TextOverlay(
        "t1", "Hola mundo", 0.5, 3.0, size=44, wrap_width=0.45,
    )
    project = _project_with_text(overlay)
    assert SCHEMA_VERSION == 15
    data = json.loads(json.dumps(to_dict(project)))
    assert data["texts"][0]["wrap_width"] == 0.45
    assert from_dict(data) == project

    # Un proyecto v14 no tiene `wrap_width`: se abre sin límite y se ve igual que antes.
    old = json.loads(json.dumps(data))
    old["schema_version"] = 14
    old["texts"][0].pop("wrap_width")
    migrated, read_only = store.migrate(old)
    assert read_only is False and migrated["schema_version"] == 15
    assert from_dict(migrated).texts[0].wrap_width == 0.0


def test_wrap_width_valida_y_se_edita_en_el_modelo() -> None:
    for value in (-0.1, 0.05, 1.01, float("nan")):
        with pytest.raises(ProjectError):
            TextOverlay("t", "Hola", 0.0, 2.0, wrap_width=value)
    for value in (0.0, 0.1, 0.5, 1.0):
        assert TextOverlay("t", "Hola", 0.0, 2.0, wrap_width=value).wrap_width == value

    project = _project_with_text(TextOverlay("t", "Hola", 0.0, 2.0))
    project, text = tl.add_text(project, "Hola mundo", 0.0, 2.0, wrap_width=0.3)
    assert text.wrap_width == 0.3
    project = tl.update_text(project, text.id, wrap_width=0.6)
    assert project.text(text.id).wrap_width == 0.6
    with pytest.raises(ProjectError):
        tl.update_text(project, text.id, wrap_width=0.02)


# -- preview --------------------------------------------------------------------------


def _solid(path: Path, color: str, *, duration: float = 3.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c={color}:size={WIDTH}x{HEIGHT}:rate=30:duration={duration}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def _base(tmp_path: Path, duration: float = 3.0) -> Project:
    source = _solid(tmp_path / "base.mp4", "blue", duration=duration)
    project = Project(width=WIDTH, height=HEIGHT, fps=30)
    project, _ = tl.insert_clip(project, media.probe(source))
    return project


def _wrapped_text(tmp_path: Path) -> tuple[Project, TextOverlay]:
    project = _base(tmp_path)
    project, text = tl.add_text(
        project, "hola mundo largo", 0.0, 2.5, x=0.5, y=0.5, size=30,
        color="#FFFFFF", bold=True, wrap_width=0.3,
    )
    return project.validate(), text


def _white_box(rgb: np.ndarray) -> tuple[int, int, int, int, int]:
    white = (rgb[:, :, 0] > 220) & (rgb[:, :, 1] > 220) & (rgb[:, :, 2] > 220)
    ys, xs = np.nonzero(white)
    assert len(xs) > 30, f"texto blanco insuficiente: {len(xs)} px"
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()), int(len(xs))


def _preview(project: Project, t: float) -> np.ndarray:
    renderer = playback.FrameRenderer(max_height=HEIGHT)
    try:
        frame = renderer.render(project, t)
    finally:
        renderer.close()
    assert frame is not None
    return frame


def test_preview_dibuja_varias_lineas_y_la_caja_las_cubre(tmp_path: Path) -> None:
    project, text = _wrapped_text(tmp_path)
    assert text_layout.wrap_text(
        text.text, size=text.size, wrap_width=text.wrap_width, project_width=project.width,
    ) == ("hola", "mundo", "largo")
    rgb = _preview(project, 0.5)
    box = _white_box(rgb)
    # Tres líneas ocupan bastante más que una sola línea de 30 px.
    assert box[3] - box[1] > 60
    assert box[2] - box[0] <= int(round(0.3 * WIDTH)) + 12
    assert (box[0] + box[2]) / 2 == pytest.approx(WIDTH / 2, abs=4)
    assert (box[1] + box[3]) / 2 == pytest.approx(HEIGHT / 2, abs=4)

    frame_box = element_overlay.text_box(project, text, (WIDTH, HEIGHT))
    assert frame_box is not None
    assert frame_box.left <= box[0] and frame_box.top <= box[1]
    assert frame_box.left + frame_box.width >= box[2]
    assert frame_box.top + frame_box.height >= box[3]
    assert frame_box.center_x == pytest.approx(WIDTH / 2, abs=2)
    assert frame_box.center_y == pytest.approx(HEIGHT / 2, abs=2)


def test_texto_de_una_linea_sin_wrap_conserva_el_drawtext(tmp_path: Path) -> None:
    project = _base(tmp_path)
    project, text = tl.add_text(
        project, "HOLA", 0.0, 2.5, x=0.4, y=0.3, size=40,
    )
    project = project.validate()
    assert text_animation.needs_bake(text) is False
    assert text_animation.materialize(
        project, (WIDTH, HEIGHT), fps=30, folder=tmp_path / "bake-vacio",
    ) == {}
    spec = G.build(
        project, G.preset_by_id("calidad"), tmp_path / "estatico.mp4",
        encoder="libx264", exe=F.ffmpeg_exe(), text_clips={},
    )
    assert "drawtext=" in spec.filter_complex
    assert "overlay=" not in spec.filter_complex
    assert spec.filter_complex.endswith("[vtext]")
    assert G.text_filters(project, HEIGHT)[0] in spec.filter_complex


def test_el_orden_de_textos_estaticos_y_envueltos_se_mantiene(tmp_path: Path) -> None:
    project = _base(tmp_path)
    project, _ = tl.add_text(project, "ABAJO", 0.0, 2.5, y=0.2, size=30)
    project, baked = tl.add_text(
        project, "hola mundo largo", 0.0, 2.5, y=0.5, size=30, wrap_width=0.3,
    )
    project, _ = tl.add_text(project, "ARRIBA", 0.0, 2.5, y=0.8, size=30)
    project = project.validate()
    clips = text_animation.materialize(
        project, (WIDTH, HEIGHT), fps=30, folder=tmp_path / "bake",
    )
    assert set(clips) == {baked.id}
    spec = G.build(
        project, G.preset_by_id("calidad"), tmp_path / "orden.mp4",
        encoder="libx264", exe=F.ffmpeg_exe(), text_clips=clips,
    )
    fc = spec.filter_complex
    assert fc.index("text='ABAJO'") < fc.index("overlay=") < fc.index("text='ARRIBA'")


# -- export real ----------------------------------------------------------------------


def _export(project: Project, tmp_path: Path, *, name: str = "out.mp4"):
    preset = G.preset_by_id("calidad")
    size = G.output_size(project, preset)
    clips = text_animation.materialize(
        project, size, fps=project.fps, folder=tmp_path / "bake",
    )
    out = tmp_path / name
    spec = G.build(
        project, preset, out, encoder="libx264", exe=F.ffmpeg_exe(), text_clips=clips,
    )
    result = subprocess.run(spec.args, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-900:]
    return spec, clips, out


def _export_frame(path: Path, t: float) -> np.ndarray:
    raw = subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(HEIGHT, WIDTH, 3)


def test_export_real_hornea_el_texto_envuelto_y_coincide_con_el_preview(tmp_path: Path) -> None:
    project, text = _wrapped_text(tmp_path)
    assert text_animation.needs_bake(text) is True
    spec, clips, out = _export(project, tmp_path)
    assert text.id in clips
    clip = clips[text.id]
    assert Path(clip.path).is_file() and Path(clip.path).stat().st_size > 0
    assert clip.frames == 75
    assert str(clip.path) in spec.args
    assert "overlay=" in spec.filter_complex and "eof_action=pass" in spec.filter_complex
    assert "drawtext=" not in spec.filter_complex

    for t in (0.1, 1.9):
        preview = _white_box(_preview(project, t))
        exported = _white_box(_export_frame(out, t))
        assert exported[4] == pytest.approx(preview[4], rel=0.35)
        assert (exported[2] - exported[0]) == pytest.approx(preview[2] - preview[0], abs=6)
        assert (exported[3] - exported[1]) == pytest.approx(preview[3] - preview[1], abs=6)
        assert (exported[0] + exported[2]) / 2 == pytest.approx(
            (preview[0] + preview[2]) / 2, abs=4,
        )
        assert (exported[1] + exported[3]) / 2 == pytest.approx(
            (preview[1] + preview[3]) / 2, abs=4,
        )


# -- UI -------------------------------------------------------------------------------


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
    path = tmp_path_factory.mktemp("multiline") / "v.mp4"
    try:
        subprocess.run(
            [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "testsrc2=size=640x360:rate=30:duration=4", "-c:v", "libx264",
             "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
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


def test_ui_ancho_maximo_desde_el_inspector(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_text()
    _settle(pilot)
    text = state.editor_project().texts[-1]
    editor.select_text(text.id)
    _settle(pilot)

    pilot.scroll("insp-scroll", dy=2000)
    pilot.settle()
    assert pilot.rect("insp-text-wrap") is not None

    state.insp_text_wrap.set(0.35)
    _settle(pilot)
    current = state.editor_project().text(text.id)
    assert current.wrap_width == pytest.approx(0.35)
    assert state.undo_label() == "editar texto"

    # Un valor por debajo del mínimo se normaliza; vacío/negativo vuelve a «sin límite».
    state.insp_text_wrap.set(0.05)
    _settle(pilot)
    assert state.editor_project().text(text.id).wrap_width == pytest.approx(0.1)

    state.insp_text_wrap.set(0.0)
    _settle(pilot)
    assert state.editor_project().text(text.id).wrap_width == 0.0
    assert state.insp_text_wrap() == 0.0
