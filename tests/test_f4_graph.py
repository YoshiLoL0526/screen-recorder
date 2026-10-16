"""Tests puros de F4: efectos, builder del filter graph (golden strings) y corte rápido."""

from __future__ import annotations

import pytest

from recstudio.core import effects as fx
from recstudio.core import graph as G
from recstudio.core import keyframes as K
from recstudio.core import timeline as tl
from recstudio.core.errors import ProjectError
from recstudio.core.project import Clip, MediaAsset, Project, Track, Transition, from_dict, to_dict
from recstudio.services.export import EtaEstimator, partial_path, unique_output

A = MediaAsset("a", "a.mp4", 30.0, 1920, 1080, 60.0, True)
B = MediaAsset("b", "b.mp4", 20.0, 1920, 1080, 60.0, True)
MUTE = MediaAsset("m", "m.mp4", 20.0, 1280, 720, 30.0, False)


def golden_project() -> Project:
    """El ejemplo de docs/04: 2 clips, fundido de 0.5 s y el segundo al doble de velocidad."""
    return Project(
        assets=(A, B),
        video=Track("video", (Clip("c1", "a", 10.0, 25.0), Clip("c2", "b", 3.2, 12.0, speed=2.0)),
                    (Transition("fade", 0.5),)),
        fps=60,
    )


GOLDEN_FILTER = ";".join([
    "[0:v]setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=decrease,"
    "pad=1920:1080:-1:-1:color=black,fps=60,tpad=stop_mode=clone:stop=60,trim=end_frame=900,"
    "format=yuv420p,setsar=1[v0]",
    "[1:v]setpts=(PTS-STARTPTS)/2,scale=1920:1080:force_original_aspect_ratio=decrease,"
    "pad=1920:1080:-1:-1:color=black,fps=60,tpad=stop_mode=clone:stop=60,trim=end_frame=264,"
    "format=yuv420p,setsar=1[v1]",
    "[v0][v1]xfade=transition=fade:duration=0.5:offset=14.5[x1]",
    "[0:a]asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
    "afade=t=out:st=14.97:d=0.03,apad=whole_dur=15,atrim=duration=15[a0]",
    "[1:a]atrim=start=1,asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
    "atempo=2,afade=t=in:st=0:d=0.03,apad=whole_dur=3.9,atrim=duration=3.9[a1]",
    "[a0][a1]concat=n=2:v=0:a=1[amain]",
])


# -- builder ---------------------------------------------------------------------------


def test_golden_dos_clips_fundido_y_velocidad() -> None:
    spec = G.build(golden_project(), G.preset_by_id("rapido"), "out.mp4", encoder="h264_nvenc", exe="ffmpeg")
    assert spec.filter_complex == GOLDEN_FILTER
    assert spec.args == (
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-ss", "10", "-t", "15.1", "-i", "a.mp4",
        "-ss", "3.2", "-t", "8.9", "-i", "b.mp4",
        "-filter_complex", GOLDEN_FILTER, "-map", "[x1]", "-map", "[amain]",
        "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", "out.mp4",
    )
    # docs/04: 15 + 4.4 − 0.5 = 18.9 s, exactamente lo que dice el modelo
    assert spec.frames == 1134 and spec.duration == pytest.approx(18.9)
    assert spec.duration == pytest.approx(tl.total_duration(golden_project()))
    assert spec.has_audio and (spec.width, spec.height) == (1920, 1080)


def test_formula_de_offsets_con_tres_clips() -> None:
    # d = [5, 4, 3], t = [0.5, 0.4] → offsets 4.5 y 8.1, total 11.1 (docs/04)
    clips = (Clip("c1", "a", 0, 5), Clip("c2", "a", 5, 9), Clip("c3", "a", 9, 12))
    p = Project(assets=(A,), video=Track("video", clips, (Transition("fade", 0.5), Transition("dissolve", 0.4))),
                fps=30)
    spec = G.build(p, G.preset_by_id("calidad"), "o.mp4")
    assert "[v0][v1]xfade=transition=fade:duration=0.5:offset=4.5[x1]" in spec.filter_complex
    assert "[x1][v2]xfade=transition=dissolve:duration=0.4:offset=8.1[x2]" in spec.filter_complex
    assert spec.duration == pytest.approx(11.1)
    assert "-map" in spec.args and spec.args[spec.args.index("-map") + 1] == "[x2]"
    assert spec.args[spec.args.index("-c:v"):spec.args.index("-c:v") + 6] == (
        "-c:v", "libx264", "-preset", "medium", "-crf", "18")


def test_cortes_sin_transicion_van_en_un_solo_concat() -> None:
    clips = tuple(Clip(f"c{i}", "a", i * 2.0, i * 2.0 + 2) for i in range(4))
    p = Project(assets=(A,), video=Track("video", clips, (None, Transition("wipeleft", 0.5), None)), fps=60)
    fc = G.build(p, G.preset_by_id("calidad"), "o.mp4").filter_complex
    assert "[v0][v1]concat=n=2:v=1:a=0[c1]" in fc
    assert "[c1][v2]xfade=transition=wipeleft:duration=0.5:offset=3.5[x2]" in fc
    assert "[x2][v3]concat=n=2:v=1:a=0[c3]" in fc
    plain = Project(assets=(A,), video=Track("video", clips, (None, None, None)), fps=60)
    fc = G.build(plain, G.preset_by_id("calidad"), "o.mp4").filter_complex
    assert "[v0][v1][v2][v3]concat=n=4:v=1:a=0[c1]" in fc and "xfade" not in fc


def test_clip_sin_audio_genera_silencio_de_su_duracion() -> None:
    p = Project(assets=(A, MUTE), video=Track("video", (Clip("c1", "a", 0, 2), Clip("c2", "m", 0, 1.5)), (None,)),
                fps=30)
    fc = G.build(p, G.preset_by_id("calidad"), "o.mp4").filter_complex
    assert "anullsrc=r=48000:cl=stereo,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=duration=1.5[a1]" in fc
    assert "[a0][a1]concat=n=2:v=0:a=1[amain]" in fc


def test_proyecto_sin_audio_exporta_con_an() -> None:
    p = Project(assets=(MUTE,), video=Track("video", (Clip("c1", "m", 0, 3),)), fps=30)
    spec = G.build(p, G.preset_by_id("calidad"), "o.mp4")
    assert "-an" in spec.args and not spec.has_audio and "[0:a]" not in spec.filter_complex
    # silenciar el audio original también quita la pista
    q = Project(assets=(A,), video=Track("video", (Clip("c1", "a", 0, 3),), muted=True), fps=30)
    assert not G.build(q, G.preset_by_id("calidad"), "o.mp4").has_audio


def test_efectos_en_la_cadena_de_video_y_audio() -> None:
    clip = Clip("c1", "a", 0, 4)
    for kind, params in (("eq", {"brightness": 0.1, "saturation": 0.5}), ("zoom", {"factor": 1.5}),
                         ("fade_in", {"seconds": 0.5}), ("fade_out", {"seconds": 1}), ("volume", {"gain": 0.5})):
        clip = fx.with_effect(clip, kind, **params)
    p = Project(assets=(A,), video=Track("video", (clip,), (), gain=0.5), fps=30)
    fc = G.build(p, G.preset_by_id("calidad"), "o.mp4").filter_complex
    video, audio = fc.split(";")
    # orden: tiempo → crop (zoom) → eq → normalización → frames exactos → fundidos → formato
    assert video.index("setpts") < video.index("crop=w=iw/1.5:h=ih/1.5:x=(iw-ow)*0.5:y=(ih-oh)*0.5") \
        < video.index("eq=brightness=0.1:contrast=1:saturation=0.5") < video.index("scale=") \
        < video.index("trim=end_frame=120") < video.index("fade=t=in:st=0:d=0.5") \
        < video.index("fade=t=out:st=3:d=1") < video.index("format=yuv420p")
    assert "volume=0.25" in audio  # gain del clip × gain de la pista
    assert "afade=t=in:st=0:d=0.5" in audio and "afade=t=out:st=3:d=1" in audio


def test_preset_ligero_baja_a_720p() -> None:
    p = golden_project()
    spec = G.build(p, G.preset_by_id("ligero"), "o.mp4")
    assert (spec.width, spec.height) == (1280, 720)
    assert "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720" in spec.filter_complex
    assert G.output_size(Project(width=1366, height=768), G.preset_by_id("ligero")) == (1280, 720)
    assert G.output_size(Project(width=640, height=360), G.preset_by_id("ligero")) == (640, 360)


def test_pista_de_audio_extra_se_mezcla_al_largo_del_video() -> None:
    music = Track("audio", (Clip("m1", "b", 0, 5),), (), gain=0.5)
    p = Project(assets=(A, B), video=Track("video", (Clip("c1", "a", 0, 8),)), audio=(music,), fps=30)
    spec = G.build(p, G.preset_by_id("calidad"), "o.mp4")
    assert spec.args.count("-i") == 2
    assert "[1:a]asetpts=PTS-STARTPTS" in spec.filter_complex and "volume=0.5" in spec.filter_complex
    assert "[m0_0]apad=whole_dur=8,atrim=duration=8[m0]" in spec.filter_complex
    assert spec.filter_complex.endswith("[a0][m0]amix=inputs=2:normalize=0:duration=first[aout]")


def test_atempo_en_cadena_y_numeros() -> None:
    assert G.atempo_chain(1.0) == []
    assert G.atempo_chain(2.0) == ["atempo=2"]
    assert G.atempo_chain(4.0) == ["atempo=2", "atempo=2"]
    assert G.atempo_chain(0.25) == ["atempo=0.5", "atempo=0.5"]
    assert G.atempo_chain(3.0) == ["atempo=2", "atempo=1.5"]
    assert G.atempo_chain(0.75) == ["atempo=0.75"]
    assert [G.num(v) for v in (10, 3.2, 1 / 30, -0.0, 14.97)] == ["10", "3.2", "0.033333", "0", "14.97"]


def test_frames_enteros_y_transiciones_acotadas() -> None:
    clips = (Clip("c1", "a", 0, 1.01), Clip("c2", "a", 2, 2.5))
    p = Project(assets=(A,), video=Track("video", clips, (Transition("fade", 0.25),)), fps=30)
    frames, overlaps, total = G.timeline_frames(p)
    assert frames == [30, 15] and overlaps == [8] and total == 37  # 0.25 s × 30 = 7.5 → 8


def test_encoder_segun_preset_y_hardware() -> None:
    hw = {"h264_nvenc": False, "h264_amf": False, "h264_qsv": True, "libx264": True}
    assert G.resolve_encoder(G.preset_by_id("rapido"), hw) == "h264_qsv"
    assert G.resolve_encoder(G.preset_by_id("rapido"), {"libx264": True}) == "libx264"
    assert G.resolve_encoder(G.preset_by_id("calidad"), hw) == "libx264"
    with pytest.raises(ProjectError):
        G.preset_by_id("nope")


def test_build_rechaza_vacio_y_copia() -> None:
    with pytest.raises(ProjectError):
        G.build(Project(), G.preset_by_id("calidad"), "o.mp4")
    with pytest.raises(ProjectError):
        G.build(golden_project(), G.preset_by_id("copia"), "o.mp4")


# -- efectos ---------------------------------------------------------------------------


def test_efectos_se_fusionan_y_se_quitan_al_volver_a_neutro() -> None:
    clip = Clip("c1", "a", 0, 4)
    clip = fx.with_effect(clip, "eq", brightness=0.2)
    clip = fx.with_effect(clip, "eq", contrast=1.5)
    assert fx.eq(clip) == {"brightness": 0.2, "contrast": 1.5, "saturation": 1.0}
    assert len(clip.effects) == 1
    clip = fx.with_effect(clip, "eq", brightness=0, contrast=1)
    assert clip.effects == () and fx.eq(clip) is None
    clip = fx.with_effect(clip, "volume", gain=9)
    assert fx.gain(clip) == fx.GAIN_MAX
    with pytest.raises(ProjectError):
        fx.with_effect(clip, "speed", factor=2)


def test_fundidos_que_no_caben_se_reparten() -> None:
    clip = fx.with_effect(fx.with_effect(Clip("c1", "a", 0, 2), "fade_in", seconds=2), "fade_out", seconds=2)
    assert fx.fades(clip, 2.0) == (1.0, 1.0)
    assert fx.fade_gain(0.5, 2.0, 1.0, 1.0) == pytest.approx(0.5)
    assert fx.fade_gain(1.0, 2.0, 1.0, 1.0) == pytest.approx(1.0)
    assert fx.fade_gain(1.75, 2.0, 1.0, 1.0) == pytest.approx(0.25)


def test_set_effect_en_el_proyecto_y_serializacion() -> None:
    p = Project(assets=(A,), fps=30)
    p, clip = tl.append_clip(p, A)
    q = fx.set_effect(p, clip.id, "zoom", factor=2, x=0.25)
    assert fx.zoom(q.clip(clip.id)) == (2.0, 0.25, 0.5)
    assert from_dict(to_dict(q)) == q
    assert fx.clear_effects(q, clip.id) == p
    assert fx.set_effect(p, clip.id, "zoom", factor=1) is p  # neutro sin efecto previo: no cambia


def test_dividir_reparte_los_fundidos() -> None:
    p = Project(assets=(A,), fps=30)
    p, clip = tl.append_clip(p, A)
    p = fx.set_effect(p, clip.id, "fade_in", seconds=1)
    p = fx.set_effect(p, clip.id, "fade_out", seconds=1)
    p = fx.set_effect(p, clip.id, "volume", gain=0.5)
    left, right = tl.split(p, 10.0).video.clips
    assert fx.fade_in(left) == 1 and fx.fade_out(left) == 0 and fx.gain(left) == 0.5
    assert fx.fade_in(right) == 0 and fx.fade_out(right) == 1 and fx.gain(right) == 0.5


# -- corte rápido --------------------------------------------------------------------------


def test_bloqueos_del_corte_rapido() -> None:
    assert K.blockers(golden_project()) == [
        "tiene transiciones", "hay clips con velocidad distinta de 1×",
    ]
    plain = Project(assets=(A,), video=Track("video", (Clip("c1", "a", 0, 2), Clip("c2", "a", 5, 8)), (None,)))
    assert K.blockers(plain) == []
    fx_project = fx.set_effect(plain, "c1", "volume", gain=2)
    assert K.blockers(fx_project) == ["hay clips con efectos"]
    mixed = Project(assets=(A, MUTE), video=Track("video", (Clip("c1", "a", 0, 2), Clip("c2", "m", 0, 2)), (None,)))
    assert K.blockers(mixed) == ["mezcla videos con distinta resolución, fps o audio"]
    assert K.blockers(Project()) == ["el timeline está vacío"]


def test_plan_imanta_inicios_a_keyframes() -> None:
    plain = Project(assets=(A,), video=Track("video", (Clip("c1", "a", 0.9, 3), Clip("c2", "a", 5.4, 8)), (None,)))
    plan = K.plan(plain, {"a": (0.0, 2.0, 4.0, 6.0, 8.0)})
    assert [(s.start, s.end) for s in plan.segments] == [(0.0, 3.0), (6.0, 8.0)]
    assert plan.segments[0].shift == pytest.approx(-0.9)
    assert plan.duration == pytest.approx(5.0) and plan.max_shift == pytest.approx(0.9)
    # el keyframe más cercano no puede quedar fuera del clip
    assert K.snap_start(7.6, 7.9, (0.0, 4.0, 8.0)) == 4.0
    with pytest.raises(ProjectError):
        K.plan(golden_project(), {})


def test_comandos_del_corte_rapido(tmp_path) -> None:
    segment = K.CutSegment("c1", "in.mp4", 2.0, 5.5, 2.3)
    cmd = K.segment_cmd("ffmpeg", segment, "seg.mp4")
    assert cmd[cmd.index("-ss") + 1] == "2.001" and cmd[cmd.index("-t") + 1] == "3.5"
    assert ["-c", "copy"] == cmd[cmd.index("-c"):cmd.index("-c") + 2]
    text = K.concat_list([tmp_path / "a b.mp4", tmp_path / "it's.mp4"])
    assert text.count("file '") == 2 and r"it'\''s.mp4" in text
    concat = K.concat_cmd("ffmpeg", "list.txt", "out.mp4")
    assert concat[concat.index("-auto_convert") + 1] == "0"


# -- utilidades del servicio -------------------------------------------------------------------


def test_eta_con_media_movil() -> None:
    eta = EtaEstimator(total=100.0, window=3)
    assert eta.eta is None
    for wall, done in ((0, 0), (1, 10), (2, 20), (3, 40)):
        eta.add(wall, done)
    # ventana de 3 ticks: (1,10)→(3,40) ⇒ 15 s/s ⇒ quedan 60 s de salida → 4 s
    assert eta.speed == pytest.approx(15.0) and eta.eta == pytest.approx(4.0)


def test_rutas_de_salida(tmp_path) -> None:
    first = unique_output(tmp_path, 'Demo: "final"?')
    assert first.name == "Demo final.mp4"
    first.write_bytes(b"x")
    assert unique_output(tmp_path, 'Demo: "final"?').name == "Demo final (2).mp4"
    assert partial_path(tmp_path / "x.mp4").name == "x.part.mp4"
