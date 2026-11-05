"""Feature D (docs/22 y docs/26): efectos globales del proyecto.

Cubre los helpers puros, los filtros del graph (golden strings), la paridad del preview con
`vignette`/`gblur` de FFmpeg y un export real de 1 s con blur y viñeta.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace

import numpy as np
import pytest

from recstudio.core import global_effects as ge
from recstudio.core import graph as G
from recstudio.core.errors import ProjectError
from recstudio.core.project import (
    Clip,
    Effect,
    MediaAsset,
    Project,
    TextOverlay,
    Track,
    from_dict,
    to_dict,
)
from recstudio.platform import ffmpeg as F
from recstudio.services import export as export_mod
from recstudio.services import media

MUTE = MediaAsset("a", "a.mp4", 10.0, 640, 360, 30.0, False)
HD = MediaAsset("hd", "hd.mp4", 10.0, 1920, 1080, 30.0, False)


def _project(*, effects: tuple[Effect, ...] = (), height: int = 360, width: int = 640,
             asset: MediaAsset = MUTE) -> Project:
    return Project(
        assets=(asset,), video=Track("video", (Clip("c1", asset.id, 0.0, 2.0),)),
        fps=30, width=width, height=height, effects=effects,
    )


# -- helpers puros y edición ------------------------------------------------------------


def test_sin_efectos_los_helpers_son_neutros_y_el_graph_no_cambia() -> None:
    project = _project()
    assert ge.color_eq(project) is None
    assert ge.blur_radius(project) == 0.0
    assert ge.vignette_strength(project) == 0.0
    assert not ge.grayscale_on(project) and not ge.has_effects(project)
    chains: list[str] = []
    assert ge.apply_graph(project, "v0", chains) == "v0" and chains == []

    # Cadena histórica de un clip (golden de F4): sin efectos no se añade nada.
    historical = (
        "[0:v]setpts=PTS-STARTPTS,scale=640:360:force_original_aspect_ratio=decrease,"
        "pad=640:360:-1:-1:color=black,fps=30,tpad=stop_mode=clone:stop=30,"
        "trim=end_frame=60,format=yuv420p,setsar=1[v0]"
    )
    assert G.build(project, G.preset_by_id("calidad"), "o.mp4").filter_complex == historical

    # Los efectos presentes pero neutros tampoco cambian el graph.
    neutral = replace(project, effects=(
        Effect("eq", (("brightness", 0.0), ("contrast", 1.0), ("saturation", 1.0))),
        Effect("blur", (("radius", 0.0),)),
        Effect("vignette", (("strength", 0.0),)),
    ))
    assert not ge.has_effects(neutral)
    assert G.build(neutral, G.preset_by_id("calidad"), "o.mp4").filter_complex == historical


def test_edicion_fusiona_limpia_y_quita_neutros() -> None:
    project = _project()
    changed = ge.set_effect(project, "eq", brightness=0.1)
    changed = ge.set_effect(changed, "eq", contrast=1.5)
    assert ge.color_eq(changed) == {"brightness": 0.1, "contrast": 1.5, "saturation": 1.0}
    assert len([e for e in changed.effects if e.kind == "eq"]) == 1
    assert from_dict(to_dict(changed)) == changed

    back = ge.set_effect(changed, "eq", brightness=0.0, contrast=1.0)
    assert not any(e.kind == "eq" for e in back.effects)

    assert ge.blur_radius(ge.set_effect(project, "blur", radius=999)) == ge.BLUR_MAX
    assert ge.vignette_strength(ge.set_effect(project, "vignette", strength=2)) == ge.VIGNETTE_MAX
    assert ge.set_effect(project, "blur", radius=0) == project

    gray = ge.set_grayscale(project, True)
    assert ge.grayscale_on(gray) and any(e.kind == "grayscale" for e in gray.effects)
    assert ge.set_grayscale(gray, False) == project
    assert ge.clear_effects(gray) == project
    with pytest.raises(ProjectError):
        ge.set_effect(project, "sharpen", amount=1.0)


# -- graph --------------------------------------------------------------------------------


def test_golden_del_graph_con_todos_los_efectos_y_su_orden() -> None:
    project = _project(effects=(
        Effect("eq", (("brightness", 0.1), ("contrast", 1.25), ("saturation", 0.5))),
        Effect("blur", (("radius", 8.0),)),
        Effect("vignette", (("strength", 0.5),)),
        Effect("grayscale"),
    ))
    spec = G.build(project, G.preset_by_id("calidad"), "o.mp4")
    chain = (
        "[v0]eq=brightness=0.1:contrast=1.25:saturation=0.5,gblur=sigma=8,"
        "vignette=angle=0.785398,hue=s=0[vglobal]"
    )
    assert chain in spec.filter_complex
    assert spec.filter_complex.endswith(chain)
    assert spec.args[spec.args.index("-map") + 1] == "[vglobal]"
    assert "(('radius', 8.0),)" in str(project.effects)


def test_blur_se_escala_al_alto_de_salida_del_preset() -> None:
    project = _project(
        effects=(Effect("blur", (("radius", 10.0),)),),
        height=1080, width=1920, asset=HD,
    )
    spec = G.build(project, G.preset_by_id("ligero"), "o.mp4")
    assert (spec.width, spec.height) == (1280, 720)
    # 10 px del lienzo 1080p → 10 × 720/1080 = 6.666667 px en la salida de 720p.
    assert "gblur=sigma=6.666667" in spec.filter_complex


def test_los_efectos_globales_van_antes_de_los_textos() -> None:
    project = replace(
        _project(effects=(Effect("vignette", (("strength", 0.75),)),)),
        texts=(TextOverlay("t1", "Hola", 0.0, 1.0),),
    )
    fc = G.build(project, G.preset_by_id("calidad"), "o.mp4").filter_complex
    assert fc.index("vignette=angle=") < fc.index("drawtext=")
    assert "[vglobal]drawtext=" in fc and fc.endswith("[vtext]")


# -- preview sintético --------------------------------------------------------------------


def test_preview_sin_efectos_devuelve_el_mismo_frame() -> None:
    frame = np.zeros((20, 30, 3), np.uint8)
    assert ge.apply_frame(_project(), frame) is frame


def test_preview_color_blur_vignette_y_blanco_y_negro() -> None:
    flat = np.full((40, 60, 3), 100, np.uint8)
    bright = _project(effects=(Effect("eq", (("brightness", 0.2),)),))
    assert ge.apply_frame(bright, flat).mean() > flat.mean() + 30

    desaturated = _project(effects=(Effect("eq", (("saturation", 0.0),)),))
    pixel = np.array([[[200, 100, 50]]], np.uint8)
    out = ge.apply_frame(desaturated, pixel)
    assert int(out[0, 0, 0]) == int(out[0, 0, 1]) == int(out[0, 0, 2])

    # Borde vertical nítido: el blur lo suaviza sin tocar los extremos.
    edge = np.zeros((80, 80, 3), np.uint8)
    edge[:, 40:] = 255
    blurred = ge.apply_frame(
        _project(effects=(Effect("blur", (("radius", 3.0),)),), height=80, width=80), edge
    )
    assert 0 < int(blurred[40, 39, 0]) < 255 and 0 < int(blurred[40, 40, 0]) < 255
    assert int(blurred[40, 0, 0]) == 0 and int(blurred[40, 79, 0]) == 255

    # Viñeta: centro intacto; con intensidad 0.5 la esquina queda al 25 % de la luma. El preview
    # emula el rango limitado del MP4 (Y=235 para el blanco), así que el RGB de la esquina es ~58.
    white = np.full((60, 100, 3), 255, np.uint8)
    vignetted = ge.apply_frame(
        _project(effects=(Effect("vignette", (("strength", 0.5),)),), height=60, width=100),
        white,
    )
    assert int(vignetted[30, 50, 0]) == 255
    assert int(vignetted[0, 0, 0]) == pytest.approx(50, abs=2)
    mask = ge.vignette_mask(60, 100, 0.5)
    assert float(mask[30, 50, 0]) == pytest.approx(1.0)
    assert float(mask[0, 0, 0]) == pytest.approx(0.25)

    gray = ge.apply_frame(
        _project(effects=(Effect("grayscale"),)), np.array([[[200, 100, 50]]], np.uint8)
    )
    assert int(gray[0, 0, 0]) == int(gray[0, 0, 1]) == int(gray[0, 0, 2])
    assert int(gray[0, 0, 0]) == pytest.approx(0.299 * 200 + 0.587 * 100 + 0.114 * 50, abs=1)


# -- export real --------------------------------------------------------------------------


def _make_white_video(path) -> None:  # noqa: ANN001
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "color=c=white:size=320x180:rate=30:duration=1",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )


def _first_frame(path) -> np.ndarray:  # noqa: ANN001
    import av

    with av.open(str(path)) as container:
        return next(container.decode(container.streams.video[0])).to_ndarray(format="rgb24")


def _sample(rgb: np.ndarray, x: float, y: float) -> tuple[int, int, int]:
    height, width = rgb.shape[:2]
    return tuple(int(v) for v in rgb[min(height - 1, int(y * height)), min(width - 1, int(x * width))])


def test_export_real_de_1s_con_blur_y_vineta(tmp_path) -> None:
    source = tmp_path / "blanco.mp4"
    _make_white_video(source)
    asset = media.probe(source)
    project = Project(
        assets=(asset,),
        video=Track("video", (Clip("c1", asset.id, 0.0, 1.0),)),
        fps=30, width=320, height=180,
        effects=(
            Effect("blur", (("radius", 5.0),)),
            Effect("vignette", (("strength", 0.5),)),
        ),
    )
    out = tmp_path / "fx.mp4"
    spec = G.build(project, G.preset_by_id("calidad"), out, exe=F.ffmpeg_exe())
    assert "gblur=sigma=5" in spec.filter_complex
    assert "vignette=angle=0.785398" in spec.filter_complex

    exporter = export_mod.Exporter()
    exporter.start(project, "calidad", out)
    result = exporter.wait(120)
    assert result.state == "done", result.message
    assert out.is_file() and out.stat().st_size > 0

    probed = media.probe(out)
    assert probed.duration == pytest.approx(1.0, abs=0.1)
    assert (probed.width, probed.height) == (320, 180)

    # Paridad: el frame exportado y el del preview coinciden en centro y esquina (±YUV/dither).
    exported = _first_frame(out)
    preview = ge.apply_frame(project, np.full((180, 320, 3), 255, np.uint8))
    for x, y in ((0.5, 0.5), (0.02, 0.02), (0.98, 0.98), (0.2, 0.5)):
        got, expected = _sample(exported, x, y), _sample(preview, x, y)
        assert all(abs(a - b) <= 10 for a, b in zip(got, expected)), (x, y, got, expected)
    assert _sample(exported, 0.02, 0.02)[0] < _sample(exported, 0.5, 0.5)[0]
