"""Keyframes de posición/opacidad (docs/31): modelo, interpolación y expresiones FFmpeg."""

from __future__ import annotations

from dataclasses import replace

import pytest

from recstudio.core import animation as anim
from recstudio.core import elements as el
from recstudio.core.errors import ProjectError
from recstudio.core.project import Element, TextOverlay


def test_interpolacion_y_limites() -> None:
    curve = anim.Animation("x", (
        anim.Keyframe(0.0, 0.2),
        anim.Keyframe(1.0, 0.8),
    ))
    assert curve.value_at(-1.0, 0.5) == pytest.approx(0.2)
    assert curve.value_at(0.0, 0.5) == pytest.approx(0.2)
    assert curve.value_at(0.5, 0.5) == pytest.approx(0.5)
    assert curve.value_at(1.0, 0.5) == pytest.approx(0.8)
    assert curve.value_at(9.0, 0.5) == pytest.approx(0.8)


def test_expresion_ffmpeg_por_tramos() -> None:
    curve = anim.Animation("x", (
        anim.Keyframe(0.0, 0.2),
        anim.Keyframe(1.0, 0.8),
    ))
    expression = curve.expr(start=2.0, default=0.5)
    assert "if(lt(t,2)" in expression
    assert "if(lt(t,3)" in expression
    assert "(0.2+(0.8-0.2)*(t-2)/1)" in expression


def test_set_y_clear_de_claves() -> None:
    animations = anim.set_keyframe((), "x", 1.0, 0.4)
    animations = anim.set_keyframe(animations, "x", 0.0, 0.1)
    curve = anim.find(animations, "x")
    assert curve is not None
    assert [point.t for point in curve.points] == [0.0, 1.0]
    updated = anim.set_keyframe(animations, "x", 0.5, 0.25)
    assert [point.value for point in anim.find(updated, "x").points] == [0.1, 0.25, 0.4]
    assert anim.clear_channel(animations, "x") == ()
    with pytest.raises(ProjectError):
        anim.Keyframe(-1.0, 0.5)
    with pytest.raises(ProjectError):
        anim.Animation("x", (anim.Keyframe(0.0, 1.5),))
    with pytest.raises(ProjectError):
        anim.Animation("x", (anim.Keyframe(1.0, 0.2), anim.Keyframe(1.0, 0.3)))


def test_text_filters_incluye_opacidad_estatica_y_animada() -> None:
    from recstudio.core import graph as G
    from recstudio.core.project import Clip, MediaAsset, Project, Track

    asset = MediaAsset("a", "a.mp4", 2.0, 320, 180, 30.0, False)
    project = Project(
        assets=(asset,), width=320, height=180, fps=30,
        video=Track("video", (Clip("c", "a", 0.0, 2.0),)),
    )
    static = TextOverlay("t1", "Hola", 0.0, 2.0, opacity=0.5)
    animated = TextOverlay(
        "t2", "Adiós", 0.0, 2.0,
        animations=(
            anim.Animation("x", (anim.Keyframe(0.0, 0.2), anim.Keyframe(1.0, 0.8))),
            anim.Animation("opacity", (anim.Keyframe(0.0, 1.0), anim.Keyframe(1.0, 0.0))),
        ),
    )
    filters = G.text_filters(replace(project, texts=(static, animated)), 180)
    assert "alpha=0.5" in filters[0]
    assert "x='(w*if(lt(t,0)" in filters[1]
    assert "alpha='if(lt(t,0)" in filters[1]


def test_elemento_effective_y_canales_permitidos() -> None:
    element = Element(
        "e", "video", 2.0, 5.0, asset_id="a",
        animations=(anim.Animation("x", (anim.Keyframe(0.0, 0.2),) ),),
    )
    moved = el.effective(element, 2.5)
    assert moved.x == pytest.approx(0.2)
    assert moved.y == pytest.approx(element.y)
    with pytest.raises(ProjectError):
        Element("e", "video", 0.0, 1.0, asset_id="a",
                animations=(anim.Animation("opacity", (anim.Keyframe(0.0, 0.5),)),))
    text = TextOverlay(
        "t", "hola", 0.0, 1.0,
        animations=(anim.Animation("opacity", (anim.Keyframe(0.0, 0.5),)),),
    )
    assert text.animations[0].channel == "opacity"
