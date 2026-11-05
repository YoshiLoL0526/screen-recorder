"""Feature A (docs/23): matemática pura de mover/redimensionar sobre el canvas del preview."""

from __future__ import annotations

from dataclasses import replace

import pytest

from recstudio.core import canvas_edit as ce
from recstudio.core import timeline as tl
from recstudio.core import transform as tx
from recstudio.core.project import Clip, MediaAsset, Project, Track, Transform


def asset(aid: str = "a", width: int = 1920, height: int = 1080) -> MediaAsset:
    return MediaAsset(id=aid, path=f"C:/v/{aid}.mp4", duration=30.0, width=width,
                      height=height, fps=30.0, has_audio=False)


def base_project(*, width: int = 1920, height: int = 1080,
                 transform: Transform | None = None) -> Project:
    return Project(
        assets=(asset(width=width, height=height),),
        video=Track("video", (Clip("c1", "a", 0, 5, transform=transform),)),
        fps=30, width=width, height=height,
    )


STAGE = (960, 540)


def _layout(project: Project, transform: Transform):  # noqa: ANN202
    clip = project.video.clips[0]
    return tx.layout(project, replace(clip, transform=transform), STAGE)


# -- escenario y cajas -----------------------------------------------------------------


def test_fit_stage_conserva_el_aspecto_y_admite_hueco_sin_medir() -> None:
    assert ce.fit_stage(1920, 1080, 900, 600) == (900, 506)
    vertical = ce.fit_stage(1080, 1920, 900, 600)
    assert vertical == (337, 600)
    assert abs(vertical[0] / vertical[1] - 1080 / 1920) < 0.01
    assert ce.fit_stage(1920, 1080, 0, 0) == (0, 0)
    assert ce.fit_stage(1920, 1080, 1, 600) == (0, 0)
    assert ce.fit_stage(0, 1080, 900, 600) == (0, 0)


def test_visible_rect_recorta_contra_el_escenario() -> None:
    project = base_project()
    clip = project.video.clips[0]
    full = tx.layout(project, clip, STAGE)
    assert ce.visible_rect(full) == ce.Rect(0, 0, 960, 540)

    moved = tl.set_transform(project, "c1", x=1.0, y=0.5)
    rect = ce.visible_rect(tx.layout(moved, moved.video.clips[0], STAGE))
    assert rect is not None
    assert rect.left == 480 and rect.width == 480  # la mitad derecha se sale
    assert (rect.top, rect.height) == (0, 540)

    outside = tl.set_transform(project, "c1", x=1.0, y=0.0, scale=0.25)
    # El centro en la esquina y un cuarto de escala siguen dejando un trozo visible.
    assert ce.visible_rect(tx.layout(outside, outside.video.clips[0], STAGE)) is not None


def test_handle_centres_mete_las_asas_dentro_si_el_clip_sobresale() -> None:
    project = base_project()
    clip = project.video.clips[0]
    lay = tx.layout(project, clip, STAGE)
    centres = ce.handle_centres(lay, 12.0)
    assert centres["tl"] == (6.0, 6.0)
    assert centres["br"] == (954.0, 534.0)

    big = tl.set_transform(project, "c1", scale=4.0)
    lay_big = tx.layout(big, big.video.clips[0], STAGE)
    centres = ce.handle_centres(lay_big, 12.0)
    for x, y in centres.values():
        assert 6 <= x <= 954 and 6 <= y <= 534


# -- mover -----------------------------------------------------------------------------


def test_mover_cambia_el_centro_en_fracciones_y_recorta_a_0_1() -> None:
    project = base_project()
    clip = project.video.clips[0]
    moved = ce.move_transform(project, clip, STAGE, 96, -54)
    assert moved.x == pytest.approx(0.6)
    assert moved.y == pytest.approx(0.4)
    lay = _layout(project, moved)
    assert (lay.x, lay.y) == (96, -54)

    far = ce.move_transform(project, clip, STAGE, 10_000, -10_000)
    assert (far.x, far.y) == (1.0, 0.0)
    assert 0.0 <= far.x <= 1.0 and 0.0 <= far.y <= 1.0


def test_mover_sin_delta_no_cambia_el_proyecto() -> None:
    project = base_project(transform=Transform(scale=1.5, x=0.25, y=0.75))
    clip = project.video.clips[0]
    same = ce.move_transform(project, clip, STAGE, 0.0, 0.0)
    assert same == clip.transform
    assert tl.set_transform(project, "c1", **ce.as_changes(same)) == project


# -- escalar ---------------------------------------------------------------------------


def test_escalar_desde_br_crece_y_ancla_la_esquina_opuesta() -> None:
    project = base_project()
    clip = project.video.clips[0]
    start = tx.layout(project, clip, STAGE)
    scaled = ce.scale_transform(project, clip, STAGE, "br", 96, 0)
    assert scaled.scale == pytest.approx(1.075964, rel=1e-5)
    lay = _layout(project, scaled)
    assert (lay.x, lay.y) == (start.x, start.y)  # esquina superior izquierda quieta
    assert lay.out_width > start.out_width and lay.out_height > start.out_height


def test_escalar_desde_cada_esquina_ancla_la_contraria() -> None:
    project = base_project(transform=Transform(scale=0.75, x=0.5, y=0.5))
    clip = project.video.clips[0]
    start = tx.layout(project, clip, STAGE)
    for handle in ce.HANDLES:
        scaled = ce.scale_transform(project, clip, STAGE, handle, 48, 32)
        lay = _layout(project, scaled)
        # Esquina opuesta (en píxeles del escenario) antes y después: la misma.
        if handle in ("tl", "bl"):
            before_x, after_x = start.x + start.out_width, lay.x + lay.out_width
        else:
            before_x, after_x = start.x, lay.x
        if handle in ("tl", "tr"):
            before_y, after_y = start.y + start.out_height, lay.y + lay.out_height
        else:
            before_y, after_y = start.y, lay.y
        assert (after_x, after_y) == (before_x, before_y)


def test_escalar_respeta_el_rango_0_25_4_y_el_centro_0_1() -> None:
    project = base_project()
    clip = project.video.clips[0]
    big = ce.scale_transform(project, clip, STAGE, "br", 1e9, 1e9)
    assert big.scale == ce.MAX_SCALE == 4.0
    small = ce.scale_transform(project, clip, STAGE, "br", -1e9, -1e9)
    assert small.scale == ce.MIN_SCALE == 0.25
    for transform in (big, small):
        assert 0.0 <= transform.x <= 1.0 and 0.0 <= transform.y <= 1.0

    # Con el ancla en una esquina del lienzo, crecer mucho puede empujar el centro fuera:
    # se recorta a 0…1 (el ancla se mueve lo justo, pero la transformación sigue siendo válida).
    anchored = ce.scale_transform(project, clip, STAGE, "tl", -1e9, -1e9)
    assert anchored.scale == 4.0
    assert (anchored.x, anchored.y) == (0.0, 0.0)


def test_escalar_sin_delta_no_cambia_el_proyecto() -> None:
    project = base_project(transform=Transform(fit="fill", scale=1.25, x=0.4, y=0.6))
    clip = project.video.clips[0]
    same = ce.scale_transform(project, clip, STAGE, "tl", 0.0, 0.0)
    assert same == clip.transform
    assert tl.set_transform(project, "c1", **ce.as_changes(same)) == project


def test_escalar_rechaza_asa_desconocida_y_escenario_vacio() -> None:
    project = base_project()
    clip = project.video.clips[0]
    with pytest.raises(ValueError):
        ce.scale_transform(project, clip, STAGE, "centro", 10, 10)
    with pytest.raises(ValueError):
        ce.move_transform(project, clip, (0, 0), 10, 10)


def test_as_changes_permite_volver_al_neutro() -> None:
    project = base_project(transform=Transform(scale=0.5, x=0.25, y=0.75))
    clip = project.video.clips[0]
    changed = tl.set_transform(project, "c1", **ce.as_changes(Transform()))
    assert changed.video.clips[0].transform is None
    assert clip.transform is not None  # el proyecto de partida no se muta
