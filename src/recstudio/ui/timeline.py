"""Timeline del editor: canvas de dibujo + capa de cajas interactivas (patrón de F0/S5).

    scroll(axis="x", key="tl-scroll")          ← pan nativo: rueda, trackpad, barra
    └─ box(width = total_px)
       ├─ timeline_layer()  @component: canvas (regla, pistas, clips, waveforms, transiciones)
       │                     + cajas absolutas por clip visible (cuerpo `grab`, asas `col-resize`)
       └─ playhead_layer()  @component aparte: a 30 Hz solo se mueve esta caja

- El canvas usa una función de dibujo **de módulo** (`_draw_tracks`): misma identidad en cada
  reconstrucción, así Aurora conserva el dibujo mientras no cambie ningún `Signal` que lea
  (el playhead no está entre ellos).
- Drags por identidad + deltas (`DragEvent.dx` acumulado). El clic/drag sobre la regla o una
  zona vacía usa la x de ventana: un `on_drag` también llega con un simple clic (fase `start`),
  y como todo lo que hay a la izquierda del timeline tiene ancho fijo, `TL_X0` la convierte a
  x del contenido. (Así sí hay «clic en la regla → saltar ahí», que F0 daba por imposible.)
- Culling: se dibuja y se generan cajas solo para el rango visible ± un ancho de vista.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from aurora import Canvas, box, canvas, column, component, image, row, scroll, text

from .. import state
from ..core import effects as fx
from ..core import timeline as tl
from ..core.project import Project
from ..services import media

NAV_W = 196  # ancho de la barra lateral (shell.nav)
EDITOR_PAD_X = 16  # padding horizontal de la pantalla del editor
PANEL_BORDER = 1  # borde del panel del timeline
LABEL_W = 34  # columna de etiquetas de pista (V / A)
TL_X0 = NAV_W + EDITOR_PAD_X + PANEL_BORDER + LABEL_W  # x de ventana del borde izquierdo del contenido

RULER_H = 24
VIDEO_TOP = RULER_H + 6
VIDEO_H = 62  # alto por defecto de las pistas (el divisor del editor las agranda o encoge)
AUDIO_H = 40
TRACKS_EXTRA_MIN = -14.0  # lo más que se pueden encoger (en px repartidos entre V y A)
TRACKS_EXTRA_MAX = 420.0
SPLIT_W = 12  # grosor de los divisores arrastrables del editor
INSPECTOR_W_MIN, INSPECTOR_W_MAX = 240.0, 560.0
PREVIEW_MIN_W = 320.0  # el divisor del inspector nunca deja el preview más estrecho que esto
# Alto de todo lo que no es preview en el editor con las pistas por defecto (padding, barra,
# transporte, divisor y panel del timeline), medido con `pilot.rect`. Con el alto útil de la
# ventana (`state.view_h`) limita cuánto pueden crecer las pistas sin aplastar el preview.
EDITOR_CHROME_H = 332.0
PREVIEW_MIN_H = 160.0
TAIL_PX = 240  # espacio tras el último clip (para soltar y desplazarse)
HANDLE_W = 9
FILMSTRIP_MIN_W = 26.0  # ancho mínimo del hueco de una miniatura para que merezca la pena
FILMSTRIP_DRAW_MIN_W = 64.0  # ancho mínimo con el que se dibuja (si el hueco es menor, se solapa)
FILMSTRIP_MIN_H = 34.0  # alto mínimo de la pista de video para el filmstrip
FILMSTRIP_MAX = 60  # tope de imágenes por frame (además del culling por vista)
_CHAR_W = 0.56  # ancho medio de carácter / tamaño de fuente (elipsis manual en el canvas)

_TICKS = (0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800)


@dataclass(frozen=True)
class Tracks:
    """Geometría vertical de las pistas; el alto extra lo fija el divisor preview/timeline (F5)."""

    video_h: float
    audio_h: float

    @property
    def audio_top(self) -> float:
        return VIDEO_TOP + self.video_h + 6

    @property
    def content_h(self) -> float:
        return self.audio_top + self.audio_h + 8


def tracks_for(extra: float) -> Tracks:
    extra = max(TRACKS_EXTRA_MIN, min(TRACKS_EXTRA_MAX, float(extra)))
    return Tracks(video_h=round(VIDEO_H + extra * 0.6), audio_h=round(AUDIO_H + extra * 0.4))


def max_tracks_extra(view_h: float) -> float:
    """Lo más que pueden crecer las pistas con este alto útil sin bajar el preview de su mínimo."""
    return max(TRACKS_EXTRA_MIN, min(TRACKS_EXTRA_MAX, view_h - EDITOR_CHROME_H - PREVIEW_MIN_H))


def tracks() -> Tracks:
    # El tope se aplica al dibujar (no al guardar): si la ventana encoge, las pistas también, y
    # al volver a agrandarla recuperan el alto elegido.
    return tracks_for(min(state.tl_tracks_extra(), max_tracks_extra(state.view_h())))


def content_width(project: Project, zoom: float, view_w: float) -> float:
    return max(view_w, tl.total_duration(project) * zoom + TAIL_PX)


def _ellipsis(label: str, width: float, size: float) -> str:
    fits = int(width / (size * _CHAR_W))
    if fits <= 1:
        return ""
    return label if len(label) <= fits else label[: max(1, fits - 1)] + "…"


def _tick_step(zoom: float) -> float:
    for step in _TICKS:
        if step * zoom >= 72:
            return step
    return _TICKS[-1]


def _label(t: float, step: float) -> str:
    if step < 1:
        return f"{int(t // 60):02d}:{t % 60:04.1f}"
    total = int(round(t))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


class Geometry:
    """Posición (s) de cada clip tal como se dibuja ahora (con el desplazamiento de un trim_l)."""

    def __init__(self, project: Project, drag: state.TimelineDrag | None) -> None:
        self.starts = tl.clip_starts(project.video)
        if drag is not None and drag.kind == "trim_l" and drag.shift_index >= 0:
            self.starts = [
                s + drag.shift if i >= drag.shift_index else s for i, s in enumerate(self.starts)
            ]

    def span(self, project: Project, index: int) -> tuple[float, float]:
        start = self.starts[index]
        return start, start + project.video.clips[index].duration


def _visible_range() -> tuple[float, float]:
    """Rango x del contenido que merece la pena dibujar (vista ± un ancho)."""
    scroll_x, view_w = state.tl_scroll(), state.tl_view_w()
    return scroll_x - view_w, scroll_x + 2 * view_w


def _draw_ruler(c: Canvas, zoom: float, x0: float, x1: float, content_h: float) -> None:
    c.rect(0, 0, c.width, RULER_H, fill="$surface")
    c.line(0, RULER_H - 0.5, c.width, RULER_H - 0.5, stroke="$border")
    step = _tick_step(zoom)
    minor = step / 5 if step / 5 * zoom >= 9 else None
    t = max(0.0, (x0 // (step * zoom)) * step)
    end = x1 / zoom
    while t <= end:
        x = t * zoom
        c.line(x, RULER_H - 9, x, RULER_H, stroke="$muted", stroke_width=1)
        c.line(x, RULER_H, x, content_h, stroke="$grid", stroke_width=1)
        c.text(x + 4, 4, _label(t, step), size=10, color="$muted", family="Consolas")
        if minor:
            for k in range(1, 5):
                xm = (t + k * minor) * zoom
                c.line(xm, RULER_H - 4, xm, RULER_H, stroke="$border", stroke_width=1)
        t = round(t + step, 6)


def _waveform_points(peaks: np.ndarray, x: float, w: float, top: float, h: float, t0: float, t1: float, gain: float):
    columns = max(2, int(w / 2))
    part = media.waveform_slice(peaks, t0, t1, columns)
    if len(part) < 2:
        return None
    xs = x + np.linspace(0, w, len(part), dtype=np.float32)
    mid = top + h / 2
    half = h / 2 - 3
    upper = mid - np.clip(part[:, 1] * gain, -1, 1) * half
    lower = mid - np.clip(part[:, 0] * gain, -1, 1) * half
    pts = np.concatenate((np.stack((xs, upper), 1), np.stack((xs[::-1], lower[::-1]), 1)))
    return pts.astype(np.float32)


def _clip_meta(clip, *, offline: bool) -> str:
    """Segunda línea de un clip (duración + velocidad + efectos); la comparten canvas y overlay."""
    meta = tl.format_time(clip.duration)
    if clip.speed != 1.0:
        meta = f"{clip.speed:g}× · {meta}"
    if fx.has_effects(clip):
        meta = f"fx · {meta}"
    if offline:
        meta = "sin archivo · " + meta
    return meta


def _clip_label_x(w: float, x: float) -> float:
    """x de la etiqueta «pegajosa»: sigue visible aunque el inicio del clip quede fuera de la vista."""
    if w <= 80:
        return x + 8
    return min(max(x + 8, state.tl_scroll() + 8), x + w - 60)


def _draw_tracks(c: Canvas) -> None:
    project: Project = state.editor_project()
    zoom = state.tl_zoom()
    selection = state.selection()
    drag = state.tl_drag()
    offline = state.offline_assets()
    state.waveforms()  # dependencia: redibujar cuando llega un waveform
    lay = tracks()
    video_h, audio_top, audio_h = lay.video_h, lay.audio_top, lay.audio_h
    x0, x1 = _visible_range()
    c.clear("$track_bg")
    c.rect(0, VIDEO_TOP - 3, c.width, video_h + 6, fill="$track_bg")
    c.rect(0, audio_top - 3, c.width, audio_h + 6, fill="$track_alt")
    _draw_ruler(c, zoom, max(0.0, x0), x1, lay.content_h)

    clips = project.video.clips
    geo = Geometry(project, drag)
    selected_id = selection[1] if selection and selection[0] == "clip" else None
    peaks_by_asset: dict[str, tuple[np.ndarray | None, float]] = {}
    for index, clip in enumerate(clips):
        start, end = geo.span(project, index)
        x, w = start * zoom, max(2.0, clip.duration * zoom)
        if x > x1 or x + w < x0:
            continue
        is_offline = clip.asset_id in offline
        moving = drag is not None and drag.kind == "move" and drag.clip_id == clip.id
        selected = clip.id == selected_id
        if moving:  # el hueco que ocupará al soltar
            c.rect(x + 1, VIDEO_TOP, w - 2, video_h, radius=6, stroke="$primary", stroke_width=1.5, dash=(5, 4))
            continue
        c.rect(x + 1, VIDEO_TOP, w - 2, video_h, radius=6, fill="$clip")
        c.rect(x + 1, VIDEO_TOP, w - 2, video_h, radius=6,
               stroke="$primary" if selected else "$clip_edge", stroke_width=2 if selected else 1,
               dash=(4, 3) if is_offline else None)
        if selected:
            c.rect(x + 3, VIDEO_TOP + 2, w - 6, 3, radius=1.5, fill="$primary")
        try:
            asset = project.asset(clip.asset_id)
            name = asset.name
        except Exception:
            asset, name = None, "?"
        # Etiqueta «pegajosa»: sigue visible aunque el inicio del clip quede fuera de la vista.
        label_x = _clip_label_x(w, x)
        c.text(label_x, VIDEO_TOP + 10, _ellipsis(name, x + w - label_x - 6, 12), size=12, weight=600)
        meta = _clip_meta(clip, offline=is_offline)
        c.text(label_x, VIDEO_TOP + 28, _ellipsis(meta, x + w - label_x - 6, 10.5), size=10.5,
               color="$danger" if is_offline else "$muted", family="Consolas")

        # Pista de audio enlazada: banda + waveform del tramo de fuente.
        c.rect(x + 1, audio_top, w - 2, audio_h, radius=5, fill="$clip_audio")
        if asset is not None and asset.has_audio and zoom >= 20 and not is_offline:
            if asset.id not in peaks_by_asset:
                peaks = media.cached_waveform(asset.path)
                peak = float(np.abs(peaks).max()) if peaks is not None and peaks.size else 0.0
                peaks_by_asset[asset.id] = (peaks, min(6.0, 0.9 / peak) if peak > 1e-4 else 1.0)
            peaks, gain = peaks_by_asset[asset.id]
            if peaks is not None:
                vx0, vx1 = max(x, x0), min(x + w, x1)
                if vx1 - vx0 > 2:
                    t0 = clip.in_ + (vx0 - x) / zoom * clip.speed
                    t1 = clip.in_ + (vx1 - x) / zoom * clip.speed
                    pts = _waveform_points(peaks, vx0, vx1 - vx0, audio_top, audio_h, t0, t1, gain)
                    if pts is not None:
                        c.polygon(pts, fill="$wave")
        elif asset is not None and not asset.has_audio:
            c.text(x + 8, audio_top + 13, _ellipsis("sin audio", w - 12, 10), size=10, color="$muted")

    # Transiciones: zona de solape + rombo en la frontera.
    for index, transition in enumerate(project.video.transitions):
        a_start, a_end = geo.span(project, index)
        b_start = geo.starts[index + 1]
        if transition is None:
            continue
        xa, xb = b_start * zoom, a_end * zoom
        if xb < x0 or xa > x1:
            continue
        c.rect(xa, VIDEO_TOP + 6, max(2.0, xb - xa), video_h - 12, fill="#F5B85130", stroke="$transition",
               stroke_width=1)
        c.line(xa, VIDEO_TOP + 6, xb, VIDEO_TOP + video_h - 6, stroke="$transition", stroke_width=1)
        c.line(xa, VIDEO_TOP + video_h - 6, xb, VIDEO_TOP + 6, stroke="$transition", stroke_width=1)
        cx = (xa + xb) / 2
        if zoom >= 40:
            c.text(cx, VIDEO_TOP + video_h + 1, f"{transition.kind} {transition.duration:g}s", size=9.5,
                   color="$transition", align="center")
    for index in range(len(project.video.transitions)):
        transition = project.video.transitions[index]
        boundary = (geo.starts[index + 1] + (transition.duration / 2 if transition else 0.0)) * zoom
        if boundary < x0 or boundary > x1:
            continue
        selected = selection == ("transition", index)
        y = VIDEO_TOP - 1
        c.polygon([(boundary, y - 6), (boundary + 6, y), (boundary, y + 6), (boundary - 6, y)],
                  fill="$transition" if transition is not None or selected else "$surface_hover",
                  stroke="$primary" if selected else "$clip_edge", stroke_width=1.5 if selected else 1)

    # Fantasma del clip que se arrastra (encima de todo).
    if drag is not None and drag.kind == "move" and project.has_clip(drag.clip_id):
        clip = project.clip(drag.clip_id)
        x, w = drag.ghost * zoom, max(2.0, clip.duration * zoom)
        c.rect(x + 1, VIDEO_TOP - 2, w - 2, video_h + 4, radius=6, fill="#7C5CFF55", stroke="$primary", stroke_width=2)
        try:
            name = project.asset(clip.asset_id).name
        except Exception:
            name = "?"
        c.text(x + 8, VIDEO_TOP + 10, _ellipsis(name, w - 14, 12), size=12, weight=600)


# -- filmstrip (miniaturas) ---------------------------------------------------------


def filmstrip_slots(
    clip, asset, count: int, zoom: float, x: float, w: float, x0: float, x1: float
) -> list[tuple[float, float, int]]:
    """Miniaturas que caen dentro del clip y de la vista: `(left, ancho, índice)`.

    Las miniaturas se reparten por el **asset completo** (`asset.duration / count` segundos cada
    una), así que cada clip elige las de su tramo de fuente y recortar o mover no cambia nada.
    """
    if count <= 0 or asset.duration <= 0 or zoom <= 0 or clip.speed <= 0:
        return []
    step = asset.duration / count  # segundos de fuente por miniatura
    source0, source1 = clip.in_, clip.in_ + clip.duration * clip.speed
    first = max(0, int(source0 / step))
    last = min(count, int(np.ceil(source1 / step)))
    slots: list[tuple[float, float, int]] = []
    for index in range(first, last):
        left = x + (index * step - clip.in_) / clip.speed * zoom
        slot_w = step / clip.speed * zoom
        # Con el zoom bajo cada hueco es muy estrecho: se dibuja más ancho y se solapa con el
        # siguiente (se pintan en orden), que se ve como un filmstrip y no como rendijas. Nunca
        # se sale del clip (el borde derecho manda).
        draw_w = min(max(slot_w, FILMSTRIP_DRAW_MIN_W), x + w - 1 - left)
        visible_left = max(left, x + 1)
        visible_right = min(left + draw_w, x + w - 1)
        if visible_right <= x0 or visible_left >= x1:
            continue
        if visible_right - visible_left < FILMSTRIP_MIN_W:
            continue
        slots.append((visible_left, visible_right - visible_left, index))
    return slots


def _filmstrip_elements(
    project: Project, geo: Geometry, zoom: float, x0: float, x1: float, lay: Tracks
) -> tuple[list, list]:
    """`(miniaturas, etiquetas)` de los clips visibles.

    `canvas()` no dibuja imágenes (docs/05), así que el filmstrip es una capa aparte: va **encima
    del canvas y debajo de las cajas de interacción** (cualquier caja tapa los clics; las de los
    clips van después, así que siguen mandando). Como las miniaturas tapan el nombre y la duración
    que pinta el canvas, encima van las mismas etiquetas sobre un fondo oscuro translúcido.
    """
    if lay.video_h < FILMSTRIP_MIN_H:
        return [], []
    offline = state.offline_assets()
    thumbs: list = []
    labels: list = []
    for index, clip in enumerate(project.video.clips):
        if len(thumbs) >= FILMSTRIP_MAX:
            break
        start, _ = geo.span(project, index)
        x, w = start * zoom, max(4.0, clip.duration * zoom)
        if x > x1 or x + w < x0 or clip.asset_id in offline:
            continue
        try:
            asset = project.asset(clip.asset_id)
            name = asset.name
        except Exception:
            continue
        available = media.cached_thumbstrip(asset.path)
        if not available:
            continue
        slots = filmstrip_slots(clip, asset, len(available), zoom, x, w, x0, x1)
        if not slots:
            continue
        for left, slot_w, thumb_index in slots:
            thumbs.append(
                image(
                    available[thumb_index],
                    key=f"thumb-{clip.id}-{thumb_index}",
                    position="absolute",
                    left=left,
                    top=VIDEO_TOP + 1,
                    width=slot_w,
                    height=lay.video_h - 2,
                    fit="cover",
                    radius=5,
                )
            )
            if len(thumbs) >= FILMSTRIP_MAX:
                break
        label_x = _clip_label_x(w, x)
        room = min(x + w - label_x - 8, 280.0)
        if room >= 60:
            labels.append(
                box(
                    key=f"cliplabel-{clip.id}",
                    position="absolute",
                    left=label_x,
                    top=VIDEO_TOP + 6,
                    width=room,
                    bg="#0B0D14B8",
                    radius=5,
                    padding=(3, 7),
                    gap=1,
                )[
                    text(name, size=12, weight=600, max_lines=1, color="#FFFFFF"),
                    text(_clip_meta(clip, offline=False), size=10.5, max_lines=1,
                         color="#FFFFFFB0", family="Consolas"),
                ]
            )
    return thumbs, labels


# -- capa de interacción ------------------------------------------------------------


def _editor():
    from .editor_controller import EDITOR

    return EDITOR


def _canvas_drag(e) -> None:  # noqa: ANN001
    editor = _editor()
    if editor is not None:
        editor.scrub_to_window_x(e.x)


def _clip_drag(clip_id: str):
    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is None:
            return
        if e.phase == "start":
            editor.begin_move(clip_id)
        elif e.phase == "move":
            editor.drag_move(e.dx)
        else:
            editor.drag_move(e.dx)
            editor.end_drag()

    return handler


def _trim_drag(clip_id: str, side: str):
    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is None:
            return
        if e.phase == "start":
            editor.begin_trim(clip_id, side)
        elif e.phase == "move":
            editor.drag_trim(e.dx)
        else:
            editor.end_drag()

    return handler


def _playhead_drag(e) -> None:  # noqa: ANN001
    editor = _editor()
    if editor is None:
        return
    if e.phase == "start":
        editor.begin_playhead_drag()
    else:
        editor.drag_playhead(e.dx)


@component
def timeline_layer():
    project: Project = state.editor_project()
    zoom = state.tl_zoom()
    drag = state.tl_drag()
    selection = state.selection()
    state.thumbs()  # dependencia: las miniaturas llegan en un hilo y hay que repintarlas
    x0, x1 = _visible_range()
    width = content_width(project, zoom, state.tl_view_w())
    geo = Geometry(project, drag)
    lay = tracks()
    video_h, content_h = lay.video_h, lay.content_h
    thumbs, labels = _filmstrip_elements(project, geo, zoom, x0, x1, lay)

    boxes = []
    for index, clip in enumerate(project.video.clips):
        start, _ = geo.span(project, index)
        x, w = start * zoom, max(4.0, clip.duration * zoom)
        if x > x1 or x + w < x0:
            continue
        boxes.append(
            box(
                key=f"clip-{clip.id}",
                position="absolute",
                left=x,
                top=VIDEO_TOP,
                width=w,
                height=video_h,
                radius=6,
                bg="#00000000",
                bg_hover="#FFFFFF0D",
                cursor="grab",
                on_drag=_clip_drag(clip.id),
            )[
                box(key=f"trim-l-{clip.id}", position="absolute", left=0, top=0, width=min(HANDLE_W, w / 3),
                    height=video_h, radius=4, bg="#00000000", bg_hover="#7C5CFF88", cursor="col-resize",
                    on_drag=_trim_drag(clip.id, "l")),
                box(key=f"trim-r-{clip.id}", position="absolute", right=0, top=0, width=min(HANDLE_W, w / 3),
                    height=video_h, radius=4, bg="#00000000", bg_hover="#7C5CFF88", cursor="col-resize",
                    on_drag=_trim_drag(clip.id, "r")),
            ]
        )
    for index, transition in enumerate(project.video.transitions):
        boundary = (geo.starts[index + 1] + (transition.duration / 2 if transition else 0.0)) * zoom
        if boundary < x0 or boundary > x1:
            continue
        label = (
            f"Transición: {transition.kind} · {transition.duration:g} s" if transition else "Añadir transición"
        )
        selected = selection == ("transition", index)

        def pick(i: int = index) -> None:
            editor = _editor()
            if editor is not None:
                editor.select_transition(i)

        boxes.append(
            box(
                key=f"tr-{index}",
                position="absolute",
                left=boundary - 8,
                top=VIDEO_TOP - 9,
                width=16,
                height=16,
                radius=8,
                bg="#7C5CFF40" if selected else "#00000000",
                bg_hover="#7C5CFF55",
                cursor="pointer",
                tooltip=label,
                on_click=pick,
            )
        )

    return box(key="tl-layer", width=width, height=content_h, shrink=0)[
        canvas(_draw_tracks, key="tl-canvas", width=width, height=content_h, on_drag=_canvas_drag),
        thumbs,
        labels,
        boxes,
    ]


@component
def playhead_layer():
    zoom = state.tl_zoom()
    x = state.playhead() * zoom
    content_h = tracks().content_h
    # Cualquier caja encima tapa los clics de lo de debajo (aunque no tenga handlers): el
    # contenedor es la propia línea de 2 px y el asa (12 px) solo vive en la regla, para no
    # tapar las asas de trim de un clip cuyo borde coincide con el playhead (lo normal tras dividir).
    return box(position="absolute", left=x - 1, top=0, width=2, height=content_h, bg="$playhead")[
        box(key="playhead", position="absolute", left=-5, top=0, width=12, height=RULER_H - 6, radius=3,
            bg="$playhead", cursor="col-resize", on_drag=_playhead_drag),
    ]


def _on_scroll(e) -> None:  # noqa: ANN001
    editor = _editor()
    if editor is not None:
        editor.on_timeline_scroll(e.x, e.width)


def track_labels():
    def label(title: str, top: float, height: float):
        return box(position="absolute", left=0, top=top, width=LABEL_W - 6, height=height, radius=6,
                   bg="$surface_hover", align="center", justify="center")[
            text(title, size=11, weight=700, color="$muted")
        ]

    lay = tracks()
    return box(width=LABEL_W, height=lay.content_h, shrink=0)[
        label("V", VIDEO_TOP, lay.video_h),
        label("A", lay.audio_top, lay.audio_h),
    ]


def timeline_view():
    """Regla + pistas desplazables (lo que no es la barra de herramientas)."""
    project = state.editor_project()
    width = content_width(project, state.tl_zoom(), state.tl_view_w())
    content_h = tracks().content_h
    return row(grow=0, shrink=0, height=content_h + 12)[
        track_labels(),
        column(grow=1, width=0)[
            scroll(axis="x", key="tl-scroll", height=content_h + 12, on_scroll=_on_scroll)[
                box(key="tl-content", width=width, height=content_h, shrink=0)[
                    timeline_layer(),
                    playhead_layer(),
                ]
            ]
        ],
    ]
