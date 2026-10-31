"""Fase 0 (docs/18/docs/19): modelo v3, pistas absolutas, validación y migración v1/v2."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from recstudio.core import store as store_mod
from recstudio.core import timeline as tl
from recstudio.core.errors import ProjectError
from recstudio.core.project import (
    SCHEMA_VERSION,
    Clip,
    MediaAsset,
    Project,
    Track,
    from_dict,
    to_dict,
)


def asset(aid: str = "a1", duration: float = 60.0, *, has_audio: bool = True) -> MediaAsset:
    return MediaAsset(id=aid, path=f"C:/v/{aid}.mp4", duration=duration, width=1920, height=1080,
                      fps=30.0, has_audio=has_audio)


def base_project() -> Project:
    return Project(
        assets=(asset(),),
        video=Track("video", (Clip("c1", "a1", 0, 10), Clip("c2", "a1", 10, 20)), (None,)),
        fps=30,
    )


# -- modelo -----------------------------------------------------------------------


def test_esquema_v3_y_defaults_retrocompatibles() -> None:
    assert SCHEMA_VERSION == 3
    clip = Clip("c", "a", 0, 1)
    assert (clip.start, clip.audio_enabled, clip.link_id) == (None, True, None)
    track = Track("audio")
    assert (track.mode, track.name, track.solo, track.locked) == ("sequence", "", False, False)


def test_pista_absoluta_con_huecos_en_clip_starts_y_duracion() -> None:
    track = Track(
        "audio", (
            Clip("m1", "a1", 0, 2, start=1.0),
            Clip("m2", "a1", 0, 3, start=5.0),
        ), (None,), mode="absolute",
    )
    assert tl.clip_starts(track) == pytest.approx([1.0, 5.0])
    assert tl.track_duration(track) == pytest.approx(8.0)
    p = Project(assets=(asset(),), video=Track("video", (Clip("c1", "a1", 0, 10),)), audio=(track,), fps=30)
    assert tl.project_extent(p) == pytest.approx(10.0)
    assert p.validate() is p


def test_pista_absoluta_sin_solapes() -> None:
    track = Track(
        "audio", (
            Clip("m1", "a1", 0, 4, start=1.0),
            Clip("m2", "a1", 0, 2, start=4.5),
        ), (None,), mode="absolute",
    )
    p = Project(assets=(asset(),), video=Track("video", (Clip("c1", "a1", 0, 10),)), audio=(track,), fps=30)
    with pytest.raises(ProjectError, match="solapados"):
        p.validate()
    # pegados sí valen (sin solape)
    glued = Track("audio", (
        Clip("m1", "a1", 0, 4, start=1.0),
        Clip("m2", "a1", 0, 2, start=5.0),
    ), (None,), mode="absolute")
    Project(assets=(asset(),), video=p.video, audio=(glued,), fps=30).validate()


def test_start_negativo_y_start_en_secuencia_fallan() -> None:
    with pytest.raises(ProjectError):
        Clip("c", "a", 0, 1, start=-0.5)
    with pytest.raises(ProjectError, match="modo absoluto"):
        Track("audio", (Clip("m", "a", 0, 1, start=0.0),))


def test_enlaces_validos_y_multiples_por_audio_dividido() -> None:
    linked = Clip("m1", "a1", 0, 5, start=0.0, link_id="c1")
    track = Track("audio", (linked,), (), mode="absolute")
    p = Project(
        assets=(asset(),),
        video=Track("video", (Clip("c1", "a1", 0, 5, audio_enabled=False),)),
        audio=(track,), fps=30,
    )
    p.validate()

    dangling = replace_track(p, (Clip("m1", "a1", 0, 5, start=0.0, link_id="nope"),))
    with pytest.raises(ProjectError, match="enlace"):
        dangling.validate()

    # Varios trozos de un audio dividido comparten el mismo enlace: es válido.
    divided = replace_track(p, (
        replace(linked, out_=2.5),
        replace(linked, id="m2", in_=2.5, start=2.5),
    ))
    divided.validate()

    video_link = Project(assets=(asset(),), video=Track("video", (Clip("c1", "a1", 0, 5, link_id="c1"),)),
                         fps=30)
    with pytest.raises(ProjectError, match="solo vale en pistas de audio"):
        video_link.validate()

    audio_disabled = replace_track(p, (replace(linked, audio_enabled=False),))
    with pytest.raises(ProjectError, match="solo vale en la pista de video"):
        audio_disabled.validate()


def replace_track(project: Project, clips) -> Project:  # noqa: ANN001
    track = Track("audio", tuple(clips), tuple(None for _ in range(max(0, len(clips) - 1))),
                  mode="absolute", name="Audio del video")
    return Project(assets=project.assets, video=project.video, audio=(track,), fps=project.fps)


# -- serialización y migración -----------------------------------------------------


def test_roundtrip_de_pista_absoluta_con_todos_los_campos() -> None:
    project = Project(
        assets=(asset(),),
        video=Track("video", (Clip("c1", "a1", 0, 10, audio_enabled=False),), (), locked=True),
        audio=(
            Track(
                "audio", (Clip("m1", "a1", 0, 5, start=2.0, link_id="c1"),), (),
                mode="absolute", name="Audio del video", gain=0.5, solo=True, locked=True, ducking=0.25,
            ),
        ),
        fps=30,
    )
    project.validate()
    data = json.loads(json.dumps(to_dict(project)))
    restored = from_dict(data)
    assert restored == project
    assert restored.audio[0].mode == "absolute" and restored.audio[0].solo
    assert restored.video.clips[0].audio_enabled is False
    assert restored.audio[0].clips[0].start == pytest.approx(2.0)


def v1_json() -> dict:
    """Forma inicial: sin textos, sin ducking y sin campos de la fase 0."""
    return {
        "schema_version": 1,
        "name": "Antiguo",
        "fps": 30,
        "width": 1280,
        "height": 720,
        "assets": [
            {"id": "a1", "path": "C:/v/a1.mp4", "duration": 20.0, "width": 1280, "height": 720,
             "fps": 30.0, "has_audio": True},
        ],
        "video": {
            "kind": "video",
            "clips": [
                {"id": "c1", "asset_id": "a1", "in_": 0.0, "out_": 5.0, "speed": 1.0, "effects": []},
            ],
            "transitions": [],
        },
    }


def v2_json() -> dict:
    data = v1_json()
    data["schema_version"] = 2
    data["video"]["muted"] = False
    data["video"]["gain"] = 1.0
    data["audio"] = [{
        "kind": "audio",
        "clips": [{"id": "m1", "asset_id": "a1", "in_": 0.0, "out_": 4.0, "speed": 1.0, "effects": []}],
        "transitions": [],
        "muted": False,
        "gain": 0.8,
        "ducking": 0.5,
    }]
    data["texts"] = []
    return data


def test_migracion_v1_v2_rellena_la_fase_0_sin_cambiar_lo_visible() -> None:
    for raw in (v1_json(), v2_json()):
        migrated, read_only = store_mod.migrate(raw)
        assert not read_only and migrated["schema_version"] == 3
        assert migrated["video"]["mode"] == "sequence"
        assert migrated["video"]["clips"][0]["start"] is None
        assert migrated["video"]["clips"][0]["audio_enabled"] is True
        assert migrated["video"]["clips"][0]["link_id"] is None
        for track in migrated.get("audio", ()):
            assert track["mode"] == "sequence"
            assert track["name"] == "" and track["solo"] is False and track["locked"] is False
        project = from_dict(migrated)
        assert project.schema_version == 3
        assert project.video.clips[0].audio_enabled is True
        assert project.video.mode == "sequence"
        if "audio" in raw:
            assert project.audio[0].ducking == pytest.approx(0.5)
            assert project.audio[0].gain == pytest.approx(0.8)
        project.validate()


def test_load_project_con_json_v2(tmp_path: Path) -> None:
    path = tmp_path / "Viejo.rsproj" / "project.json"
    path.parent.mkdir()
    path.write_text(json.dumps(v2_json()), encoding="utf-8")
    result = store_mod.load_project(path)
    assert not result.read_only and not result.from_backup
    assert result.project.schema_version == 3
    assert [c.id for c in result.project.video.clips] == ["c1"]
    assert result.project.audio[0].mode == "sequence"
    assert result.project.audio[0].clips[0].start is None
