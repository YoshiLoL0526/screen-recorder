# 15 — Roadmap post-v1: qué falta

> Estado a **2026-10-28**, tras v1.0.0, los primeros trabajos post-v1 (bandeja y atajos globales,
> filmstrip de miniaturas, diálogos nativos de Aurora y alineación con aurora-ui 0.5) y el
> **bloque A** ([16 — Bloque A](16-bloque-a-entrega.md)). Este documento es la **fuente de verdad
> de lo que queda**; [06](06-roadmap.md) conserva las fases F0–F5, sus criterios de aceptación y la
> tabla de riesgos (R1–R11), a los que aquí se hace referencia.

## Cómo leerlo

- **Estado**: ✅ hecho · 🔜 siguiente · ⏳ pendiente · 💤 aparcado (con el motivo).
- **Tamaño**: S (≤ 1 día) · M (2–4 días) · L (1–2 semanas) · XL (más), a tiempo parcial y con IA.
- Cada entrada lleva **por qué**, **dónde** toca y **cómo se comprueba**. Sin criterio comprobable no
  está terminado (docs/06 → «Definición de terminado»).

## Punto de partida

Hecho después de la v1.0.0 (para no volver a planificarlo):

| Trabajo | Dónde vive |
|---|---|
| Arranque robusto si Windows bloquea la carpeta de grabaciones | `platform/paths.py`, `state.py`, `ui/controller.py` (docs/09) |
| aurora-ui alineado a 0.5.0, con techo `<0.6` | `pyproject.toml`, `docs/05` |
| Diálogos nativos de Aurora en vez de ctypes | `ui/file_dialogs.py` (borrado `platform/dialogs.py`) |
| Bandeja, atajos globales F9/F10 y «seguir grabando al cerrar» | `ui/tray.py`, `platform/hotkeys.py` |
| Filmstrip de miniaturas en los clips | `services/media.thumbstrip`, `ui/timeline.filmstrip_slots` |
| Tests portables entre equipos (región y `draw` del timeline) | `tests/test_f21_ui.py`, `tests/test_f3_editor.py` |

**206 tests** en verde y `tools/check_build.py` OK (bundle de **253 MB** con `aurora 0.5.0`,
`pynput` y el icono de la bandeja dentro; sin OpenCV). El CI está **en verde**
(`40700123750`) y ya valida también el instalador. Todo lo que sigue **no está implementado**,
salvo lo marcado como hecho en el bloque A.

---

## Bloque A — Cerrar la entrega de la v1 (verificación y distribución)

Es lo que separa «funciona en mi máquina» de «es un producto». Nada de esto añade funciones, pero
todo lo demás pierde valor sin ello.

| # | Qué falta | Estado | Tamaño |
|---|---|---|---|
| A1 | **Poner el CI en verde** | ✅ `40700123750` | S–M |
| A2 | Prueba en **máquina limpia** del flujo CU-1→CU-5 | ⏳ (CI cubre instalar + smoke) | M |
| A3 | **RNF-06/07 con ventana real** (arranque < 2 s, RSS < 600 MB en reposo y < 1.2 GB grabando) | ✅ [medido](16-bloque-a-entrega.md#a3--rnf-0607-con-ventana-real) | S |
| A4 | **Instalador** (Inno Setup) sobre el bundle onedir | ✅ 71 MB, verificado | M |
| A5 | **Bundle más ligero** (`--exclude-module cv2`, `upx = true`) | ✅ 370 → 253 MB; UPX descartado | S |
| A6 | Verificar el export **en un móvil físico** (pendiente de F4) | ⏳ | S |
| A7 | Soak de **30 min** a 1080p60 con mic+sistema (pendiente de F1) | ⏳ | S |
| A8 | Validar a mano la «sensación» del timeline con ratón real (pendiente de F3) | ⏳ | S |

**A1 está hecho.** El último run en rojo (`39171815584`) fallaba en un solo test por una carrera
real: la cache de miniaturas se leía a medio escribir y quedaba un JPEG de 0 bytes cacheado. Se
arregló con escritura atómica y validación de la tira
([detalle y run verde](16-bloque-a-entrega.md#a1--ci-en-verde)). El job de build ya corre en el
runner (smoke del exe incluido) y se añadió la verificación del instalador.

**A2** es la prueba que más valor aporta por hora invertida: instalar en una máquina sin Python ni
FFmpeg y completar grabar → biblioteca → editar → exportar → abrir el MP4. Todo lo que falle ahí es
un bug real que los 206 tests no ven. El CI ya cubre la instalación silenciosa y el smoke en un
runner limpio; falta el flujo manual ([procedimiento](16-bloque-a-entrega.md#a2--máquina-limpia-pendiente-manual)).

**A4/A5 están hechos**: instalador de 71 MB verificado y bundle de 370 → **253 MB** al excluir
OpenCV (112 MB, no 43: el wheel actual trae `opencv_videoio_ffmpeg500_64.dll`). UPX se midió y se
descartó porque rompía RNF-06 ([detalle](16-bloque-a-entrega.md#a5--bundle-más-ligero)).

**Criterio de aceptación del bloque**: el flujo CU-1→CU-5 se completa en una máquina limpia sin tocar
la consola, con el CI en verde y los RNF medidos con ventana. CI ✅ y RNF ✅; el flujo en máquina
limpia sigue pendiente (A2), igual que el móvil, el soak y el ratón (A6–A8).

---

## Bloque B — Editor: lo que falta del producto

El editor tiene el modelo, el timeline, el preview y el export. Falta lo que la gente espera después
de cortar y pegar.

### B1 — Pista de música, ducking y crossfades de audio (🔜 · L)

- **Por qué**: es el uso más pedido en un grabador de pantalla (poner música de fondo a un tutorial).
  El `filter_complex` de F4 **ya mezcla** pistas de audio; lo que falta es la UI.
- **Dónde**: `core/graph.py` (ya preparado), `core/project.py` (`Track("audio")` existe en el modelo),
  `ui/timeline.py` (pintar la pista de audio ya está resuelto para el waveform), `services/export.py`,
  inspector.
- **Qué falta**: importar audio, añadirlo como clip a la pista de audio, volumen por clip y por pista
  (el `insp_track_gain` ya existe), **ducking** (bajar la música cuando suena la voz del sistema/mic)
  y `acrossfade` entre clips de música.
- **Criterio**: un proyecto con video + música exporta con la mezcla audible y el ducking medible
  (comparar el nivel de la música con y sin voz); el preview suena igual que el export.

### B2 — Textos y títulos sobre el video (⏳ · L)

- **Por qué**: títulos de sección y subtítulos quemados opcionales (docs/01).
- **Dónde**: nuevo `core/effects.py` (`kind="text"`), `core/graph.py` (`drawtext`), preview.
- **Bloqueado por**: el preview. `canvas()` no dibuja imágenes y el preview es una `image()` con el
  JPEG del frame; dibujar texto encima exige componer en el render (PIL/PyAV) o esperar a un
  `canvas.image()` de Aurora. **Decisión previa obligatoria** (ver bloque F).
- **Criterio**: un texto con fuente/tamaño/posición/color se ve igual en preview y en el export.

### B3 — Selección múltiple y operaciones en lote (⏳ · M)

- **Por qué**: borrar/mover 20 cortes de uno en uno es tedioso.
- **Dónde**: `ui/timeline.py` (marquee en zona vacía — ya anotado como post-v1 en docs/05),
  `state.selection`, `ui/editor_controller.py` (`delete`/`move` en lote con un solo paso de undo).
- **Criterio**: arrastrar un rectángulo selecciona N clips; `Supr` los borra con **un** undo.

### B4 — Snap de audio por waveform (⏳ · M)

- **Por qué**: alinear dos clips por el sonido (una palmada) es lo que hace el «snap de audio» de
  docs/01. Hoy el snap es solo por tiempo.
- **Dónde**: `core/timeline.py` (snap puro), `services/media.py` (ya hay picos por bucket),
  `ui/editor_controller.py`.
- **Criterio**: al mover un clip cerca de un pico del vecino, el snap engancha en el pico.

### B5 — Proxy automático para videos pesados (⏳ · M, plan B de R3)

- **Por qué**: R3 (docs/06) — el preview no llega a 24 fps con 4K o códecs exigentes.
- **Dónde**: `services/media.py` (transcode 540p al importar, cacheado), `services/playback.py`.
- **Criterio**: un 4K60 de prueba se previsualiza a ≥ 24 fps sin tocar el export (que sigue usando el
  original).

### B6 — Reubicar carpetas y buscar archivos (⏳ · S)

- **Por qué**: hoy «Reubicar…» es de uno en uno y por nombre; un proyecto con 30 clips movidos es
  tedioso.
- **Dónde**: `ui/editor_controller.py` (`relocate_asset`), `ui/screens/editor.py`.
- **Criterio**: reubicar una carpeta entera resuelve todos los assets que falten de golpe.

---

## Bloque C — Captura: lo que falta del grabador

### C1 — Webcam PiP y cara separada (⏳ · L)

- **Por qué**: el segundo uso más pedido; docs/01 lo tiene como nº2 del post-v1.
- **Dónde**: `platform/capture.py` (segunda sesión de captura), `services/recording.py` (dos fuentes),
  `core/graph.py` (overlay), pantalla de Grabar.
- **Criterio**: grabar pantalla + cara, con la cara como PiP compuesto en el export y el archivo de
  cara también disponible por separado.

### C2 — Recortar los últimos N segundos al vuelo (⏳ · S)

- **Por qué**: «eso no lo quería» sin abrir el editor (docs/09 → pendientes de F1).
- **Dónde**: `services/recording.py` (marcar un punto de corte), `ui/screens/record.py`.
- **Criterio**: un botón descarta el último tramo y el MP4 resultante lo confirma.

### C3 — Detección de HDR y aviso (⏳ · S, R9)

- **Por qué**: grabar una pantalla HDR da colores lavados sin avisar.
- **Dónde**: `platform/screens.py` (detectar), `ui/screens/record.py` (aviso).
- **Criterio**: con HDR activo aparece el aviso antes de grabar y el sidecar lo registra.

### C4 — Degradación de fps y recomendación de preset (⏳ · M, R1/R5)

- **Por qué**: en hardware modesto 1080p60 con libx264 al 124 % de un núcleo (docs/03) no sostiene.
- **Dónde**: `ui/controller.py` (`_poll_worker` ya mide fps reales), `services/settings.py`.
- **Criterio**: si el fps real cae por debajo del objetivo de forma sostenida, se avisa y se ofrece
  bajar a 30 fps; al primer arranque, el preset se propone según los encoders que funcionan.

### C5 — Pistas de audio separadas (⏳ · M)

- **Por qué**: docs/03 — el micrófono queda como WAV hermano, no como pista del proyecto.
- **Dónde**: `services/recording.py` (mux de dos pistas), `core/project.py`, biblioteca.
- **Criterio**: la grabación con mic produce dos pistas y el editor las importa por separado.

---

## Bloque D — Compartir

| # | Qué falta | Estado | Tamaño |
|---|---|---|---|
| D1 | **GIF/WebM optimizado** en un clic (docs/01 nº6) | ⏳ | M |
| D2 | Publicación directa a disco o `yt-dlp` (docs/01 nº7) | 💤 | M |
| D3 | «Copiar el archivo al portapapeles» y «compartir» desde la Biblioteca | ⏳ | S |

**D1** encaja en `services/export.py` (ya tiene presets y progreso) más un preset nuevo en
`core/graph.py`; el criterio es un GIF de 10 s que pese menos de X MB y se vea bien en un móvil.
**D2** está aparcado a propósito: depende de un tercero (yt-dlp) y de credenciales del usuario, y no
aporta nada al núcleo del producto.

---

## Bloque E — Plataforma

| # | Qué falta | Estado | Tamaño |
|---|---|---|---|
| E1 | **Port a macOS/Linux** con los mismos contratos de `platform/` | 💤 | XL |
| E2 | `strings.py` (textos centralizados) | 💤 | S |

**E1** (docs/01 nº8): la arquitectura ya lo permite (`platform/` encapsula el SO), pero exige
AVFoundation/ScreenCaptureKit en macOS y PipeWire/XDG en Linux, más un `aurora build` por plataforma.
Está aparcado porque la app es Windows-first (WGC, WASAPI, `dxcam`) y el coste es alto.
**E2** se descartó en F5 (docs/14) y se hará **solo si llega la internacionalización**: hoy extraer
los textos solo añade una indirección.

---

## Bloque F — Aurora: adopciones y propuestas

### F1 — Capacidades de Aurora 0.5 que aún no usamos (⏳ · variable)

Ordenadas por relación valor/coste (detalle en la tabla de docs/05):

| Capacidad | Qué desbloquearía | Tamaño |
|---|---|---|
| `App.window` (varias ventanas) | **Overlay de región sobre el escritorio** (hoy es in-app) y ventana de preview desacoplada | M |
| `table(...)` (ordenación, columnas, virtualización) | La Biblioteca, hoy con `virtual_list` a mano | M |
| `chart(...)` | El sparkline de fps, hoy un `canvas` con `polyline` | S |
| `dialog`/`toast`/`progress`/`menu`/`accordion`/`field` | Sustituir las capas propias de toasts/modales/progreso | M |
| `on_context_menu` y menús nativos (`App(menu=…)`) | Menú de app y clic derecho (hoy todo en la barra) | S |
| `overflow="hidden"` | Recortes que hoy se hacen con `scroll` de 0 px o canvas | S |
| `App(motion=False)` / `AURORA_MOTION=0` | Snapshots y CI deterministas | S |
| `aurora build --upx` | Bundle más pequeño (ver A5) | S |

### F2 — Propuestas a Aurora (💤 · según necesidad)

Salen de los golpes reales del proyecto (docs/05) y se proponen **cuando el proyecto lo justifica**,
no antes: `canvas.image()` (mataría la capa de `image()` absolutas del filmstrip),
`on_hover` (cursores por zona sin la capa de cajas), `scroll_to` (mantener el playhead a la vista al
hacer zoom) y `rect` del nodo en los handlers (hoy hay que deducir la x con `TL_X0` + scroll).

### F3 — Vigilar aurora-ui 0.6 (⏳ · S cada vez)

La dependencia tiene techo `<0.6` a propósito (Aurora es `0.x` y rompe entre menores). Subir de
versión es un trabajo **deliberado**: `uv lock` + suite completa + `tools/check_build.py` + revisar la
tabla de limitaciones de docs/05. Ya pasó una vez: 0.3 → 0.5 cambió comportamiento sin avisar en el
lock (ver el commit de alineación).

---

## Priorización recomendada

Si hay que elegir un orden (y no simplemente el orden del documento):

1. **A2** — máquina limpia. Es donde aparecen los bugs que importan; el CI ya cubre instalar y el
   smoke del exe, pero no el flujo CU-1→CU-5 con monitor y audio.
2. **A6 + A7 + A8** — cerrar las comprobaciones manuales del bloque A (móvil, soak de 30 min y
   sensación del timeline); los procedimientos están en [docs/16](16-bloque-a-entrega.md).
3. **B1** — pista de música. Es la función que más gente echa en falta y el motor ya la soporta.
4. **C1** — webcam PiP, el siguiente salto de producto.
5. **F1** — a medida que se toquen esas pantallas, ir sustituyendo capas propias por componentes de
   Aurora (empezando por `chart` y `overflow`, que son los más baratos).

**Fuera de este roadmap** (para no reabrirlo cada vez): telemetría, multi-ventana por capricho,
internacionalización sin traductor, y cualquier función que no tenga un caso de uso escrito en
docs/01.
