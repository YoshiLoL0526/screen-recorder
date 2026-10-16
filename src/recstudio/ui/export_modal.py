"""Modal «Exportar» del editor: nombre, preset, resumen y progreso (ETA, cancelar, abrir).

Se puede cerrar con un export en marcha: sigue en su hilo y la barra del editor muestra el %
(`export_chip`). Reabrirlo (Ctrl+E) vuelve a enseñar el progreso.
"""

from __future__ import annotations

from aurora import box, button, column, component, row, spacer, text, text_input

from .. import state
from ..core import graph
from ..core import timeline as tl
from . import icons
from .components import chip, icon_button, mark_input_key


def _editor():
    from .editor_controller import EDITOR

    return EDITOR


def _call(name: str):
    def handler() -> None:
        editor = _editor()
        if editor is not None:
            getattr(editor, name)()

    return handler


def _eta(seconds: float | None) -> str:
    if seconds is None:
        return "calculando…"
    seconds = int(round(seconds))
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"


def progress_bar(fraction: float, *, color: str = "$primary", key: str | None = None):
    fraction = max(0.0, min(1.0, fraction))
    return row(key=key, height=8, radius="$full", bg="$surface_hover", shrink=0)[
        fraction > 0 and box(grow=fraction, width=0, radius="$full", bg=color),
        fraction < 1 and box(grow=1 - fraction, width=0),
    ]


@component
def export_chip():
    """Chip de la barra del editor mientras exporta con el modal cerrado."""
    progress = state.export_progress()
    if progress is None or not progress.active or state.export_open():
        return box()
    return box(on_click=_call("open_export"), cursor="pointer", key="ed-export-chip")[
        chip(f"exportando {progress.fraction * 100:.0f} %", tone="accent")
    ]


def _preset_card(preset: graph.ExportPreset, selected: bool, blocked: tuple[str, ...]):
    disabled = preset.video == "copy" and bool(blocked)
    detail = ("No disponible: " + "; ".join(blocked)) if disabled else preset.description
    return row(
        key=f"preset-{preset.id}", gap=10, padding=(9, 12), radius="$md", align="center",
        bg="$surface_pressed" if selected else "$surface_hover",
        bg_hover="$surface_pressed", border_width=1, border_color="$primary" if selected else "$border",
        cursor="default" if disabled else "pointer", opacity=0.5 if disabled else 1.0,
        on_click=None if disabled else (lambda: state.export_preset.set(preset.id)),
    )[
        box(width=14, height=14, radius="$full", border_width=2,
            border_color="$primary" if selected else "$muted", align="center", justify="center", shrink=0)[
            selected and box(width=6, height=6, radius="$full", bg="$primary")
        ],
        column(grow=1, width=0, gap=2)[
            text(preset.label, size=13, weight=600),
            text(detail, size=11.5, color="$muted", line_height=1.35),
        ],
    ]


def _summary(project, preset: graph.ExportPreset) -> str:
    total = tl.total_duration(project)
    if preset.video == "copy":
        return f"{tl.format_time(total)} · resolución y códec originales (sin pérdida)"
    w, h = graph.output_size(project, preset)
    audio = " + AAC " + preset.audio_bitrate if graph.project_has_audio(project) else " · sin audio"
    return f"{w}×{h} · {project.fps} fps · {tl.format_time(total)} · H.264{audio}"


def _progress_block(progress):
    if progress is None:
        return None
    if progress.active:
        pct = progress.fraction * 100
        stage = {"preparing": "Preparando…", "rendering": "Exportando", "verifying": "Verificando…"}[progress.state]
        return column(gap=8, key="export-progress")[
            row(align="center", gap=8)[
                text(stage, size=13, weight=600),
                progress.encoder and chip(progress.encoder, tone="muted"),
                spacer(),
                text(f"{pct:.0f} %", size=13, weight=700, family="Consolas", key="export-pct"),
            ],
            progress_bar(progress.fraction, key="export-bar"),
            row(gap=10)[
                text(f"Quedan {_eta(progress.eta_s)}", size=11.5, color="$muted"),
                progress.speed > 0 and text(f"· {progress.speed:.1f}× tiempo real", size=11.5, color="$muted"),
                spacer(),
                text(f"{progress.elapsed:.0f} s", size=11.5, color="$muted", family="Consolas"),
            ],
            progress.message and text(progress.message, size=11.5, color="$muted"),
        ]
    if progress.state == "done":
        from pathlib import Path

        return column(gap=8, key="export-done", padding=12, radius="$md", bg="$surface_hover")[
            row(gap=8, align="center")[
                text("✓", size=15, weight=700, color="$success"),
                text(f"Exportado: {Path(progress.out_path).name}", size=13, weight=600, max_lines=1, grow=1, width=0),
            ],
            text(f"{progress.duration:.2f} s de video en {progress.elapsed:.1f} s · {progress.encoder}"
                 + (f" · {progress.message}" if progress.message else ""),
                 size=11.5, color="$muted"),
        ]
    tone = "$danger" if progress.state == "error" else "$muted"
    return box(padding=12, radius="$md", bg="$surface_hover", key="export-failed")[
        text(progress.message, size=12.5, color=tone, line_height=1.4)
    ]


@component
def export_modal():
    if not state.export_open():
        return column()
    project = state.editor_project()
    preset_id = state.export_preset()
    preset = graph.preset_by_id(preset_id)
    blocked = state.export_blockers()
    progress = state.export_progress()
    running = progress is not None and progress.active
    finished = progress is not None and progress.state == "done"
    close = _call("close_export")

    if running:
        actions = [button("Cancelar export", _call("cancel_export"), variant="soft", key="export-cancel"),
                   button("Ocultar", close, key="export-hide")]
    elif finished:
        actions = [button("Mostrar en carpeta", _call("reveal_export"), variant="soft", key="export-reveal"),
                   button("Reproducir", _call("play_export"), variant="soft", key="export-play"),
                   button("Listo", close, key="export-close")]
    else:
        actions = [button("Cancelar", close, variant="soft", key="export-dismiss"),
                   button("Exportar" if progress is None else "Reintentar", _call("start_export"),
                          key="export-start")]

    return column(layer="overlay", position="absolute", left=0, right=0, top=0, bottom=0, align="center",
                  justify="center", capture=True)[
        box(position="absolute", left=0, right=0, top=0, bottom=0, bg="#00000080",
            on_click=None if running else close)[None],
        column(position="absolute", width=540, padding=20, gap=14, radius="$lg", bg="$surface",
               border_width=1, border_color="$border", shadow=(0, 20, 50, "$shadow"), key="export-modal")[
            row(align="center", gap=10)[
                text("Exportar", size=17, weight=700),
                text(project.name, size=12.5, color="$muted", max_lines=1, grow=1, width=0),
                icon_button(icons.CLOSE, close, tooltip="Cerrar (Esc)", size=28, key="export-x"),
            ],
            column(gap=6)[
                text("Nombre del archivo", size=12, color="$muted"),
                row(gap=8, align="center")[
                    text_input(state.export_name, key="export-name", on_key=mark_input_key, grow=1, width=0,
                               padding=(7, 10), on_submit=lambda _v: None if running else _call("start_export")()),
                    text(".mp4", size=12.5, color="$muted", family="Consolas"),
                ],
                row(gap=6, align="center")[
                    text(f"En {state.exports_dir()}", size=11, color="$muted", max_lines=1, grow=1, width=0),
                    icon_button(icons.FOLDER, _call("reveal_export"), tooltip="Abrir la carpeta", size=24),
                ],
            ],
            not running and not finished and column(gap=6)[
                [_preset_card(p, p.id == preset_id, blocked) for p in graph.PRESETS]
            ],
            not finished and text(_summary(project, preset), size=11.5, color="$muted", key="export-summary"),
            _progress_block(progress),
            row(justify="end", gap=10)[actions],
        ],
    ]
