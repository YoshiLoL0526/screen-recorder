"""Envolventes de volumen (docs/32): modelo, graph, preview, export real y UI."""

from __future__ import annotations

import subprocess
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from recstudio import state
from recstudio.core import audio_envelope as env
from recstudio.core import effects as fx
from recstudio.core import graph as G
from recstudio.core import store
from recstudio.core import timeline as tl
from recstudio.core.errors import ProjectError
from recstudio.core.project import (
    Clip,
    Effect,
    MediaAsset,
    Project,
    Track,
    Transition,
    from_dict,
    to_dict,
)
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import media


# -- modelo puro ----------------------------------------------------------------------


def test_value_at_interpola_y_acota_en_los_extremos() -> None:
    points = (env.VolumePoint(1.0, 1.0), env.VolumePoint(3.0, 0.2))
    assert env.value_at((), 0.4) == 1.0
    assert env.value_at(points, 0.0) == 1.0
    assert env.value_at(points, 2.0) == pytest.approx(0.6)
    assert env.value_at(points, 9.0) == pytest.approx(0.2)


def test_expr_de_ffmpeg_y_desplazamiento_por_solape() -> None:
    points = (env.VolumePoint(0.0, 1.0), env.VolumePoint(1.0, 0.25))
    assert env.expr(points) == (
        "if(lt(t,0),1,if(lt(t,1),(1+(0.25-1)*(t-0)/1),0.25))"
    )
    # El tramo audible de un clip entrante empieza 0.5 s después: las claves se desplazan.
    assert env.expr(points, start=-0.5) == (
        "if(lt(t,-0.5),1,if(lt(t,0.5),(1+(0.25-1)*(t+0.5)/1),0.25))"
    )
    assert env.expr(()) == "1"
    assert env.expr((env.VolumePoint(0.0, 0.5),)) == "0.5"


def test_set_point_ordena_actualiza_y_clear() -> None:
    points = env.set_point((), 1.0, 0.5)
    points = env.set_point(points, 0.0, 1.0)
    points = env.set_point(points, 1.0, 0.25)
    assert [(p.t, p.gain) for p in points] == [(0.0, 1.0), (1.0, 0.25)]
    assert env.clear(points) == ()


def test_slice_envelope_reancla_y_completa_los_extremos() -> None:
    points = (
        env.VolumePoint(0.0, 1.0),
        env.VolumePoint(1.0, 0.5),
        env.VolumePoint(2.0, 1.0),
    )
    assert [(p.t, p.gain) for p in env.slice_envelope(points, 0.5, 1.5)] == [
        (0.0, 0.75), (0.5, 0.5), (1.0, 0.75),
    ]
    assert [(p.t, p.gain) for p in env.slice_envelope(points, 1.0)] == [
        (0.0, 0.5), (1.0, 1.0),
    ]
    assert env.slice_envelope((), 0.5) == ()


def test_validacion_de_claves() -> None:
    with pytest.raises(ProjectError):
        env.VolumePoint(-0.1, 1.0)
    with pytest.raises(ProjectError):
        env.VolumePoint(0.0, env.GAIN_MAX + 0.1)
    with pytest.raises(ProjectError):
        env.validate((env.VolumePoint(1.0, 1.0), env.VolumePoint(1.0, 0.5)))
    with pytest.raises(ProjectError):
        env.validate((env.VolumePoint(2.0, 1.0), env.VolumePoint(1.0, 0.5)))
    env.validate((env.VolumePoint(0.0, 0.0), env.VolumePoint(2.0, env.GAIN_MAX)))


def _asset(has_audio: bool = True) -> MediaAsset:
    return MediaAsset("a", "fuente.mp4", 4.0, 320, 180, 30, has_audio)


def test_clip_serializa_la_envolvente_y_migra_v9_al_esquema_actual() -> None:
    points = (env.VolumePoint(0.0, 1.0), env.VolumePoint(1.5, 0.25))
    project = Project(
        assets=(_asset(),),
        video=Track("video", (Clip("c1", "a", 0.0, 4.0, envelope=points),)),
        fps=30,
    ).validate()
    data = to_dict(project)
    assert data["schema_version"] == 11
    assert data["video"]["clips"][0]["envelope"] == [
        {"t": 0.0, "gain": 1.0}, {"t": 1.5, "gain": 0.25},
    ]
    assert from_dict(data).video.clips[0].envelope == points

    migrated, read_only = store.migrate({"schema_version": 9, "video": {"clips": []}})
    assert read_only is False and migrated["schema_version"] == 11
    assert from_dict({
        "schema_version": 10,
        "assets": [{"id": "a", "path": "x.mp4", "duration": 2, "width": 320, "height": 180,
                    "fps": 30, "has_audio": True}],
        "video": {"clips": [{"id": "c", "asset_id": "a", "in_": 0, "out_": 2}]},
    }).video.clips[0].envelope == ()


def test_clip_rechaza_envolventes_invalidas() -> None:
    with pytest.raises(ProjectError):
        Clip("c", "a", 0, 2, envelope=(env.VolumePoint(1.0, 1.0), env.VolumePoint(0.5, 1.0)))


def test_split_y_detach_conservan_la_parte_de_la_curva() -> None:
    points = (
        env.VolumePoint(0.0, 1.0),
        env.VolumePoint(2.0, 0.5),
        env.VolumePoint(4.0, 1.0),
    )
    project = Project(
        assets=(_asset(),),
        video=Track("video", (Clip("c1", "a", 0.0, 4.0, envelope=points),)),
        fps=30,
    ).validate()
    split = tl.split(project, 2.0)
    left, right = split.video.clips
    assert [(p.t, p.gain) for p in left.envelope] == [(0.0, 1.0), (2.0, 0.5)]
    assert [(p.t, p.gain) for p in right.envelope] == [(0.0, 0.5), (2.0, 1.0)]

    detached, audio_id = tl.detach_audio(project, "c1")
    audio_clip = next(c for t in detached.audio for c in t.clips if c.id == audio_id)
    assert audio_clip.envelope == points


# -- graph y preview ------------------------------------------------------------------


def _project(envelope: tuple[env.VolumePoint, ...] = (), *, gain: float = 1.0) -> Project:
    effects = (Effect("volume", (("gain", gain),)),) if gain != 1.0 else ()
    return Project(
        assets=(_asset(),),
        video=Track("video", (Clip("c1", "a", 0.0, 2.0, effects=effects, envelope=envelope),)),
        fps=30, width=320, height=180,
    ).validate()


def _filter(project: Project) -> str:
    args = G.build_audio(project, "salida.wav", exe="ffmpeg")
    return args[args.index("-filter_complex") + 1]


def test_sin_envolvente_la_cadena_de_audio_es_la_de_siempre() -> None:
    chain = _filter(_project())
    assert "volume=volume=" not in chain
    assert chain == (
        "[0:a]asetpts=PTS-STARTPTS,aresample=48000,"
        "aformat=sample_fmts=fltp:channel_layouts=stereo,"
        "apad=whole_dur=2,atrim=duration=2[a0]"
    )


def test_envolvente_se_multiplica_por_el_volumen_estatico() -> None:
    points = (env.VolumePoint(0.0, 1.0), env.VolumePoint(1.0, 0.25))
    chain = _filter(_project(points, gain=0.5))
    assert "volume=0.5,volume=volume='if(lt(t,0),1," in chain
    assert ":eval=frame" in chain


def test_envolvente_de_un_clip_entrante_se_alinea_con_el_solape() -> None:
    project = Project(
        assets=(_asset(),),
        video=Track(
            "video",
            (
                Clip("c1", "a", 0.0, 2.0),
                Clip("c2", "a", 1.0, 3.0, envelope=(
                    env.VolumePoint(0.0, 1.0), env.VolumePoint(1.0, 0.25),
                )),
            ),
            (Transition("fade", 0.5),),
        ),
        fps=30, width=320, height=180,
    ).validate()
    chain = _filter(project)
    # El audio del clip 2 empieza 0.5 s después de su inicio: t local = solape + t del tramo.
    assert "volume=volume='if(lt(t,-0.5),1," in chain
    assert "(t+0.5)" in chain


def test_pista_absoluta_tambien_aplica_la_envolvente() -> None:
    project = Project(
        assets=(_asset(),),
        video=Track("video", (Clip("v", "a", 0.0, 2.0),)),
        audio=(Track(
            "audio", (Clip("a1", "a", 0.0, 1.0, start=0.5,
                           envelope=(env.VolumePoint(0.0, 0.5),)),), (), mode="absolute",
        ),),
        fps=30, width=320, height=180,
    ).validate()
    chain = _filter(project)
    assert "volume=volume='0.5':eval=frame" in chain
    assert G.has_audio_envelope(project)


def test_audio_preview_fuerza_la_mezcla_wav_con_envolvente(monkeypatch: pytest.MonkeyPatch) -> None:
    from recstudio.services import playback

    project = _project((env.VolumePoint(0.0, 1.0), env.VolumePoint(1.0, 0.2)))
    preview = playback.AudioPreview()
    seen: dict[str, bool] = {}

    def fake_start(project, t, *, mixed=False):  # noqa: ANN001
        seen["mixed"] = mixed

    monkeypatch.setattr(preview, "_start_locked", fake_start)
    preview.start(project, 0.0)
    assert seen == {"mixed": True}


# -- integración real: export y WAV del preview ---------------------------------------


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
                lo = max(0, int((start - out_t0) * rate))
                hi = min(len(data), int((end - out_t0) * rate))
                if hi > lo:
                    chunks.append(data[lo:hi])
    return float(np.sqrt(np.mean(np.concatenate(chunks) ** 2))) if chunks else 0.0


def _wav_audio_rms(path: Path, start: float, end: float) -> float:
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        wav.setpos(int(start * rate))
        raw = wav.readframes(int((end - start) * rate))
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(data ** 2))) if len(data) else 0.0


def test_export_real_alinea_la_envolvente_del_clip_entrante(tmp_path: Path) -> None:
    """Con transición, la curva del clip entrante empieza en su parte audible (t + skip)."""
    video = _video(tmp_path / "fuente.mp4", seconds=2.0)
    asset = media.probe(video)
    project = Project(
        assets=(asset,),
        video=Track(
            "video",
            (
                Clip("c1", asset.id, 0.0, 1.5),
                Clip("c2", asset.id, 0.5, 2.0, envelope=(
                    env.VolumePoint(0.0, 1.0),
                    env.VolumePoint(0.8, 1.0),
                    env.VolumePoint(0.9, 0.1),
                    env.VolumePoint(1.5, 0.1),
                )),
            ),
            (Transition("fade", 0.4),),
        ),
        fps=30, width=320, height=180,
    ).validate()
    out = tmp_path / "transicion.mp4"
    spec = G.build(project, G.preset_by_id("calidad"), out, encoder="libx264", exe=F.ffmpeg_exe())
    result = subprocess.run(spec.args, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    early = _mp4_audio_rms(out, 1.6, 1.85)
    late = _mp4_audio_rms(out, 2.1, 2.4)
    assert early > 0.03, f"el clip entrante debería sonar al empezar (RMS {early:.4f})"
    assert late < early * 0.3, f"la curva no se alineó con el solape (RMS {late:.4f})"


def test_export_real_baja_el_volumen_y_el_preview_usa_la_misma_mezcla(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from recstudio.services import playback

    video = _video(tmp_path / "fuente.mp4", seconds=2.0)
    asset = media.probe(video)
    points = (
        env.VolumePoint(0.0, 1.0),
        env.VolumePoint(1.4, 1.0),
        env.VolumePoint(1.5, 0.1),
        env.VolumePoint(2.0, 0.1),
    )
    project = Project(
        assets=(asset,),
        video=Track("video", (Clip("c1", asset.id, 0.0, 2.0, envelope=points),)),
        fps=30, width=320, height=180,
    ).validate()

    out = tmp_path / "export.mp4"
    spec = G.build(project, G.preset_by_id("calidad"), out, encoder="libx264", exe=F.ffmpeg_exe())
    result = subprocess.run(spec.args, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    early = _mp4_audio_rms(out, 0.2, 0.8)
    late = _mp4_audio_rms(out, 1.5, 1.9)
    assert early > 0.03, f"el inicio debería sonar (RMS {early:.4f})"
    assert late < early * 0.3, f"la envolvente no bajó el final (RMS {late:.4f} vs {early:.4f})"

    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
    preview = playback.AudioPreview()
    target = preview._mix_file(project)
    preview._render_mix(project, target)
    assert target.is_file()
    preview_early = _wav_audio_rms(target, 0.2, 0.8)
    preview_late = _wav_audio_rms(target, 1.5, 1.9)
    assert preview_early > 0.02
    assert preview_late < preview_early * 0.3
    assert preview_late / preview_early == pytest.approx(late / early, rel=0.35, abs=0.03)


# -- UI ---------------------------------------------------------------------------------


def _wait(predicate, timeout: float = 15.0) -> bool:  # noqa: ANN001
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _settle(pilot, frames: int = 4) -> None:
    for _ in range(frames):
        pilot.frame()
    pilot.settle()


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("envelope") / "v.mp4"
    try:
        subprocess.run(
            [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "testsrc2=size=640x360:rate=30:duration=6", "-f", "lavfi",
             "-i", "sine=frequency=440:duration=6", "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
            check=True, capture_output=True,
        )
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")
    return path


@pytest.fixture()
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    from recstudio.app import build_app

    app, controller = build_app(size=(1280, 800))
    state.out_dir.set(str(tmp_path / "videos"))
    pilot = app.pilot(size=(1280, 800))
    state.screen.set("editor")
    pilot.settle()
    try:
        yield app, controller, pilot
    finally:
        controller.shutdown()
        state.project_open.set(False)
        state.selection.set(None)
        state.multi_selection.set(frozenset())
        state.preview_area.set(state.PreviewArea())
        state.screen.set("record")


def _click_inspector(pilot, key: str) -> None:
    pilot.scroll("insp-scroll", dy=3000)
    pilot.settle()
    pilot.click(key)
    _settle(pilot)


def test_claves_de_volumen_desde_el_inspector(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = controller.editor
    editor.import_paths([video])
    assert _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))
    _settle(pilot)

    project = state.editor_project()
    clip_id = project.video.clips[0].id
    start = tl.clip_starts(project.video)[0]
    editor.select_clip(clip_id)
    editor.seek(start + 0.5)
    _settle(pilot)
    state.insp_volume.set(0.5)
    _settle(pilot)
    assert abs(fx.gain(state.editor_project().clip(clip_id)) - 0.5) < 1e-6

    _click_inspector(pilot, "insp-env-key")
    clip = state.editor_project().clip(clip_id)
    assert len(clip.envelope) == 1
    assert clip.envelope[0].t == pytest.approx(0.5, abs=0.02)
    assert clip.envelope[0].gain == pytest.approx(0.5)
    assert state.undo_label() == "añadir clave de volumen"
    from recstudio.ui.timeline import _clip_meta

    assert "◆" in _clip_meta(clip, offline=False)

    editor.seek(start + 1.0)
    _settle(pilot)
    state.insp_volume.set(1.0)
    _settle(pilot)
    _click_inspector(pilot, "insp-env-key")
    clip = state.editor_project().clip(clip_id)
    assert [p.t for p in clip.envelope] == pytest.approx([0.5, 1.0], abs=0.02)
    assert clip.envelope[-1].gain == pytest.approx(1.0)

    _click_inspector(pilot, "insp-env-clear")
    assert state.editor_project().clip(clip_id).envelope == ()

    # Clip de audio de la pista separada: los mismos botones funcionan en su inspector.
    project = state.editor_project()
    assert project.audio and project.audio[0].clips
    audio_start = tl.clip_starts(project.audio[0])[0]
    editor.select_audio(0, 0)
    editor.seek(audio_start + 0.4)
    _settle(pilot)
    _click_inspector(pilot, "insp-env-key")
    audio_clip = state.editor_project().audio[0].clips[0]
    assert len(audio_clip.envelope) == 1
    assert audio_clip.envelope[0].t == pytest.approx(0.4, abs=0.02)
