"""Envolventes de volumen del audio de elementos (docs/34): modelo, graph, preview, export y UI."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from recstudio import state
from recstudio.core import audio_envelope as env
from recstudio.core import effects as fx
from recstudio.core import elements as element_mod
from recstudio.core import graph as G
from recstudio.core import store
from recstudio.core import timeline as tl
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
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import media


# -- modelo puro ----------------------------------------------------------------------


def _asset(has_audio: bool = True) -> MediaAsset:
    return MediaAsset("a", "fuente.mp4", 4.0, 320, 180, 30, has_audio)


def _points() -> tuple[env.VolumePoint, ...]:
    return (env.VolumePoint(0.0, 1.0), env.VolumePoint(0.75, 0.25))


def _project(
    envelope: tuple[env.VolumePoint, ...] = (), *, gain: float = 1.0, track: int = 0,
) -> Project:
    effects = (Effect("volume", (("gain", gain),)),) if gain != 1.0 else ()
    return Project(
        assets=(_asset(),),
        video=Track("video", (Clip("c1", "a", 0.0, 2.0),)),
        elements=(
            Element(
                "e1", "video", 0.5, 1.5, asset_id="a", video_track=track,
                effects=effects, envelope=envelope,
            ),
        ),
        fps=30, width=320, height=180,
    ).validate()


def test_element_serializa_la_envolvente_y_migra_v10_a_v11() -> None:
    points = _points()
    project = _project(points)
    data = to_dict(project)
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["elements"][0]["envelope"] == [
        {"t": 0.0, "gain": 1.0}, {"t": 0.75, "gain": 0.25},
    ]
    restored = from_dict(data)
    assert restored.elements[0].envelope == points

    # Un proyecto v10 no tiene `envelope` en los elementos: se abre sin curva y suena igual.
    old = to_dict(project)
    old["schema_version"] = 10
    for element in old["elements"]:
        element.pop("envelope")
    migrated, read_only = store.migrate(old)
    assert read_only is False and migrated["schema_version"] == SCHEMA_VERSION
    assert from_dict(migrated).elements[0].envelope == ()


def test_element_valida_la_envolvente_y_solo_en_videos() -> None:
    with pytest.raises(ProjectError):
        Element(
            "e2", "video", 0.0, 1.0, asset_id="a",
            envelope=(env.VolumePoint(1.0, 1.0), env.VolumePoint(0.5, 1.0)),
        )
    with pytest.raises(ProjectError):
        Element(
            "e3", "rect", 0.0, 1.0,
            envelope=(env.VolumePoint(0.0, 1.0),),
        )
    with pytest.raises(ProjectError):
        env.VolumePoint(0.0, env.GAIN_MAX + 0.1)

    project = _project()
    updated = element_mod.update_element(project, "e1", envelope=_points())
    assert updated.element("e1").envelope == _points()
    with pytest.raises(ProjectError):
        element_mod.update_element(project, "e1", envelope="no")


# -- graph y preview ------------------------------------------------------------------


def _element_chain(project: Project, label: str = "pip0") -> str:
    spec = G.build(project, G.preset_by_id("calidad"), "salida.mp4", exe="ffmpeg")
    return next(
        chain for chain in spec.filter_complex.split(";") if chain.endswith(f"[{label}]")
    )


def test_sin_envolvente_la_cadena_del_elemento_es_la_de_siempre() -> None:
    chain = _element_chain(_project())
    assert "volume=volume=" not in chain
    assert chain == (
        "[1:a]asetpts=PTS-STARTPTS,aresample=48000,"
        "aformat=sample_fmts=fltp:channel_layouts=stereo,"
        "afade=t=in:st=0:d=0.03,afade=t=out:st=0.97:d=0.03,"
        "apad=whole_dur=1,atrim=duration=1,adelay=delays=24000S:all=1,"
        "apad=whole_dur=2,atrim=duration=2[pip0]"
    )


def test_envolvente_del_elemento_se_multiplica_por_su_volumen_estatico() -> None:
    chain = _element_chain(_project(_points(), gain=0.5))
    assert (
        "volume=0.5,volume=volume='if(lt(t,0),1,"
        "if(lt(t,0.75),(1+(0.25-1)*(t-0)/0.75),0.25))':eval=frame"
    ) in chain
    # El retraso hasta el inicio del elemento va después de la curva local (t=0 = start).
    assert chain.index(":eval=frame") < chain.index("adelay=delays=24000S:all=1")


def test_envolvente_tambien_en_un_clip_de_v2() -> None:
    project = _project(_points(), track=1)
    assert G.has_element_audio(project)
    assert G.project_has_audio(project)
    chain = _element_chain(project)
    assert "volume=volume='if(lt(t,0),1," in chain and ":eval=frame" in chain


def test_la_envolvente_no_cambia_que_el_elemento_suene() -> None:
    """`has_element_audio`/`project_has_audio` deciden la mezcla; la curva solo la modula."""
    silent = element_mod.set_audio_gain(_project(_points()), "e1", 0.0)
    assert not G.has_element_audio(silent)
    assert G.has_element_audio(_project(_points()))
    assert G.project_has_audio(_project(_points()))


# -- integración real: export y WAV del preview ---------------------------------------


def _video(
    path: Path, *, tone: bool, seconds: float = 2.0, size: str = "320x180", color: str = "blue",
) -> Path:
    audio = (
        f"sine=frequency=440:sample_rate=48000:duration={seconds}"
        if tone else "anullsrc=r=48000:cl=stereo"
    )
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c={color}:size={size}:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", audio, "-t", str(seconds),
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


@pytest.fixture(scope="module")
def sources(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    folder = tmp_path_factory.mktemp("element-envelope")
    try:
        base = _video(folder / "base.mp4", tone=False)
        pip = _video(folder / "pip.mp4", tone=True, size="100x100", color="red")
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")
    return base, pip


@pytest.fixture(scope="module")
def long_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("element-envelope-ui")
    return _video(folder / "base6.mp4", tone=True, seconds=6.0, size="640x360")


def _element_project(sources: tuple[Path, Path], *, track: int = 0) -> tuple[Project, object]:
    base, pip = sources
    project = Project(width=320, height=180, fps=30)
    project, _ = tl.insert_clip(project, media.probe(base))
    project, pip_asset = tl.add_asset(project, media.probe(pip))
    project, element = element_mod.add_element(
        project, "video", 0.5, 1.0, asset_id=pip_asset.id, video_track=track,
        x=0.25, y=0.5, width=0.3, height=0.3, fit="stretch",
    )
    points = (
        env.VolumePoint(0.0, 1.0),
        env.VolumePoint(0.6, 1.0),
        env.VolumePoint(0.7, 0.1),
        env.VolumePoint(1.0, 0.1),
    )
    project = element_mod.update_element(project, element.id, envelope=points)
    return project.validate(), element


def _rms(path: Path, start: float, end: float) -> float:
    """RMS de un MP4 o WAV entre dos instantes (FFmpeg lo decodifica a PCM)."""
    import io
    import wave

    result = subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", str(start),
         "-t", str(end - start), "-i", str(path), "-vn", "-ac", "2", "-ar", "48000",
         "-f", "wav", "-"],
        check=True, capture_output=True,
    )
    with wave.open(io.BytesIO(result.stdout), "rb") as wav:
        raw = wav.readframes(wav.getnframes())
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    mono = data.reshape(-1, 2).mean(axis=1)
    return float(np.sqrt((mono ** 2).mean()))


def _export(project: Project, out: Path) -> None:
    spec = G.build(project, G.PRESETS[0], out, encoder="libx264", exe=F.ffmpeg_exe())
    result = subprocess.run(spec.args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]


def test_export_real_de_un_pip_con_envolvente(tmp_path: Path, sources) -> None:
    project, _element = _element_project(sources)
    out = tmp_path / "pip-env.mp4"
    _export(project, out)
    early = _rms(out, 0.6, 0.9)
    late = _rms(out, 1.2, 1.4)
    assert early > 0.02, f"el PiP debería sonar al empezar (RMS {early:.4f})"
    assert late < early * 0.3, f"la curva no atenuó el final del PiP (RMS {late:.4f})"
    assert _rms(out, 1.7, 2.0) < 0.005


def test_export_real_de_un_clip_v2_con_envolvente(tmp_path: Path, sources) -> None:
    project, _element = _element_project(sources, track=1)
    out = tmp_path / "v2-env.mp4"
    _export(project, out)
    early = _rms(out, 0.6, 0.9)
    late = _rms(out, 1.2, 1.4)
    assert early > 0.02
    assert late < early * 0.3


def test_build_audio_del_preview_aplica_la_misma_atenuacion(
    tmp_path: Path, sources,
) -> None:
    project, _element = _element_project(sources)
    wav = tmp_path / "mezcla.wav"
    args = G.build_audio(project, wav, exe=F.ffmpeg_exe())
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    early = _rms(wav, 0.6, 0.9)
    late = _rms(wav, 1.2, 1.4)
    assert early > 0.02
    assert late < early * 0.3


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
    pilot.a11y_action(key, "scroll_into_view")
    pilot.settle()
    pilot.click(key)
    _settle(pilot)


def _labels(pilot) -> dict[str, str]:
    return {n["key"]: n.get("label") for n in pilot.a11y_tree() if n.get("key") and n.get("label")}


def test_claves_de_volumen_de_un_elemento_desde_el_inspector(
    ui, sources, long_video: Path,
) -> None:
    _, controller, pilot = ui
    editor = controller.editor
    editor.import_paths([long_video])
    assert _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))
    _settle(pilot)

    editor.import_pip([sources[1]])  # PiP de 2 s con tono de 440 Hz
    assert _wait(lambda: any(e.kind == "video" for e in state.editor_project().elements))
    _settle(pilot)
    element = next(e for e in state.editor_project().elements if e.kind == "video")
    editor.select_element(element.id)
    editor.seek(element.start + 0.3)
    _settle(pilot)
    assert pilot.rect("insp-el-env-key") is not None
    assert pilot.rect("insp-el-env-clear") is None  # sin claves no hay «Quitar»

    state.insp_el_gain.set(0.5)
    _settle(pilot)
    _click_inspector(pilot, "insp-el-env-key")
    current = state.editor_project().element(element.id)
    assert len(current.envelope) == 1
    assert current.envelope[0].t == pytest.approx(0.3, abs=0.02)
    assert current.envelope[0].gain == pytest.approx(0.5)
    assert state.undo_label() == "añadir clave de volumen del elemento"
    assert "◆ env" in _labels(pilot)[f"tl-element-{element.id}"]
    assert pilot.rect("insp-el-env-clear") is not None

    editor.seek(element.start + 0.8)
    _settle(pilot)
    state.insp_el_gain.set(1.0)
    _settle(pilot)
    _click_inspector(pilot, "insp-el-env-key")
    current = state.editor_project().element(element.id)
    assert [p.t for p in current.envelope] == pytest.approx([0.3, 0.8], abs=0.02)
    assert current.envelope[-1].gain == pytest.approx(1.0)

    # Fuera del intervalo del elemento no se graba nada.
    editor.seek(element.end + 0.5)
    _settle(pilot)
    before = state.editor_project().element(element.id).envelope
    _click_inspector(pilot, "insp-el-env-key")
    assert state.editor_project().element(element.id).envelope == before

    _click_inspector(pilot, "insp-el-env-clear")
    assert state.editor_project().element(element.id).envelope == ()
    assert "◆ env" not in _labels(pilot)[f"tl-element-{element.id}"]
