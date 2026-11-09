"""Export personalizado (docs/44): tamaño/fps/bitrate, presets sociales, UI y persistencia."""

from __future__ import annotations

import json
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

CUSTOM = G.CUSTOM_PRESET_ID


def _golden_project() -> Project:
    """El ejemplo de docs/04: 2 clips, fundido de 0.5 s y el segundo al doble de velocidad."""
    a = MediaAsset("a", "a.mp4", 30.0, 1920, 1080, 60.0, True)
    b = MediaAsset("b", "b.mp4", 20.0, 1920, 1080, 60.0, True)
    return Project(
        assets=(a, b),
        video=Track("video", (Clip("c1", "a", 10.0, 25.0), Clip("c2", "b", 3.2, 12.0, speed=2.0)),
                    (Transition("fade", 0.5),)),
        fps=60,
    )


def _make_video(path: Path, *, seconds: float = 1.0, size: str = "320x240", fps: int = 30,
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
    folder = tmp_path_factory.mktemp("export-custom")
    try:
        return _make_video(folder / "fuente.mp4")
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")


# -- modelo y graph ---------------------------------------------------------------------


def test_output_size_personalizado_par_acotado_y_fallback() -> None:
    project = Project(width=1920, height=1080)
    preset = G.ExportPreset("personalizado", "Personalizado", "", "x264", width=1919, height=1079)
    assert G.output_size(project, preset) == (1918, 1078)  # par hacia abajo
    clamped = G.ExportPreset("personalizado", "Personalizado", "", "x264",
                             width=99999, height=3)
    assert G.output_size(project, clamped) == (7680, 16)
    with pytest.raises(ProjectError, match="ancho y alto"):
        G.output_size(project, G.ExportPreset("personalizado", "Personalizado", "", "x264",
                                              width=1920))
    # Sin tamaño explícito se conserva el comportamiento histórico de `max_height`.
    assert G.output_size(Project(width=1366, height=768), G.preset_by_id("ligero")) == (1280, 720)


def test_bitrates_formato_y_rangos() -> None:
    assert G.parse_bitrate("6M") == 6_000_000
    assert G.parse_bitrate("6000k") == 6_000_000
    assert G.parse_bitrate("6000") == 6_000_000  # sin sufijo = kbps
    assert G.parse_bitrate("1,5M") == 1_500_000
    assert G.parse_bitrate("0") is None and G.parse_bitrate("nope") is None
    assert G.bitrate_arg(6_000_000) == "6M" and G.bitrate_arg(800_000) == "800k"
    assert G.format_bitrate(6_000_000) == "6 Mbps"
    assert G.format_bitrate(192_000) == "192 kbps"


def test_custom_error_valida_pares_rangos_y_formato() -> None:
    assert G.custom_error("1080", "1920", "24", "6M", "192k") is None
    assert "pares" in G.custom_error("1081", "1920", "24", "6M", "192k")
    assert "ancho" in G.custom_error("x", "1920", "24", "6M", "192k").lower()
    assert "fps" in G.custom_error("1080", "1920", "999", "6M", "192k")
    assert "video" in G.custom_error("1080", "1920", "24", "nope", "192k")
    assert "audio" in G.custom_error("1080", "1920", "24", "6M", "1k")
    preset = G.custom_preset("1080", "1920", "24", "6M", "192k")
    assert (preset.id, preset.width, preset.height, preset.fps, preset.video) == (
        CUSTOM, 1080, 1920, 24, "x264")
    assert preset.video_bitrate == "6M" and preset.audio_bitrate == "192k"
    with pytest.raises(ProjectError):
        G.custom_preset("1080", "1920", "24", "6M", "192k", codec="av1")


def test_graph_respeta_fps_y_bitrate_personalizados() -> None:
    preset = G.custom_preset("320", "180", "24", "800k", "128k")
    spec = G.build(_golden_project(), preset, "out.mp4", encoder="libx264")
    assert (spec.width, spec.height, spec.fps) == (320, 180, 24)
    assert spec.frames == 454  # 360 + 106 − 12 (redondeo por clip al fps de salida)
    assert spec.duration == pytest.approx(454 / 24)
    assert spec.filter_complex.count("fps=24") == 2 and "fps=60" not in spec.filter_complex
    assert "scale=320:180:force_original_aspect_ratio=decrease" in spec.filter_complex
    assert "-b:v" in spec.args and "800k" in spec.args
    assert "-maxrate" in spec.args and "-bufsize" in spec.args
    assert "-crf" not in spec.args
    assert spec.args[spec.args.index("-b:a") + 1] == "128k"


def test_los_cuatro_presets_historicos_no_cambian() -> None:
    assert G.PRESET_IDS == ("rapido", "calidad", "ligero", "copia")
    assert all(p.width is None and p.height is None and p.fps is None
               and p.video_bitrate is None for p in G.PRESETS)
    spec = G.build(_golden_project(), G.preset_by_id("calidad"), "out.mp4", encoder="libx264")
    assert spec.fps == 60 and "fps=60" in spec.filter_complex
    assert "-crf" in spec.args and "18" in spec.args
    assert "-b:v" not in spec.args  # sin bitrate no aparece el modo CBR/VBR
    # La cadena del primer clip sigue siendo la del golden de F4.
    assert spec.filter_complex.startswith(
        "[0:v]setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:-1:-1:color=black,fps=60,tpad=stop_mode=clone:stop=60,"
        "trim=end_frame=900,format=yuv420p,setsar=1[v0]"
    )


def test_copia_rechaza_tamano_fps_y_bitrate() -> None:
    preset = G.ExportPreset("copia", "Sin recodificar", "", "copy", width=1920, height=1080)
    with pytest.raises(ProjectError, match="Sin recodificar"):
        G.build(_golden_project(), preset, "out.mp4")
    exporter = export_mod.Exporter()
    with pytest.raises(ProjectError, match="Sin recodificar"):
        exporter.start(_golden_project(), preset, "out.mp4")


def test_presets_sociales() -> None:
    assert [(s.id, s.label, s.width, s.height) for s in G.SOCIAL_PRESETS] == [
        ("9x16", "9:16", 1080, 1920),
        ("1x1", "1:1", 1080, 1080),
        ("4x5", "4:5", 1080, 1350),
        ("16x9", "16:9", 1920, 1080),
    ]
    for social in G.SOCIAL_PRESETS:
        preset = G.custom_preset(str(social.width), str(social.height), "30", "6M", "192k")
        assert G.output_size(Project(), preset) == (social.width, social.height)


# -- export real ------------------------------------------------------------------------


def test_export_real_con_tamano_fps_y_bitrate(video: Path, tmp_path: Path) -> None:
    asset = media.probe(video)
    project = Project(assets=(asset,), fps=30, width=320, height=240)
    project, _ = tl.append_clip(project, asset)
    preset = G.custom_preset("320", "180", "24", "400k", "128k", codec="x264")
    out = tmp_path / "personalizado.mp4"
    exporter = export_mod.Exporter()
    exporter.start(project, preset, out)
    result = exporter.wait(120)
    assert result.state == "done", result.message
    info = F.probe_media(out)
    assert (info.width, info.height) == (320, 180)
    assert info.fps == pytest.approx(24, abs=0.1)
    assert info.duration_s == pytest.approx(1.0, abs=0.2)
    assert info.has_audio
    # Conteo real de frames: el fps/frames de GraphSpec no se queda en una promesa.
    import av

    with av.open(str(out)) as container:
        stream = container.streams.video[0]
        frames = sum(1 for _ in container.decode(stream))
    assert frames == 24


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
        state.export_custom_width.set("1920")
        state.export_custom_height.set("1080")
        state.export_custom_fps.set("60")
        state.export_custom_video_bitrate.set("6M")
        state.export_custom_audio_bitrate.set("192k")
        state.export_custom_codec.set("auto")
        state.export_preset.set("rapido")
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


def test_modal_personalizado_valida_y_aplica_presets_sociales(ui, video: Path) -> None:
    _, controller, pilot = ui
    editor = controller.editor
    target = str(video.resolve())
    editor.import_paths([video], new_project=True)
    assert _wait(lambda: state.project_open()
                 and any(a.path == target for a in state.editor_project().assets))
    _settle(pilot)
    pilot.press("ctrl+e")
    _settle(pilot)

    assert pilot.rect("preset-personalizado") is not None
    pilot.click("export-custom-head")
    _settle(pilot)
    assert state.export_preset() == CUSTOM

    pilot.click("export-social-9x16")
    _settle(pilot)
    assert (state.export_custom_width(), state.export_custom_height()) == ("1080", "1920")
    state.export_custom_fps.set("24")
    _settle(pilot)
    labels = _texts(pilot)
    assert "1080×1920" in labels.get("export-summary", "")
    assert "24 fps" in labels.get("export-summary", "")
    assert "6 Mbps" in labels.get("export-summary", "")

    # Impar: error inline y el botón Exportar no arranca nada.
    state.export_custom_width.set("1081")
    _settle(pilot)
    labels = _texts(pilot)
    assert pilot.rect("export-custom-error") is not None
    assert "pares" in labels.get("export-custom-error", "")
    before = state.export_progress()
    pilot.click("export-start")
    _settle(pilot)
    assert state.export_progress() is before

    # FPS fuera de rango y bitrate con formato inválido: se explica y sigue bloqueado.
    state.export_custom_width.set("1080")
    state.export_custom_fps.set("999")
    _settle(pilot)
    assert "fps" in _texts(pilot).get("export-custom-error", "")
    state.export_custom_fps.set("24")
    state.export_custom_video_bitrate.set("mucho")
    _settle(pilot)
    assert "Bitrate de video" in _texts(pilot).get("export-custom-error", "")

    state.export_custom_video_bitrate.set("6M")
    _settle(pilot)
    assert pilot.rect("export-custom-error") is None
    assert "24 fps" in _texts(pilot).get("export-summary", "")
    # Los presets históricos siguen funcionando y en su orden.
    pilot.click("preset-calidad")
    _settle(pilot)
    assert state.export_preset() == "calidad"


def test_settings_persiste_y_el_editor_recupera_la_configuracion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    settings = Settings(
        export_preset=CUSTOM,
        export_custom_width=1080,
        export_custom_height=1350,
        export_custom_fps=24,
        export_custom_video_bitrate="8M",
        export_custom_audio_bitrate="128k",
        export_custom_codec="x264",
    ).normalized()
    settings.save()
    loaded = Settings.load()
    assert (loaded.export_preset, loaded.export_custom_width, loaded.export_custom_height) == (
        CUSTOM, 1080, 1350)
    assert (loaded.export_custom_fps, loaded.export_custom_video_bitrate,
            loaded.export_custom_audio_bitrate, loaded.export_custom_codec) == (
        24, "8M", "128k", "x264")

    # Valores fuera de rango o corruptos se normalizan al cargar.
    bad = Settings(export_custom_width=1079, export_custom_height=99999,
                   export_custom_fps=-4, export_custom_video_bitrate="nope",
                   export_custom_audio_bitrate="", export_custom_codec="av1",
                   export_preset="nope").normalized()
    assert (bad.export_custom_width, bad.export_custom_height) == (1078, 7680)
    assert bad.export_custom_fps == 1 and bad.export_custom_video_bitrate == "6M"
    assert bad.export_custom_audio_bitrate == "192k" and bad.export_custom_codec == "auto"
    assert bad.export_preset == "rapido"

    # Al cerrar, el editor vuelca los Signals; al arrancar de nuevo se recuperan.
    from recstudio.app import build_app

    app, controller = build_app(size=(1280, 800))
    try:
        assert state.export_preset() == CUSTOM
        assert state.export_custom_width() == "1080"
        assert state.export_custom_height() == "1350"
        assert state.export_custom_fps() == "24"
        assert state.export_custom_video_bitrate() == "8M"
        assert state.export_custom_audio_bitrate() == "128k"
    finally:
        controller.shutdown()
        state.export_open.set(False)
        state.export_progress.set(None)
        state.screen.set("record")
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert data["export_preset"] == CUSTOM
    assert data["export_custom_width"] == 1080 and data["export_custom_height"] == 1350
    assert data["export_custom_fps"] == 24 and data["export_custom_codec"] == "x264"
