"""Modal «Exportar» del editor: nombre, preset, resumen y progreso (ETA, cancelar, abrir).

Se puede cerrar con un export en marcha: sigue en su hilo y la barra del editor muestra el %
(`export_chip`). Reabrirlo (Ctrl+E) vuelve a enseñar el progreso.
"""

from __future__ import annotations

from aurora import box, button, column, component, row, scroll, select, spacer, text, text_input

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


def _radio(selected: bool):
    return box(width=14, height=14, radius="$full", border_width=2,
               border_color="$primary" if selected else "$muted", align="center", justify="center",
               shrink=0)[
        selected and box(width=6, height=6, radius="$full", bg="$primary")
    ]


def _select_format(value: str) -> None:
    """Elige el contenedor; «sin recodificar» solo existe en MP4 (docs/48)."""
    state.export_format.set(value)
    if value != "mp4" and state.export_preset() == "copia":
        state.export_preset.set("rapido")


def _select_preset(preset: graph.ExportPreset) -> None:
    """Elegir «sin recodificar» vuelve a MP4 para no dejar una combinación inválida."""
    if preset.video == "copy":
        state.export_format.set("mp4")
    state.export_preset.set(preset.id)


def _format_choice(selected: str):
    def option(value: str):
        active = value == selected
        return box(
            key=f"export-format-{value}", padding=(6, 12), radius="$md", cursor="pointer",
            bg="$surface_pressed" if active else "$surface_hover", border_width=1,
            border_color="$primary" if active else "$border",
            on_click=lambda: _select_format(value),
        )[text(value.upper(), size=12.5, weight=600 if active else 500)]

    return row(gap=8, align="center", key="export-format")[
        text("Formato", size=12, color="$muted"),
        option("mp4"),
        option("webm"),
        option("gif"),
    ]


def _preset_card(preset: graph.ExportPreset, selected: bool, blocked: tuple[str, ...],
                 fmt: str):
    only_mp4 = preset.video == "copy" and fmt != "mp4"
    disabled = preset.video == "copy" and (bool(blocked) or only_mp4)
    if only_mp4:
        detail = "Solo disponible en MP4: cambia el formato para usar el corte rápido."
    else:
        detail = ("No disponible: " + "; ".join(blocked)) if disabled else preset.description
    return row(
        key=f"preset-{preset.id}", gap=10, padding=(7, 12), radius="$md", align="center",
        bg="$surface_pressed" if selected else "$surface_hover",
        bg_hover="$surface_pressed", border_width=1, border_color="$primary" if selected else "$border",
        cursor="default" if disabled else "pointer", opacity=0.5 if disabled else 1.0,
        on_click=None if disabled else (lambda: _select_preset(preset)),
    )[
        _radio(selected),
        column(grow=1, width=0, gap=2)[
            text(preset.label, size=13, weight=600),
            text(detail, size=11.5, color="$muted", line_height=1.35),
        ],
    ]


def _custom_touch(_value=None) -> None:
    """Al escribir en un campo se elige el preset personalizado."""
    state.export_preset.set(graph.CUSTOM_PRESET_ID)


def _custom_field(label: str, control):
    return column(grow=1, width=0, gap=3)[
        text(label, size=11, color="$muted"),
        control,
    ]


def _apply_social(width: int, height: int) -> None:
    state.export_custom_width.set(str(width))
    state.export_custom_height.set(str(height))
    state.export_preset.set(graph.CUSTOM_PRESET_ID)


def _custom_card(selected: bool, error: str | None, fmt: str):
    """Tarjeta «Personalizado» con sus campos y los accesos rápidos sociales (docs/44 y 48)."""
    if fmt == "webm":
        detail = "Tamaño y fps a mano; VP9 + Opus en WebM."
    elif fmt == "gif":
        detail = "Tamaño y fps a mano; GIF con paleta de 256 colores y sin audio."
    else:
        detail = "Tamaño, fps y bitrate a mano; GPU si existe o x264."

    if fmt == "gif":
        format_fields = text("GIF no lleva audio ni bitrate: usa fps y tamaño; el lado mayor se "
                             "limita a 720 px y la cadencia a 20 fps.",
                             size=11, color="$muted", line_height=1.35,
                             key="export-gif-note")
    else:
        codec_field = (
            _custom_field("Códec", text("VP9 + Opus", size=12.5, color="$muted"))
            if fmt == "webm" else
            _custom_field("Códec", select(
                state.export_custom_codec,
                [("auto", "GPU si hay"), ("x264", "x264 (CPU)")],
                key="export-codec", label="Códec de video", padding=(6, 9),
            ))
        )
        format_fields = row(gap=8)[
            _custom_field("Bitrate de video", text_input(
                state.export_custom_video_bitrate, key="export-vbitrate", on_change=_custom_touch,
                on_key=mark_input_key, label="Bitrate de video (por ejemplo 6M)",
                font_family="Consolas", padding=(6, 9),
            )),
            _custom_field("Bitrate de audio", text_input(
                state.export_custom_audio_bitrate, key="export-abitrate", on_change=_custom_touch,
                on_key=mark_input_key, label="Bitrate de audio (por ejemplo 192k)",
                font_family="Consolas", padding=(6, 9),
            )),
            codec_field,
        ]

    return column(
        key=f"preset-{graph.CUSTOM_PRESET_ID}", gap=8, padding=(7, 12), radius="$md",
        bg="$surface_pressed" if selected else "$surface_hover",
        bg_hover="$surface_pressed", border_width=1,
        border_color="$primary" if selected else "$border", cursor="pointer",
        on_click=lambda: state.export_preset.set(graph.CUSTOM_PRESET_ID),
    )[
        row(gap=10, align="center", key="export-custom-head", cursor="pointer",
            on_click=lambda: state.export_preset.set(graph.CUSTOM_PRESET_ID))[
            _radio(selected),
            column(grow=1, width=0, gap=2)[
                text("Personalizado", size=13, weight=600),
                text(detail, size=11.5, color="$muted", line_height=1.35),
            ],
        ],
        row(gap=6, align="center")[
            text("Redes:", size=11, color="$muted"),
            *[
                button(
                    social.label, (lambda w=social.width, h=social.height: _apply_social(w, h)),
                    variant="soft", padding=(3, 9), font_size=11,
                    key=f"export-social-{social.id}",
                )
                for social in graph.SOCIAL_PRESETS
            ],
            spacer(),
        ],
        row(gap=8)[
            _custom_field("Ancho (px)", text_input(
                state.export_custom_width, key="export-width", on_change=_custom_touch,
                on_key=mark_input_key, label="Ancho de salida en píxeles",
                font_family="Consolas", padding=(6, 9),
            )),
            _custom_field("Alto (px)", text_input(
                state.export_custom_height, key="export-height", on_change=_custom_touch,
                on_key=mark_input_key, label="Alto de salida en píxeles",
                font_family="Consolas", padding=(6, 9),
            )),
            _custom_field("FPS", text_input(
                state.export_custom_fps, key="export-fps", on_change=_custom_touch,
                on_key=mark_input_key, label="Fotogramas por segundo de salida",
                font_family="Consolas", padding=(6, 9),
            )),
        ],
        format_fields,
        error and text("⚠ " + error, size=11, color="$danger", line_height=1.35,
                       key="export-custom-error"),
    ]


def _summary(project, preset: graph.ExportPreset, span: tuple[float, float] | None = None,
             custom_error: str | None = None) -> str:
    total = tl.total_duration(project) if span is None else span[1] - span[0]
    if custom_error:
        return f"Configuración personalizada no válida: {custom_error}"
    if preset.video == "copy":
        return f"{tl.format_time(total)} · resolución y códec originales (sin pérdida)"
    w, h = graph.output_size(project, preset)
    fps = graph.export_fps(project, preset)
    if preset.format == "gif":
        # GIF no lleva pista de sonido: no se anuncia ni siquiera si el proyecto tiene audio.
        return f"{w}×{h} · {fps} fps · {tl.format_time(total)} · GIF · sin audio"
    has_audio = graph.project_has_audio(project)
    if preset.format == "webm":
        audio = " + Opus " + preset.audio_bitrate if has_audio else " · sin audio"
        if preset.id == graph.CUSTOM_PRESET_ID:
            video_bits = graph.parse_bitrate(preset.video_bitrate) or 0
            return (f"{w}×{h} · {fps} fps · {tl.format_time(total)} · VP9 · "
                    f"{graph.format_bitrate(video_bits)} de video{audio}")
        return f"{w}×{h} · {fps} fps · {tl.format_time(total)} · VP9{audio}"
    if preset.id == graph.CUSTOM_PRESET_ID:
        video_bits = graph.parse_bitrate(preset.video_bitrate) or 0
        audio_bits = graph.parse_bitrate(preset.audio_bitrate) or 0
        audio = f" + AAC {graph.format_bitrate(audio_bits)}" if has_audio else " · sin audio"
        return (f"{w}×{h} · {fps} fps · {tl.format_time(total)} · H.264 · "
                f"{graph.format_bitrate(video_bits)} de video{audio}")
    audio = " + AAC " + preset.audio_bitrate if has_audio else " · sin audio"
    return f"{w}×{h} · {fps} fps · {tl.format_time(total)} · H.264{audio}"


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


def _range_choice(project, use_range: bool):
    """«Todo el timeline» o «Rango In/Out» (fase 3). Sin In/Out solo se explica cómo marcarlo."""
    if not tl.has_range(project):
        return text("Exporta todo el timeline. Marca In/Out (I y O) para exportar solo un tramo.",
                    size=11.5, color="$muted", key="export-range-hint")
    start, end = tl.play_range(project)

    def option(value: str, label: str, active: bool):
        return box(key=f"export-range-{value}", padding=(6, 12), radius="$md", cursor="pointer",
                   bg="$surface_pressed" if active else "$surface_hover", border_width=1,
                   border_color="$primary" if active else "$border",
                   on_click=lambda: state.export_range.set(value))[
            text(label, size=12.5, weight=600 if active else 500)
        ]

    return row(gap=8, align="center", key="export-range")[
        text("Exportar", size=12, color="$muted"),
        option("all", "Todo el timeline", not use_range),
        option("range", f"Rango In/Out · {tl.format_time(start)}–{tl.format_time(end)}", use_range),
    ]


@component
def export_modal():
    if not state.export_open():
        return column()
    project = state.editor_project()
    preset_id = state.export_preset()
    fmt = state.export_format()
    if fmt not in graph.EXPORT_FORMATS:
        fmt = "mp4"
    custom_error = state.export_custom_error() if preset_id == graph.CUSTOM_PRESET_ID else None
    if preset_id == graph.CUSTOM_PRESET_ID:
        preset = (state.export_custom_preset() if custom_error is None
                  else graph.with_format(graph.PRESETS[1], fmt))
    else:
        preset = graph.with_format(graph.preset_by_id(preset_id), fmt)
    custom_blocked = preset_id == graph.CUSTOM_PRESET_ID and custom_error is not None
    start_blocked = custom_blocked or (preset.video == "copy" and fmt != "mp4")
    blocked = state.export_blockers()
    progress = state.export_progress()
    running = progress is not None and progress.active
    finished = progress is not None and progress.state == "done"
    close = _call("close_export")
    has_range = tl.has_range(project)
    use_range = has_range and state.export_range() == "range"
    span = tl.play_range(project) if use_range else None

    if running:
        actions = [button("Cancelar export", _call("cancel_export"), variant="soft", key="export-cancel"),
                   button("Ocultar", close, key="export-hide")]
    elif finished:
        actions = [button("Mostrar en carpeta", _call("reveal_export"), variant="soft", key="export-reveal"),
                   button("Reproducir", _call("play_export"), variant="soft", key="export-play"),
                   button("Listo", close, key="export-close")]
    else:
        actions = [
            button("Cancelar", close, variant="soft", key="export-dismiss"),
            button("Exportar" if progress is None else "Reintentar",
                   None if start_blocked else _call("start_export"),
                   variant="soft" if start_blocked else "primary",
                   opacity=0.55 if start_blocked else 1.0,
                   cursor="default" if start_blocked else "pointer",
                   key="export-start"),
        ]

    return column(layer="overlay", position="absolute", left=0, right=0, top=0, bottom=0, align="center",
                  justify="center", capture=True)[
        box(position="absolute", left=0, right=0, top=0, bottom=0, bg="#00000080",
            label="Cerrar el diálogo", on_click=None if running else close)[None],
        column(position="absolute", width=540, height=min(state.view_h(), 720.0), padding=16, gap=10,
               radius="$lg", bg="$surface", border_width=1, border_color="$border",
               shadow=(0, 20, 50, "$shadow"), key="export-modal")[
            row(align="center", gap=10)[
                text("Exportar", size=17, weight=700),
                text(project.name, size=12.5, color="$muted", max_lines=1, grow=1, width=0),
                icon_button(icons.CLOSE, close, tooltip="Cerrar (Esc)", size=28, key="export-x"),
            ],
            scroll(key="export-scroll", grow=1, height=0, gap=10)[
                column(gap=6)[
                    text("Nombre del archivo", size=12, color="$muted"),
                    row(gap=8, align="center")[
                        text_input(state.export_name, key="export-name", on_key=mark_input_key,
                                   grow=1, width=0, padding=(7, 10), label="Nombre del archivo",
                                   on_submit=lambda _v: None if running else _call("start_export")()),
                        text(graph.format_extension(fmt), size=12.5, color="$muted",
                             family="Consolas", key="export-ext"),
                    ],
                    row(gap=6, align="center")[
                        text(f"En {state.exports_dir()}", size=11, color="$muted", max_lines=1,
                             grow=1, width=0),
                        icon_button(icons.FOLDER, _call("reveal_export"), tooltip="Abrir la carpeta",
                                    size=24),
                    ],
                ],
                not running and not finished and _format_choice(fmt),
                not running and not finished and _range_choice(project, use_range),
                not finished and text(_summary(project, preset, span, custom_error),
                                      size=11.5, color="$muted", key="export-summary"),
                not running and not finished and column(gap=6)[
                    [_preset_card(p, p.id == preset_id, blocked, fmt) for p in graph.PRESETS],
                    _custom_card(preset_id == graph.CUSTOM_PRESET_ID,
                                 custom_error if preset_id == graph.CUSTOM_PRESET_ID else None,
                                 fmt),
                ],
                _progress_block(progress),
            ],
            row(justify="end", gap=10)[actions],
        ],
    ]
