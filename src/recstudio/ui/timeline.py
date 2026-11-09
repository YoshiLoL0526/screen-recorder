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
- Fase 3 (docs/21): la regla dibuja el rango In/Out y los marcadores (cada uno con su caja
  clicable); con la razor (`state.razor_on`) las cajas de los clips cortan donde se pulsa en vez
  de mover, y el canvas (regla o zona vacía) corta todas las pistas.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from aurora import Canvas, box, canvas, column, component, image, menus, row, scroll, text

from .. import state
from ..core import animation as anim
from ..core import audio_envelope as env
from ..core import effects as fx
from ..core import elements as element_mod
from ..core import timeline as tl
from ..core.project import (
    TRANSITION_MIN,
    VIDEO_TRACKS_MAX,
    Project,
    audio_track_audible,
    video_audio_gain,
)
from ..services import media

NAV_W = 196  # ancho de la barra lateral (shell.nav)
EDITOR_PAD_X = 16  # padding horizontal de la pantalla del editor
PANEL_BORDER = 1  # borde del panel del timeline
LABEL_W = 196  # columna de cabeceras de pista (V/A + controles M/S/lock/volumen)
TL_X0 = NAV_W + EDITOR_PAD_X + PANEL_BORDER + LABEL_W  # x de ventana del borde izquierdo del contenido

RULER_H = 24
VIDEO_TOP = RULER_H + 6
VIDEO_H = 62  # alto por defecto de las pistas (el divisor del editor las agranda o encoge)
AUDIO_H = 40
AUDIO_TRACK_H = 42  # alto de cada pista de audio extra (música, audio separado, voz…)
OVERLAY_H = 26  # alto de las filas de textos/elementos sobre el video
VIDEO_ROW_H = 32  # alto de cada pista de video V2/V3
TRACK_GAP = 6
MAX_AUDIO_TRACKS = 6  # tope de la UI antes de aplastar el preview (alternativa al scroll vertical)
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
MARKER_COLOR = "#F5B851"
ENVELOPE_COLOR = "#FFD166"  # línea de la envolvente de volumen (docs/32)
ENVELOPE_HANDLE_W = 11.0  # asa de una clave sobre la waveform (docs/37)
ENVELOPE_ELEMENT_W = 9.0  # asa de una clave sobre la barra de un elemento
AUDIO_FADE_COLOR = "#4CC9F0"  # fundidos y crossfades de audio en el timeline (docs/46)
AUDIO_FADE_HANDLE_W = 9.0  # asa de fundido sobre la waveform/barra de un clip de audio
AUDIO_FADE_HANDLE_H = 8.0  # vive en el hueco superior para no tapar el nombre del clip
AUDIO_CROSS_HANDLE_W = 14.0  # asa/zona del crossfade en un corte de pista secuencial
AUDIO_CROSS_HANDLE_H = 10.0
ANIMATION_COLOR = "#FF6EC7"  # polilínea y asas de animación visual (docs/38)
ANIMATION_HANDLE_W = 9.0  # asa de una clave sobre la barra de un elemento/texto
KEYFRAME_LABELS = {
    "x": "X", "y": "Y", "opacity": "opacidad", "scale": "escala",
    "rotation": "rotación", "size": "tamaño",
}
MARKER_W = 12  # caja clicable de cada marcador en la regla
RANGE_FILL = "#7C5CFF40"  # banda del rango In/Out en la regla


@dataclass(frozen=True)
class AudioRow:
    """Fila de una pista de `Project.audio` dentro del timeline."""

    index: int
    top: float
    height: float


@dataclass(frozen=True)
class OverlayRow:
    """Fila de una capa global (textos o elementos) por encima de la pista de video."""

    kind: str  # text | element
    top: float
    height: float
    count: int


@dataclass(frozen=True)
class VideoRow:
    """Fila de una pista de video V2/V3 (elementos con `video_track`)."""

    track: int  # 1 = V2, 2 = V3
    top: float
    height: float
    count: int


@dataclass(frozen=True)
class Tracks:
    """Geometría vertical de las pistas; el alto extra lo fija el divisor preview/timeline (F5)."""

    video_h: float
    audio_h: float
    rows: tuple[AudioRow, ...] = ()
    overlays: tuple[OverlayRow, ...] = ()
    video_rows: tuple[VideoRow, ...] = ()
    video_top: float = VIDEO_TOP

    @property
    def audio_top(self) -> float:
        return self.video_top + self.video_h + TRACK_GAP

    @property
    def first_audio_top(self) -> float:
        return self.audio_top + self.audio_h + TRACK_GAP if self.rows else self.audio_top + self.audio_h

    @property
    def music_top(self) -> float:  # compatibilidad: primera pista de audio extra
        return self.rows[0].top if self.rows else self.first_audio_top

    @property
    def music_h(self) -> float:  # compatibilidad: alto de la primera pista extra
        return self.rows[0].height if self.rows else 0.0

    @property
    def content_h(self) -> float:
        bottom = self.rows[-1].top + self.rows[-1].height if self.rows else self.audio_top + self.audio_h
        return bottom + 8

    def row_for(self, index: int) -> AudioRow | None:
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def overlay_for(self, kind: str) -> OverlayRow | None:
        return next((row for row in self.overlays if row.kind == kind), None)

    def video_row(self, track: int) -> VideoRow | None:
        return next((row for row in self.video_rows if row.track == track), None)


def tracks_for(extra: float, project: Project | None = None) -> Tracks:
    extra = max(TRACKS_EXTRA_MIN, min(TRACKS_EXTRA_MAX, float(extra)))
    video_h = round(VIDEO_H + extra * 0.6)
    audio_h = round(AUDIO_H + extra * 0.4)
    overlays: list[OverlayRow] = []
    video_rows: list[VideoRow] = []
    top = VIDEO_TOP
    if project is not None:
        generic = [e for e in project.elements if e.video_track <= 0]
        # Orden visual = z-order de composición: textos, elementos globales, V3, V2 y video base.
        for kind, items in (("text", project.texts), ("element", generic)):
            if not items:
                continue
            overlays.append(OverlayRow(kind, top, OVERLAY_H, len(items)))
            top += OVERLAY_H + TRACK_GAP
        counts = {
            track: sum(1 for e in project.elements if e.video_track == track)
            for track in range(1, VIDEO_TRACKS_MAX + 1)
        }
        for track in range(VIDEO_TRACKS_MAX, 0, -1):
            if counts.get(track):
                video_rows.append(VideoRow(track, top, VIDEO_ROW_H, counts[track]))
                top += VIDEO_ROW_H + TRACK_GAP
    rows: list[AudioRow] = []
    audio_top = top + video_h + TRACK_GAP
    first = audio_top + audio_h + TRACK_GAP
    for index in range(len(project.audio) if project is not None else 0):
        rows.append(AudioRow(index, first, AUDIO_TRACK_H))
        first += AUDIO_TRACK_H + TRACK_GAP
    return Tracks(video_h=video_h, audio_h=audio_h, rows=tuple(rows),
                  overlays=tuple(overlays), video_rows=tuple(video_rows),
                  video_top=float(top))


def max_tracks_extra(view_h: float) -> float:
    """Lo más que pueden crecer las pistas con este alto útil sin bajar el preview de su mínimo."""
    project = state.editor_project()
    rows = len(project.audio) * (AUDIO_TRACK_H + TRACK_GAP)
    generic = any(e.video_track <= 0 for e in project.elements)
    groups = (1 if project.texts else 0) + (1 if generic else 0)
    rows += groups * (OVERLAY_H + TRACK_GAP)
    video_rows = len({e.video_track for e in project.elements if e.video_track > 0})
    rows += video_rows * (VIDEO_ROW_H + TRACK_GAP)
    return max(TRACKS_EXTRA_MIN, min(TRACKS_EXTRA_MAX,
                                     view_h - EDITOR_CHROME_H - PREVIEW_MIN_H - rows))


def tracks() -> Tracks:
    # El tope se aplica al dibujar (no al guardar): si la ventana encoge, las pistas también, y
    # al volver a agrandarla recuperan el alto elegido. Las pistas de audio extra se dibujan
    # todas; si no caben, `timeline_view` las envuelve en un scroll vertical.
    base = tracks_for(min(state.tl_tracks_extra(), max_tracks_extra(state.view_h())), state.editor_project())
    return base


def content_width(project: Project, zoom: float, view_w: float) -> float:
    return max(view_w, tl.project_extent(project) * zoom + TAIL_PX)


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


def _draw_range_and_markers(c: Canvas, project: Project, zoom: float, x0: float, x1: float,
                            content_h: float, selected_marker: str | None) -> None:
    """Rango In/Out (banda en la regla + líneas) y marcadores (bandera + línea fina)."""
    if tl.has_range(project):
        start, end = tl.play_range(project)
        xa, xb = start * zoom, end * zoom
        if xb >= x0 and xa <= x1:
            c.rect(xa, 0, max(2.0, xb - xa), RULER_H, fill=RANGE_FILL)
            for x, label in ((xa, "In"), (xb, "Out")):
                if (label == "In" and project.range_in is None) or (label == "Out" and project.range_out is None):
                    continue
                c.line(x, 0, x, content_h, stroke="$primary", stroke_width=1.5, dash=(6, 4))
                c.text(x + 3 if label == "In" else x - 3, RULER_H - 12, label, size=9, weight=700,
                       color="$primary", align="left" if label == "In" else "right")
    for marker in project.markers:
        x = marker.t * zoom
        if x < x0 - MARKER_W or x > x1 + MARKER_W:
            continue
        selected = marker.id == selected_marker
        c.line(x, RULER_H, x, content_h, stroke=MARKER_COLOR, stroke_width=1.5 if selected else 1,
               dash=None if selected else (2, 4))
        c.polygon([(x - 5, 1), (x + 5, 1), (x + 5, 8), (x, 13), (x - 5, 8)],
                  fill=MARKER_COLOR, stroke="$primary" if selected else MARKER_COLOR,
                  stroke_width=1.5 if selected else 1)
        if zoom >= 30 and marker.name:
            c.text(x + 7, 1, _ellipsis(marker.name, 120, 9.5), size=9.5, color=MARKER_COLOR, weight=600)


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


def envelope_gain_y(top: float, height: float, gain: float) -> float:
    """`y` (del contenido) de una ganancia 0…4 dentro de una fila de `height` px.

    El mapeo es el mismo para la waveform de un clip y para la barra de un elemento:
    2 px de margen arriba/abajo y el resto reparte `GAIN_MAX`.
    """
    inner_top = top + 2.0
    inner_h = max(1.0, height - 4.0)
    gain = max(0.0, min(env.GAIN_MAX, float(gain)))
    return inner_top + inner_h * (1.0 - gain / env.GAIN_MAX)


def envelope_gain_at_y(top: float, height: float, y: float) -> float:
    """Ganancia 0…4 que corresponde a la `y` de una fila (inversa de `envelope_gain_y`)."""
    inner_top = top + 2.0
    inner_h = max(1.0, height - 4.0)
    value = (inner_top + inner_h - float(y)) / inner_h * env.GAIN_MAX
    return max(0.0, min(env.GAIN_MAX, value))


def envelope_x(start: float, duration: float, t: float, zoom: float) -> float:
    """`x` (del contenido) de un tiempo local `t` de un clip/elemento que empieza en `start`."""
    t = max(0.0, min(float(duration), float(t)))
    return (float(start) + t) * zoom


def envelope_time(start: float, duration: float, x: float, zoom: float) -> float:
    """Tiempo local (0…`duration`) de una `x` del contenido (inversa de `envelope_x`)."""
    if zoom <= 0:
        return 0.0
    return max(0.0, min(float(duration), float(x) / zoom - float(start)))


def audio_fade_x(start: float, duration: float, fade: float, zoom: float, *, side: str) -> float:
    """`x` del asa de un fundido de audio: extremo de la rampa (inicio o fin del clip).

    `side="in"` coloca el asa al final del fundido de entrada; `side="out"`, al principio del
    de salida. La inversa es `audio_fade_at_x`.
    """
    duration = max(0.0, float(duration))
    fade = max(0.0, min(duration, float(fade)))
    if side == "in":
        return (float(start) + fade) * zoom
    return (float(start) + duration - fade) * zoom


def audio_fade_at_x(start: float, duration: float, x: float, zoom: float, *, side: str) -> float:
    """Segundos de fundido que corresponden a la `x` del asa (inversa de `audio_fade_x`)."""
    if zoom <= 0:
        return 0.0
    duration = max(0.0, float(duration))
    if side == "in":
        value = float(x) / zoom - float(start)
    else:
        value = float(start) + duration - float(x) / zoom
    return max(0.0, min(duration, value))


def keyframe_x(start: float, duration: float, t: float, zoom: float) -> float:
    """`x` del contenido de un tiempo local de un elemento/texto (docs/38)."""
    return envelope_x(start, duration, t, zoom)


def keyframe_time(start: float, duration: float, x: float, zoom: float) -> float:
    """Tiempo local (0…`duration`) de una `x` del contenido (inversa de `keyframe_x`)."""
    return envelope_time(start, duration, x, zoom)


def keyframe_default(item, channel: str) -> float:  # noqa: ANN001
    """Valor base de un canal de animación de un elemento/texto (docs/42).

    Los canales derivados no viven en el modelo: `scale` empieza en 1.0 y `rotation` usa la
    rotación base de la capa (0.0 en textos, que no tienen campo). El resto (`x`, `y`, `opacity`
    y `size`) es el campo homónimo del elemento/texto.
    """
    if channel == "scale":
        return 1.0
    if channel == "rotation":
        return float(getattr(item, "rotation", 0.0))
    return float(getattr(item, channel))


def keyframe_value_at(item, channel: str, t: float) -> float:  # noqa: ANN001
    """Valor actual del canal en `t` local: interpolado o su base si aún no hay claves."""
    return anim.value_at(item.animations, channel, float(t), keyframe_default(item, channel))


def keyframe_value_y(top: float, height: float, channel: str, value: float) -> float:
    """`y` del contenido para un valor del canal de animación dentro de una fila.

    El mapeo es el mismo para elementos y textos: 2 px de margen arriba/abajo y el resto
    reparte el rango del canal (`animation.CHANNEL_RANGES`). Arriba = valor máximo.
    """
    low, high = anim.channel_range(channel)
    inner_top = top + 2.0
    inner_h = max(1.0, height - 4.0)
    value = max(low, min(high, float(value)))
    span = high - low
    if span <= 0:
        return inner_top + inner_h / 2.0
    return inner_top + inner_h * (1.0 - (value - low) / span)


def keyframe_value_at_y(top: float, height: float, channel: str, y: float) -> float:
    """Valor del canal que corresponde a la `y` de una fila (inversa de `keyframe_value_y`)."""
    low, high = anim.channel_range(channel)
    inner_top = top + 2.0
    inner_h = max(1.0, height - 4.0)
    value = low + (inner_top + inner_h - float(y)) / inner_h * (high - low)
    return max(low, min(high, value))


def _keyframe_curve(
    item, channel: str, start: float, zoom: float, top: float, height: float,
    x0: float, x1: float,
):
    """Puntos de la polilínea y asas de un canal de animación, en px del canvas (docs/38)."""
    animation = anim.find(item.animations, channel)
    duration = float(item.end - item.start)
    if animation is None or duration <= 0 or zoom <= 0:
        return None, []
    x, w = start * zoom, max(4.0, duration * zoom)
    if x > x1 or x + w < x0:
        return None, []
    points = [(x, keyframe_value_y(top, height, channel, animation.value_at(0.0, 0.0)))]
    keys: list[tuple[float, float]] = []
    for point in animation.points:
        if point.t > duration + 1e-9:
            break
        px = keyframe_x(start, duration, point.t, zoom)
        py = keyframe_value_y(top, height, channel, point.value)
        points.append((px, py))
        keys.append((px, py))
    points.append((
        x + w,
        keyframe_value_y(top, height, channel, animation.value_at(duration, 0.0)),
    ))
    return points, keys


def _envelope_curve(clip, x: float, w: float, top: float, h: float):
    """Puntos de la polilínea de la envolvente y de sus claves, en px del canvas."""
    duration = clip.duration
    if not clip.envelope or duration <= 0 or w <= 1:
        return None, []
    points = [(x, envelope_gain_y(top, h, env.value_at(clip.envelope, 0.0)))]
    keys = []
    for point in clip.envelope:
        if point.t > duration + 1e-9:
            break
        px = x + point.t / duration * w
        py = envelope_gain_y(top, h, point.gain)
        points.append((px, py))
        keys.append((px, py))
    points.append((x + w, envelope_gain_y(top, h, env.value_at(clip.envelope, duration))))
    return points, keys


def _draw_envelope(c: Canvas, clip, x: float, w: float, top: float, h: float) -> None:
    points, keys = _envelope_curve(clip, x, w, top, h)
    if not points:
        return
    c.polyline(points, stroke=ENVELOPE_COLOR, stroke_width=1.5)
    if state.tl_zoom() >= 40:
        for px, py in keys:
            c.circle(px, py, 2.2, fill=ENVELOPE_COLOR)


def _draw_audio_fades(c: Canvas, clip, x: float, w: float, top: float, h: float) -> None:
    """Rampas de los fundidos de audio del clip sobre su waveform/barra (docs/46)."""
    duration = clip.duration
    if duration <= 0 or w <= 1:
        return
    fin, fout = fx.audio_fades(clip, duration)
    if fin > 0:
        x1 = x + min(w, fin / duration * w)
        c.polygon([(x, top + h), (x1, top), (x1, top + h)], fill=AUDIO_FADE_COLOR + "22")
        c.line(x, top + h, x1, top, stroke=AUDIO_FADE_COLOR, stroke_width=1.2)
    if fout > 0:
        x0 = x + w - min(w, fout / duration * w)
        c.polygon([(x0, top), (x + w, top + h), (x0, top + h)], fill=AUDIO_FADE_COLOR + "22")
        c.line(x0, top, x + w, top + h, stroke=AUDIO_FADE_COLOR, stroke_width=1.2)


def _envelope_key_boxes(
    scope: str, item, start: float, track_index: int, top: float, height: float,
    zoom: float, x0: float, x1: float, *, size: float,
):
    """Asas absolutas de las claves de una envolvente (docs/37).

    Se colocan **encima** de la waveform/barra y usan el mismo mapeo que la polilínea
    (`envelope_gain_y`). Un clic sin movimiento selecciona la clave; `Supr` la borra.
    """
    duration = float(item.duration) if scope != "element" else float(item.end - item.start)
    points = item.envelope
    selected = state.envelope_key()
    boxes = []
    for index, point in enumerate(points):
        if point.t > duration + 1e-9:
            continue
        px = envelope_x(start, duration, point.t, zoom)
        py = envelope_gain_y(top, height, point.gain)
        if px < x0 - size or px > x1 + size:
            continue
        key = (
            f"env-key-{scope}-{item.id}-{index}" if scope != "audio"
            else f"env-key-audio-{track_index}-{item.id}-{index}"
        )
        is_selected = (
            selected is not None and selected.scope == scope and selected.item == item.id
            and selected.track == track_index and abs(selected.t - point.t) <= 1e-4
        )
        label = (
            f"Clave de volumen {index + 1}: {point.t:.2f} s · {point.gain:.2f}×"
        )
        boxes.append(
            box(
                key=key, position="absolute", left=px - size / 2, top=py - size / 2,
                width=size, height=size, radius=size / 2,
                bg=ENVELOPE_COLOR, border_width=2 if is_selected else 1,
                border_color="#FFFFFF" if is_selected else ENVELOPE_COLOR + "CC",
                cursor="grab", tooltip=label + " · arrastra para mover; Supr para borrar",
                label=label,
                on_click=_envelope_key_click(scope, item.id, track_index, point.t),
                on_drag=_envelope_key_drag(scope, item.id, track_index, index, height),
                on_context_menu=_envelope_key_menu(scope, item.id, track_index, point.t),
            )
        )
    return boxes


def _clip_envelope_boxes(
    clip, start: float, track_index: int, top: float, height: float,
    zoom: float, x0: float, x1: float, *, scope: str,
):
    """Modo envolvente + asas de un clip de V1 o de una pista de audio."""
    boxes = []
    if state.env_edit_on():
        key = f"env-add-video-{clip.id}" if scope == "video" else f"env-add-audio-{track_index}-{clip.id}"
        boxes.append(
            box(
                key=key, position="absolute", left=start * zoom, top=top,
                width=max(4.0, clip.duration * zoom), height=height, radius=4,
                bg="#FFD1660D", border_width=1, border_color="#FFD16655",
                cursor="crosshair",
                tooltip="Modo envolvente: clic para añadir una clave · arrastra un punto para moverlo",
                label="Añadir clave de volumen en este punto",
                on_drag=_envelope_add_drag(scope, clip.id, track_index, height),
            )
        )
    if clip.envelope:
        boxes.extend(_envelope_key_boxes(
            scope, clip, start, track_index, top, height, zoom, x0, x1,
            size=ENVELOPE_HANDLE_W,
        ))
    return boxes


def _element_envelope_boxes(
    item, row, zoom: float, x0: float, x1: float,
):
    """Modo envolvente + asas sobre la barra de un elemento de video (sin waveform)."""
    if item.kind != "video" or item.end <= item.start:
        return []
    x, w = item.start * zoom, max(4.0, (item.end - item.start) * zoom)
    if x > x1 or x + w < x0:
        return []
    top, height = row.top + 2, row.height - 4
    boxes = []
    if state.env_edit_on():
        boxes.append(
            box(
                key=f"env-add-element-{item.id}", position="absolute", left=x, top=top,
                width=w, height=height, radius=4, bg="#FFD1660D", border_width=1,
                border_color="#FFD16655", cursor="crosshair",
                tooltip="Modo envolvente: clic para añadir una clave al audio del elemento",
                label="Añadir clave de volumen del elemento",
                on_drag=_envelope_add_drag("element", item.id, -1, height),
            )
        )
    if item.envelope:
        boxes.extend(_envelope_key_boxes(
            "element", item, item.start, -1, top, height, zoom, x0, x1,
            size=ENVELOPE_ELEMENT_W,
        ))
    return boxes


def _audio_fade_boxes(
    clip, start: float, track_index: int, row, zoom: float, x0: float, x1: float,
):
    """Asas de los fundidos de entrada/salida de un clip de una pista de audio (docs/46).

    Son cajas de `AUDIO_FADE_HANDLE_W` en la banda superior del clip, separadas de las asas de
    recorte laterales. El fundido de entrada se arrastra hacia la derecha y el de salida hacia la
    izquierda; el valor se acota con `fx.audio_fade_limit` (2 s y sin cruzar el otro fundido).
    """
    duration = float(clip.duration)
    if duration <= 0:
        return []
    x, w = float(start) * zoom, max(4.0, duration * zoom)
    if x > x1 + AUDIO_FADE_HANDLE_W or x + w < x0 - AUDIO_FADE_HANDLE_W:
        return []
    fin, fout = fx.audio_fades(clip, duration)
    width, height = AUDIO_FADE_HANDLE_W, AUDIO_FADE_HANDLE_H
    boxes = []
    for side, value in (("in", fin), ("out", fout)):
        px = audio_fade_x(start, duration, value, zoom, side=side)
        if side == "in":
            left = max(x + HANDLE_W, px - width / 2)
            left = min(left, x + w - width)
            direction = "a la derecha"
        else:
            left = min(x + w - HANDLE_W - width, px - width / 2)
            left = max(x, left)
            direction = "a la izquierda"
        limit = fx.audio_fade_limit(duration, fout if side == "in" else fin)
        label = (
            f"Fundido de {'entrada' if side == 'in' else 'salida'}: {value:.2f} s "
            f"(máx. {limit:.2f} s) · arrastra {direction}"
        )
        boxes.append(
            box(
                key=f"audio-fade-{side}-{track_index}-{clip.id}",
                position="absolute", left=left, top=row.top - height + 2,
                width=width, height=height, radius=height / 2,
                bg=AUDIO_FADE_COLOR, border_width=1, border_color="#FFFFFFCC",
                cursor="col-resize", tooltip=label, label=label,
                on_drag=_audio_fade_drag(track_index, clip.id, side),
            )
        )
    return boxes


def _audio_crossfade_boxes(
    project: Project, lay: Tracks, zoom: float, x0: float, x1: float,
):
    """Asas/zona de crossfade en cada corte de una pista de audio secuencial (docs/46).

    El asa vive justo encima de la fila (en el hueco entre pistas) para no tapar las asas de
    recorte, que ocupan el alto completo del clip. Arrastrarla hacia la izquierda alarga el
    crossfade: el asa sigue al puntero porque está en el inicio del clip entrante.
    """
    if state.razor_on():
        return []
    boxes = []
    for row in lay.rows:
        track = project.audio[row.index]
        if track.mode != "sequence" or track.locked or len(track.clips) < 2:
            continue
        starts = tl.clip_starts(track)
        for index in range(len(track.transitions)):
            limit = tl.transition_limit(track, index)
            if limit < TRANSITION_MIN - 1e-9:
                continue
            boundary = starts[index + 1] * zoom
            if boundary < x0 - AUDIO_CROSS_HANDLE_W or boundary > x1 + AUDIO_CROSS_HANDLE_W:
                continue
            transition = track.transitions[index]
            label = (
                f"Crossfade {index + 1}: {transition.duration:.2f} s"
                if transition is not None else f"Crear crossfade en el corte {index + 1}"
            )
            label += " · arrastra a la izquierda para alargar"
            boxes.append(
                box(
                    key=f"audio-cross-{row.index}-{index}",
                    position="absolute", left=boundary - AUDIO_CROSS_HANDLE_W / 2,
                    top=row.top - AUDIO_CROSS_HANDLE_H + 2,
                    width=AUDIO_CROSS_HANDLE_W, height=AUDIO_CROSS_HANDLE_H,
                    radius=3, bg="#4CC9F033", bg_hover="#4CC9F066",
                    border_width=1, border_color=AUDIO_FADE_COLOR + "CC",
                    cursor="col-resize", tooltip=label, label=label,
                    on_drag=_audio_crossfade_drag(row.index, index),
                )
            )
    return boxes


def _keyframe_boxes(
    item, scope: str, start: float, row, zoom: float, x0: float, x1: float,
):
    """Asas de las claves del canal elegido sobre la barra de un elemento/texto (docs/38)."""
    channel = state.keyframe_channel()
    if channel not in anim.CHANNELS:
        return []
    animation = anim.find(item.animations, channel)
    duration = float(item.end - item.start)
    if animation is None or duration <= 0:
        return []
    x, w = start * zoom, max(4.0, duration * zoom)
    if x > x1 or x + w < x0:
        return []
    top, height = row.top + 2, row.height - 4
    selected = state.keyframe_key()
    label = KEYFRAME_LABELS.get(channel, channel)
    boxes = []
    for index, point in enumerate(animation.points):
        if point.t > duration + 1e-9:
            continue
        px = keyframe_x(start, duration, point.t, zoom)
        py = keyframe_value_y(top, height, channel, point.value)
        if px < x0 - ANIMATION_HANDLE_W or px > x1 + ANIMATION_HANDLE_W:
            continue
        is_selected = (
            selected is not None and selected.scope == scope and selected.item == item.id
            and selected.channel == channel and abs(selected.t - point.t) <= 1e-4
        )
        text_label = f"Clave {label} {index + 1}: {point.t:.2f} s · {point.value:g}"
        boxes.append(
            box(
                key=f"anim-key-{scope}-{item.id}-{index}",
                position="absolute", left=px - ANIMATION_HANDLE_W / 2,
                top=py - ANIMATION_HANDLE_W / 2,
                width=ANIMATION_HANDLE_W, height=ANIMATION_HANDLE_W,
                radius=ANIMATION_HANDLE_W / 2, bg=ANIMATION_COLOR,
                border_width=2 if is_selected else 1,
                border_color="#FFFFFF" if is_selected else ANIMATION_COLOR + "CC",
                cursor="grab",
                tooltip=text_label + " · arrastra para mover; Supr para borrar",
                label=text_label,
                on_click=_keyframe_key_click(scope, item.id, channel, point.t),
                on_drag=_keyframe_key_drag(scope, item.id, channel, index, row.height),
                on_context_menu=_keyframe_key_menu(scope, item.id, channel, point.t),
            )
        )
    return boxes


def _keyframe_add_box(
    item, scope: str, start: float, row, zoom: float, x0: float, x1: float,
):  # noqa: ANN001, ANN202
    """Zona translúcida de «añadir clave» sobre la barra del elemento/texto (docs/42).

    Ocupa la barra entera del elemento/texto seleccionado y va antes que las asas en la capa:
    las asas siguen ganando el clic para arrastrar una clave existente. Sin la capa (o con el
    modo apagado) el gesto no existe y la barra se comporta como siempre.
    """
    duration = float(item.end - item.start)
    x, w = start * zoom, max(4.0, duration * zoom)
    if duration <= 0 or x > x1 or x + w < x0:
        return None
    top, height = row.top + 2, row.height - 4
    label = "Modo añadir clave: clic para crear una clave del canal elegido"
    return box(
        key=f"anim-add-{scope}-{item.id}", position="absolute", left=x, top=top,
        width=w, height=height, radius=4, bg="#FF6EC70D", border_width=1,
        border_color="#FF6EC766", cursor="crosshair",
        tooltip=label + " · arrastra para colocarla",
        label=label,
        on_drag=_keyframe_add_drag(scope, item.id, row.height),
    )


def _clip_meta(clip, *, offline: bool) -> str:
    """Segunda línea de un clip (duración + velocidad + efectos); la comparten canvas y overlay."""
    meta = tl.format_time(clip.duration)
    if clip.speed != 1.0:
        meta = f"{clip.speed:g}× · {meta}"
    if fx.has_effects(clip):
        meta = f"fx · {meta}"
    if clip.envelope:
        meta = f"◆ env · {meta}"
    if offline:
        meta = "sin archivo · " + meta
    return meta


def tooltip_audio(track, clip) -> str:
    name = track.name or "Audio"
    bits = [name, f"{clip.duration:.2f} s"]
    if track.mode == "absolute":
        bits.append(f"inicio {tl.format_time(float(clip.start or 0.0))}")
    if clip.link_id:
        bits.append("enlazado al video")
    if clip.envelope:
        bits.append(f"envolvente: {len(clip.envelope)} clave{'s' if len(clip.envelope) != 1 else ''}")
    if track.locked:
        bits.append("pista bloqueada")
    if track.solo:
        bits.append("solo")
    elif track.muted:
        bits.append("silenciada")
    return " · ".join(bits)


def _clip_label_x(w: float, x: float) -> float:
    """x de la etiqueta «pegajosa»: sigue visible aunque el inicio del clip quede fuera de la vista."""
    if w <= 80:
        return x + 8
    return min(max(x + 8, state.tl_scroll() + 8), x + w - 60)


def _element_boxes(
    project: Project, row, items, *, zoom: float, x0: float, x1: float,
    selection, multi, color: str,
):  # noqa: ANN001, ANN202
    """Barras de una fila de capas (Elementos o V2/V3), con asas de mover/recortar."""
    boxes = []
    for item in items:
        x, w = item.start * zoom, max(4.0, (item.end - item.start) * zoom)
        if x > x1 or x + w < x0:
            continue
        selected = (
            (selection is not None and selection[0] == "element" and selection[1] == item.id)
            or item.id in multi
        )
        adding = state.key_add_on() and selected
        if item.kind == "video":
            try:
                label = project.asset(item.asset_id or "").name
            except Exception:
                label = "Vídeo"
        else:
            label = element_mod.ELEMENT_LABELS.get(item.kind, item.kind)
        if selected:
            label = f"{label} · {item.start:.2f}–{item.end:.2f}s"
        if item.animations:
            label = f"{label} ◆"
        if item.envelope:
            label = f"{label} ◆ env"
        track = element_mod.video_track_label(item.video_track)
        tooltip = f"{track} · {tl.format_time(item.start)}–{tl.format_time(item.end)}"
        if item.envelope:
            count = len(item.envelope)
            tooltip += f" · envolvente: {count} clave{'s' if count != 1 else ''}"
        boxes.append(
            box(
                key=f"tl-element-{item.id}", position="absolute", left=x,
                top=row.top + 2, width=w, height=row.height - 4, radius=4,
                bg=ANIMATION_COLOR + "22" if adding else color + "33",
                border_width=2 if adding else (1.5 if selected else 1),
                border_color=ANIMATION_COLOR if adding else (color if selected else color + "66"),
                cursor="crosshair" if adding else "grab",
                padding=(2, 6),
                tooltip=tooltip + (" · modo añadir clave (N)" if adding else ""),
                label=f"Capa {track}: {label}" + (" · añadir clave" if adding else ""),
                on_drag=_overlay_drag("element", item.id, "move"),
            )[
                box(key=f"tl-trim-l-{item.id}", position="absolute", left=0, top=0, width=6,
                    height=row.height - 4, radius=3, bg="#00000000", bg_hover=color + "88",
                    cursor="col-resize", on_drag=_overlay_drag("element", item.id, "trim-l")),
                box(key=f"tl-trim-r-{item.id}", position="absolute", right=0, top=0, width=6,
                    height=row.height - 4, radius=3, bg="#00000000", bg_hover=color + "88",
                    cursor="col-resize", on_drag=_overlay_drag("element", item.id, "trim-r")),
                text(_ellipsis(label, w - 14, 10.5), size=10.5, weight=600,
                     color="$text", max_lines=1),
            ]
        )
    return boxes


def _selected_animation_target(project: Project, selection, lay: Tracks):
    """Elemento/texto seleccionado, su fila y el alto útil de la barra."""
    if selection is None:
        return None
    if selection[0] == "text":
        if not project.has_text(selection[1]):
            return None
        item, scope, row = project.text(selection[1]), "text", lay.overlay_for("text")
    elif selection[0] == "element":
        if not project.has_element(selection[1]):
            return None
        item = project.element(selection[1])
        scope = "element"
        row = (
            lay.overlay_for("element") if item.video_track <= 0
            else lay.video_row(item.video_track)
        )
    else:
        return None
    if row is None:
        return None
    return item, scope, row


def _draw_animation_curve(c: Canvas, project: Project, selection, lay: Tracks, zoom: float,
                          x0: float, x1: float) -> None:
    """Polilínea del canal elegido sobre la barra del elemento/texto seleccionado (docs/38)."""
    channel = state.keyframe_channel()
    if channel not in anim.CHANNELS:
        return
    target = _selected_animation_target(project, selection, lay)
    if target is None:
        return
    item, _scope, row = target
    points, _keys = _keyframe_curve(
        item, channel, float(item.start), zoom, row.top + 2, row.height - 4, x0, x1,
    )
    if points:
        c.polyline(points, stroke=ANIMATION_COLOR, stroke_width=1.5)


def _draw_tracks(c: Canvas) -> None:
    project: Project = state.editor_project()
    zoom = state.tl_zoom()
    selection = state.selection()
    multi = state.multi_selection()
    drag = state.tl_drag()
    offline = state.offline_assets()
    state.waveforms()  # dependencia: redibujar cuando llega un waveform
    state.keyframe_channel()  # dependencia: el canal elegido cambia la polilínea (docs/38)
    lay = tracks()
    video_h, audio_top, audio_h = lay.video_h, lay.audio_top, lay.audio_h
    x0, x1 = _visible_range()
    c.clear("$track_bg")
    for row in lay.overlays:
        fill = "$track_alt" if row.kind == "text" else "$track_bg"
        c.rect(0, row.top - 3, c.width, row.height + 6, fill=fill)
    for row in lay.video_rows:
        fill = "$track_alt" if row.track % 2 == 0 else "$track_bg"
        c.rect(0, row.top - 3, c.width, row.height + 6, fill=fill)
    c.rect(0, lay.video_top - 3, c.width, video_h + 6, fill="$track_bg")
    c.rect(0, audio_top - 3, c.width, audio_h + 6, fill="$track_alt")
    for row in lay.rows:
        c.rect(0, row.top - 3, c.width, row.height + 6, fill="$track_bg" if row.index % 2 == 0 else "$track_alt")
    _draw_ruler(c, zoom, max(0.0, x0), x1, lay.content_h)
    _draw_range_and_markers(c, project, zoom, x0, x1, lay.content_h,
                            selection[1] if selection and selection[0] == "marker" else None)

    clips = project.video.clips
    geo = Geometry(project, drag)
    selected_id = selection[1] if selection and selection[0] == "clip" else None
    peaks_by_asset: dict[str, tuple[np.ndarray | None, float]] = {}
    video_gain = video_audio_gain(project)
    for index, clip in enumerate(clips):
        start, end = geo.span(project, index)
        x, w = start * zoom, max(2.0, clip.duration * zoom)
        if x > x1 or x + w < x0:
            continue
        is_offline = clip.asset_id in offline
        moving = drag is not None and drag.kind == "move" and drag.clip_id == clip.id
        selected = clip.id == selected_id or clip.id in multi
        if moving:  # el hueco que ocupará al soltar
            c.rect(x + 1, lay.video_top, w - 2, video_h, radius=6, stroke="$primary", stroke_width=1.5, dash=(5, 4))
            continue
        c.rect(x + 1, lay.video_top, w - 2, video_h, radius=6, fill="$clip")
        c.rect(x + 1, lay.video_top, w - 2, video_h, radius=6,
               stroke="$primary" if selected else "$clip_edge", stroke_width=2 if selected else 1,
               dash=(4, 3) if is_offline else None)
        if selected:
            c.rect(x + 3, lay.video_top + 2, w - 6, 3, radius=1.5, fill="$primary")
        try:
            asset = project.asset(clip.asset_id)
            name = asset.name
        except Exception:
            asset, name = None, "?"
        # Etiqueta «pegajosa»: sigue visible aunque el inicio del clip quede fuera de la vista.
        label_x = _clip_label_x(w, x)
        c.text(label_x, lay.video_top + 10, _ellipsis(name, x + w - label_x - 6, 12), size=12, weight=600)
        meta = _clip_meta(clip, offline=is_offline)
        c.text(label_x, lay.video_top + 28, _ellipsis(meta, x + w - label_x - 6, 10.5), size=10.5,
               color="$danger" if is_offline else "$muted", family="Consolas")

        # Pista de audio enlazada: banda + waveform del tramo de fuente (o aviso de separado).
        c.rect(x + 1, audio_top, w - 2, audio_h, radius=5, fill="$clip_audio")
        if selected:
            c.rect(x + 1, audio_top, w - 2, audio_h, radius=5, stroke="$primary", stroke_width=1.5)
        if asset is not None and not clip.audio_enabled:
            c.text(x + 8, audio_top + 13, _ellipsis("audio separado · pista inferior", w - 12, 10),
                   size=10, color="$muted")
        elif asset is not None and asset.has_audio and zoom >= 20 and not is_offline and video_gain > 0:
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
                    pts = _waveform_points(peaks, vx0, vx1 - vx0, audio_top, audio_h, t0, t1,
                                           gain * video_gain)
                    if pts is not None:
                        c.polygon(pts, fill="$wave")
        elif asset is not None and not asset.has_audio:
            c.text(x + 8, audio_top + 13, _ellipsis("sin audio", w - 12, 10), size=10, color="$muted")
        if clip.envelope:
            _draw_envelope(c, clip, x, w, audio_top, audio_h)

    # Pistas de audio extra (fase 1): todas visibles, en secuencia o con posición absoluta.
    selected_audio = (
        (int(selection[1]), int(selection[2]))
        if selection is not None and selection[0] == "audio" and len(selection) > 2 else None
    )
    for row in lay.rows:
        track = project.audio[row.index]
        starts = tl.clip_starts(track)
        audible = audio_track_audible(project, track)
        selected_track = selected_audio is not None and selected_audio[0] == row.index
        for clip_index, clip in enumerate(track.clips):
            start = starts[clip_index]
            x, w = start * zoom, max(2.0, clip.duration * zoom)
            if x > x1 or x + w < x0:
                continue
            try:
                asset = project.asset(clip.asset_id)
            except Exception:
                continue
            is_offline = clip.asset_id in offline
            selected = (selected_track and selected_audio[1] == clip_index) or clip.id in multi
            fill = "$clip_audio" if audible else "$surface_hover"
            c.rect(x + 1, row.top, w - 2, row.height, radius=5, fill=fill)
            c.rect(x + 1, row.top, w - 2, row.height, radius=5,
                   stroke="$primary" if selected else "$clip_edge", stroke_width=2 if selected else 1,
                   dash=(4, 3) if is_offline else None)
            if asset.has_audio and zoom >= 20 and not is_offline and audible:
                peaks = media.cached_waveform(asset.path)
                if peaks is not None and peaks.size:
                    vx0, vx1 = max(x, x0), min(x + w, x1)
                    if vx1 - vx0 > 2:
                        t0 = clip.in_ + (vx0 - x) / zoom * clip.speed
                        t1 = clip.in_ + (vx1 - x) / zoom * clip.speed
                        pts = _waveform_points(peaks, vx0, vx1 - vx0, row.top, row.height,
                                               t0, t1, track.gain)
                        if pts is not None:
                            c.polygon(pts, fill="$wave")
            _draw_audio_fades(c, clip, x, w, row.top, row.height)
            if clip.envelope:
                _draw_envelope(c, clip, x, w, row.top, row.height)
            label_x = _clip_label_x(w, x)
            label = asset.name
            if clip.link_id:
                label = "↔ " + label
            c.text(label_x, row.top + 7, _ellipsis(label, x + w - label_x - 6, 11), size=11,
                   weight=600)
            if w > 70:
                meta = f"{clip.duration:g}s"
                if track.gain != 1.0:
                    meta += f" · {track.gain * 100:.0f}%"
                if clip.link_id:
                    meta += " · enlazado"
                if clip.envelope:
                    meta += " · ◆ env"
                c.text(label_x, row.top + 22, _ellipsis(meta, x + w - label_x - 6, 9.5), size=9.5,
                       color="$muted", family="Consolas")
            if not audible or track.locked:
                c.text(x + 6, row.top + row.height - 13,
                       "silenciada" if not audible else "bloqueada", size=9, color="$muted")
            if drag is not None and drag.kind == "audio_move" and drag.clip_id == clip.id:
                gx = drag.ghost * zoom
                c.rect(gx + 1, row.top - 2, w - 2, row.height + 4, radius=5,
                       fill="#7C5CFF33", stroke="$primary", stroke_width=1.5, dash=(5, 3))

    # Crossfades de audio (docs/46): zona de solape sobre la waveform y rombo en el corte.
    for row in lay.rows:
        track = project.audio[row.index]
        if track.mode != "sequence" or len(track.clips) < 2:
            continue
        starts = tl.clip_starts(track)
        for index, transition in enumerate(track.transitions):
            boundary_px = starts[index + 1] * zoom
            if boundary_px < x0 - 8 or boundary_px > x1 + 8:
                continue
            selected = (
                selection is not None and selection[0] == "audio"
                and int(selection[1]) == row.index
                and int(selection[2]) in (index, index + 1)
            )
            if transition is not None:
                end_px = (starts[index] + track.clips[index].duration) * zoom
                c.rect(boundary_px, row.top + 2, max(2.0, end_px - boundary_px),
                       row.height - 4, fill="#4CC9F033", stroke=AUDIO_FADE_COLOR + "99",
                       stroke_width=1)
                if zoom >= 40:
                    c.text((boundary_px + end_px) / 2, row.top + row.height - 12,
                           f"fade {transition.duration:g}s", size=9.5,
                           color=AUDIO_FADE_COLOR, align="center")
            y = row.top + 2
            c.polygon(
                [(boundary_px, y - 4), (boundary_px + 5, y + 1), (boundary_px, y + 6),
                 (boundary_px - 5, y + 1)],
                fill=AUDIO_FADE_COLOR if transition is not None or selected else "$surface_hover",
                stroke="$primary" if selected else AUDIO_FADE_COLOR + "AA",
                stroke_width=1.5 if selected else 1,
            )

    # Transiciones: zona de solape + rombo en la frontera.
    for index, transition in enumerate(project.video.transitions):
        a_start, a_end = geo.span(project, index)
        b_start = geo.starts[index + 1]
        if transition is None:
            continue
        xa, xb = b_start * zoom, a_end * zoom
        if xb < x0 or xa > x1:
            continue
        c.rect(xa, lay.video_top + 6, max(2.0, xb - xa), video_h - 12, fill="#F5B85130", stroke="$transition",
               stroke_width=1)
        c.line(xa, lay.video_top + 6, xb, lay.video_top + video_h - 6, stroke="$transition", stroke_width=1)
        c.line(xa, lay.video_top + video_h - 6, xb, lay.video_top + 6, stroke="$transition", stroke_width=1)
        cx = (xa + xb) / 2
        if zoom >= 40:
            c.text(cx, lay.video_top + video_h + 1, f"{transition.kind} {transition.duration:g}s", size=9.5,
                   color="$transition", align="center")
    for index in range(len(project.video.transitions)):
        transition = project.video.transitions[index]
        boundary = (geo.starts[index + 1] + (transition.duration / 2 if transition else 0.0)) * zoom
        if boundary < x0 or boundary > x1:
            continue
        selected = selection == ("transition", index)
        y = lay.video_top - 1
        c.polygon([(boundary, y - 6), (boundary + 6, y), (boundary, y + 6), (boundary - 6, y)],
                  fill="$transition" if transition is not None or selected else "$surface_hover",
                  stroke="$primary" if selected else "$clip_edge", stroke_width=1.5 if selected else 1)

    # Polilínea del canal de animación del elemento/texto seleccionado (docs/38). Va en el
    # canvas, debajo de su barra translúcida, para que siga viéndose sin tapar clics.
    _draw_animation_curve(c, project, selection, lay, zoom, x0, x1)

    # Fantasma del bloque de la selección múltiple (fase 3): cubre todas las pistas.
    if drag is not None and drag.kind == "block" and drag.length > 0:
        gx, gw = drag.ghost * zoom, max(2.0, drag.length * zoom)
        c.rect(gx, lay.video_top - 3, gw, lay.content_h - lay.video_top - 5, radius=6, fill="#7C5CFF22",
               stroke="$primary", stroke_width=1.5, dash=(6, 4))

    # Fantasma del clip que se arrastra (encima de todo).
    if drag is not None and drag.kind == "move" and project.has_clip(drag.clip_id):
        clip = project.clip(drag.clip_id)
        x, w = drag.ghost * zoom, max(2.0, clip.duration * zoom)
        c.rect(x + 1, lay.video_top - 2, w - 2, video_h + 4, radius=6, fill="#7C5CFF55", stroke="$primary", stroke_width=2)
        try:
            name = project.asset(clip.asset_id).name
        except Exception:
            name = "?"
        c.text(x + 8, lay.video_top + 10, _ellipsis(name, w - 14, 12), size=12, weight=600)


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
                    top=lay.video_top + 1,
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
                    top=lay.video_top + 6,
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


def _run(name: str, *args) -> None:
    editor = _editor()
    if editor is not None:
        getattr(editor, name)(*args)


def _canvas_drag(e) -> None:  # noqa: ANN001
    editor = _editor()
    if editor is None:
        return
    if state.razor_on():  # regla o zona vacía: corta todas las pistas en ese punto
        if e.phase == "start":
            editor.razor_at(e.x, everything=True)
        return
    if state.marquee_on():
        if e.phase == "start":
            editor.begin_marquee(e.x)
        elif e.phase == "move":
            editor.drag_marquee(e.x)
        else:
            editor.drag_marquee(e.x)
            editor.end_marquee()
        return
    editor.scrub_to_window_x(e.x)


def _razor_drag(track_index: int | None):
    """Con la razor, el clic (fase `start` del drag, que trae la x de ventana) corta la pista."""
    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is not None and e.phase == "start":
            editor.razor_at(e.x, track_index)

    return handler


def _marker_click(marker_id: str):
    def handler() -> None:
        editor = _editor()
        if editor is not None:
            editor.select_marker(marker_id)

    return handler


def _overlay_drag(kind: str, item_id: str, mode: str):
    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is None:
            return
        if e.phase == "start":
            editor.begin_overlay_drag(kind, item_id, mode)
        elif e.phase == "move":
            editor.drag_overlay(e.dx)
        else:
            editor.drag_overlay(e.dx)
            editor.end_overlay_drag()

    return handler


def _envelope_key_drag(scope: str, item_id: str, track_index: int, index: int, height: float):
    """Arrastra un asa de la envolvente; el primer movimiento abre la transacción (docs/37)."""

    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is None:
            return
        if e.phase == "start":
            editor.begin_envelope_key_drag(scope, item_id, track_index, index, height)
        elif e.phase == "move":
            editor.drag_envelope_key(e.dx, e.dy)
        else:
            editor.drag_envelope_key(e.dx, e.dy)
            editor.end_envelope_key_drag()

    return handler


def _envelope_add_drag(scope: str, item_id: str, track_index: int, height: float):
    """Clic/arrastre sobre la waveform en modo envolvente: crea y mueve la clave."""

    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is None:
            return
        if e.phase == "start":
            editor.begin_envelope_add(scope, item_id, track_index, e.x, height)
        elif e.phase == "move":
            editor.drag_envelope_key(e.dx, e.dy)
        else:
            editor.drag_envelope_key(e.dx, e.dy)
            editor.end_envelope_key_drag()

    return handler


def _audio_fade_drag(track_index: int, clip_id: str, side: str):
    """Arrastra el asa de un fundido de audio; el primer movimiento abre la transacción."""

    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is None:
            return
        if e.phase == "start":
            editor.begin_audio_fade_drag(track_index, clip_id, side)
        elif e.phase == "move":
            editor.drag_audio_fade(e.dx)
        else:
            editor.drag_audio_fade(e.dx)
            editor.end_audio_fade_drag()

    return handler


def _audio_crossfade_drag(track_index: int, index: int):
    """Arrastra el asa de un crossfade de audio (corte de una pista secuencial)."""

    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is None:
            return
        if e.phase == "start":
            editor.begin_audio_crossfade_drag(track_index, index)
        elif e.phase == "move":
            editor.drag_audio_crossfade(e.dx)
        else:
            editor.drag_audio_crossfade(e.dx)
            editor.end_audio_crossfade_drag()

    return handler


def _envelope_key_menu(scope: str, item_id: str, track_index: int, t: float):
    def items() -> list:  # noqa: ANN202
        def copy_key() -> None:
            editor = _editor()
            if editor is not None:
                editor.copy_envelope_key_at(scope, item_id, track_index, t)

        def paste_key() -> None:
            editor = _editor()
            if editor is not None:
                editor.paste_key()

        def remove() -> None:
            editor = _editor()
            if editor is not None:
                editor.delete_envelope_key_at(scope, item_id, track_index, t)

        return [
            menus.item("Copiar clave", copy_key),
            menus.item("Pegar clave", paste_key),
            menus.item("Eliminar clave de volumen", remove),
        ]

    return items


def _envelope_key_click(scope: str, item_id: str, track_index: int, t: float):
    def handler() -> None:
        editor = _editor()
        if editor is not None:
            editor.select_envelope_key(scope, item_id, track_index, t)

    return handler


def _keyframe_add_drag(scope: str, item_id: str, height: float):
    """Clic/arrastre en la barra en modo «añadir clave»: crea la clave y la deja arrastrable."""

    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is None:
            return
        if e.phase == "start":
            editor.begin_keyframe_add(scope, item_id, e.x, height)
        elif e.phase == "move":
            editor.drag_keyframe_key(e.dx, e.dy)
        else:
            editor.drag_keyframe_key(e.dx, e.dy)
            editor.end_keyframe_key_drag()

    return handler


def _keyframe_key_drag(scope: str, item_id: str, channel: str, index: int, height: float):
    """Arrastra un asa de animación; el primer movimiento abre la transacción (docs/38)."""

    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is None:
            return
        if e.phase == "start":
            editor.begin_keyframe_key_drag(scope, item_id, channel, index, height)
        elif e.phase == "move":
            editor.drag_keyframe_key(e.dx, e.dy)
        else:
            editor.drag_keyframe_key(e.dx, e.dy)
            editor.end_keyframe_key_drag()

    return handler


def _keyframe_key_click(scope: str, item_id: str, channel: str, t: float):
    def handler() -> None:
        editor = _editor()
        if editor is not None:
            editor.select_keyframe_key(scope, item_id, channel, t)

    return handler


def _keyframe_key_menu(scope: str, item_id: str, channel: str, t: float):
    def items() -> list:  # noqa: ANN202
        def copy_key() -> None:
            editor = _editor()
            if editor is not None:
                editor.copy_keyframe_key_at(scope, item_id, channel, t)

        def paste_key() -> None:
            editor = _editor()
            if editor is not None:
                editor.paste_key()

        def remove() -> None:
            editor = _editor()
            if editor is not None:
                editor.delete_keyframe_key_at(scope, item_id, channel, t)

        label = KEYFRAME_LABELS.get(channel, channel)
        return [
            menus.item("Copiar clave", copy_key),
            menus.item("Pegar clave", paste_key),
            menus.item(f"Eliminar clave {label}", remove),
        ]

    return items


def _select_audio(track_index: int, clip_index: int) -> None:
    editor = _editor()
    if editor is not None:
        editor.select_audio(track_index, clip_index)


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


def _audio_clip_drag(track_index: int, clip_id: str):
    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is None:
            return
        if e.phase == "start":
            editor.begin_move_audio(track_index, clip_id)
        elif e.phase == "move":
            editor.drag_move_audio(e.dx)
        else:
            editor.drag_move_audio(e.dx)
            editor.end_drag()

    return handler


def _audio_trim_drag(track_index: int, clip_id: str, side: str):
    def handler(e) -> None:  # noqa: ANN001
        editor = _editor()
        if editor is None:
            return
        if e.phase == "start":
            editor.begin_trim_audio(track_index, clip_id, side)
        elif e.phase == "move":
            editor.drag_trim_audio(e.dx)
        else:
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
    state.multi_selection()  # dependencia: la marquesina repinta la selección
    state.thumbs()  # dependencia: las miniaturas llegan en un hilo y hay que repintarlas
    x0, x1 = _visible_range()
    width = content_width(project, zoom, state.tl_view_w())
    geo = Geometry(project, drag)
    lay = tracks()
    video_h, content_h = lay.video_h, lay.content_h
    thumbs, labels = _filmstrip_elements(project, geo, zoom, x0, x1, lay)
    marquee = state.marquee()
    marquee_box = (
        box(key="marquee", position="absolute", left=min(marquee.x0, marquee.x1), top=0,
            width=max(1.0, abs(marquee.x1 - marquee.x0)), height=content_h,
            bg="#7C5CFF22", radius=3)
        if marquee is not None else None
    )

    razor = state.razor_on()
    boxes = []
    # Capas globales: textos y elementos con su intervalo, encima de la pista de video. Son
    # barras con asas propias (mover y recortar) sin tocar el modelo: `start`/`end` ya existen.
    for row in lay.overlays:
        if row.kind == "text":
            for item in project.texts:
                x, w = item.start * zoom, max(4.0, (item.end - item.start) * zoom)
                if x > x1 or x + w < x0:
                    continue
                color = "#38D6B0" if item.subtitle else "#F5B851"
                selected = selection is not None and selection[0] == "text" and selection[1] == item.id
                adding = state.key_add_on() and selected
                label = item.text
                if selected:
                    label = f"{label} · {item.start:.2f}–{item.end:.2f}s"
                if item.animations:
                    label = f"{label} ◆"
                boxes.append(
                    box(
                        key=f"tl-text-{item.id}", position="absolute", left=x,
                        top=row.top + 2, width=w, height=row.height - 4, radius=4,
                        bg=ANIMATION_COLOR + "22" if adding else color + "33",
                        border_width=2 if adding else (1.5 if selected else 1),
                        border_color=ANIMATION_COLOR if adding else (color if selected else color + "66"),
                        cursor="crosshair" if adding else "grab",
                        padding=(2, 6),
                        tooltip=f"Texto: {tl.format_time(item.start)}–{tl.format_time(item.end)}"
                                + (" · modo añadir clave (N)" if adding else ""),
                        label=f"Capa {label}" + (" · añadir clave" if adding else ""),
                        on_drag=_overlay_drag("text", item.id, "move"),
                    )[
                        box(key=f"tl-trim-l-{item.id}", position="absolute", left=0, top=0, width=6,
                            height=row.height - 4, radius=3, bg="#00000000", bg_hover=color + "88",
                            cursor="col-resize", on_drag=_overlay_drag("text", item.id, "trim-l")),
                        box(key=f"tl-trim-r-{item.id}", position="absolute", right=0, top=0, width=6,
                            height=row.height - 4, radius=3, bg="#00000000", bg_hover=color + "88",
                            cursor="col-resize", on_drag=_overlay_drag("text", item.id, "trim-r")),
                        text(_ellipsis(label, w - 14, 10.5), size=10.5, weight=600,
                             color="$text", max_lines=1),
                    ]
                )
        else:
            generic = [e for e in project.elements if e.video_track <= 0]
            boxes.extend(_element_boxes(
                project, row, generic,
                zoom=zoom, x0=x0, x1=x1, selection=selection, multi=state.multi_selection(),
                color="#7C5CFF",
            ))
    for row in lay.video_rows:
        color = "#4EA1FF" if row.track == 1 else "#38D6B0"
        items = [e for e in project.elements if e.video_track == row.track]
        boxes.extend(_element_boxes(
            project, row, items,
            zoom=zoom, x0=x0, x1=x1, selection=selection, multi=state.multi_selection(),
            color=color,
        ))
    for index, clip in enumerate(project.video.clips):
        start, _ = geo.span(project, index)
        x, w = start * zoom, max(4.0, clip.duration * zoom)
        if x > x1 or x + w < x0:
            continue
        if razor:
            boxes.append(box(key=f"clip-{clip.id}", position="absolute", left=x, top=lay.video_top, width=w,
                             height=video_h, radius=6, bg="#00000000", bg_hover="#FF547018",
                             cursor="crosshair", on_drag=_razor_drag(None)))
            continue
        boxes.append(
            box(
                key=f"clip-{clip.id}",
                position="absolute",
                left=x,
                top=lay.video_top,
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
    for row in lay.rows:
        track = project.audio[row.index]
        starts = tl.clip_starts(track)
        locked = track.locked
        for index, clip in enumerate(track.clips):
            start = starts[index]
            x, w = start * zoom, max(4.0, clip.duration * zoom)
            if x > x1 or x + w < x0:
                continue
            handlers = {} if locked else {"on_drag": _audio_clip_drag(row.index, clip.id)}
            if locked:
                handlers["on_click"] = lambda ti=row.index, ci=index: _select_audio(ti, ci)
            elif razor:
                handlers = {"on_drag": _razor_drag(row.index)}
            handles = []
            if not locked and not razor:
                handles = [
                    box(key=f"audio-trim-l-{clip.id}", position="absolute", left=0, top=0,
                        width=min(HANDLE_W, w / 3), height=row.height, radius=4, bg="#00000000",
                        bg_hover="#7C5CFF88", cursor="col-resize",
                        on_drag=_audio_trim_drag(row.index, clip.id, "l")),
                    box(key=f"audio-trim-r-{clip.id}", position="absolute", right=0, top=0,
                        width=min(HANDLE_W, w / 3), height=row.height, radius=4, bg="#00000000",
                        bg_hover="#7C5CFF88", cursor="col-resize",
                        on_drag=_audio_trim_drag(row.index, clip.id, "r")),
                ]
            boxes.append(
                box(key=f"audio-{clip.id}", position="absolute", left=x, top=row.top,
                    width=w, height=row.height, radius=5, bg="#00000000",
                    bg_hover="#FFFFFF0D",
                    cursor="default" if locked else ("crosshair" if razor else "grab"),
                    tooltip=tooltip_audio(track, clip), **handlers)[handles]
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
                top=lay.video_top - 9,
                width=16,
                height=16,
                radius=8,
                bg="#7C5CFF40" if selected else "#00000000",
                bg_hover="#7C5CFF55",
                cursor="pointer",
                tooltip=label,
                label=label,
                on_click=pick,
            )
        )

    # Marcadores: una caja pequeña en la regla (clic = seleccionar y saltar ahí). Solo tapan su
    # bandera; el resto de la regla sigue haciendo scrub.
    for marker in project.markers:
        mx = marker.t * zoom
        if mx < x0 - MARKER_W or mx > x1 + MARKER_W:
            continue
        marker_label = f"{marker.name} · {tl.format_time(marker.t)}"
        boxes.append(box(key=f"marker-{marker.id}", position="absolute", left=mx - MARKER_W / 2, top=0,
                         width=MARKER_W, height=14, radius=3, bg="#00000000", bg_hover="#F5B85155",
                         cursor="pointer", tooltip=marker_label, label=marker_label,
                         on_click=_marker_click(marker.id)))

    return box(key="tl-layer", width=width, height=content_h, shrink=0)[
        canvas(_draw_tracks, key="tl-canvas", width=width, height=content_h, on_drag=_canvas_drag,
               cursor="crosshair" if razor else "default"),
        thumbs,
        labels,
        marquee_box,
        boxes,
    ]


def _selected_envelope_boxes(
    project: Project, selection, lay: Tracks, zoom: float, x0: float, x1: float,
):  # noqa: ANN001
    """Asas y zona de añadir del elemento seleccionado (docs/37).

    Van en su propia capa **encima del playhead**: la línea de 2 px del playhead bloquearía el
    centro de un asa justo en ese instante (lo normal al grabar una clave desde el inspector).
    """
    if selection is None or state.razor_on():
        return []
    boxes: list = []
    if selection[0] == "clip":
        if not project.has_clip(selection[1]) or project.video.locked:
            return []
        index = project.video.index_of(selection[1])
        clip = project.video.clips[index]
        start = tl.clip_starts(project.video)[index]
        boxes.extend(_clip_envelope_boxes(
            clip, start, -1, lay.audio_top, lay.audio_h, zoom, x0, x1, scope="video",
        ))
    elif selection[0] == "audio":
        try:
            track_index, clip_index = int(selection[1]), int(selection[2])
        except (IndexError, TypeError, ValueError):
            return []
        if not 0 <= track_index < len(project.audio):
            return []
        track = project.audio[track_index]
        if track.locked or not 0 <= clip_index < len(track.clips):
            return []
        row = lay.row_for(track_index)
        if row is None:
            return []
        clip = track.clips[clip_index]
        start = tl.clip_starts(track)[clip_index]
        boxes.extend(_clip_envelope_boxes(
            clip, start, track_index, row.top, row.height, zoom, x0, x1, scope="audio",
        ))
    elif selection[0] == "element":
        if not project.has_element(selection[1]):
            return []
        item = project.element(selection[1])
        row = (
            lay.overlay_for("element") if item.video_track <= 0
            else lay.video_row(item.video_track)
        )
        if row is not None:
            boxes.extend(_element_envelope_boxes(item, row, zoom, x0, x1))
    return boxes


def _selected_keyframe_boxes(
    project: Project, selection, lay: Tracks, zoom: float, x0: float, x1: float,
):  # noqa: ANN001
    """Asas de animación y zona de añadir del elemento/texto seleccionado (docs/38 y docs/42).

    Van en su propia capa **encima del playhead**, igual que las de las envolventes: la línea
    de 2 px del playhead bloquearía el centro de un asa en ese instante. La zona de añadir va
    primero para que un asa existente siga ganando su propio clic.
    """
    if selection is None or state.razor_on():
        return []
    target = _selected_animation_target(project, selection, lay)
    if target is None:
        return []
    item, scope, row = target
    boxes = []
    if state.key_add_on():
        add = _keyframe_add_box(item, scope, float(item.start), row, zoom, x0, x1)
        if add is not None:
            boxes.append(add)
    boxes.extend(_keyframe_boxes(item, scope, float(item.start), row, zoom, x0, x1))
    return boxes


def _selected_audio_handles(
    project: Project, selection, lay: Tracks, zoom: float, x0: float, x1: float,
):  # noqa: ANN001
    """Asas de fundido del clip de audio seleccionado + crossfades de las pistas (docs/46)."""
    boxes = []
    if selection is not None and selection[0] == "audio" and not state.razor_on():
        try:
            track_index, clip_index = int(selection[1]), int(selection[2])
        except (IndexError, TypeError, ValueError):
            track_index = clip_index = -1
        if 0 <= track_index < len(project.audio):
            track = project.audio[track_index]
            if not track.locked and 0 <= clip_index < len(track.clips):
                row = lay.row_for(track_index)
                if row is not None:
                    clip = track.clips[clip_index]
                    start = tl.clip_starts(track)[clip_index]
                    boxes.extend(
                        _audio_fade_boxes(clip, start, track_index, row, zoom, x0, x1)
                    )
    boxes.extend(_audio_crossfade_boxes(project, lay, zoom, x0, x1))
    return boxes


@component
def audio_handles_layer():
    project: Project = state.editor_project()
    selection = state.selection()
    zoom = state.tl_zoom()
    x0, x1 = _visible_range()
    boxes = _selected_audio_handles(project, selection, tracks(), zoom, x0, x1)
    # Contenedor mínimo: cualquier caja grande encima taparía los clics del timeline.
    return box(key="audio-handles-layer", position="absolute", left=0, top=0, width=1, height=1)[boxes]


@component
def animation_handles_layer():
    state.keyframe_channel()  # dependencias: canal y clave seleccionada repintan las asas
    state.keyframe_key()
    state.key_add_on()  # el modo añadir muestra/oculta la zona de la barra (docs/42)
    project: Project = state.editor_project()
    selection = state.selection()
    zoom = state.tl_zoom()
    x0, x1 = _visible_range()
    boxes = _selected_keyframe_boxes(project, selection, tracks(), zoom, x0, x1)
    # Contenedor mínimo: cualquier caja grande encima taparía los clics del timeline.
    return box(key="anim-layer", position="absolute", left=0, top=0, width=1, height=1)[boxes]


@component
def envelope_handles_layer():
    state.env_edit_on()  # dependencias: el modo y la clave seleccionada repintan las asas
    state.envelope_key()
    project: Project = state.editor_project()
    selection = state.selection()
    zoom = state.tl_zoom()
    x0, x1 = _visible_range()
    boxes = _selected_envelope_boxes(project, selection, tracks(), zoom, x0, x1)
    # Contenedor mínimo: cualquier caja grande encima taparía los clics del timeline.
    return box(key="env-layer", position="absolute", left=0, top=0, width=1, height=1)[boxes]


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
    """Cabeceras de pista: nombre, mute, solo, bloqueo y volumen (fase 1, docs/19)."""
    from aurora import button

    project = state.editor_project()
    lay = tracks()

    def mini(label: str, handler, *, active: bool, tooltip: str, key: str):
        return button(
            label, handler, variant="soft", key=key,
            width=20, height=20, padding=(0, 0), font_size=9.5, radius="$sm",
            bg="$primary" if active else "$surface_hover",
            color="#FFFFFF" if active else "$muted",
            tooltip=tooltip,
        )

    def header(top: float, height: float, title: str, *, active: bool = False, children=()):  # noqa: ANN001
        return box(position="absolute", left=0, top=top, width=LABEL_W - 6, height=height,
                   radius=6, bg="$surface_hover" if active else "$surface", padding=(4, 6), gap=3,
                   border_width=1, border_color="$primary" if active else "$border")[
            row(gap=3, align="center")[
                text(title, size=10.5, weight=700, grow=1, width=0, max_lines=1),
                *children,
            ]
        ]

    video = project.video
    video_gain = 0.0 if video.muted else video.gain
    overlay_headers = [
        header(
            row.top, row.height,
            "Textos" if row.kind == "text" else "Elementos",
            children=(text(f"{row.count}", size=9.5, color="$muted", family="Consolas"),),
        )
        for row in lay.overlays
    ]
    video_row_headers = [
        header(
            row.top, row.height, f"V{row.track + 1}",
            children=(
                text(f"{row.count}", size=9.5, color="$muted", family="Consolas"),
                mini(
                    "+", lambda t=row.track: _run("browse_pip_track", t), active=False,
                    tooltip=f"Añadir un video a V{row.track + 1} en el playhead",
                    key=f"trk-add-{row.track}",
                ),
            ),
        )
        for row in lay.video_rows
    ]
    video_header = header(
        lay.video_top, lay.video_h + TRACK_GAP + lay.audio_h, "Video",
        children=(
            mini("M", lambda: _run("toggle_video_mute"), active=video.muted,
                 tooltip="Silenciar el audio del video", key="trk-video-mute"),
            mini("L", lambda: _run("toggle_video_lock"), active=video.locked,
                 tooltip="Bloquear la pista de video", key="trk-video-lock"),
            button(f"{video_gain * 100:.0f}%", lambda: _run("cycle_track_gain"), variant="soft",
                   key="trk-video-vol", width=42, height=20, padding=(0, 0), font_size=9.5,
                   radius="$sm", tooltip="Volumen del audio del video (clic para cambiar)"),
        ),
    )

    headers = [*overlay_headers, *video_row_headers, video_header]
    selected = state.selection()
    for audio_row in lay.rows:
        track = project.audio[audio_row.index]
        linked = any(clip.link_id for clip in track.clips)
        name = track.name or f"Audio {audio_row.index + 1}"
        if linked:
            name += " ↔"
        active = (
            selected is not None and selected[0] == "audio"
            and int(selected[1]) == audio_row.index
        )
        headers.append(header(
            audio_row.top, audio_row.height, name, active=active,
            children=(
                mini("M", lambda i=audio_row.index: _run("toggle_audio_mute", i), active=track.muted,
                     tooltip="Silenciar esta pista", key=f"trk-mute-{audio_row.index}"),
                mini("S", lambda i=audio_row.index: _run("toggle_audio_solo", i), active=track.solo,
                     tooltip="Solo: aísla esta pista", key=f"trk-solo-{audio_row.index}"),
                mini("L", lambda i=audio_row.index: _run("toggle_audio_lock", i), active=track.locked,
                     tooltip="Bloquear mover/recortar/borrar", key=f"trk-lock-{audio_row.index}"),
                button(f"{track.gain * 100:.0f}%", lambda i=audio_row.index: _run("cycle_audio_gain", i),
                       variant="soft", key=f"trk-vol-{audio_row.index}", width=42, height=20,
                       padding=(0, 0), font_size=9.5, radius="$sm",
                       tooltip="Volumen de la pista (clic para cambiar)"),
            ),
        ))
    return box(width=LABEL_W, height=lay.content_h, shrink=0)[headers]


def timeline_view():
    """Regla + pistas desplazables (lo que no es la barra de herramientas).

    Alternativa al scroll vertical anidado de Aurora 0.9 (ver docs/19): el panel crece con las
    pistas y la UI limita a `MAX_AUDIO_TRACKS` las pistas nuevas, para no aplastar el preview.
    """
    project = state.editor_project()
    width = content_width(project, state.tl_zoom(), state.tl_view_w())
    content_h = tracks().content_h
    return row(grow=0, shrink=0, height=content_h + 12)[
        track_labels(),
        column(grow=1, width=0)[
            scroll(axis="x", key="tl-scroll", height=content_h + 12, on_scroll=_on_scroll)[
                box(key="tl-content", width=width, height=content_h, shrink=0, drop_target=True)[
                    timeline_layer(),
                    playhead_layer(),
                    audio_handles_layer(),
                    envelope_handles_layer(),
                    animation_handles_layer(),
                ]
            ]
        ],
    ]
