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

from .core.project import Project
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
screen = Signal("record")  # record | library | editor | settings
view_h = Signal(720.0)  # alto útil bajo la cabecera (lo mide el `on_scroll` de la navegación)
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


# -- editor (F3) ---------------------------------------------------------------


@dataclass(frozen=True)
class TimelineDrag:
    """Lo que el timeline dibuja durante un drag (el modelo ya va actualizado en el store)."""

    kind: str  # move | trim_l | trim_r
    clip_id: str
    ghost: float = 0.0  # move: inicio del fantasma (s)
    shift_index: int = -1  # trim_l: desde este clip se dibuja desplazado `shift` s…
    shift: float = 0.0  # …para que el borde siga al ratón hasta soltar (luego se cierra el hueco)


@dataclass(frozen=True)
class RecentProject:
    path: str  # project.json
    name: str
    modified_label: str
    clips: int
    duration_label: str


editor_project = Signal(Project())
project_open = Signal(False)
project_path = Signal("")  # project.json del proyecto abierto
project_dirty = Signal(False)
project_read_only = Signal(False)
undo_label = Signal("")
redo_label = Signal("")
playhead = Signal(0.0)  # segundos (frontera de frame en pausa)
playing = Signal(False)
preview_frame = Signal(b"")  # JPEG del preview del editor
preview_note = Signal("")  # aviso discreto bajo el preview («transición aproximada», …)
preview_audio = Signal(True)  # audio del preview (best-effort)
selection = Signal(None)  # ("clip", id) | ("transition", índice) | None
tl_zoom = Signal(60.0)  # px por segundo
tl_scroll = Signal(0.0)  # desplazamiento horizontal del timeline (px)
tl_view_w = Signal(1000.0)  # ancho visible del timeline (px)
snap_on = Signal(True)
tl_tracks_extra = Signal(0.0)  # alto extra de las pistas (divisor preview/timeline, F5)
inspector_w = Signal(290.0)  # ancho del inspector (divisor preview/inspector, F5)
inspector_open = Signal(True)  # inspector visible (I) — oculto, el preview usa todo el ancho
tl_drag = Signal(None)  # TimelineDrag | None
waveforms = Signal(0)  # versión: sube cada vez que llega un waveform nuevo
offline_assets = Signal(frozenset())  # ids de assets cuyo archivo no existe
importing = Signal(())  # nombres de archivo que se están analizando
import_open = Signal(False)  # modal «Importar»
recent_projects = Signal(())  # RecentProject…
insp_in = Signal("")  # campos de tiempo del inspector (texto editable)
insp_out = Signal("")
insp_speed = Signal(1.0)  # selects del inspector (el controlador los sincroniza y escucha)
insp_transition = Signal("none")
insp_transition_dur = Signal(0.5)
insp_fps = Signal(60)

# -- efectos y export (F4) ---------------------------------------------------------
insp_volume = Signal(1.0)  # efecto `volume` del clip seleccionado
insp_fade_in = Signal(0.0)
insp_fade_out = Signal(0.0)
insp_zoom = Signal(1.0)
insp_brightness = Signal(0.0)
insp_contrast = Signal(1.0)
insp_saturation = Signal(1.0)
insp_track_gain = Signal(1.0)  # volumen del audio original (track de video; 0 = silenciado)

export_open = Signal(False)  # modal «Exportar»
export_name = Signal("")  # nombre del archivo (sin extensión)
export_preset = Signal("rapido")
export_progress = Signal(None)  # services.export.ExportProgress | None
export_blockers = Signal(())  # motivos por los que no se puede «sin recodificar»


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


def projects_dir() -> Path:
    """Proyectos del editor: `<carpeta de grabaciones>/Proyectos/*.rsproj`."""
    return recordings_dir().expanduser() / "Proyectos"


def exports_dir() -> Path:
    """Exportaciones del editor: `<carpeta de grabaciones>/Exportados`."""
    return recordings_dir().expanduser() / "Exportados"
