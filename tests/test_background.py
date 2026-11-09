"""Fondo del lienzo (v16, docs/47): modelo, preview, export real y UI.

Cubre el color histórico, el blur del propio V1 y la imagen del proyecto; el fondo va detrás de
V1 y el resto de la composición (V2/V3, efectos, elementos y textos) no cambia de orden.
"""

from __future__ import annotations

import io
import json
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from recstudio import state
from recstudio.core import background as bg
from recstudio.core import graph as G
from recstudio.core import keyframes as K
from recstudio.core import store as store_mod
from recstudio.core import timeline as tl
from recstudio.core import transform as tx
from recstudio.core.errors import ProjectError
from recstudio.core.project import (
    SCHEMA_VERSION,
    Clip,
    MediaAsset,
    Project,
    Track,
    Transform,
    Transition,
    from_dict,
    to_dict,
)
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import export as export_mod
from recstudio.services import media, playback

GREEN = (20, 180, 60)
BLUE = (0x20, 0x40, 0x60)


def _asset(
    aid: str = "a", width: int = 320, height: int = 180, *, kind: str = "video",
    path: str | None = None,
) -> MediaAsset:
    return MediaAsset(
        id=aid, path=path or f"C:/v/{aid}.mp4", duration=4.0 if kind == "video" else 0.0,
        width=width, height=height, fps=30.0 if kind == "video" else 0.0,
        has_audio=False, kind=kind,  # type: ignore[arg-type]
    )


def _base(*clips: Clip, width: int = 180, height: int = 320, assets=None) -> Project:
    clips = clips or (Clip("c1", "a", 0.0, 2.0),)
    return Project(
        assets=assets or (_asset(),),
        video=Track("video", clips, tuple(None for _ in range(len(clips) - 1))),
        fps=30, width=width, height=height,
    )


# -- modelo v16 -------------------------------------------------------------------------


def test_modelo_v16_defaults_validacion_y_round_trip() -> None:
    assert SCHEMA_VERSION == 16
    project = Project()
    assert (project.background_mode, project.background_blur) == ("color", 24)
    assert project.background_asset is None

    with pytest.raises(ProjectError, match="modo de fondo"):
        replace(project, background_mode="cover").validate()
    with pytest.raises(ProjectError, match="intensidad"):
        replace(project, background_blur=-1).validate()
    with pytest.raises(ProjectError, match="intensidad"):
        replace(project, background_blur=101).validate()
    with pytest.raises(ProjectError, match="intensidad"):
        replace(project, background_blur=1.5).validate()  # type: ignore[arg-type]
    with pytest.raises(ProjectError, match="asset de fondo"):
        replace(project, background_asset=12).validate()  # type: ignore[arg-type]

    image = _asset("img", 400, 300, kind="image")
    full = replace(
        _base(assets=(_asset(), image)), background_mode="image",
        background_asset="img", background_blur=60,
    ).validate()
    restored = from_dict(json.loads(json.dumps(to_dict(full))))
    assert restored == full
    assert (restored.background_mode, restored.background_blur, restored.background_asset) == (
        "image", 60, "img",
    )


def test_operaciones_de_fondo_y_asset_en_uso() -> None:
    project = _base()
    blurred = tl.set_project_background_mode(project, "blur")
    assert blurred.background_mode == "blur"
    assert tl.set_project_background_mode(blurred, "blur") is blurred
    with pytest.raises(ProjectError):
        tl.set_project_background_mode(project, "nope")

    louder = tl.set_project_background_blur(blurred, 80)
    assert louder.background_blur == 80
    assert tl.set_project_background_blur(louder, 80) is louder
    for value in (-1, 101, "x"):
        with pytest.raises(ProjectError):
            tl.set_project_background_blur(project, value)  # type: ignore[arg-type]

    image = _asset("img", 400, 300, kind="image")
    with_image = replace(project, assets=(*project.assets, image))
    picked = tl.set_project_background_image(with_image, "img")
    assert picked.background_asset == "img"
    assert tl.set_project_background_image(picked, "img") is picked
    with pytest.raises(ProjectError):
        tl.set_project_background_image(project, "a")  # un video no vale como fondo

    cleared = tl.clear_project_background_image(
        replace(picked, background_mode="image")
    )
    assert (cleared.background_mode, cleared.background_asset) == ("color", None)
    assert tl.clear_project_background_image(cleared) is cleared

    with pytest.raises(ProjectError, match="todavía se usa"):
        tl.remove_asset(picked, "img")


def test_migracion_v15_v16_y_fallback_si_falta_la_imagen() -> None:
    data = to_dict(_base())
    data["schema_version"] = 15
    for key in ("background_mode", "background_blur", "background_asset"):
        data.pop(key)
    migrated, read_only = store_mod.migrate(data)
    assert not read_only and migrated["schema_version"] == SCHEMA_VERSION
    old = from_dict(migrated)
    assert (old.background_mode, old.background_blur, old.background_asset) == ("color", 24, None)
    old.validate()

    missing = replace(_base(), background_mode="image", background_asset="nope")
    missing.validate()  # no rompe proyectos con la imagen perdida
    assert bg.effective_mode(missing) == "color"
    assert not tx.needs_custom_render(missing, missing.video.clips[0])

    by_video = replace(_base(), background_mode="image", background_asset="a")
    assert bg.effective_mode(by_video) == "color"

    image = _asset("img", 400, 300, kind="image")
    by_image = replace(
        _base(assets=(_asset(), image)), background_mode="image", background_asset="img",
    )
    assert bg.effective_mode(by_image) == "image"
    assert bg.image_asset(by_image) == image
    assert tx.needs_custom_render(by_image, by_image.video.clips[0])


def test_geometria_cover_y_radio_escalado() -> None:
    assert bg.cover_crop(200, 100, 200, 200) == (100, 0, 400, 200)
    assert bg.cover_crop(100, 200, 200, 200) == (0, 100, 200, 400)
    assert bg.cover_crop(200, 200, 100, 100) == (0, 0, 100, 100)
    project = _base(height=1080)
    assert bg.blur_radius(project, 540) == pytest.approx(12.0)
    assert bg.blur_radius(replace(project, background_blur=0), 540) == 0.0


# -- graph ------------------------------------------------------------------------------


def test_graph_por_defecto_no_cambia_y_blur_o_imagen_anaden_cadenas() -> None:
    project = _base(Clip("c1", "a", 0, 2), Clip("c2", "a", 2, 4))
    spec = G.build(project, G.preset_by_id("calidad"), "o.mp4")
    assert spec.filter_complex == (
        "[0:v]setpts=PTS-STARTPTS,scale=180:320:force_original_aspect_ratio=decrease,"
        "pad=180:320:-1:-1:color=black,fps=30,tpad=stop_mode=clone:stop=30,"
        "trim=end_frame=60,format=yuv420p,setsar=1[v0];"
        "[1:v]setpts=PTS-STARTPTS,scale=180:320:force_original_aspect_ratio=decrease,"
        "pad=180:320:-1:-1:color=black,fps=30,tpad=stop_mode=clone:stop=30,"
        "trim=end_frame=60,format=yuv420p,setsar=1[v1];"
        "[v0][v1]concat=n=2:v=1:a=0[c1]"
    )
    assert "split" not in spec.filter_complex and "overlay" not in spec.filter_complex

    blurred = replace(project, background_mode="blur", background_blur=24)
    fc = G.build(blurred, G.preset_by_id("calidad"), "o.mp4").filter_complex
    assert "split=2" in fc and "boxblur=luma_radius=24:luma_power=2" in fc
    assert "overlay=x=0:y=110:format=auto:shortest=1" in fc
    assert "color=black" not in fc

    # El fondo se escala al tamaño de salida y el radio se reescala con el alto (720p).
    low = G.build(
        blurred, G.custom_preset("404", "720", "30", "6M", "192k"), "o.mp4",
    ).filter_complex
    assert "scale=404:720:force_original_aspect_ratio=increase" in low
    assert "boxblur=luma_radius=54:luma_power=2" in low

    image = _asset("img", 400, 300, kind="image")
    with_image = replace(
        project, assets=(*project.assets, image), background_mode="image",
        background_asset="img",
    )
    spec = G.build(with_image, G.preset_by_id("calidad"), "o.mp4")
    assert "-loop" in spec.args
    assert "C:/v/img.mp4" in spec.args
    assert "[2:v]scale=180:320:force_original_aspect_ratio=increase,crop=180:320" in spec.filter_complex
    assert "split=2[bg0][bg1]" in spec.filter_complex


def test_graph_con_transicion_y_fits_distintos_mantiene_el_orden() -> None:
    project = Project(
        assets=(_asset(),),
        video=Track(
            "video",
            (
                Clip("c1", "a", 0, 2),
                Clip("c2", "a", 2, 4, transform=Transform(fit="fill")),
            ),
            (Transition("fade", 0.5),),
        ),
        fps=30, width=180, height=320, background_mode="blur", background_blur=10,
    )
    fc = G.build(project, G.preset_by_id("calidad"), "o.mp4").filter_complex
    assert fc.count("boxblur") == 2
    assert "overlay=x=0:y=110" in fc  # fit: barras arriba/abajo
    assert "overlay=x=-194:y=0" in fc  # fill: el clip se sale por los lados
    assert "xfade=transition=fade" in fc


def test_corte_rapido_se_bloquea_con_fondo_especial() -> None:
    project = _base(width=320, height=180)
    assert K.blockers(project) == []
    image = _asset("img", 400, 300, kind="image")
    blurred = replace(project, background_mode="blur")
    assert "fondo desenfocado" in "; ".join(K.blockers(blurred))
    with_image = replace(
        project, assets=(*project.assets, image), background_mode="image",
        background_asset="img",
    )
    assert "fondo desenfocado" in "; ".join(K.blockers(with_image))
    # Una imagen perdida cae a color y no bloquea por sí sola (el resto sigue igual).
    missing = replace(project, background_mode="image", background_asset="nope")
    assert K.blockers(missing) == []


def test_offline_assets_incluye_la_imagen_de_fondo(tmp_path: Path) -> None:
    image = _asset("img", 400, 300, kind="image", path=str(tmp_path / "no-existe.png"))
    project = replace(
        _base(assets=(*_base().assets, image)),
        background_mode="image", background_asset="img",
    )
    assert "no-existe" in export_mod.offline_assets(project)


# -- medios sintéticos ------------------------------------------------------------------


def _quad_rgb(width: int = 320, height: int = 180) -> np.ndarray:
    image = np.zeros((height, width, 3), np.uint8)
    image[: height // 2, : width // 2] = (255, 0, 0)
    image[: height // 2, width // 2 :] = (0, 255, 0)
    image[height // 2 :, : width // 2] = (0, 0, 255)
    image[height // 2 :, width // 2 :] = (255, 255, 255)
    return image


@pytest.fixture(scope="module")
def media_files(tmp_path_factory: pytest.TempPathFactory):  # noqa: ANN201
    from PIL import Image

    folder = tmp_path_factory.mktemp("fondo")
    png = folder / "quad.png"
    Image.fromarray(_quad_rgb()).save(png)
    video = folder / "quad.mp4"
    try:
        subprocess.run(
            [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
             "-loop", "1", "-framerate", "30", "-i", str(png), "-t", "4",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video)],
            check=True, capture_output=True,
        )
        green = folder / "green.png"
        Image.fromarray(np.full((300, 500, 3), GREEN, np.uint8)).save(green)
        return media.probe(video), media.probe_image(green), folder
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")


def _sample(rgb: np.ndarray, x: float, y: float) -> tuple[int, int, int]:
    height, width = rgb.shape[:2]
    return tuple(int(v) for v in rgb[min(height - 1, int(y * height)), min(width - 1, int(x * width))])


def _near(color, expected, tolerance: int = 40) -> bool:  # noqa: ANN001
    return all(abs(int(a) - int(b)) <= tolerance for a, b in zip(color, expected))


def _distance(a, b) -> float:  # noqa: ANN001
    return float(np.linalg.norm(np.asarray(a, float) - np.asarray(b, float)))


def _projects(asset, image, *, minutes: bool = False) -> dict[str, Project]:  # noqa: ANN001
    clips = (Clip("c1", asset.id, 0.0, 2.0),)
    if minutes:
        clips = (Clip("c1", asset.id, 0.0, 1.0), Clip("c2", asset.id, 1.0, 2.0))
    transitions = tuple(None for _ in range(len(clips) - 1))
    if minutes:
        transitions = (Transition("fade", 0.3),)
    base = Project(
        assets=(asset, image),
        video=Track("video", clips, transitions),
        fps=30, width=180, height=320,
    )
    return {
        "color": tl.set_project_background(base, "#204060"),
        "blur": replace(base, background_mode="blur", background_blur=20),
        "image": replace(base, background_mode="image", background_asset=image.id),
    }


# -- preview ----------------------------------------------------------------------------


def _render(project: Project, t: float = 0.5, max_height: int = 320) -> np.ndarray:
    renderer = playback.FrameRenderer(max_height=max_height)
    try:
        frame = renderer.render(project, t)
        assert frame is not None
        return frame
    finally:
        renderer.close()


def test_preview_blur_e_imagen_rellenan_las_barras(media_files) -> None:
    asset, image, _folder = media_files
    projects = _projects(asset, image)
    assert playback.preview_size(projects["color"]) == (180, 320)

    color = _render(projects["color"])
    assert _near(_sample(color, 0.05, 0.02), BLUE, tolerance=8)
    assert _near(_sample(color, 0.25, 0.4), (255, 0, 0))  # el clip sigue encima
    assert _near(_sample(color, 0.75, 0.6), (255, 255, 255))

    blurred = _render(projects["blur"])
    corner = _sample(blurred, 0.05, 0.02)
    assert _distance(corner, BLUE) > 60  # ya no es el color del lienzo
    assert _distance(corner, (0, 0, 0)) > 40  # tampoco un frame negro
    assert _near(_sample(blurred, 0.25, 0.4), (255, 0, 0))
    assert _near(_sample(blurred, 0.75, 0.6), (255, 255, 255))

    with_image = _render(projects["image"])
    assert _near(_sample(with_image, 0.05, 0.02), GREEN, tolerance=8)
    assert _near(_sample(with_image, 0.95, 0.98), GREEN, tolerance=8)
    assert _near(_sample(with_image, 0.25, 0.4), (255, 0, 0))


def test_preview_imagen_ausente_cae_al_color(media_files) -> None:
    asset, image, _folder = media_files
    missing = replace(
        _base(Clip("c1", asset.id, 0.0, 1.0), assets=(asset, image)),
        background_mode="image", background_asset="no-existe", background="#204060",
    )
    frame = _render(missing)
    assert _near(_sample(frame, 0.05, 0.02), BLUE, tolerance=8)


# -- export real ------------------------------------------------------------------------


def _export(project: Project, out: Path) -> export_mod.ExportProgress:
    exporter = export_mod.Exporter()
    exporter.start(project, "calidad", out)
    return exporter.wait(180)


def _first_frame(path: Path) -> np.ndarray:
    import av

    with av.open(str(path)) as container:
        return next(container.decode(container.streams.video[0])).to_ndarray(format="rgb24")


def test_export_real_color_blur_e_imagen(media_files, tmp_path: Path) -> None:
    asset, image, _folder = media_files
    projects = _projects(asset, image)
    frames = {}
    for name, project in projects.items():
        out = tmp_path / f"{name}.mp4"
        result = _export(project, out)
        assert result.state == "done", result.message
        frames[name] = _first_frame(out)

    assert _near(_sample(frames["color"], 0.05, 0.02), BLUE, tolerance=12)
    assert _near(_sample(frames["blur"], 0.25, 0.4), (255, 0, 0), tolerance=12)
    assert _distance(_sample(frames["blur"], 0.05, 0.02), BLUE) > 60
    assert _distance(_sample(frames["blur"], 0.05, 0.02), (0, 0, 0)) > 40
    assert _near(_sample(frames["image"], 0.05, 0.02), GREEN, tolerance=12)
    assert _near(_sample(frames["image"], 0.95, 0.98), GREEN, tolerance=12)
    # El clip normal sigue encima del fondo en los tres modos.
    for name in frames:
        assert _near(_sample(frames[name], 0.25, 0.4), (255, 0, 0), tolerance=20)


def test_export_con_transicion_y_blur_no_introduce_negros(media_files, tmp_path: Path) -> None:
    asset, image, _folder = media_files
    project = _projects(asset, image, minutes=True)["blur"]
    out = tmp_path / "trans.mp4"
    result = _export(project, out)
    assert result.state == "done", result.message

    import av

    with av.open(str(out)) as container:
        stream = container.streams.video[0]
        assert (stream.codec_context.width, stream.codec_context.height) == (180, 320)
        decoded = [frame.to_ndarray(format="rgb24") for frame in container.decode(stream)]
    assert len(decoded) == G.timeline_frames(project)[2]
    # 0.8 s cae dentro del xfade (empieza en 0.7 s): ni negro ni un frame perdido.
    sample = decoded[int(0.8 * 30)]
    assert float(sample.mean()) > 12
    assert _distance(_sample(sample, 0.05, 0.02), (0, 0, 0)) > 40


# -- UI ---------------------------------------------------------------------------------


def _wait(predicate, timeout: float = 20.0) -> bool:  # noqa: ANN001
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _settle(pilot, frames: int = 3) -> None:  # noqa: ANN001
    for _ in range(frames):
        pilot.frame()
    pilot.settle()


def _ui_video(path: Path) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=640x360:rate=30:duration=3", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
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
        state.screen.set("record")


def _open(controller, pilot, video: Path):  # noqa: ANN001, ANN202
    editor = controller.editor
    editor.import_paths([video])
    assert _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))
    _settle(pilot)
    return editor


def _preview_corner() -> tuple[int, int, int]:
    from PIL import Image

    image = Image.open(io.BytesIO(state.preview_frame())).convert("RGB")
    return tuple(int(v) for v in image.getpixel((2, 2)))


def test_ui_fondo_modos_blur_imagen_y_undo(
    ui, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, controller, pilot = ui
    editor = _open(controller, pilot, _ui_video(tmp_path / "v.mp4"))
    editor.set_project_size(360, 640)
    _settle(pilot)

    assert pilot.rect("proj-bg-mode") is not None
    assert pilot.rect("proj-bg") is not None and pilot.rect("proj-bg-hex") is not None

    state.proj_bg_mode.set("blur")
    _settle(pilot)
    assert state.editor_project().background_mode == "blur"
    assert state.undo_label() == "fondo del lienzo"
    assert pilot.rect("proj-bg-blur") is not None
    state.proj_bg_blur.set(40)
    _settle(pilot)
    assert state.editor_project().background_blur == 40
    assert state.undo_label() == "desenfoque del fondo"

    from recstudio.ui import file_dialogs

    image_path = tmp_path / "green.png"
    from PIL import Image

    Image.fromarray(np.full((300, 500, 3), GREEN, np.uint8)).save(image_path)
    monkeypatch.setattr(file_dialogs, "open_images", lambda *a, **k: [image_path])
    editor.browse_background_image()
    assert _wait(lambda: state.editor_project().background_mode == "image"
                 and state.editor_project().background_asset is not None)
    _settle(pilot)
    project = state.editor_project()
    assert project.background_mode == "image"
    assert bg.image_asset(project) is not None and bg.image_asset(project).kind == "image"
    assert pilot.rect("proj-bg-choose") is not None
    assert pilot.rect("proj-bg-clear") is not None
    chosen = bg.image_asset(project)
    assert chosen is not None
    assert pilot.rect(f"media-remove-{chosen.id}") is None  # el panel lo cuenta como en uso
    assert _wait(lambda: bool(state.preview_frame()))
    assert _wait(lambda: _distance_preview_corner(GREEN) < 50)

    editor.clear_background_image()
    _settle(pilot)
    assert state.editor_project().background_mode == "color"
    assert state.editor_project().background_asset is None

    # Sin imagen elegida el modo imagen no rompe: cae a color.
    state.proj_bg_mode.set("image")
    _settle(pilot)
    assert state.editor_project().background_mode == "image"
    assert bg.effective_mode(state.editor_project()) == "color"
    assert pilot.rect("proj-bg-choose") is not None


def _distance_preview_corner(expected) -> float:  # noqa: ANN001
    if not state.preview_frame():
        return 999.0
    return _distance(_preview_corner(), expected)
