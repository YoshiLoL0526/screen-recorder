"""Tests del bloque B: música/ducking, textos, selección múltiple, snap, proxy y reubicar."""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import pytest

from recstudio.core import graph as G
from recstudio.core import keyframes as K
from recstudio.core import timeline as tl
from recstudio.core.errors import ProjectError
from recstudio.core.project import (
    Clip,
    MediaAsset,
    Project,
    TextOverlay,
    Track,
    Transition,
    from_dict,
    to_dict,
)
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import media, playback

A = MediaAsset("a", "a.mp4", 20.0, 1920, 1080, 30.0, True)
M = MediaAsset("m", "m.wav", 20.0, 0, 0, 0.0, True)


def _video(path: Path, *, seconds: float = 2.0, size: str = "640x360", audio: bool = True) -> Path:
    command = [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}"]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:a", "aac", "-shortest"]
    command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(command, check=True, capture_output=True)
    return path


def _audio(path: Path, *, seconds: float = 2.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=330:duration={seconds}", "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True,
    )
    return path


# -- modelo y textos ------------------------------------------------------------------


def test_textos_y_ducking_sobreviven_al_json() -> None:
    project = Project(
        assets=(A, M),
        video=Track("video", (Clip("c1", "a", 0, 5),)),
        audio=(Track("audio", (Clip("m1", "m", 0, 5),), (), gain=0.5, ducking=0.7),),
        texts=(TextOverlay("t1", "Hola", 0.5, 2.5, x=0.25, y=0.1, size=56, color="#F5B851", bold=True),),
        fps=30,
    )
    restored = from_dict(to_dict(project))

    assert restored == project
    assert restored.texts[0].bold and restored.audio[0].ducking == pytest.approx(0.7)


def test_texto_invalido_falla() -> None:
    with pytest.raises(ProjectError):
        TextOverlay("t", "  ", 0, 1)
    with pytest.raises(ProjectError):
        TextOverlay("t", "Hola", 2, 1)
    with pytest.raises(ProjectError):
        TextOverlay("t", "Hola", 0, 1, x=1.2)
    with pytest.raises(ProjectError):
        TextOverlay("t", "Hola", 0, 1, color="rojo")


def test_add_update_y_remove_texto() -> None:
    project = Project(assets=(A,), video=Track("video", (Clip("c1", "a", 0, 5),)), fps=30)

    project, overlay = tl.add_text(project, "Título", 1.0, 2.0)
    assert (overlay.start, overlay.end) == (1.0, 3.0)
    project = tl.update_text(project, overlay.id, text="Nuevo", size=72)
    assert project.text(overlay.id).text == "Nuevo"
    project = tl.remove_text(project, overlay.id)
    assert project.texts == ()


def test_borrado_multiple_es_una_sola_operacion() -> None:
    project = Project(
        assets=(A,),
        video=Track("video", tuple(Clip(f"c{i}", "a", i, i + 1) for i in range(4)), (None, None, None)),
        fps=30,
    )
    result = tl.ripple_delete_many(project, {"c0", "c2"})

    assert [c.id for c in result.video.clips] == ["c1", "c3"]
    assert len(result.video.transitions) == 1


def test_snap_de_audio_mapea_los_picos_de_los_vecinos() -> None:
    project = Project(
        assets=(A,),
        video=Track("video", (Clip("c1", "a", 0, 10), Clip("c2", "a", 0, 10)), (None,)),
        fps=30,
    )
    candidates = tl.audio_snap_candidates(project, "c1", {"a": [1.0, 2.0]})

    assert candidates == [11.0, 12.0]


def test_waveform_peaks_elige_maximos_locales() -> None:
    import numpy as np

    peaks = np.zeros((1000, 2), np.float32)
    peaks[100] = (0.8, 0.9)
    peaks[500] = (-0.7, -0.6)
    assert media.waveform_peaks(peaks, threshold=0.5) == [1.0, 5.0]
    assert media.waveform_peaks(np.zeros((10, 2), np.float32), threshold=0.5) == []


# -- probe de audio y proxy -----------------------------------------------------------


def test_probe_acepta_un_audio_sin_video(tmp_path: Path) -> None:
    path = _audio(tmp_path / "musica.wav", seconds=1.2)
    asset = media.probe(path)

    assert asset.has_audio and asset.width == 0 and asset.height == 0
    assert asset.duration == pytest.approx(1.2, abs=0.01)


def test_proxy_540p_se_cachea_y_es_lo_que_usa_el_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
    video = _video(tmp_path / "pesado.mp4", size="1280x720", seconds=1.0)
    asset = media.probe(video)

    assert not media.is_heavy(asset)
    assert media.is_heavy(MediaAsset("x", "x.mp4", 1, 3840, 2160, 60, True))
    proxy = media.proxy(video, height=540)
    assert proxy.is_file() and media.cached_proxy(video) == proxy
    assert media.preview_path(asset) == str(proxy)

    import av

    with av.open(str(proxy)) as container:
        stream = container.streams.video[0]
        assert stream.codec_context.height == 540
        assert stream.codec_context.width == 960


# -- graph ----------------------------------------------------------------------------


def _music_project() -> Project:
    return Project(
        assets=(A, M),
        video=Track("video", (Clip("c1", "a", 0, 8),)),
        audio=(Track("audio", (Clip("m1", "m", 0, 4), Clip("m2", "m", 4, 8)),
                      (Transition("fade", 0.5),), gain=0.5, ducking=0.5),),
        texts=(TextOverlay("t1", "Hola: 100% 'mundo'", 1.0, 2.5, size=42),),
        fps=30,
    )


def test_graph_mezcla_musica_con_crossfade_y_ducking() -> None:
    spec = G.build(_music_project(), G.preset_by_id("calidad"), "o.mp4")
    fc = spec.filter_complex

    assert "acrossfade=d=0.5:c1=tri:c2=tri" in fc
    assert "sidechaincompress=threshold=0.05:ratio=7:attack=20:release=350" in fc
    assert "amix=inputs=2:normalize=0:duration=first[aout]" in fc
    assert "drawtext=" in fc and "expansion=none" in fc
    assert "enable='between(t,1,2.5)'" in fc
    assert "Hola\\: 100% \\'mundo\\'" in fc


def test_build_audio_genera_un_wav_pcm() -> None:
    args = G.build_audio(_music_project(), "audio.wav", exe="ffmpeg")
    assert "-map" in args and "[aout]" in args
    assert args[args.index("-c:a") + 1] == "pcm_s16le"
    assert args[-1] == "audio.wav"


def test_el_corte_rapido_no_admite_textos() -> None:
    project = _music_project()
    assert any("textos" in reason for reason in K.blockers(project))


# -- integración con FFmpeg: export con música, ducking y texto -----------------------


def test_el_preview_audio_renderiza_la_mezcla_a_wav(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
    video = _video(tmp_path / "voz.mp4", seconds=2.0, size="640x360")
    music = _audio(tmp_path / "musica.wav", seconds=2.0)
    voice, track_asset = media.probe(video), media.probe(music)
    project = Project(
        assets=(voice, track_asset),
        video=Track("video", (Clip("c1", voice.id, 0, 2.0),)),
        audio=(Track("audio", (Clip("m1", track_asset.id, 0, 2.0),), (), gain=0.6, ducking=0.5),),
        fps=30, width=640, height=360,
    )
    preview = playback.AudioPreview()
    target = preview._mix_file(project)
    preview._render_mix(project, target)

    assert target.is_file()
    with wave.open(str(target), "rb") as wav:
        assert wav.getnchannels() == 2
        assert wav.getframerate() == 48000
        assert wav.getnframes() / 48000 == pytest.approx(2.0, abs=0.05)


def test_el_preview_dibuja_los_textos(tmp_path: Path) -> None:
    import numpy as np

    video = _video(tmp_path / "v.mp4", seconds=1.0, size="640x360")
    asset = media.probe(video)
    base = Project(
        assets=(asset,), video=Track("video", (Clip("c1", asset.id, 0, 1.0),)), fps=30,
        width=640, height=360,
    )
    with_text = Project(
        assets=base.assets, video=base.video, fps=30, width=640, height=360,
        texts=(TextOverlay("t1", "HOLA", 0.0, 1.0, x=0.5, y=0.5, size=64, color="#FFFFFF"),),
    )
    renderer = playback.FrameRenderer(max_height=360)
    try:
        plain = renderer.render(base, 0.5)
        drawn = renderer.render(with_text, 0.5)
    finally:
        renderer.close()

    assert plain is not None and drawn is not None
    assert not np.array_equal(plain, drawn), "el texto debe cambiar el frame del preview"


def test_export_real_con_musica_texto_y_ducking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
    video = _video(tmp_path / "voz.mp4", seconds=2.0, size="640x360")
    music = _audio(tmp_path / "musica.wav", seconds=4.0)
    voice = media.probe(video)
    track_asset = media.probe(music)
    project = Project(
        assets=(voice, track_asset),
        video=Track("video", (Clip("c1", voice.id, 0, 2.0),)),
        audio=(Track("audio", (Clip("m1", track_asset.id, 0, 2.0),), (), gain=0.6, ducking=0.8),),
        texts=(TextOverlay("t1", "Rec Studio: B1+B2", 0.2, 1.8, size=42),),
        fps=30, width=640, height=360,
    )
    out = tmp_path / "export.mp4"
    spec = G.build(project, G.preset_by_id("calidad"), out, encoder="libx264")
    result = subprocess.run(spec.args, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=120)

    assert result.returncode == 0, result.stderr[-2000:]
    assert out.is_file() and out.stat().st_size > 0
    import av

    with av.open(str(out)) as container:
        assert container.streams.video and container.streams.audio
        assert float(container.duration) / 1_000_000 == pytest.approx(2.0, abs=0.1)
