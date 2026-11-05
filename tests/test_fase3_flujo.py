"""Fase 3 (docs/21): modelo v5 (marcadores e In/Out), portapapeles, mover en bloque, razor, Q/W,
loop y J/K/L del `Player`, export de rango (graph, corte rápido y FFmpeg real) y miniaturas de
videos rotados."""

from __future__ import annotations

import io
import json
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from recstudio.core import graph as G
from recstudio.core import keyframes as K
from recstudio.core import store as store_mod
from recstudio.core import timeline as tl
from recstudio.core.errors import ProjectError
from recstudio.core.project import (
    SCHEMA_VERSION,
    Clip,
    Effect,
    Marker,
    MediaAsset,
    Project,
    TextOverlay,
    Track,
    Transform,
    Transition,
    from_dict,
    to_dict,
)
from recstudio.core.store import ProjectStore
from recstudio.platform import ffmpeg as F
from recstudio.services import export as export_mod
from recstudio.services import media, playback

A = MediaAsset("a", "a.mp4", 30.0, 640, 360, 30.0, True)


def three_clips() -> Project:
    """c1 [0–4) · c2 [4–8) · c3 [8–12) del mismo asset, a 30 fps."""
    clips = (Clip("c1", "a", 0, 4), Clip("c2", "a", 10, 14), Clip("c3", "a", 20, 24))
    return Project(assets=(A,), video=Track("video", clips, (None, None)), fps=30, width=640, height=360)


def absolute(*clips: Clip, name: str = "Voz", **kwargs) -> Track:  # noqa: ANN003
    return Track("audio", clips, tuple(None for _ in range(max(0, len(clips) - 1))),
                 mode="absolute", name=name, **kwargs)


def ids_of(track: Track) -> list[str]:
    return [c.id for c in track.clips]


# -- modelo v5 y migración ----------------------------------------------------------


def test_esquema_v5_marcadores_e_in_out_con_round_trip() -> None:
    assert SCHEMA_VERSION >= 5  # v6 (docs/22) conserva todo lo de v5
    assert Project().markers == () and Project().range_in is None and Project().range_out is None
    project = replace(three_clips(), markers=(Marker("k1", 2.0, "Intro"), Marker("k2", 6.5, "Clímax")),
                      range_in=1.0, range_out=9.0)
    data = to_dict(project)
    assert data["markers"] == [{"id": "k1", "t": 2.0, "name": "Intro"}, {"id": "k2", "t": 6.5, "name": "Clímax"}]
    assert (data["range_in"], data["range_out"]) == (1.0, 9.0)
    assert from_dict(json.loads(json.dumps(data))) == project.validate()


def test_validacion_de_marcadores_e_in_out() -> None:
    with pytest.raises(ProjectError):
        Marker("k", -1.0)
    with pytest.raises(ProjectError):
        replace(three_clips(), range_in=5.0, range_out=5.0).validate()
    with pytest.raises(ProjectError):
        replace(three_clips(), markers=(Marker("k", 1.0), Marker("k", 2.0))).validate()
    replace(three_clips(), range_in=5.0).validate()  # un solo punto vale


def test_migracion_v4_v5_abre_identico_y_mas_nuevo_es_solo_lectura(tmp_path: Path) -> None:
    project = three_clips()
    v4 = to_dict(project)
    for key in ("markers", "range_in", "range_out"):
        v4.pop(key)
    v4["schema_version"] = 4
    migrated, read_only = store_mod.migrate(v4)
    assert not read_only and migrated["schema_version"] == SCHEMA_VERSION
    assert migrated["markers"] == [] and migrated["range_in"] is None
    assert from_dict(migrated) == project

    path = tmp_path / "Viejo.rsproj" / "project.json"
    path.parent.mkdir()
    path.write_text(json.dumps(v4), encoding="utf-8")
    result = store_mod.load_project(path)
    assert result.project == project and not result.read_only

    future = {**to_dict(project), "schema_version": SCHEMA_VERSION + 1}
    assert store_mod.migrate(future)[1] is True


# -- marcadores e In/Out -------------------------------------------------------------


def test_marcadores_anadir_renombrar_borrar_y_navegar() -> None:
    p, m1 = tl.add_marker(three_clips(), 2.01)
    assert m1.t == pytest.approx(2.0) and m1.name == "Marcador 1"
    with pytest.raises(ProjectError):
        tl.add_marker(p, 2.0)  # uno por fotograma
    p, m2 = tl.add_marker(p, 1.0, "Antes")
    assert [m.id for m in p.markers] == [m2.id, m1.id]  # ordenados por tiempo
    p = tl.rename_marker(p, m1.id, "Intro")
    assert p.marker(m1.id).name == "Intro"
    with pytest.raises(ProjectError):
        tl.rename_marker(p, m1.id, "  ")
    assert tl.adjacent_marker(p, 1.0, 1).id == m1.id
    assert tl.adjacent_marker(p, 2.0, -1).id == m2.id
    assert tl.adjacent_marker(p, 2.0, 1) is None
    assert tl.marker_at(p, 1.02, 0.05).id == m2.id
    p = tl.remove_marker(p, m2.id)
    assert [m.id for m in p.markers] == [m1.id]


def test_in_out_redondeo_orden_y_rango_efectivo() -> None:
    p = three_clips()
    assert tl.play_range(p) == (0.0, 12.0) and not tl.has_range(p)
    p = tl.set_range_in(p, 2.013)
    assert p.range_in == pytest.approx(2.0) and tl.play_range(p) == (pytest.approx(2.0), 12.0)
    p = tl.set_range_out(p, 7.0)
    assert tl.play_range(p) == (pytest.approx(2.0), pytest.approx(7.0))
    assert tl.set_range_in(p, 8.0).range_out is None  # entrada después de la salida: la salida se va
    assert tl.set_range_out(p, 1.0).range_in is None
    assert tl.set_range_out(p, 99.0).range_out == pytest.approx(12.0)  # acotado al timeline
    with pytest.raises(ProjectError):
        tl.set_range_out(p, 0.0)
    assert tl.clear_range(p).range_in is None and not tl.has_range(tl.clear_range(p))


def test_el_snap_engancha_a_marcadores_y_a_in_out() -> None:
    p, marker = tl.add_marker(three_clips(), 5.5)
    p = tl.set_range_in(p, 2.5)
    cands = tl.snap_candidates(p)
    assert marker.t in cands and 2.5 in cands
    assert tl.snap(5.53, cands, zoom=100) == pytest.approx(5.5)


# -- razor y Q/W ------------------------------------------------------------------------


def test_razor_por_pista_y_en_todas_las_pistas() -> None:
    p, _ = tl.detach_audio(three_clips(), "c1")  # «Audio del video» enlazado a c1
    music = Track("audio", (Clip("m1", "a", 0, 10),), (), mode="sequence", name="Música")
    p = replace(p, audio=(*p.audio, music))
    # Video: igual que S (video + su audio enlazado), la música no se toca.
    video_cut = tl.razor(p, 2.0, None)
    same = tl.split_all(p, 2.0)
    spans = lambda q: [(c.in_, c.out_) for t in (q.video, *q.audio) for c in t.clips]  # noqa: E731
    assert spans(video_cut) == spans(same)
    assert len(video_cut.audio[1].clips) == 1
    # Una pista de audio concreta: solo esa, y los trozos conservan el enlace.
    music_cut = tl.razor(p, 2.0, 1)
    assert len(music_cut.audio[1].clips) == 2 and len(music_cut.video.clips) == 3
    voice_cut = tl.razor(p, 2.0, 0)
    assert [c.link_id for c in voice_cut.audio[0].clips] == ["c1", "c1"]
    # En la regla: todas las pistas desbloqueadas de una vez.
    everything = tl.split_everything(p, 2.0)
    assert len(everything.video.clips) == 4
    assert len(everything.audio[0].clips) == 2 and len(everything.audio[1].clips) == 2
    locked = tl.set_audio_track(p, 1, locked=True)
    assert len(tl.split_everything(locked, 2.0).audio[1].clips) == 1
    with pytest.raises(ProjectError):
        tl.split_everything(p, 40.0)


def test_q_y_w_recortan_hasta_el_playhead_con_ripple_en_video() -> None:
    p = three_clips()
    q = tl.trim_to(p, 5.0, "start")  # c2 empieza en 4: pierde 1 s por la izquierda
    assert q.clip("c2").in_ == pytest.approx(11.0)
    assert tl.clip_starts(q.video)[2] == pytest.approx(7.0)  # ripple: c3 se desplaza
    w = tl.trim_to(p, 5.0, "end")
    assert w.clip("c2").out_ == pytest.approx(11.0) and tl.total_duration(w) == pytest.approx(9.0)
    with pytest.raises(ProjectError):
        tl.trim_to(p, 4.0, "start")  # en el borde: nada que recortar
    with pytest.raises(ProjectError):
        tl.trim_to(replace(p, video=replace(p.video, locked=True)), 5.0, "start")
    # Audio absoluto: Q deja fijo el borde derecho.
    voice = replace(p, audio=(absolute(Clip("v1", "a", 0, 4, start=2.0)),))
    trimmed = tl.trim_to(voice, 3.0, "start", 0)
    clip = trimmed.audio[0].clips[0]
    assert clip.start == pytest.approx(3.0) and clip.start + clip.duration == pytest.approx(6.0)


# -- portapapeles --------------------------------------------------------------------


def _decorated() -> Project:
    """c1 y c2 con transición entre ellos, transformación y efecto en c1 y un texto."""
    p = three_clips()
    p, _ = tl.set_transition(p, 0, "fade", 0.5)
    p = tl.set_transform(p, "c1", scale=1.5, rotation=90)
    clips = list(p.video.clips)
    clips[0] = replace(clips[0], effects=(Effect("volume", (("gain", 0.5),)),))
    return replace(p, video=replace(p.video, clips=tuple(clips)),
                   texts=(TextOverlay("t1", "Hola", 1.0, 2.0),))


def test_copiar_y_pegar_dos_clips_en_el_playhead_conserva_todo() -> None:
    p = _decorated()
    clipboard = tl.copy_items(p, {"c1", "c2"})
    assert [i.kind for i in clipboard.items] == ["video", "video"]
    pasted, new_ids = tl.paste(p, clipboard, 11.0)  # el hueco más cercano: el final
    assert len(new_ids) == 2 and not set(new_ids) & {"c1", "c2", "c3"}
    clips = pasted.video.clips
    assert ids_of(pasted.video)[:3] == ["c1", "c2", "c3"] and list(ids_of(pasted.video)[3:]) == list(new_ids)
    copy1, copy2 = clips[3], clips[4]
    assert copy1.transform == Transform(scale=1.5, rotation=90) and copy1.effects == clips[0].effects
    assert (copy1.in_, copy1.out_, copy2.in_, copy2.out_) == (0, 4, 10, 14)
    assert pasted.video.transitions[3] == Transition("fade", 0.5)  # entre los pegados
    assert pasted.video.transitions[2] is None  # con el vecino, corte
    assert tl.total_duration(pasted) == pytest.approx(tl.total_duration(p) + 7.5)
    pasted.validate()


def test_pegar_en_medio_inserta_en_el_hueco_mas_cercano() -> None:
    p = three_clips()
    clipboard = tl.copy_items(p, {"c3"})
    pasted, (new,) = tl.paste(p, clipboard, 4.4)  # cerca del corte c1|c2
    assert ids_of(pasted.video) == ["c1", new, "c2", "c3"]
    pasted, (new,) = tl.paste(p, clipboard, 7.0)  # pasado el centro de c2
    assert ids_of(pasted.video) == ["c1", "c2", new, "c3"]


def test_pegar_video_con_su_audio_separado_mantiene_el_enlace() -> None:
    p, audio_id = tl.detach_audio(three_clips(), "c2")
    clipboard = tl.copy_items(p, {"c2", audio_id})
    pasted, new_ids = tl.paste(p, clipboard, 12.0)
    new_video = next(c for c in pasted.video.clips if c.id in new_ids)
    new_audio = next(c for c in pasted.audio[0].clips if c.id in new_ids)
    assert new_video.audio_enabled is False and new_audio.link_id == new_video.id
    assert new_audio.start == pytest.approx(tl.clip_starts(pasted.video)[3])  # alineados
    pasted.validate()
    # Solo el video (sin su audio separado): la copia recupera su audio propio.
    alone, (vid,) = tl.paste(p, tl.copy_items(p, {"c2"}), 12.0)
    assert alone.clip(vid).audio_enabled is True
    # Solo el audio: copia independiente (sin enlace) en el playhead.
    free, (aid,) = tl.paste(p, tl.copy_items(p, {audio_id}), 20.0)
    found = tl.find_audio_clip(free, aid)
    assert found[2].link_id is None and found[2].start == pytest.approx(20.0)


def test_pegar_audio_que_se_solapa_falla_entero() -> None:
    p = replace(three_clips(), audio=(absolute(Clip("v1", "a", 0, 3, start=1.0), Clip("v2", "a", 5, 8, start=6.0)),))
    clipboard = tl.copy_items(p, {"v1"})
    with pytest.raises(ProjectError):
        tl.paste(p, clipboard, 5.0)
    ok, (new,) = tl.paste(p, clipboard, 9.0)
    assert tl.find_audio_clip(ok, new)[2].start == pytest.approx(9.0)
    locked = tl.set_audio_track(p, 0, locked=True)
    with pytest.raises(ProjectError):
        tl.paste(locked, clipboard, 9.0)


def test_duplicar_pone_la_copia_justo_detras_y_textos() -> None:
    p = _decorated()
    dup, (new,) = tl.duplicate(p, {"c2"})
    assert ids_of(dup.video) == ["c1", "c2", new, "c3"]
    text, (tid,) = tl.duplicate(p, {"t1"})
    copy = text.text(tid)
    assert (copy.start, copy.end, copy.text) == (2.0, 3.0, "Hola")
    with pytest.raises(ProjectError):
        tl.copy_items(p, {"no-existe"})


def test_cortar_y_borrar_varios_tipos_de_una_vez() -> None:
    p, audio_id = tl.detach_audio(three_clips(), "c3")
    p = replace(p, texts=(TextOverlay("t1", "Hola", 1.0, 2.0),))
    removed = tl.remove_items(p, {"c1", audio_id, "t1"})
    assert ids_of(removed.video) == ["c2", "c3"] and removed.audio[0].clips == () and removed.texts == ()


def test_cada_operacion_es_un_solo_undo() -> None:
    p = _decorated()
    store = ProjectStore(p)
    pasted, _ = tl.paste(p, tl.copy_items(p, {"c1", "c2", "t1"}), 12.0)
    assert store.apply("pegar", pasted)
    assert store.undo() == "pegar" and store.project == p
    assert not store.can_undo


# -- mover en bloque ----------------------------------------------------------------------


def test_mover_en_bloque_reordena_el_video_y_desplaza_su_audio() -> None:
    p, audio_id = tl.detach_audio(three_clips(), "c1")
    ids = {"c1", "c2", audio_id}
    moved = tl.move_block(p, ids, 4.2)  # el bloque (8 s) cae detrás de c3
    assert ids_of(moved.video) == ["c3", "c1", "c2"]
    audio = moved.audio[0].clips[0]
    assert audio.start == pytest.approx(tl.clip_starts(moved.video)[1])  # sigue a su video
    assert tl.move_block(p, ids, 0.1) == p  # mismo sitio: nada cambia
    with pytest.raises(ProjectError):
        tl.move_block(replace(p, video=replace(p.video, locked=True)), ids, 4.2)


def test_mover_en_bloque_conserva_transiciones_internas() -> None:
    p, _ = tl.set_transition(three_clips(), 0, "fade", 0.5)
    moved = tl.move_many(p, {"c1", "c2"}, 1)
    assert ids_of(moved.video) == ["c3", "c1", "c2"]
    assert moved.video.transitions == (None, Transition("fade", 0.5))


def test_mover_en_bloque_solo_audio_respeta_solapes() -> None:
    p = replace(three_clips(), audio=(absolute(
        Clip("v1", "a", 0, 1, start=0.0), Clip("v2", "a", 0, 1, start=2.0), Clip("v3", "a", 0, 1, start=6.0),
    ),))
    moved = tl.move_block(p, {"v1", "v2"}, 3.0)
    assert [c.start for c in moved.audio[0].clips] == [3.0, 5.0, 6.0]
    with pytest.raises(ProjectError):
        tl.move_block(p, {"v1", "v2"}, 4.5)  # v2 caería encima de v3
    assert tl.move_block(p, {"v1", "v2"}, -3.0).audio[0].clips[0].start == 0.0  # nunca antes de 0


# -- Player: loop y J/K/L --------------------------------------------------------------------


class _StubRenderer:
    """Renderer falso: el Player solo necesita `render_jpeg`, `pool` y `close`."""

    class _Pool:
        def forget_failures(self) -> None:
            pass

    def __init__(self) -> None:
        self.pool = self._Pool()
        self.last_transition = None

    def render_jpeg(self, project, t):  # noqa: ANN001, ANN201
        return b""

    def prefetch(self, project, index) -> None:  # noqa: ANN001
        pass

    def close(self) -> None:
        pass


def _player():  # noqa: ANN202
    times: list[float] = []
    states: list[bool] = []
    player = playback.Player(lambda _d: None, times.append, states.append, tick_hz=120,
                             renderer=_StubRenderer())
    player.set_project(three_clips())
    player.start()
    return player, times, states


def test_loop_repite_el_rango_in_out() -> None:
    player, times, _states = _player()
    try:
        player.set_loop((1.0, 1.3))
        player.play(5.0)  # fuera del rango: empieza en la entrada
        time.sleep(0.9)
        assert player.playing
        assert all(1.0 - 1e-6 <= t <= 1.3 + 0.05 for t in times)
        assert any(b < a for a, b in zip(times, times[1:])), "el loop debería volver al inicio"
    finally:
        player.stop()


def test_velocidad_y_marcha_atras_del_player() -> None:
    player, times, states = _player()
    try:
        player.play(1.0, rate=2.0)
        time.sleep(0.5)
        assert player.time == pytest.approx(2.0, abs=0.2)
        player.set_rate(-1.0)  # J: sin saltos, hacia atrás
        t0 = player.time
        time.sleep(0.3)
        assert player.time < t0
        player.set_rate(-8.0)
        assert _wait_for(lambda: not player.playing, 2.0), "hacia atrás se para al llegar a 0"
        assert times[-1] == 0.0 and states[-1] is False and player.rate == 1.0
    finally:
        player.stop()


def _wait_for(predicate, timeout: float) -> bool:  # noqa: ANN001
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# -- export de rango ---------------------------------------------------------------------------


def test_graph_con_rango_recorta_por_frames_sin_tocar_el_resto() -> None:
    p = three_clips()
    preset = G.preset_by_id("rapido")
    full = G.build(p, preset, "o.mp4")
    ranged = G.build(p, preset, "o.mp4", span=(1.0, 6.5))
    assert "trim=start_frame=30:end_frame=195,setpts=PTS-STARTPTS[vrange]" in ranged.filter_complex
    assert "atrim=start=1:end=6.5,asetpts=PTS-STARTPTS[arange]" in ranged.filter_complex
    assert ranged.frames == 165 and ranged.duration == pytest.approx(5.5)
    # c3 empieza en 8 s: no se ve en el rango y FFmpeg ni lo abre.
    assert ranged.args.count("-i") == full.args.count("-i") - 1
    assert full.filter_complex.split(";")[0] == ranged.filter_complex.split(";")[0]
    assert "-map" in ranged.args and "[vrange]" in ranged.args
    with pytest.raises(ProjectError):
        G.build(p, preset, "o.mp4", span=(3.0, 3.0))


def test_corte_rapido_con_rango_recorta_los_segmentos() -> None:
    p = three_clips()
    keyframes = {"a": (0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 20.0, 22.0)}
    plan = K.plan(p, keyframes, span=(1.6, 6.5))
    assert [s.clip_id for s in plan.segments] == ["c1", "c2"]
    first, second = plan.segments
    assert first.requested == pytest.approx(1.6) and first.start == pytest.approx(2.0)  # imantado
    assert second.requested == pytest.approx(10.0) and second.end == pytest.approx(12.5)
    with pytest.raises(ProjectError):
        K.plan(p, keyframes, span=(30.0, 31.0))


def _make_video(path: Path, *, seconds: float = 4.0, rotation: int | None = None) -> Path:
    exe = F.ffmpeg_exe()
    raw = path.with_name(path.stem + "-raw.mp4") if rotation else path
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc2=size=320x180:rate=30:duration={seconds}", "-f", "lavfi",
         "-i", f"sine=frequency=330:duration={seconds}", "-c:v", "libx264", "-preset", "ultrafast",
         "-g", "15", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(raw)],
        check=True, capture_output=True,
    )
    if rotation:
        subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-y", "-display_rotation", str(rotation),
             "-i", str(raw), "-c", "copy", str(path)],
            check=True, capture_output=True,
        )
    return path


@pytest.fixture(scope="module")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("fase3")
    try:
        _make_video(folder / "a.mp4", seconds=4.0)
        _make_video(folder / "rot.mp4", seconds=2.0, rotation=90)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")
    return folder


def test_export_real_de_un_rango_dura_lo_marcado(media_dir: Path, tmp_path: Path) -> None:
    asset = media.probe(media_dir / "a.mp4")
    p = Project(fps=30, width=asset.width, height=asset.height)
    p, _ = tl.append_clip(p, asset)
    p = tl.split(p, 2.0)
    p = tl.set_range_out(tl.set_range_in(p, 1.0), 2.5)
    for preset in ("rapido", "copia"):
        out = tmp_path / f"rango-{preset}.mp4"
        exporter = export_mod.Exporter()
        exporter.start(p, preset, out, span=tl.play_range(p))
        progress = exporter.wait(120)
        assert progress.state == "done", progress.message
        info = F.probe_media(out)
        assert info.has_audio
        if preset == "rapido":
            assert info.duration_s == pytest.approx(1.5, abs=0.07)
        else:  # el inicio se imanta a un keyframe (GOP de 15 ⇒ ≤ 0.5 s antes)
            assert 1.5 - 0.07 <= info.duration_s <= 2.0 + 0.07


# -- miniaturas de videos rotados -------------------------------------------------------


def test_filmstrip_y_poster_de_un_video_rotado_salen_derechos(media_dir: Path) -> None:
    from PIL import Image

    rotated = media_dir / "rot.mp4"
    assert media.probe(rotated).rotation in (90, 270)
    thumbs = media.thumbstrip(rotated)
    width, height = Image.open(io.BytesIO(thumbs[0])).size
    assert height == media.THUMB_H and width < height  # vertical, como se ve
    poster_w, poster_h = Image.open(io.BytesIO(media.poster(rotated, height=120))).size
    assert poster_h == 120 and poster_w < poster_h
    normal = media.thumbstrip(media_dir / "a.mp4")
    assert Image.open(io.BytesIO(normal[0])).size[0] > media.THUMB_H  # horizontal, intacto
    # La carpeta de cache es nueva: las miniaturas viejas (sin enderezar) no se reutilizan.
    assert media.THUMBS_DIR != "thumbs"


def test_upright_rgb_usa_la_convencion_del_preview() -> None:
    import numpy as np

    rgb = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    for rotation in (0, 90, 180, 270):
        assert np.array_equal(media.upright_rgb(rgb, rotation),
                              playback.rotate_rgb(rgb, rotation, clockwise=False))
