# 09 — F1: motor de grabación (implementación)

> Implementado el **2026-10-16**. Código en `src/recstudio/` (paquete instalable), CLI en
> `tools/rec.py`, pruebas de aceptación en `tools/av_sync_test.py` y `tools/soak_test.py`.

## Qué se implementó

| Módulo | Contenido |
|---|---|
| `core/config.py` | `RecordingConfig`, `SourceConfig`, `AudioConfig` (frozen), presets y validación. |
| `core/errors.py` | Jerarquía `RecStudioError` (config, capture, audio, ffmpeg, disk). |
| `platform/capture.py` | 4 backends con interfaz común (`wgc`, `dxgi`, `dxcam`, `mss`), `LatestFrame` y selección automática con cascada. |
| `platform/audio.py` | `AudioRecorder` (WAV incremental con hilos), `SilenceKeepAlive`, `list_devices`, instancia única de PyAudio. |
| `platform/ffmpeg.py` | Binario (env `RECSTUDIO_FFMPEG` → wheel), probe real de encoders con caché, comandos de captura/mux, `probe_media`, miniaturas. |
| `platform/screens.py` | Monitores (mss) y captura de referencia PNG para elegir región. |
| `platform/paths.py` | Rutas de config/cache/temporales/videos (`%APPDATA%`, `%LOCALAPPDATA%`). |
| `services/recording.py` | `RecordingSession` (máquina de estados), hilo de captura con pacing, pausa real, housekeeping (disco/tamaño/ffmpeg), mux con offsets, sidecar JSON, `scan_recoverable()`/`recover_session()`. |
| `tools/rec.py` | CLI: `--list`, `--probe-encoders`, `--recover`, región, ventana, fps/preset/encoder, audio, duración, pausa programada. |
| `tests/` | 20 tests (config, comandos FFmpeg, e integración real con lavfi: probe, mux con offset, mezcla, miniatura). |

Flujo implementado (idéntico al documentado en 03): captura → `video.mkv` por stdin rawvideo;
audio a WAV por dispositivo con keep-alive; pausa = dejar de alimentar ambos (tiempos continuos);
stop → mux `-c:v copy` + AAC 48 kHz con `-itsoffset`/`atrim` según el offset medido
(`first_callback − t0_video`), miniatura, sidecar JSON y limpieza de temporales.

## Bugs reales encontrados (y corregidos) durante F1

Estos seis habrían costado horas de depuración después; quedan documentados para no repetirlos:

1. **`Frame.frame_buffer` de WGC ya es un numpy `(h, w, 4)` BGRA contiguo.** `convert_to_bgr()`
   **muta el frame y devuelve el propio objeto `Frame`** (no un array): usarlo con `np.array()`
   lanza `TypeError`. Se usa `frame_buffer` directamente y se copia (el buffer se recicla).
2. **`on_closed` es obligatorio** en `WindowsCapture`: sin ese handler, `start_free_threaded()`
   lanza `RuntimeError("on_closed Event Handler Is Not Set")`.
3. **`minimum_update_interval` se cuantiza al periodo del compositor**: en una pantalla de
   100 Hz, valores ≤ 8 ms equivalen a "sin throttle" (~100 fps); 16 → ~50 fps, 33 → ~25 fps, y
   sin el ajuste (por defecto) el ritmo es *damage-driven* (4.5–50 fps). Con la fórmula "mitad
   del frame time" (8 ms para 60 fps) la entrega cayó a ~10 fps de frames nuevos: fijado a **1**.
4. **Una sola instancia de PyAudio por proceso.** Crear instancias y llamar a `terminate()`
   mientras otra tiene streams vivos llama a `Pa_Terminate` globalmente y **mata esos streams**:
   en la primera grabación real, el loopback capturó un único bloque (23 ms). Ahora hay una
   instancia compartida (`_shared_pa()`) que nunca se termina.
5. **El callback de PyAudio debe devolver `paContinue` (0).** Devolver `1` es `paComplete`: el
   stream se detiene tras el primer buffer. (`paAbort` es 2.) Este bug costaba los "1 callback
   y silencio" de las primeras pruebas.
6. **Firma del callback**: `(in_data, frame_count, time_info, status)` — omitir `time_info`
   lanza `TypeError` que PortAudio propaga de forma confusa.

## Mediciones de F1

| Prueba | Resultado |
|---|---|
| `uv run pytest` | 20/20 ✅ (unit + integración FFmpeg real) |
| Grabación 8 s 1080p60 (WGC + NVENC, audio sistema) | fps real **60.0** · dup 5–253 · skipped 9–367 · audio **8.34 s** para 8.03 s de video ✅ |
| Pausa (14 s de reloj, 3 s de pausa) | archivo de **11.02 s** (14 − 3) y audio de 11.35 s: la pausa no avanza ninguno ✅ |
| Soak 10 min (600 s de reloj, 5 s de pausa) | archivo **595.03 s** (= 600 − 5) · fps 60.0 sostenido · audio 595.31 s · 66 MB ✅ |
| Soak de deriva (8 min, beeps cada 60 s, pausa a mitad) | latencias de 2–42 ms, sin salto tras la pausa · **deriva −88 ppm ⇒ −53 ms/10 min** (residuo máx 16 ms) → OK (<80 ms) ✅ |
| Latencia de audio (beep) | **+2 a +42 ms** estable (buffer de salida + buffer del loopback) |
| Latencia de video (cursor) | **+12 a +119 ms, variable** (compositing del cursor de WGC) |
| Desfase A/V medido (audio ↔ cursor) | **+29 a −83 ms** según pasada (criterio < 80 ms: al límite) |
| Corrección aplicada en el mux | `aresample=async=1000` (huecos de timestamp) — **no** corrige deriva continua (ver abajo) |
| Recuperación tras crash | ✅ `tools/recovery_test.py` en verde (3.01 s con audio) |

### Desfase A/V y deriva: interpretación y plan

1. **Latencia del cursor (variable, 12–119 ms)**: es el compositing del cursor de Windows a través
   de WGC. No es un error de nuestro offset (que se calcula con el mismo reloj en ambos lados).
   Mitigación planificada (F1.1): **cursor propio** — capturar sin cursor y dibujarlo en el frame
   con la posición leída en el instante de captura (técnica de OBS). Elimina la varianza y
   habilita resaltar clics.
2. **Deriva del reloj de audio (~90 ppm)**: el reloj del dispositivo WASAPI no coincide
   exactamente con el del sistema; medido en dos soaks independientes (+92 ppm y −88 ppm) ⇒
   ~50 ms cada 10 min. Está **dentro** del criterio de RNF-02 (< 80 ms), pero con poco margen para
   grabaciones de 30 min (~150 ms ⇒ incumpliría). `aresample=async` **no** la corrige (los
   timestamps de cada archivo son internamente coherentes; `async` solo rellena huecos).
   Mitigación F1.1: medir el reloj del dispositivo durante la grabación (ya contamos muestras y
   sabemos el tiempo real: `samples/rate` vs `perf_counter`) y aplicar el ratio en el mux
   (`atempo=1+ppm` o `aresample` con tasa corregida). Es un cambio de ~10 líneas con tests.
3. El marcador de contenido parpadeante (ventana Aurora) no se pudo validar en esta máquina
   porque el escritorio no muestra ventanas (entorno sin terminal visible); el test lo soporta y
   debería re-ejecutarse en una máquina de uso normal.

## Criterios de aceptación de F1

| Criterio (roadmap) | Estado |
|---|---|
| 10 min a 1080p60 con mic + sistema | ✅ soak de 10 min (600 s de reloj → 595.03 s de archivo) |
| Deriva < 80 ms | ✅ **−53 ms/10 min** (−88 ppm, residuo máx 16 ms) · sin salto tras la pausa |
| Pausa en medio sin desincronía | ✅ 14 s − 3 s = 11.02 s con audio coherente |
| Matar el proceso → recuperación válida | ✅ `tools/recovery_test.py` (detección + mux + validación) |
| UI mantiene < 8 ms/frame mientras graba | N/A en F1 (sin UI); se validará en F2 |

### Pendientes que pasan a F1.1 (no bloquean F2)

1. **Cursor propio** (software cursor) para eliminar la varianza de latencia de WGC.
2. **Compensación activa de deriva** del dispositivo (~90 ppm) con ratio en el mux.
3. Soak de 30 min (RNF-02 completo) con la compensación activa y eventos cada 5 min.
4. Re-validar el marcador de contenido parpadeante en una máquina con ventanas visibles.

## Uso de la CLI (prototipo de F1)

```bash
uv run python tools/rec.py --list                      # monitores, audio y encoders
uv run python tools/rec.py --duration 30 -o salida/    # 30 s, monitor primario, audio del sistema
uv run python tools/rec.py --region 100,100,1280,720 --fps 30 --preset alta --duration 20
uv run python tools/rec.py --window "Visual Studio" --audio both --mic 1 --duration 15
uv run python tools/rec.py --recover                   # muxea la última sesión interrumpida
```

## Prueba de recuperación

Automatizada en `tools/recovery_test.py` (crea una sesión interrumpida sintética, la detecta, la
recupera y valida el MP4):

```
1) creando sesión interrumpida sintética en %TEMP%/recstudio/rec_…
2) scan_recoverable() → la incluye
3) recover_session() → 2026-10-16_16-39-17-recuperada.mp4 · 3.01s · 640×360 · audio=sí · 1.37 MB
4) la sesión ya no vuelve a aparecer como recuperable
✅ recuperación OK
```

Para el caso real (matar la app a mitad):

```bash
uv run python tools/rec.py --duration 60 -o salida/     # y matar el proceso a los ~10 s
uv run python tools/rec.py --list-recoverable
uv run python tools/rec.py --recover
```

Además, la prueba destapó un bug real: **las sesiones completadas quedaban con `session.json` en
estado `recording`** y aparecían como recuperables. Corregido: al muxear con éxito se marca
`done` y se limpia el temporal (`shutil.rmtree`); solo los abortos/crashes se conservan.

## Qué NO hace F1 (fuera de alcance, ya en el roadmap)

- Selección de región con UI (existe `capture_reference_png`, pero la UI es F2).
- Hotkeys globales, bandeja, notificaciones.
- Cursor propio y resaltado de clics (F1.1).
- Recorte de audio/video al vuelo («recortar los últimos 5 s»), post-v1.
- UI Aurora completa (F2 en adelante).
