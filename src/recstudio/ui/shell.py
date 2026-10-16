"""Esqueleto de la app: cabecera, navegación, contenido y overlays."""

from __future__ import annotations

from aurora import box, column, image, row, spacer, text

from .. import state
from .components import chip, icon_button, modal_layer, take_input_key, toast_layer
from . import icons
from .timeline import NAV_W

_NAV = (
    ("record", "Grabar", icons.RECORD),
    ("library", "Biblioteca", icons.LIBRARY),
    ("editor", "Editor", icons.CUT),
    ("settings", "Ajustes", icons.SETTINGS),
)

_SCREENS = {}


def _screen_factory(name: str):
    if not _SCREENS:
        from .screens.editor import editor_screen
        from .screens.library import library_screen
        from .screens.record import record_screen
        from .screens.settings import settings_screen

        _SCREENS.update({
            "record": record_screen, "library": library_screen, "editor": editor_screen,
            "settings": settings_screen,
        })
    return _SCREENS.get(name, _SCREENS["record"])


def header():
    from .controller import CONTROLLER

    def toggle_theme() -> None:
        if CONTROLLER is not None:
            CONTROLLER.set_theme("light" if state.theme_name() == "dark" else "dark")

    rec = state.rec_state()
    recording = rec in ("recording", "paused", "starting", "stopping", "muxing")
    icon = icons.SUN if state.theme_name() == "dark" else icons.MOON

    return row(
        gap=12,
        padding=(10, 18),
        align="center",
        bg="$surface",
        border_width=0,
        shadow=(0, 2, 10, "$shadow"),
        grow=0,
        shrink=0,
    )[
        image(icons.CAMERA, width=20),
        text("Rec Studio", size=15, weight=700),
        chip("F3", tone="accent"),
        spacer(),
        recording and chip(
            ("⏸ pausado" if rec == "paused" else "● grabando"), tone="rec" if rec == "recording" else "muted"
        ),
        icon_button(icon, toggle_theme, tooltip="Cambiar tema", size=32),
    ]


def nav():
    def item(value: str, label: str, icon: bytes):
        active = state.screen() == value
        return row(
            key=f"nav-{value}",
            gap=10,
            padding=(9, 12),
            radius="$md",
            bg="$surface_hover" if active else "#00000000",
            bg_hover="$surface_hover",
            align="center",
            cursor="pointer",
            on_click=lambda: state.screen.set(value),
        )[
            image(icon, width=18),
            text(label, size=13.5, weight=600 if active else 500, color="$text" if active else "$muted"),
        ]

    return column(
        width=NAV_W,
        padding=(12, 12),
        gap=4,
        bg="$surface",
        border_width=0,
        shrink=0,
    )[[item(value, label, icon) for value, label, icon in _NAV]]


def content():
    factory = _screen_factory(state.screen())
    return factory()


def on_key(e) -> bool | None:
    from .controller import CONTROLLER
    from .editor_controller import EDITOR

    from_input = take_input_key()
    if CONTROLLER is None:
        return None
    if state.screen() == "editor" and EDITOR is not None:
        handled = EDITOR.on_key(e, from_input=from_input)
        if handled:
            return True
    if e.matches("f9"):
        CONTROLLER.toggle_record()
        return True
    if e.matches("f10"):
        CONTROLLER.toggle_pause()
        return True
    for index, (value, _, _) in enumerate(_NAV, start=1):
        if e.matches(f"mod+{index}"):
            state.screen.set(value)
            return True
    return None


def shell():
    from .region_editor import region_editor

    return column(grow=1, on_key=on_key)[
        header(),
        row(grow=1)[nav(), box(grow=1)[content()]],
        toast_layer(),
        modal_layer(),
        region_editor(),
    ]
