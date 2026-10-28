"""Pantalla del editor: barra de proyecto, preview + transporte, inspector y timeline.

    column[ toolbar, row(grow=1)[ column[preview, transporte], ┃, inspector ], ━, timeline ]

Los divisores (┃ ━) se arrastran (F5): ┃ cambia el ancho del inspector y ━ el alto de las pistas
del timeline (y con ello el del preview). El inspector se oculta con `I` o el botón de panel.

Cada panel es un `@component` que lee solo sus `Signal`: el preview (30 Hz) y el playhead no
reconstruyen el inspector ni el canvas del timeline.
"""

from __future__ import annotations

from aurora import box, button, column, component, image, row, scroll, select, spacer, text, text_input

from ... import state
from ...core import timeline as tl
from ...core.project import TRANSITION_KINDS
from ..components import chip, icon_button, mark_input_key, section
from .. import icons
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


def _options(options, current):
    """Las opciones fijas más el valor actual si no está entre ellas (p. ej. de un proyecto viejo)."""
    if all(abs(v - current) > 1e-6 for v, _ in options):
        return sorted([*options, (current, f"{current:g}")])
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
                    tooltip=("Ocultar" if state.inspector_open() else "Mostrar") + " el inspector (I)", size=32,
                    key="ed-panel", bg="$surface_pressed" if state.inspector_open() else "$surface_hover"),
        box(width=8),
        icon_button(icons.MUSIC, _call("browse_music"), tooltip="Añadir música", size=32, key="ed-music"),
        icon_button(icons.TEXT, _call("add_text"), tooltip="Añadir texto en el playhead", size=32, key="ed-text"),
        button("Importar", _call("open_import"), variant="soft", padding=(6, 14), font_size=12.5, key="ed-import"),
        button("Exportar…", _call("open_export"), variant="soft", padding=(6, 14), font_size=12.5, key="ed-export"),
        button("Cerrar", _call("close_project"), variant="ghost", padding=(6, 12), font_size=12.5, key="ed-close"),
    ]


# -- preview y transporte -----------------------------------------------------------


@component
def preview_panel():
    frame = state.preview_frame()
    note = state.preview_note()
    has_clips = bool(state.editor_project().video.clips)
    # El hueco lo decide el layout (`width/height=0` + `grow`), nunca el tamaño de la imagen; y
    # `fit="contain"` la centra en él: sin esto, al agrandar la ventana el frame se quedaba
    # pegado a la izquierda y sin aprovechar el espacio.
    body = (
        column(grow=1, height=0, align="center", justify="center")[
            image(frame, fit="contain", grow=1, height=0, width="100%", key="ed-preview-img")
        ]
        if frame
        else column(grow=1, align="center", justify="center", gap=6)[
            text("Sin imagen" if has_clips else "El timeline está vacío", size=14, weight=600, color="$muted"),
            text("Importa un video (Ctrl+I) para empezar" if not has_clips else "decodificando…",
                 size=12, color="$muted"),
        ]
    )
    return column(key="ed-preview", grow=1, height=0, min_height=160, bg="#000000", radius="$lg",
                  border_width=1, border_color="$border", padding=6, gap=4)[
        body,
        row(justify="center")[text(note, size=11, color="#F5B851")] if note else None,
    ]


@component
def transport():
    project = state.editor_project()
    t = state.playhead()
    playing = state.playing()
    total = tl.total_duration(project)
    audio = state.preview_audio()
    frame = tl.frame_index(t, project.fps)
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
        spacer(),
        text(f"fotograma {frame} · {project.fps} fps", size=11.5, color="$muted", family="Consolas"),
        icon_button(icons.SPEAKER if audio else icons.MUTE, _call("toggle_preview_audio"),
                    tooltip="Audio del preview (M) · best-effort", size=30, key="tp-audio"),
    ]


# -- inspector -------------------------------------------------------------------------


def _row(label: str, value):
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


@component
def inspector():
    from ..editor_controller import SPEED_CHOICES

    project = state.editor_project()
    selection = state.selection()
    children = []

    if selection and selection[0] == "clip" and project.has_clip(selection[1]):
        clip = project.clip(selection[1])
        index = project.video.index_of(clip.id)
        try:
            asset = project.asset(clip.asset_id)
            name, offline = asset.name, asset.id in state.offline_assets()
        except Exception:
            name, offline = "?", True
        children.append(section(
            f"Clip {index + 1} de {len(project.video.clips)}",
            text(name, size=13.5, weight=600, max_lines=2),
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
            gap=10,
        ))
        children.append(_effects_controls(clip))
        if index < len(project.video.transitions):
            children.append(_transition_controls(index))
        children.append(row(gap=8)[
            button("✂ Dividir (S)", _call("split"), variant="soft", padding=(6, 12), font_size=12.5, key="insp-split"),
            button("Eliminar", _call("delete_selected"), variant="soft", padding=(6, 12), font_size=12.5,
                   key="insp-delete"),
        ])
    elif selection and selection[0] == "music" and int(selection[1]) < len(project.audio):
        track = project.audio[int(selection[1])]
        children.append(section(
            "Música",
            text(f"{len(track.clips)} clip(s) · {tl.format_time(tl.track_duration(track))}", size=12.5,
                 color="$muted"),
            _row("Volumen", select(state.insp_music_gain,
                                   _options([(0.0, "Silenciada"), *_VOLUMES[1:]],
                                           0.0 if track.muted else track.gain),
                                   key="insp-music-gain")),
            _row("Vol. clip", select(state.insp_music_clip_gain,
                                     _options(_VOLUMES, state.insp_music_clip_gain()),
                                     key="insp-music-clip-gain")),
            _row("Ducking", select(state.insp_music_duck,
                                   [(0.0, "Sin ducking"), (0.25, "Suave"), (0.5, "Medio"),
                                    (0.75, "Fuerte"), (1.0, "Máximo")], key="insp-music-duck")),
            len(track.clips) > 1 and _row("Crossfade", select(
                state.insp_music_cross,
                _options([(v, "Sin crossfade" if v == 0 else f"{v:g} s")
                          for v in (0.0, 0.25, 0.5, 1.0, 2.0)], state.insp_music_cross()),
                key="insp-music-cross")),
            text("Ducking baja la música cuando suena la voz o el audio del sistema.",
                 size=11, color="$muted", line_height=1.35),
            row(gap=8)[
                button("Quitar música", _call("remove_music"), variant="ghost", padding=(4, 10),
                       font_size=11.5, key="insp-music-remove"),
            ],
            gap=10,
        ))
    elif selection and selection[0] == "text" and project.has_text(selection[1]):
        overlay = project.text(selection[1])
        children.append(section(
            "Texto",
            text_input(state.insp_text, key="insp-text", on_submit=_submit("set_text_text"),
                       on_key=mark_input_key, grow=1, width=0, padding=(7, 10)),
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
    elif selection and selection[0] == "transition" and selection[1] < len(project.video.transitions):
        children.append(_transition_controls(int(selection[1])))
        children.append(text("Las transiciones se previsualizan de forma aproximada; el export usa xfade de FFmpeg.",
                             size=11.5, color="$muted", line_height=1.4))
    else:
        total = tl.total_duration(project)
        children.append(section(
            "Proyecto",
            _row("Duración", tl.format_time(total)),
            _row("Clips", str(len(project.video.clips))),
            _row("Resolución", f"{project.width}×{project.height}"),
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
            "Atajos",
            text("Espacio reproducir · ←/→ fotograma · Mayús+←/→ 1 s · S dividir · Supr eliminar · "
                 "Ctrl+Z/Y deshacer/rehacer · +/− zoom · 0 ajustar · I inspector · Ctrl+E exportar · Esc cancelar",
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


# -- timeline -----------------------------------------------------------------------


@component
def timeline_toolbar():
    zoom = state.tl_zoom()
    snap_on = state.snap_on()
    has_selection = state.selection() is not None
    return row(gap=6, align="center", shrink=0)[
        icon_button(icons.ZOOM_OUT, _call("zoom_out"), tooltip="Alejar (−)", size=30, key="tl-zoom-out"),
        icon_button(icons.ZOOM_IN, _call("zoom_in"), tooltip="Acercar (+)", size=30, key="tl-zoom-in"),
        icon_button(icons.FIT, _call("zoom_fit"), tooltip="Ajustar al ancho (0)", size=30, key="tl-fit"),
        text(f"{zoom:.0f} px/s", size=11.5, color="$muted", family="Consolas", width=70),
        icon_button(icons.MAGNET, lambda: state.snap_on.set(not state.snap_on()),
                    tooltip="Imán: " + ("activado" if snap_on else "desactivado"), size=30, key="tl-snap",
                    bg="$primary" if snap_on else "$surface_hover"),
        icon_button(icons.SELECT, _call("toggle_marquee"),
                    tooltip="Selección múltiple: arrastra sobre el timeline",
                    size=30, key="tl-marquee",
                    bg="$primary" if state.marquee_on() else "$surface_hover"),
        box(width=6),
        icon_button(icons.CUT, _call("split"), tooltip="Dividir en el playhead (S)", size=30, key="tl-split"),
        icon_button(icons.TRASH, _call("delete_selected"), tooltip="Eliminar selección (Supr)", size=30,
                    key="tl-delete", opacity=1.0 if has_selection else 0.45),
        spacer(),
        text("Arrastra los clips para reordenar · sus bordes para recortar · la regla para moverte",
             size=11, color="$muted", max_lines=1),
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
