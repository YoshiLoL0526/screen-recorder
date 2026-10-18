"""Bandeja del sistema: icono, menú, «seguir grabando al cerrar» y avisos.

La bandeja la pone Aurora (`app.tray`) y mantiene la app viva aunque no queden ventanas; el icono
va incluido en el bundle por `[tool.aurora.build]` (`include = ["assets"]`) y se resuelve con
`aurora.resource`, que funciona igual con Python y empaquetado.

Aurora no reconstruye el menú solo: el controlador vuelve a llamar a `setup()` cuando cambia el
estado de la grabación, para que las etiquetas («Grabar»/«Detener») sigan a la realidad.
"""

from __future__ import annotations

from pathlib import Path

from aurora import App, menus, resource

from .. import state

ICON = "assets/icon.png"

#: Estados con una grabación viva: cerrar la ventana la deja grabando en la bandeja.
LIVE_STATES = ("starting", "recording", "paused", "stopping", "muxing")


def icon_path() -> str:
    """Ruta del icono: `aurora.resource` (correcta empaquetada) con reserva junto al paquete."""
    candidate = Path(resource(ICON))
    if candidate.is_file():
        return str(candidate)
    local = Path(__file__).resolve().parents[3] / ICON
    return str(local if local.is_file() else candidate)


def menu(app: App, controller) -> list[dict]:
    """Menú de la bandeja, según el estado actual de la grabación."""
    rec = state.rec_state()
    live = rec in LIVE_STATES
    can_pause = rec in ("recording", "paused")
    return [
        menus.item("Abrir Rec Studio", lambda: app.main.open()),
        menus.separator(),
        menus.item("Detener grabación (F9)" if live else "Grabar (F9)", controller.toggle_record),
        menus.item(
            "Reanudar (F10)" if rec == "paused" else "Pausar (F10)",
            controller.toggle_pause,
            enabled=can_pause,
        ),
        menus.separator(),
        menus.item("Salir", app.quit),
    ]


def setup(app: App, controller) -> bool:
    """Pone (o reemplaza) la bandeja. `False` si el sistema no la admite."""
    try:
        app.tray(
            icon_path(),
            tooltip="Rec Studio",
            menu=menu(app, controller),
            # Con menú, el clic izquierdo lo abre; el doble clic llega a la app.
            on_double_click=lambda _button: app.main.open(),
        )
    except Exception:
        return False
    return True


def hide(app: App) -> None:
    """Quita el icono (la app vuelve a terminar con su última ventana)."""
    try:
        app.tray_hide()
    except Exception:
        pass


def notify(app: App, title: str, body: str = "") -> None:
    """Aviso del sistema. Nunca lanza: si el sistema no los admite, la app sigue."""
    try:
        app.notify(title, body, icon=icon_path(), on_click=app.main.open)
    except Exception:
        pass
