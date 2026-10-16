"""Tests de F3 (núcleo puro): modelo, matemática del timeline, operaciones y undo/persistencia."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from recstudio.core import store as store_mod
from recstudio.core import timeline as tl
from recstudio.core.errors import ProjectError
from recstudio.core.project import Clip, MediaAsset, Project, Track, Transition, from_dict, to_dict
from recstudio.core.store import ProjectStore


def asset(aid: str = "a1", duration: float = 60.0, fps: float = 60.0) -> MediaAsset:
    return MediaAsset(id=aid, path=f"C:/v/{aid}.mp4", duration=duration, width=1920, height=1080,
                      fps=fps, has_audio=True)


def project_with(durations: list[float], transitions: list[float | None] | None = None, *, fps: int = 60) -> Project:
    """Clips consecutivos del mismo asset con las duraciones dadas (velocidad 1)."""
    clips = []
    cursor = 0.0
    for i, d in enumerate(durations):
        clips.append(Clip(id=f"c{i + 1}", asset_id="a1", in_=cursor, out_=cursor + d))
        cursor += d
    trans = tuple(None if t is None else Transition("fade", t) for t in (transitions or [None] * (len(durations) - 1)))
    return Project(assets=(asset(duration=max(60.0, cursor)),), video=Track("video", tuple(clips), trans), fps=fps)


# -- modelo -----------------------------------------------------------------------

def test_invariantes_de_clip_y_track() -> None:
    with pytest.raises(ProjectError):
        Clip(id="x", asset_id="a1", in_=5, out_=5)
    with pytest.raises(ProjectError):
        Clip(id="x", asset_id="a1", in_=0, out_=1, speed=8)
    with pytest.raises(ProjectError):
        Track("video", (Clip("x", "a1", 0, 1), Clip("y", "a1", 1, 2)), ())
    with pytest.raises(ProjectError):
        Transition("estrella", 0.5)


def test_validate_detecta_asset_inexistente_y_salida_fuera() -> None:
    p = project_with([5, 4])
    assert p.validate() is p
    bad = replace(p, video=Track("video", (Clip("c1", "zz", 0, 1),), ()))
    with pytest.raises(ProjectError):
        bad.validate()
    too_long = replace(p, video=Track("video", (Clip("c1", "a1", 0, 61),), ()))
    with pytest.raises(ProjectError):
        too_long.validate()
    dup = replace(p, video=Track("video", (Clip("c1", "a1", 0, 1), Clip("c1", "a1", 1, 2)), (None,)))
    with pytest.raises(ProjectError):
        dup.validate()


def test_validate_limite_de_transicion() -> None:
    p = project_with([5, 4], [2.5])  # límite = min(5, 4) / 2 = 2.0 (y tope de 2 s)
    with pytest.raises(ProjectError):
        p.validate()
    assert project_with([5, 4], [1.5]).validate()


def test_roundtrip_json_es_identico() -> None:
    p = project_with([5, 4, 3], [0.5, None])
    p = replace(p, name="Demo", video=replace(p.video, clips=(
        replace(p.video.clips[0], speed=1.5),
        *p.video.clips[1:],
    )))
    data = json.loads(json.dumps(to_dict(p)))
    assert from_dict(data) == p


# -- matemática (caso golden de docs/04) --------------------------------------------

def test_duracion_total_y_offsets_golden() -> None:
    p = project_with([5, 4, 3], [0.5, 0.4])
    assert tl.total_duration(p) == pytest.approx(11.1)
    starts = tl.clip_starts(p.video)
    assert starts == pytest.approx([0.0, 4.5, 8.1])  # = offsets de xfade
    assert tl.clip_range(p, 1) == pytest.approx((4.5, 8.5))


def test_velocidad_cambia_la_duracion() -> None:
    clip = Clip("c", "a1", in_=3.2, out_=12.0, speed=2.0)
    assert clip.duration == pytest.approx(4.4)


def test_locate_y_transicion() -> None:
    p = project_with([5, 4, 3], [0.5, 0.4])
    pos = tl.locate(p, 2.0)
    assert pos.index == 0 and pos.t_source == pytest.approx(2.0)
    # dentro del solape [4.5, 5.0) manda el saliente; el entrante llega por transition_at
    pos = tl.locate(p, 4.75)
    assert pos.index == 0 and pos.t_local == pytest.approx(4.75)
    tr = tl.transition_at(p, 4.75)
    assert tr is not None and tr.index == 0
    assert tr.progress == pytest.approx(0.5)
    assert tr.incoming.index == 1 and tr.incoming.t_local == pytest.approx(0.25)
    assert tr.incoming.t_source == pytest.approx(5.25)  # in_ del clip 2 = 5
    assert tl.transition_at(p, 3.0) is None
    pos = tl.locate(p, 6.0)
    assert pos.index == 1 and pos.t_local == pytest.approx(1.5)
    end = tl.locate(p, 99)
    assert end.index == 2 and end.t_local == pytest.approx(3.0)


def test_locate_con_velocidad() -> None:
    p = project_with([10])
    p = tl.set_speed(p, "c1", 2.0)
    pos = tl.locate(p, 2.0)
    assert pos.t_source == pytest.approx(4.0)
    assert tl.total_duration(p) == pytest.approx(5.0)


def test_conversiones_x_tiempo_y_formato() -> None:
    assert tl.x_at_time(2.0, zoom=100, scroll=50) == 150
    assert tl.time_at_x(150, zoom=100, scroll=50) == 2.0
    assert tl.frame_round(1.0 / 60 * 7.4, 60) == pytest.approx(7 / 60)
    assert tl.format_time(72.25) == "01:12.250"
    assert tl.format_time(3723.5) == "1:02:03.500"
    assert tl.parse_time("01:12.250") == pytest.approx(72.25)
    assert tl.parse_time("1:02:03,5") == pytest.approx(3723.5)
    assert tl.parse_time("7.5") == 7.5
    with pytest.raises(ValueError):
        tl.parse_time("a:b")
    assert tl.fit_zoom(100, 1000) == pytest.approx(9.6)
    assert tl.fit_zoom(0.1, 1000) == tl.ZOOM_MAX


# -- operaciones --------------------------------------------------------------------

def test_split_en_frontera_de_frame_y_reparto_de_transiciones() -> None:
    p = project_with([5, 4, 3], [0.5, 0.4])
    out = tl.split(p, 6.0071)  # cae dentro del clip 2 (empieza en 4.5)
    clips = out.video.clips
    assert len(clips) == 4
    left, right = clips[1], clips[2]
    assert left.id == "c2" and right.id != "c2"
    assert left.out_ == right.in_
    assert left.out_ * 60 == pytest.approx(round(left.out_ * 60))  # frontera de frame
    # el izquierdo conserva la entrante, el derecho hereda la saliente, el corte nace limpio
    assert out.video.transitions == (Transition("fade", 0.5), None, Transition("fade", 0.4))
    assert tl.total_duration(out) == pytest.approx(tl.total_duration(p))
    out.validate()


def test_split_en_el_borde_falla() -> None:
    p = project_with([5, 4])
    with pytest.raises(ProjectError):
        tl.split(p, 0.0)
    with pytest.raises(ProjectError):
        tl.split(p, 9.0)


def test_split_acota_transicion_que_ya_no_cabe() -> None:
    p = project_with([5, 4], [1.0])
    out = tl.split(p, 4.6)  # el trozo izquierdo del clip 1 dura 4.6 → ok; el derecho 0.4
    # la transición (antes entre c1 y c2) es ahora la saliente del trozo derecho de 0.4 s:
    # su límite es 0.2 s, así que se acota
    assert out.video.transitions[1] is not None
    assert out.video.transitions[1].duration == pytest.approx(0.2)
    out.validate()


def test_trim_acota_redondea_y_respeta_un_frame() -> None:
    p = project_with([5, 4])
    out = tl.trim(p, "c1", 1.0042, 5.0)
    c1 = out.clip("c1")
    assert c1.in_ == pytest.approx(1.0) and c1.out_ == 5.0
    # cerrar más allá del otro borde deja exactamente 1 frame
    out = tl.trim(p, "c1", 4.9999, 5.0)
    assert out.clip("c1").duration == pytest.approx(1 / 60)
    out = tl.trim(p, "c1", 0.0, -3)
    assert out.clip("c1").duration == pytest.approx(1 / 60)
    # no se sale del asset
    out = tl.trim(p, "c2", 5.0, 999)
    assert out.clip("c2").out_ == 60.0
    assert tl.trim(p, "c1", 0.0, 5.0) is p  # sin cambios: mismo objeto


def test_move_reordena_y_limpia_transiciones() -> None:
    p = project_with([5, 4, 3], [0.5, 0.4])
    out = tl.move(p, "c1", 2)
    assert [c.id for c in out.video.clips] == ["c2", "c3", "c1"]
    assert len(out.video.transitions) == 2
    assert out.video.transitions == (Transition("fade", 0.4), None)
    out = tl.move(p, "c3", 0)
    assert [c.id for c in out.video.clips] == ["c3", "c1", "c2"]
    assert out.video.transitions == (None, Transition("fade", 0.5))
    assert tl.move(p, "c2", 1) is p


def test_drop_index_por_centro_del_fantasma() -> None:
    p = project_with([5, 4, 3])
    assert tl.drop_index(p, "c1", 0.0) == 0
    assert tl.drop_index(p, "c1", 3.0) == 1  # centro 5.5 > centro de c2 (2 s en el resto)
    assert tl.drop_index(p, "c1", 50.0) == 2
    assert tl.drop_index(p, "c3", -10.0) == 0


def test_ripple_delete_cierra_el_hueco() -> None:
    p = project_with([5, 4, 3], [0.5, 0.4])
    out = tl.ripple_delete(p, "c2")
    assert [c.id for c in out.video.clips] == ["c1", "c3"]
    assert out.video.transitions == (None,)
    assert tl.total_duration(out) == pytest.approx(8.0)
    assert tl.ripple_delete(tl.ripple_delete(out, "c1"), "c3").video.clips == ()


def test_set_speed_y_transiciones() -> None:
    p = project_with([5, 4], [1.5])
    out = tl.set_speed(p, "c2", 4.0)  # c2 pasa a 1 s → la transición se acota a 0.5
    assert out.clip("c2").duration == pytest.approx(1.0)
    assert out.video.transitions[0].duration == pytest.approx(0.5)
    assert tl.set_speed(p, "c1", 99).clip("c1").speed == 4.0

    p2, clamped = tl.set_transition(project_with([5, 4]), 0, "wipeleft", 3.0)
    assert clamped and p2.video.transitions[0] == Transition("wipeleft", 2.0)
    p3, clamped = tl.set_transition(p2, 0, None)
    assert not clamped and p3.video.transitions == (None,)
    with pytest.raises(ProjectError):
        tl.set_transition(project_with([0.3, 4]), 0, "fade", 0.5)


def test_append_clip_no_duplica_assets() -> None:
    p = Project()
    a = asset("a9", 12.0)
    p, c1 = tl.append_clip(p, a)
    p, c2 = tl.append_clip(p, replace(a, id="otro"))  # misma ruta → mismo asset
    assert len(p.assets) == 1 and c2.asset_id == "a9"
    assert [c.id for c in p.video.clips] == [c1.id, c2.id]
    assert p.video.transitions == (None,)
    assert tl.total_duration(p) == pytest.approx(24.0)


def test_snap_en_pixeles() -> None:
    cands = [0.0, 5.0, 9.0]
    assert tl.snap(5.05, cands, zoom=100) == 5.0  # 5 px < 8 px
    assert tl.snap(5.2, cands, zoom=100) == 5.2  # 20 px
    assert tl.snap(5.2, cands, zoom=20) == 5.0  # 4 px a zoom bajo
    p = project_with([5, 4])
    assert tl.snap_candidates(p, playhead=2.5, exclude="c2") == [0.0, 2.5, 5.0]
    assert 3.0 in tl.snap_candidates(p, zoom=10)


# -- store: undo/redo, transacciones y autosave ---------------------------------------

def test_undo_redo_y_etiquetas() -> None:
    seen: list[Project] = []
    s = ProjectStore(project_with([5, 4]), on_change=seen.append)
    p0 = s.project
    assert s.apply("dividir", tl.split(p0, 2.0))
    p1 = s.project
    assert s.apply("eliminar clip", tl.ripple_delete(p1, "c2"))
    assert not s.apply("nada", s.project)  # sin cambios no hay entrada
    assert s.undo_label == "eliminar clip"
    assert s.undo() == "eliminar clip" and s.project == p1
    assert s.undo() == "dividir" and s.project == p0
    assert s.undo() is None
    assert s.redo() == "dividir" and s.project == p1
    s.apply("otra", tl.ripple_delete(p1, "c1"))
    assert not s.can_redo  # un comando nuevo invalida el redo
    assert seen[-1] == s.project


def test_transaccion_de_drag_una_sola_entrada_y_cancel() -> None:
    s = ProjectStore(project_with([5, 4]))
    p0 = s.project
    s.begin("recortar")
    for k in range(1, 6):
        s.update(tl.trim(p0, "c1", 0.0, 5.0 - k * 0.1))
    assert s.commit()
    assert len(s._undo) == 1
    assert s.undo() == "recortar" and s.project == p0

    s.begin("mover clip")
    s.update(tl.move(p0, "c1", 1))
    s.cancel()
    assert s.project == p0
    assert not s.in_transaction
    s.begin("vacío")
    assert not s.commit()  # sin cambios no se apila


def test_limite_de_undo() -> None:
    s = ProjectStore(project_with([10]), limit=3)
    for k in range(6):
        s.apply(f"op{k}", tl.trim(s.project, "c1", 0.0, 9.0 - k))
    assert len(s._undo) == 3 and s.undo_label == "op5"


def test_autosave_due_respeta_intervalo_y_suciedad(tmp_path: Path) -> None:
    now = [100.0]
    s = ProjectStore(project_with([5]), clock=lambda: now[0])
    s.path = tmp_path / "p.rsproj" / "project.json"
    assert not s.autosave_due()  # limpio
    s.apply("x", tl.trim(s.project, "c1", 1.0, 5.0))
    assert s.dirty and not s.autosave_due()
    now[0] += 31
    assert s.autosave_due()
    s.save()
    assert not s.dirty and not s.autosave_due()
    assert store_mod.load_project(s.path).project == s.project


def test_guardado_atomico_bak_y_recuperacion(tmp_path: Path) -> None:
    path = tmp_path / "Demo.rsproj" / "project.json"
    v1 = project_with([5, 4])
    v2 = tl.ripple_delete(v1, "c2")
    store_mod.save_project(path, v1)
    store_mod.save_project(path, v2)
    assert path.with_name("project.json.bak").exists()
    assert not path.with_name("project.json.tmp").exists()
    assert store_mod.load_project(path).project == v2

    path.write_text("{roto", encoding="utf-8")  # corrupción
    result = store_mod.load_project(path)
    assert result.from_backup and result.project == v1 and result.warnings

    path.with_name("project.json.bak").unlink()
    with pytest.raises(ProjectError):
        store_mod.load_project(path)


def test_schema_mas_nuevo_abre_en_lectura(tmp_path: Path) -> None:
    path = tmp_path / "x.rsproj" / "project.json"
    path.parent.mkdir()
    data = to_dict(project_with([5]))
    data["schema_version"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")
    result = store_mod.load_project(path)
    assert result.read_only and result.warnings
    s = ProjectStore(result.project)
    s.path, s.read_only = path, True
    with pytest.raises(ProjectError):
        s.save()


def test_nombres_de_carpeta_seguros(tmp_path: Path) -> None:
    assert store_mod.safe_name('demo: "uno"/dos?.') == "demo uno dos"
    first = store_mod.unique_project_file(tmp_path, "Demo")
    store_mod.save_project(first, Project(name="Demo"))
    second = store_mod.unique_project_file(tmp_path, "Demo")
    assert second.parent.name == "Demo (2).rsproj"
    assert store_mod.list_projects(tmp_path) == [first]
