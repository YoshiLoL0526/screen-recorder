"""Pantalla de biblioteca: grabaciones, recuperación y acciones."""

from __future__ import annotations

from aurora import box, button, column, component, image, row, spacer, text, virtual_list

from ... import state
from ...services import library
from ..components import chip, icon_button
from .. import icons

ITEM_HEIGHT = 108


@component
def recording_row(index: int):
    items = state.recordings()
    if index >= len(items):
        return row()
    rec = items[index]

    thumb = (
        image(rec.thumb, width=150, height=86, fit="cover", radius="$sm")
        if rec.thumb is not None
        else box(
            width=150,
            height=86,
            radius="$sm",
            bg="$surface_hover",
            align="center",
            justify="center",
        )[text("sin miniatura", size=10, color="$muted")]
    )

    meta = " · ".join(
        part
        for part in (
            rec.created_label,
            f"{rec.width}×{rec.height}" if rec.width else "",
            f"{rec.fps:.0f} fps" if rec.fps else "",
            rec.encoder if rec.encoder != "?" else "",
        )
        if part
    )

    def ask_delete() -> None:
        from ..controller import CONTROLLER

        if CONTROLLER is not None:
            CONTROLLER.ask_delete(rec)

    def open_it() -> None:
        library.open_file(rec.mp4)

    def edit_hint() -> None:
        state.toast("info", "El editor llega en F3 — el motor ya está listo (docs/04-editor.md).")

    return box(
        key=f"rec-{index}",
        grow=1,
        padding=(10, 12),
        radius="$md",
        bg="$surface",
        border_width=1,
        border_color="$border",
        gap=14,
    )[
        row(gap=14, align="center", grow=1)[
            thumb,
            column(gap=4, grow=1)[
                text(rec.name, size=14, weight=600, max_lines=1),
                text(meta, size=11.5, color="$muted", max_lines=1),
                row(gap=8, align="center")[
                    chip(rec.duration_label, tone="text"),
                    chip(f"{rec.size_mb:.1f} MB"),
                    rec.recovered and chip("Recuperada", tone="accent"),
                    spacer(),
                    button("▶ Reproducir", open_it, variant="soft", padding=(5, 12), font_size=12),
                    button("✂ Editar", edit_hint, variant="soft", padding=(5, 12), font_size=12),
                    icon_button(icons.FOLDER, lambda: library.reveal_in_explorer(rec.mp4),
                                tooltip="Mostrar en el explorador", size=28),
                    icon_button(icons.TRASH, ask_delete, tooltip="Mover a la papelera", size=28),
                ],
            ],
        ]
    ]


@component
def recoverable_section():
    folders = state.recoverable()
    busy = state.recovering()
    if not folders:
        return column()

    def row_for(folder):
        from ..controller import CONTROLLER

        working = folder.name in busy

        def recover() -> None:
            if CONTROLLER is not None and not working:
                CONTROLLER.ask_recover(folder)

        return row(
            key=f"recov-{folder.name}",
            gap=10,
            padding=(8, 12),
            radius="$md",
            bg="$surface",
            border_width=1,
            border_color="#F5B851",
            align="center",
        )[
            text("Interrumpida", size=12, weight=700, color="#F5B851"),
            text(folder.name, size=12.5, color="$muted", grow=1, max_lines=1),
            button("Recuperando…" if working else "Recuperar", recover, variant="soft",
                   padding=(5, 12), font_size=12, opacity=0.55 if working else 1.0),
        ]

    return column(gap=8)[[row_for(f) for f in folders]]


@component
def library_screen():
    from ..controller import CONTROLLER

    items = state.recordings()

    def refresh() -> None:
        if CONTROLLER is not None:
            CONTROLLER.refresh_library()

    header = row(gap=10, align="center")[
        text("Grabaciones", size=22, weight=700),
        chip(f"{len(items)}", tone="muted"),
        spacer(),
        icon_button(icons.FOLDER, lambda: library.reveal_in_explorer(state.recordings_dir()),
                    tooltip="Abrir la carpeta de grabaciones"),
        button("Actualizar", refresh, variant="soft", padding=(6, 14), font_size=12.5),
    ]

    if not items:
        empty = box(
            grow=1,
            bg="$surface",
            radius="$lg",
            border_width=1,
            border_color="$border",
            align="center",
            justify="center",
            gap=10,
        )[
            column(align="center", gap=8)[
                text("Aún no hay grabaciones", size=17, weight=600),
                text("Cuando grabes algo aparecerá aquí, listo para editar.", size=13, color="$muted"),
                button("Ir a Grabar", lambda: state.screen.set("record")),
            ]
        ]
        body = empty
    else:
        body = virtual_list(
            len(items),
            recording_row,
            item_height=ITEM_HEIGHT,
            gap=10,
            key="library-list",
        )

    return column(grow=1, padding=(18, 20), gap=14)[
        recoverable_section(),
        header,
        body,
    ]
