"""Color y tamaño animados de textos/subtítulos (docs/36): modelo, preview, export real y UI."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from recstudio import state
from recstudio.core import animation as anim
from recstudio.core import graph as G
from recstudio.core import store
from recstudio.core import text_color as tc
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

WIDTH, HEIGHT = 320, 180
RED = "#FF0000"
GREEN = "#00FF00"


def _solid(path: Path, color: str = "blue", *, duration: float = 3.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c={color}:size={WIDTH}x{HEIGHT}:rate=30:duration={duration}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def _base(tmp_path: Path, duration: float = 3.0) -> Project:
    source = _solid(tmp_path / "base.mp4", duration=duration)
    project = Project(width=WIDTH, height=HEIGHT, fps=30)
    project, _ = tl.insert_clip(project, media.probe(source))
    return project


def _animated_text(tmp_path: Path) -> tuple[Project, str]:
    project = _base(tmp_path)
    project, text = tl.add_text(
        project, "HOLA", 0.0, 2.5, x=0.5, y=0.5, size=20, color=RED, bold=True,
        animations=(anim.Animation(
            "size", (anim.Keyframe(0.0, 20.0), anim.Keyframe(2.0, 60.0)),
        ),),
        color_keys=(
            tc.ColorKeyframe(0.0, RED),
            tc.ColorKeyframe(2.0, GREEN),
        ),
    )
    return project.validate(), text.id


def _preview(project: Project, t: float) -> np.ndarray:
    renderer = playback.FrameRenderer(max_height=HEIGHT)
    try:
        frame = renderer.render(project, t)
    finally:
        renderer.close()
    assert frame is not None
    return frame


def _red_pixels(rgb: np.ndarray) -> int:
    mask = (rgb[:, :, 0] > 170) & (rgb[:, :, 1] < 90) & (rgb[:, :, 2] < 90)
    return int(mask.sum())


def _green_pixels(rgb: np.ndarray) -> int:
    mask = (rgb[:, :, 1] > 150) & (rgb[:, :, 0] < 110) & (rgb[:, :, 2] < 110)
    return int(mask.sum())


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


# -- modelo ---------------------------------------------------------------------------


def test_color_keys_round_trip_y_migracion_v11_a_v12(tmp_path: Path) -> None:
    keys = (tc.ColorKeyframe(0.0, RED), tc.ColorKeyframe(1.5, "#00ff00"))
    overlay = TextOverlay(
        "t1", "Hola", 0.5, 3.0, size=44, color="#123456", color_keys=keys,
    )
    project = Project(
        assets=(MediaAsset("a", str(tmp_path / "x.mp4"), 4.0, WIDTH, HEIGHT, 30, False),),
        video=Track("video", (Clip("c1", "a", 0.0, 4.0),)),
        texts=(overlay,), width=WIDTH, height=HEIGHT, fps=30,
    ).validate()
    assert SCHEMA_VERSION == 14
    data = json.loads(json.dumps(to_dict(project)))
    assert data["texts"][0]["color_keys"] == [
        {"t": 0.0, "color": RED},
        {"t": 1.5, "color": GREEN},  # normalizado a mayúsculas
    ]
    assert from_dict(data) == project

    # Un proyecto v11 no tiene `color_keys`: se abre sin curva y conserva su color base.
    old = json.loads(json.dumps(data))
    old["schema_version"] = 11
    old["texts"][0].pop("color_keys")
    migrated, read_only = store.migrate(old)
    assert read_only is False and migrated["schema_version"] == SCHEMA_VERSION
    restored = from_dict(migrated)
    assert restored.texts[0].color_keys == ()
    assert restored.texts[0].color == "#123456"


def test_validacion_de_color_y_tamano() -> None:
    for t in (-0.1, float("nan")):
        with pytest.raises(ProjectError):
            tc.ColorKeyframe(t, RED)
    for color in ("rojo", "#FFF", "#GG0000", "FF0000"):
        with pytest.raises(ProjectError):
            tc.ColorKeyframe(0.0, color)
    with pytest.raises(ProjectError):
        tc.validate((tc.ColorKeyframe(1.0, RED), tc.ColorKeyframe(1.0, GREEN)))
    with pytest.raises(ProjectError):
        TextOverlay("t", "Hola", 0.0, 2.0, color_keys=(
            tc.ColorKeyframe(2.0, RED), tc.ColorKeyframe(1.0, GREEN),
        ))

    # `set_keyframe` reemplaza la clave del mismo instante y mantiene el orden.
    keys = tc.set_keyframe((), 1.0, RED)
    keys = tc.set_keyframe(keys, 0.0, GREEN)
    keys = tc.set_keyframe(keys, 1.0, "#0000FF")
    assert [(key.t, key.color) for key in keys] == [(0.0, GREEN), (1.0, "#0000FF")]
    assert tc.clear(keys) == ()

    # Tamaño: mismo rango que TextOverlay.size, pero solo en canales de texto.
    assert anim.CHANNEL_RANGES["size"] == (8.0, 200.0)
    assert "size" in anim.TEXT_CHANNELS and "size" not in anim.ELEMENT_CHANNELS
    for value in (7.0, 201.0):
        with pytest.raises(ProjectError):
            anim.Animation("size", (anim.Keyframe(0.0, value),))
    with pytest.raises(ProjectError):
        TextOverlay(
            "t", "Hola", 0.0, 2.0,
            animations=(anim.Animation("size", (anim.Keyframe(0.0, 7.0),)),),
        )


def test_interpolacion_de_color_y_tamano_y_text_style_values() -> None:
    keys = (tc.ColorKeyframe(0.0, "#000000"), tc.ColorKeyframe(2.0, "#FFFFFF"))
    assert tc.value_at(keys, -1.0, "#123456") == "#000000"
    assert tc.value_at(keys, 0.0, "#123456") == "#000000"
    assert tc.value_at(keys, 1.0, "#123456") == "#808080"
    assert tc.value_at(keys, 2.0, "#123456") == "#FFFFFF"
    assert tc.value_at(keys, 9.0, "#123456") == "#FFFFFF"
    assert tc.value_at((), 1.0, "#123456") == "#123456"
    assert tc.rgb_at(keys, 1.0, "#123456") == (128, 128, 128)

    overlay = TextOverlay(
        "t", "Hola", 1.0, 4.0, size=20, color="#123456",
        animations=(anim.Animation(
            "size", (anim.Keyframe(0.0, 20.0), anim.Keyframe(2.0, 60.0)),
        ),),
        color_keys=(
            tc.ColorKeyframe(0.0, RED), tc.ColorKeyframe(2.0, GREEN),
        ),
    )
    size, color = tl.text_style_values(overlay, 2.0)  # t local 1.0 s
    assert size == pytest.approx(40.0)
    assert color == "#808000"  # rojo → verde al 50 % en sRGB
    assert tl.text_style_values(overlay, 0.0) == (20.0, RED)
    assert tl.text_style_values(overlay, 9.0) == (60.0, GREEN)

    # La firma pública de text_values no cambia: sigue devolviendo cinco valores.
    assert tl.text_values(overlay, 2.0) == (0.5, 0.82, 1.0, 1.0, 0.0)


# -- preview --------------------------------------------------------------------------


def test_preview_real_aplica_color_y_tamano(tmp_path: Path) -> None:
    project, _ = _animated_text(tmp_path)
    early = _preview(project, 0.1)
    late = _preview(project, 1.9)
    assert _red_pixels(early) > 80
    assert _green_pixels(early) < _red_pixels(early) * 0.1
    assert _green_pixels(late) > 80
    assert _red_pixels(late) < _green_pixels(late) * 0.1
    assert _green_pixels(late) > _red_pixels(early) * 2.0  # el texto crece con el tamaño


# -- export real ----------------------------------------------------------------------


def test_export_real_paridad_de_color_y_tamano(tmp_path: Path) -> None:
    project, text_id = _animated_text(tmp_path)
    spec, clips, out = _export(project, tmp_path)
    assert text_id in clips
    clip = clips[text_id]
    assert Path(clip.path).is_file() and Path(clip.path).stat().st_size > 0
    assert clip.frames == 75  # 2.5 s a 30 fps
    assert str(clip.path) in spec.args
    assert "overlay=" in spec.filter_complex and "eof_action=pass" in spec.filter_complex
    assert "drawtext=" not in spec.filter_complex

    for t in (0.1, 1.9):
        preview = _preview(project, t)
        exported = _export_frame(out, t)
        assert _red_pixels(exported) == pytest.approx(_red_pixels(preview), rel=0.35)
        assert _green_pixels(exported) == pytest.approx(_green_pixels(preview), rel=0.35)
    early, late = _export_frame(out, 0.1), _export_frame(out, 1.9)
    assert _red_pixels(early) > 60 and _green_pixels(early) < _red_pixels(early) * 0.15
    assert _green_pixels(late) > 60 and _red_pixels(late) < _green_pixels(late) * 0.15


def test_sin_color_ni_tamano_animados_las_cadenas_siguen_siendo_drawtext(tmp_path: Path) -> None:
    project = _base(tmp_path)
    project, text = tl.add_text(
        project, "HOLA", 0.0, 2.5, x=0.4, y=0.3, size=40, color=RED,
        animations=(anim.Animation("x", (anim.Keyframe(0.0, 0.3), anim.Keyframe(2.0, 0.7))),),
    )
    project = project.validate()
    assert text_animation.needs_bake(text) is False
    assert text_animation.materialize(
        project, (WIDTH, HEIGHT), fps=30, folder=tmp_path / "bake-vacio",
    ) == {}
    spec = G.build(project, G.preset_by_id("calidad"), tmp_path / "estatico.mp4",
                   encoder="libx264", exe=F.ffmpeg_exe(), text_clips={})
    assert "drawtext=" in spec.filter_complex
    assert "overlay=" not in spec.filter_complex
    assert "fontcolor=0xFF0000" in spec.filter_complex
    assert spec.filter_complex.endswith("[vtext]")
    assert G.text_filters(project, HEIGHT)[0] in spec.filter_complex


def test_los_textos_estaticos_y_con_color_o_tamano_mantienen_el_orden(tmp_path: Path) -> None:
    project = _base(tmp_path)
    project, _ = tl.add_text(project, "ABAJO", 0.0, 2.5, y=0.2, size=30)
    project, baked = tl.add_text(
        project, "MEDIO", 0.0, 2.5, y=0.5, size=30,
        animations=(anim.Animation("size", (anim.Keyframe(0.0, 20.0), anim.Keyframe(2.0, 40.0))),),
        color_keys=(tc.ColorKeyframe(0.0, RED), tc.ColorKeyframe(2.0, GREEN)),
    )
    project, _ = tl.add_text(project, "ARRIBA", 0.0, 2.5, y=0.8, size=30)
    project = project.validate()
    clips = text_animation.materialize(
        project, (WIDTH, HEIGHT), fps=30, folder=tmp_path / "bake",
    )
    assert set(clips) == {baked.id}
    spec = G.build(project, G.preset_by_id("calidad"), tmp_path / "orden.mp4",
                   encoder="libx264", exe=F.ffmpeg_exe(), text_clips=clips)
    fc = spec.filter_complex
    assert fc.index("text='ABAJO'") < fc.index("overlay=") < fc.index("text='ARRIBA'")


def test_el_grafo_falla_si_falta_el_intermedio_de_color(tmp_path: Path) -> None:
    project, _ = _animated_text(tmp_path)
    with pytest.raises(ProjectError):
        G.build(project, G.preset_by_id("calidad"), tmp_path / "sin-bake.mp4",
                encoder="libx264", exe=F.ffmpeg_exe(), text_clips={})


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
    path = tmp_path_factory.mktemp("color-size") / "v.mp4"
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


def _click_inspector(pilot, key: str) -> None:
    pilot.scroll("insp-scroll", dy=2000)
    pilot.settle()
    pilot.a11y_action(key, "scroll_into_view")
    pilot.settle()
    pilot.click(key)
    _settle(pilot)


def test_claves_de_color_y_tamano_desde_el_inspector(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_text()
    _settle(pilot)
    text = state.editor_project().texts[-1]
    editor.select_text(text.id)
    _settle(pilot)
    editor.seek(text.start)
    _settle(pilot)

    # Color: usa el color_picker del inspector (`insp_text_color`).
    state.insp_text_color.set(RED)
    _settle(pilot)
    _click_inspector(pilot, "insp-text-key-color")
    current = state.editor_project().text(text.id)
    assert len(current.color_keys) == 1 and current.color_keys[0].color == RED
    assert state.undo_label() == "añadir clave color"

    editor.seek(text.start + 1.0)
    _settle(pilot)
    state.insp_text_color.set("#0000FF")
    _settle(pilot)
    _click_inspector(pilot, "insp-text-key-color")
    current = state.editor_project().text(text.id)
    assert [key.color for key in current.color_keys] == [RED, "#0000FF"]
    assert [key.t for key in current.color_keys] == [0.0, 1.0]

    _click_inspector(pilot, "insp-text-clear-color")
    assert state.editor_project().text(text.id).color_keys == ()

    # Tamaño: usa el number_input existente (`insp_text_size`) y el canal «size».
    state.insp_text_size.set(64)
    _settle(pilot)
    _click_inspector(pilot, "insp-text-key-size")
    current = state.editor_project().text(text.id)
    curve = next(a for a in current.animations if a.channel == "size")
    assert curve.points[0].value == pytest.approx(64.0)
    assert state.undo_label() == "añadir clave tamaño"
    _click_inspector(pilot, "insp-text-clear-size")
    assert all(a.channel != "size" for a in state.editor_project().text(text.id).animations)
