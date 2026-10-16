# 03 — Grabación (motor de captura)

## Objetivo

Convertir lo que se ve y se oye en el PC en un `.mp4` reproducible, sin pérdidas notables, sin
desincronía y sin que la app pierda fluidez. Todo el video pasa por **FFmpeg como proceso
separado** (encoder); Python solo captura y alimenta.

## Pipeline completo

```
                ┌──────────────────────── hilo capture ────────────────────────┐
 monitor/ventana│  backend (WGC/DXGI) ─→ LatestFrame ─→ tick a fps objetivo    │
 /región        │        │                    │             │  (duplica si falta)│
                └────────┼────────────────────┼─────────────┼──────────────────┘
                         │                    │             ▼
                         │                    │      stdin (rawvideo bgra)
                         │                    │        ┌──────────────┐
                         │                    │        │    ffmpeg    │──→ temp/video.mkv
                         │                    │        └──────────────┘
                         │                    ▼
                         │          hilo preview (6 fps): último frame
                         │           → 480p JPEG → Signal(rec_preview) → image()
                         │
                         └── (sin coste de captura extra: reusa el frame)

 hilo audio (por dispositivo):  PyAudioWPatch callback → writer WAV 48 k/2 ch/16 bit
                                                      → Signal(nivel) para el vúmetro

 stop →  mux: ffmpeg -i video.mkv -i audio.wav -c:v copy -c:a aac 192k → ~/Videos/RecStudio/xxx.mp4
```

## Backends de captura (`platform/capture.py`)

Una interfaz única y tres implementaciones:

```python
class CaptureBackend(Protocol):
    def start(self, source: SourceConfig, fps: int) -> None: ...
    def grab(self) -> np.ndarray | None:   # BGRA uint8 (h, w, 4) o None si no hay frame nuevo
        ...
    def stop(self) -> None: ...

def create(source: SourceConfig, *, preferred: str | None = None) -> CaptureBackend: ...
```

| Backend | API | Fuentes | Cursor | fps máx (1080p, medido en F0) | CPU (F0) | Plataforma | Cuándo |
|---|---|---|---|---|---|---|---|
| `windows-capture` 2.0.1 | Windows.Graphics.Capture (WGC) | Monitor **y ventana** | Sí (ajustable) | **98 fps** | **18.6 %** | Windows 10 1903+ | **Default** (barato y constante; ventanas). |
| `dxcam` 0.3.0 | Desktop Duplication (DXGI) | Monitor | No | **135 fps** (paced a 144) | 64.8 % | Windows 8+ | Modo altas fps, monitor sin cursor. |
| `mss` 10.2.0 | GDI/BitBlt (ctypes) | Monitor (acumulada) | No | 48 fps | 35.9 % | Todas | Screenshots de referencia / fallback portátil. |

Selección por defecto (ajustada con los datos de F0, ver `docs/08-f0-resultados.md`):

- Fuente = **ventana** o se pidió **cursor** → `windows-capture` (WGC).
- Fuente = **monitor** sin cursor y el usuario eligió "máximo rendimiento" → `dxcam`.
- Resto → `windows-capture` (es el default).
- En Linux/macOS, o si falla el nativo → `mss` (con aviso de "modo compatible, menor fps").

⚠️ **Gotchas medidos en F0** (van en el código con comentario):

- WGC es **damage-driven** por defecto: sin `minimum_update_interval`, una pantalla estática
  entregó **4.5 fps** (no 60). El grabador abre siempre con `minimum_update_interval=1`.
- En `windows-capture`, **`monitor_index` es 1-based** (`1` = primario; `0` lanza excepción).
- `dxcam` 0.3 usa el procesador `cv2` por defecto y arrastra `opencv-python`; fijamos
  `processor_backend="numpy"` (BGRA directo, sin copias extra).
- La tasa del ring de `dxcam` en `video_mode` es *pacing* (duplica para mantener el ritmo),
  no pruebas de tasa de captura: la captura real va al ritmo del compositor.

Notas de implementación:

- `dxcam`: `camera.start(target_fps=fps, video_mode=True)` y `get_latest_frame()`; región con
  `dxcam.create(output_idx=n).grab(region=(l, t, r, b))`. Devuelve RGB o BGRA según
  `output_color`; fijamos **BGRA** para todos los backends (un solo `-pix_fmt bgra`).
- `windows-capture`: `WindowsCapture(cursor_capture=True, monitor_index=i, window_name=...)`,
  el handler recibe `Frame` y `frame.convert_to_bgra()` (equivale a buffer numpy con `np.asarray`).
  `draw_border=False`. También expone `minimum_update_interval`, que usamos para no capturar
  por encima del fps objetivo.
- **Región**: se recorta con slicing de numpy en el hilo de captura (coste ~0 con vistas
  contiguas; `np.ascontiguousarray` solo si hace falta para el pipe).
- **Ventanas cerradas**: si la ventana objetivo se cierra, la captura se detiene con estado
  `ERROR("fuente cerrada")` y archivado limpio de lo grabado.

### Selección de región (sin overlay a pantalla completa)

Aurora 0.3 todavía **no tiene multi-ventana ni ventanas transparentes/frameless**, así que no
se puede hacer el rectángulo semitransparente clásico sobre el escritorio. La UX elegida:

1. Al pulsar "Región", `platform/screens.py` toma una **captura de referencia** del monitor con
   `mss` (rápida, formato PNG en memoria).
2. Esa imagen se muestra en la pantalla de Grabar; el usuario arrastra un rectángulo encima
   (capa `position="absolute"` con `on_drag`, cursor `crosshair`).
3. El rectángulo se guarda en **píxeles físicos** del monitor (los mismos que captura el
   backend) y se dibuja igual sobre el preview en vivo.

Es una limitación consciente: el rango de regiones se elige sobre la captura, no sobre el
escritorio real (ver 05 y 07-ADR). Cuando Aurora tenga varias ventanas, se añadirá el overlay.

## Alimentación de FFmpeg (el contrato)

Frame = `np.uint8 (h, w, 4)` BGRA contiguo en memoria → `frame.tobytes()` → `stdin.write` del
proceso. Comando base (video solo):

```bash
ffmpeg -hide_banner -loglevel warning -y \
  -thread_queue_size 512 \
  -f rawvideo -pixel_format bgra -video_size 1920x1080 -framerate 60 -i - \
  -an \
  -c:v h264_nvenc -preset p5 -rc vbr -cq 21 -b:v 0 -pix_fmt yuv420p \
  -flush_packets 1 \
  -f matroska "<temp>/video.mkv"
```

Puntos finos que el código debe respetar:

- **`-thread_queue_size 512`** en entradas "vivas" para que FFmpeg no descarte por cola llena.
- **Escritura en el hilo de captura con cola acotada (3 frames)**: si la cola está llena se
  **descarta el frame nuevo** (cuenta `dropped`) — nunca se bloquea esperando al pipe porque
  eso provoca perder vsyncs en cadena.
- **Pausa real**: basta con **dejar de escribir** en el pipe (y en el WAV). El stdin rawvideo
  no lleva timestamps propios: FFmpeg los asigna por orden de llegada, así que el tiempo del
  archivo no avanza mientras no se envían frames y la sincronía se mantiene sola. Contingencia
  (si un encoder hardware se queja tras una pausa larga): cerrar segmento y concatenar al
  final con `concat demuxer -c copy` (mismos parámetros ⇒ concatenación sin recodificar).
- **MKV temporal, MP4 final**: `.mkv` sobrevive a un crash (índice reconstruible); al detener
  se hace remux con `-c copy` (instantáneo, sin pérdida).

## Encoders y presets

Detección al arrancar (una vez, cacheada en settings):

```bash
ffmpeg -hide_banner -f lavfi -i testsrc=d=0.1:s=640x360:r=30 -c:v h264_nvenc -f null -
```

Si termina con código 0 → encoder disponible. Orden de preferencia por defecto:
`h264_nvenc` → `h264_amf` → `h264_qsv` → `libx264`.

| Preset UI | Resolución sugerida | fps | Bitrate (VBR/cq) | Notas |
|---|---|---|---|---|
| Baja | 1280×720 | 30 | 4 Mbps (`-cq 26`) | para compartir/apuntes. |
| Media | 1920×1080 | 30 | 8 Mbps (`-cq 24`) | por defecto. |
| Alta | 1920×1080 | 60 | 16 Mbps (`-cq 21`) | demos/gameplay ligero. |
| Insane | resolución nativa ≤ 2560 | 60 | 40 Mbps (`-cq 18`) | máxima fidelidad, NVENC/AMF. |

Fragmentos de flags por encoder (se documentan en código como tabla):

`h264_nvenc`: `-preset p5 -rc vbr -cq 21 -b:v 0 -pix_fmt yuv420p`
`h264_amf`: `-quality balanced -rc cqp -qp_i 22 -qp_p 24 -pix_fmt yuv420p`
`h264_qsv`: `-preset veryfast -global_quality 22 -look_ahead 0 -pix_fmt yuv420p`
`libx264`: `-preset veryfast -crf 21 -pix_fmt yuv420p` (fallback universal en CPU)

> Los valores exactos se ajustan con una grabación de prueba por GPU en F0; la tabla es el
> punto de partida. Para HEVC/AV1 hay hardware equivalente (`hevc_nvenc`, `av1_nvenc`…), pero
> **v1 exporta H.264** por compatibilidad.

## Audio (`platform/audio.py`)

- **Sistema (loopback)**: `PyAudioWPatch`: localizar el dispositivo loopback del altavoz
  predeterminado (`get_default_wasapi_loopback()`) y abrir **al rate del dispositivo**
  (44.1 kHz en F0; puede ser 48 kHz) / 2 canales / 16 bits.
- ⚠️ **Keep-alive de silencio (requisito medido en F0/S3b)**: WASAPI loopback deja de entregar
  datos cuando el motor de audio está inactivo — sin keep-alive la cobertura fue del **14.7 %**
  con huecos de **11.3 s**. Mientras se graba loopback, la app reproduce **ceros** (inaudible)
  por la salida por defecto: cobertura **99.96 %**, hueco máx. **33 ms**.
- **Micrófono**: mismo API, dispositivo de entrada elegido por el usuario. Loopback + mic
  simultáneos validados (0 overflows).
- Los callbacks entregan **bloques de tamaño variable**: contar siempre el `frame_count` real,
  nunca asumir `frames_per_buffer`.
- Cada dispositivo abre su **propio hilo + WAV incremental** (`wave` + writer thread). El
  callback jamás hace IO: encola bytes de PCM; el writer los vuelca y calcula RMS para el vúmetro.
- **Modos de mezcla**:
  - *Mezclar* (default): al mux final, `amix` de las pistas → una sola pista AAC.
  - *Pistas separadas*: el video final se muxa con el audio del sistema, y el micrófono queda
    como `.wav` hermano para importarlo como segunda pista en el editor (útil para regrabar voz).
- **Alineación A/V**: al arrancar se registran `t0_video` (primer frame al pipe) y `t0_audio`
  (primer callback) con `time.perf_counter()`. En el mux:

  ```bash
  # audio arrancó Δ segundos después del video → se retrasa Δ
  ffmpeg -i video.mkv -itsoffset <Δ> -i audio.wav \
         -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -ar 48000 \
         -af "aresample=async=1000:first_pts=0" -shortest out.mp4
  ```

  `aresample=async=1000` compensa además el drift de reloj del dispositivo (válido para los
  ±80 ms de RNF-02); en F0 la resolución de la medida quedó limitada por el buffer
  (±1 buffer ≈ 23 ms), así que la validación fina de deriva es el test de 10 min de F1.

## Preview en vivo y estadísticas

- **Preview**: no se captura dos veces. Un hilo de preview lee `LatestFrame` (con lock), escala
  a ≤ 480p y comprime JPEG (Pillow, `quality=80`) a ~6 fps → `rec_preview.set(bytes)` →
  `image(rec_preview(), fit="contain")`. Coste típico 2–5 ms por frame en un hilo aparte.
- **Stats** (throttle 4 Hz por `Signal.set`, que ya no dispara si el valor no cambió):
  tiempo, fps real (`frames_enviados / (now−t0)`), frames duplicados (relleno por tick),
  descartados (cola llena), tamaño en disco (`os.stat` del mkv), bitrate efectivo de los
  últimos 5 s, disco libre.
- **Sparkline** de fps real: `canvas()` + `polyline` con la ventana de los últimos 120 muestreos.

## Atajos

| Atajo | Acción | Ámbito |
|---|---|---|
| `F9` | Iniciar / detener | In-app v1; global en v1.1 (`pynput`, con toggle en Ajustes). |
| `F10` | Pausar / reanudar | igual. |
| `Esc` | Cancelar cuenta atrás / descartar grabación (con confirmación). | In-app. |

En v1 los atajos funcionan con la ventana enfocada (`on_key` en la raíz de la vista, con
`e.matches("f9")` y devolviendo `True` para consumir el evento). El hotkey global llega en v1.1
porque requiere un hook del sistema (riesgo de AV y de conflicto con juegos).

## Robustez (lo que evita perder una grabación)

| Escenario | Comportamiento |
|---|---|
| Crash de la app / cierre forzado | `temp/video.mkv` sigue siendo reproducible; al siguiente arranque la app ofrece "recuperar grabación interrumpida". |
| Fallo del encoder hardware al iniciar | Reintento automático con `libx264` + toast informativo. |
| Fallo del encoder a mitad | Se detiene limpio, se conserva y muxa lo grabado. |
| Micrófono ocupado / desconectado | Aviso + continuar con video (o sin esa pista); si el dispositivo desaparece a mitad, se cierra la pista y el resto sigue. |
| Disco casi lleno (< 500 MB) | Auto-stop ordenado + mux + aviso. |
| Fuente cerrada (ventana) | Stop ordenado + preservación. |
| Suspensión del PC | Se trata como pausa; al volver, si el backend se invalidó, se reinicia la captura internamente (marcado en el archivo como corte) o se detiene. |

## Presupuesto de rendimiento (medido en F0, 1080p, ver `docs/08-f0-resultados.md`)

| Etapa | Medido | Comentario |
|---|---|---|
| Captura WGC | ~98 fps · 18.6 % de un núcleo | con `minimum_update_interval=1`; pantalla estática o en movimiento. |
| Captura DXGI (dxcam) | ~135 fps · ~65 % | modo "altas fps" (sin cursor). |
| Captura mss | ~48 fps · ~36 % | respaldo; insuficiente como principal a 60 fps. |
| Copia + cola + pipe BGRA a 60 fps | **495 MB/s sostenidos**, sin drops | 60 s con 99.6 % de frames enviados. |
| Encoder NVENC (con conversión BGRA→YUV) | 84.5 % de **un** núcleo | 1080p60 válido; el resto de núcleos queda libre. |
| Encoder libx264 veryfast | 124 % (multihilo) | también válido a 60 fps en esta máquina. |
| Preview del editor (decode + escala + JPEG) | **8.5 ms/frame** (p95 9.9) | >100 fps posibles a 960×540. |
| Hilo UI | ~0 % extra | solo consume `Signal`s; RNF-03. |

Riesgo residual a vigilar en F1: máquinas sin NVENC ni iGPU (libx264 al 124 % puede quedarse
corto a 1080p60 en CPUs antiguas ⇒ degradación automática a 30 fps con aviso).

## Configuración (modelo)

```python
@dataclass(frozen=True)
class SourceConfig:
    kind: Literal["monitor", "window", "region"]
    monitor_index: int = 0
    window_title: str | None = None
    region: tuple[int, int, int, int] | None = None   # x, y, w, h en px físicos
    capture_cursor: bool = True
    backend: str | None = None                         # None = automático

@dataclass(frozen=True)
class RecordingConfig:
    source: SourceConfig
    fps: int = 60
    preset: str = "media"                 # baja|media|alta|insane
    encoder: str | None = None            # None = automático
    system_audio: bool = True
    mic_device: str | None = None
    audio_mode: Literal["mix", "separate"] = "mix"
    countdown_s: int = 0
    max_size_gb: float | None = None
```

La config completa se guarda en el sidecar `.json` de cada grabación (reproducibilidad de bugs
y reutilización de ajustes).

## Plan de pruebas del motor (F0–F1)

1. **Bench de captura pura** (`tools/bench_capture.py`): 60 s grabando sin encoder; reporta
   fps real y jitter por backend/fuente — decide la selección automática con datos.
2. **Grabación real de 10 s** por combinación {backend} × {mic, loopback, ambos} × {pausa en
   medio}: verificar con `ffprobe` duración ±1 frame, fps, pista de audio, y desfase A/V con
   test de palmada (medir `astats`/silencedetect vs frame de flash).
3. **Crash test**: matar el proceso a los 20 s → recuperar el mkv y validar.
4. **Disco lleno**: simular con cuota de archivo pequeño.
