"""Pantalla del editor: barra de proyecto, preview + transporte, inspector y timeline.

    column[ toolbar, row(grow=1)[ column[preview, transporte], ┃, inspector ], ━, timeline ]

Los divisores (┃ ━) se arrastran (F5): ┃ cambia el ancho del inspector y ━ el alto de las pistas
del timeline (y con ello el del preview). El inspector se oculta con `P` o el botón de panel
(desde la fase 3 la `I`/`O` marcan la entrada y la salida del timeline, ver docs/21).

Cada panel es un `@component` que lee solo sus `Signal`: el preview (30 Hz) y el playhead no
reconstruyen el inspector ni el canvas del timeline.
"""

from __future__ import annotations

from aurora import box, button, column, component, image, row, scroll, select, spacer, text, text_input

from ... import state
from ...core import canvas_edit as ce
from ...core import elements as element_mod
from ...core import timeline as tl
from ...core import transform as tx
from ...core.errors import ProjectError
from ...core.project import TRANSITION_KINDS
from .. import element_overlay, icons
from ..components import chip, icon_button, label_control, mark_input_key, section
from ..export_modal import export_chip, export_modal
from ..timeline import EDITOR_PAD_X, PANEL_BORDER, PREVIEW_MIN_W, SPLIT_W, timeline_view

_TRANSITION_LABELS = {
    "none": "Corte (sin transición)",
    "fade": "Fundido",
    "fadeblack": "Fundido a negro",
    "fadewhite": "Fundido a blanco",
    "dissolve": "Disolver",
    "wipeleft": "Barrido ←",
    "wiperight": "Barrido →",
    "slideleft": "Deslizar ←",
    "circleopen": "Círculo",
}

# Opciones de los efectos del inspector (valor, etiqueta)
_VOLUMES = [(v, f"{v * 100:.0f} %") for v in (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)]
_FADES = [(0.0, "Sin fundido")] + [(v, f"{v:g} s") for v in (0.25, 0.5, 1.0, 1.5, 2.0, 3.0)]
_ZOOMS = [(v, f"{v:g}×") for v in (1.0, 1.1, 1.25, 1.5, 2.0, 3.0)]
_BRIGHTNESS = [(v, f"{v:+.2f}" if v else "0 (original)") for v in (-0.3, -0.2, -0.1, -0.05, 0.0, 0.05, 0.1, 0.2, 0.3)]
_CONTRAST = [(v, f"{v:g}×" if v != 1 else "1× (original)") for v in (0.5, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 2.0)]
_SATURATION = [(v, f"{v:g}×" if v != 1 else "1× (original)") for v in (0.0, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0)]
_CANVAS_COLORS = [("#000000", "Negro"), ("#FFFFFF", "Blanco"), ("#808080", "Gris"),
                  ("#1E1E1E", "Grafito")]
_PROJECT_FITS = [("fit", "Encajar"), ("fill", "Rellenar"), ("stretch", "Estirar")]
_CLIP_FITS = [("inherit", "Heredar del proyecto"), ("fit", "Encajar"), ("fill", "Rellenar"),
              ("stretch", "Estirar")]
_SCALES = [(v, f"{v:g}×") for v in (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)]
_POSITIONS = [(v, f"{v:.2f}") for v in (0.0, 0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0)]
_ROTATIONS = [(0, "0°"), (90, "90°"), (180, "180°"), (270, "270°")]
_FLIPS = [(False, "No"), (True, "Sí")]
# Efectos globales (feature D, docs/26): los mismos rangos que el clip, acotados en la UI.
_BLURS = [(0.0, "Sin blur")] + [(v, f"{v:g} px") for v in (2.0, 5.0, 10.0, 20.0)]
_VIGNETTES = [(0.0, "Sin viñeta")] + [(v, f"{v:g}") for v in (0.25, 0.5, 0.75, 1.0)]

# Elementos superpuestos (v6, docs/25)
_ELEMENT_POSITIONS = [(round(i / 20, 2), f"{i / 20:.2f}") for i in range(21)]
_ELEMENT_SIZES = [(round(i / 20, 2), f"{i / 20:.2f}") for i in range(1, 31)]
_ELEMENT_ROTATIONS = [(float(v), f"{v}°") for v in
                      (0, 15, 30, 45, 60, 90, 120, 135, 180, 225, 270, 315)]
_ELEMENT_OPACITIES = [(round(i / 10, 1), f"{i * 10} %") for i in range(11)]
_ELEMENT_BLURS = [(float(v), "Sin desenfoque" if v == 0 else f"{v:g} px")
                  for v in (0, 2, 4, 6, 8, 12, 16, 24, 32, 50)]
_ELEMENT_COLORS = [
    ("#FF5470", "Rojo"), ("#F5B851", "Ámbar"), ("#7C5CFF", "Violeta"),
    ("#4CD6A0", "Verde"), ("#4AA8FF", "Azul"), ("#FFFFFF", "Blanco"), ("#000000", "Negro"),
]
_ELEMENT_FITS_UI = [("fit", "Encajar"), ("fill", "Rellenar"), ("stretch", "Estirar")]


def _options(options, current):
    """Las opciones fijas más el valor actual si no está entre ellas (p. ej. de un proyecto viejo)."""
    if all(abs(v - current) > 1e-6 for v, _ in options):
        return sorted([*options, (current, f"{current:g}")])
    return options


def _string_options(options, current):
    """Igual que `_options` para valores de texto (colores, encajes…)."""
    if all(str(v).upper() != str(current).upper() for v, _ in options):
        return [*options, (current, f"Personalizado ({current})")]
    return options


def _editor():
    from ..editor_controller import EDITOR

    return EDITOR


def _call(name: str, *args):
    def handler() -> None:
        editor = _editor()
        if editor is not None:
            getattr(editor, name)(*args)

    return handler


# -- barra superior ---------------------------------------------------------------


@component
def editor_toolbar():
    project = state.editor_project()
    dirty = state.project_dirty()
    read_only = state.project_read_only()
    undo, redo = state.undo_label(), state.redo_label()
    importing = state.importing()
    return row(gap=8, align="center", shrink=0)[
        image(icons.CUT, width=18),
        text(project.name, size=15, weight=700, max_lines=1),
        read_only and chip("solo lectura", tone="muted"),
        not read_only and chip("sin guardar" if dirty else "guardado", tone="text" if dirty else "muted"),
        importing and chip(f"analizando {importing[0]}…", tone="accent"),
        spacer(),
        export_chip(),
        icon_button(icons.SAVE, _call("save"), tooltip="Guardar (Ctrl+S)", size=32, key="ed-save"),
        icon_button(icons.UNDO, _call("undo"), tooltip=f"Deshacer: {undo} (Ctrl+Z)" if undo else "Nada que deshacer",
                    size=32, key="ed-undo", opacity=1.0 if undo else 0.45),
        icon_button(icons.REDO, _call("redo"), tooltip=f"Rehacer: {redo} (Ctrl+Y)" if redo else "Nada que rehacer",
                    size=32, key="ed-redo", opacity=1.0 if redo else 0.45),
        icon_button(icons.PANEL, _call("toggle_inspector"),
                    tooltip=("Ocultar" if state.inspector_open() else "Mostrar") + " el inspector (P)", size=32,
                    key="ed-panel", bg="$surface_pressed" if state.inspector_open() else "$surface_hover"),
        box(width=8),
        icon_button(icons.MUSIC, _call("browse_music"), tooltip="Añadir música", size=32, key="ed-music"),
        icon_button(icons.TEXT, _call("add_text"), tooltip="Añadir texto en el playhead", size=32, key="ed-text"),
        icon_button(icons.IMAGE, _call("browse_images"), tooltip="Añadir imagen como elemento",
                    size=32, key="ed-image"),
        button("Importar", _call("open_import"), variant="soft", padding=(6, 14), font_size=12.5, key="ed-import"),
        button("Exportar…", _call("open_export"), variant="soft", padding=(6, 14), font_size=12.5, key="ed-export"),
        button("Cerrar", _call("close_project"), variant="ghost", padding=(6, 12), font_size=12.5, key="ed-close"),
    ]


# -- preview y transporte -----------------------------------------------------------


_CANVAS_HANDLE = 12.0
_HANDLE_LABELS = {
    "tl": "esquina superior izquierda",
    "tr": "esquina superior derecha",
    "bl": "esquina inferior izquierda",
    "br": "esquina inferior derecha",
}


def _measure_preview(e) -> None:  # noqa: ANN001
    """Mide el hueco del preview con `on_resize`; solo escribe si cambia (evita bucles).

    Deja en `preview_area` el hueco disponible y en `preview_box` el tamaño real del escenario
    (con el aspecto del proyecto), que es la referencia que usan tanto la capa del clip (A,
    docs/23) como la caja del elemento (C, docs/25).
    """
    area = state.PreviewArea(int(round(e.width)), int(round(e.height)))
    if area != state.preview_area():
        state.preview_area.set(area)
    project = state.editor_project()
    stage = ce.fit_stage(project.width, project.height, area.width, area.height)
    box = (float(stage[0]), float(stage[1]))
    if box != state.preview_box():
        state.preview_box.set(box)


def _canvas_drag(kind: str, handle: str | None = None):
    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is None:
            return
        if e.phase == "start":
            editor.begin_canvas_drag(kind, handle)
        elif e.phase == "move":
            editor.drag_canvas(e.dx, e.dy)
        else:
            editor.drag_canvas(e.dx, e.dy)
            editor.end_canvas_drag()

    return handler


def _element_drag(element_id: str, mode: str):
    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is None:
            return
        if e.phase == "start":
            editor.begin_element_drag(element_id, mode)
        elif e.phase == "move":
            editor.drag_element(e.dx, e.dy)
        else:
            editor.drag_element(e.dx, e.dy)
            editor.end_drag()

    return handler


def _canvas_layer(project, stage_w: int, stage_h: int):  # noqa: ANN001, ANN202
    """Caja y asas del clip seleccionado sobre el escenario (feature A, docs/23).

    Solo aparece con un clip de video seleccionado y el preview en pausa. La caja es la del
    clip seleccionado aunque el playhead esté mostrando otro (el inspector sigue siendo la
    referencia numérica). Durante la reproducción se oculta para no editar a ciegas sobre un
    frame que cambia. Las asas se anclan a la esquina real del clip, pero sus centros se meten
    dentro del escenario si el clip sobresale, para que sigan siendo pulsables.
    """
    selection = state.selection()
    if not selection or selection[0] != "clip" or state.playing() or state.project_read_only():
        return None
    clip_id = selection[1]
    if not project.has_clip(clip_id):
        return None
    clip = project.clip(clip_id)
    try:
        lay = tx.layout(project, clip, (stage_w, stage_h))
    except ProjectError:
        return None
    if ce.visible_rect(lay) is None:
        return None
    children = [
        box(
            key="canvas-body",
            position="absolute",
            left=lay.x,
            top=lay.y,
            width=lay.out_width,
            height=lay.out_height,
            bg="#00000000",
            border_width=2,
            border_color="$primary",
            radius=2,
            cursor="grab",
            tooltip="Arrastra para mover el clip en el lienzo",
            label="Mover el clip en el lienzo",
            on_drag=_canvas_drag("move"),
        )
    ]
    for name, (cx, cy) in ce.handle_centres(lay, _CANVAS_HANDLE).items():
        children.append(box(
            key=f"canvas-handle-{name}",
            position="absolute",
            left=cx - _CANVAS_HANDLE / 2,
            top=cy - _CANVAS_HANDLE / 2,
            width=_CANVAS_HANDLE,
            height=_CANVAS_HANDLE,
            radius=3,
            bg="$primary",
            border_width=1,
            border_color="#FFFFFF",
            cursor=ce.HANDLE_CURSORS[name],
            tooltip=f"Arrastra para escalar desde la {_HANDLE_LABELS[name]}",
            label=f"Redimensionar desde la {_HANDLE_LABELS[name]}",
            on_drag=_canvas_drag("scale", name),
        ))
    return children


def _preview_overlay(project, panel: tuple[float, float]):
    """Caja con asas del elemento seleccionado (solo en pausa y si está activo en el playhead).

    `panel` es el tamaño del escenario (ya con el aspecto del proyecto), así las coordenadas
    de `element_overlay.element_box` coinciden con la caja del stage de A (docs/23/docs/25).
    """
    selection = state.selection()
    if state.playing() or not selection or selection[0] != "element":
        return None
    if not project.has_element(selection[1]):
        return None
    element = project.element(selection[1])
    if not (element.start <= state.playhead() < element.end):
        return None
    frame_box = element_overlay.element_box(project, element, panel)
    if frame_box is None or frame_box.width < 2 or frame_box.height < 2:
        return None
    handle = 12
    return box(
        key="el-box", position="absolute", left=frame_box.left, top=frame_box.top,
        width=frame_box.width, height=frame_box.height, bg="#00000000",
        border_width=1.5, border_color="#7C5CFF", cursor="grab",
        label="Caja del elemento seleccionado", tooltip="Arrastra para mover el elemento",
        on_drag=_element_drag(element.id, "move"),
    )[
        box(key="el-handle-se", position="absolute", right=-handle / 2, bottom=-handle / 2,
            width=handle, height=handle, radius=handle / 2, bg="#7C5CFF", cursor="nwse-resize",
            label="Asa inferior derecha", on_drag=_element_drag(element.id, "se")),
        box(key="el-handle-sw", position="absolute", left=-handle / 2, bottom=-handle / 2,
            width=handle, height=handle, radius=handle / 2, bg="#7C5CFF", cursor="nesw-resize",
            label="Asa inferior izquierda", on_drag=_element_drag(element.id, "sw")),
        box(key="el-handle-ne", position="absolute", right=-handle / 2, top=-handle / 2,
            width=handle, height=handle, radius=handle / 2, bg="#7C5CFF", cursor="nesw-resize",
            label="Asa superior derecha", on_drag=_element_drag(element.id, "ne")),
        box(key="el-handle-nw", position="absolute", left=-handle / 2, top=-handle / 2,
            width=handle, height=handle, radius=handle / 2, bg="#7C5CFF", cursor="nwse-resize",
            label="Asa superior izquierda", on_drag=_element_drag(element.id, "nw")),
    ]


@component
def preview_panel():
    frame = state.preview_frame()
    note = state.preview_note()
    project = state.editor_project()
    has_clips = bool(project.video.clips)
    area = state.preview_area()
    stage_w, stage_h = ce.fit_stage(project.width, project.height, area.width, area.height)
    # El hueco lo decide el layout (`width/height=0` + `grow`), nunca el contenido, y se mide
    # con `on_resize`. Con la medida, el frame se pinta en un escenario de tamaño fijo con el
    # aspecto del proyecto; dentro viven la capa del clip (A, docs/23) y la del elemento
    # (C, docs/25), ambas con coordenadas relativas a este escenario.
    if frame:
        content = (
            box(key="ed-stage", width=stage_w, height=stage_h, shrink=0, bg=project.background,
                overflow="hidden", radius=2)[
                image(frame, fit="fill", width="100%", height="100%", key="ed-preview-img"),
                _preview_overlay(project, (stage_w, stage_h)),
                _canvas_layer(project, stage_w, stage_h),
            ]
            if stage_w > 1 and stage_h > 1
            else image(frame, fit="contain", grow=1, height=0, width="100%", key="ed-preview-img")
        )
        body = column(grow=1, height=0, align="center", justify="center", key="ed-preview-body",
                      on_resize=_measure_preview)[content]
    else:
        body = column(grow=1, height=0, align="center", justify="center", gap=6,
                      key="ed-preview-body", on_resize=_measure_preview)[
            text("Sin imagen" if has_clips else "El timeline está vacío", size=14, weight=600, color="$muted"),
            text("Importa un video (Ctrl+I) para empezar" if not has_clips else "decodificando…",
                 size=12, color="$muted"),
        ]
    return column(key="ed-preview", grow=1, height=0, min_height=160, bg="#000000", radius="$lg",
                  border_width=1, border_color="$border", padding=6, gap=4)[
        body,
        row(justify="center")[text(note, size=11, color="#F5B851")] if note else None,
    ]


def _rate_label(rate: float) -> str:
    if rate == 1.0:
        return ""
    return f"◀ {abs(rate):g}×" if rate < 0 else f"{rate:g}× ▶"


@component
def transport():
    project = state.editor_project()
    t = state.playhead()
    playing = state.playing()
    total = tl.total_duration(project)
    audio = state.preview_audio()
    frame = tl.frame_index(t, project.fps)
    loop = state.loop_on()
    rate = state.play_rate() if playing else 1.0
    has_range = tl.has_range(project)
    return row(key="ed-transport", gap=6, align="center", shrink=0)[
        icon_button(icons.TO_START, _call("go_start"), tooltip="Inicio (Inicio)", size=32, key="tp-start"),
        icon_button(icons.STEP_BACK, _call("step", -1), tooltip="−1 fotograma (←)", size=32, key="tp-back"),
        icon_button(icons.PAUSE if playing else icons.PLAY, _call("toggle_play"),
                    tooltip="Pausa (Espacio)" if playing else "Reproducir (Espacio)", size=38, key="tp-play"),
        icon_button(icons.STEP_FWD, _call("step", 1), tooltip="+1 fotograma (→)", size=32, key="tp-fwd"),
        icon_button(icons.TO_END, _call("go_end"), tooltip="Final (Fin)", size=32, key="tp-end"),
        box(width=10),
        text(tl.format_time(t), key="tp-time", size=15, weight=700, family="Consolas", letter_spacing=0.2),
        text(f"/ {tl.format_time(total)}", size=13, color="$muted", family="Consolas"),
        rate != 1.0 and chip(_rate_label(rate), tone="accent"),
        box(width=6),
        icon_button(icons.MARK_IN, _call("set_range_in"), tooltip="Punto de entrada del timeline (I)",
                    size=28, key="tp-in", bg="$surface_pressed" if project.range_in is not None else "$surface_hover"),
        icon_button(icons.MARK_OUT, _call("set_range_out"), tooltip="Punto de salida del timeline (O)",
                    size=28, key="tp-out", bg="$surface_pressed" if project.range_out is not None else "$surface_hover"),
        icon_button(icons.LOOP, _call("toggle_loop"),
                    tooltip=("Loop: " + ("activado" if loop else "desactivado")
                             + (" · rango In/Out" if has_range else " · todo el timeline") + " (Ctrl+L)"),
                    size=28, key="tp-loop", bg="$primary" if loop else "$surface_hover"),
        spacer(),
        text(f"fotograma {frame} · {project.fps} fps", size=11.5, color="$muted", family="Consolas"),
        icon_button(icons.SPEAKER if audio else icons.MUTE, _call("toggle_preview_audio"),
                    tooltip="Audio del preview (M) · best-effort", size=30, key="tp-audio"),
    ]


# -- inspector -------------------------------------------------------------------------


def _row(label: str, value):
    # El texto de la izquierda no es el nombre accesible del control (Aurora 0.10 lo avisa como
    # `no-label`); se lo damos al control sin cambiar el layout.
    if not isinstance(value, str):
        label_control(value, label)
    return row(gap=8, align="center")[
        text(label, size=12.5, color="$muted", width=74, shrink=0),
        box(grow=1)[value] if not isinstance(value, str) else text(value, size=12.5, family="Consolas"),
    ]


def _time_field(signal, on_submit, key: str, mark, mark_tip: str, mark_icon: bytes):
    return row(gap=6, align="center")[
        text_input(signal, key=key, on_submit=on_submit, on_key=mark_input_key, grow=1, width=0,
                   font_family="Consolas", padding=(7, 10)),
        icon_button(mark_icon, mark, tooltip=mark_tip, size=30, key=f"{key}-mark"),
    ]


def _effects_controls(clip):
    from ...core import effects as fx

    fin, fout = fx.fade_in(clip), fx.fade_out(clip)
    gain = fx.gain(clip)
    factor = fx.zoom(clip)[0]
    eq = fx.eq(clip) or fx.EQ_DEFAULTS
    return section(
        "Efectos",
        _row("Volumen", select(state.insp_volume, _options(_VOLUMES, gain), key="insp-volume")),
        _row("Fundido ↗", select(state.insp_fade_in, _options(_FADES, fin), key="insp-fade-in")),
        _row("Fundido ↘", select(state.insp_fade_out, _options(_FADES, fout), key="insp-fade-out")),
        _row("Zoom", select(state.insp_zoom, _options(_ZOOMS, factor), key="insp-zoom")),
        _row("Brillo", select(state.insp_brightness, _options(_BRIGHTNESS, eq["brightness"]), key="insp-bright")),
        _row("Contraste", select(state.insp_contrast, _options(_CONTRAST, eq["contrast"]), key="insp-contrast")),
        _row("Saturación", select(state.insp_saturation, _options(_SATURATION, eq["saturation"]), key="insp-sat")),
        row(gap=8, align="center")[
            text("Fundidos de imagen (a negro) y de audio.", size=11, color="$muted", grow=1, width=0,
                 line_height=1.35),
            clip.effects and button("Quitar efectos", _call("reset_effects"), variant="ghost", padding=(4, 8),
                                    font_size=11.5, key="insp-fx-reset"),
        ],
        gap=8,
    )


def _color_options(current: str):
    if all(value.upper() != current.upper() for value, _label in _CANVAS_COLORS):
        return [*_CANVAS_COLORS, (current, f"Personalizado ({current})")]
    return _CANVAS_COLORS


def _global_effects_controls():
    return section(
        "Efectos globales",
        _row("Brillo", select(state.glob_brightness, _options(_BRIGHTNESS, state.glob_brightness()),
                              key="proj-global-bright")),
        _row("Contraste", select(state.glob_contrast, _options(_CONTRAST, state.glob_contrast()),
                                 key="proj-global-contrast")),
        _row("Saturación", select(state.glob_saturation,
                                  _options(_SATURATION, state.glob_saturation()),
                                  key="proj-global-sat")),
        _row("Blur", select(state.glob_blur, _options(_BLURS, state.glob_blur()),
                            key="proj-global-blur")),
        _row("Viñeta", select(state.glob_vignette, _options(_VIGNETTES, state.glob_vignette()),
                              key="proj-global-vignette")),
        _row("Blanco y negro", select(state.glob_grayscale, _FLIPS, key="proj-global-gray")),
        text("Se aplican a todo el video (también en las transiciones), antes de elementos y "
             "textos. El corte rápido se desactiva mientras haya efectos globales.",
             size=11, color="$muted", line_height=1.35),
        button("Restablecer efectos globales", _call("reset_global_effects"), variant="ghost",
               padding=(4, 10), font_size=11.5, key="proj-global-reset"),
        gap=8,
    )


def _transform_controls(clip):
    from ...core.project import Transform

    transform = clip.transform or Transform()
    return section(
        "Transformación",
        _row("Encaje", select(state.insp_fit, _CLIP_FITS, key="insp-fit")),
        _row("Escala", select(state.insp_scale, _options(_SCALES, transform.scale), key="insp-scale")),
        _row("Posición X", select(state.insp_pos_x, _options(_POSITIONS, transform.x), key="insp-pos-x")),
        _row("Posición Y", select(state.insp_pos_y, _options(_POSITIONS, transform.y), key="insp-pos-y")),
        _row("Rotación", select(state.insp_rotation, _ROTATIONS, key="insp-rotation")),
        _row("Voltear H", select(state.insp_flip_h, _FLIPS, key="insp-flip-h")),
        _row("Voltear V", select(state.insp_flip_v, _FLIPS, key="insp-flip-v")),
        _row("Recorte L", text_input(state.insp_crop_l, key="insp-crop-l",
                                     on_submit=_submit_crop(0), on_key=mark_input_key,
                                     font_family="Consolas", padding=(7, 10))),
        _row("Recorte T", text_input(state.insp_crop_t, key="insp-crop-t",
                                     on_submit=_submit_crop(1), on_key=mark_input_key,
                                     font_family="Consolas", padding=(7, 10))),
        _row("Recorte R", text_input(state.insp_crop_r, key="insp-crop-r",
                                     on_submit=_submit_crop(2), on_key=mark_input_key,
                                     font_family="Consolas", padding=(7, 10))),
        _row("Recorte B", text_input(state.insp_crop_b, key="insp-crop-b",
                                     on_submit=_submit_crop(3), on_key=mark_input_key,
                                     font_family="Consolas", padding=(7, 10))),
        text("0–1 normalizado: (0,0,1,1) es el clip entero. La rotación se aplica después de la "
             "auto-rotación del archivo.", size=11, color="$muted", line_height=1.35),
        button("Restablecer transformación", _call("reset_transform"), variant="ghost",
               padding=(4, 10), font_size=11.5, key="insp-transform-reset"),
        gap=8,
    )


def _audio_fade_rows(prefix: str = "insp-audio"):
    return (
        _row("Fundido audio ↗", select(
            state.insp_audio_fade_in, _options(_FADES, state.insp_audio_fade_in()),
            key=f"{prefix}-fade-in")),
        _row("Fundido audio ↘", select(
            state.insp_audio_fade_out, _options(_FADES, state.insp_audio_fade_out()),
            key=f"{prefix}-fade-out")),
    )


def _video_audio_controls(project, clip):
    note = (
        "El audio del clip se separó a una pista absoluta."
        if not clip.audio_enabled
        else "El audio propio del video se puede separar; si no ajustas estos fundidos, hereda los del video."
    )
    return section(
        "Audio del clip",
        *(_audio_fade_rows("insp-audio")),
        text(note, size=11, color="$muted", line_height=1.35),
        gap=8,
    )


def _transition_controls(index: int | None):
    if index is None:
        return None
    project = state.editor_project()
    current = project.video.transitions[index]
    limit = tl.transition_limit(project.video, index) if index < len(project.video.transitions) else 0
    durations = [(d, f"{d:g} s") for d in (0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0) if d <= limit + 1e-9]
    if current is not None and all(abs(d - current.duration) > 1e-6 for d, _ in durations):
        durations.append((current.duration, f"{current.duration:.2f} s"))
    kinds = [("none", _TRANSITION_LABELS["none"])] + [(k, _TRANSITION_LABELS[k]) for k in TRANSITION_KINDS]
    return section(
        f"Transición {index + 1} → {index + 2}",
        _row("Tipo", select(state.insp_transition, kinds, key="insp-tr-kind")),
        current is not None and _row("Duración", select(state.insp_transition_dur, durations, key="insp-tr-dur")),
        limit < 0.2 and text("Clips demasiado cortos para una transición (mín. 0.4 s cada uno).",
                             size=11.5, color="$muted"),
        gap=8,
    )


def _element_controls(project, element):
    label = element_mod.ELEMENT_LABELS.get(element.kind, element.kind)
    children = [
        text(f"{label} · {tl.format_time(element.start)}–{tl.format_time(element.end)}",
             size=12, color="$muted", family="Consolas"),
        _row("Inicio", text_input(state.insp_el_start, key="insp-el-start",
                                  on_submit=_submit("submit_element_start"), on_key=mark_input_key,
                                  font_family="Consolas", padding=(7, 10))),
        _row("Fin", text_input(state.insp_el_end, key="insp-el-end",
                               on_submit=_submit("submit_element_end"), on_key=mark_input_key,
                               font_family="Consolas", padding=(7, 10))),
        _row("Duración", tl.format_time(element.duration)),
        _row("X", select(state.insp_el_x, _options(_ELEMENT_POSITIONS, element.x), key="insp-el-x")),
        _row("Y", select(state.insp_el_y, _options(_ELEMENT_POSITIONS, element.y), key="insp-el-y")),
        _row("Ancho", select(state.insp_el_width, _options(_ELEMENT_SIZES, element.width),
                             key="insp-el-width")),
        _row("Alto", select(state.insp_el_height, _options(_ELEMENT_SIZES, element.height),
                            key="insp-el-height")),
        _row("Rotación", select(state.insp_el_rotation,
                                _options(_ELEMENT_ROTATIONS, element.rotation), key="insp-el-rotation")),
        _row("Opacidad", select(state.insp_el_opacity,
                                _options(_ELEMENT_OPACITIES, element.opacity), key="insp-el-opacity")),
    ]
    if element.kind == "image":
        try:
            asset = project.asset(element.asset_id or "")
            children.append(text(asset.name, size=11.5, color="$muted", max_lines=1))
        except Exception:
            pass
        children.append(_row("Encaje", select(state.insp_el_fit, _ELEMENT_FITS_UI,
                                              key="insp-el-fit")))
    else:
        children.append(_row("Color", select(
            state.insp_el_color, _string_options(_ELEMENT_COLORS, element.color),
            key="insp-el-color",
        )))
    children += [
        _row("Desenfoque", select(state.insp_el_blur,
                                  _options(_ELEMENT_BLURS, element_mod.blur_radius(element)),
                                  key="insp-el-blur")),
        text("Se compone sobre el video en el preview y en el export; en la lista, lo de arriba "
             "queda encima.", size=11, color="$muted", line_height=1.35),
        row(gap=6)[
            button("Al frente", _call("move_element_z", "front"), variant="ghost",
                   padding=(4, 9), font_size=11.5, key="insp-el-front"),
            button("Al fondo", _call("move_element_z", "back"), variant="ghost",
                   padding=(4, 9), font_size=11.5, key="insp-el-back"),
        ],
        row(gap=6)[
            button("Subir", _call("move_element_z", "up"), variant="ghost", padding=(4, 9),
                   font_size=11.5, key="insp-el-up"),
            button("Bajar", _call("move_element_z", "down"), variant="ghost", padding=(4, 9),
                   font_size=11.5, key="insp-el-down"),
            button("Eliminar", _call("delete_selected"), variant="soft", padding=(4, 9),
                   font_size=11.5, key="insp-el-delete"),
        ],
    ]
    return section(f"Elemento · {label}", *children, gap=9)


def _elements_section(project):
    rows = []
    for element in reversed(project.elements):  # la lista se lee de arriba (encima) abajo
        label = element_mod.ELEMENT_LABELS.get(element.kind, element.kind)
        if element.kind == "image":
            try:
                name = project.asset(element.asset_id or "").name
                label = f"Imagen · {name}"
            except Exception:
                label = "Imagen"
        rows.append(row(
            key=f"el-{element.id}", gap=8, align="center", padding=(5, 7), radius="$md",
            bg_hover="$surface_hover", cursor="pointer", label=f"Seleccionar {label}",
            on_click=_call("select_element", element.id),
        )[
            text(label, size=12, grow=1, max_lines=1),
            text(f"{element.start:.1f}–{element.end:.1f}s", size=10.5, color="$muted",
                 family="Consolas"),
        ])
    return section(
        "Elementos",
        text("Figuras e imágenes por encima del video; el orden de la lista es el z-order "
             "(arriba = encima).", size=11, color="$muted", line_height=1.35),
        rows and column(gap=3)[rows],
        not rows and text("Todavía no hay elementos.", size=11.5, color="$muted"),
        row(gap=6)[
            button("Rectángulo", _call("add_element", "rect"), variant="soft", padding=(4, 9),
                   font_size=11.5, key="insp-el-rect"),
            button("Elipse", _call("add_element", "ellipse"), variant="soft", padding=(4, 9),
                   font_size=11.5, key="insp-el-ellipse"),
        ],
        row(gap=6)[
            button("Triángulo", _call("add_element", "triangle"), variant="soft", padding=(4, 9),
                   font_size=11.5, key="insp-el-triangle"),
            button("Añadir imagen…", _call("browse_images"), variant="soft", padding=(4, 9),
                   font_size=11.5, key="insp-el-image"),
        ],
        gap=7,
    )


@component
def inspector():
    from ..editor_controller import CANVAS_LABELS, SPEED_CHOICES

    project = state.editor_project()
    selection = state.selection()
    children = []

    if selection and selection[0] == "clip" and project.has_clip(selection[1]):
        clip = project.clip(selection[1])
        index = project.video.index_of(clip.id)
        try:
            asset = project.asset(clip.asset_id)
            name, offline = asset.name, asset.id in state.offline_assets()
            has_audio = asset.has_audio
        except Exception:
            name, offline, has_audio = "?", True, False
        linked = any(c.link_id == clip.id for track in project.audio for c in track.clips)
        audio_action = None
        if clip.audio_enabled and has_audio:
            audio_action = button("Separar audio", _call("detach_audio"), variant="soft",
                                  padding=(4, 10), font_size=11.5, key="insp-detach")
        elif not clip.audio_enabled and not linked:
            audio_action = button("Activar audio", _call("attach_audio"), variant="soft",
                                  padding=(4, 10), font_size=11.5, key="insp-enable-audio")
        children.append(section(
            f"Clip {index + 1} de {len(project.video.clips)}",
            text(name, size=13.5, weight=600, max_lines=2),
            linked and chip("audio enlazado · pista «Audio del video»", tone="accent"),
            offline and row(gap=8, align="center")[
                text("Archivo no encontrado (offline)", size=11.5, color="$danger", grow=1, width=0),
                button("Reubicar…", _call("relocate_asset", clip.asset_id), variant="soft", padding=(4, 10),
                       font_size=11.5, key="insp-relocate"),
                button("Carpeta…", _call("relocate_folder"), variant="soft", padding=(4, 10),
                       font_size=11.5, key="insp-relocate-folder"),
            ],
            _row("Entrada", _time_field(state.insp_in, _submit("submit_in"), "insp-in", _call("mark_in"),
                                        "Entrada = playhead", icons.STEP_BACK)),
            _row("Salida", _time_field(state.insp_out, _submit("submit_out"), "insp-out", _call("mark_out"),
                                       "Salida = playhead", icons.STEP_FWD)),
            _row("Duración", tl.format_time(clip.duration)),
            _row("Velocidad", select(state.insp_speed, [(s, f"{s:g}×") for s in SPEED_CHOICES], key="insp-speed")),
            audio_action and row(gap=8)[audio_action],
            linked and row(gap=8)[
                button("Desvincular audio", _call("unlink_audio"), variant="soft",
                       padding=(4, 10), font_size=11.5, key="insp-unlink",
                       tooltip="Mantiene el audio en su pista y corta el enlace con el video"),
                button("Vincular audio", _call("attach_audio"), variant="soft",
                       padding=(4, 10), font_size=11.5, key="insp-attach",
                       tooltip="Borra el audio separado y reactiva el audio original del video"),
            ],
            not clip.audio_enabled and not linked and text(
                "El audio separado sigue libre en su pista: selecciónalo para volver a vincularlo "
                "o usa «Activar audio» para recuperar el sonido original del video.",
                size=11, color="$muted", line_height=1.35,
            ),
            gap=10,
        ))
        children.append(_effects_controls(clip))
        children.append(_transform_controls(clip))
        children.append(_video_audio_controls(project, clip))
        if index < len(project.video.transitions):
            children.append(_transition_controls(index))
        children.append(row(gap=8)[
            button("✂ Dividir (S)", _call("split"), variant="soft", padding=(6, 12), font_size=12.5, key="insp-split"),
            button("Eliminar", _call("delete_selected"), variant="soft", padding=(6, 12), font_size=12.5,
                   key="insp-delete"),
        ])
    elif (
        selection and selection[0] == "audio" and len(selection) > 2
        and 0 <= int(selection[1]) < len(project.audio)
        and 0 <= int(selection[2]) < len(project.audio[int(selection[1])].clips)
    ):
        track_index, clip_index = int(selection[1]), int(selection[2])
        track = project.audio[track_index]
        clip = track.clips[clip_index]
        start = tl.clip_starts(track)[clip_index]
        try:
            asset = project.asset(clip.asset_id)
            asset_name, offline = asset.name, asset.id in state.offline_assets()
        except Exception:
            asset_name, offline = "?", True
        relinkable = (
            track.mode == "absolute" and not clip.link_id
            and any(video.asset_id == clip.asset_id for video in project.video.clips)
        )
        children.append(section(
            f"Audio · {track.name or f'Pista {track_index + 1}'}",
            text(asset_name, size=13.5, weight=600, max_lines=2),
            clip.link_id and chip("enlazado al video · sigue sus cortes", tone="accent"),
            clip.link_id and row(gap=8)[
                button("Desvincular audio", _call("unlink_audio"), variant="soft",
                       padding=(4, 10), font_size=11.5, key="insp-audio-unlink",
                       tooltip="Mantiene el audio en su pista y corta el enlace con el video"),
                button("Vincular audio", _call("attach_audio"), variant="soft",
                       padding=(4, 10), font_size=11.5, key="insp-audio-attach",
                       tooltip="Borra este audio separado y reactiva el audio original del video"),
            ],
            relinkable and button("Vincular al video", _call("relink_audio"), variant="soft",
                                  padding=(4, 10), font_size=11.5, key="insp-audio-relink",
                                  tooltip="Conserva este audio y lo vuelve a enlazar con su video"),
            offline and button("Reubicar…", _call("relocate_asset", clip.asset_id), variant="soft",
                               padding=(4, 10), font_size=11.5, key="insp-audio-relocate"),
            _row("Inicio", text_input(state.insp_audio_start, key="insp-audio-start",
                                      on_submit=_submit("submit_audio_start"), on_key=mark_input_key,
                                      font_family="Consolas", padding=(7, 10))
                 if track.mode == "absolute" else text(
                     f"{tl.format_time(start)} (secuencial)", size=12, family="Consolas")),
            _row("Fin", text_input(state.insp_audio_end, key="insp-audio-end",
                                   on_submit=_submit("submit_audio_end"), on_key=mark_input_key,
                                   font_family="Consolas", padding=(7, 10))),
            _row("Duración", tl.format_time(clip.duration)),
            _row("Pista", f"{track.name or f'Pista {track_index + 1}'} · "
                          f"{'absoluta' if track.mode == 'absolute' else 'secuencial'}"),
            gap=10,
        ))
        children.append(section(
            "Audio",
            _row("Volumen", select(state.insp_music_clip_gain, _options(_VOLUMES, state.insp_music_clip_gain()),
                                   key="insp-audio-clip-gain")),
            *_audio_fade_rows("insp-audio-clip"),
            gap=8,
        ))
        children.append(section(
            "Pista",
            _row("Volumen", select(state.insp_music_gain,
                                   _options([(0.0, "Silenciada"), *_VOLUMES[1:]],
                                            0.0 if track.muted else track.gain),
                                   key="insp-music-gain")),
            _row("Ducking", select(state.insp_music_duck,
                                   [(0.0, "Sin ducking"), (0.25, "Suave"), (0.5, "Medio"),
                                    (0.75, "Fuerte"), (1.0, "Máximo")], key="insp-music-duck")),
            track.mode == "sequence" and len(track.clips) > 1 and _row("Crossfade", select(
                state.insp_music_cross,
                _options([(v, "Sin crossfade" if v == 0 else f"{v:g} s")
                          for v in (0.0, 0.25, 0.5, 1.0, 2.0)], state.insp_music_cross()),
                key="insp-music-cross")),
            text("Ducking baja esta pista cuando suena la voz o el audio del sistema.",
                 size=11, color="$muted", line_height=1.35),
            gap=8,
        ))
        children.append(row(gap=8)[
            button("Eliminar", _call("delete_selected"), variant="soft", padding=(6, 12),
                   font_size=12.5, key="insp-audio-delete"),
        ])
    elif selection and selection[0] == "element" and project.has_element(selection[1]):
        children.append(_element_controls(project, project.element(selection[1])))
    elif selection and selection[0] == "text" and project.has_text(selection[1]):
        overlay = project.text(selection[1])
        children.append(section(
            "Texto",
            text_input(state.insp_text, key="insp-text", on_submit=_submit("set_text_text"),
                       on_key=mark_input_key, grow=1, width=0, padding=(7, 10), label="Texto"),
            _row("Inicio", text_input(state.insp_text_start, key="insp-text-start",
                                      on_submit=_submit("set_text_start"), on_key=mark_input_key,
                                      font_family="Consolas", padding=(7, 10))),
            _row("Fin", text_input(state.insp_text_end, key="insp-text-end",
                                   on_submit=_submit("set_text_end"), on_key=mark_input_key,
                                   font_family="Consolas", padding=(7, 10))),
            _row("Tamaño", select(state.insp_text_size,
                                  [(v, f"{v} px") for v in (24, 32, 42, 56, 72, 96, 128)],
                                  key="insp-text-size")),
            _row("Color", select(state.insp_text_color,
                                 [("#FFFFFF", "Blanco"), ("#F5B851", "Ámbar"), ("#FF5470", "Rojo"),
                                  ("#7C5CFF", "Violeta"), ("#000000", "Negro")], key="insp-text-color")),
            _row("X", select(state.insp_text_x,
                             [(v, f"{v:.2f}") for v in (0.0, 0.25, 0.5, 0.75, 1.0)], key="insp-text-x")),
            _row("Y", select(state.insp_text_y,
                             [(v, f"{v:.2f}") for v in (0.1, 0.5, 0.82, 0.95)], key="insp-text-y")),
            _row("Estilo", select(state.insp_text_bold,
                                  [(False, "Normal"), (True, "Negrita")], key="insp-text-bold")),
            text("Se quema en el video exportado; en el preview se dibuja con la misma posición.",
                 size=11, color="$muted", line_height=1.35),
            button("Eliminar texto", _call("delete_text"), variant="ghost", padding=(4, 10),
                   font_size=11.5, key="insp-text-delete"),
            gap=10,
        ))
    elif selection and selection[0] == "marker" and project.has_marker(selection[1]):
        marker = project.marker(selection[1])
        children.append(section(
            "Marcador",
            _row("Nombre", text_input(state.insp_marker_name, key="insp-marker-name",
                                      on_submit=_submit("rename_marker"), on_key=mark_input_key,
                                      padding=(7, 10))),
            _row("Tiempo", tl.format_time(marker.t)),
            row(gap=8)[
                button("Ir al marcador", _call("select_marker", marker.id), variant="soft", padding=(6, 12),
                       font_size=12.5, key="insp-marker-go"),
                button("Eliminar", _call("delete_marker"), variant="soft", padding=(6, 12),
                       font_size=12.5, key="insp-marker-delete"),
            ],
            text("↑/↓ salta al marcador anterior/siguiente · B añade uno en el playhead · el imán engancha "
                 "a los marcadores.", size=11, color="$muted", line_height=1.35),
            gap=10,
        ))
    elif selection and selection[0] == "transition" and selection[1] < len(project.video.transitions):
        children.append(_transition_controls(int(selection[1])))
        children.append(text("Las transiciones se previsualizan de forma aproximada; el export usa xfade de FFmpeg.",
                             size=11.5, color="$muted", line_height=1.4))
    else:
        total = tl.total_duration(project)
        canvas_options = [(key, label) for key, label in CANVAS_LABELS.items()] + [
            ("custom", "Personalizado"),
        ]
        children.append(section(
            "Proyecto",
            _row("Duración", tl.format_time(total)),
            _row("Clips", str(len(project.video.clips))),
            _row("Lienzo", select(state.proj_canvas, canvas_options, key="proj-canvas")),
            _row("Ancho", text_input(state.proj_width, key="proj-width", on_submit=_submit_width,
                                     on_key=mark_input_key, font_family="Consolas", padding=(7, 10))),
            _row("Alto", text_input(state.proj_height, key="proj-height", on_submit=_submit_height,
                                    on_key=mark_input_key, font_family="Consolas", padding=(7, 10))),
            _row("Encaje", select(state.proj_fit, _PROJECT_FITS, key="proj-fit")),
            _row("Fondo", select(state.proj_background, _color_options(state.proj_background()),
                                 key="proj-bg")),
            _row("Hex", text_input(state.proj_bg_hex, key="proj-bg-hex", on_submit=_submit_background,
                                   on_key=mark_input_key, font_family="Consolas", padding=(7, 10))),
            _row("FPS", select(state.insp_fps, [(f, f"{f} fps") for f in (24, 30, 60)], key="insp-fps")),
            _row("Audio", select(state.insp_track_gain,
                                 _options([(0.0, "Silenciado"), *_VOLUMES[1:]],
                                          0.0 if project.video.muted else project.video.gain),
                                 key="insp-track-gain")),
            state.offline_assets() and button("Reubicar carpeta…", _call("relocate_folder"),
                                              variant="soft", padding=(5, 12), font_size=12,
                                              key="insp-relocate-all"),
            button("Exportar… (Ctrl+E)", _call("open_export"), padding=(7, 14), font_size=12.5, key="insp-export"),
            gap=10,
        ))
        children.append(_global_effects_controls())
        children.append(_elements_section(project))
        if project.texts:
            children.append(section(
                "Textos",
                column(gap=4)[[
                    row(key=f"text-{t.id}", gap=8, align="center", padding=(5, 7), radius="$md",
                        bg_hover="$surface_hover", cursor="pointer",
                        on_click=_call("select_text", t.id))[
                        image(icons.TEXT, width=14),
                        text(t.text, size=12, grow=1, max_lines=1),
                        text(f"{t.start:.1f}–{t.end:.1f}s", size=10.5, color="$muted",
                             family="Consolas"),
                    ]
                    for t in project.texts
                ]],
                gap=6,
            ))
        children.append(section(
            "Pistas de audio",
            column(gap=4)[[
                row(key=f"atrack-{i}", gap=8, align="center", padding=(5, 7), radius="$md",
                    bg="$surface_hover")[
                    text(track.name or f"Audio {i + 1}", size=12, grow=1, max_lines=1),
                    text("absoluta" if track.mode == "absolute" else "secuencial", size=10.5,
                         color="$muted"),
                    text(f"{track.gain * 100:.0f}%", size=10.5, color="$muted", family="Consolas"),
                    button("M", _call("toggle_audio_mute", i), variant="ghost", padding=(2, 6),
                           font_size=10, key=f"atrack-mute-{i}",
                           bg="$primary" if track.muted else "$surface_hover"),
                    button("S", _call("toggle_audio_solo", i), variant="ghost", padding=(2, 6),
                           font_size=10, key=f"atrack-solo-{i}",
                           bg="$primary" if track.solo else "$surface_hover"),
                    button("L", _call("toggle_audio_lock", i), variant="ghost", padding=(2, 6),
                           font_size=10, key=f"atrack-lock-{i}",
                           bg="$primary" if track.locked else "$surface_hover"),
                    button("Quitar", _call("remove_audio_track", i), variant="ghost", padding=(2, 8),
                           font_size=10.5, key=f"atrack-remove-{i}"),
                ]
                for i, track in enumerate(project.audio)
            ]],
            button("Añadir pista de audio", _call("add_audio_track"), variant="soft",
                   padding=(5, 10), font_size=11.5, key="insp-audio-add"),
            gap=6,
        ))
        children.append(section(
            "Atajos",
            text("Espacio reproducir · J/K/L atrás/pausa/adelante (2×, 4×, 8×) · ←/→ fotograma · "
                 "Mayús+←/→ 1 s · S dividir todo · Mayús+S dividir solo audio · Q/W recortar hasta "
                 "el playhead · C razor · V selección · Ctrl+C/X/V/D copiar/cortar/pegar/duplicar · "
                 "I/O entrada/salida · Mayús+I/O ir a ellas · Alt+X quitar In/Out · Ctrl+L loop · "
                 "B marcador · ↑/↓ marcadores · Supr eliminar · Ctrl+Z/Y deshacer/rehacer · "
                 "+/− zoom · 0 ajustar · M audio del preview · P inspector · Ctrl+E exportar · "
                 "Esc cancelar",
                 size=11.5, color="$muted", line_height=1.5),
        ))
    return column(key="ed-inspector", width=state.inspector_w(), grow=0, shrink=0, bg="$surface", radius="$lg",
                  border_width=1, border_color="$border")[
        scroll(key="insp-scroll", grow=1, padding=14, gap=16)[children]
    ]


def _submit(name: str):
    def handler(value: str) -> None:
        editor = _editor()
        if editor is not None:
            getattr(editor, name)(value)

    return handler


def _submit_crop(side: int):
    def handler(value: str) -> None:
        editor = _editor()
        if editor is not None:
            editor.submit_transform_crop(side, value)

    return handler


def _submit_width(value: str) -> None:
    editor = _editor()
    if editor is not None:
        editor.submit_project_size(value, state.proj_height())


def _submit_height(value: str) -> None:
    editor = _editor()
    if editor is not None:
        editor.submit_project_size(state.proj_width(), value)


def _submit_background(value: str) -> None:
    editor = _editor()
    if editor is not None:
        editor.submit_project_background(value)


# -- timeline -----------------------------------------------------------------------


@component
def timeline_toolbar():
    zoom = state.tl_zoom()
    snap_on = state.snap_on()
    has_selection = state.selection() is not None
    project = state.editor_project()
    start, end = tl.play_range(project)
    return row(gap=6, align="center", shrink=0)[
        icon_button(icons.ZOOM_OUT, _call("zoom_out"), tooltip="Alejar (−)", size=30, key="tl-zoom-out"),
        icon_button(icons.ZOOM_IN, _call("zoom_in"), tooltip="Acercar (+)", size=30, key="tl-zoom-in"),
        icon_button(icons.FIT, _call("zoom_fit"), tooltip="Ajustar al ancho (0)", size=30, key="tl-fit"),
        text(f"{zoom:.0f} px/s", size=11.5, color="$muted", family="Consolas", width=70),
        icon_button(icons.MAGNET, lambda: state.snap_on.set(not state.snap_on()),
                    tooltip="Imán: " + ("activado" if snap_on else "desactivado"), size=30, key="tl-snap",
                    bg="$primary" if snap_on else "$surface_hover"),
        icon_button(icons.SELECT, _call("toggle_marquee"),
                    tooltip="Selección múltiple: arrastra sobre el timeline (luego arrastra un clip "
                            "para mover el bloque)",
                    size=30, key="tl-marquee",
                    bg="$primary" if state.marquee_on() else "$surface_hover"),
        icon_button(icons.RAZOR, _call("toggle_razor"),
                    tooltip="Razor (C): clic en un clip para cortarlo ahí; en la regla, todas las pistas",
                    size=30, key="tl-razor",
                    bg="$primary" if state.razor_on() else "$surface_hover"),
        icon_button(icons.MARKER, _call("add_marker"), tooltip="Añadir marcador en el playhead (B)",
                    size=30, key="tl-marker"),
        box(width=6),
        icon_button(icons.CUT, _call("split"), tooltip="Dividir todo en el playhead (S)", size=30, key="tl-split"),
        icon_button(icons.SPLIT_AUDIO, _call("split_audio_only"),
                    tooltip="Dividir solo audio en el playhead (Mayús+S)", size=30, key="tl-split-audio"),
        icon_button(icons.TRACK_ADD, _call("add_audio_track"), tooltip="Añadir pista de audio",
                    size=30, key="tl-track-add"),
        icon_button(icons.TRACK_REMOVE, _call("remove_selected_audio_track"),
                    tooltip="Quitar la pista del clip de audio seleccionado", size=30, key="tl-track-remove"),
        icon_button(icons.TRASH, _call("delete_selected"), tooltip="Eliminar selección (Supr)", size=30,
                    key="tl-delete", opacity=1.0 if has_selection else 0.45),
        tl.has_range(project) and text(f"In/Out {tl.format_time(start)}–{tl.format_time(end)}", key="tl-range",
                                       size=11, color="$primary", family="Consolas", max_lines=1, shrink=0),
        spacer(),
        text("Razor activa: clic para cortar · V o Esc para salir" if state.razor_on() else
             "Arrastra clips y asas para mover/recortar · M/S/L en las cabeceras · la regla para moverte",
             size=11, color="$primary" if state.razor_on() else "$muted", max_lines=1),
    ]


def timeline_panel():
    return column(key="ed-timeline", gap=6, shrink=0, bg="$surface", radius="$lg", border_width=PANEL_BORDER,
                  border_color="$border", padding=(8, 0, 4, 0))[
        box(padding=(0, 10))[timeline_toolbar()],
        timeline_view(),
    ]


# -- estados vacíos e importación ---------------------------------------------------------


@component
def empty_state():
    recents = state.recent_projects()
    recordings = state.recordings()

    def open_recent(path: str):
        def handler() -> None:
            from pathlib import Path

            editor = _editor()
            if editor is not None:
                editor.open_project(Path(path))

        return handler

    def recent_row(i: int, item: state.RecentProject):
        return row(key=f"recent-{i}", gap=10, padding=(9, 12), radius="$md", bg="$surface_hover",
                   bg_hover="$surface_pressed", align="center", cursor="pointer", on_click=open_recent(item.path))[
            image(icons.CUT, width=16),
            text(item.name, size=13, weight=600, grow=1, max_lines=1),
            text(f"{item.clips} clips · {item.duration_label} · {item.modified_label}", size=11.5, color="$muted"),
        ]

    return column(grow=1, padding=(18, 20), gap=14)[
        text("Editor", size=22, weight=700),
        box(bg="$surface", radius="$lg", border_width=1, border_color="$border", padding=22, gap=12,
            align="center")[
            column(align="center", gap=8)[
                text("Abre una grabación para editarla", size=17, weight=600),
                text("Corta, reordena y ajusta la velocidad; el proyecto se guarda solo.", size=13, color="$muted"),
                row(gap=10)[
                    button("Ir a la Biblioteca", lambda: state.screen.set("library"), key="empty-library"),
                    button("Importar archivo…", _call("open_import"), variant="soft", key="empty-import"),
                ],
                recordings and text(f"{len(recordings)} grabaciones disponibles en la Biblioteca",
                                    size=11.5, color="$muted"),
            ]
        ],
        recents and section("Proyectos recientes", column(gap=6)[[recent_row(i, r) for i, r in enumerate(recents)]]),
    ]


@component
def import_modal():
    if not state.import_open():
        return column()
    recordings = state.recordings()
    importing = state.importing()

    def add(path):
        def handler() -> None:
            editor = _editor()
            if editor is not None:
                editor.import_paths([path])

        return handler

    def rec_row(i: int, rec):
        return row(key=f"imp-{i}", gap=10, padding=(7, 10), radius="$md", bg_hover="$surface_hover",
                   align="center", cursor="pointer", on_click=add(rec.mp4))[
            rec.thumb is not None and image(rec.thumb, width=72, height=41, fit="cover", radius=4),
            column(grow=1, gap=2)[
                text(rec.name, size=12.5, weight=600, max_lines=1),
                text(f"{rec.duration_label} · {rec.created_label}", size=11, color="$muted"),
            ],
            text("+ añadir", size=12, weight=600, color="$primary"),
        ]

    close = lambda: state.import_open.set(False)  # noqa: E731
    return column(layer="overlay", position="absolute", left=0, right=0, top=0, bottom=0, align="center",
                  justify="center", capture=True)[
        box(position="absolute", left=0, right=0, top=0, bottom=0, bg="#00000080", on_click=close)[None],
        column(position="absolute", width=520, max_height=560, padding=20, gap=12, radius="$lg", bg="$surface",
               border_width=1, border_color="$border", shadow=(0, 20, 50, "$shadow"), key="import-modal")[
            row(align="center", gap=10)[
                text("Importar", size=17, weight=700),
                importing and chip(f"analizando {importing[0]}…", tone="accent"),
                spacer(),
                icon_button(icons.CLOSE, close, tooltip="Cerrar (Esc)", size=28),
            ],
            text("Elige una grabación de la Biblioteca o cualquier video del disco. Se añade al final del timeline.",
                 size=12.5, color="$muted", line_height=1.4),
            scroll(key="import-list", max_height=340, gap=2)[[rec_row(i, r) for i, r in enumerate(recordings)]]
            if recordings else text("La Biblioteca está vacía.", size=12.5, color="$muted"),
            row(justify="end", gap=10)[
                button("Examinar…", _call("browse_import"), variant="soft", key="import-browse"),
                button("Listo", close, key="import-done"),
            ],
        ],
    ]


def _split_drag(which: str):
    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is None:
            return
        if e.phase == "start":
            editor.begin_split()
        else:
            editor.drag_split(which, e.dx, e.dy)

    return handler


def splitter(which: str):
    """Divisor arrastrable: «inspector» (vertical, entre preview e inspector) o «timeline»."""
    vertical = which == "inspector"
    grip = box(width=3 if vertical else 40, height=34 if vertical else 3, radius="$full", bg="$border")
    size = {"width": SPLIT_W} if vertical else {"height": SPLIT_W}
    return box(
        key=f"split-{which}",
        shrink=0,
        align="center",
        justify="center",
        radius="$md",
        bg_hover="$surface_hover",
        cursor="col-resize" if vertical else "row-resize",
        tooltip="Arrastra para redimensionar",
        on_drag=_split_drag(which),
        **size,
    )[grip]


def editor_screen():
    show_inspector = state.inspector_open()
    body = (
        column(grow=1, padding=(12, EDITOR_PAD_X), gap=0)[
            editor_toolbar(),
            box(height=10, shrink=0),
            row(grow=1, height=0)[  # base 0: el inspector (alto) hace scroll en vez de empujar el timeline
                column(grow=1, width=0, gap=8, min_width=PREVIEW_MIN_W)[preview_panel(), transport()],
                show_inspector and splitter("inspector"),
                show_inspector and inspector(),
            ],
            splitter("timeline"),
            timeline_panel(),
        ]
        if state.project_open()
        else empty_state()
    )
    return column(grow=1)[body, import_modal(), export_modal()]
