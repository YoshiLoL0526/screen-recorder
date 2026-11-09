"""Easing de las claves de animación (docs/39): curvas, expresiones, modelo, export y UI."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from recstudio import state
from recstudio.core import animation as anim
from recstudio.core import elements as el
from recstudio.core import graph as G
from recstudio.core import store
from recstudio.core import timeline as tl
from recstudio.core.errors import ProjectError
from recstudio.core.project import SCHEMA_VERSION, Project, TextOverlay, from_dict, to_dict
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import element_assets, media, playback


def test_ease_curvas_normalizadas() -> None:
    assert anim.ease(0.0, "linear") == 0.0
    assert anim.ease(0.5, "linear") == 0.5
    assert anim.ease(1.0, "linear") == 1.0
    assert anim.ease(0.5, "ease_in") == pytest.approx(0.125)
    assert anim.ease(0.5, "ease_out") == pytest.approx(0.875)
    assert anim.ease(0.25, "ease_in_out") == pytest.approx(0.0625)
    assert anim.ease(0.5, "ease_in_out") == pytest.approx(0.5)
    assert anim.ease(0.75, "ease_in_out") == pytest.approx(0.9375)
    # Fuera de 0…1 no se extrapola.
    assert anim.ease(-2.0, "ease_in") == 0.0
    assert anim.ease(2.0, "ease_out") == 1.0
    with pytest.raises(ProjectError):
        anim.ease(0.5, "bounce")  # type: ignore[arg-type]


def test_value_at_usa_el_easing_de_la_clave_izquierda() -> None:
    def curve(easing: str) -> anim.Animation:
        return anim.Animation("x", (
            anim.Keyframe(0.0, 0.0, easing=easing),  # type: ignore[arg-type]
            anim.Keyframe(1.0, 1.0),
        ))

    assert curve("linear").value_at(0.5, 0.0) == pytest.approx(0.5)
    assert curve("ease_in").value_at(0.5, 0.0) == pytest.approx(0.125)
    assert curve("ease_out").value_at(0.5, 0.0) == pytest.approx(0.875)
    assert curve("ease_in_out").value_at(0.25, 0.0) == pytest.approx(0.0625)
    assert curve("ease_in_out").value_at(0.75, 0.0) == pytest.approx(0.9375)

    # El easing del segundo punto no tiene tramo saliente: no cambia la curva.
    second = anim.Animation("x", (
        anim.Keyframe(0.0, 0.0, easing="linear"),
        anim.Keyframe(1.0, 1.0, easing="ease_in"),
    ))
    assert second.value_at(0.5, 0.0) == pytest.approx(0.5)


def test_expresiones_ffmpeg_por_curva_y_linear_intacta() -> None:
    linear = anim.Animation("x", (
        anim.Keyframe(0.0, 0.2), anim.Keyframe(1.0, 0.8),
    ))
    assert linear.expr(2.0, 0.5) == (
        "if(lt(t,2),0.2,if(lt(t,3),(0.2+(0.8-0.2)*(t-2)/1),0.8))"
    )

    def expression(easing: str) -> str:
        return anim.Animation("x", (
            anim.Keyframe(0.0, 0.2, easing=easing),  # type: ignore[arg-type]
            anim.Keyframe(1.0, 0.8),
        )).expr(2.0, 0.5)

    ease_in = expression("ease_in")
    assert "*((t-2)/1)*((t-2)/1)*((t-2)/1)" in ease_in
    ease_out = expression("ease_out")
    assert "(1-(1-((t-2)/1))*(1-((t-2)/1))*(1-((t-2)/1)))" in ease_out
    in_out = expression("ease_in_out")
    assert "if(lt(((t-2)/1),0.5)" in in_out
    assert "(4*((t-2)/1)*((t-2)/1)*((t-2)/1))" in in_out


def test_serializacion_easing_y_migracion_v12_v13(tmp_path: Path) -> None:
    project = Project(
        width=320, height=180, fps=30,
        texts=(
            # Un proyecto sin assets basta: las animaciones no los necesitan.
            TextOverlay(
                "t", "Hola", 0.0, 2.0,
                animations=(anim.Animation("x", (
                    anim.Keyframe(0.0, 0.2, "ease_in"),
                    anim.Keyframe(1.0, 0.8, "ease_in_out"),
                )),),
            ),
        ),
    )
    assert SCHEMA_VERSION == 13
    data = json.loads(json.dumps(to_dict(project)))
    points = data["texts"][0]["animations"][0]["points"]
    assert [point["easing"] for point in points] == ["ease_in", "ease_in_out"]
    assert from_dict(data) == project

    # Un proyecto v12 no tiene `easing`: se abre lineal y sin tocar las curvas.
    old = json.loads(json.dumps(data))
    old["schema_version"] = 12
    for point in old["texts"][0]["animations"][0]["points"]:
        point.pop("easing")
    migrated, read_only = store.migrate(old)
    assert read_only is False and migrated["schema_version"] == 13
    restored = from_dict(migrated)
    assert [point.easing for point in restored.texts[0].animations[0].points] == [
        "linear", "linear",
    ]

    with pytest.raises(ProjectError):
        anim.Keyframe(0.0, 0.5, "bounce")  # type: ignore[arg-type]


def test_set_keyframe_actualiza_el_easing_en_el_mismo_instante() -> None:
    animations = anim.set_keyframe((), "x", 1.0, 0.4, "ease_in")
    updated = anim.set_keyframe(animations, "x", 1.0, 0.5, "ease_out")
    curve = anim.find(updated, "x")
    assert curve is not None and len(curve.points) == 1
    assert curve.points[0].value == pytest.approx(0.5)
    assert curve.points[0].easing == "ease_out"
    with pytest.raises(ProjectError):
        anim.set_keyframe((), "x", 0.0, 0.5, "bounce")  # type: ignore[arg-type]


# -- preview y export real -------------------------------------------------------------


def _solid(path: Path, color: str, *, size: str = "320x180", duration: float = 3.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c={color}:size={size}:rate=30:duration={duration}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def _project(tmp_path: Path) -> Project:
    base = _solid(tmp_path / "base.mp4", "blue")
    pip = _solid(tmp_path / "pip.mp4", "red", size="100x100")
    project = Project(width=320, height=180, fps=30)
    project, _ = tl.insert_clip(project, media.probe(base))
    project, pip_asset = tl.add_asset(project, media.probe(pip))
    element_curve = anim.Animation("x", (
        anim.Keyframe(0.0, 0.25, "ease_in"),
        anim.Keyframe(1.0, 0.75),
    ))
    project, _ = el.add_element(
        project, "video", 0.0, 3.0, asset_id=pip_asset.id,
        x=0.25, y=0.65, width=0.2, height=0.4, animations=(element_curve,),
    )
    text_curve = anim.Animation("x", (
        anim.Keyframe(0.0, 0.25, "ease_in_out"),
        anim.Keyframe(1.0, 0.75),
    ))
    project, _ = tl.add_text(
        project, "HOLA", 0.0, 3.0, x=0.25, y=0.2, size=36, animations=(text_curve,),
    )
    return project.validate()


def _red_center(rgb: np.ndarray) -> float:
    red = (rgb[:, :, 0] > 150) & (rgb[:, :, 1] < 100) & (rgb[:, :, 2] < 100)
    _ys, xs = np.nonzero(red)
    assert len(xs) > 100
    return float(xs.mean())


def _white_center(rgb: np.ndarray) -> float | None:
    white = (rgb[:, :, 0] > 220) & (rgb[:, :, 1] > 220) & (rgb[:, :, 2] > 220)
    _ys, xs = np.nonzero(white)
    return None if len(xs) < 20 else float(xs.mean())


def _export_frame(path: Path, t: float) -> np.ndarray:
    raw = subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(180, 320, 3)


def _preview_frame(project: Project, t: float) -> np.ndarray:
    renderer = playback.FrameRenderer(max_height=180)
    try:
        frame = renderer.render(project, t)
    finally:
        renderer.close()
    assert frame is not None
    return frame


@pytest.mark.skipif(
    not Path("C:/Windows/Fonts/arial.ttf").exists(), reason="Arial no disponible",
)
def test_preview_y_export_con_ease_in_y_ease_in_out(tmp_path: Path) -> None:
    project = _project(tmp_path)
    # Elemento ease_in: en t=0.5 la lineal estaría en 160 px y la curva en 100 px.
    assert _red_center(_preview_frame(project, 0.5)) == pytest.approx(100, abs=7)
    # Texto ease_in_out: en t=0.25 la lineal estaría en 120 px y la curva en 90 px.
    assert _white_center(_preview_frame(project, 0.25)) == pytest.approx(90, abs=8)
    # En t=0.5 la in-out simétrica coincide con la lineal (160 px).
    assert _white_center(_preview_frame(project, 0.5)) == pytest.approx(160, abs=8)

    out = tmp_path / "out.mp4"
    sources = element_assets.materialize(project, G.output_size(project, G.PRESETS[0]))
    spec = G.build(
        project, G.PRESETS[0], out, encoder="libx264", exe=F.ffmpeg_exe(),
        element_sources=sources,
    )
    assert "if(lt(((t-0)/1),0.5)" in spec.filter_complex
    result = subprocess.run(spec.args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-800:]
    assert _red_center(_export_frame(out, 0.5)) == pytest.approx(100, abs=8)
    assert _white_center(_export_frame(out, 0.25)) == pytest.approx(90, abs=9)
    assert _white_center(_export_frame(out, 0.5)) == pytest.approx(160, abs=9)


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
    path = tmp_path_factory.mktemp("easing") / "v.mp4"
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
        state.insp_easing.set("linear")
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


def _choose_easing(pilot, index: int) -> None:
    _click_inspector(pilot, "insp-easing")
    pilot.click(f"insp-easing-opt-{index}")
    _settle(pilot)


def test_ui_el_selector_decide_el_easing_de_las_nuevas_claves(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.add_text()
    _settle(pilot)
    overlay = state.editor_project().texts[-1]
    editor.select_text(overlay.id)
    _settle(pilot)
    editor.seek(overlay.start)
    _settle(pilot)

    # El desplegable es el de la sección Animación: opción 1 = ease_in.
    _choose_easing(pilot, 1)
    assert state.insp_easing() == "ease_in"
    _click_inspector(pilot, "insp-text-key-x")
    curve = next(
        a for a in state.editor_project().text(overlay.id).animations if a.channel == "x"
    )
    assert len(curve.points) == 1 and curve.points[0].easing == "ease_in"

    # Volver a grabar el mismo instante actualiza el easing, no duplica la clave.
    _choose_easing(pilot, 2)  # ease_out
    _click_inspector(pilot, "insp-text-key-x")
    curve = next(
        a for a in state.editor_project().text(overlay.id).animations if a.channel == "x"
    )
    assert len(curve.points) == 1 and curve.points[0].easing == "ease_out"

    editor.seek(overlay.start + 1.0)
    _settle(pilot)
    _choose_easing(pilot, 3)  # ease_in_out
    state.insp_text_x.set(0.8)
    _settle(pilot)
    _click_inspector(pilot, "insp-text-key-x")
    curve = next(
        a for a in state.editor_project().text(overlay.id).animations if a.channel == "x"
    )
    assert [point.easing for point in curve.points] == ["ease_out", "ease_in_out"]
    assert [point.value for point in curve.points] == pytest.approx([0.5, 0.8])
    assert state.undo_label() == "añadir clave X"
