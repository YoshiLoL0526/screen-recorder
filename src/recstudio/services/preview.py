"""Preview en vivo (hilo aparte): frame de la sesión o captura del monitor, a JPEG.

- Con grabación activa: usa el último frame capturado (ya con la región recortada).
- En reposo: captura el monitor elegido con `mss` y recorta la región configurada.
- La UI solo pinta `image(preview())`; aquí se decodifica/escala/comprime.
"""

from __future__ import annotations

import io
import threading
import time
from collections.abc import Callable

import numpy as np


def encode_preview(frame: np.ndarray, max_height: int = 360, quality: int = 80) -> bytes:
    """BGRA/RGBA → JPEG escalado (reduce entero + ajuste fino, como midió F0)."""
    from PIL import Image

    if frame.ndim != 3 or frame.shape[2] < 3:
        raise ValueError(f"frame inesperado: {frame.shape}")
    rgb = np.ascontiguousarray(frame[:, :, [2, 1, 0]]) if frame.shape[2] == 4 else np.ascontiguousarray(frame)
    img = Image.fromarray(rgb)
    if img.height > max_height:
        factor = max(1, img.height // max_height)
        while factor > 1 and img.height // factor < max_height:
            factor -= 1
        if factor > 1:
            img = img.reduce(factor)
        if img.height > max_height:
            width = max(2, int(round(img.width * max_height / img.height)))
            img = img.resize((width - width % 2, max_height), Image.Resampling.BILINEAR)
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=quality, subsampling=2)
    return buffer.getvalue()


class PreviewLoop:
    """Hilo de preview: `frame_getter` (sesión) o `mss` en reposo; entrega JPEG al callback."""

    def __init__(
        self,
        frame_getter: Callable[[], np.ndarray | None],
        geometry_getter: Callable[[], tuple[int, tuple[int, int, int, int] | None]],
        on_frame: Callable[[bytes], None],
        *,
        fps: float = 6.0,
        max_height: int = 360,
    ) -> None:
        self._frame_getter = frame_getter
        self._geometry_getter = geometry_getter
        self._on_frame = on_frame
        self._interval = 1.0 / max(fps, 0.5)
        self._max_height = max_height
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.errors = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="preview", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                frame = self._frame_getter()
                if frame is None:
                    frame = self._grab_idle()
                if frame is not None:
                    self._on_frame(encode_preview(frame, self._max_height))
            except Exception:
                self.errors += 1
                time.sleep(0.2)
            elapsed = time.perf_counter() - started
            self._stop.wait(max(0.0, self._interval - elapsed))

    def _grab_idle(self) -> np.ndarray | None:
        import mss

        monitor_index, region = self._geometry_getter()
        try:
            sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
        except Exception:
            return None
        try:
            monitors = sct.monitors
            if not 1 <= monitor_index < len(monitors):
                monitor_index = 1
            shot = sct.grab(monitors[monitor_index])
            frame = np.frombuffer(shot.bgra, np.uint8).reshape(shot.height, shot.width, 4)
            if region is not None:
                x, y, w, h = region
                fh, fw = frame.shape[:2]
                x = max(0, min(x, fw - 2))
                y = max(0, min(y, fh - 2))
                frame = frame[y : y + max(2, min(h, fh - y)), x : x + max(2, min(w, fw - x))]
            return frame
        except Exception:
            return None
        finally:
            sct.close()
