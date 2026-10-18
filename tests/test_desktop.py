"""Tests de escritorio: atajos globales (hook con backend falso) y bandeja del sistema.

Nunca se instala un hook de teclado real ni se pone una bandeja de verdad: se inyectan dobles.
El estado de grabación y la ventana también se sustituyen por objetos mínimos.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from recstudio import state
from recstudio.platform import hotkeys as hk
from recstudio.platform import paths
from recstudio.services.settings import Settings
from recstudio.ui import tray


# -- atajos globales: platform/hotkeys.py ------------------------------------------


class _Listener:
    def __init__(self, mapping) -> None:  # noqa: ANN001
        self.mapping = mapping
        self.started = False
        self.stopped = False
        self.daemon = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class _Backend:
    """Doble de `pynput.keyboard`."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.listener: _Listener | None = None

    def GlobalHotKeys(self, mapping):  # noqa: N802, ANN001, ANN201
        if self.fail:
            raise RuntimeError("sin permisos")
        self.listener = _Listener(mapping)
        return self.listener


def test_hotkeys_instala_los_atajos_y_los_dispara() -> None:
    disparos: list[str] = []
    backend = _Backend()
    manager = hk.GlobalHotkeys(hk.BINDINGS, disparos.append, backend=backend)

    assert manager.start() is True
    assert manager.running
    assert backend.listener is not None and backend.listener.started
    assert backend.listener.daemon, "el hook no debe dejar el proceso colgado"
    assert set(backend.listener.mapping) == {"<f9>", "<f10>"}

    backend.listener.mapping["<f9>"]()  # como si el usuario pulsara F9
    backend.listener.mapping["<f10>"]()
    assert disparos == ["record", "pause"]

    manager.stop()
    assert backend.listener.stopped and not manager.running
    manager.stop()  # idempotente


def test_hotkeys_sin_backend_no_lanza_y_devuelve_false() -> None:
    manager = hk.GlobalHotkeys(hk.BINDINGS, lambda _name: None, backend=_Backend(fail=True))
    assert manager.start() is False
    assert not manager.running


def test_un_handler_que_falla_no_tumba_el_hook() -> None:
    backend = _Backend()

    def boom(_name: str) -> None:
        raise RuntimeError("vaya")

    manager = hk.GlobalHotkeys(hk.BINDINGS, boom, backend=backend)
    manager.start()
    assert backend.listener is not None
    backend.listener.mapping["<f9>"]()  # no debe propagar


# -- ajustes ------------------------------------------------------------------------


def test_ajustes_de_escritorio_por_defecto() -> None:
    s = Settings().normalized()
    assert s.global_hotkeys is False  # hook del sistema: opt-in
    assert s.tray is True
    assert s.notify_finished is True


# -- controlador --------------------------------------------------------------------


@pytest.fixture()
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    from recstudio.app import build_app

    app, controller = build_app(size=(1280, 800))
    pilot = app.pilot(size=(1280, 800))
    pilot.settle()
    try:
        yield app, controller
    finally:
        controller.shutdown()
        state.global_hotkeys.set(False)
        state.tray.set(True)
        state.notify_finished.set(True)
        state.rec_state.set("idle")
        state.screen.set("record")


def _hook(controller, backend: _Backend) -> hk.GlobalHotkeys:
    return hk.GlobalHotkeys(hk.BINDINGS, controller._on_hotkey, backend=backend)


def test_activar_atajos_globales_instala_y_quita_el_hook(ui, monkeypatch) -> None:
    _, controller = ui
    backend = _Backend()
    monkeypatch.setattr(controller, "_hotkeys", _hook(controller, backend))

    state.global_hotkeys.set(True)
    assert controller._hotkeys.running and backend.listener is not None
    assert backend.listener.started

    state.global_hotkeys.set(False)
    assert not controller._hotkeys.running
    assert backend.listener.stopped


def test_si_el_hook_falla_se_revierte_el_ajuste_y_se_avisa(ui, monkeypatch) -> None:
    _, controller = ui
    monkeypatch.setattr(controller, "_hotkeys", _hook(controller, _Backend(fail=True)))
    state.toasts.set(())

    state.global_hotkeys.set(True)

    assert state.global_hotkeys() is False, "el ajuste debe revertirse"
    assert any("atajos globales" in t.text.lower() for t in state.toasts())


def test_el_hook_global_llama_a_grabar_y_pausar(ui, monkeypatch) -> None:
    _, controller = ui
    llamadas: list[str] = []
    monkeypatch.setattr(controller, "toggle_record", lambda: llamadas.append("record"))
    monkeypatch.setattr(controller, "toggle_pause", lambda: llamadas.append("pause"))

    controller._on_hotkey("record")
    controller._on_hotkey("pause")
    controller._on_hotkey("desconocido")  # se ignora

    assert llamadas == ["record", "pause"]


# -- bandeja: ui/tray.py ------------------------------------------------------------


class _Window:
    def __init__(self) -> None:
        self.abierta = True

    def open(self) -> None:
        self.abierta = True


class _App:
    """Doble de `aurora.App` para la bandeja."""

    def __init__(self, *, falla: bool = False) -> None:
        self.falla = falla
        self.tray_calls: list[dict] = []
        self.notify_calls: list[tuple[str, str]] = []
        self.quits = 0
        self.tray_hidden = False
        self.main = _Window()

    def tray(self, icon, **kwargs) -> None:  # noqa: ANN001
        if self.falla:
            raise RuntimeError("sin bandeja")
        self.tray_calls.append({"icon": icon, **kwargs})

    def tray_hide(self) -> None:
        self.tray_hidden = True

    def notify(self, title, body="", **kwargs) -> None:  # noqa: ANN001
        self.notify_calls.append((title, body))

    def quit(self) -> None:
        self.quits += 1


def test_setup_de_bandeja_pasa_icono_menu_y_doble_clic(ui) -> None:
    _, controller = ui
    app = _App()

    assert tray.setup(app, controller) is True
    call = app.tray_calls[0]
    assert call["tooltip"] == "Rec Studio"
    assert Path(call["icon"]).is_file(), "el icono de la bandeja debe existir"
    assert call["menu"]
    call["on_double_click"]("left")
    assert app.main.abierta


def test_el_menu_de_la_bandeja_sigue_el_estado_de_grabacion(ui) -> None:
    app, controller = ui

    def labels() -> list[str]:
        return [item.get("label", "") for item in tray.menu(app, controller)]

    state.rec_state.set("idle")
    assert "Grabar (F9)" in labels()
    assert "Pausar (F10)" in labels()

    state.rec_state.set("recording")
    assert "Detener grabación (F9)" in labels()

    state.rec_state.set("paused")
    assert "Reanudar (F10)" in labels()


def test_las_opciones_del_menu_llaman_al_controlador(ui, monkeypatch) -> None:
    app, controller = ui
    llamadas: list[str] = []
    monkeypatch.setattr(controller, "toggle_record", lambda: llamadas.append("record"))
    monkeypatch.setattr(controller, "toggle_pause", lambda: llamadas.append("pause"))

    items = tray.menu(app, controller)
    for item in items:
        label = item.get("label", "")
        if label.startswith(("Grabar", "Detener")):
            item["action"]()
        elif label.startswith(("Pausar", "Reanudar")):
            item["action"]()
    assert llamadas == ["record", "pause"]


def test_setup_devuelve_false_si_el_sistema_no_admite_la_bandeja(ui) -> None:
    _, controller = ui
    assert tray.setup(_App(falla=True), controller) is False
    tray.hide(_App(falla=True))  # no lanza aunque no haya bandeja


def test_engancher_la_ventana_real_activa_o_quita_la_bandeja(ui, monkeypatch) -> None:
    app, controller = ui
    puestas: list[bool] = []
    monkeypatch.setattr(tray, "setup", lambda *a, **k: puestas.append(True) or True)
    monkeypatch.setattr(tray, "hide", lambda *a, **k: puestas.append(False))

    state.tray.set(True)
    controller.attach_desktop(app)
    assert puestas == [True] or puestas[-1] is True
    assert app.main.on_close == controller._on_main_close


# -- cierre de la ventana y avisos --------------------------------------------------


def test_cerrar_la_ventana_grabando_la_deja_en_la_bandeja(ui) -> None:
    _, controller = ui
    app = _App()
    controller._app = app
    state.tray.set(True)
    state.rec_state.set("recording")

    assert controller._on_main_close() is True  # se permite cerrar la ventana
    assert app.quits == 0, "la grabación debe seguir en la bandeja"


def test_cerrar_la_ventana_sin_grabar_termina_la_app(ui) -> None:
    _, controller = ui
    app = _App()
    controller._app = app
    state.rec_state.set("idle")

    assert controller._on_main_close() is True
    # `quit()` se aplaza fuera del callback de cierre
    deadline = time.perf_counter() + 2.0
    while app.quits == 0 and time.perf_counter() < deadline:
        time.sleep(0.01)
    assert app.quits == 1


def test_aviso_al_terminar_con_ventana(ui, tmp_path: Path) -> None:
    _, controller = ui
    app = _App()
    controller._app = app
    state.notify_finished.set(True)
    mp4 = tmp_path / "grabacion.mp4"
    mp4.write_bytes(b"x" * 2048)

    controller._notify_finished(SimpleNamespace(mp4=mp4, duration_s=3.2))

    assert app.notify_calls
    assert "grabacion.mp4" in app.notify_calls[0][1]


def test_aviso_al_terminar_apagado_o_sin_ventana(ui, tmp_path: Path) -> None:
    _, controller = ui
    mp4 = tmp_path / "grabacion.mp4"
    mp4.write_bytes(b"x")
    resultado = SimpleNamespace(mp4=mp4, duration_s=1.0)

    controller._notify_finished(resultado)  # sin `_app`: no lanza y no avisa

    app = _App()
    controller._app = app
    state.notify_finished.set(False)
    controller._notify_finished(resultado)
    assert app.notify_calls == []
