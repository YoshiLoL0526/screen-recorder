"""Editor de región: captura de referencia del monitor + rectángulo arrastrable.

Se apoya en los hallazgos del spike S5 (F0): los `DragEvent` dan **deltas acumulados**, así que
el editor mueve/redimensiona el rectángulo por deltas (nunca por coordenadas absolutas).
"""

from __future__ import annotations

from aurora import box, button, column, component, image, row, spacer, text

from .. import state


def _drag_handler(corner: str | None):
    def handler(e) -> None:  # noqa: ANN001
        from .controller import CONTROLLER

        if CONTROLLER is None:
            return
        if e.phase == "start":
            CONTROLLER.begin_region_drag()
        elif e.phase == "move":
            CONTROLLER.drag_region(e.dx, e.dy, corner)

    return handler


@component
def _editor():
    data = state.region_ref()
    display_w, display_h = state.region_ref_size()
    bounds = state.region_bounds()
    if not data or not display_w or not display_h or not bounds:
        return column(
            layer="overlay", position="absolute", left=0, right=0, top=0, bottom=0,
            align="center", justify="center", capture=True,
        )[
            box(padding=18, radius="$md", bg="$surface", border_width=1, border_color="$border")[
                text("capturando la pantalla…", size=13, color="$muted")
            ]
        ]

    from .controller import CONTROLLER

    scale_x = display_w / bounds[0]
    scale_y = display_h / bounds[1]
    x, y, w, h = state.region()
    left, top = x * scale_x, y * scale_y
    width, height = w * scale_x, h * scale_y

    def close() -> None:
        if CONTROLLER is not None:
            CONTROLLER.close_region_editor()

    def full_screen() -> None:
        state.set_region((0, 0, bounds[0], bounds[1]))

    def handle(corner: str, cx: float, cy: float):
        cursor = {
            "tl": "crosshair", "tr": "crosshair", "bl": "crosshair", "br": "crosshair",
        }[corner]
        return box(
            key=f"region-{corner}",
            position="absolute",
            left=cx - 6,
            top=cy - 6,
            width=12,
            height=12,
            radius=3,
            bg="$primary",
            border_width=2,
            border_color="#FFFFFF",
            cursor=cursor,
            on_drag=_drag_handler(corner),
        )

    region_box = box(
        key="region-body",
        position="absolute",
        left=left,
        top=top,
        width=width,
        height=height,
        bg="#7C5CFF22",
        border_width=2,
        border_color="$primary",
        cursor="grab",
        on_drag=_drag_handler(None),
    )[
        handle("tl", 0, 0),
        handle("tr", width, 0),
        handle("bl", 0, height),
        handle("br", width, height),
    ]

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
        box(position="absolute", left=0, right=0, top=0, bottom=0, bg="#00000099",
            on_click=close)[None],
        column(
            position="absolute",
            padding=16,
            gap=12,
            radius="$lg",
            bg="$surface",
            border_width=1,
            border_color="$border",
            shadow=(0, 20, 50, "$shadow"),
        )[
            row(gap=10, align="center")[
                text("Elegir región", size=16, weight=700),
                text("arrastra el recuadro o sus esquinas", size=12, color="$muted"),
                spacer(),
                text(f"{x}, {y} · {w}×{h}", size=12, color="$muted", font_family="Consolas"),
            ],
            box(width=display_w, height=display_h, key="region-canvas")[
                image(data, width=display_w, height=display_h, fit="fill"),
                region_box,
            ],
            row(gap=10, justify="end")[
                button("Pantalla completa", full_screen, variant="soft", padding=(6, 14), font_size=12.5),
                button("Listo", close),
            ],
        ],
    ]


def region_editor():
    enabled = state.region_editing()
    if not enabled:
        return column()
    return _editor()
