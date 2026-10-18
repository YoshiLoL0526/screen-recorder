# 02 — Arquitectura

## Principios

1. **UI delgada, núcleo puro.** Toda la lógica de negocio (modelo del proyecto, matemática del
   timeline, construcción de filtros FFmpeg) vive en `core/` y no importa `aurora`, ni PyAV, ni
   nada de captura. Se testea con `pytest` sin ventana.
2. **La UI nunca bloquea.** Ningún handler de aurora toca disco, red o un subprocess. Las tareas
   largas (captura, decode, export) corren en hilos/servicios y reportan por `Signal`.
3. **Un solo sentido de dependencias:**
   `ui → services → core` y `ui → state`, `services → platform → core`.
   `core` no conoce a nadie; `platform` encapsula el sistema operativo.
4. **Todo lo asíncrono se comunica por `Signal`** (es seguro entre hilos) o por `app.spawn()`
   para corrutinas. Nunca se llama a la UI desde otro hilo directamente.
5. **Fallar suave.** Un error de captura o export se convierte en un estado + toast; el hilo de
   UI no muere nunca. Las grabaciones en curso se escriben en formatos recuperables.
6. **Portable por diseño, Windows primero.** Los backends se eligen en `platform/`; `core/` y la
   UI son agnósticos.

## Capas y módulos

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ ui/  (aurora-ui: views, widgets, tema)                                      │
│   state.py (Signals) · screens/ · widgets/ (timeline, preview, rec_button)   │
└───────────────▲──────────────────────────────────────────────▼──────────────┘
        Signals │                                        comandos
┌───────────────┴──────────────────────────────────────────────▼──────────────┐
│ services/  (orquestación, hilos, estado de aplicación)                      │
│   recording.py · playback.py · export.py · media.py · library.py ·         │
│   hotkeys.py · settings.py                                                  │
└───────────────▲──────────────────────────────────────────────▼──────────────┘
        eventos │                                             llamadas
┌───────────────┴──────────────────────────────────────────────▼──────────────┐
│ platform/  (sistema operativo)          core/  (lógica pura, testeable)     │
│   capture.py (wgc/dxcam/mss)              project.py (modelo)               │
│   audio.py (WASAPI loopback/mic)          timeline.py (tiempo↔píxel, snap)  │
│   ffmpeg.py (binario, run, progress)      graph.py (filter_complex builder) │
│   screens.py (monitores, DPI)             store.py (JSON, undo, autosave)   │
│   paths.py (carpetas, bloqueos)           keyframes.py (snap corte rápido)  │
└──────────────────────────────────────────────────────────────────────────────┘
```

Regla práctica: si una función se puede escribir en un test sin tocar el SO, va en `core/`.
Si toca ventanas, procesos o dispositivos, va en `platform/`.

## Estructura de carpetas propuesta

```
screen-recorder/
├─ main.py                    # entrada: crea App y arranca (App(view, ...).run())
├─ pyproject.toml             # deps, extras, [tool.aurora.build]
├─ assets/                    # icono .ico, iconos SVG, fuente opcional
├─ docs/                      # esta documentación
├─ src/recstudio/             # paquete (nombre definitivo por decidir)
│  ├─ __init__.py
│  ├─ app.py                  # composición: App, ventana, navegación raíz
│  ├─ theme.py                # tema propio (tokens + claro/oscuro)
│  ├─ state.py                # TODOS los Signals de la aplicación
│  ├─ strings.py              # textos (es) en un solo sitio
│  ├─ core/
│  │  ├─ project.py           # dataclasses: Project, Track, Clip, Transition, Effect
│  │  ├─ timeline.py          # t↔x, snapping, ripple, split, trim (puro)
│  │  ├─ graph.py             # Project → filter_complex de FFmpeg
│  │  ├─ keyframes.py         # índice de keyframes y snap para corte rápido
│  │  └─ store.py             # JSON, schema_version, undo (snapshots), autosave
│  ├─ platform/
│  │  ├─ capture.py           # CaptureSession + backends
│  │  ├─ audio.py             # AudioRecorder (loopback/mic)
│  │  ├─ ffmpeg.py            # localizar binario, ejecutar, parsear -progress
│  │  ├─ screens.py           # monitores, DPI, captura de referencia
│  │  └─ paths.py             # carpetas del usuario, creación a prueba de bloqueos
│  ├─ services/
│  │  ├─ recording.py         # orquesta captura+audio+encoder, máquina de estados
│  │  ├─ playback.py          # decode → preview, reloj, audio de preview
│  │  ├─ export.py            # orquesta graph → FFmpeg → progreso
│  │  ├─ media.py             # probe, thumbnails, waveforms, cache
│  │  ├─ library.py           # grabaciones: alta, listado, borrado, recuperación
│  │  └─ settings.py          # preferencias (JSON en %APPDATA%)
│  └─ ui/
│     ├─ layout.py            # shell: sidebar + contenido + header
│     ├─ file_dialogs.py      # diálogos nativos de archivo/carpeta (aurora.dialogs)
│     ├─ screens/
│     │  ├─ record.py  biblioteca.py  editor.py  settings.py
│     ├─ widgets/
│     │  ├─ timeline.py       # el canvas del timeline (dibujo + drags)
│     │  ├─ preview.py        # image() + transport
│     │  ├─ rec_button.py     # botón REC con animación
│     │  ├─ stats_bar.py      # sparkline + contadores
│     │  └─ modal.py  toast.py
│     └─ icons.py             # SVG como bytes (image())
└─ tests/
   ├─ test_timeline.py        # matemática y operaciones (puro)
   ├─ test_graph.py           # args de ffmpeg (golden strings)
   ├─ test_store.py           # JSON + undo
   └─ test_ui_smoke.py        # render_png + pilot de pantallas clave
```

`main.py` queda como lanzador de 5 líneas; `aurora dev main.py` funciona desde el primer día
(hot reload conserva los `Signal`).

## Modelo de hilos

| Hilo | Qué corre | Cómo comunica |
|---|---|---|
| **UI (principal, winit/Aurora)** | `view()`, handlers, animaciones. | Es el receptor: lee `Signal`s; nunca se bloquea. |
| **asyncio de `app.spawn`** | Tareas cortas async (preview del editor, temporizadores, secuencias de UI). | `Signal.set()` (thread-safe) y `frame()` para pedir repintado. |
| **capture (1 por sesión)** | Loop de captura → cola → pipe a FFmpeg. | `Signal`s de stats (fps, dropped, bytes) con throttling (≤ 4 Hz). |
| **preview (compartido con capture)** | Toma el último frame capturado y lo convierte a JPEG (~6 fps) para el preview en vivo. | `Signal(frame_bytes)`. |
| **audio (1 por dispositivo)** | Callback de PyAudioWPatch → buffer → escritura WAV incremental. | `Signal(nivel_db)` decimado; errores → evento. |
| **export (1 por job)** | Hilo lector de `ffmpeg -progress` y stderr. | `Signal(progress)`, `Signal(eta)`, `Signal(export_state)`. |

Reglas de concurrencia:

- **`Signal.set()` es el único canal cross-thread.** Ya despierta el bucle de eventos.
- Objetos compartidos vivos (último frame capturado, sesión de captura) se publican en un
  *slot* protegido por `threading.Lock` — patrón `LatestFrame`: el productor sobrescribe,
  el consumidor copia y suelta. Nunca se encola más de un frame "de preview".
- Los servicios exponen máquinas de estado explícitas (`IDLE → STARTING → RECORDING →
  PAUSING → PAUSED → STOPPING → MUXING → DONE/ERROR`) y **validan transiciones** (llamar a
  `stop()` dos veces es no-op, no un crash).

## Flujos end-to-end

### Grabar

```
UI (botón REC / F9)
  └─ recording.start(config)                # config validada por core
       ├─ platform.capture.create(source)   # WGC | DXGI | mss
       ├─ platform.audio.open(mic?, loopback?)  # WAV por dispositivo
       ├─ ffmpeg spawn (rawvideo stdin → .mkv en temp)
       ├─ hilos vivos; Signals: estado, tiempo, fps, tamaño
       └─ (preview hilo: último frame → JPEG → image())
UI (botón stop)
  └─ recording.stop()
       ├─ cerrar pipe (flush encoder) y audio (cerrar WAVs)
       ├─ mux: ffmpeg -i video.mkv -i audio*.wav -c:v copy -c:a aac → biblioteca/archivo.mp4
       ├─ registrar en biblioteca (json sidecar + thumbnail)
       └─ Signal: grabación terminada → toast + navegar a Biblioteca
```

### Editar / previsualizar

```
UI selecciona clip o mueve playhead
  └─ playback.request(t)         # hilo asyncio
       ├─ decoder PyAV: seek al keyframe ≤ t → decode hasta t (si t salta > 0.5 s)
       ├─ escala a 960×540 → JPEG bytes (Pillow) → Signal(frame)
       └─ en modo play: reloj monótono; cada tick pide el frame de t_actual
```
La UI solo dibuja `image(preview_frame())` + el canvas del timeline. El timeline **no** decodifica nada.

### Exportar

```
UI (Exportar…)
  └─ export.start(project, preset)
       ├─ core.graph.build(project) → (args de ffmpeg, duración estimada)
       ├─ subprocess ffmpeg -progress pipe:1
       ├─ hilo lector: parsea out_time → Signals (%, ETA)
       └─ al terminar: verificar con ffprobe (duración, pistas) → toast/DONE
```

## Estado reactivo (`state.py`)

Un solo módulo con los `Signal` de la app, agrupados por área. La UI los lee; los servicios los
escriben. Ejemplos reales que usaremos:

```python
# Navegación
screen = Signal("record")            # "record" | "library" | "editor" | "settings"
theme = Signal(MIDNIGHT)

# Grabación
rec_state = Signal("idle")           # máquina de estados de services/recording
rec_elapsed = Signal(0.0)
rec_stats = Signal({"fps": 0.0, "dropped": 0, "size": 0})
rec_preview = Signal(b"")            # JPEG del último frame (bytes)
sel_source = Signal(SourceConfig())  # dataclass frozen (hashable)
sel_fps = Signal(60)
sel_quality = Signal("high")

# Editor
project = Signal(Project.empty())    # dataclass frozen: set() por snapshot nuevo
selection = Signal(None)             # id de clip seleccionado
playhead = Signal(0.0)               # segundos
playing = Signal(False)
zoom = Signal(60.0)                  # px por segundo en el timeline
preview_frame = Signal(b"")
export_state = Signal("idle")
export_progress = Signal(0.0)
toasts = Signal(())                  # tupla de toasts (inmutable para ==)
```

Convenciones:

- **Los datos complejos se modelan como dataclasses `frozen=True`** (comparables con `==`,
  hashables). Así `Signal.set(nuevo_estado)` solo dispara si de verdad cambió — el mismo
  mecanismo que usa `@component` para no reconstruir.
- Cada pantalla pesada (biblioteca, editor) se anida en `@component`s con `key` estable, y las
  listas largas usan `virtual_list`.
- Los bytes de imágenes (previews) se pasan a `image(src=bytes)`; se reemplazan por identidad
  solo cuando hay frame nuevo.

## Persistencia y archivos

| Qué | Dónde | Formato |
|---|---|---|
| Preferencias | `%APPDATA%/RecStudio/settings.json` | JSON plano |
| Proyecto | `<carpeta>/nombre.rsproj/project.json` | JSON con `schema_version` |
| Medios del proyecto | Referenciados por ruta absoluta (opción de copiar a `media/`) | archivos originales |
| Cache del proyecto | `<proyecto>/.cache/` | thumbnails (JPEG), waveforms (NPZ), keyframes (JSON) |
| Grabaciones | `~/Videos/RecStudio/` (configurable) | `.mp4` + `.json` sidecar + `.jpg` thumbnail |
| Temporales de grabación | `%LOCALAPPDATA%/Temp/recstudio/<sesión>/` | `video.mkv`, `audio_*.wav` |
| Logs | `%LOCALAPPDATA%/RecStudio/logs/app.log` (rotación 5×2 MB) | texto |

Política de autosave: al editar se marca `dirty`; un `Signal` de "tick" (cada 5 s) guarda si
pasaron 30 s desde el último guardado y hay cambios (siempre con escritura atómica: `.tmp` + replace).

## Manejo de errores

- `RecStudioError` (base de dominio) con `code`, `message`, `details`. `platform/` traduce
  excepciones del SO a esta jerarquía.
- **Recuperables** (micrófono ocupado, carpeta sin permiso, encoder no disponible): estado de
  error + toast con acción ("usar libx264", "elegir otra carpeta").
- **Fatales de sesión** (captura cortada, disco lleno): detener limpio, preservar `.mkv`,
  ofrecer recuperación.
- Nada de `except Exception` mudo; el log siempre con `traceback`. La vista nunca muestra un
  stacktrace crudo (excepto en modo dev con `aurora dev`, donde Aurora ya lo hace).

## Tests

| Nivel | Herramienta | Qué cubre |
|---|---|---|
| Unit `core/` | pytest, sin IO | timeline (t↔x, snap, ripple), graph builder (golden), store/undo, keyframes. |
| Integración FFmpeg | pytest + assets de 2 s generados con `lavfi` | render real sin transiciones / con transición, duración de salida ±1 frame, mux A/V, corte rápido. |
| Integración captura | manual/script | grabación de 10 s a 60 fps; verificación de fps y sync con `ffprobe`. |
| UI | `app.pilot()` + `render_png()` | navegación, timeline (drag con `pilot.drag`), modales, toasts; snapshots PNG por pantalla. |

Los snapshots PNG (`render_png`) sirven además como documentación viva de la UI.

## Dependencias (verificadas el 2026-10-16)

| Paquete | Versión actual | Por qué | Notas |
|---|---|---|---|
| `aurora-ui` | 0.5.0 | Toda la UI. | Ya en `pyproject.toml`; con techo `<0.6` (0.x rompe compatibilidad entre menores). |
| `numpy` | 2.5.4 | Frames, waveforms, peaks, conversiones. | Requisito de facto de PyAV/Pillow. |
| `av` (PyAV) | 19.0.1 | Decode, probe, thumbnails, waveforms. | Wheels con FFmpeg embebido para decodificar. |
| `pillow` | 12.3.0 | Conversión frame→JPEG para preview; thumbnails. | Variante JPEG elegida en F0 (S4b). |
| `imageio-ffmpeg` | 0.6.0 | Binario de FFmpeg para encoding/render. | Wheel ~30 MB con FFmpeg 7.1 essentials (GPL, con x264); hook de PyInstaller disponible. |
| `windows-capture` | 2.0.1 | Captura WGC (monitor/ventana/cursor) — **backend por defecto**. | Windows; Rust; `monitor_index` 1-based; usar `minimum_update_interval=1`. |
| `dxcam` | 0.3.0 | Captura DXGI (modo altas fps). | Windows; arrastra `opencv-python` (~43 MB); fijar `processor_backend="numpy"`. |
| `mss` | 10.2.0 | Captura de referencia (región) y fallback portátil. | Cross-platform; usar `mss.MSS()` (la API `mss.mss()` está deprecada). |
| `PyAudioWPatch` | 0.2.12.9 | WASAPI loopback (audio del sistema) + mic. | Windows. |
| `sounddevice` | 0.5.6 | Salida de audio del preview; fallback de entrada. | Cross-platform. |
| `opencv-python` | 5.0.0.93 | **No es dependencia directa**: la arrastra `dxcam`. | Si se puede, evitar usarla en el código (solo el processor de dxcam). |
| `pynput` | 1.8.2 | Hotkeys globales (post-v1). | `keyboard` está sin mantenimiento desde 2020. |
| `psutil` (dev) | 7.2.2 | Medir CPU de procesos en spikes/benchmarks. | Solo desarrollo. |

`pyproject.toml` propuesto (extras por plataforma para que `uv sync` instale solo lo correcto):

```toml
[project]
name = "recstudio"
requires-python = ">=3.12"
dependencies = [
    "aurora-ui>=0.5.0,<0.6.0",
    "numpy>=2.0",
    "av>=16",
    "pillow>=11",
    "imageio-ffmpeg>=0.6",
]

[project.optional-dependencies]
windows = [
    "windows-capture>=2.0.1",
    "dxcam>=0.3.0",
    "PyAudioWPatch>=0.2.12.9",
]

[dependency-groups]
dev = [
    "aurora-ui[build]>=0.5.0,<0.6.0",
    "pytest>=8",
]

[tool.aurora.build]
script = "main.py"
name = "Rec Studio"
icon = "assets/icon.ico"
include = ["assets"]
onefile = false        # onedir: arranca más rápido con numpy/PyAV dentro
console = false
```

## Empaquetado (`aurora build`)

- `onefile = false` (carpeta) por defecto: con numpy + PyAV + binario de FFmpeg, un onefile
  tarda en extraerse en cada arranque; onedir arranca al instante. Un instalador (Inno Setup)
  queda para post-v1.
- PyInstaller ya trae hooks para **`av`**, **`imageio_ffmpeg`** y **`sounddevice`** en
  `hook-contrib` (verificado en el entorno y **ejercitado en F0/S6**: build de 25 s, bundle de
  **345 MB** onedir, exe que importa todo el stack y codifica un MP4 real con el FFmpeg
  incluido). El grueso del tamaño es numpy + PyAV + **`opencv` (arrastrado por `dxcam`)** +
  el binario de FFmpeg (~30 MB); post-v1: probar `--exclude-module cv2`.
- Licencia: los builds GPL de FFmpeg (con libx264) se distribuyen como **binario separado**
  que la app ejecuta por subprocess — no contamina el código de la app (el wheel de
  `imageio-ffmpeg` es un build *essentials* GPL). Se documentará la fuente del binario y su
  licencia en los "Acerca de".
- Verificación post-build: script `tools/check_build.py` (evolución de
  `tools/spikes/s6_packaging.py`) que arranca el exe empaquetado, comprueba `ffmpeg -version`,
  hace un render de 2 s y valida el MP4 (smoke test de CI).

## Decisiones abiertas

- Nombre definitivo del producto y del paquete (`recstudio` es provisional).
- ¿Proyecto = carpeta `.rsproj` o archivo suelto `.rsproj.json`? (la carpeta deja sitio al cache).
- ¿El editor v1 necesita pista de música separada o basta "audio del clip + música"? (ver 04).
