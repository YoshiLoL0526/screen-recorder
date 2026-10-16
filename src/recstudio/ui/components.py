"""Componentes pequeños y reutilizables de la app (sobre las primitivas de Aurora)."""

from __future__ import annotations

from aurora import Canvas, animate, box, button, canvas, column, component, image, row, text

from .. import state
from . import icons

_TOAST_TONES = {
    "info": ("$surface_hover", "$text", icons.INFO),
    "success": ("$success", "#0B0D14", icons.CHECK),
    "warning": ("#F5B851", "#0B0D14", icons.ALERT),
    "danger": ("$danger", "#FFFFFF", icons.ALERT),
}


def chip(label, *, tone: str = "muted", size: float = 11):
    tones = {
        "muted": ("$surface_hover", "$muted"),
        "rec": ("$rec", "#FFFFFF"),
        "accent": ("$accent", "#0B0D14"),
        "text": ("$surface_pressed", "$text"),
    }
    bg, color = tones.get(tone, tones["muted"])
    return box(padding=(2, 8), radius="$full", bg=bg, align="center")[
        text(label, size=size, weight=700, color=color, letter_spacing=0.3)
    ]


def section(title: str, *children, gap: float = 10, **style):
    """Bloque con título en mayúsculas (estilo panel de configuración)."""
    return column(gap=gap, **style)[
        text(title.upper(), size=11, weight=700, color="$muted", letter_spacing=0.8),
        *children,
    ]


def field(label: str, control):
    """Etiqueta a la izquierda y control a la derecha."""
    return row(gap=8, align="center")[
        text(label, size=12.5, color="$muted", width=84, shrink=0),
        box(grow=1)[control],
    ]


def icon_button(icon: bytes, on_click, *, tooltip: str | None = None, size: float = 34, **style):
    element = box(
        width=size,
        height=size,
        radius="$md",
        align="center",
        justify="center",
        bg="$surface_hover",
        bg_hover="$surface_pressed",
        on_click=on_click,
        cursor="pointer",
        press_scale=0.94,
        **style,
    )[image(icon, width=size * 0.52)]
    if tooltip:
        element.style["tooltip"] = tooltip
    return element


def sparkline_fps():
    """Sparkline del fps real de grabación (últimos muestreos)."""

    def draw(c: Canvas) -> None:
        values = state.fps_history()
        target = max(1.0, float(state.fps_target()))
        c.clear("$surface")
        total = target * 1.25
        y_target = c.height - (target / total) * c.height
        c.line(0, y_target, c.width, y_target, stroke="$grid", stroke_width=1)
        if len(values) < 2:
            c.text(6, 6, "esperando muestras…", size=11, color="$muted")
            return
        step = c.width / max(1, len(values) - 1)
        points = [(i * step, c.height - min(1.0, v / total) * c.height) for i, v in enumerate(values)]
        area = [(0.0, float(c.height)), *points, (float(c.width), float(c.height))]
        c.polygon(area, fill="#7C5CFF33")
        c.polyline(points, stroke="$primary", stroke_width=1.6)
        c.text(c.width - 4, 4, f"{values[-1]:.0f} fps", size=11, color="$muted", align="right")

    return canvas(draw, height=64, bg="$surface", radius="$md", grow=1)


# -- overlays -----------------------------------------------------------------

@component
def _toasts():
    def item(t: state.Toast):
        bg, color, icon = _TOAST_TONES.get(t.kind, _TOAST_TONES["info"])
        return row(
            key=f"toast-{t.id}",
            gap=10,
            padding=(10, 14),
            radius="$md",
            bg=bg,
            align="center",
            shadow=(0, 10, 26, "$shadow"),
            enter=animate.slide(y=14),
            exit=animate.fade(),
        )[
            image(icon, width=16),
            text(t.text, size=13, color=color, max_lines=2, width=340),
        ]

    items = state.toasts()
    return column(
        layer="overlay",
        position="absolute",
        right=18,
        bottom=18,
        gap=8,
        align="end",
        width=420,
    )[[item(t) for t in items]]


def toast_layer():
    return _toasts()


_on_accept = None


def bind_modal_accept(handler) -> None:
    """El controlador registra aquí qué hacer al aceptar una confirmación."""
    global _on_accept
    _on_accept = handler


@component
def _modal():
    confirm = state.confirm()
    if confirm is None:
        return column()

    def accept() -> None:
        if _on_accept is not None:
            _on_accept(confirm)
        state.confirm.set(None)

    return column(
        layer="overlay",
        position="absolute",
        left=0,
        right=0,
        top=0,
        bottom=0,
        align="center",
        justify="center",
        capture=True,
    )[
        box(position="absolute", left=0, right=0, top=0, bottom=0, bg="#00000080",
            on_click=lambda: state.confirm.set(None))[None],
        box(
            position="absolute",
            width=430,
            padding=22,
            gap=14,
            radius="$lg",
            bg="$surface",
            border_width=1,
            border_color="$border",
            shadow=(0, 20, 50, "$shadow"),
            enter=animate.zoom(0.97),
        )[
            text(confirm.title, size=17, weight=700),
            text(confirm.message, size=13, color="$muted", line_height=1.4),
            row(gap=10, justify="end")[
                button("Cancelar", lambda: state.confirm.set(None), variant="soft"),
                button("Continuar", accept),
            ],
        ],
    ]


def modal_layer():
    return _modal()
