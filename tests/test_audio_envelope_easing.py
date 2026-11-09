"""Easing de las envolventes de volumen (docs/40): curvas, modelo, export, preview y UI."""

from __future__ import annotations

import json
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from recstudio import state
from recstudio.core import animation as anim
from recstudio.core import audio_envelope as env
from recstudio.core import graph as G
from recstudio.core import store
from recstudio.core.errors import ProjectError
from recstudio.core.project import (
    SCHEMA_VERSION,
    Clip,
    Element,
    MediaAsset,
    Project,
    Track,
    from_dict,
    to_dict,
)
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import media, playback
from recstudio.ui import timeline as ui_tl


# -- modelo puro ----------------------------------------------------------------------


def test_curvas_compartidas_con_animation() -> None:
    # No hay una segunda fórmula: `value_at` reutiliza `core.animation.ease`.
    assert anim.EASING_KINDS == ("linear", "ease_in", "ease_out", "ease_in_out")
    for kind in anim.EASING_KINDS:
        curve = (
            env.VolumePoint(0.0, 0.0, kind),  # type: ignore[arg-type]
            env.VolumePoint(1.0, 1.0),
        )
        for k in (0.0, 0.25, 0.5, 0.75, 1.0):
            assert env.value_at(curve, k) == pytest.approx(anim.ease(k, kind))

    with pytest.raises(ProjectError):
        env.VolumePoint(0.0, 1.0, "bounce")  # type: ignore[arg-type]


def test_value_at_usa_el_easing_de_la_clave_izquierda() -> None:
    points = (
        env.VolumePoint(0.0, 1.0, "ease_in"),
        env.VolumePoint(1.0, 0.25, "ease_out"),
        env.VolumePoint(2.0, 0.5, "linear"),
    )
    # Primer tramo con ease_in: k = t, la curva es k³.
    assert env.value_at(points, 0.25) == pytest.approx(1.0 + (0.25 - 1.0) * 0.25 ** 3)
    assert env.value_at(points, 0.5) == pytest.approx(1.0 + (0.25 - 1.0) * 0.5 ** 3)
    assert env.value_at(points, 0.75) == pytest.approx(1.0 + (0.25 - 1.0) * 0.75 ** 3)
    # Segundo tramo con ease_out: el easing de la clave izquierda cambia la curva.
    assert env.value_at(points, 1.5) == pytest.approx(0.25 + (0.5 - 0.25) * 0.875)
    # El easing de la última clave no tiene tramo saliente y no altera nada.
    last = (env.VolumePoint(0.0, 1.0), env.VolumePoint(1.0, 0.25, "ease_in_out"))
    assert env.value_at(last, 0.5) == pytest.approx(0.625)
    # Extremos y envolvente vacía.
    assert env.value_at(points, -1.0) == pytest.approx(1.0)
    assert env.value_at(points, 9.0) == pytest.approx(0.5)
    assert env.value_at((), 1.0, default=0.3) == pytest.approx(0.3)


def test_expr_linear_intacta_y_curvas_ffmpeg() -> None:
    linear = (env.VolumePoint(0.0, 1.0), env.VolumePoint(1.0, 0.25))
    assert env.expr(linear) == (
        "if(lt(t,0),1,if(lt(t,1),(1+(0.25-1)*(t-0)/1),0.25))"
    )
    assert env.expr(linear, start=-0.5) == (
        "if(lt(t,-0.5),1,if(lt(t,0.5),(1+(0.25-1)*(t+0.5)/1),0.25))"
    )
    assert env.expr(()) == "1"
    assert env.expr((env.VolumePoint(0.0, 0.5),)) == "0.5"

    def expression(kind: str) -> str:
        return env.expr((
            env.VolumePoint(0.0, 1.0, kind),  # type: ignore[arg-type]
            env.VolumePoint(1.0, 0.25),
        ))

    assert "*((t-0)/1)*((t-0)/1)*((t-0)/1)" in expression("ease_in")
    assert "(1-(1-((t-0)/1))*(1-((t-0)/1))*(1-((t-0)/1)))" in expression("ease_out")
    assert "if(lt(((t-0)/1),0.5)" in expression("ease_in_out")
    assert "4*((t-0)/1)*((t-0)/1)*((t-0)/1)" in expression("ease_in_out")
    # El desplazamiento por solape se aplica dentro de la curva.
    shifted = env.expr((
        env.VolumePoint(0.0, 1.0, "ease_in"),
        env.VolumePoint(1.0, 0.25),
    ), start=-0.5)
    assert "((t+0.5)/1)*((t+0.5)/1)*((t+0.5)/1)" in shifted
    # El easing de la última clave no se usa (no tiene tramo saliente).
    last_easing = env.expr((
        env.VolumePoint(0.0, 1.0),
        env.VolumePoint(1.0, 0.25, "ease_in"),
    ))
    assert last_easing == env.expr(linear)


def test_set_point_actualiza_el_easing_y_slice_lo_conserva() -> None:
    points = env.set_point((), 1.0, 0.5, "ease_in")
    points = env.set_point(points, 0.0, 1.0, "linear")
    points = env.set_point(points, 1.0, 0.5, "ease_out")
    assert [(p.t, p.gain, p.easing) for p in points] == [
        (0.0, 1.0, "linear"), (1.0, 0.5, "ease_out"),
    ]
    with pytest.raises(ProjectError):
        env.set_point((), 0.0, 1.0, "bounce")  # type: ignore[arg-type]

    original = (
        env.VolumePoint(0.0, 1.0, "ease_in"),
        env.VolumePoint(1.0, 0.25, "ease_out"),
        env.VolumePoint(2.0, 1.0, "linear"),
    )
    sliced = env.slice_envelope(original, 0.5, 1.5)
    assert [(p.t, p.gain, p.easing) for p in sliced] == [
        (0.0, pytest.approx(0.90625), "ease_in"),
        (0.5, 0.25, "ease_out"),
        (1.0, pytest.approx(0.90625), "ease_out"),
    ]
    assert [(p.t, p.gain, p.easing) for p in env.slice_envelope(original, 1.0)] == [
        (0.0, 0.25, "ease_out"),
        (1.0, 1.0, "linear"),
    ]


def _project_with_envelopes() -> Project:
    asset = MediaAsset("a", "fuente.mp4", 4.0, 320, 180, 30, True)
    clip = Clip(
        "c1", "a", 0.0, 2.0,
        envelope=(
            env.VolumePoint(0.0, 1.0, "ease_in"),
            env.VolumePoint(1.5, 0.25, "ease_out"),
        ),
    )
    element = Element(
        "e1", "video", 0.0, 1.5, asset_id="a",
        envelope=(
            env.VolumePoint(0.0, 0.5, "ease_in_out"),
            env.VolumePoint(0.75, 1.0),
        ),
    )
    return Project(
        assets=(asset,), video=Track("video", (clip,)), elements=(element,),
        fps=30, width=320, height=180,
    ).validate()


def test_serializacion_easing_de_volumen_y_migracion_v13_v14() -> None:
    project = _project_with_envelopes()
    assert SCHEMA_VERSION == 15
    data = json.loads(json.dumps(to_dict(project)))
    assert data["video"]["clips"][0]["envelope"] == [
        {"t": 0.0, "gain": 1.0, "easing": "ease_in"},
        {"t": 1.5, "gain": 0.25, "easing": "ease_out"},
    ]
    assert data["elements"][0]["envelope"] == [
        {"t": 0.0, "gain": 0.5, "easing": "ease_in_out"},
        {"t": 0.75, "gain": 1.0, "easing": "linear"},
    ]
    assert from_dict(data) == project

    # Un proyecto v13 no tiene `easing` en los puntos: se abre lineal y suena igual.
    old = json.loads(json.dumps(data))
    old["schema_version"] = 13
    for group in ("video", "elements"):
        items = old[group]["clips"] if group == "video" else old[group]
        for item in items:
            for point in item["envelope"]:
                point.pop("easing")
    migrated, read_only = store.migrate(old)
    assert read_only is False and migrated["schema_version"] == 15
    restored = from_dict(migrated)
    assert [point.easing for point in restored.video.clips[0].envelope] == [
        "linear", "linear",
    ]
    assert [point.easing for point in restored.elements[0].envelope] == [
        "linear", "linear",
    ]
    assert [point.gain for point in restored.video.clips[0].envelope] == [1.0, 0.25]


# -- preview y export real -------------------------------------------------------------


def _video(path: Path, *, seconds: float = 2.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc2=size=320x180:rate=30:duration={seconds}", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-preset",
         "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


def _project(asset: MediaAsset, easing: str, *, envelope: bool = True) -> Project:
    points = (
        (env.VolumePoint(0.0, 1.0, easing), env.VolumePoint(2.0, 0.1))  # type: ignore[arg-type]
        if envelope else ()
    )
    return Project(
        assets=(asset,),
        video=Track("video", (Clip("c1", asset.id, 0.0, 2.0, envelope=points),)),
        fps=30, width=320, height=180,
    ).validate()


def _gain(t: float, easing: str) -> float:
    # Dos claves (0 s, 1.0) y (2 s, 0.1): k = t / 2.
    return 1.0 + (0.1 - 1.0) * anim.ease(t / 2.0, easing)  # type: ignore[arg-type]


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


def _windows() -> tuple[tuple[float, float], ...]:
    return ((0.2, 0.3), (0.45, 0.55), (0.7, 0.8))


def test_preview_y_export_aplican_la_misma_curva(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _video(tmp_path / "fuente.mp4")
    asset = media.probe(video)
    linear = _project(asset, "linear")
    eased = _project(asset, "ease_in")
    plain = _project(asset, "linear", envelope=False)

    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
    preview = playback.AudioPreview()
    wavs: dict[str, Path] = {}
    for name, project in (("plain", plain), ("linear", linear), ("ease", eased)):
        target = tmp_path / f"{name}.wav"
        preview._render_mix(project, target)
        assert target.is_file()
        wavs[name] = target

    # El WAV del preview (build_audio) usa la misma curva que el export.
    for start, end in _windows():
        t = (start + end) / 2
        base = _wav_audio_rms(wavs["plain"], start, end)
        assert _wav_audio_rms(wavs["linear"], start, end) / base == pytest.approx(
            _gain(t, "linear"), rel=0.05,
        )
        assert _wav_audio_rms(wavs["ease"], start, end) / base == pytest.approx(
            _gain(t, "ease_in"), rel=0.05,
        )

    # Export MP4 real: la curva cúbica se nota frente a la lineal en los mismos instantes.
    outs: dict[str, Path] = {}
    for name, project in (("linear", linear), ("ease", eased)):
        out = tmp_path / f"{name}.mp4"
        spec = G.build(
            project, G.PRESETS[0], out, encoder="libx264", exe=F.ffmpeg_exe(),
        )
        result = subprocess.run(
            spec.args, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stderr[-2000:]
        outs[name] = out

    for start, end in _windows():
        t = (start + end) / 2
        ratio = _mp4_audio_rms(outs["ease"], start, end) / _mp4_audio_rms(
            outs["linear"], start, end,
        )
        assert ratio == pytest.approx(_gain(t, "ease_in") / _gain(t, "linear"), rel=0.15)


# -- UI -------------------------------------------------------------------------------


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
    path = tmp_path_factory.mktemp("env-easing") / "v.mp4"
    try:
        _video(path, seconds=4.0)
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
        state.insp_env_easing.set("linear")
        state.insp_easing.set("linear")
        state.screen.set("record")


def _click_inspector(pilot, key: str) -> None:
    pilot.scroll("insp-scroll", dy=3000)
    pilot.settle()
    pilot.a11y_action(key, "scroll_into_view")
    pilot.settle()
    pilot.click(key)
    _settle(pilot)


def _choose_env_easing(pilot, key: str, index: int) -> None:
    _click_inspector(pilot, key)
    pilot.click(f"{key}-opt-{index}")
    _settle(pilot)


def _open(controller, pilot, path: Path):  # noqa: ANN001
    editor = controller.editor
    editor.import_paths([path])
    assert _wait(lambda: state.project_open() and bool(state.editor_project().video.clips))
    _settle(pilot)
    assert _wait(lambda: state.preview_area().width > 0)
    return editor


def test_ui_el_selector_de_envolvente_decide_el_easing(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    project = state.editor_project()
    clip_id = project.video.clips[0].id
    start = project.video.clips[0].start or 0.0
    editor.select_clip(clip_id)
    editor.seek(start + 0.5)
    _settle(pilot)

    # Opción 1 = ease_in. La clave nueva lo guarda.
    _choose_env_easing(pilot, "insp-env-easing", 1)
    assert state.insp_env_easing() == "ease_in"
    _click_inspector(pilot, "insp-env-key")
    clip = state.editor_project().clip(clip_id)
    assert len(clip.envelope) == 1
    assert clip.envelope[0].easing == "ease_in"

    # Volver a grabar en el mismo instante actualiza el easing, no duplica.
    _choose_env_easing(pilot, "insp-env-easing", 2)
    _click_inspector(pilot, "insp-env-key")
    clip = state.editor_project().clip(clip_id)
    assert len(clip.envelope) == 1 and clip.envelope[0].easing == "ease_out"

    # Otra clave usa el easing elegido en ese momento; la anterior conserva el suyo.
    editor.seek(start + 1.5)
    _settle(pilot)
    _choose_env_easing(pilot, "insp-env-easing", 3)
    _click_inspector(pilot, "insp-env-key")
    clip = state.editor_project().clip(clip_id)
    assert [point.easing for point in clip.envelope] == ["ease_out", "ease_in_out"]
    assert state.undo_label() == "añadir clave de volumen"

    # El modo Env del timeline también usa el easing elegido para la clave nueva.
    _choose_env_easing(pilot, "insp-env-easing", 1)
    editor.toggle_envelope_edit()
    _settle(pilot)
    rect = pilot.rect(f"clip-{clip_id}")
    assert rect is not None
    lay = ui_tl.tracks()
    zoom = state.tl_zoom()
    x = rect[0] + 2.5 * zoom
    y = rect[1] + (lay.audio_top - lay.video_top) + lay.audio_h / 2
    pilot.click_at(x, y)
    _settle(pilot, 8)
    clip = state.editor_project().clip(clip_id)
    assert [point.easing for point in clip.envelope] == [
        "ease_out", "ease_in_out", "ease_in",
    ]
    editor.toggle_envelope_edit()
    _settle(pilot)


def test_ui_el_selector_de_envolvente_de_elemento_decide_el_easing(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = _open(controller, pilot, video)
    editor.import_pip([video])
    assert _wait(lambda: any(e.kind == "video" for e in state.editor_project().elements))
    _settle(pilot)
    element = next(e for e in state.editor_project().elements if e.kind == "video")
    editor.select_element(element.id)
    editor.seek(element.start + 0.3)
    _settle(pilot)

    _choose_env_easing(pilot, "insp-el-env-easing", 1)
    assert state.insp_env_easing() == "ease_in"
    _click_inspector(pilot, "insp-el-env-key")
    current = state.editor_project().element(element.id)
    assert len(current.envelope) == 1
    assert current.envelope[0].easing == "ease_in"
    assert state.undo_label() == "añadir clave de volumen del elemento"

    # Re-grabar el mismo instante actualiza el easing.
    _choose_env_easing(pilot, "insp-el-env-easing", 3)
    _click_inspector(pilot, "insp-el-env-key")
    current = state.editor_project().element(element.id)
    assert len(current.envelope) == 1 and current.envelope[0].easing == "ease_in_out"
