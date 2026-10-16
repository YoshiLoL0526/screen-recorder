"""Composición de la app: controlador + ventana Aurora."""

from __future__ import annotations

from aurora import App

from . import state
from .services.settings import Settings
from .ui.controller import Controller
from .ui.shell import shell


def build_app(*, size: tuple[float, float] = (1280, 800)) -> tuple[App, Controller]:
    """Crea la app y arranca los servicios (sin abrir la ventana todavía)."""
    settings = Settings.load()
    controller = Controller(settings)
    controller.boot()
    app = App(shell, title="Rec Studio", size=size, theme=state.theme)
    return app, controller


def main() -> None:
    app, controller = build_app()
    try:
        app.run()
    finally:
        controller.shutdown()


if __name__ == "__main__":
    main()
