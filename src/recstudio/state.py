"""Todo el estado reactivo de la app en un solo sitio.

Reglas:
- Solo datos *frozen* (comparables con `==`): `Signal.set` ignora lo que no cambia.
- Los servicios no tocan estos Signals directamente: lo hace `ui/controller.py`.
- El motor (`services/recording.py`) es agnóstico de la UI; el controlador lo puentea.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aurora import Signal

from .platform import paths
from .theme import STUDIO_DARK, theme_by_name


@dataclass(frozen=True)
class Toast:
    id: int
    kind: str  # info | success | warning | danger
    text: str


@dataclass(frozen=True)
class Confirm:
    """Petición de confirmación (modal). `action` la interpreta el controlador."""

    action: str
    title: str
    message: str
    target: str = ""


# -- navegación y aspecto -----------------------------------------------------
screen = Signal("record")  # record | library | settings
theme_name = Signal("dark")
theme = Signal(STUDIO_DARK)
toasts = Signal(())
confirm = Signal(None)

# -- grabación ----------------------------------------------------------------
rec_state = Signal("idle")  # idle | starting | recording | paused | stopping | muxing | done | error
rec_elapsed = Signal(0.0)
rec_fps = Signal(0.0)
rec_dup = Signal(0)
rec_skip = Signal(0)
rec_size_mb = Signal(0.0)
rec_disk_gb = Signal(0.0)
rec_level = Signal(0.0)
rec_error = Signal("")
fps_history = Signal(())  # últimos valores para el sparkline

# -- configuración de captura (se persiste en settings.json) -------------------
source_kind = Signal("monitor")  # monitor | window | region
monitor_index = Signal(1)
window_title = Signal("")
region = Signal((0, 0, 1280, 720))
region_x = Signal("0")
region_y = Signal("0")
region_w = Signal("1280")
region_h = Signal("720")
countdown_s = Signal(0)  # segundos de cuenta atrás configurados (0 = sin cuenta atrás)
countdown = Signal(0)  # segundos restantes mientras corre (0 = inactiva)
region_ref = Signal(b"")  # captura de referencia (PNG) del monitor para elegir la región
region_ref_size = Signal((0, 0))  # tamaño en px lógicos con el que se muestra la referencia
region_bounds = Signal((0, 0))  # tamaño del monitor de la referencia (px físicos)
region_editing = Signal(False)
fps_target = Signal(60)
preset = Signal("media")
encoder = Signal("auto")
audio_system = Signal(True)
keepalive = Signal(True)
mic_index = Signal(-1)  # -1 = sin micrófono
out_dir = Signal(str(paths.videos_dir()))

# -- datos del sistema (se cargan una vez al arrancar) -------------------------
monitors = Signal(())
audio_devices = Signal(())
encoders = Signal({})
ffmpeg_version = Signal("")

# -- preview ------------------------------------------------------------------
preview = Signal(b"")  # JPEG en memoria (bytes)

# -- biblioteca ----------------------------------------------------------------
recordings = Signal(())
recoverable = Signal(())
recovering = Signal(())  # nombres de las carpetas que se están recuperando ahora mismo
library_reload = Signal(0)  # contador: cambiarlo fuerza recarga


def toast(kind: str, text: str) -> None:
    """Encola un toast con auto-cierre (4 s; 6 s si lleva acción implícita)."""
    import threading

    current = toasts()
    next_id = (current[-1].id + 1) if current else 1
    item = Toast(next_id, kind, text)
    toasts.set((*current, item))
    timer = threading.Timer(4.0, drop_toast, args=(item.id,))
    timer.daemon = True
    timer.start()


def drop_toast(toast_id: int) -> None:
    toasts.set(tuple(t for t in toasts() if t.id != toast_id))


def set_theme(name: str) -> None:
    theme_name.set(name)
    theme.set(theme_by_name(name))


def set_region(value: tuple[int, int, int, int]) -> None:
    """Fija la región canónica y sincroniza los campos de texto."""
    x, y, w, h = (int(v) for v in value)
    region.set((x, y, w, h))
    region_x.set(str(x))
    region_y.set(str(y))
    region_w.set(str(w))
    region_h.set(str(h))


def recordings_dir() -> Path:
    return Path(out_dir())
