# 08 — F0: resultados de los spikes

> Ejecutado el **2026-10-16** en la máquina de desarrollo. Los scripts viven en
> `tools/spikes/` y son re-ejecutables; los artefactos crudos (JSON, videos, WAVs) quedan en
> `tools/spikes/out/` (ignorado por git).

## Entorno de medida

| | |
|---|---|
| SO | Windows (10/11), monitor primario **1920×1080 a ~100 Hz** |
| Python | 3.12 (uv) |
| aurora-ui | 0.3.0 |
| FFmpeg | 7.1 essentials (gyan.dev) del wheel de `imageio-ffmpeg` 0.6.0 |
| Encoders disponibles (prueba real de encode) | `h264_nvenc`, `h264_amf`, `h264_qsv`, `libx264` — **los cuatro** |
| Captura | `windows-capture` 2.0.1 · `dxcam` 0.3.0 · `mss` 10.2.0 |
| Audio | `PyAudioWPatch` 0.2.12.9 · `sounddevice` 0.5.6 |
| Decode/IO | `av` 19.0.1 · `pillow` 12.3.0 · `numpy` 2.5.4 · `opencv-python` 5.0.0.93 (lo arrastra dxcam) |

---

## S1 — Bench de captura pura

6 s por backend, 1080p, cursor visible, copia real del frame (`tobytes`-equivalente).
CPU = del proceso de captura (un núcleo = 100 %).

| Backend | fps | p50 intervalo | p95 intervalo | CPU |
|---|---:|---:|---:|---:|
| **dxgi-ring (dxcam, paced a 144)** | **135.5** | 6.9 ms | 8.9 ms | 64.8 % |
| wgc-callback (windows-capture, `minimum_update_interval=1`) | 97.9 | 10.0 ms | 10.6 ms | 18.6 % |
| dxgi-pull (`DxgiDuplicationSession`) | 96.3 | 10.0 ms | 10.4 ms | 25.8 % |
| mss (GDI) | 48.6 | 20.1 ms | 25.7 ms | 35.9 % |

Hallazgos:

1. **Todos los backends modernos superan de largo el objetivo de 60 fps** en esta máquina; `mss`
   queda por debajo (48 fps) y con más CPU ⇒ es respaldo, no opción principal.
2. **WGC sin `minimum_update_interval` es damage-driven**: la primera ejecución midió **4.5 fps**
   con la pantalla casi estática. Con `minimum_update_interval=1` entrega a ritmo del compositor
   (~100 fps con este monitor) incluso en pantalla estática. **Dato crítico para el grabador.**
3. **WGC es el backend más barato en CPU** (18.6 %) y entrega frames a ritmo constante;
   `dxcam` da la tasa más alta (135 fps) a cambio de ~65 % de un núcleo (con el pacing a 144;
   a 60 el coste baja).
4. `windows-capture` con `monitor_index` es **1-based** (`monitor_index=1` = primario;
   `0` lanza excepción). Documentado para no repetirlo.
5. Los frames llegan correctos: `(1080, 1920, 4)` `uint8` BGRA/RGBA (según backend) con brillo
   coherente (no hay frames negros/DRM en esta máquina).

**Consecuencia para el diseño (ajuste de ADR-04):** el backend por defecto pasa a ser **WGC
(`windows-capture`)** para todo (monitor, ventana y cursor) y `dxcam` queda como *modo altas fps*
para monitores sin cursor. Motivo: barato, constante y cubre ventanas; dxcam exige más CPU y no
tiene cursor. `mss` se queda para captura de referencia (screenshot) y portabilidad.

> El bench de S1 mide la **copia** del frame, no su envío al pipe: eso lo mide S2 con el encoder real.

---

## S2 — Captura → pipe FFmpeg → MKV/MP4

Dos pasadas completas: **60 s con hardware (NVENC)** y 15 s con `libx264`. Captura por `dxcam`
(BGRA a 60 fps) → stdin rawvideo → MKV → remux MP4 + validación.

**Prueba real de encoders** (codificar 0.5 s; `-encoders` lista más de los que funcionan):

| Encoder | ¿Abre? |
|---|---|
| `h264_nvenc` | ✅ (GPU NVIDIA presente) |
| `h264_amf` | ❌ (listado, pero sin hardware AMD) |
| `h264_qsv` | ❌ (listado, pero sin iGPU Intel utilizable) |
| `libx264` | ✅ |

**NVENC, 1080p60, 60.12 s:**

| Métrica | Valor |
|---|---:|
| Frames enviados / esperados | **3591 / ~3607 (99.6 %)** |
| fps reales | 59.7 |
| Intervalo entre frames p50 / p95 | 16.67 / 18.84 ms |
| Throughput del pipe | **495.4 MB/s** |
| CPU del proceso ffmpeg | 84.5 % de un núcleo |
| Salida | 59.85 s · 1920×1080 · 60 fps · 1078 kbps · 8.1 MB |
| Remux MP4 (`-c copy` + faststart) | ✅ |

**libx264, 15 s:** 59.7 fps, 123.9 % de CPU (multihilo), remux ✅.

Conclusiones:

1. **El pipeline funciona tal como está diseñado**: 1080p60 BGRA por stdin sin drops apreciables
   y con el archivo validado (duración/fps/códec correctos por `ffmpeg -i`).
2. **La prueba de encoders por encode real es imprescindible**: en esta máquina solo NVENC y
   libx264 están disponibles aunque los cuatro aparezcan en la lista.
3. El jitter de llegada (p95 18.8 ms) **no afecta al archivo**: el stdin rawvideo no lleva
   timestamps y FFmpeg los asigna secuencialmente a 60 fps ⇒ A/V sin deriva por jitter.
4. `ffmpeg` consume ~0.85 núcleos con NVENC: casi todo es la conversión BGRA→YUV420 en CPU.
   Optimización futura (post-v1): convertir en Python con numpy o ajustar hilos; **no es
   bloqueante** (hay núcleos de sobra).
5. Gotcha: `dxcam.create()` es un **singleton por dispositivo/output**; recrear la cámara con
   otros parámetros devuelve la instancia anterior (y avisa por stderr). En la app: una única
   sesión de captura viva, o `release()` explícito.

---

## S3 — Audio WASAPI loopback: estabilidad y drift

Equipo: *Speakers (Realtek(R) Audio) [Loopback]* a **44 100 Hz**, micrófono de webcam XWF-1080P.

### El hallazgo crítico: el loopback se duerme sin audio sonando

Primera ejecución (120 s, sin más): **cobertura 0.147** — cero callbacks durante ~40 s
iniciales, luego ráfagas, y un **hueco máximo de 11 327 ms**. WASAPI loopback (modo compartido)
solo entrega datos cuando el motor de audio está activo; con el PC en silencio, se para. Con esa
cobertura, cualquier alineación A/V es imposible.

### La solución: keep-alive de silencio (S3b)

Mientras se graba loopback, la app reproduce **ceros** (inaudible) por el dispositivo de salida
por defecto para mantener vivo el motor de audio. Medición de 60 s con keep-alive:

| Métrica | Sin keep-alive (S3) | **Con keep-alive (S3b)** |
|---|---:|---:|
| Cobertura (audio/tiempo real) | 0.147 | **0.9996** |
| Callbacks | 761 (con huecos) | 2 583 |
| Intervalo máx. entre callbacks | **11 327 ms** | **32.7 ms** |
| Overflows | 0 | 0 |
| Drift medido (±1 buffer ≈ 23 ms) | — (sin datos) | ~29 ms en 60 s, dentro del error de frontera |

Conclusiones:

1. **El keep-alive de silencio es un requisito del grabador**, no un detalle: va en
   `platform/audio.py` como parte del diseño (y se documenta en la UI como "mantener activo el
   audio del sistema"? no: es transparente, no se comunica).
2. La resolución de la medida de drift está limitada por el tamaño de buffer (±1 buffer ≈ 23 ms);
   la validación fina de deriva (10 min, < 80 ms) pasa a **F1** como estaba previsto. No hay
   indicio de deriva patológica; el mux usa `aresample=async=1000` como red de seguridad.
3. El dispositivo entrega a **44 100 Hz** (no 48 k): el grabador respeta el rate del dispositivo
   y deja que FFmpeg remuestree al muxear (documentado).
4. Los callbacks entregan **tamaños variables** de bloque (no siempre `frames_per_buffer`):
   contar siempre `frame_count` real.
5. Micrófono y loopback **simultáneos funcionan** (mic: p95 30 ms, 0 overflows).

---

## S4 — Preview del editor (PyAV → resize → JPEG → `image(bytes)`)

Fuente: 1080p60 H.264 (8 s). Preview objetivo 960×540. 200 frames.
*(Los números iniciales de este spike estaban mal etiquetados por un bug de cronometraje; los
corregidos son los de abajo, con la ruta de escalado elegida en S4c.)*

| Etapa | p50 | p95 |
|---|---:|---:|
| Decode (`container.decode`, multihilo) | 0.13 ms | 0.21 ms |
| `to_ndarray(rgb24)` | 1.59 ms | 2.00 ms |
| Escalado (`Image.reduce(2)`) | 5.76 ms | 6.73 ms |
| JPEG q80 4:2:0 | 1.05 ms | 1.22 ms |
| **Total por frame** | **8.5 ms** | **9.9 ms** |

- **101 fps posibles a 960×540** ⇒ objetivo de 30 fps del editor con enorme margen. ✅
- `frame.to_image()` (atajo de PyAV) es mucho peor (30 ms) ⇒ `to_ndarray` + PIL.
- `seek` + decode del frame: ~24 ms ⇒ el *scrubbing* (seek a keyframe y decodificar hacia delante)
  es viable.
- `render_png()` con `image(bytes)`: ~145 ms por snapshot a 2× — es una API de tests/capturas,
  **no** se usa por frame. (El repintado de la ventana en vivo es otra ruta; se validará en F3
  con la app real.) El patrón `Signal[bytes] → image()` funciona sin recodificaciones de Aurora.
- JPEG medio del preview: 27 KB a 960×540 (irrelevante para memoria/copias).

### S4b — Variantes de JPEG (con el escalado ya fuera de la medida)

| Variante | p50 | Tamaño |
|---|---:|---:|
| PIL por defecto (q80) | 1.02 ms | 27.9 KB |
| PIL 4:2:0 (q80) | 0.95 ms | 27.9 KB |
| PIL 4:2:0 (q70) | 0.92 ms | 23.8 KB |
| cv2 `imencode` (q80) | 0.91 ms | 27.9 KB |

Decisión: **PIL, quality 80, subsampling 4:2:0** (el JPEG no era el cuello de botella; no
merece la pena meter `cv2` en el camino del preview).

### S4c — Variantes de escalado 1080p → 960×540 (el verdadero cuello de botella)

| Variante | p50 | p95 |
|---|---:|---:|
| PIL bilinear | 13.16 ms | 14.47 ms |
| PIL bilinear + `reducing_gap=2` | 13.03 ms | 14.27 ms |
| PIL `BOX` | 9.85 ms | 10.94 ms |
| **PIL `reduce(2)`** | **4.39 ms** | 5.22 ms |
| cv2 `INTER_AREA` | 8.15 ms | 9.44 ms |
| cv2 `INTER_LINEAR` | 8.09 ms | 9.62 ms |
| PIL `reduce(2)` + JPEG | **5.59 ms** | 7.52 ms |
| cv2 area + `imencode` | 9.03 ms | 11.37 ms |

Decisión: **`reduce(k)` entero (filtro de caja) + `resize` pequeño si el resto no es exacto**.
Para 1080p→540p es un solo `reduce(2)` (sin resize extra). Bate a todo lo demás —`cv2` incluido—
y evita meter `opencv` en el código de la app (solo lo usa `dxcam` internamente).

---

## S5 + S7 — Drag/scroll/cursor en Aurora (patrón del timeline)

Ejecutado con `App.pilot()` (sin ventana). Escenario: `canvas` de 2000 px dentro de un
`scroll(axis="x")` de 400 px + caja `position="absolute"` (asa) en `left=600`.

| Pregunta | Resultado medido |
|---|---|
| ¿`DragEvent.x/y` en coordenadas de contenido o de ventana? | **De ventana**: pulsando en la misma posición de ventana (x=60) antes y después de un scroll de 300 px, el evento reporta `x=60` en ambos casos (el contenido cambió de 50 a 350). |
| ¿`e.dx/dy`? | **Deltas acumulados desde el `start`** (0 → 60 → 200), con fases `start/move/end` y sin cortarse al salir del nodo (el move a `x=260` con canvas desde `x=10` sigue llegando). |
| ¿`position="absolute"` dentro de un `scroll`? | **Funciona**: `rect(asa)` pasa de `x=610` a `x=310` tras el scroll (se mueve con el contenido); el clic en el asa después del scroll llega a su handler. |
| ¿Cursores por elemento en hover (S7)? | **Funcionan**: sobre el asa con `cursor="col-resize"` → `pilot.cursor == "col-resize"`; sobre el canvas con `cursor="crosshair"` → `"crosshair"`. `pilot.hovered` identifica el nodo. |
| ¿`rect()` durante el scroll? | `rect(canvas)` = `(-290, 38, 2000, 100)`: el core reporta la caja ya desplazada (útil en tests). |

### Consecuencias para el timeline (patrón definitivo)

1. **Interacción por identidad + deltas**: cada clip/asa visible es una caja interactiva
   (identidad conocida por `key`); mover/trim/scrub usan `e.dx/dy ÷ zoom` — no hace falta
   convertir coordenadas absolutas. ✅ viable en 0.3.
2. **Clic con posición absoluta NO es posible** (`on_click` no recibe coordenadas y `DragEvent`
   viene en coordenadas de ventana): “clic en la regla para saltar a t” queda fuera de v1.
   Alternativas implementadas en el diseño: arrastrar el **asa del playhead** y la regla con
   scrub relativo + flechas (precisión por frame). → Hueco registrado para Aurora:
   *coordenadas de nodo en `ClickEvent`/`DragEvent` o `rect` accesible desde handlers*.
3. **Cursores por zona: resueltos con las cajas** (S7 sin plan B: no hace falta el cursor fijo
   `crosshair` para todo el canvas).

---

## S6 — Empaquetado (`aurora build` con el stack real)

`aurora build tools/spikes/s6_app.py --onedir --console --clean` (PyInstaller con los hooks de
`av`, `imageio_ffmpeg` y `sounddevice` de hooks-contrib, ya presentes):

| Métrica | Valor |
|---|---:|
| Tiempo de build | 25 s |
| Bundle (onedir) | 345 MB |
| Ejecución del .exe | 4.4 s, rc=0 |
| Prueba funcional | `PACKAGING_OK` con numpy/av/Pillow/sounddevice(18 disp.)/mss/dxcam/windows-capture |
| FFmpeg embebido | ✅ generó un **MP4 real (1.8 MB)** desde dentro del ejecutable |

Conclusiones:

1. **Criterio cumplido sin trabajo extra de empaquetado**: el exe arranca, importa todo el stack
   y codifica con el binario de FFmpeg incluido.
2. 345 MB es el precio real del stack: numpy + PyAV + **`opencv` (lo arrastra `dxcam`)** +
   binario FFmpeg (~30 MB) + DLLs. Optimización post-v1: probar `--exclude-module cv2` con
   `dxcam` en `processor_backend="numpy"` (ahorro estimado ~100 MB) y/o hacer `dxcam` un extra
   opcional.
3. `onefile` no se probó: la decisión de diseño (arranque rápido en onedir) se mantiene.
4. Tiempo de compilación bajo (25 s) ⇒ el ciclo de release es cómodo; el smoke test del bundle
   queda como script reutilizable (`tools/spikes/s6_packaging.py` es el prototipo).

---

## Resumen de decisiones actualizadas

| ADR | Cambio |
|---|---|
| ADR-04 (captura) | Backend por defecto **WGC (`windows-capture`)**; `dxcam` pasa a modo altas fps sin cursor; `mss` a screenshots/portabilidad. Gotchas: `minimum_update_interval=1`, `monitor_index` 1-based, singleton de `dxcam`. |
| ADR-08 (timeline) | Patrón confirmado por S5: canvas + cajas absolutas, interacción por identidad y deltas. Añadido: sin clic posicional absoluto en 0.3 (asa de playhead + scrub relativo + teclado). |
| ADR-09 (preview) | Ruta definitiva medida: decode PyAV + `to_ndarray` + `Image.reduce(k)` + JPEG q80 4:2:0 (PIL) = **8.5 ms/frame**. `to_image()` y `cv2` descartados con datos. |
| ADR-02/ADR-07 | Encoders: probar por encode real (AMF/QSV se listan pero no funcionan aquí); NVENC + libx264 verificados a 1080p60 sin drops. |

## Criterios de salida de F0 — verificación

| Spike | Criterio | Resultado |
|---|---|---|
| S1 | Tabla de fps y CPU; backend por defecto justificado | ✅ 4 backends medidos; WGC por defecto |
| S2 | 60 s grabados, `ffprobe` ok, < 1 % drops | ✅ 99.6 % a 60 fps, remux validado (NVENC y libx264) |
| S3 | Estabilidad y drift del loopback | ✅ con keep-alive: cobertura 99.96 %, hueco máx. 33 ms (validación de 10 min pasa a F1) |
| S4 | < 20 ms por frame a 960×540; caché/patrón validado | ✅ 8.5 ms/frame (p95 9.9) |
| S5 | Semántica de `DragEvent` en scroll + patrón fijado | ✅ documentado: coords de ventana, deltas, cajas absolutas OK |
| S6 | Exe que arranca y renderiza un MP4 de 2 s | ✅ 25 s de build, exe funcional (345 MB onedir) |
| S7 | Cursores por zona | ✅ `col-resize` sobre el asa sin plan B |

## Pendientes tras F0

- **F1**: test de audio de 10 min (deriva fina < 80 ms; la resolución de F0 estaba limitada por
  el buffer), prueba de pausa/reanudar de la mano del grabador real, y recuperación tras crash.
- **F1**: medir el consumo con WGC como backend por defecto durante 10 min continuos
  (F0 midió 98 fps a 6 s; falta la estabilidad larga).
- **Post-v1**: bundle más ligero (excluir `opencv`/`cv2` del empaquetado si `dxcam` lo permite),
  captura HDR/color, y `onefile`.
- Nada de F0 bloquea el arranque de F1: la arquitectura documentada en 01–05 queda validada.
