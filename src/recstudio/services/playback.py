"""Preview del editor: decode con PyAV, composición (transiciones aproximadas) y reloj de 30 Hz.

- `VideoSource`: un decoder por asset. `seek` al keyframe anterior y decode hacia delante; en
  reproducción secuencial **no** vuelve a hacer seek (avanza el generador, que es lo rápido).
  El escalado lo hace swscale dentro de `to_ndarray(width, height)` (≈1.2 ms por frame
  1080p→540p, más rápido que el `Image.reduce` medido en F0 porque evita el RGB a tamaño real).
- `FrameRenderer`: t global → `core.timeline.locate` → frame del clip; dentro de una transición
  decodifica también el entrante (otro decoder, aunque sea el mismo asset) y aproxima el efecto
  en numpy, y aplica los efectos del clip (`core/effects.py`). El render definitivo es el de
  FFmpeg (`core/graph.py`).
- `Player`: hilo con reloj `perf_counter`: `t = t_inicio + (ahora − t_wall_0) · velocidad`; si un
  frame tarda más que el tick se descartan ticks (nunca se acumula retraso). La velocidad puede
  ser negativa (J, hacia atrás: cada frame es un seek, así que va a menos fps y sin audio) y el
  loop repite el rango In/Out o todo el timeline (fase 3, docs/21).
- `AudioPreview`: audio *best-effort* por `sounddevice`; si falla o se queda sin datos, silencio.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
import wave
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from ..core import effects as fx
from ..core import elements as el
from ..core import global_effects
from ..core import graph as graph_mod
from ..core import overlays
from ..core import timeline as tl
from ..core import transform as tx
from ..core.color import apply_eq
from ..core.project import Clip, MediaAsset, Project, to_dict, video_audio_gain
from ..platform import ffmpeg as ffmpeg_mod
from ..platform import paths
from . import element_assets, media

PREVIEW_HEIGHT = 540
TICK_HZ = 30.0
JPEG_QUALITY = 80


def preview_size(project: Project, max_height: int = PREVIEW_HEIGHT) -> tuple[int, int]:
    """Tamaño del preview con el aspecto del proyecto (pares, como pide el JPEG 4:2:0)."""
    height = max(2, min(max_height, project.height))
    width = max(2, round(height * project.width / project.height))
    return width - width % 2, height - height % 2


def _fit(src_w: int, src_h: int, box_w: int, box_h: int) -> tuple[int, int]:
    scale = min(box_w / src_w, box_h / src_h)
    w = max(2, int(round(src_w * scale)))
    h = max(2, int(round(src_h * scale)))
    return w - w % 2, h - h % 2


def rotate_rgb(rgb: np.ndarray, rotation: int, *, clockwise: bool) -> np.ndarray:
    """Gira un frame RGB con la misma convención que los filtros `transpose` del graph."""
    rotation = tx.normalize_rotation(rotation)
    if rotation == 180:
        return np.rot90(rgb, 2)
    if rotation == 0:
        return rgb
    turn = 1 if (rotation == 90) != clockwise else -1
    return np.rot90(rgb, turn)


def encode_jpeg(rgb: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(rgb).save(buffer, "JPEG", quality=quality, subsampling=2)
    return buffer.getvalue()


# -- decode -------------------------------------------------------------------------


class VideoSource:
    """Decoder de un asset con cache del último frame entregado."""

    # Hasta cuánto se avanza decodificando antes de preferir un seek (s).
    FORWARD_WINDOW = 1.5

    def __init__(self, path: str) -> None:
        import av

        self.path = path
        self._container = av.open(path)
        self._stream = self._container.streams.video[0]
        self._stream.thread_type = "AUTO"
        self._tb = float(self._stream.time_base)
        rate = self._stream.average_rate or self._stream.guessed_rate or 30
        self._frame_dur = 1.0 / float(rate)
        self._frames = None  # generador de decode activo
        self._pending = None  # frame decodificado pero aún no entregado (está por delante)
        self._last_t: float | None = None  # pts del último frame entregado
        self._last_key: tuple[float, tuple[int, int]] | None = None
        self._last_rgb: np.ndarray | None = None
        self.seeks = 0
        # El render y la precarga del clip siguiente (otro hilo) pueden pedir frames a la vez.
        self.lock = threading.Lock()

    def close(self) -> None:
        try:
            self._container.close()
        except Exception:
            pass

    def _seek(self, t: float) -> None:
        self._container.seek(max(0, int(t / self._tb)), stream=self._stream, backward=True)
        self._frames = self._container.decode(self._stream)
        self._pending = None
        self.seeks += 1

    def _next(self):  # noqa: ANN202
        if self._pending is not None:
            frame, self._pending = self._pending, None
            return frame
        if self._frames is None:
            return None
        try:
            return next(self._frames)
        except StopIteration:
            self._frames = None
            return None

    def frame_at(self, t: float, size: tuple[int, int]) -> np.ndarray | None:
        """Frame RGB de la fuente que se ve en `t` (s), escalado a `size` (ancho, alto)."""
        with self.lock:
            return self._frame_at(t, size)

    def _frame_at(self, t: float, size: tuple[int, int]) -> np.ndarray | None:
        half = self._frame_dur / 2
        if self._last_t is not None and self._last_rgb is not None and self._last_key is not None:
            if self._last_key[1] == size and self._last_t - half <= t < self._last_t + half:
                return self._last_rgb
        sequential = (
            self._frames is not None
            and self._last_t is not None
            and self._last_t - half <= t <= self._last_t + self.FORWARD_WINDOW
        )
        if not sequential:
            self._seek(t)
        chosen = None
        while True:
            frame = self._next()
            if frame is None:
                break
            ft = frame.time if frame.time is not None else 0.0
            if ft + half > t:
                if chosen is None or ft - half <= t:
                    chosen = frame
                else:  # este ya es del futuro: se guarda para la siguiente petición
                    self._pending = frame
                break
            chosen = frame
        if chosen is None:
            return self._last_rgb
        self._last_t = chosen.time if chosen.time is not None else t
        rgb = chosen.to_ndarray(format="rgb24", width=size[0], height=size[1], interpolation="AREA")
        self._last_key = (self._last_t, size)
        self._last_rgb = rgb
        return rgb


class DecoderPool:
    """Decoders abiertos por `(ruta, ranura)`, cerrados por LRU (mínimo 2 vivos).

    La ranura es la paridad del índice del clip: dos clips consecutivos del mismo asset (lo
    típico tras un split) usan decoders distintos, así que el entrante de una transición y la
    precarga del clip siguiente no obligan a hacer seek al decoder que está en uso.
    """

    def __init__(self, capacity: int = 4) -> None:
        self.capacity = max(2, capacity)
        self._items: OrderedDict[tuple[str, int], VideoSource] = OrderedDict()
        self._failed: set[str] = set()
        self._lock = threading.Lock()

    def get(self, path: str, slot: int = 0) -> VideoSource | None:
        key = (path, slot)
        with self._lock:
            source = self._items.get(key)
            if source is not None:
                self._items.move_to_end(key)
                return source
            if path in self._failed:
                return None
            try:
                source = VideoSource(path)
            except Exception:
                self._failed.add(path)  # offline o ilegible: no se reintenta en cada frame
                return None
            self._items[key] = source
            while len(self._items) > self.capacity:
                _, old = self._items.popitem(last=False)
                with old.lock:
                    old.close()
            return source

    def forget_failures(self) -> None:
        self._failed.clear()

    def close(self) -> None:
        with self._lock:
            for source in self._items.values():
                with source.lock:
                    source.close()
            self._items.clear()


# -- composición ---------------------------------------------------------------------

_NOISE: dict[tuple[int, int], np.ndarray] = {}


def _noise(h: int, w: int) -> np.ndarray:
    key = (h, w)
    if key not in _NOISE:
        _NOISE[key] = np.random.default_rng(7).random((h, w), dtype=np.float32)
    return _NOISE[key]


def blend(kind: str, a: np.ndarray, b: np.ndarray, p: float) -> np.ndarray:
    """Aproximación en numpy de las transiciones `xfade` (a = saliente, b = entrante)."""
    p = float(min(1.0, max(0.0, p)))
    if p <= 0.0:
        return a
    if p >= 1.0:
        return b
    h, w = a.shape[:2]
    if kind == "fadeblack" or kind == "fadewhite":
        level = 0.0 if kind == "fadeblack" else 255.0
        if p < 0.5:
            k = p * 2
            return (a * (1 - k) + level * k).astype(np.uint8)
        k = (p - 0.5) * 2
        return (level * (1 - k) + b * k).astype(np.uint8)
    if kind == "wipeleft":  # el entrante aparece desde la derecha
        x = int(round(w * (1 - p)))
        out = a.copy()
        out[:, x:] = b[:, x:]
        return out
    if kind == "wiperight":  # el entrante aparece desde la izquierda
        x = int(round(w * p))
        out = a.copy()
        out[:, :x] = b[:, :x]
        return out
    if kind == "slideleft":
        x = int(round(w * p))
        out = np.empty_like(a)
        out[:, : w - x] = a[:, x:]
        out[:, w - x :] = b[:, :x]
        return out
    if kind == "circleopen":
        yy, xx = np.ogrid[:h, :w]
        radius = p * np.hypot(w / 2, h / 2)
        mask = (xx - w / 2) ** 2 + (yy - h / 2) ** 2 < radius * radius
        out = a.copy()
        out[mask] = b[mask]
        return out
    if kind == "dissolve":
        mask = _noise(h, w) < p
        out = a.copy()
        out[mask] = b[mask]
        return out
    # fade (y cualquier otra): mezcla lineal
    return (a * (1 - p) + b * p).astype(np.uint8)


APPROXIMATE = frozenset({"wipeleft", "wiperight", "slideleft", "circleopen", "dissolve"})


# -- efectos del preview (las mismas curvas que `core.graph`) ------------------------------

# `apply_eq` se importa de `core.color` (la comparten los efectos globales); aquí se mantiene el
# nombre histórico `playback.apply_eq` para no romper a quien lo importe.


def apply_zoom(rgb: np.ndarray, factor: float, cx: float, cy: float) -> np.ndarray:
    """Recorte centrado en (cx, cy) de `1/factor` del frame, reescalado al tamaño original
    (`crop=iw/f:ih/f:(iw-ow)*cx:(ih-oh)*cy` + `scale` del graph)."""
    from PIL import Image

    h, w = rgb.shape[:2]
    cw, ch = max(2, int(round(w / factor))), max(2, int(round(h / factor)))
    x, y = int(round((w - cw) * cx)), int(round((h - ch) * cy))
    crop = Image.fromarray(np.ascontiguousarray(rgb[y : y + ch, x : x + cw]))
    return np.asarray(crop.resize((w, h), Image.BILINEAR))


def fade_envelope(t0: float, n: int, rate: float, duration: float, fin: float, fout: float) -> np.ndarray:
    """`fx.fade_gain` vectorizado para `n` muestras desde `t0` (s del tramo)."""
    t = t0 + np.arange(n, dtype=np.float32) / rate
    k = np.ones(n, np.float32)
    if fin > 0:
        k = np.minimum(k, np.clip(t / fin, 0.0, 1.0))
    if fout > 0:
        k = np.minimum(k, np.clip((duration - t) / fout, 0.0, 1.0))
    return k


_FONTS: dict[tuple[bool, int], object] = {}


def _font(bold: bool, size: int):
    from PIL import ImageFont

    key = (bold, max(8, int(size)))
    cached = _FONTS.get(key)
    if cached is not None:
        return cached
    names = ("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")
    for name in names:
        try:
            font = ImageFont.truetype(name, key[1])
            break
        except OSError:
            continue
    else:
        font = ImageFont.load_default(key[1])
    _FONTS[key] = font
    return font


def text_font(bold: bool, size: int):
    """Fuente pública para medir el texto con la misma que usa el preview."""
    return _font(bold, size)


def _draw_texts(project: Project, t: float, rgb: np.ndarray) -> np.ndarray:
    """Dibuja los textos activos sobre el frame RGB del preview (misma posición que drawtext)."""
    active = [overlay for overlay in project.texts if overlay.start <= t < overlay.end]
    if not active or project.height <= 0:
        return rgb
    from PIL import Image, ImageColor, ImageDraw

    base = Image.fromarray(rgb).convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    scale = base.height / project.height
    for overlay in active:
        size = max(8, int(round(overlay.size * scale)))
        font = _font(overlay.bold, size)
        x, y, opacity = tl.text_values(overlay, t)
        if opacity <= 1e-4:
            continue
        cx, cy = x * base.width, y * base.height
        red, green, blue = ImageColor.getrgb(overlay.color)
        alpha = max(0, min(255, int(round(opacity * 255))))
        # Mismo contorno que `drawtext` (borderw=2) para que el preview no mienta: antes se
        # dibujaba una sombra desplazada que el export no tenía. La capa RGBA permite animar
        # la opacidad sin alterar el resto del frame.
        border = max(1, int(round(2 * scale)))
        draw.text((cx, cy), overlay.text, font=font, fill=(red, green, blue, alpha), anchor="mm",
                  stroke_width=border, stroke_fill=(0, 0, 0, alpha))
    return np.asarray(Image.alpha_composite(base, layer).convert("RGB"))


def video_element_image(
    project: Project,
    element,  # noqa: ANN001
    t: float,
    size: tuple[int, int],
    pool: DecoderPool,
    slot: int = 0,
):
    """Frame RGBA de un video superpuesto, encajado, escalado y rotado como su caja.

    Es la única implementación de la imagen de un PiP: la usa el preview y también el bake del
    export (`services.element_animation`) para que ambos vean exactamente lo mismo.
    """
    from PIL import Image

    element = el.effective(element, t)
    asset = project.asset(element.asset_id or "")
    layout = el.layout(project, element, size)
    draw_w = max(1, int(layout.draw_width))
    draw_h = max(1, int(layout.draw_height))
    if element.fit == "fill":
        want_w, want_h = max(1, int(layout.scale_width)), max(1, int(layout.scale_height))
    else:
        want_w, want_h = draw_w, draw_h
    rotation = tx.normalize_rotation(asset.rotation)
    raw_w, raw_h = (want_h, want_w) if rotation in (90, 270) else (want_w, want_h)
    source = pool.get(media.preview_path(asset), slot)
    if source is None:
        return None
    try:
        rgb = source.frame_at(element.in_ + max(0.0, t - element.start), (raw_w, raw_h))
    except Exception:
        return None
    if rgb is None:
        return None
    rgb = rotate_rgb(rgb, rotation, clockwise=False)
    image = Image.fromarray(np.ascontiguousarray(rgb)).convert("RGBA")
    if element.fit == "fill":
        left = max(0, (image.width - draw_w) // 2)
        top = max(0, (image.height - draw_h) // 2)
        image = image.crop((left, top, left + draw_w, top + draw_h))
    elif image.size != (draw_w, draw_h):
        image = image.resize((draw_w, draw_h), Image.Resampling.LANCZOS)
    image = overlays.rotate_rgba(image, element.rotation)
    if element.opacity < 0.999999:
        alpha = image.getchannel("A").point(lambda value: int(round(value * element.opacity)))
        image.putalpha(alpha)
    return image


class FrameRenderer:
    """Compone el frame del preview para un instante global del proyecto."""

    def __init__(self, max_height: int = PREVIEW_HEIGHT) -> None:
        self.max_height = max_height
        self.pool = DecoderPool()
        self.element_pool = DecoderPool(capacity=6)
        self.last_transition: str | None = None
        self._element_key = None
        self._element_sources: dict[str, str] = {}

    def close(self) -> None:
        self.pool.close()
        self.element_pool.close()

    def _sources(self, project: Project, size: tuple[int, int]) -> dict[str, str]:
        """PNGs de los elementos del preview, cacheados por proyecto+tamaño (no por frame)."""
        if not project.elements:
            return {}
        key = (
            project.elements,
            tuple((asset.id, asset.path) for asset in project.assets),
            size,
        )
        if key == self._element_key:
            return self._element_sources
        try:
            sources = element_assets.materialize(project, size)
        except Exception:  # best-effort: el preview sigue sin el elemento (el export sí falla)
            sources = {}
        self._element_key = key
        self._element_sources = sources
        return sources

    def _video_element_image(
        self, project: Project, element, t: float, size: tuple[int, int], slot: int,
    ):  # noqa: ANN001, ANN202
        """Frame RGBA de un video superpuesto (delegado a `video_element_image`)."""
        return video_element_image(project, element, t, size, self.element_pool, slot)

    def _video_element_images(
        self, project: Project, t: float, size: tuple[int, int],
    ) -> dict[str, object]:
        active = [element for element in el.active(project, t) if element.kind == "video"]
        if not active:
            return {}
        images: dict[str, object] = {}
        for index, element in enumerate(active):
            image = self._video_element_image(project, element, t, size, index % 4)
            if image is not None:
                images[element.id] = image
        return images

    def _clip_frame(
        self, project: Project, clip: Clip, t_source: float, size: tuple[int, int], slot: int,
        t_local: float | None = None,
    ) -> np.ndarray:
        asset: MediaAsset | None
        try:
            asset = project.asset(clip.asset_id)
        except Exception:
            asset = None
        canvas = np.zeros((size[1], size[0], 3), np.uint8)
        if asset is None:
            return canvas
        source = self.pool.get(media.preview_path(asset), slot)
        if source is None:
            return canvas

        if not tx.needs_custom_render(project, clip):
            # Camino histórico de F3/F4 (sin copias adicionales): fit + negro.
            fit_w, fit_h = _fit(asset.width or size[0], asset.height or size[1], size[0], size[1])
            try:
                rgb = source.frame_at(t_source, (fit_w, fit_h))
            except Exception:
                return canvas
            if rgb is None:
                return canvas
            # mismo orden que el graph: zoom (crop) → eq → letterbox → fundido
            factor, cx, cy = fx.zoom(clip)
            if factor > 1.0 + 1e-6:
                rgb = apply_zoom(rgb, factor, cx, cy)
            eq = fx.eq(clip)
            if eq is not None:
                rgb = apply_eq(rgb, eq)
            x = (size[0] - rgb.shape[1]) // 2
            y = (size[1] - rgb.shape[0]) // 2
            canvas[y : y + rgb.shape[0], x : x + rgb.shape[1]] = rgb
            if t_local is not None:
                fin, fout = fx.fades(clip, clip.duration)
                k = fx.fade_gain(t_local, clip.duration, fin, fout)
                if k < 1.0:
                    canvas = (canvas * k).astype(np.uint8)
            return canvas

        lay = tx.layout(project, clip, size)
        canvas = np.empty((size[1], size[0], 3), np.uint8)
        canvas[:] = tx.hex_to_rgb(project.background)
        left, top, right, bottom = lay.crop
        # Decodificar justo lo necesario para que el recorte llene `out_width × out_height`
        # (sin superar la resolución nativa del asset).
        want_w = max(2, int(round(lay.out_width / max(1e-6, right - left))))
        want_h = max(2, int(round(lay.out_height / max(1e-6, bottom - top))))
        shrink = min(1.0, lay.source_width / want_w, lay.source_height / want_h)
        dec_w = max(2, int(round(want_w * shrink)))
        dec_h = max(2, int(round(want_h * shrink)))
        raw_w, raw_h = (dec_h, dec_w) if lay.asset_rotation in (90, 270) else (dec_w, dec_h)
        try:
            rgb = source.frame_at(t_source, (raw_w, raw_h))
        except Exception:
            rgb = None
        if rgb is None:
            return canvas
        rgb = rotate_rgb(rgb, lay.asset_rotation, clockwise=False)
        rgb = rotate_rgb(rgb, lay.rotation, clockwise=True)
        if lay.flip_h:
            rgb = np.flip(rgb, axis=1)
        if lay.flip_v:
            rgb = np.flip(rgb, axis=0)
        height, width = rgb.shape[:2]
        x0 = min(width - 1, max(0, int(round(width * left))))
        y0 = min(height - 1, max(0, int(round(height * top))))
        x1 = min(width, max(x0 + 1, int(round(width * right))))
        y1 = min(height, max(y0 + 1, int(round(height * bottom))))
        rgb = rgb[y0:y1, x0:x1]
        factor, cx, cy = fx.zoom(clip)
        if factor > 1.0 + 1e-6:
            rgb = apply_zoom(rgb, factor, cx, cy)
        if rgb.shape[1] != lay.out_width or rgb.shape[0] != lay.out_height:
            from PIL import Image

            image = Image.fromarray(np.ascontiguousarray(rgb))
            rgb = np.asarray(image.resize((lay.out_width, lay.out_height), Image.BILINEAR))
        # Composición con recorte contra los bordes del lienzo (offsets negativos incluidos).
        dst_x, dst_y = max(0, lay.x), max(0, lay.y)
        src_x, src_y = max(0, -lay.x), max(0, -lay.y)
        copy_w = min(lay.out_width - src_x, size[0] - dst_x)
        copy_h = min(lay.out_height - src_y, size[1] - dst_y)
        if copy_w > 0 and copy_h > 0:
            canvas[dst_y : dst_y + copy_h, dst_x : dst_x + copy_w] = rgb[
                src_y : src_y + copy_h, src_x : src_x + copy_w
            ]
        eq = fx.eq(clip)
        if eq is not None:
            canvas = apply_eq(canvas, eq)
        if t_local is not None:
            fin, fout = fx.fades(clip, clip.duration)
            k = fx.fade_gain(t_local, clip.duration, fin, fout)
            if k < 1.0:
                canvas = (canvas * k).astype(np.uint8)
        return canvas

    def render(self, project: Project, t: float) -> np.ndarray | None:
        size = preview_size(project, self.max_height)
        pos = tl.locate(project, t)
        if pos is None:
            self.last_transition = None
            return None
        frame = self._clip_frame(project, pos.clip, pos.t_source, size, pos.index % 2, pos.t_local)
        tr = tl.transition_at(project, t)
        if tr is not None:
            incoming = self._clip_frame(
                project, tr.incoming.clip, tr.incoming.t_source, size, tr.incoming.index % 2,
                tr.incoming.t_local,
            )
            frame = blend(tr.transition.kind, frame, incoming, tr.progress)
            self.last_transition = tr.transition.kind
        else:
            self.last_transition = None
        # Orden compartido con el export: V1 → V2/V3 → efectos globales → capa «Elementos»
        # (imágenes, figuras y PiP suelto) → textos.
        sources = self._sources(project, size)
        video_elements = self._video_element_images(project, t, size)
        video_layer = tuple(e for e in project.elements if e.video_track > 0)
        generic_layer = tuple(e for e in project.elements if e.video_track <= 0)
        if video_layer:
            frame = overlays.apply_frame(
                replace(project, elements=video_layer), t, frame, sources=sources,
                video_images=video_elements,
            )
        frame = global_effects.apply_frame(project, frame)
        if generic_layer:
            frame = overlays.apply_frame(
                replace(project, elements=generic_layer), t, frame, sources=sources,
                video_images=video_elements,
            )
        return _draw_texts(project, t, frame)

    def prefetch(self, project: Project, index: int) -> None:
        """Abre y posiciona el decoder del clip `index` en su primer frame (otro hilo)."""
        if not 0 <= index < len(project.video.clips):
            return
        clip = project.video.clips[index]
        self._clip_frame(project, clip, clip.in_, preview_size(project, self.max_height), index % 2)

    def render_jpeg(self, project: Project, t: float) -> bytes:
        frame = self.render(project, t)
        return b"" if frame is None else encode_jpeg(frame)


# -- audio del preview (best-effort) ---------------------------------------------------


@dataclass(frozen=True)
class AudioSegment:
    clip: Clip
    start: float  # global
    end: float  # global
    local: float  # t_local del clip en `start`


def audio_segments(project: Project) -> list[AudioSegment]:
    """Tramos de audio del timeline: en un solape suena el saliente hasta su final; el
    entrante empieza en su t_local = duración de la transición (audio y video duran igual)."""
    track = project.video
    starts = tl.clip_starts(track)
    segments: list[AudioSegment] = []
    previous_end = 0.0
    for clip, start in zip(track.clips, starts):
        end = start + clip.duration
        begin = max(start, previous_end)
        if end > begin:
            segments.append(AudioSegment(clip, begin, end, begin - start))
        previous_end = end
    return segments


class AudioPreview:
    """Ring-buffer alimentado por un hilo de decode y consumido por un `OutputStream`."""

    RATE = 48_000
    AHEAD_S = 0.6  # audio decodificado por delante del dispositivo

    def __init__(self) -> None:
        self._stream = None
        self._feeder: threading.Thread | None = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._chunks: deque[np.ndarray] = deque()
        self._buffered = 0
        self.underruns = 0
        self.error: str | None = None
        self.muted = False
        self._control = threading.Lock()  # start/stop pueden llegar desde varios hilos
        self._render_lock = threading.Lock()  # un solo FFmpeg de mezcla a la vez

    def start(self, project: Project, t: float) -> None:
        with self._control:
            self._stop_locked()
            if not self.muted:
                # Con pistas extra o audio de un PiP la mezcla exacta del export se renderiza a
                # un WAV cacheado; sin ellos se decodifica el video pista a pista (más ligero).
                mixed = (
                    bool(project.audio) or graph_mod.has_element_audio(project)
                    or graph_mod.has_audio_envelope(project)
                )
                self._start_locked(project, t, mixed=mixed)

    def stop(self) -> None:
        with self._control:
            self._stop_locked()

    def _start_locked(self, project: Project, t: float, *, mixed: bool = False) -> None:
        try:
            import sounddevice as sd

            self._cancel = threading.Event()
            self._stream = sd.OutputStream(
                samplerate=self.RATE, channels=2, dtype="float32", latency=0.08, callback=self._callback
            )
            latency = float(self._stream.latency or 0.08)
        except Exception as exc:  # sin dispositivo de salida: preview mudo
            self.error = str(exc)
            self._stream = None
            return
        cancel = self._cancel
        target = self._feed_mixed if mixed else self._feed
        self._feeder = threading.Thread(
            target=target, args=(project, t + latency, cancel), daemon=True, name="preview-audio"
        )
        self._feeder.start()
        try:
            self._stream.start()
        except Exception as exc:
            self.error = str(exc)
            self._stop_locked()

    def _stop_locked(self) -> None:
        self._cancel.set()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass
        if self._feeder is not None:
            self._feeder.join(timeout=1.0)
            self._feeder = None
        with self._lock:
            self._chunks.clear()
            self._buffered = 0

    def _mix_file(self, project: Project) -> Path:
        payload = json.dumps(to_dict(project), sort_keys=True, ensure_ascii=False)
        key = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]
        folder = paths.cache_dir() / "preview-audio"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{key}.wav"

    def _render_mix(self, project: Project, target: Path) -> None:
        with self._render_lock:
            if target.is_file() and target.stat().st_size > 0:
                return  # otro hilo (o un play anterior) ya la generó
            tmp = target.with_name(target.name + ".tmp.wav")
            try:
                args = graph_mod.build_audio(project, tmp, exe=ffmpeg_mod.ffmpeg_exe())
                result = ffmpeg_mod.run(args, timeout=600)
                if result.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
                    raise RuntimeError(result.stderr[-400:] or "FFmpeg no generó el audio")
                os.replace(tmp, target)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    def _feed_mixed(self, project: Project, t: float, cancel: threading.Event) -> None:
        """Preview con música/ducking: mezcla exacta del export en un WAV cacheado (B1)."""
        try:
            target = self._mix_file(project)
            if not target.is_file():
                self._render_mix(project, target)
            if cancel.is_set():
                return
            with wave.open(str(target), "rb") as wav:
                rate = wav.getframerate()
                channels = wav.getnchannels()
                start = max(0, int(round(t * rate)))
                wav.setpos(min(wav.getnframes(), start))
                block = max(1, int(0.1 * rate))
                while not cancel.is_set():
                    raw = wav.readframes(block)
                    if not raw:
                        break
                    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    if channels == 1:
                        data = np.repeat(data[:, None], 2, axis=1)
                    else:
                        data = data.reshape(-1, channels)[:, :2]
                    if not self._push(np.ascontiguousarray(data, dtype=np.float32), cancel):
                        return
        except Exception as exc:  # best-effort: sin audio de preview, la UI sigue
            self.error = str(exc)

    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        filled = 0
        with self._lock:
            while filled < frames and self._chunks:
                chunk = self._chunks[0]
                take = min(frames - filled, len(chunk))
                outdata[filled : filled + take] = chunk[:take]
                filled += take
                if take == len(chunk):
                    self._chunks.popleft()
                else:
                    self._chunks[0] = chunk[take:]
            self._buffered -= filled
        if filled < frames:
            outdata[filled:] = 0
            if not self._cancel.is_set():
                self.underruns += 1

    def _push(self, samples: np.ndarray, cancel: threading.Event) -> bool:
        """Encola (bloquea si hay suficiente por delante). `False` si se canceló."""
        while not cancel.is_set():
            with self._lock:
                if self._buffered < self.AHEAD_S * self.RATE:
                    self._chunks.append(samples)
                    self._buffered += len(samples)
                    return True
            cancel.wait(0.02)
        return False

    def _feed(self, project: Project, t: float, cancel: threading.Event) -> None:
        try:
            for segment in audio_segments(project):
                if cancel.is_set() or segment.end <= t:
                    continue
                begin = max(t, segment.start)
                local = segment.local + (begin - segment.start)
                if not self._feed_segment(project, segment, local, segment.end - begin, cancel):
                    return
        except Exception as exc:  # best-effort: un fallo deja el preview mudo, nunca la UI
            self.error = str(exc)

    def _silence(self, seconds: float, cancel: threading.Event) -> bool:
        block = int(0.1 * self.RATE)
        remaining = int(seconds * self.RATE)
        while remaining > 0:
            n = min(block, remaining)
            if not self._push(np.zeros((n, 2), np.float32), cancel):
                return False
            remaining -= n
        return True

    def _feed_segment(
        self, project: Project, segment: AudioSegment, local: float, seconds: float, cancel: threading.Event
    ) -> bool:
        import av

        clip = segment.clip
        asset = project.asset(clip.asset_id)
        gain = fx.gain(clip) * (video_audio_gain(project) if clip.audio_enabled else 0.0)
        if not asset.has_audio or gain <= 0:
            return self._silence(seconds, cancel)
        # fundidos sobre el tramo audible del clip (igual que `core.graph`)
        span = clip.duration - segment.local
        fin, fout = fx.audio_fades(clip, span)
        offset = local - segment.local  # posición dentro del tramo al empezar
        try:
            container = av.open(asset.path)
        except Exception:
            return self._silence(seconds, cancel)
        try:
            stream = container.streams.audio[0]
            src_start = clip.in_ + local * clip.speed
            src_len = seconds * clip.speed
            container.seek(max(0, int(src_start / stream.time_base)), stream=stream, backward=True)
            resampler = av.AudioResampler(format="flt", layout="stereo", rate=self.RATE)
            want = int(round(seconds * self.RATE))
            sent = 0
            src_cursor: float | None = None
            for frame in container.decode(stream):
                if cancel.is_set():
                    return False
                for out in resampler.resample(frame):
                    data = out.to_ndarray().reshape(-1, 2).astype(np.float32, copy=False)
                    t0 = float(out.pts * out.time_base) if out.pts is not None else (src_cursor or src_start)
                    src_cursor = t0 + len(data) / self.RATE
                    if src_cursor <= src_start:
                        continue
                    if t0 < src_start:  # recortar hasta el instante pedido (seek a keyframe)
                        data = data[int((src_start - t0) * self.RATE):]
                    if clip.speed != 1.0 and len(data) > 1:  # velocidad: remuestreo simple
                        n = max(1, int(len(data) / clip.speed))
                        idx = np.linspace(0, len(data) - 1, n)
                        data = np.stack(
                            [np.interp(idx, np.arange(len(data)), data[:, ch]) for ch in (0, 1)], axis=1
                        ).astype(np.float32)
                    data = data[: max(0, want - sent)]
                    if len(data) and (gain != 1.0 or fin > 0 or fout > 0):
                        env = fade_envelope(offset + sent / self.RATE, len(data), self.RATE, span, fin, fout)
                        data = data * (env * gain)[:, None]
                    if len(data) and not self._push(np.ascontiguousarray(data, dtype=np.float32), cancel):
                        return False
                    sent += len(data)
                if sent >= want or (src_cursor is not None and src_cursor >= src_start + src_len + 0.05):
                    break
            if sent < want:
                return self._silence((want - sent) / self.RATE, cancel)
            return True
        except Exception:
            return self._silence(seconds, cancel)
        finally:
            container.close()


# -- reproductor -----------------------------------------------------------------------


class Player:
    """Hilo de preview: reproducción a `TICK_HZ` y frames sueltos al hacer scrub."""

    def __init__(
        self,
        on_frame: Callable[[bytes], None],
        on_time: Callable[[float], None],
        on_state: Callable[[bool], None],
        *,
        tick_hz: float = TICK_HZ,
        renderer: FrameRenderer | None = None,
        audio: AudioPreview | None = None,
    ) -> None:
        self._on_frame = on_frame
        self._on_time = on_time
        self._on_state = on_state
        self._tick = 1.0 / tick_hz
        self.renderer = renderer or FrameRenderer()
        self.audio = audio
        self._cond = threading.Condition()
        self._project = Project()
        self._t = 0.0
        self._playing = False
        self._dirty = False
        self._t0 = 0.0
        self._wall0 = 0.0
        self._rate = 1.0  # velocidad del reloj (J/K/L); el audio solo suena a 1×
        self._loop: tuple[float, float] | None = None  # rango que se repite (None = sin loop)
        self._stop = False
        self._thread: threading.Thread | None = None
        self.rendered = 0
        self.render_ms: deque[float] = deque(maxlen=240)
        self.errors = 0
        self._prefetched: set[tuple[int, str]] = set()

    # -- API (cualquier hilo) --

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="playback")
            self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if self.audio is not None:
            self.audio.stop()
        self.renderer.close()

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def time(self) -> float:
        with self._cond:
            if self._playing:
                return self._clock_locked()
            return self._t

    @property
    def rate(self) -> float:
        return self._rate

    @property
    def loop(self) -> tuple[float, float] | None:
        return self._loop

    def _clock_locked(self) -> float:
        return self._t0 + (time.perf_counter() - self._wall0) * self._rate

    def set_loop(self, loop: tuple[float, float] | None) -> None:
        """Rango `(inicio, final)` que se repite al reproducir (`None` desactiva el loop)."""
        with self._cond:
            if loop is not None and not loop[1] > loop[0]:
                loop = None
            self._loop = None if loop is None else (float(loop[0]), float(loop[1]))
            self._cond.notify_all()

    def set_rate(self, rate: float) -> None:
        """Cambia la velocidad sin saltar (re-ancla el reloj en el instante actual)."""
        with self._cond:
            rate = float(rate)
            if rate == 0 or rate == self._rate:
                return
            if self._playing:
                self._t0 = self._clock_locked()
                self._wall0 = time.perf_counter()
            self._rate = rate
            if self._playing:
                self._restart_audio_locked()
            self._cond.notify_all()

    def set_project(self, project: Project) -> None:
        with self._cond:
            if project == self._project:
                return
            self._project = project
            self.renderer.pool.forget_failures()
            if self._playing:
                self._restart_audio_locked()
            else:
                self._dirty = True
            self._cond.notify_all()

    def seek(self, t: float) -> None:
        with self._cond:
            if self._playing:
                self._t0 = t
                self._wall0 = time.perf_counter()
                self._restart_audio_locked()
            else:
                self._t = t
                self._dirty = True
            self._cond.notify_all()

    def play(self, t: float | None = None, rate: float | None = None) -> None:
        with self._cond:
            if self._playing:
                return
            total = tl.total_duration(self._project)
            if total <= 0:
                return
            if rate:
                self._rate = float(rate)
            start = self._t if t is None else t
            frame = 1.0 / max(1, self._project.fps)
            if self._loop is not None:
                begin, end = self._loop
                if not begin - 1e-6 <= start < end - frame / 2:
                    start = begin if self._rate > 0 else max(begin, end - frame)
            elif self._rate > 0 and start >= total - 1e-3:
                start = 0.0  # al final: vuelve a empezar
            elif self._rate < 0 and start <= 1e-3:
                start = max(0.0, total - frame)  # hacia atrás desde el principio: desde el final
            self._t0 = start
            self._wall0 = time.perf_counter()
            self._playing = True
            self._prefetched.clear()
            self._restart_audio_locked()
            self._cond.notify_all()
        self._on_state(True)

    def pause(self) -> None:
        with self._cond:
            if not self._playing:
                return
            self._t = max(0.0, self._clock_locked())
            self._playing = False
            self._rate = 1.0  # la siguiente reproducción vuelve a 1× (K)
            self._dirty = True
            self._cond.notify_all()
        if self.audio is not None:
            self.audio.stop()
        self._on_state(False)

    def _restart_audio_locked(self) -> None:
        if self.audio is None or not self._playing:
            return
        if self._rate != 1.0:  # a otra velocidad (o hacia atrás) el preview va mudo
            threading.Thread(target=self.audio.stop, daemon=True, name="audio-stop").start()
            return
        project, t = self._project, self._t0
        threading.Thread(target=self.audio.start, args=(project, t), daemon=True, name="audio-restart").start()

    # -- hilo --

    def _render(self, project: Project, t: float) -> None:
        started = time.perf_counter()
        try:
            data = self.renderer.render_jpeg(project, t)
        except Exception:
            self.errors += 1
            return
        self.render_ms.append((time.perf_counter() - started) * 1000)
        self.rendered += 1
        self._on_frame(data)

    PREFETCH_S = 0.7  # se precarga el clip siguiente cuando falta esto para llegar a él

    def _maybe_prefetch(self, project: Project, t: float) -> None:
        starts = tl.clip_starts(project.video)
        for index, start in enumerate(starts):
            if t < start <= t + self.PREFETCH_S:
                key = (index, project.video.clips[index].id)
                if key not in self._prefetched:
                    self._prefetched.add(key)
                    threading.Thread(
                        target=self.renderer.prefetch, args=(project, index), daemon=True, name="prefetch"
                    ).start()
                break

    def _run(self) -> None:
        next_tick = 0.0
        while True:
            with self._cond:
                while not self._stop and not self._playing and not self._dirty:
                    self._cond.wait(0.5)
                if self._stop:
                    return
                project = self._project
                if self._playing:
                    t = self._clock_locked()
                    total = tl.total_duration(project)
                    frame = 1.0 / max(1, project.fps)
                    begin, end = self._loop if self._loop is not None else (0.0, total)
                    end = min(end, total)
                    wrapped = self._rate > 0 and t >= end or self._rate < 0 and t < begin
                    finished = False
                    if wrapped and self._loop is not None and end > begin:
                        # Loop: vuelve al otro extremo del rango y re-ancla el reloj (y el audio).
                        t = begin if self._rate > 0 else max(begin, end - frame)
                        self._t0, self._wall0 = t, time.perf_counter()
                        self._restart_audio_locked()
                    elif wrapped:
                        finished = True
                        t = max(0.0, total - frame) if self._rate > 0 else 0.0
                        self._t = t
                        self._playing = False
                        self._rate = 1.0
                        self._dirty = False
                    mode = "end" if finished else "play"
                else:
                    t = self._t
                    self._dirty = False
                    mode = "still"
            self._render(project, t)
            if mode != "still":
                self._on_time(t)
            if mode == "end":
                if self.audio is not None:
                    self.audio.stop()
                self._on_state(False)
            elif mode == "play":
                self._maybe_prefetch(project, t)
                # Siguiente tick en una rejilla fija; si vamos tarde se descartan ticks (nunca se
                # acumula retraso). `time.sleep` es de alta resolución en Windows; las esperas
                # de `Condition` van a saltos de ~15.6 ms y bajaban el preview a ~21 fps.
                now = time.perf_counter()
                next_tick = next_tick + self._tick if next_tick else now + self._tick
                next_tick = min(max(next_tick, now), now + self._tick)
                time.sleep(next_tick - now)
            else:
                next_tick = 0.0
