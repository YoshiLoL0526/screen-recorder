"""Tests de F2.1: cuenta atrás y editor de región (con Pilot, sin ventana)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from recstudio import state
from recstudio.app import build_app
from recstudio.platform import paths


@pytest.fixture()
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """App real con los ajustes aislados en un fichero temporal."""
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    app, controller = build_app(size=(1280, 800))
    pilot = app.pilot(size=(1280, 800))
    pilot.settle()
    try:
        yield app, controller, pilot
    finally:
        controller.close_region_editor()
        controller.shutdown()
        state.countdown.set(0)
        state.countdown_s.set(0)
        state.source_kind.set("monitor")
        state.screen.set("record")


def _wait(predicate, timeout: float = 8.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_cuenta_atras_se_cancela_sin_grabar(ui) -> None:
    _, controller, _ = ui
    state.countdown_s.set(3)
    controller.toggle_record()
    assert _wait(lambda: state.countdown() > 0, 3), "la cuenta atrás no arrancó"
    controller.toggle_record()  # cancelar
    assert _wait(lambda: state.countdown() == 0, 3)
    time.sleep(1.2)
    assert controller.session is None  # cancelada: no se inició ninguna grabación
    assert state.rec_state() == "idle"


def test_editor_de_region_mueve_y_redimensiona(ui) -> None:
    _, controller, pilot = ui
    state.source_kind.set("region")
    state.set_region((100, 150, 800, 600))
    pilot.settle()

    controller.open_region_editor()
    if not _wait(lambda: state.region_editing(), 10):
        pytest.skip("la captura de pantalla no está disponible en este entorno")
    pilot.settle()

    display_w, display_h = state.region_ref_size()
    bounds = state.region_bounds()
    assert display_w > 0 and bounds[0] > 0

    # Región de partida con margen en las dos direcciones: en un monitor pequeño (p. ej.
    # 1366×768) la región inicial ya toca el borde inferior, `resize_region` la recorta y
    # el arrastre de la esquina se queda en un no-op (ver docs/05).
    w = min(800, bounds[0] * 2 // 3)
    h = min(600, bounds[1] * 2 // 3)
    state.set_region((bounds[0] // 8, bounds[1] // 8, w, h))
    pilot.settle()

    before = state.region()
    rect = pilot.rect("region-body")
    assert rect is not None
    cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    pilot.move_to(cx, cy)
    pilot.mouse_down()
    pilot.move_to(cx + 80, cy + 40)
    pilot.mouse_up()
    pilot.settle()
    moved = state.region()
    assert moved != before, "el arrastre no movió la región"
    assert moved[2] == before[2] and moved[3] == before[3], "mover no debe cambiar el tamaño"
    # los campos de texto siguen la región
    assert state.region_x() == str(moved[0]) and state.region_w() == str(moved[2])

    # redimensionar por la esquina inferior derecha
    rect = pilot.rect("region-br")
    assert rect is not None
    cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    pilot.move_to(cx, cy)
    pilot.mouse_down()
    pilot.move_to(cx + 60, cy + 50)
    pilot.mouse_up()
    pilot.settle()
    resized = state.region()
    assert resized[2] > moved[2] and resized[3] > moved[3]
    assert resized[0] == moved[0] and resized[1] == moved[1]  # la esquina opuesta no se mueve

    controller.close_region_editor()
    assert state.region_editing() is False


def test_region_nunca_sale_del_monitor(ui) -> None:
    _, controller, pilot = ui
    state.source_kind.set("region")
    state.set_region((100, 100, 800, 600))
    controller.open_region_editor()
    if not _wait(lambda: state.region_editing(), 10):
        pytest.skip("la captura de pantalla no está disponible en este entorno")
    pilot.settle()

    rect = pilot.rect("region-body")
    assert rect is not None
    cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    pilot.move_to(cx, cy)
    pilot.mouse_down()
    pilot.move_to(cx + 4000, cy + 4000)  # muy fuera
    pilot.mouse_up()
    pilot.settle()

    x, y, w, h = state.region()
    bounds_w, bounds_h = state.region_bounds()
    assert 0 <= x and 0 <= y
    assert x + w <= bounds_w and y + h <= bounds_h
