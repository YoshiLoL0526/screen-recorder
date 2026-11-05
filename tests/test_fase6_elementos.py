"""Feature C (v6): elementos superpuestos, figuras e imágenes con blur (docs/25).

Cubre geometría pura, PNG de figuras con alfa, `probe_image`, filtros del graph, composición
sintética del preview y un export real de 1 s con un elemento desenfocado y una imagen.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from recstudio.core import elements as el
from recstudio.core import graph as G
from recstudio.core import keyframes
from recstudio.core import overlays
from recstudio.core.errors import MediaError, ProjectError
from recstudio.core.project import (
    Clip,
    Effect,
    Element,
    MediaAsset,
    Project,
    Track,
    from_dict,
    to_dict,
)
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import element_assets, export as export_mod, media


@pytest.fixture()
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "cache"
    folder.mkdir()
    monkeypatch.setattr(paths, "cache_dir", lambda: folder)
    return folder


def _project(
    *, image: bool = False, image_path: str = "logo.png", width: int = 320, height: int = 240
) -> Project:
    assets = [MediaAsset("v", "v.mp4", 10.0, 320, 240, 30.0, False)]
    if image:
        assets.append(MediaAsset("img", image_path, 0.0, 200, 100, 0.0, False, kind="image"))
    return Project(
        assets=tuple(assets),
        video=Track("video", (Clip("c", "v", 0.0, 10.0),)),
        width=width, height=height, fps=30,
    )


def _png(path: Path, size: tuple[int, int], color, *, mode: str = "RGBA") -> Path:  # noqa: ANN001
    Image.new(mode, size, color).save(path)
    return path


# -- geometría -----------------------------------------------------------------------


def test_geometria_normalizada_y_encajes_coinciden_en_dos_lienzos() -> None:
    project = _project(image=True)
    rect = Element("rect", "rect", 0.0, 2.0, x=0.25, y=0.75, width=0.5, height=0.25)
    small = el.layout(project, rect, (320, 240))
    large = el.layout(project, rect, (640, 480))
    assert (small.box_width, small.box_height) == (160, 60)
    assert (large.box_width, large.box_height) == (320, 120)
    assert (small.center_x, small.center_y) == (80.0, 180.0)
    assert (large.center_x, large.center_y) == (160.0, 360.0)
    assert (small.x, small.y) == (0, 150)
    assert (large.x, large.y) == (0, 300)

    image = Element("img", "image", 0.0, 2.0, asset_id="img", width=0.5, height=0.5)
    fit = el.layout(project, image, (320, 240))
    assert (fit.draw_width, fit.draw_height) == (160, 80)  # 200×100 encajado
    fill = el.layout(project, replace(image, fit="fill"), (320, 240))
    assert (fill.draw_width, fill.draw_height) == (160, 120)
    assert (fill.scale_width, fill.scale_height) == (240, 120)  # cubre y recorta
    stretch = el.layout(project, replace(image, fit="stretch"), (320, 240))
    assert (stretch.draw_width, stretch.draw_height) == (160, 120)


def test_blur_rotacion_y_orden_de_apilado() -> None:
    project = _project()
    element = Element(
        "e", "rect", 1.0, 3.0, width=0.5, height=0.25, rotation=30.0, opacity=0.5,
        effects=(Effect("blur", (("radius", 10.0),)),),
    )
    small = el.layout(project, element, (320, 240))
    large = el.layout(project, element, (640, 480))
    assert small.blur == pytest.approx(10.0)
    assert large.blur == pytest.approx(20.0)  # escala con el alto del lienzo
    assert el.blur_radius(element) == 10.0
    assert el.rotated_size(100, 50, 30) == (112, 93)  # misma truncación que FFmpeg

    project, first = el.add_element(project, "rect", 0.0, 2.0, width=0.2, height=0.2)
    project, second = el.add_element(project, "ellipse", 1.0, 2.0, width=0.2, height=0.2)
    assert [e.id for e in project.elements] == [first.id, second.id]
    assert [e.id for e in el.active(project, 1.5)] == [first.id, second.id]
    assert [e.id for e in el.active(project, 2.5)] == [second.id]
    project = el.send_to_back(project, second.id)
    assert [e.id for e in project.elements] == [second.id, first.id]
    project = el.bring_to_front(project, second.id)
    assert [e.id for e in project.elements] == [first.id, second.id]
    project = el.raise_element(project, first.id)
    assert [e.id for e in project.elements] == [second.id, first.id]
    project = el.lower_element(project, first.id)
    assert [e.id for e in project.elements] == [first.id, second.id]


def test_edicion_de_elementos_blur_intervalo_y_borrado() -> None:
    project = _project()
    project, element = el.add_element(project, "triangle", 9.9, 5.0, width=0.2, height=0.2)
    assert (element.start, element.end) == (9.8, 10.0)  # acotado al timeline (mínimo 0.2 s)
    project = el.set_blur(project, element.id, 12.0)
    assert el.blur_radius(project.element(element.id)) == 12.0
    project = el.set_blur(project, element.id, 0.0)
    assert project.element(element.id).effects == ()
    project = el.update_element(project, element.id, x=0.1, y=0.9, opacity=0.25, rotation=45.0)
    assert (project.element(element.id).x, project.element(element.id).y) == (0.1, 0.9)
    project = el.remove_element(project, element.id)
    assert project.elements == ()
    with pytest.raises(ProjectError):
        el.remove_element(project, element.id)


def test_default_image_box_conserva_el_aspecto() -> None:
    project = _project(image=True, width=1920, height=1080)
    asset = project.asset("img")
    width, height = el.default_image_box(project, asset, fraction=0.4)
    pixels_w, pixels_h = width * project.width, height * project.height
    assert pixels_w / pixels_h == pytest.approx(asset.width / asset.height, rel=0.01)
    assert max(width, height) <= 0.4 + 1e-6


# -- assets y probe ------------------------------------------------------------------


def test_figuras_png_con_alfa_y_cache_atomica(cache: Path) -> None:
    project = _project()
    element = Element("e", "ellipse", 0.0, 2.0, width=0.5, height=0.5, color="#00FF00")
    layout = el.layout(project, element, (200, 200))
    path = element_assets.shape_png(element, layout)
    assert path.is_file() and path.read_bytes().startswith(b"\x89PNG")
    assert path == element_assets.shape_png(element, layout)  # cache por contenido
    image = Image.open(path).convert("RGBA")
    assert image.size == (100, 100)
    assert image.getpixel((50, 50)) == (0, 255, 0, 255)
    assert image.getpixel((2, 2))[3] == 0  # esquina transparente
    assert image.getpixel((98, 2))[3] == 0
    assert not list(path.parent.glob("*.tmp"))

    other = replace(element, color="#FF0000")
    assert element_assets.shape_png(other, layout) != path
    triangle = Element("t", "triangle", 0.0, 2.0, width=0.5, height=0.5)
    tri_path = element_assets.shape_png(triangle, el.layout(project, triangle, (200, 200)))
    tri = Image.open(tri_path).convert("RGBA")
    assert tri.getpixel((50, 20))[3] == 255 and tri.getpixel((2, 2))[3] == 0
    rect = Element("r", "rect", 0.0, 2.0, width=0.5, height=0.5)
    sharp = Image.open(element_assets.shape_png(rect, el.layout(project, rect, (200, 200)))).convert("RGBA")
    assert sharp.getpixel((2, 2))[3] == 255


def test_probe_image_formatos_y_asset_de_imagen(tmp_path: Path) -> None:
    sizes = {"png": (120, 80), "jpg": (60, 90), "webp": (80, 60), "bmp": (40, 30)}
    for suffix, size in sizes.items():
        mode = "RGB" if suffix in ("jpg", "bmp") else "RGBA"
        path = _png(tmp_path / f"imagen.{suffix}", size, (10, 20, 30), mode=mode)
        asset = media.probe_image(path)
        assert asset.kind == "image"
        assert (asset.width, asset.height) == size
        assert asset.duration == 0.0 and asset.fps == 0.0
        assert asset.has_audio is False and asset.rotation == 0
        assert asset.audio_channels == 0
        assert Path(asset.path).is_absolute()
    bad = tmp_path / "malo.txt"
    bad.write_bytes(b"no soy una imagen")
    with pytest.raises(MediaError):
        media.probe_image(bad)


def test_open_images_usa_el_filtro_de_imagenes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from recstudio.ui import file_dialogs

    captured: dict = {}

    def fake_open_files(*, title: str, directory, filters):  # noqa: ANN001
        captured.update(title=title, directory=directory, filters=filters)
        return [str(tmp_path / "logo.png")]

    monkeypatch.setattr(file_dialogs.dialogs, "open_files", fake_open_files)
    assert file_dialogs.open_images(tmp_path) == [tmp_path / "logo.png"]
    assert ("Imágenes", ("png", "jpg", "jpeg", "webp", "bmp")) in captured["filters"]


def test_materializar_imagen_genera_png_del_tamano_de_dibujo(cache: Path, tmp_path: Path) -> None:
    png = _png(tmp_path / "logo.png", (200, 100), (0, 255, 0, 255))
    asset = media.probe_image(png)
    project = replace(_project(), assets=(*_project().assets, asset))
    project, element = el.add_element(
        project, "image", 0.0, 2.0, asset_id=asset.id, width=0.5, height=0.5, fit="fit"
    )
    sources = element_assets.materialize(project, (320, 240))
    image = Image.open(sources[element.id]).convert("RGBA")
    assert image.size == (160, 80)
    assert sources[element.id].endswith(".png")


# -- graph ---------------------------------------------------------------------------


def test_graph_de_elementos_loop_rotate_alpha_y_overlay(cache: Path, tmp_path: Path) -> None:
    logo = _png(tmp_path / "logo.png", (200, 100), (0, 255, 0, 255))
    project = _project(image=True, image_path=str(logo))
    project, rect = el.add_element(
        project, "rect", 0.0, 2.0, x=0.25, width=0.4, height=0.25,
        color="#FF0000", opacity=0.5, rotation=30.0, blur=4.0,
    )
    project, image = el.add_element(
        project, "image", 1.0, 2.0, asset_id="img", width=0.3, height=0.3, fit="fill"
    )
    later = Element("later", "ellipse", 20.0, 22.0)  # fuera del timeline (no se materializa)
    project = replace(project, elements=(*project.elements, later))
    sources = element_assets.materialize(project, (320, 240))
    assert set(sources) == {rect.id, image.id}  # el que no llega a verse no se materializa
    spec = G.build(
        project, G.preset_by_id("calidad"), tmp_path / "o.mp4", element_sources=sources,
    )
    fc = spec.filter_complex
    assert spec.args.count("-loop") == 2
    assert fc.count("rotate=") == 1 and "rotw(" in fc and "c=none" in fc
    assert "colorchannelmixer=aa=0.5" in fc
    assert "overlay=" in fc and "between(t,0,2)*lt(t,2)" in fc
    assert "between(t,1,3)*lt(t,3)" in fc
    assert "[ov0]" in fc and "[ov1]" in fc
    assert all(path in spec.args for path in sources.values())
    assert keyframes.blockers(project)  # el corte rápido sigue bloqueado

    with pytest.raises(ProjectError):
        G.build(project, G.preset_by_id("calidad"), tmp_path / "sin.mp4")


def test_graph_sin_elementos_no_cambia(tmp_path: Path) -> None:
    project = _project()
    out = tmp_path / "a.mp4"
    base = G.build(project, G.preset_by_id("calidad"), out)
    with_empty = G.build(project, G.preset_by_id("calidad"), out, element_sources={})
    assert base.filter_complex == with_empty.filter_complex
    assert base.args == with_empty.args


# -- preview sintético ---------------------------------------------------------------


def test_preview_sintetico_rectangulo_opacidad_blur_y_imagen(cache: Path, tmp_path: Path) -> None:
    logo = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(logo).rectangle((50, 0, 99, 99), fill=(0, 255, 0, 255))
    logo.save(tmp_path / "logo.png")
    project = _project(image=True, image_path=str(tmp_path / "logo.png"))
    project, _rect = el.add_element(
        project, "rect", 0.0, 2.0, x=0.25, y=0.5, width=0.25, height=0.5,
        color="#FF0000", opacity=1.0,
    )
    sources = element_assets.materialize(project, (320, 240))
    base = np.zeros((240, 320, 3), np.uint8)
    base[:, :] = (0, 0, 255)
    frame = overlays.apply_frame(project, 0.5, base, sources=sources)
    assert tuple(frame[120, 80]) == (255, 0, 0)
    assert tuple(frame[120, 160]) == (0, 0, 255)
    assert overlays.apply_frame(project, 3.0, base, sources=sources) is base

    project = el.update_element(project, project.elements[0].id, opacity=0.5)
    sources = element_assets.materialize(project, (320, 240))
    half = overlays.apply_frame(project, 0.5, base, sources=sources)
    assert 100 <= int(half[120, 80, 0]) <= 155 and 100 <= int(half[120, 80, 2]) <= 155

    project, image = el.add_element(
        project, "image", 0.0, 2.0, x=0.75, y=0.5, width=0.25, height=0.25,
        asset_id="img", fit="stretch",
    )
    sources = element_assets.materialize(project, (320, 240))
    composed = overlays.apply_frame(project, 0.5, base, sources=sources)
    assert tuple(composed[120, 260]) == (0, 255, 0)  # mitad derecha opaca de la imagen
    assert tuple(composed[120, 220]) == (0, 0, 255)  # la mitad izquierda es transparente


def test_preview_rota_el_elemento_alrededor_del_centro(cache: Path) -> None:
    project = _project()
    project, _ = el.add_element(
        project, "rect", 0.0, 2.0, x=0.5, y=0.5, width=0.5, height=0.25, color="#FFFFFF"
    )
    project = el.update_element(project, project.elements[0].id, rotation=90.0)
    layout = el.layout(project, project.elements[0], (320, 240))
    assert (layout.rotated_width, layout.rotated_height) == (60, 160)
    sources = element_assets.materialize(project, (320, 240))
    base = np.zeros((240, 320, 3), np.uint8)
    frame = overlays.apply_frame(project, 0.5, base, sources=sources)
    assert tuple(frame[120, 160]) == (255, 255, 255)  # centro intacto
    assert tuple(frame[50, 160]) == (255, 255, 255)  # dentro de la caja girada
    assert tuple(frame[20, 160]) == (0, 0, 0)  # fuera por arriba
    assert tuple(frame[120, 120]) == (0, 0, 0)  # fuera por la izquierda


def test_preview_blur_suaviza_el_borde(cache: Path) -> None:
    project = _project()
    project, rect = el.add_element(
        project, "rect", 0.0, 2.0, x=0.5, y=0.5, width=0.25, height=0.25, color="#FFFFFF"
    )
    sources = element_assets.materialize(project, (320, 240))
    base = np.zeros((240, 320, 3), np.uint8)
    sharp = overlays.apply_frame(project, 0.5, base, sources=sources)
    assert tuple(sharp[120, 120]) == (255, 255, 255)
    # borde izquierdo de la caja: x = 160 - 40 = 120; justo fuera (118) debe ser negro
    assert tuple(sharp[120, 118]) == (0, 0, 0)

    project = el.set_blur(project, rect.id, 8.0)
    sources = element_assets.materialize(project, (320, 240))
    blurred = overlays.apply_frame(project, 0.5, base, sources=sources)
    assert tuple(blurred[120, 160]) == (255, 255, 255)  # centro de la caja
    edge = int(blurred[120, 118].mean())
    assert 0 < edge < 255  # el desenfoque vierte alfa fuera de la caja
    assert int(blurred[120, 100].mean()) == 0  # lo bastante lejos sigue negro


# -- export real ---------------------------------------------------------------------


def _make_video(path: Path, *, seconds: float = 1.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"color=c=blue:s=320x240:r=30:d={seconds}", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def _frame_at(path: Path, t: float) -> np.ndarray:
    import av

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        container.seek(max(0, int(t / float(stream.time_base))), stream=stream, backward=True)
        for frame in container.decode(stream):
            if frame.time is not None and float(frame.time) >= t - 1e-6:
                return frame.to_ndarray(format="rgb24")
    raise AssertionError(f"sin frame en {t}")


def test_export_real_de_un_segundo_con_elemento_y_blur(cache: Path, tmp_path: Path) -> None:
    source = _make_video(tmp_path / "base.mp4")
    asset = media.probe(source)
    project = Project(assets=(asset,), video=Track("video", (Clip("c", asset.id, 0.0, 1.0),)),
                      width=320, height=240, fps=30)
    project, rect = el.add_element(
        project, "rect", 0.0, 0.6, x=0.5, y=0.5, width=0.5, height=0.5,
        color="#FF0000", opacity=0.5, blur=6.0,
    )
    green = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
    ImageDraw.Draw(green).rectangle((40, 0, 79, 79), fill=(0, 255, 0, 255))
    green.save(tmp_path / "green.png")
    green_asset = media.probe_image(tmp_path / "green.png")
    project = replace(project, assets=(*project.assets, green_asset))
    project, image = el.add_element(
        project, "image", 0.7, 0.3, x=0.8, y=0.3, width=0.25, height=0.25,
        asset_id=green_asset.id, fit="stretch",
    )
    exporter = export_mod.Exporter()
    exporter.start(project, "calidad", tmp_path / "out.mp4")
    result = exporter.wait(120)
    assert result.state == "done", result.message
    import av

    with av.open(str(tmp_path / "out.mp4")) as container:
        frames = sum(1 for _ in container.decode(container.streams.video[0]))
    assert frames == 30

    inside = _frame_at(tmp_path / "out.mp4", 0.2)
    assert int(inside[120, 160, 0]) > 90  # rojo del rectángulo
    assert int(inside[120, 160, 2]) > 60  # azul del fondo sigue mezclado
    assert int(inside[30, 160, 0]) < 40  # fuera de la caja del rectángulo
    edge = inside[120, 78]  # 2 px fuera del borde izquierdo (x=80): alfa difuminado
    assert 5 < int(edge[0]) < 110
    assert int(inside[120, 60, 0]) < 10  # el halo no llega tan lejos
    after = _frame_at(tmp_path / "out.mp4", 0.8)
    pixel = after[72, 282]  # mitad derecha opaca de la imagen (x=0.8·320, y=0.3·240)
    assert int(pixel[1]) > 200 and int(pixel[0]) < 70 and int(pixel[2]) < 70
    assert int(after[72, 230, 0]) < 40  # mitad izquierda transparente: fondo azul
    assert int(after[72, 230, 2]) > 200
    assert int(after[120, 160, 0]) < 40  # el rectángulo ya no está
    assert not any(tmp_path.glob("*.part.mp4"))


def test_round_trip_de_elementos_con_blur(tmp_path: Path) -> None:
    project = _project(image=True)
    project, element = el.add_element(
        project, "image", 0.0, 2.0, asset_id="img", width=0.3, height=0.3,
        opacity=0.7, rotation=15.0, fit="fill", blur=9.0,
    )
    data = json.loads(json.dumps(to_dict(project)))
    assert from_dict(data) == project
    assert from_dict(data).element(element.id).effects[0].kind == "blur"
