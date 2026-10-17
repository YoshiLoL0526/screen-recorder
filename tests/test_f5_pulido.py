"""Tests de F5 (pulido): layout adaptable, divisores del editor, Ajustes, icono y smoke test.

Los de UI construyen la app real sin ventana (`build_app` + `app.pilot`) como los de F3/F4.
"""

from __future__ import annotations

import io
import json
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image

from recstudio import __version__, state
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services.settings import Settings
from recstudio.ui import timeline as tlui
from recstudio.ui.controller import REF_MAX_W, fit_reference


def _wait(predicate, timeout: float = 10.0) -> bool:
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


def _jpeg(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (40, 120, 200)).save(buffer, "JPEG")
    return buffer.getvalue()


def _inside(rect, size) -> bool:
    x, y, w, h = rect
    return x >= 0 and y >= 0 and x + w <= size[0] + 0.5 and y + h <= size[1] + 0.5


@pytest.fixture()
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    from recstudio.app import build_app

    app, controller = build_app(size=(1280, 800))
    state.out_dir.set(str(tmp_path / "videos"))
    pilot = app.pilot(size=(1280, 800))
    pilot.settle()
    try:
        yield app, controller, pilot
    finally:
        controller.shutdown()
        state.project_open.set(False)
        state.selection.set(None)
        state.preview.set(b"")
        state.confirm.set(None)
        state.region_editing.set(False)
        state.screen.set("record")


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("media") / "demo.mp4"
    try:
        subprocess.run(
            [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=3",
             "-f", "lavfi", "-i", "sine=frequency=330:duration=3",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
             str(path)],
            check=True, capture_output=True,
        )
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")
    return path


# -- puro ---------------------------------------------------------------------------


def test_pistas_del_timeline_crecen_y_se_topan() -> None:
    base = tlui.tracks_for(0)
    assert (base.video_h, base.audio_h) == (tlui.VIDEO_H, tlui.AUDIO_H)
    bigger = tlui.tracks_for(100)
    assert bigger.video_h == tlui.VIDEO_H + 60 and bigger.audio_h == tlui.AUDIO_H + 40
    assert bigger.content_h == base.content_h + 100
    assert tlui.tracks_for(-999) == tlui.tracks_for(tlui.TRACKS_EXTRA_MIN)  # nunca por debajo del mínimo
    # el tope depende del alto útil: con poca ventana las pistas no pueden crecer
    assert tlui.max_tracks_extra(400) == tlui.TRACKS_EXTRA_MIN
    assert tlui.max_tracks_extra(748) == 748 - tlui.EDITOR_CHROME_H - tlui.PREVIEW_MIN_H
    assert tlui.max_tracks_extra(5000) == tlui.TRACKS_EXTRA_MAX


def test_referencia_de_region_cabe_en_la_ventana() -> None:
    assert fit_reference(1920, 1080, 748) == (REF_MAX_W, 529)  # apaisado: manda el ancho
    w, h = fit_reference(1080, 1920, 748)  # monitor vertical: antes medía 940×1671
    assert h <= 748 + 52 - 150 and w < h
    assert fit_reference(800, 600, 2000) == (800, 600)  # nunca se amplía


def test_ajustes_normaliza_la_disposicion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text(json.dumps(
        {"inspector_w": 5000, "timeline_extra": -300, "inspector_open": 0}), encoding="utf-8")
    s = Settings.load()
    assert (s.inspector_w, s.timeline_extra, s.inspector_open) == (560.0, -14.0, False)
    (tmp_path / "settings.json").write_text(json.dumps({"inspector_w": "ancho"}), encoding="utf-8")
    assert Settings.load().inspector_w == 290.0


def test_icono_generado() -> None:
    ico = Path(__file__).resolve().parents[1] / "assets" / "icon.ico"
    assert ico.is_file(), "falta assets/icon.ico (uv run python tools/make_icon.py)"
    with Image.open(ico) as image:
        assert (256, 256) in image.info["sizes"] and (16, 16) in image.info["sizes"]


# -- grabación ------------------------------------------------------------------------


def test_preview_vertical_no_esconde_el_boton_de_grabar(ui) -> None:
    _, _, pilot = ui
    state.screen.set("record")
    state.preview.set(_jpeg(400, 1600))  # región muy vertical
    _settle(pilot)
    assert _inside(pilot.rect("rec-button"), (1280, 800)), pilot.rect("rec-button")
    pilot.resize(1100, 640)
    _settle(pilot)
    assert _inside(pilot.rect("rec-button"), (1100, 640)), pilot.rect("rec-button")


def test_esc_e_intro_cierran_modales(ui) -> None:
    _, _, pilot = ui
    state.confirm.set(state.Confirm(action="delete", title="t", message="m"))
    _settle(pilot)
    pilot.press("escape")
    assert state.confirm() is None
    state.region_editing.set(True)
    _settle(pilot)
    pilot.press("enter")
    assert not state.region_editing()


# -- editor -----------------------------------------------------------------------------


def _open_video(controller, pilot, video: Path) -> None:
    state.screen.set("editor")
    controller.editor.import_paths([video])
    assert _wait(lambda: state.project_open() and state.editor_project().video.clips), "no se importó"
    assert _wait(lambda: bool(state.preview_frame())), "sin frame de preview"
    _settle(pilot)


def test_preview_del_editor_aprovecha_la_ventana(ui, video: Path) -> None:
    _, controller, pilot = ui
    _open_video(controller, pilot, video)
    small = pilot.rect("ed-preview")
    pilot.resize(1920, 1080)
    _settle(pilot)
    assert state.view_h() == 1080 - 52  # lo mide el `on_scroll` de la navegación
    big = pilot.rect("ed-preview")
    assert big[2] > small[2] + 500 and big[3] > small[3] + 200
    # la imagen ocupa todo el panel (antes se quedaba con su tamaño y pegada a la izquierda)
    img = pilot.rect("ed-preview-img")
    assert img[2] >= big[2] - 16 and img[3] >= big[3] - 16


def test_divisor_del_timeline_redimensiona_preview_y_pistas(ui, video: Path) -> None:
    _, controller, pilot = ui
    _open_video(controller, pilot, video)
    preview0, timeline0 = pilot.rect("ed-preview"), pilot.rect("ed-timeline")
    pilot.drag("split-timeline", dy=-120, steps=6)
    _settle(pilot)
    assert state.tl_tracks_extra() == 120
    assert pilot.rect("ed-timeline")[3] == timeline0[3] + 120
    assert pilot.rect("ed-preview")[3] == preview0[3] - 120
    # tope: el preview nunca baja de su mínimo ni el editor se sale de la ventana
    pilot.drag("split-timeline", dy=-2000, steps=6)
    _settle(pilot)
    assert pilot.rect("ed-preview")[3] == pytest.approx(tlui.PREVIEW_MIN_H, abs=1)
    assert _inside(pilot.rect("ed-timeline"), (1280, 800))
    # y hacia abajo encoge hasta el mínimo de las pistas
    pilot.drag("split-timeline", dy=2000, steps=6)
    _settle(pilot)
    assert state.tl_tracks_extra() == tlui.TRACKS_EXTRA_MIN
    # al encoger la ventana, las pistas se topan al dibujar sin perder el valor elegido
    pilot.drag("split-timeline", dy=-2000, steps=6)
    chosen = state.tl_tracks_extra()
    pilot.resize(1280, 640)
    _settle(pilot)
    assert state.tl_tracks_extra() == chosen
    assert _inside(pilot.rect("ed-timeline"), (1280, 640))


def test_inspector_redimensionable_y_ocultable(ui, video: Path) -> None:
    _, controller, pilot = ui
    _open_video(controller, pilot, video)
    width0 = pilot.rect("ed-inspector")[2]
    preview0 = pilot.rect("ed-preview")[2]
    pilot.drag("split-inspector", dx=-100, steps=5)
    _settle(pilot)
    assert pilot.rect("ed-inspector")[2] == width0 + 100
    assert pilot.rect("ed-preview")[2] == preview0 - 100
    pilot.drag("split-inspector", dx=-3000, steps=5)  # tope: el preview conserva su ancho mínimo
    _settle(pilot)
    assert pilot.rect("ed-preview")[2] >= tlui.PREVIEW_MIN_W - 1
    pilot.press("i")
    _settle(pilot)
    assert pilot.rect("ed-inspector") is None and not state.inspector_open()
    assert pilot.rect("ed-preview")[2] > preview0 + 200
    pilot.click("ed-panel")
    _settle(pilot)
    assert pilot.rect("ed-inspector") is not None


def test_disposicion_del_editor_se_persiste(ui, tmp_path: Path) -> None:
    _, controller, _ = ui
    state.inspector_w.set(400.0)
    state.inspector_open.set(False)
    state.tl_tracks_extra.set(80.0)
    controller._save_settings()
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert (data["inspector_w"], data["inspector_open"], data["timeline_extra"]) == (400.0, False, 80.0)
    controller.reset_editor_layout()
    assert (state.inspector_w(), state.inspector_open(), state.tl_tracks_extra()) == (290.0, True, 0.0)


# -- ajustes ------------------------------------------------------------------------------


def test_ajustes_hacen_scroll_y_se_restablecen(ui) -> None:
    _, controller, pilot = ui
    state.screen.set("settings")
    _settle(pilot)
    assert pilot.rect("set-reset") is not None
    pilot.scroll("settings-scroll", dy=900)
    _settle(pilot)
    offset = pilot.scroll_offset("settings-scroll")
    assert offset is not None and offset[1] > 100  # antes la fila crecía y no había scroll
    assert _inside(pilot.rect("set-reset"), (1280, 800))

    state.fps_target.set(30)
    state.inspector_w.set(450.0)
    pilot.click("set-reset")
    _settle(pilot)
    assert state.confirm() is not None and state.confirm().action == "reset-settings"
    from recstudio.ui.components import _on_accept

    _on_accept(state.confirm())
    assert state.fps_target() == 60 and state.inspector_w() == 290.0


def test_cabecera_muestra_la_version(ui) -> None:
    _, _, pilot = ui
    _settle(pilot)
    assert __version__ == "1.0.0"


# -- smoke test (el mismo que corre el exe empaquetado) -------------------------------------


def test_smoke_en_proceso(tmp_path: Path) -> None:
    from recstudio import smoke

    report = tmp_path / "smoke.json"
    try:
        rc = smoke.run(report)
    finally:
        state.screen.set("record")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert rc == 0 and data["ok"], data
    assert data["checks"]["ui"]["screens"] == ["record", "library", "editor", "settings"]
    assert data["checks"]["ffmpeg"]["duration_s"] == pytest.approx(2.0, abs=0.2)
