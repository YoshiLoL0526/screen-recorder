"""Feature B (docs/22/docs/24): pistas A/V separadas por defecto y audio enlazado (puro)."""

from __future__ import annotations

import pytest

from recstudio.core import graph as G
from recstudio.core import timeline as tl
from recstudio.core.errors import ProjectError
from recstudio.core.project import (
    Clip,
    MediaAsset,
    Project,
    Track,
    Transition,
    from_dict,
    to_dict,
)
from recstudio.core.store import ProjectStore

A = MediaAsset("a", "a.mp4", 20.0, 640, 360, 30.0, True)
SIN_AUDIO = MediaAsset("s", "s.mp4", 20.0, 640, 360, 30.0, False)


def _two_clips(transition: float | None = None) -> Project:
    transitions = (Transition("fade", transition),) if transition is not None else (None,)
    return Project(
        assets=(A,),
        video=Track("video", (Clip("c1", "a", 0, 5), Clip("c2", "a", 5, 10)), transitions),
        fps=30, width=640, height=360,
    )


def _linked(project: Project):
    return [
        (track.name, clip) for track in project.audio for clip in track.clips if clip.link_id
    ]


def _overlap(project: Project, index: int) -> float:
    if index <= 0:
        return 0.0
    transition = project.video.transitions[index - 1]
    return transition.duration if transition is not None else 0.0


def _assert_sync(project: Project) -> None:
    """Cada audio enlazado cae en el inicio de su video y, si es el único, usa su misma fuente."""
    starts = tl.clip_starts(project.video)
    counts: dict[str, int] = {}
    for _name, clip in _linked(project):
        counts[clip.link_id] = counts.get(clip.link_id, 0) + 1  # type: ignore[index]
    for _name, clip in _linked(project):
        video_index = project.video.index_of(clip.link_id)  # type: ignore[arg-type]
        video = project.video.clips[video_index]
        overlap = _overlap(project, video_index)
        assert clip.start == pytest.approx(starts[video_index] + overlap)
        if counts[clip.link_id] == 1:  # type: ignore[index]
            assert clip.in_ == pytest.approx(video.in_ + overlap * video.speed)
            assert clip.out_ == pytest.approx(video.out_)
            assert clip.speed == pytest.approx(video.speed)


# -- separar al importar -------------------------------------------------------------


def test_separate_audio_all_usa_una_sola_pista_y_conserva_posiciones() -> None:
    project = _two_clips(transition=0.5)
    separated = tl.separate_audio_all(project)
    separated.validate()

    assert all(not clip.audio_enabled for clip in separated.video.clips)
    assert len(separated.audio) == 1  # una sola pista aunque haya dos videos
    track = separated.audio[0]
    assert (track.mode, track.name) == ("absolute", "Audio del video")
    assert [(c.link_id, c.start, c.in_, c.out_, c.speed) for c in track.clips] == [
        ("c1", 0.0, 0.0, 5.0, 1.0),
        ("c2", 5.0, 5.5, 10.0, 1.0),  # después del solape de la transición
    ]
    assert [c.duration for c in track.clips] == pytest.approx([5.0, 4.5])
    assert separated.audio[0].clips[0].start + separated.audio[0].clips[0].duration == pytest.approx(5.0)
    _assert_sync(separated)
    assert tl.separate_audio_all(separated) == separated  # idempotente, no duplica


def test_separate_audio_all_crea_pista_solo_si_hay_audio() -> None:
    mudo = Project(
        assets=(SIN_AUDIO,),
        video=Track("video", (Clip("c1", SIN_AUDIO.id, 0, 5),)),
        fps=30,
    )
    assert tl.separate_audio_all(mudo) == mudo

    mixto = Project(
        assets=(A, SIN_AUDIO),
        video=Track("video", (
            Clip("c1", A.id, 0, 5), Clip("c2", SIN_AUDIO.id, 0, 5), Clip("c3", A.id, 5, 10),
        ), (None, None)),
        fps=30,
    )
    separated = tl.separate_audio_all(mixto)
    separated.validate()
    assert len(separated.audio) == 1
    assert [(c.link_id, c.start) for c in separated.audio[0].clips] == [("c1", 0.0), ("c3", 10.0)]
    assert separated.video.clips[1].audio_enabled is True  # el mudo no se toca


def test_importar_separado_es_un_solo_undo() -> None:
    project = _two_clips()
    store = ProjectStore(project)
    separated = tl.separate_audio_all(project)
    assert store.apply("importar", separated)
    assert store.undo() == "importar"
    assert store.project == project


# -- sincronización del enlace -------------------------------------------------------


def test_mover_video_recoloca_todo_el_audio_enlazado() -> None:
    before = tl.separate_audio_all(_two_clips(transition=0.5))
    moved = tl.move(before, "c1", 1)  # el clip 1 pasa al final
    synced = tl.sync_linked_audio(moved, before)
    synced.validate()

    assert [c.id for c in synced.video.clips] == ["c2", "c1"]
    assert tl.clip_starts(synced.video) == pytest.approx([0.0, 5.0])
    assert [(c.link_id, c.start) for c in synced.audio[0].clips] == [("c2", 0.0), ("c1", 5.0)]
    _assert_sync(synced)
    # la base no se muta y volver a sincronizar con el mismo `before` es idempotente
    assert before.video.clips[0].id == "c1"
    assert tl.sync_linked_audio(synced, before) == synced


def test_recortar_video_ajusta_in_out_del_unico_segmento_y_los_vecinos() -> None:
    before = tl.separate_audio_all(_two_clips(transition=0.5))

    # recorte por la derecha: el audio del clip acorta su salida y el vecino se adelanta
    trimmed = tl.sync_linked_audio(tl.trim(before, "c1", 0.0, 4.0), before)
    trimmed.validate()
    c1_audio = next(c for c in trimmed.audio[0].clips if c.link_id == "c1")
    c2_audio = next(c for c in trimmed.audio[0].clips if c.link_id == "c2")
    assert (c1_audio.start, c1_audio.in_, c1_audio.out_) == (0.0, 0.0, 4.0)
    assert (c2_audio.start, c2_audio.in_, c2_audio.out_) == (4.0, 5.5, 10.0)
    _assert_sync(trimmed)

    # recorte por la izquierda: cambia la entrada del audio, no su inicio
    left = tl.sync_linked_audio(tl.trim(before, "c1", 1.0, 5.0), before)
    left.validate()
    c1_audio = next(c for c in left.audio[0].clips if c.link_id == "c1")
    assert (c1_audio.start, c1_audio.in_, c1_audio.out_) == (0.0, 1.0, 5.0)
    _assert_sync(left)


def test_cambiar_velocidad_del_video_cambia_la_del_audio() -> None:
    before = tl.separate_audio_all(_two_clips(transition=0.5))
    fast = tl.sync_linked_audio(tl.set_speed(before, "c1", 2.0), before)
    fast.validate()

    audio = next(c for c in fast.audio[0].clips if c.link_id == "c1")
    assert (audio.speed, audio.in_, audio.out_) == (2.0, 0.0, 5.0)
    assert audio.duration == pytest.approx(2.5)
    assert tl.clip_starts(fast.video) == pytest.approx([0.0, 2.0])
    assert next(c for c in fast.audio[0].clips if c.link_id == "c2").start == pytest.approx(2.5)
    _assert_sync(fast)


def test_split_all_mantiene_los_enlaces_del_audio_separado() -> None:
    project = tl.separate_audio_all(_two_clips())
    split = tl.split_all(project, 2.5)
    split.validate()

    assert len(split.video.clips) == 3
    right_video = split.video.clips[1]
    audio = split.audio[0].clips
    assert [(c.start, c.link_id) for c in audio] == [
        (0.0, "c1"), (2.5, right_video.id), (5.0, "c2"),
    ]
    assert all(not c.audio_enabled for c in split.video.clips)
    _assert_sync(split)


def test_solape_del_audio_enlazado_cancela_el_movimiento() -> None:
    """Un audio dividido (varios segmentos) puede no caber al reordenar: se rechaza entero."""
    audio = Track(
        "audio",
        (
            Clip("a1", "a", 0, 2, start=0.0, link_id="c1"),
            Clip("a2a", "a", 2, 4, start=2.0, link_id="c2"),
            Clip("a2b", "a", 4, 6, start=4.0, link_id="c2"),
            Clip("libre", "a", 0, 2, start=6.0),
        ),
        (None, None, None),
        mode="absolute",
        name="Audio del video",
    )
    project = Project(
        assets=(A,),
        video=Track("video", (
            Clip("c1", "a", 0, 2, audio_enabled=False),
            Clip("c2", "a", 2, 4, audio_enabled=False),
        ), (None,)),
        audio=(audio,),
        fps=30,
    )
    project.validate()

    moved = tl.move(project, "c2", 0)  # c1 pasa al final: su audio choca con el 2.º de c2
    with pytest.raises(ProjectError, match="solaparía"):
        tl.sync_linked_audio(moved, project)


# -- desvincular y persistir ---------------------------------------------------------


def test_unlink_audio_conserva_el_audio_y_no_reactiva_el_video() -> None:
    project = tl.separate_audio_all(_two_clips())
    audio_ids = [c.id for c in project.audio[0].clips]

    unlinked = tl.unlink_audio(project, "c1")
    unlinked.validate()
    assert [c.id for c in unlinked.audio[0].clips] == audio_ids  # no se borra nada
    assert [c.link_id for c in unlinked.audio[0].clips] == [None, "c2"]
    assert unlinked.video.clips[0].audio_enabled is False  # no se reactiva el sonido original
    assert tl.unlink_audio(unlinked, "c1") == unlinked  # idempotente

    # quitando también el otro enlace, los dos quedan libres
    free = tl.unlink_audio(unlinked, "c2")
    assert all(c.link_id is None for c in free.audio[0].clips)
    free.validate()


def test_relink_audio_conserva_el_audio_editado_y_no_duplica() -> None:
    project = tl.separate_audio_all(_two_clips())
    audio_id = project.audio[0].clips[0].id
    free = tl.unlink_audio(project, "c1")
    # el usuario recorta y mueve el audio libre antes de volver a enlazarlo
    free = tl.trim_audio(free, 0, audio_id, 0.0, 4.5)
    free = tl.move_audio(free, 0, audio_id, start=0.25)

    relinked = tl.relink_audio(free, audio_id)
    relinked.validate()
    clip = next(c for c in relinked.audio[0].clips if c.id == audio_id)
    assert clip.link_id == "c1" and clip.start == pytest.approx(round(tl.frame_round(0.25, 30), 6))
    assert relinked.video.clips[0].audio_enabled is False
    assert len(relinked.audio[0].clips) == 2  # ni se borra ni se duplica
    assert tl.relink_audio(relinked, audio_id) == relinked  # ya estaba enlazado


def test_round_trip_de_pista_enlazada() -> None:
    project = tl.separate_audio_all(_two_clips(transition=0.5))
    assert from_dict(to_dict(project)) == project
    assert from_dict(to_dict(project)).validate() == project


def test_graph_igual_que_separar_a_mano_y_sin_duplicar_audio() -> None:
    project = _two_clips(transition=0.5)
    automatic = tl.separate_audio_all(project)
    manual, _ = tl.detach_audio(project, "c1")
    manual, _ = tl.detach_audio(manual, "c2")

    auto_spec = G.build(automatic, G.preset_by_id("calidad"), "o.mp4")
    manual_spec = G.build(manual, G.preset_by_id("calidad"), "o.mp4")
    assert auto_spec.filter_complex == manual_spec.filter_complex
    assert auto_spec.args.count("-i") == 4  # 2 videos + 2 audios separados (sin duplicar)
    assert "adelay=delays=240000S:all=1" in auto_spec.filter_complex  # c2 empieza en 5 s
    assert "[amain][m0]amix=inputs=2:normalize=0:duration=first[aout]" in auto_spec.filter_complex
