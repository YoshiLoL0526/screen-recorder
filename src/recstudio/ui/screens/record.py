"""Pantalla de grabación: preview en vivo, stats y controles."""

from __future__ import annotations

from aurora import (
    box,
    button,
    column,
    component,
    divider,
    image,
    row,
    scroll,
    select,
    spacer,
    switch,
    text,
    text_input,
)

from ... import state
from ..components import chip, field, icon_button, section, sparkline_fps
from .. import icons

FPS_OPTIONS = (15, 24, 30, 48, 60, 120)
PRESET_OPTIONS = (
    ("baja", "Baja · 720p"),
    ("media", "Media · 1080p"),
    ("alta", "Alta · 1080p60"),
    ("insane", "Insane · 2K"),
)


def time_label(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


# -- preview ------------------------------------------------------------------

@component
def preview_panel():
    data = state.preview()
    rec = state.rec_state()
    recording = rec in ("recording", "paused", "starting")

    badges = row(position="absolute", left=14, top=12, gap=8, align="center")[
        recording and chip("● REC", tone="rec", size=12),
        recording and chip(time_label(state.rec_elapsed()), tone="text", size=12),
        not recording and chip("vista previa", tone="muted"),
    ]

    content = (
        column(grow=1, align="center", justify="center")[
            image(data, fit="contain", grow=1, width="100%", key="preview-image")
        ]
        if data
        else column(grow=1, align="center", justify="center", gap=6)[
            text("Vista previa", size=15, weight=600, color="$muted"),
            text("iniciando el preview…", size=12, color="$muted"),
        ]
    )

    return box(
        grow=1,
        bg="#000000",
        radius="$lg",
        border_width=1,
        border_color="$rec" if recording and rec == "recording" else "$border",
        min_height=240,
        shadow=(0, 10, 30, "$shadow"),
        key="preview-panel",
    )[
        content,
        badges,
    ]


# -- stats --------------------------------------------------------------------

@component
def stats_panel():
    rec = state.rec_state()
    if rec == "idle" and not state.fps_history():
        return row()

    def stat(label: str, value: str):
        return column(gap=2)[
            text(label.upper(), size=9.5, weight=700, color="$muted", letter_spacing=0.6),
            text(value, size=14, weight=700, font_family="Consolas"),
        ]

    return row(gap=14, align="center", key="stats-panel")[
        sparkline_fps(),
        stat("tiempo", time_label(state.rec_elapsed())),
        stat("fps", f"{state.rec_fps():.1f}"),
        stat("dup", str(state.rec_dup())),
        stat("skip", str(state.rec_skip())),
        stat("tamaño", f"{state.rec_size_mb():.1f} MB"),
        stat("disco", f"{state.rec_disk_gb():.0f} GB"),
        column(gap=2)[
            text("AUDIO", size=9.5, weight=700, color="$muted", letter_spacing=0.6),
            _level_meter(),
        ],
    ]


def _level_meter():
    def draw(c) -> None:  # noqa: ANN001
        level = max(0.0, min(1.0, state.rec_level()))
        c.clear("$surface_hover")
        c.rect(0, 0, c.width * level, c.height, radius=3, fill="$accent")
        c.rect(0.72 * c.width, 0, 1, c.height, fill="#F5B851")

    from aurora import canvas as _canvas

    return _canvas(draw, width=70, height=14, radius=7)


# -- controles ----------------------------------------------------------------

def _record_label() -> str:
    return {
        "idle": "GRABAR",
        "starting": "PREPARANDO…",
        "recording": "DETENER",
        "paused": "DETENER",
        "stopping": "GUARDANDO…",
        "muxing": "GUARDANDO…",
    }.get(state.rec_state(), "GRABAR")


@component
def controls_panel():
    from ..controller import CONTROLLER

    rec = state.rec_state()
    busy = rec in ("starting", "stopping", "muxing", "error")
    recording = rec in ("recording", "paused")

    def on_record() -> None:
        if CONTROLLER is not None:
            CONTROLLER.toggle_record()

    def on_pause() -> None:
        if CONTROLLER is not None:
            CONTROLLER.toggle_pause()

    main = button(
        _record_label(),
        on_record,
        variant="primary",
        padding=(14, 30),
        font_size=15,
        font_weight=700,
        radius="$full",
        bg="$rec" if recording else "$primary",
        bg_hover="$rec_hover" if recording else "$primary_hover",
        bg_pressed="$rec_pressed" if recording else "$primary_pressed",
        opacity=0.55 if busy else 1.0,
        focusable=not busy,
        key="rec-button",
    )

    pause = recording and button(
        "REANUDAR" if rec == "paused" else "PAUSAR",
        on_pause,
        variant="soft",
        padding=(14, 22),
        radius="$full",
    )

    return row(gap=12, align="center", justify="center", key="controls-panel")[
        main,
        pause,
        spacer(),
        text("F9 grabar · F10 pausar", size=12, color="$muted"),
    ]


# -- columna de configuración ---------------------------------------------------

@component
def config_panel():
    kind = state.source_kind()
    monitors = state.monitors()
    devices = [d for d in state.audio_devices() if d.kind == "input"]
    encoders = state.encoders()

    source_block = [
        field("Fuente", select(
            state.source_kind,
            [("monitor", "Monitor completo"), ("window", "Una ventana"), ("region", "Región")],
            key="sel-kind",
        )),
    ]
    if kind == "monitor":
        source_block.append(field("Monitor", select(
            state.monitor_index,
            [(m.index, f"Monitor {m.index} · {m.width}×{m.height}") for m in monitors] or [(1, "Monitor 1")],
            key="sel-monitor",
        )))
    elif kind == "window":
        source_block.append(field("Título", text_input(
            state.window_title, placeholder="parte del título…", key="win-title",
        )))
    else:
        source_block.append(
            row(gap=6, align="center")[
                text("x", size=12, color="$muted"), text_input(state.region_x, width=54, key="rx"),
                text("y", size=12, color="$muted"), text_input(state.region_y, width=54, key="ry"),
                text("an", size=12, color="$muted"), text_input(state.region_w, width=58, key="rw"),
                text("al", size=12, color="$muted"), text_input(state.region_h, width=58, key="rh"),
            ]
        )

    audio_block = [
        field("Sistema", switch(state.audio_system, key="sw-system")),
        field("Keep-alive", switch(state.keepalive, key="sw-keep")),
        field("Micrófono", select(
            state.mic_index,
            [(-1, "Sin micrófono")] + [(d.index, d.name[:26]) for d in devices],
            key="sel-mic",
        )),
    ]

    video_block = [
        field("FPS", select(state.fps_target, list(FPS_OPTIONS), key="sel-fps")),
        field("Calidad", select(state.preset, list(PRESET_OPTIONS), key="sel-preset")),
        field("Encoder", select(
            state.encoder,
            [("auto", "Automático")] + [(name, name) for name, ok in encoders.items() if ok],
            key="sel-encoder",
        )),
    ]

    out_block = [
        field("Carpeta", text_input(state.out_dir, key="out-dir")),
        row(gap=8)[
            spacer(),
            icon_button(icons.FOLDER, lambda: _open_out_dir(), tooltip="Abrir la carpeta"),
        ],
    ]

    return scroll(
        width=310,
        grow=0,  # sin esto, `scroll()` añade grow=1 y el panel crece dentro de la row
        shrink=0,
        padding=(16, 16, 16, 20),
        gap=18,
        bg="$surface",
        border_width=1,
        border_color="$border",
        radius=0,
        key="config-scroll",
    )[
        column(gap=18)[
            section("Fuente", *source_block),
            divider(),
            section("Video", *video_block),
            divider(),
            section("Audio", *audio_block),
            divider(),
            section("Salida", *out_block),
        ]
    ]


def _open_out_dir() -> None:
    from ...platform import paths
    from ...services import library

    library.reveal_in_explorer(paths.videos_dir())


# -- pantalla ------------------------------------------------------------------

def record_screen():
    return row(grow=1, gap=0)[
        # `width=0 + grow=1`: el truco de Aurora para que la columna ocupe el resto sin que
        # su min-content (la imagen) impida encoger al panel de configuración.
        column(grow=1, width=0, padding=16, gap=12, min_width=440, shrink=1)[
            preview_panel(),
            stats_panel(),
            controls_panel(),
        ],
        config_panel(),
    ]
