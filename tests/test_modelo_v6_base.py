"""Base del modelo v6 (docs/22): elementos superpuestos, efectos globales y tipo de asset.

Las features de composición construyen encima de este contrato; aquí se comprueba que la base
no cambia lo visible de un proyecto v5 y que los hooks de preview/export son no-op.
"""

from __future__ import annotations

import json

import pytest

from recstudio.core import keyframes, store as store_mod
from recstudio.core.errors import ProjectError
from recstudio.core.project import (
    SCHEMA_VERSION,
    Clip,
    Effect,
    Element,
    MediaAsset,
    Project,
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


def test_defaults_v6_y_round_trip() -> None:
    assert SCHEMA_VERSION == 6
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
