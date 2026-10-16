"""Pantalla de ajustes: apariencia, salida y «Acerca de»."""

from __future__ import annotations

from aurora import card, column, component, divider, row, scroll, spacer, switch, text, text_input

from ... import state
from ...platform import ffmpeg as ffmpeg_mod
from ...platform import paths
from ...services import library
from ..components import field, icon_button, section
from .. import icons


@component
def settings_screen():
    from ..controller import CONTROLLER

    def toggle_theme(dark: bool) -> None:
        if CONTROLLER is not None:
            CONTROLLER.set_theme("dark" if dark else "light")

    def open_config() -> None:
        library.reveal_in_explorer(paths.app_dir())

    version_text = state.ffmpeg_version() or "detectando…"

    return scroll(grow=1, padding=(24, 28), gap=16, key="settings-scroll")[
        column(gap=16, max_width=760)[
            text("Ajustes", size=22, weight=700),

            card(gap=14)[
                section(
                    "Apariencia",
                    field("Tema oscuro", switch(state.theme_name() == "dark", on_change=toggle_theme,
                                                key="sw-theme")),
                ),
            ],

            card(gap=14)[
                section(
                    "Grabación",
                    field("Carpeta", text_input(state.out_dir, key="set-out-dir")),
                    field("Keep-alive de audio", switch(state.keepalive, key="set-keepalive")),
                    text(
                        "El keep-alive reproduce silencio por la salida por defecto para que el audio "
                        "del sistema se siga grabando aunque no suene nada (medido en F0: cobertura "
                        "14.7 % → 99.96 %).",
                        size=12,
                        color="$muted",
                        line_height=1.5,
                    ),
                ),
            ],

            card(gap=14)[
                section(
                    "Acerca de",
                    row(gap=10, align="center")[
                        icon_button(icons.INFO, open_config, tooltip="Abrir la carpeta de configuración"),
                        column(gap=2)[
                            text("Rec Studio · F2 (UI de grabación + biblioteca)", size=13, weight=600),
                            text(version_text, size=11.5, color="$muted", max_lines=2),
                        ],
                        spacer(),
                    ],
                    divider(),
                    text(
                        "El encoder/render usa el binario GPL de FFmpeg (imageio-ffmpeg) como proceso "
                        "separado; su licencia no afecta al código de la app (docs/07, ADR-02).",
                        size=11.5,
                        color="$muted",
                        line_height=1.5,
                    ),
                ),
            ],
        ]
    ]
