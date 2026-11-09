"""Formatos de export MP4/WebM/GIF (docs/48): graph, servicio, archivos reales y UI."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from recstudio import state
from recstudio.core import graph as G
from recstudio.core import timeline as tl
from recstudio.core.errors import ProjectError
from recstudio.core.project import Clip, MediaAsset, Project, Track, Transition
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import export as export_mod
from recstudio.services import media
from recstudio.services.settings import Settings

A = MediaAsset("a", "a.mp4", 30.0, 1920, 1080, 60.0, True)
B = MediaAsset("b", "b.mp4", 20.0, 1920, 1080, 60.0, True)


def _golden_project() -> Project:
    """El ejemplo de docs/04: 2 clips, fundido de 0.5 s y el segundo al doble de velocidad."""
    return Project(
        assets=(A, B),
        video=Track("video", (Clip("c1", "a", 10.0, 25.0), Clip("c2", "b", 3.2, 12.0, speed=2.0)),
                    (Transition("fade", 0.5),)),
        fps=60,
    )


def _make_video(path: Path, *, seconds: float = 2.0, size: str = "320x240", fps: int = 30,
                audio: bool = True) -> Path:
    exe = F.ffmpeg_exe()
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-g", str(fps), "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-shortest"] if audio else ["-an"]
    subprocess.run([*cmd, str(path)], check=True, capture_output=True)
    return path


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("formatos")
    try:
        return _make_video(folder / "fuente.mp4")
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")


# -- modelo y graph ---------------------------------------------------------------------


def test_mp4_por_defecto_conserva_los_presets_y_el_golden() -> None:
    assert G.EXPORT_FORMATS == ("mp4", "webm", "gif")
    assert G.FORMAT_EXTENSIONS == {"mp4": ".mp4", "webm": ".webm", "gif": ".gif"}
    assert all(preset.format == "mp4" for preset in G.PRESETS)
    spec = G.build(_golden_project(), G.preset_by_id("rapido"), "out.mp4", encoder="h264_nvenc")
    # El camino histórico no cambia: mismo filtro, AAC y faststart, sin rastro de VP9/GIF.
    assert "palettegen" not in spec.filter_complex and "split=2" not in spec.filter_complex
    assert "-c:a" in spec.args and spec.args[spec.args.index("-c:a") + 1] == "aac"
    assert "-movflags" in spec.args and "+faststart" in spec.args
    assert "-c:v" in spec.args and spec.args[spec.args.index("-c:v") + 1] == "h264_nvenc"
    assert "libvpx-vp9" not in spec.args and "-loop" not in spec.args


def test_webm_usa_vp9_y_opus_sin_aac_ni_faststart() -> None:
    preset = G.with_format(G.preset_by_id("calidad"), "webm")
    spec = G.build(_golden_project(), preset, "out.webm", encoder="libvpx-vp9")
    assert spec.has_audio and spec.frames == 1134
    assert spec.filter_complex == G.build(
        _golden_project(), G.preset_by_id("calidad"), "out.mp4", encoder="libx264"
    ).filter_complex  # la composición es la misma; solo cambia el códec/contenedor
    assert ("-c:v", "libvpx-vp9") == tuple(spec.args[spec.args.index("-c:v"):
                                                       spec.args.index("-c:v") + 2])
    assert "-crf" in spec.args and "18" in spec.args and "-b:v" in spec.args
    assert "-c:a" in spec.args and spec.args[spec.args.index("-c:a") + 1] == "libopus"
    assert "aac" not in spec.args and "+faststart" not in spec.args
    from recstudio.ui.export_modal import _summary

    summary = _summary(_golden_project(), preset)
    assert "VP9" in summary and "Opus" in summary


def test_webm_sin_audio_usa_an_y_bitrate_si_se_pide() -> None:
    mute = MediaAsset("m", "m.mp4", 10.0, 320, 240, 30.0, False)
    project = Project(assets=(mute,), video=Track("video", (Clip("c", "m", 0, 2),)), fps=30)
    preset = G.ExportPreset("personalizado", "Personalizado", "", "x264", video_bitrate="800k",
                            format="webm")
    spec = G.build(project, preset, "o.webm", encoder="libvpx-vp9")
    assert not spec.has_audio and "-an" in spec.args and "libopus" not in spec.args
    assert "-b:v" in spec.args and "800k" in spec.args and "-crf" not in spec.args


def test_gif_usa_paleta_sin_audio_y_acota_fps_y_tamano() -> None:
    project = _golden_project()
    preset = G.with_format(G.preset_by_id("calidad"), "gif")
    spec = G.build(project, preset, "out.gif", encoder="gif")
    assert not spec.has_audio
    assert spec.fps == G.GIF_MAX_FPS and "fps=20" in spec.filter_complex
    assert spec.filter_complex.endswith(
        "[x1]split=2[gifv][gifp];[gifp]palettegen=stats_mode=diff[gifpal];"
        "[gifv][gifpal]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle[vgif]"
    )
    assert "palettegen" in spec.filter_complex and "paletteuse" in spec.filter_complex
    assert "-an" in spec.args and "-loop" in spec.args and spec.args[spec.args.index("-loop") + 1] == "0"
    assert spec.args[spec.args.index("-c:v") + 1] == "gif"
    assert "-c:a" not in spec.args and "aac" not in spec.args
    assert (spec.width, spec.height) == (720, 404)
    assert spec.frames == round(18.9 * 20)  # conserva la duración del timeline a 20 fps
    assert spec.duration == pytest.approx(18.9, abs=0.1)


def test_gif_respeta_el_tamano_personalizado_pero_lo_acota() -> None:
    project = Project(width=1920, height=1080)
    preset = G.ExportPreset("personalizado", "Personalizado", "", "x264",
                            width=1080, height=1920, format="gif")
    assert G.output_size(project, preset) == (404, 720)
    assert G.export_fps(Project(fps=60), preset) == 20
    landscape = G.ExportPreset("personalizado", "Personalizado", "", "x264",
                               width=1080, height=1920, fps=60)
    assert G.output_size(project, landscape) == (1080, 1920)  # MP4 no se acota
    assert G.export_fps(Project(fps=60), landscape) == 60


def test_formatos_invalidos_y_combinaciones() -> None:
    with pytest.raises(ProjectError, match="desconocido"):
        G.with_format(G.preset_by_id("calidad"), "avi")
    with pytest.raises(ProjectError, match="Sin recodificar|sin recodificar"):
        G.build(_golden_project(), G.with_format(G.preset_by_id("copia"), "webm"), "o.webm")
    with pytest.raises(ProjectError, match="GIF"):
        G.build(_golden_project(), G.with_format(G.preset_by_id("calidad"), "gif"), "o.gif",
                encoder="libx264")
    with pytest.raises(ProjectError, match="WebM"):
        G.build(_golden_project(), G.with_format(G.preset_by_id("calidad"), "webm"), "o.webm",
                encoder="libx264")
    with pytest.raises(ProjectError, match="VP9"):
        G.resolve_encoder(G.with_format(G.preset_by_id("calidad"), "webm"), {"libx264": True})
    exporter = export_mod.Exporter()
    with pytest.raises(ProjectError, match="MP4"):
        exporter.start(_golden_project(), G.with_format(G.preset_by_id("copia"), "gif"), "o.gif")
    assert G.format_extension("webm") == ".webm"


def test_unique_output_y_parcial_usan_la_extension_del_formato(tmp_path: Path) -> None:
    first = export_mod.unique_output(tmp_path, "clip", ".webm")
    assert first.name == "clip.webm"
    first.write_bytes(b"x")
    assert export_mod.unique_output(tmp_path, "clip", ".webm").name == "clip (2).webm"
    assert export_mod.partial_path(tmp_path / "clip.webm").name == "clip.part.webm"
    assert export_mod.partial_path(tmp_path / "clip.gif").name == "clip.part.gif"


# -- export real ------------------------------------------------------------------------


def _export(project: Project, preset: G.ExportPreset, out: Path) -> export_mod.ExportProgress:
    exporter = export_mod.Exporter()
    exporter.start(project, preset, out)
    return exporter.wait(120)


def _video_frames(path: Path) -> int:
    import av

    with av.open(str(path)) as container:
        return sum(1 for _ in container.decode(container.streams.video[0]))


def test_export_real_webm_vp9_opus(video: Path, tmp_path: Path) -> None:
    asset = media.probe(video)
    project = Project(assets=(asset,), fps=30, width=320, height=240)
    project, _ = tl.append_clip(project, asset)
    out = tmp_path / "real.webm"
    result = _export(project, G.with_format(G.preset_by_id("calidad"), "webm"), out)
    assert result.state == "done", result.message
    info = F.probe_media(out)
    assert (info.video_codec, info.audio_codec) == ("vp9", "opus")
    assert (info.width, info.height) == (320, 240)
    assert info.duration_s == pytest.approx(2.0, abs=0.2)
    assert info.fps == pytest.approx(30, abs=0.1)
    assert _video_frames(out) == 60
    assert not list(tmp_path.glob("*.part.webm"))


def test_export_real_gif_sin_audio(video: Path, tmp_path: Path) -> None:
    asset = media.probe(video)
    project = Project(assets=(asset,), fps=30, width=320, height=240)
    project, _ = tl.append_clip(project, asset)
    out = tmp_path / "real.gif"
    result = _export(project, G.with_format(G.preset_by_id("calidad"), "gif"), out)
    assert result.state == "done", result.message
    info = F.probe_media(out)
    assert info.video_codec == "gif" and not info.has_audio
    assert (info.width, info.height) == (320, 240)
    assert info.duration_s == pytest.approx(2.0, abs=0.2)
    assert info.fps == pytest.approx(20, abs=0.1)
    assert _video_frames(out) == 40  # 2 s × 20 fps, conserva la duración
    assert not list(tmp_path.glob("*.part.gif"))


def test_export_webm_avisa_si_falta_opus(video: Path, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    asset = media.probe(video)
    project = Project(assets=(asset,), fps=30, width=320, height=240)
    project, _ = tl.append_clip(project, asset)
    monkeypatch.setattr(export_mod.F, "probe_encoders", lambda **_: {"libx264": True})
    monkeypatch.setattr(export_mod.F, "probe_format_encoders", lambda **_: {"libvpx-vp9": True})
    result = _export(project, G.with_format(G.preset_by_id("calidad"), "webm"),
                     tmp_path / "sin-opus.webm")
    assert result.state == "error" and "Opus" in result.message
    assert not (tmp_path / "sin-opus.webm").exists()


# -- UI y persistencia ------------------------------------------------------------------


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
        state.export_open.set(False)
        state.export_progress.set(None)
        state.export_preset.set("rapido")
        state.export_format.set("mp4")
        state.screen.set("record")


def _wait(predicate, timeout: float = 30.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _settle(pilot, frames: int = 3) -> None:
    for _ in range(frames):
        pilot.frame()
    pilot.settle()


def _texts(pilot) -> dict[str, str]:
    return {n["key"]: n.get("text", "") for n in pilot.layout()
            if n.get("key") and n.get("text")}


def test_modal_cambia_formato_extension_resumen_y_audio(ui, tmp_path: Path) -> None:
    _, controller, pilot = ui
    source = _make_video(tmp_path / "mudo.mp4", audio=False)
    editor = controller.editor
    target = str(source.resolve())
    editor.import_paths([source], new_project=True)
    assert _wait(lambda: state.project_open()
                 and any(a.path == target for a in state.editor_project().assets))
    _settle(pilot)
    pilot.press("ctrl+e")
    _settle(pilot)

    assert state.export_format() == "mp4"
    assert _texts(pilot).get("export-ext") == ".mp4"
    assert "H.264" in _texts(pilot).get("export-summary", "")

    pilot.click("export-format-webm")
    _settle(pilot)
    assert state.export_format() == "webm"
    assert _texts(pilot).get("export-ext") == ".webm"
    summary = _texts(pilot).get("export-summary", "")
    assert "VP9" in summary and "sin audio" in summary  # la fuente del test es muda
    assert pilot.rect("export-abitrate") is not None  # WebM sí puede llevar audio
    assert pilot.rect("export-codec") is None  # VP9/Opus no eligen H.264

    pilot.click("export-format-gif")
    _settle(pilot)
    assert state.export_format() == "gif"
    assert _texts(pilot).get("export-ext") == ".gif"
    summary = _texts(pilot).get("export-summary", "")
    assert "GIF" in summary and "sin audio" in summary
    assert pilot.rect("export-abitrate") is None  # GIF no ofrece bitrate de audio
    assert pilot.rect("export-vbitrate") is None
    assert pilot.rect("export-gif-note") is not None

    # «Sin recodificar» queda deshabilitado fuera de MP4 y no cambia el preset.
    before = state.export_preset()
    pilot.click("preset-copia")
    _settle(pilot)
    assert state.export_preset() == before
    pilot.click("export-format-mp4")
    _settle(pilot)
    pilot.click("preset-copia")
    _settle(pilot)
    assert state.export_preset() == "copia" and state.export_format() == "mp4"


def test_controlador_exporta_con_formato_y_extension(ui, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    _, controller, pilot = ui
    source = _make_video(tmp_path / "mudo.mp4", audio=False)
    editor = controller.editor
    target = str(source.resolve())
    editor.import_paths([source], new_project=True)
    assert _wait(lambda: state.project_open()
                 and any(a.path == target for a in state.editor_project().assets))
    _settle(pilot)
    state.export_format.set("gif")
    state.export_name.set("animacion")
    captured: dict[str, object] = {}

    def fake_start(project, preset, out_path, span=None):  # noqa: ANN001, ANN202
        captured.update(project=project, preset=preset, out_path=Path(out_path), span=span)

    monkeypatch.setattr(editor.exporter, "start", fake_start)
    editor.start_export()
    preset = captured["preset"]
    assert isinstance(preset, G.ExportPreset) and preset.format == "gif"
    assert captured["out_path"].name == "animacion.gif"
    assert editor.settings.export_format == "gif"


def test_gif_ignora_los_bitrates_ocultos_del_personalizado() -> None:
    state.export_custom_video_bitrate.set("no-es-un-bitrate")
    state.export_custom_audio_bitrate.set("tampoco")
    try:
        state.export_format.set("gif")
        assert state.export_custom_error() is None
        preset = state.export_custom_preset()
        assert preset.format == "gif"
    finally:
        state.export_format.set("mp4")
        state.export_custom_video_bitrate.set("6M")
        state.export_custom_audio_bitrate.set("192k")


def test_settings_persiste_y_normaliza_el_formato(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    settings = Settings(export_format="webm").normalized()
    settings.save()
    assert Settings.load().export_format == "webm"
    assert Settings(export_format="avi").normalized().export_format == "mp4"
    assert Settings().normalized().export_format == "mp4"
