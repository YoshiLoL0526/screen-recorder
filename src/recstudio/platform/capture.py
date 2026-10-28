"""Backends de captura con una interfaz común.

Medido en F0 (`docs/08-f0-resultados.md`): WGC ~98 fps @ 18.6 % CPU (default), DXGI-pull ~96 fps,
`dxcam` ~135 fps (modo altas fps, sin cursor), `mss` ~48 fps (fallback/screenshots).

Gotchas grabados a fuego:
- WGC es *damage-driven* sin `minimum_update_interval`: por defecto se queda en 4.5 fps con la
  pantalla estática. Siempre se abre con un intervalo mínimo (mitad del frame time objetivo).
- `monitor_index` es 1-based en `windows-capture` y `mss`.
- WGC entrega BGR de 3 canales (`convert_to_bgr()`); el resto, BGRA de 4.
- Los buffers de WGC se reciclan: hay que copiar dentro del callback.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol

import numpy as np

from ..core.config import SourceConfig
from ..core.errors import CaptureError


def _windows_capture():
    """Importa `windows_capture` con el `cv2` que haya (real o el sustituto del bundle A5)."""
    from . import cv2_shim

    cv2_shim.install()
    import windows_capture

    return windows_capture


class LatestFrame:
    """Slot de un solo frame: el productor sobrescribe, el consumidor se queda con el último.

    Si el productor escribe dos veces antes de que el consumidor lea, el frame intermedio se
    pierde (se cuenta en `skipped`): es exactamente lo que queremos para tiempo real.
    """

    __slots__ = ("_frame", "_lock", "_new", "skipped")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._new = False
        self.skipped = 0

    def put(self, frame: np.ndarray) -> None:
        with self._lock:
            if self._new:
                self.skipped += 1
            self._frame = frame
            self._new = True

    def take(self) -> np.ndarray | None:
        with self._lock:
            if not self._new:
                return None
            self._new = False
            return self._frame

    def wait(self, timeout: float) -> np.ndarray | None:
        deadline = time.perf_counter() + timeout
        while True:
            frame = self.take()
            if frame is not None:
                return frame
            if time.perf_counter() >= deadline:
                return None
            time.sleep(0.002)


class CaptureBackend(Protocol):
    """Contrato mínimo que consume `services.recording`."""

    name: str
    pixel_format: str  # "bgra" | "bgr24"
    lost_source: bool

    def start(self, fps: int) -> None: ...

    def grab(self) -> np.ndarray | None: ...

    def stop(self) -> None: ...


# ---------------------------------------------------------------------------

class MssBackend:
    """GDI/BitBlt: lento (48 fps) pero disponible en cualquier plataforma."""

    name = "mss"
    pixel_format = "bgra"

    def __init__(self, source: SourceConfig) -> None:
        self.source = source
        self.lost_source = False
        self._sct = None
        self._bbox: dict[str, int] = {}

    def start(self, fps: int) -> None:
        import mss

        self._sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
        monitors = self._sct.monitors
        idx = self.source.monitor_index
        if not 1 <= idx < len(monitors):
            raise CaptureError(f"no existe el monitor {idx}")
        self._bbox = monitors[idx]

    def grab(self) -> np.ndarray | None:
        try:
            shot = self._sct.grab(self._bbox)
        except Exception as exc:
            self.lost_source = True
            raise CaptureError(f"mss: {exc}") from exc
        return np.frombuffer(shot.bgra, np.uint8).reshape(shot.height, shot.width, 4)

    def stop(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None


def match_dxgi_output(
    outputs: list[list[tuple[int, int]]], left: int, top: int
) -> tuple[int, int] | None:
    """(device_idx, output_idx) de la salida DXGI cuya esquina es (left, top), o None.

    `outputs[d][o]` = esquina superior izquierda de la salida `o` del adaptador `d`.
    """
    for device_idx, corners in enumerate(outputs):
        for output_idx, corner in enumerate(corners):
            if corner == (left, top):
                return device_idx, output_idx
    return None


class DxcamBackend:
    """DXGI con ring buffer paced (modo altas fps; sin cursor)."""

    name = "dxcam"
    pixel_format = "bgra"

    def __init__(self, source: SourceConfig) -> None:
        self.source = source
        self.lost_source = False
        self._cam = None

    def start(self, fps: int) -> None:
        from . import cv2_shim

        cv2_shim.install()
        if cv2_shim.is_stub():
            raise CaptureError(
                "este build no incluye OpenCV: el backend dxcam no está disponible "
                "(usa WGC, DXGI o mss)"
            )
        import dxcam

        from .screens import list_monitors

        # dxcam numera por adaptador DXGI (y por GPU), no como EnumDisplayMonitors (mss/WGC):
        # se busca la salida cuyo escritorio empieza donde el monitor elegido.
        idx = self.source.monitor_index
        monitor = next((m for m in list_monitors() if m.index == idx), None)
        if monitor is None:
            raise CaptureError(f"no existe el monitor {idx}")
        factory = getattr(dxcam, "__factory", None) or dxcam.DXFactory()
        coords = [
            [(o.desc.DesktopCoordinates.left, o.desc.DesktopCoordinates.top) for o in outputs]
            for outputs in factory.outputs
        ]
        found = match_dxgi_output(coords, monitor.left, monitor.top)
        if found is None:
            raise CaptureError(f"dxcam: ninguna salida DXGI corresponde al monitor {idx}")
        device_idx, output_idx = found
        try:
            self._cam = dxcam.create(
                device_idx=device_idx, output_idx=output_idx, output_color="BGRA",
                processor_backend="numpy",
            )
            self._cam.start(target_fps=max(10, fps), video_mode=True)
        except Exception as exc:
            raise CaptureError(f"dxcam: {exc}") from exc

    def grab(self) -> np.ndarray | None:
        try:
            got = self._cam.get_latest_frame(copy=True)
        except Exception as exc:
            self.lost_source = True
            raise CaptureError(f"dxcam: {exc}") from exc
        if got is None:
            return None
        arr = got[0] if isinstance(got, tuple) else got
        return np.asarray(arr)

    def stop(self) -> None:
        if self._cam is not None:
            try:
                self._cam.stop()
            except Exception:
                pass
            self._cam = None


class DxgiPullBackend:
    """Desktop Duplication (pull) con `windows-capture` 2.x. Sin cursor."""

    name = "dxgi"
    pixel_format = "bgra"

    def __init__(self, source: SourceConfig) -> None:
        self.source = source
        self.lost_source = False
        self._session = None
        self._timeout_ms = 8

    def start(self, fps: int) -> None:
        wc = _windows_capture()

        try:
            self._session = wc.DxgiDuplicationSession(monitor_index=self.source.monitor_index)
        except Exception as exc:
            raise CaptureError(f"DXGI: {exc}") from exc
        self._timeout_ms = max(2, int(500 / max(fps, 10)))

    def grab(self) -> np.ndarray | None:
        try:
            frame = self._session.acquire_frame(timeout_ms=self._timeout_ms)
        except Exception as exc:  # el timeout puede llegar como excepción según la versión
            message = str(exc).lower()
            if "timeout" in message or "no frame" in message:
                return None
            self.lost_source = True
            raise CaptureError(f"DXGI: {exc}") from exc
        if frame is None:
            return None
        color = str(getattr(frame, "color_format", "bgra8")).lower()
        if color not in ("bgra8", "bgra"):
            self.lost_source = True
            raise CaptureError(f"formato de color no soportado: {color} (¿pantalla HDR?)")
        return frame.to_numpy()

    def stop(self) -> None:
        self._session = None


class WgcBackend:
    """Windows.Graphics.Capture por callback: monitor o ventana, con cursor.

    Entrega BGRA de 4 canales en `frame.frame_buffer` (numpy, copia obligatoria: el buffer se
    recicla al volver del callback). Ojo: `convert_to_bgr()` muta el frame y devuelve el propio
    objeto `Frame`, no un array; no hace falta aquí. `on_closed` es obligatorio en la API.
    """

    name = "wgc"
    pixel_format = "bgra"

    def __init__(self, source: SourceConfig) -> None:
        self.source = source
        self.lost_source = False
        self._latest = LatestFrame()
        self._capture = None
        self._control = None

    def start(self, fps: int) -> None:
        wc = _windows_capture()

        kwargs: dict[str, object] = {
            # El cursor nativo solo si se pide: por defecto lo dibuja la app (software) para
            # evitar la latencia variable de la composición de Windows (F0/F1: 12–119 ms).
            "cursor_capture": self.source.cursor == "native",
            "draw_border": False,
            "dirty_region": False,
            # Medido en F0/F1: el intervalo se cuantiza al periodo del compositor (~10 ms en
            # pantalla de 100 Hz); valores ≤8 ms son "sin throttle" (~100 fps) y el de por
            # defecto es damage-driven (4.5–50 fps según actividad). Usamos 1 (sin throttle)
            # y el grabber duplica los frames que falten para mantener el CFR objetivo.
            "minimum_update_interval": 1,
        }
        if self.source.kind == "window":
            kwargs["window_name"] = self.source.window_title
        else:
            kwargs["monitor_index"] = self.source.monitor_index

        capture = wc.WindowsCapture(**kwargs)  # type: ignore[attr-defined]
        latest = self._latest

        @capture.event
        def on_frame_arrived(frame, control) -> None:  # noqa: ANN001
            # El buffer del frame se recicla al volver del callback: copiar aquí.
            buffer = frame.frame_buffer
            arr = buffer if isinstance(buffer, np.ndarray) else np.asarray(buffer)
            if arr.ndim == 1:
                arr = arr.reshape(frame.height, frame.width, -1)
            latest.put(np.array(arr, copy=True, dtype=np.uint8))

        @capture.event
        def on_closed() -> None:  # noqa: ANN001
            self.lost_source = True

        self._capture = capture
        try:
            self._control = capture.start_free_threaded()
        except Exception as exc:
            raise CaptureError(f"WGC: {exc}") from exc

    def grab(self) -> np.ndarray | None:
        return self._latest.take()

    def stop(self) -> None:
        if self._control is not None:
            try:
                self._control.stop()
                self._control.wait()
            except Exception:
                pass
            self._control = None
        self._capture = None

    @property
    def skipped(self) -> int:
        return self._latest.skipped


# ---------------------------------------------------------------------------

_DEFAULT_ORDER = ("wgc", "dxgi", "dxcam", "mss")
_FACTORIES = {
    "wgc": WgcBackend,
    "dxgi": DxgiPullBackend,
    "dxcam": DxcamBackend,
    "mss": MssBackend,
}


def create_backend(source: SourceConfig, *, fps: int = 60, preferred: str | None = None) -> CaptureBackend:
    """Elige backend según F0: WGC por defecto; `dxcam` solo si se pide (o preferencia).

    Cae en cascada por el orden WGC → DXGI → dxcam → mss, acumulando los motivos de fallo.
    """
    source.validate()
    pref = preferred or source.backend

    if source.kind == "window":
        if pref not in (None, "wgc"):
            raise CaptureError(f"la captura de ventanas requiere WGC (no {pref!r})")
        order = ["wgc"]
    elif pref == "dxcam" and source.cursor == "native":
        raise CaptureError("dxcam no captura el cursor del sistema: usa WGC o el cursor propio")
    else:
        order = [pref, *[b for b in _DEFAULT_ORDER if b != pref]] if pref else list(_DEFAULT_ORDER)

    failures: list[str] = []
    for name in order:
        if not name:
            continue
        backend = _FACTORIES[name](source)  # type: ignore[operator]
        try:
            backend.start(fps)
        except CaptureError as exc:
            failures.append(f"{name}: {exc}")
            continue
        except Exception as exc:  # backend no disponible (import, hardware…)
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        return backend

    raise CaptureError("ningún backend de captura disponible", details="; ".join(failures))
