"""Pantalla de ajustes: apariencia, grabación, editor, sistema y «Acerca de».

Todo lo que se cambia aquí son los mismos `Signal` que usan las otras pantallas: el controlador
los persiste con debounce en `settings.json` (no hay botón «Guardar»).
"""

from __future__ import annotations

from aurora import button, card, column, component, divider, row, scroll, select, spacer, switch, text, text_input

from ... import __version__, state
from ...core.graph import PRESETS
from ...platform import paths
from ...services import library
from ..components import field, icon_button, section
from .. import icons
from .record import COUNTDOWN_OPTIONS, FPS_OPTIONS, PRESET_OPTIONS, encoder_options


def _controller():
    from ..controller import CONTROLLER

    return CONTROLLER


def _call(name: str, *args):
    def handler() -> None:
        controller = _controller()
        if controller is not None:
            getattr(controller, name)(*args)

    return handler


def _note(value: str):
    return text(value, size=12, color="$muted", line_height=1.5)


@component
def _system_card():
    encoders = state.encoders()
    working = [name for name, ok in encoders.items() if ok]
    version_text = state.ffmpeg_version() or "detectando…"
    return card(gap=14, key="set-system")[
        section(
            "Sistema",
            field("FFmpeg", text(version_text, size=12, color="$muted", max_lines=2)),
            field("Encoders", text(
                " · ".join(working) if working else ("probando…" if not encoders else "ninguno disponible"),
                size=12, color="$muted", font_family="Consolas",
            )),
            field("Monitores", text(
                " · ".join(f"{m.index}: {m.width}×{m.height}" for m in state.monitors()) or "detectando…",
                size=12, color="$muted",
            )),
            _note("Los encoders se prueban codificando de verdad al arrancar (AMF/QSV pueden aparecer en "
                  "FFmpeg y no funcionar sin el hardware) y el resultado se guarda en caché."),
        ),
    ]


@component
def settings_screen():
    controller = _controller()
    editor = controller.editor if controller is not None else None

    def toggle_theme(dark: bool) -> None:
        if controller is not None:
            controller.set_theme("dark" if dark else "light")

    def toggle_preview_audio(enabled: bool) -> None:
        if editor is not None:
            editor.set_preview_audio(enabled, notify=False)

    def open_config() -> None:
        library.reveal_in_explorer(paths.app_dir())

    return scroll(grow=1, padding=(24, 28), gap=16, key="settings-scroll")[
        column(gap=16, max_width=780)[
            row(align="center", gap=10)[
                text("Ajustes", size=22, weight=700),
                spacer(),
                text("los cambios se guardan solos", size=12, color="$muted"),
            ],

            card(gap=14, key="set-appearance")[
                section(
                    "Apariencia",
                    field("Tema oscuro", switch(state.theme_name() == "dark", on_change=toggle_theme,
                                                key="sw-theme")),
                ),
            ],

            card(gap=14, key="set-recording")[
                section(
                    "Grabación",
                    field("Carpeta", row(gap=8, align="center")[
                        text_input(state.out_dir, key="set-out-dir", grow=1, width=0),
                        button("Cambiar…", _call("browse_out_dir"), variant="soft", padding=(6, 12),
                               font_size=12.5, key="set-browse-out"),
                        icon_button(icons.FOLDER, _call("open_out_dir"), tooltip="Abrir la carpeta", size=32),
                    ]),
                    field("FPS", select(state.fps_target, list(FPS_OPTIONS), key="set-fps")),
                    field("Calidad", select(state.preset, list(PRESET_OPTIONS), key="set-preset")),
                    field("Encoder", select(state.encoder, encoder_options(), key="set-encoder")),
                    field("Cuenta atrás", select(state.countdown_s, list(COUNTDOWN_OPTIONS), key="set-countdown")),
                    divider(),
                    field("Audio sistema", switch(state.audio_system, key="set-system-audio")),
                    field("Keep-alive", switch(state.keepalive, key="set-keepalive")),
                    _note("El keep-alive reproduce silencio por la salida por defecto para que el audio del "
                          "sistema se siga grabando aunque no suene nada (medido en F0: cobertura "
                          "14.7 % → 99.96 %). Atajos globales de la ventana: F9 grabar/detener, F10 pausar."),
                ),
            ],

            card(gap=14, key="set-editor")[
                section(
                    "Editor",
                    field("Audio preview", switch(state.preview_audio(), on_change=toggle_preview_audio,
                                                  key="set-preview-audio")),
                    field("Export", select(state.export_preset, [(p.id, p.label) for p in PRESETS],
                                           key="set-export-preset")),
                    row(gap=10, align="center")[
                        _note("Los divisores del editor (preview ↔ inspector ↔ timeline) se recuerdan entre "
                              "sesiones."),
                        spacer(),
                        button("Restablecer disposición", _call("reset_editor_layout"), variant="soft",
                               padding=(6, 12), font_size=12.5, key="set-reset-layout"),
                    ],
                ),
            ],

            _system_card(),

            card(gap=14, key="set-about")[
                section(
                    "Acerca de",
                    row(gap=10, align="center")[
                        icon_button(icons.INFO, open_config, tooltip="Abrir la carpeta de configuración"),
                        column(gap=2)[
                            text(f"Rec Studio {__version__}", size=13, weight=600),
                            text("Grabador de pantalla y editor de video · interfaz con aurora-ui",
                                 size=11.5, color="$muted"),
                        ],
                        spacer(),
                        button("Restablecer ajustes…", _call("ask_reset_settings"), variant="ghost",
                               padding=(6, 12), font_size=12.5, key="set-reset"),
                    ],
                    divider(),
                    _note("El encoder/render usa el binario GPL de FFmpeg (imageio-ffmpeg) como proceso "
                          "separado; su licencia no afecta al código de la app (docs/07, ADR-02)."),
                ),
            ],
        ]
    ]
