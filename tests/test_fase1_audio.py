"""Fase 1 (docs/18/docs/19): audio separable, cortes, edición, solo/mute/lock y export."""

from __future__ import annotations

import subprocess
import wave
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from recstudio.core import effects as fx
from recstudio.core import graph as G
from recstudio.core import keyframes as K
from recstudio.core import timeline as tl
from recstudio.core.errors import ProjectError
from recstudio.core.project import (
    Clip,
    MediaAsset,
    Project,
    Track,
    Transition,
    audio_solo_active,
    audio_track_audible,
    video_audio_gain,
)
from recstudio.core.store import ProjectStore
from recstudio.platform import ffmpeg as F
from recstudio.services import media, playback

A = MediaAsset("a", "a.mp4", 20.0, 640, 360, 30.0, True)
SIN_AUDIO = MediaAsset("s", "s.mp4", 20.0, 640, 360, 30.0, False)


def project_two_clips(asset: MediaAsset = A) -> Project:
    return Project(
        assets=(asset,),
        video=Track("video", (Clip("c1", asset.id, 0, 5), Clip("c2", asset.id, 5, 10)), (None,)),
        fps=30, width=640, height=360,
    )


def absolute_track(*clips: Clip, name: str = "Audio del video", **kwargs) -> Track:  # noqa: ANN003
    return Track("audio", clips, tuple(None for _ in range(max(0, len(clips) - 1))),
                 mode="absolute", name=name, **kwargs)


# -- separar / adjuntar -------------------------------------------------------------


def test_detach_audio_crea_pista_absoluta_enlazada_y_es_un_solo_undo() -> None:
    project = project_two_clips()
    store = ProjectStore(project)
    detached, audio_id = tl.detach_audio(project, "c1")

    assert detached.video.clips[0].audio_enabled is False
    assert detached.video.clips[1].audio_enabled is True
    track = detached.audio[0]
    assert track.mode == "absolute" and track.name == "Audio del video"
    audio = track.clips[0]
    assert audio.id == audio_id and audio.link_id == "c1"
    assert (audio.asset_id, audio.in_, audio.out_, audio.speed, audio.start) == ("a", 0.0, 5.0, 1.0, 0.0)
    detached.validate()

    assert store.apply("separar audio", detached)
    assert store.undo() == "separar audio" and store.project == project


def test_detach_audio_con_transicion_empieza_tras_el_solape() -> None:
    """El audio audible del clip entrante empieza después del solape (igual que el export)."""
    def with_transition() -> Project:
        return Project(
            assets=(A,),
            video=Track("video", (
                Clip("c1", "a", 0, 5), Clip("c2", "a", 5, 10),
            ), (Transition("fade", 0.5),)),
            fps=30,
        )

    for order in (("c1", "c2"), ("c2", "c1")):
        project = with_transition()
        for clip_id in order:
            project, _ = tl.detach_audio(project, clip_id)
        project.validate()
        audio = project.audio[0].clips
        assert [(c.start, c.in_, c.out_) for c in audio] == [
            (0.0, 0.0, 5.0),
            (5.0, 5.5, 10.0),
        ]
        assert [c.duration for c in audio] == pytest.approx([5.0, 4.5])
        assert [c.link_id for c in audio] == ["c1", "c2"]
        # no se solapan: el primero acaba justo cuando empieza el segundo
        assert audio[0].start + audio[0].duration == pytest.approx(audio[1].start)
        # el primer clip no cambia (inicio, entrada y salida)
        assert (audio[0].start, audio[0].in_, audio[0].out_) == (0.0, 0.0, 5.0)


def test_detach_reutiliza_la_pista_y_attach_deshace_el_enlace() -> None:
    project, _ = tl.detach_audio(project_two_clips(), "c1")
    project, _ = tl.detach_audio(project, "c2")
    assert len(project.audio) == 1 and len(project.audio[0].clips) == 2
    assert [c.start for c in project.audio[0].clips] == [0.0, 5.0]

    attached = tl.attach_audio(project, "c1")
    assert attached.video.clips[0].audio_enabled is True
    assert attached.video.clips[1].audio_enabled is False
    assert [c.link_id for c in attached.audio[0].clips] == ["c2"]
    attached.validate()
    with pytest.raises(ProjectError):
        tl.attach_audio(attached, "c1")


def test_detach_audio_con_asset_sin_audio_falla() -> None:
    with pytest.raises(ProjectError, match="no tiene audio"):
        tl.detach_audio(Project(
            assets=(SIN_AUDIO,),
            video=Track("video", (Clip("c1", SIN_AUDIO.id, 0, 5),)),
            fps=30,
        ), "c1")


def test_detach_copia_volumen_y_fundidos_del_clip() -> None:
    project = project_two_clips()
    project = fx.set_effect(project, "c1", "volume", gain=0.5)
    project = fx.set_effect(project, "c1", "fade_in", seconds=1.0)
    project = fx.set_effect(project, "c1", "zoom", factor=2.0)
    detached, _ = tl.detach_audio(project, "c1")
    audio = detached.audio[0].clips[0]
    assert fx.gain(audio) == pytest.approx(0.5)
    assert fx.fade_in(audio) == pytest.approx(1.0)
    assert fx.zoom(audio)[0] == pytest.approx(1.0)  # el zoom no viaja al audio


# -- dividir ------------------------------------------------------------------------


def test_split_all_corta_video_y_audio_enlazado_y_mantiene_el_enlace() -> None:
    project, _ = tl.detach_audio(project_two_clips(), "c1")
    result = tl.split_all(project, 2.5)

    assert len(result.video.clips) == 3
    left_video, right_video = result.video.clips[0], result.video.clips[1]
    assert left_video.id == "c1" and left_video.audio_enabled is False
    assert right_video.audio_enabled is False

    audio_clips = result.audio[0].clips
    assert [(c.start, c.link_id) for c in audio_clips] == [
        (0.0, "c1"), (2.5, right_video.id),
    ]
    assert all(c.audio_enabled for c in audio_clips)
    result.validate()
    assert tl.total_duration(result) == pytest.approx(10.0)


def test_split_audio_corta_solo_audio_y_conserva_el_enlace() -> None:
    project, _ = tl.detach_audio(project_two_clips(), "c1")
    result = tl.split_audio(project, 2.5)

    assert len(result.video.clips) == 2  # el video no se toca
    audio = result.audio[0].clips
    assert len(audio) == 2
    assert audio[0].link_id == "c1" and audio[1].link_id == "c1"
    assert audio[1].start == pytest.approx(2.5)
    result.validate()


def test_attach_tras_split_audio_borra_todos_los_trozos() -> None:
    project, _ = tl.detach_audio(project_two_clips(), "c1")
    project = tl.split_audio(project, 2.5)
    assert [c.link_id for c in project.audio[0].clips] == ["c1", "c1"]

    attached = tl.attach_audio(project, "c1")
    assert attached.audio[0].clips == ()
    assert attached.video.clips[0].audio_enabled is True
    attached.validate()


def test_split_track_secuencial_y_absoluta() -> None:
    music = Track("audio", (Clip("m1", "a", 0, 4), Clip("m2", "a", 4, 8)), (None,), name="Música")
    project = Project(assets=(A,), video=Track("video", (Clip("c1", "a", 0, 8),)), audio=(music,), fps=30)
    result = tl.split_track(project, music, 2.0)
    assert [c.id for c in result.audio[0].clips] == ["m1", result.audio[0].clips[1].id, "m2"]
    assert result.audio[0].clips[1].start is None
    assert tl.track_duration(result.audio[0]) == pytest.approx(8.0)

    absolute = absolute_track(Clip("x1", "a", 0, 3, start=1.0))
    project = replace(project, audio=(absolute,))
    result = tl.split_track(project, 0, 2.5)
    assert [c.start for c in result.audio[0].clips] == [1.0, 2.5]
    result.validate()


# -- mover / recortar / borrar -------------------------------------------------------


def test_mover_audio_absoluto_respeta_huecos_y_solapes() -> None:
    track = absolute_track(
        Clip("m1", "a", 0, 2, start=1.0),
        Clip("m2", "a", 0, 2, start=5.0),
    )
    project = Project(assets=(A,), video=Track("video", (Clip("c1", "a", 0, 8),)), audio=(track,), fps=30)

    moved = tl.move_audio(project, 0, "m1", start=2.5)
    assert moved.audio[0].clips[0].start == pytest.approx(2.5)
    assert tl.clip_starts(moved.audio[0]) == pytest.approx([2.5, 5.0])
    with pytest.raises(ProjectError, match="solaparía"):
        tl.move_audio(moved, 0, "m1", start=4.5)
    with pytest.raises(ProjectError, match="start"):
        tl.move_audio(project, 0, "m1")


def test_mover_audio_secuencial_reordena() -> None:
    music = Track("audio", (
        Clip("m1", "a", 0, 2), Clip("m2", "a", 2, 4), Clip("m3", "a", 4, 6),
    ), (None, None), name="Música")
    project = Project(assets=(A,), video=Track("video", (Clip("c1", "a", 0, 6),)), audio=(music,), fps=30)
    moved = tl.move_audio(project, 0, "m1", to_index=2)
    assert [c.id for c in moved.audio[0].clips] == ["m2", "m3", "m1"]
    assert tl.clip_starts(moved.audio[0]) == pytest.approx([0.0, 2.0, 4.0])


def test_trim_y_borrado_de_audio_en_ambos_modos() -> None:
    track = absolute_track(Clip("m1", "a", 0, 4, start=1.0))
    project = Project(assets=(A,), video=Track("video", (Clip("c1", "a", 0, 8),)), audio=(track,), fps=30)

    trimmed = tl.trim_audio(project, 0, "m1", 1.0, 3.5)
    clip = trimmed.audio[0].clips[0]
    assert (clip.in_, clip.out_, clip.start) == (1.0, 3.5, 1.0)
    assert clip.duration == pytest.approx(2.5)
    removed = tl.remove_audio(trimmed, 0, "m1")
    assert removed.audio[0].clips == ()

    music = Track("audio", (Clip("m1", "a", 0, 2), Clip("m2", "a", 2, 4)), (None,), name="Música")
    project = replace(project, audio=(music,))
    trimmed_music = tl.trim_audio(project, 0, "m1", 0.5, 2.0)
    assert trimmed_music.audio[0].clips[0].duration == pytest.approx(1.5)
    assert tl.clip_starts(trimmed_music.audio[0]) == pytest.approx([0.0, 1.5])
    removed = tl.remove_audio(project, 0, "m1")
    assert [c.id for c in removed.audio[0].clips] == ["m2"]
    assert tl.clip_starts(removed.audio[0]) == pytest.approx([0.0])


def test_trim_izquierdo_absoluto_mueve_start_y_fija_el_borde_derecho() -> None:
    track = absolute_track(Clip("m1", "a", 0, 5, start=5.0))
    project = Project(assets=(A,), video=Track("video", (Clip("c1", "a", 0, 10),)), audio=(track,), fps=30)

    trimmed = tl.trim_audio(project, 0, "m1", 1.0, 5.0)
    clip = trimmed.audio[0].clips[0]
    assert (clip.start, clip.in_, clip.out_) == (6.0, 1.0, 5.0)
    assert clip.duration == pytest.approx(4.0)
    assert clip.start + clip.duration == pytest.approx(10.0)  # borde derecho fijo

    # extender hacia la izquierda mueve el inicio hacia atrás y conserva el borde derecho
    extend = replace(project, audio=(absolute_track(Clip("m1", "a", 1, 5, start=5.0)),))
    extended = tl.trim_audio(extend, 0, "m1", 0.0, 5.0)
    clip = extended.audio[0].clips[0]
    assert (clip.start, clip.in_, clip.out_) == (4.0, 0.0, 5.0)
    assert clip.start + clip.duration == pytest.approx(9.0)

    # conflicto con el vecino: no cabe hacia la izquierda
    neighbour = replace(project, audio=(absolute_track(
        Clip("m0", "a", 0, 5, start=0.0),
        Clip("m1", "a", 1, 6, start=5.0),
    ),))
    with pytest.raises(ProjectError, match="solaparía"):
        tl.trim_audio(neighbour, 0, "m1", 0.0, 6.0)

    # no se puede dejar un inicio negativo
    at_zero = replace(project, audio=(absolute_track(Clip("m1", "a", 1, 5, start=0.0)),))
    with pytest.raises(ProjectError, match="negativo"):
        tl.trim_audio(at_zero, 0, "m1", 0.0, 5.0)


def test_trim_derecho_absoluto_no_mueve_start() -> None:
    track = absolute_track(Clip("m1", "a", 0, 5, start=5.0))
    project = Project(assets=(A,), video=Track("video", (Clip("c1", "a", 0, 10),)), audio=(track,), fps=30)
    trimmed = tl.trim_audio(project, 0, "m1", 0.0, 4.0)
    clip = trimmed.audio[0].clips[0]
    assert (clip.start, clip.in_, clip.out_) == (5.0, 0.0, 4.0)


def test_add_remove_pista_y_reactiva_el_video() -> None:
    project, _ = tl.detach_audio(project_two_clips(), "c1")
    project, index = tl.add_audio_track(project, "Voz")
    assert index == 1 and project.audio[1].mode == "absolute"
    project = tl.set_audio_track(project, 1, gain=0.5, solo=True, locked=True, muted=False)
    assert project.audio[1].solo and project.audio[1].locked and project.audio[1].gain == 0.5

    restored = tl.remove_audio_track(project, 0)
    assert len(restored.audio) == 1
    assert restored.video.clips[0].audio_enabled is True  # al quitar el audio separado vuelve el original


def test_solo_mute_y_lock_semantica() -> None:
    music = Track("audio", (Clip("m1", "a", 0, 5),), (), name="Música")
    voice = absolute_track(Clip("v1", "a", 0, 5, start=0.0), name="Voz", solo=True)
    project = Project(assets=(A,), video=Track("video", (Clip("c1", "a", 0, 5),)), audio=(music, voice), fps=30)
    assert audio_solo_active(project)
    assert not audio_track_audible(project, music)
    assert audio_track_audible(project, voice)
    assert video_audio_gain(project) == 0.0  # el solo aísla también el audio del video

    no_solo = replace(project, audio=(music, replace(voice, solo=False)))
    assert audio_track_audible(no_solo, music)
    assert video_audio_gain(no_solo) == 1.0
    muted = replace(no_solo, audio=(replace(music, muted=True), no_solo.audio[1]))
    assert not audio_track_audible(muted, muted.audio[0])

    locked = replace(project, audio=(replace(music, locked=True), voice))
    with pytest.raises(ProjectError, match="bloqueada"):
        tl.move_audio(locked, 0, "m1", to_index=0)
    with pytest.raises(ProjectError, match="bloqueada"):
        tl.trim_audio(locked, 0, "m1", 0.0, 3.0)
    with pytest.raises(ProjectError, match="bloqueada"):
        tl.remove_audio(locked, 0, "m1")


def test_borrar_un_video_borra_su_audio_enlazado() -> None:
    project, _ = tl.detach_audio(project_two_clips(), "c1")
    result = tl.ripple_delete(project, "c1")
    assert [c.id for c in result.video.clips] == ["c2"]
    assert result.audio[0].clips == ()
    result.validate()

    locked = replace(project, audio=(replace(project.audio[0], locked=True),))
    with pytest.raises(ProjectError, match="bloqueada"):
        tl.ripple_delete(locked, "c1")


# -- graph y preview -----------------------------------------------------------------


def test_graph_mezcla_audio_absoluto_con_adelay_y_silencia_el_video() -> None:
    project, audio_id = tl.detach_audio(project_two_clips(), "c1")
    project = tl.move_audio(project, 0, audio_id, start=0.5)
    spec = G.build(project, G.preset_by_id("calidad"), "o.mp4")
    fc = spec.filter_complex

    assert "anullsrc=r=48000:cl=stereo" in fc and "atrim=duration=5[a0]" in fc
    assert "adelay=delays=24000S:all=1" in fc
    assert "[amain][m0]amix=inputs=2:normalize=0:duration=first[aout]" in fc
    assert spec.args.count("-i") == 3  # dos clips de video + audio separado
    assert spec.has_audio


def test_build_audio_incluye_pistas_absolutas() -> None:
    project, audio_id = tl.detach_audio(project_two_clips(), "c1")
    project = tl.move_audio(project, 0, audio_id, start=1.0)
    args = G.build_audio(project, "audio.wav", exe="ffmpeg")
    text = " ".join(args)
    assert "adelay=delays=48000S:all=1" in text
    assert "amix=inputs=2:normalize=0" in text
    assert args[-1] == "audio.wav"


def test_video_sin_audio_no_genera_pista() -> None:
    project = Project(
        assets=(A,), video=Track("video", (Clip("c1", "a", 0, 3, audio_enabled=False),)), fps=30,
    )
    spec = G.build(project, G.preset_by_id("calidad"), "o.mp4")
    assert "-an" in spec.args and not spec.has_audio


def test_solo_excluye_las_demas_pistas_en_el_graph() -> None:
    music = Track("audio", (Clip("m1", "a", 0, 5),), (), name="Música")
    voice = absolute_track(Clip("v1", "a", 0, 5, start=0.5), name="Voz", solo=True)
    project = Project(
        assets=(A,), video=Track("video", (Clip("c1", "a", 0, 5),)), audio=(music, voice), fps=30,
    )
    fc = G.build(project, G.preset_by_id("calidad"), "o.mp4").filter_complex
    assert "m0" not in fc  # la música queda fuera
    assert "m1_0" in fc and "adelay=" in fc
    assert "anullsrc" in fc  # el audio del video también se silencia


def test_fundidos_de_audio_independientes_del_video() -> None:
    video = fx.with_effect(Clip("c1", "a", 0, 4), "fade_in", seconds=1.0)
    video = fx.with_effect(video, "fade_out", seconds=2.0)
    video = fx.with_effect(video, "audio_fade_in", seconds=0.25)
    video = fx.with_effect(video, "audio_fade_out", seconds=0.5)
    project = Project(assets=(A,), video=Track("video", (video,)), fps=30)
    fc = G.build(project, G.preset_by_id("calidad"), "o.mp4").filter_complex
    video_chain, audio_chain = fc.split(";")[:2]
    assert "fade=t=in:st=0:d=1" in video_chain and "fade=t=out:st=2:d=2" in video_chain
    assert "afade=t=in:st=0:d=0.25" in audio_chain and "afade=t=out:st=3.5:d=0.5" in audio_chain
    assert fx.audio_fade_in(video) == pytest.approx(0.25)
    assert fx.audio_fade_out(video) == pytest.approx(0.5)


def test_corte_rapido_se_bloquea_con_audio_separado_o_absoluto() -> None:
    project, _ = tl.detach_audio(project_two_clips(), "c1")
    reasons = K.blockers(project)
    assert "tiene audio separado del video" in reasons
    assert "tiene pistas de audio en posición absoluta" in reasons
    with pytest.raises(ProjectError):
        K.plan(project, {})


# -- integración real: mover el audio lo desplaza en el export -----------------------


def _video(path: Path, *, seconds: float = 2.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc2=size=320x180:rate=30:duration={seconds}", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-preset",
         "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


def _mp4_audio_rms(path: Path, start: float, end: float, rate: int = 48000) -> float:
    import av

    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=rate)
        chunks: list[np.ndarray] = []
        for frame in container.decode(stream):
            t0 = float(frame.pts * frame.time_base) if frame.pts is not None else 0.0
            for out in resampler.resample(frame):
                data = out.to_ndarray().reshape(-1).astype(np.float32)
                out_t0 = float(out.pts * out.time_base) if out.pts is not None else t0
                lo, hi = max(0, int((start - out_t0) * rate)), min(len(data), int((end - out_t0) * rate))
                if hi > lo:
                    chunks.append(data[lo:hi])
    return float(np.sqrt(np.mean(np.concatenate(chunks) ** 2))) if chunks else 0.0


def test_export_real_desplaza_el_pitido_y_silencia_el_video(tmp_path: Path) -> None:
    video = _video(tmp_path / "fuente.mp4", seconds=2.0)
    asset = media.probe(video)
    project = Project(
        assets=(asset,), video=Track("video", (Clip("c1", asset.id, 0, 2.0),)),
        fps=30, width=320, height=180,
    )
    project, audio_id = tl.detach_audio(project, "c1")
    project = tl.move_audio(project, 0, audio_id, start=0.5)

    out = tmp_path / "export.mp4"
    spec = G.build(project, G.preset_by_id("calidad"), out, encoder="libx264", exe=F.ffmpeg_exe())
    result = subprocess.run(spec.args, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]

    before = _mp4_audio_rms(out, 0.0, 0.3)
    after = _mp4_audio_rms(out, 1.0, 1.5)
    assert before < 0.01, f"el inicio debería ser silencio (RMS {before:.4f})"
    assert after > 0.03, f"el pitido movido no suena (RMS {after:.4f})"


def test_preview_audio_renderiza_pistas_absolutas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from recstudio.platform import paths

    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
    video = _video(tmp_path / "voz.mp4", seconds=2.0)
    asset = media.probe(video)
    project = Project(
        assets=(asset,), video=Track("video", (Clip("c1", asset.id, 0, 2.0),)),
        fps=30, width=320, height=180,
    )
    project, audio_id = tl.detach_audio(project, "c1")
    project = tl.move_audio(project, 0, audio_id, start=0.5)
    preview = playback.AudioPreview()
    target = preview._mix_file(project)
    preview._render_mix(project, target)

    assert target.is_file()
    with wave.open(str(target), "rb") as wav:
        assert wav.getframerate() == 48000
        assert wav.getnframes() / 48000 == pytest.approx(2.0, abs=0.05)
        wav.setpos(0)
        first = np.frombuffer(wav.readframes(int(0.25 * 48000)), dtype=np.int16)
        wav.setpos(int(1.0 * 48000))
        later = np.frombuffer(wav.readframes(int(0.4 * 48000)), dtype=np.int16)
    assert np.abs(first).max() < 100
    assert np.abs(later).max() > 1000
