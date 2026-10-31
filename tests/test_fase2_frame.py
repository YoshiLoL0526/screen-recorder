"""Fase 2 (docs/18/docs/20): modelo v4, lienzo, transformación, preview y export real."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace

import numpy as np
import pytest

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
    TextOverlay,
    Track,
    Transform,
    Transition,
    from_dict,
    to_dict,
)
from recstudio.platform import ffmpeg as F
from recstudio.services import export as export_mod
from recstudio.services import media, playback


def asset(aid: str = "a", width: int = 1920, height: int = 1080, duration: float = 30.0,
          rotation: int = 0) -> MediaAsset:
    return MediaAsset(id=aid, path=f"C:/v/{aid}.mp4", duration=duration, width=width,
                      height=height, fps=30.0, has_audio=False, rotation=rotation)


def base_project(*, fit: str = "fit", background: str = "#000000") -> Project:
    return Project(
        assets=(asset(),),
        video=Track("video", (Clip("c1", "a", 0, 5),)),
        fps=30, width=1920, height=1080, default_fit=fit, background=background,
    )


# -- modelo v4 ------------------------------------------------------------------------


def test_modelo_v4_defaults_y_validacion() -> None:
    assert SCHEMA_VERSION == 4
    project = Project()
    assert (project.background, project.default_fit) == ("#000000", "fit")
    transform = Transform()
    assert (transform.fit, transform.scale, transform.x, transform.y) == ("inherit", 1.0, 0.5, 0.5)
    assert (transform.rotation, transform.flip_h, transform.flip_v) == (0, False, False)
    assert transform.crop == (0.0, 0.0, 1.0, 1.0)
    base_project().validate()

    for kwargs in (
        {"fit": "nope"},
        {"scale": 0.1},
        {"scale": 4.5},
        {"x": -0.1},
        {"y": 1.1},
        {"rotation": 45},
        {"crop": (0.5, 0.0, 0.5, 1.0)},
        {"crop": (0.0, 0.0, 1.1, 1.0)},
        {"crop": (0.0, 0.0, 1.0)},
    ):
        with pytest.raises(ProjectError):
            Transform(**kwargs)

    with pytest.raises(ProjectError, match="fondo"):
        replace(project, background="negro").validate()
    with pytest.raises(ProjectError, match="encaje"):
        replace(project, default_fit="cover").validate()
    base_project().validate()


def test_roundtrip_v4_y_migracion_v3() -> None:
    project = Project(
        assets=(asset(),),
        video=Track("video", (
            Clip(
                "c1", "a", 0, 5,
                transform=Transform(fit="fill", scale=1.5, x=0.25, y=0.75, rotation=270,
                                    flip_h=True, flip_v=True, crop=(0.1, 0.2, 0.9, 0.8)),
            ),
        ), ()),
        fps=30, width=1080, height=1920, background="#204060", default_fit="fill",
    )
    project.validate()
    restored = from_dict(json.loads(json.dumps(to_dict(project))))
    assert restored == project
    assert restored.video.clips[0].transform == project.video.clips[0].transform

    # Un JSON v3 real (sin background/default_fit/transform) migra a v4 sin cambiar lo visible.
    legacy = to_dict(base_project())
    legacy["schema_version"] = 3
    legacy.pop("background")
    legacy.pop("default_fit")
    for track in (legacy["video"], *legacy["audio"]):
        for clip in track["clips"]:
            clip.pop("transform")
    migrated, read_only = store_mod.migrate(legacy)
    assert not read_only and migrated["schema_version"] == 4
    assert migrated["background"] == "#000000" and migrated["default_fit"] == "fit"
    assert migrated["video"]["clips"][0]["transform"] is None
    old = from_dict(migrated)
    assert old.video.clips[0].transform is None and old.default_fit == "fit"
    old.validate()


def test_operaciones_de_lienzo_no_mueven_clips_ni_transiciones() -> None:
    project = pipeline_project()
    before = project
    resized = tl.set_project_size(project, 1080, 1920)
    assert (resized.width, resized.height) == (1080, 1920)
    assert resized.video.clips == before.video.clips
    assert resized.video.transitions == before.video.transitions
    assert resized.audio == before.audio
    assert tl.set_project_size(project, 1081, 1921).width == 1080  # par
    assert tl.set_project_size(project, 1920, 1080) is project

    for size in ((0, 1080), (8, 1080), (8000, 1080), (1920.5, 1080)):
        with pytest.raises(ProjectError):
            tl.set_project_size(project, *size)

    fitted = tl.set_project_fit(project, "fill")
    assert fitted.default_fit == "fill"
    assert tl.set_project_fit(fitted, "fill") is fitted
    with pytest.raises(ProjectError):
        tl.set_project_fit(project, "cover")

    colored = tl.set_project_background(project, "ff8800")
    assert colored.background == "#FF8800"
    assert tl.set_project_background(colored, "#FF8800") is colored
    with pytest.raises(ProjectError):
        tl.set_project_background(project, "#ZZZZZZ")


def pipeline_project() -> Project:
    track = Track("video", (Clip("c1", "a", 0, 5), Clip("c2", "a", 5, 10)),
                  (Transition("fade", 0.5),))
    audio = Track("audio", (Clip("m1", "a", 0, 3),), (), mode="absolute", name="Música")
    return Project(assets=(asset(),), video=track, audio=(audio,), fps=30)


def test_set_transform_fusiona_reset_y_neutro() -> None:
    project = base_project()
    changed = tl.set_transform(project, "c1", rotation=90, flip_h=True,
                               crop=(0.1, 0.1, 0.9, 0.9))
    transform = changed.video.clips[0].transform
    assert transform is not None
    assert (transform.rotation, transform.flip_h) == (90, True)
    assert transform.crop == pytest.approx((0.1, 0.1, 0.9, 0.9))
    changed.validate()

    scaled = tl.set_transform(changed, "c1", scale=0.5)
    assert scaled.video.clips[0].transform.scale == 0.5
    assert scaled.video.clips[0].transform.rotation == 90
    with pytest.raises(ProjectError):
        tl.set_transform(project, "c1", rotation=45)
    with pytest.raises(ProjectError):
        tl.set_transform(project, "c1", desconocido=1)

    # Volver a todos los valores neutros equivale a no tener transformación.
    neutral = tl.set_transform(changed, "c1", rotation=0, flip_h=False, crop=(0, 0, 1, 1))
    assert neutral.video.clips[0].transform is None
    assert tl.reset_transform(neutral, "c1") is neutral
    assert tl.reset_transform(changed, "c1").video.clips[0].transform is None


# -- matemática de layout ---------------------------------------------------------------


def test_layout_fit_fill_stretch_en_vertical() -> None:
    square = Project(
        assets=(asset("a", 1600, 900),),
        video=Track("video", (Clip("c1", "a", 0, 5),)), fps=30,
        width=1080, height=1920,
    )
    fit = tx.layout(square, square.video.clips[0])
    assert (fit.fit, fit.crop_width, fit.crop_height) == ("fit", 1600, 900)
    assert fit.out_width == 1080 and fit.out_height == 608
    assert (fit.x, fit.y) == (0, 656)

    fill_project = tl.set_project_fit(square, "fill")
    fill = tx.layout(fill_project, fill_project.video.clips[0])
    assert fill.out_width == 3412 and fill.out_height == 1920
    assert (fill.x, fill.y) == (-1166, 0)

    stretch_project = tl.set_project_fit(square, "stretch")
    stretch = tx.layout(stretch_project, stretch_project.video.clips[0])
    assert (stretch.out_width, stretch.out_height, stretch.x, stretch.y) == (1080, 1920, 0, 0)


def test_layout_crop_rotacion_flip_escala_y_posicion() -> None:
    project = Project(
        assets=(asset("a", 1000, 500),),
        video=Track("video", (
            Clip("c1", "a", 0, 5, transform=Transform(
                fit="fit", scale=0.5, x=0.25, y=0.75, rotation=90, flip_h=True,
                crop=(0.1, 0.2, 0.9, 0.8),
            )),
        ), ()),
        fps=30, width=800, height=800,
    )
    lay = tx.layout(project, project.video.clips[0])
    # rotación 90: 1000×500 → 500×1000; el crop se aplica después.
    assert (lay.source_width, lay.source_height) == (500, 1000)
    assert (lay.crop_x, lay.crop_y, lay.crop_width, lay.crop_height) == (50, 200, 400, 600)
    assert lay.rotation == 90 and lay.flip_h
    # fit 400×600 en 800×800 ⇒ 533×800; scale 0.5 ⇒ 266×400.
    assert (lay.out_width, lay.out_height) in ((266, 400), (266, 400))
    assert lay.x == round(0.25 * 800 - lay.out_width / 2)
    assert lay.y == round(0.75 * 800 - lay.out_height / 2)


def test_needs_custom_render_solo_con_lo_nuevo() -> None:
    project = base_project()
    assert not tx.needs_custom_render(project, project.video.clips[0])
    assert tx.needs_custom_render(
        tl.set_project_background(project, "#FFFFFF"), project.video.clips[0])
    assert tx.needs_custom_render(
        tl.set_project_fit(project, "stretch"), project.video.clips[0])
    transformed = tl.set_transform(project, "c1", scale=0.5)
    assert tx.needs_custom_render(transformed, transformed.video.clips[0])
    rotated = Project(
        assets=(replace(asset(), rotation=90, width=1080, height=1920),),
        video=Track("video", (Clip("c1", "a", 0, 5),)), fps=30, width=1080, height=1920,
    )
    assert tx.needs_custom_render(rotated, rotated.video.clips[0])


# -- graph -------------------------------------------------------------------------------


def test_graph_fill_vertical_rellena_y_recorta() -> None:
    project = Project(
        assets=(asset("a", 1920, 1080),),
        video=Track("video", (Clip("c1", "a", 0, 5),)), fps=30,
        width=1080, height=1920, default_fit="fill", background="#204060",
    )
    spec = G.build(project, G.preset_by_id("calidad"), "o.mp4")
    assert (spec.width, spec.height) == (1080, 1920)
    assert "force_original_aspect_ratio" not in spec.filter_complex
    assert "scale=3412:1920" in spec.filter_complex
    assert "color=0x204060" in spec.filter_complex
    assert "crop=1080:1920:1166:0" in spec.filter_complex


def test_graph_stretch_crop_rotacion_flip_y_fondo() -> None:
    project = Project(
        assets=(asset("a", 1920, 1080),),
        video=Track("video", (
            Clip("c1", "a", 0, 5, transform=Transform(
                fit="stretch", scale=0.5, x=0.25, y=0.75, rotation=180, flip_h=True,
                crop=(0.0, 0.0, 0.5, 1.0),
            )),
        ), ()),
        fps=30, width=1080, height=1920, background="#112233",
    )
    fc = G.build(project, G.preset_by_id("calidad"), "o.mp4").filter_complex
    # rotación 180 = hflip+vflip; después el volteo horizontal del clip.
    assert fc.index("hflip") < fc.index("vflip") < fc.index("hflip", fc.index("vflip"))
    assert "crop=w=960:h=1080:x=0:y=0" in fc
    assert "scale=540:960" in fc
    assert "pad=1080:1920:0:960:color=0x112233" in fc
    assert "eq=" not in fc


def test_graph_auto_rotacion_deja_que_ffmpeg_la_aplique() -> None:
    rotated = MediaAsset("a", "rot.mp4", 5.0, 240, 320, 30.0, False, rotation=90)
    project = Project(assets=(rotated,), video=Track("video", (Clip("c1", "a", 0, 5),)),
                      fps=30, width=240, height=320)
    spec = G.build(project, G.preset_by_id("calidad"), "o.mp4")
    assert "-noautorotate" not in spec.args
    assert "transpose" not in spec.filter_complex
    assert "scale=240:320" in spec.filter_complex


def test_graph_textos_escalan_al_alto_de_salida() -> None:
    project = replace(base_project(), height=1920, width=1080,
                      texts=(TextOverlay("t1", "Hola", 0, 1, size=42),))
    filters = G.text_filters(project, 720)
    assert "fontsize=16" in filters[0]  # 42 × 720 / 1920 = 15.75 → 16


def test_output_size_respeta_el_aspecto_vertical_en_720p() -> None:
    vertical = Project(width=1080, height=1920)
    assert G.output_size(vertical, G.preset_by_id("ligero")) == (404, 720)
    assert G.output_size(Project(width=1080, height=1920), G.preset_by_id("calidad")) == (1080, 1920)


# -- preview con frame sintético ---------------------------------------------------------


def _quad_png(path) -> None:  # noqa: ANN001
    image = np.zeros((100, 200, 3), np.uint8)
    image[:50, :100] = (255, 0, 0)  # arriba izquierda
    image[:50, 100:] = (0, 255, 0)  # arriba derecha
    image[50:, :100] = (0, 0, 255)  # abajo izquierda
    image[50:, 100:] = (255, 255, 255)  # abajo derecha
    from PIL import Image

    Image.fromarray(image).save(path)


@pytest.fixture(scope="module")
def quad_media(tmp_path_factory: pytest.TempPathFactory):  # noqa: ANN201
    folder = tmp_path_factory.mktemp("fase2")
    _quad_png(folder / "quad.png")
    try:
        subprocess.run(
            [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
             "-loop", "1", "-framerate", "30", "-i", str(folder / "quad.png"),
             "-t", "1", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             str(folder / "quad.mp4")],
            check=True, capture_output=True,
        )
        subprocess.run(
            [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
             "-display_rotation", "90", "-i", str(folder / "quad.mp4"), "-c", "copy",
             str(folder / "quad90.mp4")],
            check=True, capture_output=True,
        )
        return media.probe(folder / "quad.mp4"), media.probe(folder / "quad90.mp4"), folder
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")


def _sample(rgb: np.ndarray, x: float, y: float) -> tuple[int, int, int]:
    height, width = rgb.shape[:2]
    return tuple(int(v) for v in rgb[min(height - 1, int(y * height)), min(width - 1, int(x * width))])


def _near(color, expected, tolerance: int = 40) -> bool:  # noqa: ANN001
    return all(abs(int(a) - int(b)) <= tolerance for a, b in zip(color, expected))


def _render(project: Project, t: float = 0.5, max_height: int = 200) -> np.ndarray:
    renderer = playback.FrameRenderer(max_height=max_height)
    try:
        frame = renderer.render(project, t)
        assert frame is not None
        return frame
    finally:
        renderer.close()


def test_preview_fit_fill_stretch_y_fondo(quad_media) -> None:
    plain, _rotated, _folder = quad_media

    fit = Project(assets=(plain,), video=Track("video", (Clip("c1", plain.id, 0, 1),)),
                  fps=30, width=200, height=200, background="#204060", default_fit="fit")
    frame = _render(fit)
    assert frame.shape == (200, 200, 3)
    assert _near(_sample(frame, 0.1, 0.05), (0x20, 0x40, 0x60))
    assert _near(_sample(frame, 0.25, 0.4), (255, 0, 0))
    assert _near(_sample(frame, 0.75, 0.6), (255, 255, 255))

    fill = replace(fit, width=100, height=100, default_fit="fill")
    frame = _render(fill)
    assert frame.shape == (100, 100, 3)
    assert _near(_sample(frame, 0.25, 0.25), (255, 0, 0))
    assert _near(_sample(frame, 0.75, 0.25), (0, 255, 0))
    assert _near(_sample(frame, 0.25, 0.75), (0, 0, 255))
    assert _near(_sample(frame, 0.75, 0.75), (255, 255, 255))

    stretch = replace(fit, width=100, height=200, default_fit="stretch")
    frame = _render(stretch)
    assert frame.shape == (200, 100, 3)
    assert _near(_sample(frame, 0.25, 0.25), (255, 0, 0))
    assert _near(_sample(frame, 0.75, 0.75), (255, 255, 255))


def test_preview_crop_rotacion_flip_y_posicion(quad_media) -> None:
    plain, _rotated, _folder = quad_media
    cropped = Project(
        assets=(plain,),
        video=Track("video", (Clip("c1", plain.id, 0, 1, transform=Transform(
            crop=(0.0, 0.0, 0.5, 1.0))),)), fps=30, width=200, height=100,
        background="#204060",
    )
    frame = _render(cropped)
    assert _near(_sample(frame, 0.25, 0.25), (255, 0, 0))
    assert _near(_sample(frame, 0.25, 0.75), (0, 0, 255))
    assert _near(_sample(frame, 0.75, 0.5), (0x20, 0x40, 0x60))

    flipped = replace(cropped, video=Track("video", (Clip(
        "c1", plain.id, 0, 1, transform=Transform(flip_h=True)),), ()))
    frame = _render(flipped)
    assert _near(_sample(frame, 0.25, 0.25), (0, 255, 0))
    assert _near(_sample(frame, 0.75, 0.25), (255, 0, 0))
    assert _near(_sample(frame, 0.25, 0.75), (255, 255, 255))
    assert _near(_sample(frame, 0.75, 0.75), (0, 0, 255))

    rotated = Project(
        assets=(plain,),
        video=Track("video", (Clip("c1", plain.id, 0, 1, transform=Transform(rotation=90)),)),
        fps=30, width=100, height=200, background="#000000",
    )
    frame = _render(rotated)
    assert frame.shape == (200, 100, 3)
    # 90° horario: la esquina superior izquierda pasa a la superior derecha.
    assert _near(_sample(frame, 0.25, 0.25), (0, 0, 255))
    assert _near(_sample(frame, 0.75, 0.25), (255, 0, 0))
    assert _near(_sample(frame, 0.25, 0.75), (255, 255, 255))
    assert _near(_sample(frame, 0.75, 0.75), (0, 255, 0))

    placed = replace(cropped, video=Track("video", (Clip(
        "c1", plain.id, 0, 1, transform=Transform(scale=0.5, x=0.25, y=0.5)),), ()))
    frame = _render(placed)
    assert _near(_sample(frame, 0.1, 0.4), (255, 0, 0))
    assert _near(_sample(frame, 0.9, 0.5), (0x20, 0x40, 0x60))


def test_preview_auto_rotacion_coincide_con_el_export(quad_media, tmp_path) -> None:
    _plain, rotated, _folder = quad_media
    assert (rotated.width, rotated.height, rotated.rotation) == (100, 200, 90)
    project = Project(assets=(rotated,), video=Track("video", (Clip("c1", rotated.id, 0, 1),)),
                      fps=30, width=100, height=200)
    preview = _render(project, max_height=200)
    assert preview.shape == (200, 100, 3)

    result = _export(project, "calidad", tmp_path / "rot.mp4")
    assert result.state == "done", result.message
    import av

    with av.open(str(tmp_path / "rot.mp4")) as container:
        stream = container.streams.video[0]
        assert (stream.codec_context.width, stream.codec_context.height) == (100, 200)
        assert (next(container.decode(stream)).rotation or 0) == 0  # sin display matrix duplicada
    first = _first_frame(tmp_path / "rot.mp4")
    assert first.shape == preview.shape
    for x, y in ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)):
        assert _near(_sample(first, x, y), _sample(preview, x, y), tolerance=8)


# -- export real -------------------------------------------------------------------------


def _make_video(path, *, seconds: float = 4.0) -> None:  # noqa: ANN001
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )


def _export(project: Project, preset: str, out) -> export_mod.ExportProgress:  # noqa: ANN001
    exporter = export_mod.Exporter()
    exporter.start(project, preset, out)
    return exporter.wait(120)


def _first_frame(path) -> np.ndarray:  # noqa: ANN001
    import av

    with av.open(str(path)) as container:
        return next(container.decode(container.streams.video[0])).to_ndarray(format="rgb24")


def test_export_vertical_fill_dimensiones_duracion_y_audio(tmp_path) -> None:
    source = tmp_path / "v.mp4"
    _make_video(source)
    asset = media.probe(source)
    project = Project(assets=(asset,), video=Track("video", (Clip("c1", asset.id, 0, 4),)),
                      fps=30, width=320, height=240)
    project = tl.set_project_size(project, 240, 320)
    project = tl.set_project_fit(project, "fill")
    project = tl.set_project_background(project, "#204060")
    result = _export(project, "calidad", tmp_path / "vertical.mp4")
    assert result.state == "done", result.message

    import av

    with av.open(str(tmp_path / "vertical.mp4")) as container:
        video = container.streams.video[0]
        size = (video.codec_context.width, video.codec_context.height)
        frames = sum(1 for _ in container.decode(video))
    with av.open(str(tmp_path / "vertical.mp4")) as container:
        audio = container.streams.audio[0]
        seconds = sum(f.samples for f in container.decode(audio)) / audio.rate
    assert size == (240, 320)
    assert frames == G.timeline_frames(project)[2]
    assert seconds == pytest.approx(frames / 30, abs=0.03)


def test_export_con_transicion_y_transformacion_conserva_frames_y_xfade(tmp_path) -> None:
    source = tmp_path / "v.mp4"
    _make_video(source)
    asset = media.probe(source)
    project = Project(
        assets=(asset,),
        video=Track("video", (
            Clip("c1", asset.id, 0.0, 2.0),
            Clip("c2", asset.id, 2.0, 4.0, transform=Transform(fit="fill", rotation=90,
                                                               scale=0.75, x=0.5, y=0.5)),
        ), (Transition("fade", 0.5),)),
        fps=30, width=320, height=240, background="#204060", default_fit="fill",
    )
    project = tl.set_project_size(project, 240, 320)
    result = _export(project, "calidad", tmp_path / "mix.mp4")
    assert result.state == "done", result.message
    assert G.timeline_frames(project)[2] == round((2 + 2 - 0.5) * 30)

    import av

    with av.open(str(tmp_path / "mix.mp4")) as container:
        video = container.streams.video[0]
        assert (video.codec_context.width, video.codec_context.height) == (240, 320)
        assert sum(1 for _ in container.decode(video)) == G.timeline_frames(project)[2]


# -- corte rápido ------------------------------------------------------------------------


def test_corte_rapido_se_bloquea_con_lienzo_fondo_transformacion_y_rotacion() -> None:
    plain = base_project()
    assert K.blockers(plain) == []

    resized = tl.set_project_size(plain, 1080, 1920)
    assert "tamaño del lienzo" in "; ".join(K.blockers(resized))

    colored = tl.set_project_background(plain, "#FFFFFF")
    assert "fondo o el encaje" in "; ".join(K.blockers(colored))
    fitted = tl.set_project_fit(plain, "fill")
    assert "fondo o el encaje" in "; ".join(K.blockers(fitted))

    transformed = tl.set_transform(plain, "c1", scale=0.5)
    assert "transformaciones" in "; ".join(K.blockers(transformed))

    rotated = Project(
        assets=(replace(asset(), rotation=90, width=1080, height=1920),),
        video=Track("video", (Clip("c1", "a", 0, 5),)), fps=30, width=1080, height=1920,
    )
    assert "rotación automática" in "; ".join(K.blockers(rotated))
