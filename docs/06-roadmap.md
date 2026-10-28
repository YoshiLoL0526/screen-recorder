# 06 — Roadmap, riesgos y criterios de aceptación

## Estrategia

Construir el **motor primero, la UI después, el editor al final de lo fácil**:
cada fase deja algo que funciona de verdad (se puede grabar antes de que exista el editor), y las
incógnitas técnicas se resuelven en F0 con prototipos desechables, no con arquitectura especulativa.

Estimaciones en semanas de trabajo de una persona + IA, a tiempo parcial.

## F0 — Spikes (3–5 días)

Objetivo: matar las incógnitas que pueden cambiar la arquitectura. Cada spike es un script
suelto en `tools/spikes/`, se mide y se documenta el resultado.

> ✅ **Ejecutado (2026-10-16): resultados completos y decisiones actualizadas en
> [08 — F0: resultados](08-f0-resultados.md).**

| Spike | Pregunta | Criterio de salida |
|---|---|---|
| S1 Captura pura | ¿fps reales y jitter por backend (WGC/DXGI/mss) en este HW? | Tabla de fps y CPU; elección de backend por defecto con datos. |
| S2 Pipe a FFmpeg | ¿aguanta 1080p60 BGRA por stdin con NVENC/libx264 sin drops? | 60 s grabados, `ffprobe` ok, < 1 % drops. |
| S3 Audio loopback | ¿PyAudioWPatch estable + cuánto drift en 10 min? | Drift < 50 ms con `aresample=async`; comando de mux afinado. |
| S4 Preview del editor | ¿decode PyAV + resize + JPEG → `image(bytes)` a 30 fps? | Latencia por frame < 20 ms a 960×540; patrón de cache validado. |
| S5 DragEvent en scroll | ¿`DragEvent.x` viene en coordenadas del contenido o del viewport? ¿`position="absolute"` funciona dentro de un contenedor con scroll? | Respuesta documentada + patrón de timeline fijado. |
| S6 Empaquetado | ¿`aurora build` con av+numpy+imageio-ffmpeg+sounddevice? | Exe onedir que arranca y renderiza un MP4 de 2 s. |
| S7 Cursores `col-resize` | ¿la capa de asas interactivas da el cursor correcto en hover? | GIF/captura de validación; si falla, plan B (cursor fijo `crosshair`). |

**Deadline de decisiones:** al terminar F0 se revisan los ADR de 07 con los datos medidos.

## F1 — Motor de grabación sin UI (1–2 semanas) — ✅ implementado (2026-10-16)

> Implementación y mediciones completas en [09 — F1](09-f1-motor-grabacion.md). Incluye la CLI
> (`tools/rec.py`), la máquina de estados con pausa, mux con offsets, sidecar JSON y recuperación
> tras crash, más 20 tests (unit + integración FFmpeg real).

- `platform/capture.py` (backends + selección), `platform/audio.py`, `platform/ffmpeg.py`
  (localización del binario, run, parseo de progreso), `services/recording.py` (máquina de estados).
- `tools/rec.py`: CLI mínima (`rec.py --monitor 0 --fps 60 --audio system -o out.mp4 10s`).
- Pausa/reanudar, mux, sidecar JSON, recuperación de sesiones.

**Aceptación:** 10 min a 1080p60 con mic+sistema; deriva < 80 ms; pausa en medio sin desincronía;
matar el proceso a mitad → oferta de recuperación válida. RNF-01/02/08 verificados.

## F2 — UI de grabación + Biblioteca (1–2 semanas) — ✅ implementado (2026-10-16)

> Pantallas de Grabar y Biblioteca con Aurora, preview en vivo, stats con sparkline, toasts y
> modal, atajos F9/F10, configuración persistente y recuperación de sesiones en la UI.
> Prueba end-to-end con `App.pilot()` en `tools/f2_ui_test.py` (ver
> [10 — F2](10-f2-ui.md), con capturas).
>
> Aceptación: ✅ grabar/parar/pausar solo con la UI y F9/F10; ✅ la UI no bloquea (el motor va en
> hilos); ✅ «detener → biblioteca con miniatura → acciones».

- `theme.py`, `state.py`, shell (sidebar/header), pantalla Grabar completa, preview en vivo,
  stats/sparkline, cuenta atrás, hotkeys in-app.
- `services/library.py` + pantalla Biblioteca (`virtual_list`, thumbnails, recuperación).
- `services/settings.py` (persistencia de preferencias).

**Aceptación:** grabar/parar/pausar solo con la UI y F9/F10; la UI mantiene < 8 ms por frame
mientras graba (RNF-03); el flujo "detener → aparece en biblioteca con thumbnail → Editar" funciona.

## F3 — Editor: modelo, timeline y preview (2–3 semanas) — ✅ implementado (2026-10-16)

> Modelo, timeline, undo/autosave, preview a 30 fps y pantalla Editor completa; resultados,
> mediciones y bugs en [12 — F3](12-f3-editor.md). Aceptación automatizada en
> `tools/f3_editor_test.py`. Quedan para F4: efectos, pistas de audio independientes y
> reubicar archivos offline.

- `core/project.py`, `core/timeline.py`, `core/store.py` (undo/autosave) con su batería de tests.
- `services/media.py` (probe, waveforms, thumbnails) y `services/playback.py` (preview + reloj).
- Pantalla Editor: preview + transport + inspector + **timeline interactivo completo**
  (mover/trim/split/delete/reordenar/zoom/snap).

**Aceptación:** montar 3 clips con cortes exactos y previsualizarlos a ≥ 24 fps; todos los drags
con undo correcto; abrir/cerrar proyecto preserva estado; RNF-04 y la "sensación" del timeline validada.

## F4 — Export y transiciones (1–2 semanas) — ✅ implementado (2026-10-16)

> Builder del filter graph (golden tests), export con progreso/ETA/cancelar/verificación,
> presets (GPU, calidad, 720p, sin recodificar), efectos por clip (color, volumen, fundidos,
> zoom) visibles en el preview, reubicar archivos offline. Resultados y bugs en
> [13 — F4](13-f4-export.md); aceptación automatizada en `tools/f4_export_test.py`.
>
> Aceptación: ✅ 3 clips + 2 transiciones + velocidad con duración exacta (546/546 frames) y A/V
> en sync (≤ 10 ms); ✅ H.264 High/yuv420p/AAC/faststart; ✅ 1 min 1080p60 en 12.9 s con NVENC;
> ✅ corte rápido idéntico byte a byte. Pendiente: probar a mano en un móvil físico. Las pistas
> de música siguen en el backlog (el graph ya las mezcla, falta la UI).

- `core/graph.py` (builder + golden tests) y `services/export.py` (progreso, ETA, cancelar).
- 8 transiciones + efectos (eq, volumen, fades, velocidad) + presets de export + corte rápido.

**Aceptación:** export de un proyecto de 3 clips con 2 transiciones y velocidad: duración ±1 frame
de la esperada, audio en sync, reproducible en un móvil y en Windows; export de 1 min 1080p60
< 40 s con NVENC (RNF-05); corte rápido sin recodificar idéntico byte a byte en los tramos conservados.

## F5 — Pulido y v1.0 (1–2 semanas) — ✅ implementado (2026-10-16, v1.0.0)

> Detalle en [14 — F5: pulido](14-f5-pulido.md). Hecho: Ajustes completos (selector de carpeta,
> valores por defecto, editor, sistema, restablecer), layout adaptable (preview vertical,
> ventana grande, Ajustes con scroll), divisores del editor + inspector ocultable, `Esc`/`Intro`
> en modales, icono, `[tool.aurora.build]`, smoke del exe (`tools/check_build.py`: ✅ build 27 s,
> 369 MB, smoke rc=0), 13 tests y CI en `.github/workflows/ci.yml`. El CI está en verde desde el
> bloque A ([docs/16](16-bloque-a-entrega.md)); RNF-06/07 ya medidos e instalador hecho. Pendiente:
> prueba en máquina limpia, móvil físico, soak y ratón (A2/A6/A7/A8); `strings.py` descartado
> mientras la app sea solo en español.

- Ajustes completos, tema claro/oscuro, toasts/modales pulidos, estados vacíos, textos (strings.py).
- `aurora build` + icono + smoke test del exe (`tools/check_build.py`).
- Suite de tests completa + snapshots PNG; README; CHANGELOG.

**Aceptación:** instalar en una máquina limpia y completar el flujo CU-1→CU-5 sin tocar la
consola; CI en verde (unit + golden + smoke UI); RNF-06/07 medidos.

## Post-v1 (backlog ordenado)

> El backlog vivo está en **[15 — Roadmap post-v1](15-post-v1.md)**, con estado, tamaño y criterio de
> aceptación de cada pieza. Aquí queda el orden original y lo ya hecho.

1. ✅ **Hotkeys globales (pynput) + bandeja + minimizar a bandeja al grabar** — hecho: `platform/hotkeys.py`
   (hook opt-in) y `ui/tray.py` (icono, menú, cierre a la bandeja grabando y aviso al terminar). Ver
   [docs/05](05-ui-aurora.md) y el CHANGELOG.
2. ✅ **Filmstrip de thumbnails en los clips** — hecho: capa de `image()` sobre el canvas
   (`ui/timeline.py`), tira por asset cacheada en disco (`services/media.thumbstrip`) y etiqueta del
   clip encima. Ver [docs/05](05-ui-aurora.md) y el CHANGELOG.
3. ✅ **Pista de música + ducking + crossfades** — hecho: pista M, inspector, `sidechaincompress`
   y `acrossfade`, preview con la mezcla completa (B1, [docs/17](17-bloque-b-editor.md)).
4. ✅ **Textos/títulos sobre el video** — hecho: `TextOverlay`, `drawtext` y composición PIL en el
   preview (B2, [docs/17](17-bloque-b-editor.md)).
4b. ✅ **Selección múltiple, snap por waveform, proxy 540p y reubicar carpetas** (B3–B6,
   [docs/17](17-bloque-b-editor.md)).
5. Webcam PiP (segunda captura) + grabación de cara separada.
6. Export GIF/WebM optimizado para compartir en un clic.
7. Port a macOS/Linux con los mismos contratos de `platform/`.
8. Integración con Aurora: proponer `canvas.image()`, `on_hover` y multi-ventana si el proyecto lo justifica.

## Riesgos

| # | Riesgo | Prob. | Impacto | Mitigación | Plan B |
|---|---|---|---|---|---|
| R1 | La captura no alcanza 60 fps estables en HW objetivo | Media | Alto | S1/S2 eligen backend con datos; WGC/DXGI son rápidos; preview sin coste extra | Degradación automática de fps con aviso; preset 30 fps por defecto en HW bajo |
| R2 | Deriva/desincronía de audio | Media | **Crítico** | Medición exacta de `t0`, `itsoffset` en mux, `aresample=async`; S3 valida 10 min | Pista de audio "sellada" por dispositivo + test de palmada automático en CI de grabación |
| R3 | El preview del editor no llega a 24 fps en videos pesados | Media | Alto | Preview 960×540 adaptativo; decode secuencial sin seeks en play; S4 | Proxy automático (transcode 540p al importar) post-v1 |
| R4 | `xfade` genera A/V desincronizado o falla con assets diversos | Media | Alto | Normalización obligatoria (frame/fps/format/sar/tb) antes de xfade; golden tests con assets de prueba variados | Fallback: export sin transiciones + JPEG de transición… o reemplazo de la transición por corte (aviso al usuario) |
| R5 | Capacidad de disco/CPU insuficiente con presets altos | Media | Medio | Presets con bitrate claro + estimación MB/min en la UI; auto-stop por disco; detección de encoder HW (CPU si no) | Recomendación automática de preset según HW al primer arranque |
| R6 | PyInstaller rompe algo (PyAV/numpy/ffmpeg) | Baja | Alto | S6 temprano + smoke test del exe en CI; hooks oficiales ya existen para av/imageio_ffmpeg/sounddevice | zip portable de la carpeta onedir |
| R7 | Comportamiento de `DragEvent` dentro de `scroll` distinto de lo asumido | Media | Medio | S5 primero; toda la interacción del timeline depende de esto | Timeline sin scroll nativo: pan manual con `on_scroll` del contenedor y todo en un canvas (válido también) |
| R8 | Antivirus/Anti-cheat bloquea hooks o captura | Baja | Medio | v1 sin hooks globales ni DLL-injection; WGC es la API "limpia" (no hookea procesos) | Documentar excepciones recomendadas |
| R9 | HDR / color incorrecto al grabar pantallas HDR | Media | Bajo | Detectar pantallas HDR y avisar; grabar SDR (tone-map básico con zscale en post si hiciera falta) | Captura SDR del monitor SDR / recomendar desactivar HDR |
| R10 | Aviso de "app joven" (Aurora 0.5 pre-alfa) | Media | Bajo | Contener los golpes: caps de API por temporada (techo `<0.6`), tests de UI, y usarla como campo de pruebas (los bugs se convierten en issues de Aurora) | — (los límites ya están documentados en 05) |
| R11 | El alcance crece ("ya que estamos, webcam/música/textos") | **Alta** | Alto | Este documento: v1 cerrado; nuevas ideas → backlog | Revisión de alcance al terminar cada fase |

## Definición de terminado (por feature)

1. Funciona en el flujo real (no solo en test).
2. Test unitario (si es `core/`) o de UI/snapshot (si es visual).
3. Errores posibles traducidos a estado + toast (nada de excepciones crudas).
4. Sin bloqueos del hilo de UI (regla de arquitectura).
5. Documentado: doc tocada o CHANGELOG + comentario razonado en el código donde no sea obvio.
