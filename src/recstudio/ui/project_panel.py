"""Panel de medios y capas del editor.

Dos pestañas en una columna a la izquierda del preview:

- **Medios**: todos los `Project.assets` (videos, audio e imágenes), con miniatura, uso y un
  botón para insertarlos en el playhead. También acepta archivos soltados desde el Explorador.
- **Capas**: el video base, los textos y los elementos en orden de apilado, siempre visibles
  aunque haya una selección. Seleccionar una capa salta a su intervalo si el playhead está fuera.

El panel es deliberadamente independiente del inspector: el inspector responde a la selección y
este panel responde a «qué tengo y qué capas hay». Sin IO en los handlers: los pósters los llena
`EditorController` en un hilo y llegan por `state.posters`.
"""

from __future__ import annotations

from pathlib import Path

from aurora import box, button, column, component, image, row, scroll, spacer, text

from .. import state
from ..core import elements as element_mod
from ..core import timeline as tl
from . import icons
from .components import chip, icon_button


def _editor():
    from .editor_controller import EDITOR

    return EDITOR


def _call(name: str, *args):
    def handler() -> None:
        editor = _editor()
        if editor is not None:
            getattr(editor, name)(*args)

    return handler


def _asset_usage(project, asset_id: str) -> int:
    clips = sum(
        clip.asset_id == asset_id
        for track in (project.video, *project.audio)
        for clip in track.clips
    )
    elements = sum(element.asset_id == asset_id for element in project.elements)
    return clips + elements + (1 if project.background_asset == asset_id else 0)


def _asset_meta(asset, usage: int) -> str:
    if asset.kind == "image":
        return f"Imagen · {asset.width}×{asset.height}"
    duration = tl.format_time(asset.duration)
    state_label = "en uso" if usage else "sin usar"
    if asset.kind == "audio":
        return f"Audio · {duration} · {state_label}"
    return f"{duration} · {state_label}"


def _thumbnail(asset, *, posters, offline: bool):
    if offline:
        return box(width=60, height=34, radius=4, bg="$surface_hover", align="center",
                   justify="center")[text("sin archivo", size=9, color="$danger")]
    if asset.kind == "image":
        return image(asset.path, width=60, height=34, fit="cover", radius=4)
    if asset.kind == "audio":
        return box(width=60, height=34, radius=4, bg="$surface_hover", align="center",
                   justify="center")[image(icons.MUSIC, width=18)]
    data = posters.get(asset.id)
    if data:
        return image(data, width=60, height=34, fit="cover", radius=4)
    return box(width=60, height=34, radius=4, bg="$surface_hover", align="center",
               justify="center")[image(icons.CAMERA, width=16)]


def _asset_drag(asset_id: str):
    """Arrastrar un medio a la línea de tiempo o al preview."""

    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is None or e.phase != "end":
            return
        if e.target == "tl-content":
            editor.drop_asset(asset_id, e.x, "timeline")
        elif e.target == "ed-stage":
            editor.drop_asset(asset_id, e.x, "preview")

    return handler


@component
def media_tab():
    project = state.editor_project()
    posters = state.posters()
    offline = state.offline_assets()
    target = state.video_track_target()
    target_label = element_mod.video_track_label(target)
    assets = project.assets
    if not assets:
        return column(grow=1, gap=10, justify="center", align="center")[
            image(icons.MEDIA, width=28),
            text("Sin medios todavía", size=12.5, weight=600, color="$muted"),
            text("Importa un video, audio o imagen. Después podrás insertarlo en el playhead "
                 "o arrastrarlo al timeline.", size=10.5, color="$muted", line_height=1.35,
                 align="center"),
            button("Importar medios…", _call("browse_media"), variant="soft", padding=(5, 10),
                   font_size=11.5, key="media-empty-import"),
        ]

    rows = []
    for asset in assets:
        usage = _asset_usage(project, asset.id)
        rows.append(
            row(
                key=f"media-{asset.id}", gap=8, align="center", padding=(5, 6), radius="$md",
                bg_hover="$surface_hover", cursor="pointer",
                on_drag=_asset_drag(asset.id),
                label=f"Medio {asset.name}",
                tooltip=f"{asset.name} · arrastra al timeline o pulsa +",
            )[
                _thumbnail(asset, posters=posters, offline=asset.id in offline),
                column(grow=1, width=0, gap=1)[
                    text(asset.name, size=11.5, weight=600, max_lines=1),
                    text(_asset_meta(asset, usage), size=9.5, color="$muted", max_lines=1,
                         family="Consolas"),
                ],
                icon_button(
                    icons.ADD, _call("insert_asset", asset.id),
                    tooltip=(
                        f"Añadir «{asset.name}» a {target_label} en el playhead"
                        if asset.kind == "video" and target > 0
                        else f"Añadir «{asset.name}» al playhead"
                    ), size=24,
                    key=f"media-add-{asset.id}",
                ),
                asset.kind == "video" and icon_button(
                    icons.PIP, _call("add_video_element", asset.id),
                    tooltip=f"Añadir «{asset.name}» a {target_label} en el playhead", size=24,
                    key=f"media-pip-{asset.id}",
                ),
                usage == 0 and icon_button(
                    icons.TRASH, _call("remove_asset", asset.id),
                    tooltip=f"Quitar «{asset.name}» del proyecto", size=24,
                    key=f"media-remove-{asset.id}",
                ),
            ]
        )
    return column(grow=1, height=0, gap=6)[
        scroll(key="media-scroll", grow=1, gap=2)[rows],
        button("Importar medios…", _call("browse_media"), variant="soft", padding=(5, 10),
               font_size=11.5, key="media-import"),
    ]


def layer_row(key: str, icon: bytes, label: str, meta: str, on_click) -> object:
    return row(
        key=key, gap=8, align="center", padding=(5, 6), radius="$md", bg_hover="$surface_hover",
        cursor="pointer", on_click=on_click, label=label,
    )[
        image(icon, width=14),
        column(grow=1, width=0, gap=1)[
            text(label, size=11.5, weight=600, max_lines=1),
            text(meta, size=9.5, color="$muted", max_lines=1, family="Consolas"),
        ],
    ]


@component
def layers_tab():
    project = state.editor_project()
    rows = []
    for overlay in reversed(project.texts):
        preview = overlay.text if len(overlay.text) <= 28 else overlay.text[:27] + "…"
        if overlay.subtitle:
            preview = f"Subtítulo · {preview}"
        rows.append(layer_row(
            f"layer-text-{overlay.id}", icons.TEXT, preview,
            f"{'Subtítulo' if overlay.subtitle else 'Texto'} · "
            f"{tl.format_time(overlay.start)}–{tl.format_time(overlay.end)}",
            _call("focus_text", overlay.id),
        ))
    for element in reversed(project.elements):
        label = element_mod.ELEMENT_LABELS.get(element.kind, element.kind)
        if element.kind in ("image", "video"):
            try:
                name = project.asset(element.asset_id or "").name
                prefix = "Imagen" if element.kind == "image" else "Vídeo"
                label = f"{prefix} · {name}"
            except Exception:
                label = "Imagen" if element.kind == "image" else "Vídeo"
            if element.kind == "video" and element.video_track > 0:
                label = f"{element_mod.video_track_label(element.video_track)} · {label}"
        rows.append(layer_row(
            f"layer-element-{element.id}", icons.LAYERS, label,
            f"Elemento · {tl.format_time(element.start)}–{tl.format_time(element.end)}",
            _call("focus_element", element.id),
        ))
    clips = project.video.clips
    if clips:
        rows.append(layer_row(
            "layer-video", icons.CUT, f"Video · {len(clips)} clip{'s' if len(clips) != 1 else ''}",
            "pista base", _call("select_first_clip"),
        ))

    if not rows:
        rows.append(text("Aún no hay capas en el proyecto.", size=10.5, color="$muted"))
    return column(grow=1, height=0, gap=6)[
        scroll(key="layers-scroll", grow=1, gap=2)[rows],
        text("Añadir capa", size=10, weight=700, color="$muted", letter_spacing=0.7),
        row(gap=5)[
            button("Texto", _call("add_text"), variant="soft", padding=(3, 8), font_size=11,
                   key="panel-add-text"),
            button("Rect.", _call("add_element", "rect"), variant="soft", padding=(3, 8),
                   font_size=11, key="panel-add-rect"),
        ],
        row(gap=5)[
            button("Elipse", _call("add_element", "ellipse"), variant="soft", padding=(3, 8),
                   font_size=11, key="panel-add-ellipse"),
            button("Triáng.", _call("add_element", "triangle"), variant="soft", padding=(3, 8),
                   font_size=11, key="panel-add-triangle"),
        ],
        button("Imagen…", _call("browse_images"), variant="soft", padding=(3, 8),
               font_size=11, key="panel-add-image"),
        text("Insertar video en", size=10, color="$muted"),
        row(gap=4)[
            button(
                "Elementos", _call("set_video_track_target", 0), variant="soft",
                padding=(3, 6), font_size=10.5, key="panel-video-target-0",
                bg="$primary" if state.video_track_target() == 0 else "$surface_hover",
                color="#FFFFFF" if state.video_track_target() == 0 else "$muted",
                tooltip="Los videos se añaden como capa «Elementos» (PiP libre)",
            ),
            *[
                button(
                    f"V{i + 1}", _call("set_video_track_target", i), variant="soft",
                    padding=(3, 6), font_size=10.5, key=f"panel-video-target-{i}",
                    bg="$primary" if state.video_track_target() == i else "$surface_hover",
                    color="#FFFFFF" if state.video_track_target() == i else "$muted",
                    tooltip=f"Los videos se añaden a la pista V{i + 1}",
                )
                for i in range(1, element_mod.VIDEO_TRACKS_MAX + 1)
            ],
        ],
        button(f"Video en {element_mod.video_track_label(state.video_track_target())}…",
               _call("browse_pip"), variant="soft", padding=(3, 8),
               font_size=11, key="panel-add-pip"),
        row(gap=5)[
            button("Subtítulos…", _call("browse_subtitles"), variant="soft", padding=(3, 8),
                   font_size=11, key="panel-add-subtitles",
                   tooltip="Importar un SRT o VTT como textos subtítulo"),
            button("SRT…", _call("export_subtitles"), variant="ghost", padding=(3, 8),
                   font_size=11, key="panel-export-subtitles",
                   tooltip="Exportar los subtítulos a SRT"),
        ],
    ]


@component
def project_panel():
    tab = state.project_panel_tab()

    def tab_button(label: str, value: str, icon: bytes):
        selected = tab == value
        return row(
            key=f"panel-tab-{value}", gap=5, padding=(4, 7), radius="$sm",
            bg="$surface_pressed" if selected else "$surface_hover",
            bg_hover="$surface_pressed", cursor="pointer", align="center",
            on_click=_call("set_project_panel_tab", value),
            label=f"Ver {label}",
        )[
            image(icon, width=13),
            text(label, size=11, weight=700 if selected else 500,
                 color="$text" if selected else "$muted"),
        ]

    def drop_files(paths) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is not None:
            editor.add_media([Path(p) for p in paths])

    return column(
        key="ed-project-panel", width=236, shrink=0, bg="$surface", radius="$lg",
        border_width=1, border_color="$border", padding=9, gap=7,
        on_drop=drop_files,
    )[
        row(gap=4, align="center")[
            tab_button("Medios", "media", icons.MEDIA),
            tab_button("Capas", "layers", icons.LAYERS),
            spacer(),
            chip(f"{len(state.editor_project().assets)}", tone="muted"),
        ],
        media_tab() if tab == "media" else layers_tab(),
    ]
