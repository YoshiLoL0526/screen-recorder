"""Base del modelo v6/v7 (docs/22 y docs/28): elementos, efectos globales y tipo de asset.

Las features de composición construyen encima de este contrato; aquí se comprueba que la base
no cambia lo visible de un proyecto v5 y que los hooks de preview/export son no-op.
"""

from __future__ import annotations

import json

import pytest

from recstudio.core import animation as anim
from recstudio.core import elements as el
from recstudio.core import keyframes, store as store_mod
from recstudio.core.errors import ProjectError
from recstudio.core.project import (
    SCHEMA_VERSION,
    Clip,
    Effect,
    Element,
    MediaAsset,
    Project,
    TextOverlay,
    Track,
    from_dict,
    to_dict,
)

A = MediaAsset("a", "a.mp4", 10.0, 640, 360, 30.0, True)
IMG = MediaAsset("img", "logo.png", 0.0, 200, 100, 0.0, False, kind="image")
AUDIO = MediaAsset("m", "m.wav", 10.0, 0, 0, 0.0, True, kind="audio")


def _project(**changes) -> Project:  # noqa: ANN001
    base = Project(
        assets=(A, IMG), video=Track("video", (Clip("c1", "a", 0.0, 10.0),)),
        width=640, height=360, fps=30,
    )
    return Project(**{**base.__dict__, **changes})


def test_defaults_v9_y_round_trip() -> None:
    assert SCHEMA_VERSION == 9
    assert Project().elements == () and Project().effects == ()
    element = Element(
        "e1", "rect", 1.0, 5.0, x=0.25, y=0.75, width=0.4, height=0.3,
        rotation=15.0, opacity=0.8, color="#FF5470",
        effects=(Effect("blur", (("radius", 8.0),)),),
    )
    global_fx = Effect("blur", (("radius", 4.0),))
    project = _project(elements=(element,), effects=(global_fx,)).validate()
    data = json.loads(json.dumps(to_dict(project)))
    assert data["elements"][0]["kind"] == "rect"
    assert data["effects"] == [{"kind": "blur", "params": [["radius", 4.0]]}]
    assert from_dict(data) == project
    assert project.element("e1") == element and project.has_element("e1")


def test_validacion_de_elementos_y_efectos_globales() -> None:
    with pytest.raises(ProjectError):
        Element("e", "star", 0.0, 1.0)
    with pytest.raises(ProjectError):
        Element("e", "rect", 2.0, 1.0)
    with pytest.raises(ProjectError):
        Element("e", "image", 0.0, 1.0)  # sin asset
    with pytest.raises(ProjectError):
        Element("e", "rect", 0.0, 1.0, opacity=1.5)
    with pytest.raises(ProjectError):
        Element("e", "rect", 0.0, 1.0, width=0.0)
    with pytest.raises(ProjectError):
        _project(elements=(Element("e", "image", 0.0, 1.0, asset_id="a"),)).validate()
    with pytest.raises(ProjectError):
        _project(elements=(Element("e", "rect", 0.0, 1.0), Element("e", "rect", 1.0, 2.0))).validate()
    with pytest.raises(ProjectError):
        _project(effects=(Effect("blur"), Effect("blur"))).validate()
    _project(elements=(Element("e", "image", 0.0, 1.0, asset_id="img"),)).validate()


def test_elemento_video_pip_round_trip_y_limites() -> None:
    element = Element("v1", "video", 1.0, 3.0, asset_id="a", in_=2.0, width=0.4, height=0.3)
    project = _project(elements=(element,)).validate()
    data = json.loads(json.dumps(to_dict(project)))
    assert data["elements"][0]["kind"] == "video" and data["elements"][0]["in"] == 2.0
    assert from_dict(data) == project

    with pytest.raises(ProjectError):
        Element("v2", "video", 0.0, 2.0)  # sin asset
    with pytest.raises(ProjectError):
        _project(elements=(Element("v3", "video", 0.0, 2.0, asset_id="img"),)).validate()
    with pytest.raises(ProjectError):
        _project(elements=(Element("v4", "video", 0.0, 9.5, asset_id="a", in_=1.0),)).validate()


def test_pistas_de_video_v2_v3_por_capa() -> None:
    project = _project()
    project, v2 = el.add_element(project, "video", 0.0, 1.0, asset_id="a", video_track=1)
    project, v3 = el.add_element(project, "video", 0.0, 1.0, asset_id="a", video_track=2)
    project, generic = el.add_element(project, "rect", 0.0, 1.0)
    # z-order: V2 < V3 < Elementos, aunque se añada la figura al final.
    assert [e.id for e in project.elements] == [v2.id, v3.id, generic.id]
    assert (v2.video_track, v3.video_track, generic.video_track) == (1, 2, 0)
    assert (v2.width, v2.height, v2.fit) == (1.0, 1.0, "fill")

    data = json.loads(json.dumps(to_dict(project)))
    assert data["elements"][0]["video_track"] == 1
    assert from_dict(data) == project

    with pytest.raises(ProjectError):
        el.set_video_track(project, generic.id, 1)  # una figura no va a V2
    with pytest.raises(ProjectError):
        Element("e", "rect", 0.0, 1.0, video_track=1)
    with pytest.raises(ProjectError):
        Element("e", "video", 0.0, 1.0, asset_id="a", video_track=3)

    reordered = el.set_video_track(project, v2.id, 2)
    assert [e.video_track for e in reordered.elements] == [2, 2, 0]
    assert [e.id for e in reordered.elements] == [v3.id, v2.id, generic.id]


def test_zorder_respeta_las_capas_de_video() -> None:
    project = _project()
    project, v2a = el.add_element(project, "video", 0.0, 1.0, asset_id="a", video_track=1)
    project, v2b = el.add_element(project, "video", 0.0, 1.0, asset_id="a", video_track=1)
    project, v3 = el.add_element(project, "video", 0.0, 1.0, asset_id="a", video_track=2)
    project, generic = el.add_element(project, "rect", 0.0, 1.0)
    ids = lambda p: [e.id for e in p.elements]  # noqa: E731

    front = el.bring_to_front(project, v2a.id)
    assert ids(front) == [v2b.id, v2a.id, v3.id, generic.id]
    assert el.bring_to_front(project, v2b.id) == project  # ya está al frente de V2
    back = el.send_to_back(project, v2b.id)
    assert ids(back) == [v2b.id, v2a.id, v3.id, generic.id]
    assert el.send_to_back(project, v2a.id) == project  # ya está al fondo de V2
    assert ids(el.raise_element(project, v2a.id)) == [v2b.id, v2a.id, v3.id, generic.id]
    assert el.raise_element(project, v3.id) == project  # única de V3
    assert el.send_to_back(project, generic.id) == project  # única de Elementos


def test_subtitulos_y_keyframes_round_trip_y_migracion() -> None:
    curve = anim.Animation("x", (
        anim.Keyframe(0.0, 0.2),
        anim.Keyframe(1.0, 0.8),
    ))
    fade = anim.Animation("opacity", (
        anim.Keyframe(0.0, 1.0),
        anim.Keyframe(2.0, 0.0),
    ))
    text = TextOverlay(
        "t1", "Hola", 1.0, 3.0, opacity=0.75, subtitle=True,
        animations=(curve, fade),
    )
    element = Element(
        "e1", "video", 0.0, 2.0, asset_id="a", animations=(curve,),
    )
    project = _project(texts=(text,), elements=(element,)).validate()
    data = json.loads(json.dumps(to_dict(project)))
    assert data["texts"][0]["subtitle"] is True and data["texts"][0]["opacity"] == 0.75
    assert data["texts"][0]["animations"][0]["channel"] == "x"
    assert data["elements"][0]["animations"][0]["points"][1]["value"] == 0.8
    assert from_dict(data) == project

    # Un v8 sin los campos nuevos migra con opacidad 1, subtítulo apagado y sin animaciones.
    legacy = to_dict(project)
    legacy["schema_version"] = 8
    legacy["texts"][0].pop("opacity", None)
    legacy["texts"][0].pop("subtitle", None)
    legacy["texts"][0].pop("animations", None)
    legacy["elements"][0].pop("animations", None)
    migrated, read_only = store_mod.migrate(legacy)
    assert not read_only and migrated["schema_version"] == SCHEMA_VERSION
    restored = from_dict(migrated)
    assert restored.text("t1").opacity == 1.0 and not restored.text("t1").subtitle
    assert restored.text("t1").animations == ()
    assert restored.element("e1").animations == ()


def test_audio_del_pip_usa_el_efecto_volume() -> None:
    project, element = el.add_element(
        _project(), "video", 0.0, 2.0, asset_id="a", blur=8.0, audio_gain=0.4,
    )
    assert el.audio_gain(element) == pytest.approx(0.4)
    assert el.blur_radius(element) == pytest.approx(8.0)
    data = json.loads(json.dumps(to_dict(project)))
    effects = data["elements"][0]["effects"]
    assert {"kind": "volume", "params": [["gain", 0.4]]} in effects
    assert {"kind": "blur", "params": [["radius", 8.0]]} in effects
    assert from_dict(data) == project

    # 1.0 es el neutro: el efecto desaparece, pero el blur sigue.
    neutral = el.set_audio_gain(project, element.id, 1.0)
    assert el.audio_gain(neutral.element(element.id)) == 1.0
    assert [fx.kind for fx in neutral.element(element.id).effects] == ["blur"]
    muted = el.set_audio_gain(project, element.id, 0.0)
    assert not el.has_audio(muted.element(element.id))
    assert el.audio_gain(muted.element(element.id)) == 0.0

    rect = el.add_element(project, "rect", 0.0, 1.0)[1]
    with pytest.raises(ProjectError):
        el.set_audio_gain(project, rect.id, 0.5)


def test_migracion_v6_v7_conserva_elementos() -> None:
    element = Element("e1", "image", 0.0, 2.0, asset_id="img", width=0.3, height=0.2)
    project = _project(elements=(element,)).validate()
    v6 = to_dict(project)
    v6["schema_version"] = 6
    v6["elements"][0].pop("in", None)
    migrated, read_only = store_mod.migrate(v6)
    assert not read_only and migrated["schema_version"] == SCHEMA_VERSION
    restored = from_dict(migrated)
    assert restored.element("e1").in_ == 0.0
    assert restored.elements[0].kind == "image"


def test_migracion_v5_v6_abre_identico_y_tipos_de_asset() -> None:
    project = _project(assets=(A,))
    v5 = to_dict(project)
    for key in ("elements", "effects"):
        v5.pop(key)
    for asset in v5["assets"]:
        asset.pop("kind")
    v5["schema_version"] = 5
    migrated, read_only = store_mod.migrate(v5)
    assert not read_only and migrated["schema_version"] == SCHEMA_VERSION
    assert migrated["elements"] == [] and migrated["effects"] == []
    kinds = {asset["id"]: asset["kind"] for asset in migrated["assets"]}
    assert kinds == {"a": "video"}
    assert from_dict(migrated) == project

    v5_audio = to_dict(_project(assets=(A, IMG, AUDIO)))
    for asset in v5_audio["assets"]:
        asset.pop("kind")
    v5_audio["schema_version"] = 5
    kinds = {a["id"]: a["kind"] for a in store_mod.migrate(v5_audio)[0]["assets"]}
    assert kinds["m"] == "audio"


def test_corte_rapido_bloqueado_por_composicion() -> None:
    element = Element("e", "rect", 0.0, 2.0)
    reasons = keyframes.blockers(_project(elements=(element,)))
    assert "tiene elementos superpuestos" in reasons
    reasons = keyframes.blockers(_project(effects=(Effect("blur", (("radius", 2.0),)),)))
    assert "tiene efectos globales" in reasons
    assert keyframes.blockers(_project()) == []


def test_hooks_de_composicion_son_noop() -> None:
    from recstudio.core import global_effects, overlays

    project = _project()
    chains: list[str] = []
    inputs = ["-ss", "0", "-i", "a.mp4"]
    label, next_input = overlays.apply_graph(
        project, "v0", chains, inputs, (640, 360), first_input=1,
    )
    assert (label, next_input) == ("v0", 1) and chains == [] and inputs == ["-ss", "0", "-i", "a.mp4"]
    assert global_effects.apply_graph(project, "v0", chains) == "v0" and chains == []
    assert overlays.apply_frame(project, 1.0, "frame") == "frame"
    assert global_effects.apply_frame(project, "frame") == "frame"
