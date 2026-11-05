"""Accesibilidad de los controles: nombres que Aurora 0.10 exige para no avisar `no-label`.

Los avisos de layout (`aurora check`/`aurora tree`) usan estos nombres, pero aquí se comprueban
desde el árbol de accesibilidad público (`Pilot.a11y_tree`), el mismo que ve un lector de pantalla.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recstudio import state
from recstudio.platform import paths


@pytest.fixture()
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    from recstudio.app import build_app

    app, controller = build_app(size=(1280, 800))
    state.out_dir.set(str(tmp_path / "videos"))
    pilot = app.pilot(size=(1280, 800))
    pilot.settle()
    try:
        yield controller, pilot
    finally:
        controller.shutdown()
        state.screen.set("record")
        state.confirm.set(None)
        state.region_editing.set(False)


def _names(pilot) -> dict[str, str]:
    return {n["key"]: n.get("label") for n in pilot.a11y_tree() if n.get("key") and n.get("label")}


def test_grabar_nombra_iconos_campos_y_switches(ui) -> None:
    _, pilot = ui
    names = _names(pilot)
    assert names["sw-system"] == "Sistema"
    assert names["sw-keep"] == "Keep-alive"
    assert names["sel-kind"] == "Fuente"
    assert names["sel-monitor"] == "Monitor"
    assert names["out-dir"] == "Carpeta"
    assert "Cambiar tema" in names.values()  # icon_button deriva el label del tooltip


def test_ajustes_nombra_todos_los_controles(ui) -> None:
    _, pilot = ui
    state.screen.set("settings")
    pilot.settle()
    names = _names(pilot)
    for key, label in {
        "sw-theme": "Tema oscuro",
        "set-out-dir": "Carpeta de grabaciones",
        "set-system-audio": "Audio sistema",
        "set-keepalive": "Keep-alive",
        "set-hk-global": "Atajos globales",
        "set-tray": "Bandeja",
        "set-notify": "Aviso al terminar",
        "set-preview-audio": "Audio preview",
    }.items():
        assert names.get(key) == label
