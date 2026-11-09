# 15 — Roadmap post-v1: qué falta

> Estado a **2026-11-05**, tras v1.0.0, los primeros trabajos post-v1 (bandeja y atajos globales,
> filmstrip de miniaturas, diálogos nativos de Aurora y alineación con aurora-ui 0.10), los
> **bloques A y B** ([16 — Bloque A](16-bloque-a-entrega.md), [17 — Bloque B](17-bloque-b-editor.md))
> y las **fases 0, 1, 2 y 3 del editor** ([19 - Fase 0 y 1](19-fase-0-1-audio.md),
> [20 - Fase 2](20-fase-2-frame.md), [21 - Fase 3](21-fase-3-flujo.md)), más las **features v6**
> de composición ([22 - plan](22-plan-features-editor.md), [23](23-canvas-resize.md),
> [24](24-pistas-av.md), [25](25-elementos.md), [26](26-efectos-globales.md)) y el
> **panel de medios/capas** ([27](27-panel-medios-y-capas.md)), el **PiP de video**
> ([28](28-pip-video.md)), las **pistas V2/V3** ([29](29-pistas-video.md)), los
> **subtítulos** ([30](30-subtitulos.md)), los **keyframes** ([31](31-keyframes.md)), las
> **envolventes de volumen de audio** ([32](32-audio-envelopes.md)), la **escala/rotación de
> elementos** ([33](33-escala-rotacion.md)), las **envolventes de audio de elementos**
> ([34](34-envolventes-elementos.md)), la **escala/rotación de textos** ([35](35-textos-escala-rotacion.md)),
> el **color/tamaño de textos** ([36](36-color-tamano-textos.md)), la **edición de claves de
> envolvente en el timeline** ([37](37-editor-claves.md)), las **asas de animaciones visuales**
> ([38](38-asas-animaciones.md)), el **easing de las animaciones y de las envolventes de audio**
> ([39](39-easing.md) y [40](40-easing-audio.md)) y el **copiar/pegar de claves**
> ([41](41-copiar-pegar-claves.md)).
> Este documento es la **fuente de verdad de lo que queda**; [06](06-roadmap.md) conserva las fases
> F0–F5, sus criterios de aceptación y la tabla de riesgos (R1–R11), a los que aquí se hace
> referencia.

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
| aurora-ui alineado a 0.10.0, con techo `<0.11` (props desconocidas y `no-label` incluidos) | `pyproject.toml`, `docs/05` |
| Diálogos nativos de Aurora en vez de ctypes | `ui/file_dialogs.py` (borrado `platform/dialogs.py`) |
| Bandeja, atajos globales F9/F10 y «seguir grabando al cerrar» | `ui/tray.py`, `platform/hotkeys.py` |
| Filmstrip de miniaturas en los clips | `services/media.thumbstrip`, `ui/timeline.filmstrip_slots` |
| Lienzo configurable, transformación del frame y auto-rotación | `core/project.py`, `core/transform.py`, `core/graph.py`, `services/playback.py`, inspector (docs/20) |
| Flujo de edición: portapapeles, marquesina que mueve, razor, marcadores, In/Out, loop, J/K/L, Q/W y export de rango | `core/timeline.py`, `core/graph.py`, `core/keyframes.py`, `services/playback.py`, `ui/editor_controller.py`, `ui/timeline.py` (docs/21) |
| Redimensionar y mover el clip sobre el canvas del preview | `core/canvas_edit.py`, `ui/screens/editor.py`, `ui/editor_controller.py` (docs/23) |
| Pistas de video/audio separadas por defecto y enlazadas | `core/timeline.py`, `ui/editor_controller.py`, `ui/timeline.py` (docs/24) |
| Elementos superpuestos: figuras, imágenes, blur y z-order | `core/elements.py`, `core/overlays.py`, `services/element_assets.py`, inspector y preview (docs/25) |
| Efectos globales: color, blur, viñeta y blanco y negro | `core/global_effects.py`, `core/color.py`, inspector (docs/26) |
| Tests portables entre equipos (región y `draw` del timeline) | `tests/test_f21_ui.py`, `tests/test_f3_editor.py` |

**394 tests** en verde y `tools/check_build.py` OK (bundle de **256 MB** en local con
`aurora 0.10.0`, `pynput` y el icono de la bandeja dentro; sin OpenCV). El CI valida tests + build +
instalador. Todo lo que sigue **no está implementado**, salvo lo marcado como hecho en los
bloques A y B y las fases 0/1/2/3 del editor.

---

## Bloque A — Cerrar la entrega de la v1 (verificación y distribución)

Es lo que separa «funciona en mi máquina» de «es un producto». Nada de esto añade funciones, pero
todo lo demás pierde valor sin ello.

| # | Qué falta | Estado | Tamaño |
|---|---|---|---|
| A1 | **Poner el CI en verde** | ✅ `40701857907` | S–M |
| A2 | Prueba en **máquina limpia** del flujo CU-1→CU-5 | ⏳ (CI cubre instalar + smoke) | M |
| A3 | **RNF-06/07 con ventana real** (arranque < 2 s, RSS < 600 MB en reposo y < 1.2 GB grabando) | ✅ [medido](16-bloque-a-entrega.md#a3--rnf-0607-con-ventana-real) | S |
| A4 | **Instalador** (Inno Setup) sobre el bundle onedir | ✅ 72 MB, verificado | M |
| A5 | **Bundle más ligero** (`--exclude-module cv2`; UPX medido y descartado) | ✅ 370 → 255 MB | S |
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
un bug real que los 288 tests no ven. El CI ya cubre la instalación silenciosa y el smoke en un
runner limpio; falta el flujo manual ([procedimiento](16-bloque-a-entrega.md#a2--máquina-limpia-pendiente-manual)).

**A4/A5 están hechos**: instalador de 72 MB verificado y bundle de 370 → **255 MB** al excluir
OpenCV (112 MB, no 43: el wheel actual trae `opencv_videoio_ffmpeg500_64.dll`). UPX se midió y se
descartó porque rompía RNF-06 ([detalle](16-bloque-a-entrega.md#a5--bundle-más-ligero)).

**Criterio de aceptación del bloque**: el flujo CU-1→CU-5 se completa en una máquina limpia sin tocar
la consola, con el CI en verde y los RNF medidos con ventana. CI ✅ y RNF ✅; el flujo en máquina
limpia sigue pendiente (A2), igual que el móvil, el soak y el ratón (A6–A8).

---

## Bloque B — Editor: lo hecho

El editor ya tiene todo lo de v1 y ahora también música, textos, selección múltiple, snap de audio,
proxy, reubicar carpetas, **audio separable/editable** (fases 0 y 1), **lienzo/transformación del
frame** (fase 2) y el **flujo de edición** (fase 3). Resultados y decisiones en
[17 — Bloque B](17-bloque-b-editor.md), [19 — Fase 0 y 1](19-fase-0-1-audio.md),
[20 — Fase 2](20-fase-2-frame.md) y [21 — Fase 3](21-fase-3-flujo.md).

### B1 — Pista de música, ducking y crossfades de audio (✅ · L)

- `Track` gana `ducking` (0…1); `Project.audio` se dibuja en una pista **M** propia.
- Importar música (`EditorController.import_music`) crea la pista y sus clips; inspector con volumen de pista,
  volumen del clip, ducking y crossfade.
- Export: `acrossfade` entre clips de música y `sidechaincompress` usando el audio del video como
  sidechain; el preview reproduce la **misma mezcla** renderizada a WAV por FFmpeg (cacheada).

### B2 — Textos y títulos sobre el video (✅ · L)

- `TextOverlay` en `Project.texts` (texto, intervalo, posición, tamaño, color, negrita).
- Export con `drawtext` (una sola cadena, `expansion=none`, texto escapado); preview con PIL sobre
  el frame, misma posición y escala. `texts` bloquea el corte rápido.

### B3 — Selección múltiple y operaciones en lote (✅ · M)

- Herramienta de marquesina en la barra del timeline; `Supr` borra N clips con **un** undo;
  `Ctrl+A` selecciona todo. La selección se pinta en el canvas.

### B4 — Snap de audio por waveform (✅ · M)

- `media.waveform_peaks` elige máximos locales; `tl.audio_snap_candidates` los mapea al timeline.
  Mover o recortar un clip engancha también a los picos de los vecinos y de la música.

### B5 — Proxy automático para videos pesados (✅ · M, plan B de R3)

- `media.proxy` transcodea 540p sin audio a la cache; `playback` usa el proxy si está listo y el
  export sigue usando el original. Medido en esta máquina: 4K60 5.9 ms/frame → 540p 1.3 ms/frame
  (4.6×), con el proxy generado en 0.6 s para 2 s de video.

### B6 — Reubicar carpetas y buscar archivos (✅ · S)

- «Reubicar carpeta…» busca por nombre (sin distinguir mayúsculas) en la carpeta elegida y resuelve
  todos los assets offline de una vez, con un solo undo.

### B7 — Audio separable y editable: fases 0 y 1 (✅ · L)

Resultados y decisiones completas en [19 — Fase 0 y 1](19-fase-0-1-audio.md).

- **Modelo v3** (`SCHEMA_VERSION=3`): `Clip.start`, `audio_enabled`, `link_id`; `Track.mode`
  (`sequence`/`absolute`), `name`, `solo`, `locked`. Migración v2 → v3 y proyectos v1/v2
  idénticos al abrir.
- **Separar/adjuntar audio** (`detach_audio`/`attach_audio`) con una pista absoluta enlazada y
  un solo undo. **Dividir todo** (`S`), **solo audio** (`Mayús+S`) y por pista.
- **Editar clips de audio**: mover, recortar y borrar en pistas secuenciales y absolutas, con
  drags, snap y transacciones de undo.
- **Varias pistas visibles** con cabeceras de nombre, mute, solo, bloqueo y volumen; añadir y
  quitar pista. El solo aísla también el audio del video; el bloqueo impide mover/recortar/
  borrar/dividir. Límite de 6 pistas nuevas (alternativa al scroll vertical anidado de Aurora 0.10).
- **Fundidos de audio independientes** (`audio_fade_in/out`) en el graph y el preview.
- **Export/preview**: `adelay` en muestras + `amix` para pistas absolutas, WAV de preview con la
  misma mezcla (`build_audio`) y corte rápido bloqueado con audio separado/absoluto.

### B8 — Lienzo y transformación del frame: fase 2 (✅ · M)

Resultados y decisiones completas en [20 — Fase 2](20-fase-2-frame.md).

- **Modelo v4** (`SCHEMA_VERSION=4`): `Project.background`, `Project.default_fit` y
  `Clip.transform` (`Transform` con fit/scale/x/y/rotation/flip/crop). Migración v3 → v4 y
  proyectos v1/v2/v3 idénticos.
- **Lienzo configurable** en el inspector: presets 16:9/9:16/1:1/4:5/1440p/4K, tamaño
  personalizado, encaje por defecto, color de fondo y FPS. Cambiarlo no mueve clips.
- **Transformación por clip** con un solo undo por cambio y preview inmediato.
- **Fit/fill/stretch** y la misma geometría en `core/transform.py` para preview y export;
  `eq`/fundidos/textos mantienen su orden y los golden de F4 no cambian.
- **Auto-rotación** de móviles leída con PyAV; el export deja que FFmpeg auto-rote (evita la
  display matrix duplicada) y el preview la aplica explícitamente.
- **Corte rápido** bloqueado con lienzo distinto, fondo/fit no histórico, transformación o
  auto-rotación.

### B9 — Flujo de edición: fase 3 (✅ · M)

Resultados y decisiones completas en [21 — Fase 3](21-fase-3-flujo.md).

- **Modelo v5**: `Project.markers`, `range_in`, `range_out`; migración v4 → v5 y proyectos v1–v4
  idénticos al abrir.
- **Portapapeles**: `Ctrl+C/X/V/D` para video, audio y textos (también en selección múltiple),
  pegando en el playhead con transformación, efectos y enlace audio↔video; un undo por operación.
- **Marquesina que mueve** el bloque (un undo, respeta bloqueos y snap) y **razor** visible (`C`),
  excluyente con la marquesina, por pista o en todas.
- **Marcadores** (`B`, ↑/↓, regla, inspector, snap), **In/Out** del timeline (`I`/`O`; el
  inspector pasa a `P`), **loop** (`Ctrl+L`) en el `Player` y **J/K/L** con velocidad creciente.
- **Q/W** recortan hasta el playhead (ripple en video) y el **export de rango** funciona en render
  y en corte rápido sin tocar los golden.
- Filmstrip y pósters de assets rotados enderezados. **A17** (zoom que sigue al playhead) queda
  bloqueado por Aurora 0.10.

### B10 — Canvas interactivo: mover y redimensionar el clip (✅ · M)

Resultados y decisiones completas en [23 — Canvas resize](23-canvas-resize.md).

- **Preview medido**: el panel mide su hueco con `on_resize` y pinta un escenario con el aspecto
  del proyecto; la imagen y las cajas comparten píxeles.
- **Manipulación directa**: cuerpo para mover (`Transform.x/y`) y cuatro asas para escalar
  (`Transform.scale`, 0.25…4) anclando la esquina opuesta.
- **Un undo por gesto**, `Esc` cancela, un clic sin movimiento no ensucia el historial y la capa
  se oculta al reproducir.
- Sin cambios de esquema: usa `Clip.transform` de la fase 2.

### B11 — Pistas de video/audio separadas por defecto y enlazadas (✅ · M)

Resultados y decisiones completas en [24 — Pistas A/V](24-pistas-av.md).

- **Importación**: si el video tiene audio, se crea «Audio del video» (pista absoluta) y el clip
  de video queda mudo; todo en un solo undo.
- **Enlace**: mover, recortar, dividir o cambiar la velocidad del video recoloca/ajusta su audio
  enlazado; mover o recortar el audio enlazado por su cuenta se bloquea con aviso.
- **Desvincular/Vincular**: el audio se conserva al desvincular y `relink_audio` recupera el
  enlace sin duplicar clips; el corte rápido sigue bloqueado con audio separado.

### B12 — Elementos superpuestos: figuras, imágenes y blur (✅ · L)

Resultados y decisiones completas en [25 — Elementos](25-elementos.md).

- **Modelo v6**: `Project.elements` y `Element` (imagen/rect/elipse/triángulo) con intervalo,
  centro/tamaño normalizados, rotación, opacidad, color/asset, fit y efectos.
- **Composición**: geometría pura compartida, PNG de figuras materializado en cache atómica,
  blur horneado con Pillow; el export usa `-loop 1`, `scale`, `rotate`, alfa y `overlay`, y el
  preview compone con PIL.
- **UI**: lista y controles en el inspector, alta de figuras/imágenes, z-order, borrado y caja
  con asas sobre el preview; un undo por cambio/gesto.
- El corte rápido se bloquea con elementos; sin elementos el graph es idéntico al histórico.

### B13 — Efectos globales sobre todo el video (✅ · M)

Resultados y decisiones completas en [26 — Efectos globales](26-efectos-globales.md).

- **Modelo v6**: `Project.effects` (color, blur, viñeta, blanco y negro) además de los efectos
  por clip.
- **Orden único** en preview y export: compuesto → `eq` → `gblur` → `vignette` → `hue=s=0` →
  elementos → textos.
- **UI** en el inspector del proyecto, un undo por cambio y reset; sin efectos el filter graph
  es byte a byte el histórico.
- Blur escalado al alto de salida, viñeta con la fórmula real de FFmpeg y paridad verificada con
  export real.

### B14 — Panel de medios, selección directa y capas (✅ · M)

Resultados y decisiones completas en [27 — Panel de medios y capas](27-panel-medios-y-capas.md).

- **Panel de medios/capas** a la izquierda del preview (`ui/project_panel.py`): ve todos los
  assets, inserta en el playhead, arrastra al timeline/preview, acepta `on_drop` del Explorador y
  quita medios sin usar. Los pósters se generan en un hilo y llegan por `state.posters`.
- **Selección directa en el preview** de clip, elemento y texto, con caja y asas. Los textos usan
  `element_overlay.text_box` (métricas reales de la fuente) y se pueden mover y escalar; el clip
  base ya se puede seleccionar con un clic aunque no tuviera selección.
- **Capas en el timeline**: filas de Textos y Elementos sobre la pista de video, con barras
  movibles/recortables, snap y cabeceras. La geometría usa `Tracks.video_top` y
  `max_tracks_extra` descuenta su alto.
- **Inspector**: `number_input` para geometría/opacidad de elementos y tamaño/posición de texto;
  `color_picker` para color de elemento y texto; campo de texto del título con ancho correcto.
- **Portapapeles**: copiar/duplicar elementos (antes quedaban fuera).
- **Preview de texto**: mismo contorno que `drawtext`, en vez de una sombra que el export no tenía.
- Sin cambios de esquema; 13 tests nuevos en `tests/test_project_panel.py` y **407 tests** en verde.

### B15 — PiP de video (✅ · M)

Resultados y decisiones completas en [28 — PiP de video](28-pip-video.md).

- **Modelo v7** (`SCHEMA_VERSION=7`): `Element.kind="video"` y `Element.in_` (segundo de entrada);
  migración v6 → v7 sin cambios visibles.
- **Composición**: el video superpuesto se decodifica en preview y se exporta con `-ss/-t`,
  `setpts` al inicio del elemento, `overlay` con `eof_action=pass` y la misma geometría que la caja
  del elemento.
- **UI**: botón PiP en el panel de medios, «Video PiP…» en Capas, recorte izquierdo que avanza la
  fuente, inspector con Fuente y **Volumen** (0 = silencio, botones Silenciar/Original).
- **Audio**: se mezcla con el video y las pistas en preview y export. Usa `Effect("volume")` en
  `Element.effects` (sin cambiar el esquema), reutiliza la entrada del overlay y alimenta el
  sidechain del ducking. Un solo de audio silencia también los PiP.
- **Límite**: el PiP no tiene handles de fundido propios ni animación de volumen; el resto de la
  composición (imagen, posición, rotación, opacidad, intervalo) sí.
- Tests nuevos en `test_modelo_v6_base`, `test_pip_video` y `test_project_panel`;
  **421 tests** en verde.

### B16 — Pistas de video V2/V3 (✅ · M)

Resultados y decisiones completas en [29 — Pistas de video](29-pistas-video.md).

- **Modelo v8** (`SCHEMA_VERSION=8`): `Element.video_track` (0 = Elementos, 1 = V2, 2 = V3) y
  migración v7 → v8 sin cambios visibles. No hay `Track` nuevas: las pistas son grupos de
  elementos de video, reutilizando composición, audio y drag.
- **Composición**: V1 → V2 → V3 → efectos globales → Elementos → textos, en preview y export.
- **UI**: destino Elementos/V2/V3 en Capas, alta desde el panel/`+`/arrastre/`Video en V2…`,
  filas V3/V2 en el timeline con cabecera y `+`, selector **Pista** en el inspector y prefijo en
  la lista de capas. A pantalla completa por defecto; se puede reducir a PiP.
- **Audio**: el de un clip V2/V3 se mezcla como el del PiP; un solo de audio lo silencia.
- **Límite**: sin razor/transiciones por pista V2/V3 (se recortan como elementos), sin mute/lock
  por pista y máximo dos pistas extra.
- Tests nuevos en `test_modelo_v6_base`, `test_video_tracks` y `test_project_panel`;
  **431 tests** en verde.

### B17 — Subtítulos SRT/VTT (✅ · M)

Resultados y decisiones completas en [30 — Subtítulos](30-subtitulos.md).

- **Parser puro** (`core/subtitles.py`): SRT y VTT, BOM/CRLF, índices, ajustes de cue, `NOTE`,
  etiquetas HTML/ASS y cues multilínea (unidos con espacios).
- **Modelo v9**: `TextOverlay.subtitle` y `TextOverlay.opacity`; migración v8 → v9 sin cambios
  visibles.
- **UI**: Subtítulos… y SRT… en Capas, cues en la fila Textos en teal, chip Subtítulo y
  «Aplicar estilo a todos los subtítulos».
- **Límite**: una línea por cue, Arial fija, sin cajas/estilos ASS ni subtítulos blandos.
- Tests nuevos en `test_subtitles`, `test_project_panel` y `test_modelo_v6_base`.

### B18 — Keyframes de posición y opacidad (✅ · M)

Resultados y decisiones completas en [31 — Keyframes](31-keyframes.md).

- **Modelo v9**: `Animation`/`Keyframe` en `Element.animations` y `TextOverlay.animations`;
  tiempos locales, valores 0…1, interpolación lineal.
- **Canales**: X/Y de elementos y textos; opacidad de textos. La opacidad de un elemento no se
  anima porque el filtro de export es estático.
- **Preview/export**: `value_at` compartido y expresiones `if(lt(t,…))` en `overlay` y
  `drawtext` (`alpha` incluido); sin animación, las cadenas del graph son las de siempre.
- **UI**: sección Animación con «Clave X/Y/Opacidad» y «Quitar»; ◆ en el timeline; la caja del
  preview se oculta si X/Y están animados.
- **Límite**: sin escala/rotación/color/tamaño animados en esa entrega; el easing llegó en B26 y
  las claves siguen siendo explícitas (sin grabación automática).
- Tests nuevos en `test_animation`, `test_animation_export` y `test_project_panel`;
  **446 tests** en verde.

### B19 - Envolventes de volumen de audio (✅ · M)

Resultados y decisiones completas en [32 — Envolventes de volumen](32-audio-envelopes.md).

- **Modelo v10** (`SCHEMA_VERSION=10`): `Clip.envelope` con `VolumePoint(t, gain)` en tiempo
  local al clip y ganancia 0…4; migración v9 → v10 sin cambios visibles.
- **Export/preview**: un `volume=volume='…':eval=frame` por clip con curva, multiplicando el
  volumen estático y el de la pista; el clip entrante de una transición desplaza las claves con
  `skip`. El preview fuerza la mezcla WAV de `build_audio` cuando hay envolvente.
- **UI**: sección «Envolvente de volumen» en el inspector de video y de audio, «Clave volumen»
  en el playhead y «Quitar»; polilínea sobre la waveform y **◆ env** en la barra.
- **Límite**: no hay envolvente en el audio de un PiP/V2/V3 (`Element.envelope` en B21); las
  asas de arrastre sobre la waveform y el modo Env llegaron en B24. El corte rápido se bloquea.
- Tests nuevos en `test_audio_envelope`; **462 tests** en verde.

### B20 - Animación de escala y rotación de elementos (✅ · M)

Resultados y decisiones completas en [33 — Escala y rotación](33-escala-rotacion.md).

- **Modelo v9 sin cambios**: `Element.animations` acepta `scale` (multiplicador `0.05…4.0`) y
  `rotation` (grados absolutos `-360…360`); `TextOverlay` sigue solo con X/Y/opacidad.
- **Preview**: `core.elements.effective` aplica la caja escalada y la rotación; las imágenes
  escalan su PNG materializado y los PiP/V2/V3 su frame decodificado.
- **Export**: el elemento animado se hornea a un vídeo RGBA `qtrle` con PIL (misma geometría que
  el preview) y se superpone con las expresiones X/Y de siempre. Sin animación de geometría, las
  cadenas del graph son las de antes; el audio del PiP sigue usando el archivo original.
- **UI**: campo **Escala** (no persistido), claves **Escala** y **Rotación**, «Quitar» y caja del
  preview oculta si hay geometría animada.
- **Límite**: los textos no tienen escala ni rotación animadas (llegaron en B22).
- Tests nuevos en `test_scale_rotation` y `test_project_panel`; **454 tests** en verde.

### B21 - Envolventes de audio de elementos (✅ · S)

Resultados y decisiones completas en [34 — Envolventes de elementos](34-envolventes-elementos.md).

- **Modelo v11** (`SCHEMA_VERSION=11`): `Element.envelope` reutiliza `VolumePoint` de
  `core/audio_envelope`; claves locales al elemento (`0` = su `start`) y ganancia 0…4.
  Migración v10 → v11 sin cambios visibles (`envelope=()`).
- **Export/preview**: `_element_audio_mixes` añade `volume=volume='…':eval=frame` después del
  volumen estático y antes de `adelay`; cubre PiP y V2/V3. `build_audio` (preview) usa la misma
  sección de audio.
- **UI**: sección «Envolvente de volumen» en el inspector de un video con audio, «Clave volumen»
  en el playhead local y «Quitar»; **◆ env** en la barra del elemento. Las asas sobre la barra
  llegaron en B24.
- **Límite**: el volumen estático 0 sigue silenciando; sin polilínea en la barra (solo asas).
- Tests nuevos en `test_element_audio_envelope`; **480 tests** en verde.

### B22 - Escala y rotación animadas de textos (✅ · M)

Resultados y decisiones completas en [35 — Textos con escala y rotación](35-textos-escala-rotacion.md).

- **Modelo v10 sin cambios**: `TextOverlay.animations` acepta `scale` (multiplicador `0.05…4.0`
  del `size`) y `rotation` (`-360…360`, grados absolutos); `text_values` devuelve también ambos.
- **Preview**: `services.playback.text_rgba` rasteriza el texto con PIL (escala, opacidad y
  rotación horneadas) y `_draw_texts` lo compone centrado en X/Y. Los textos sin transformar
  conservan el camino anterior.
- **Export**: los textos con escala/rotación se hornean a un `.mov` `qtrle` RGBA con la misma
  función del preview y se superponen con `overlay` (X/Y como expresiones). Los estáticos siguen
  usando `drawtext` en orden; sin textos transformados, las cadenas del graph no cambian.
- **UI**: campos **Escala** y **Rotación** no persistidos, claves **Escala**/**Rotación**,
  «Quitar» y caja del preview oculta si hay geometría animada.
- **Límite**: sin color ni rotación base en esa entrega (el color llegó en B23); interpolación
  lineal; el corte rápido
  sigue bloqueado por `Project.texts`.
- Tests nuevos en `test_text_transform` y `test_project_panel`; **479 tests** en verde.

### B23 - Color y tamaño animados de textos (✅ · M)

Resultados y decisiones completas en [36 — Color y tamaño de textos](36-color-tamano-textos.md).

- **Modelo v12**: canal `size` (`8…200`) en `TextOverlay.animations` y modelo aparte de color
  `core/text_color.py` con `ColorKeyframe(t, color)`, interpolación lineal en sRGB,
  `TextOverlay.color_keys` y migración v11 → v12 sin cambios visibles.
- **Preview**: `services.playback.text_rgba` aplica tamaño y color animados; los textos con esos
  canales usan la misma ruta que escala/rotación y los estáticos conservan el dibujo directo.
- **Export**: `services.text_animation` hornea también color y tamaño (`.mov` `qtrle` RGBA) y
  `drawtext` sigue intacto cuando no hay animación de estilo. El orden de `Project.texts` se
  mantiene mezclando `drawtext` y overlays.
- **UI**: claves **Color** y **Tamaño** en el inspector (contador, «Quitar»), con el valor actual
  de los campos en el playhead local.
- **Límite**: sin color animado en elementos/clips; el easing de los canales de `Animation`
  llegó en B26 y el color sigue interpolándose linealmente; el corte rápido sigue bloqueado por
  `Project.texts`.
- Tests nuevos en `test_text_color_size`; **498 tests** en verde.

### B24 - Edición de claves de envolvente en el timeline (✅ · M)

Resultados y decisiones completas en [37 — Edición de claves](37-editor-claves.md).

- **Sin cambios de modelo** (`SCHEMA_VERSION=11`): se editan `Clip.envelope` y `Element.envelope`
  con `audio_envelope.set_point`/`value_at`; preview y export leen el mismo estado.
- **Asas**: cada clave es una caja arrastrable sobre la waveform (V1 y pistas de audio) o sobre la
  barra de un video superpuesto. `dx` mueve el tiempo local y `dy` la ganancia 0…4; geometría pura
  compartida (`envelope_gain_y`/`envelope_time`).
- **Modo Env** (`E` o botón): clic en la waveform para añadir una clave; clic sin movimiento en un
  asa la selecciona y `Supr` la borra; menú contextual del asa para eliminar. Las asas viven en una
  capa encima del playhead para que su línea de 2 px no bloquee el clic.
- **Undo**: transacción que empieza en el primer movimiento (`begun=False`), una entrada por gesto
  (`mover`/`añadir`/`eliminar clave de volumen`); `Esc` cancela el gesto.
- **Límite**: los keyframes visuales (`Animation`) siguen sin asas en el timeline; no hay
  copiar/pegar claves ni grabación automática (las asas visuales llegaron en B25).
- Tests nuevos en `test_envelope_handles`; **496 tests** en verde.
- Tras integrar ambas ramas: **505 tests** en verde.

### B25 - Asas de animaciones visuales en el timeline (✅ · M)

Resultados y decisiones completas en [38 — Asas de animaciones](38-asas-animaciones.md).

- **Sin cambios de modelo** (`SCHEMA_VERSION=12`): se reutilizan `Element.animations`,
  `TextOverlay.animations`, `animation.set_keyframe`/`clear_channel` y las expresiones de siempre.
- **Selector de canal** (`state.keyframe_channel`, no persistido): X/Y/Escala/Rotación en
  elementos y X/Y/Opacidad/Escala/Rotación/Tamaño en textos; `color` queda fuera.
- **Asas**: polilínea y una caja arrastrable por clave sobre la barra del elemento/texto
  seleccionado. `dx` mueve el tiempo local y `dy` el valor del canal, con los rangos `0…1`,
  `0.05…4`, `-360…360` y `8…200`; geometría pura compartida
  (`keyframe_value_y`/`keyframe_time`).
- **Undo**: transacción que empieza en el primer movimiento (`begun=False`), una entrada por
  gesto (`mover clave …`); `Esc` cancela. Clic sin mover solo selecciona; `Supr`/menú contextual
  borran la clave y **Quitar** del inspector sigue borrando el canal entero.
- **Límite**: no hay modo para añadir claves desde el timeline ni copiar/pegar; las asas solo
  aparecen con la capa seleccionada y el canal con claves.
- Tests nuevos en `test_animation_handles`; **513 tests** en verde (505 + 8).

### B26 - Easing de las claves de animación (✅ · S)

Resultados y decisiones completas en [39 — Easing](39-easing.md).

- **Modelo v13**: `Keyframe.easing` (`linear`, `ease_in`, `ease_out`, `ease_in_out`) con
  migración v12 → v13 sin cambios visibles; el easing de cada tramo es el de su clave izquierda.
- **Curvas**: cúbicas puras (`k³`, `1-(1-k)³` y la in-out por tramos) en `core.animation.ease`;
  `value_at` y `expr` comparten la fórmula. La rama `linear` mantiene las cadenas históricas.
- **Preview/export**: los canales horneados (escala/rotación/tamaño) también aplican la curva
  porque leen `value_at`; X/Y/opacidad usan la expresión de FFmpeg por frame.
- **UI**: selector **Easing** en la sección Animación del inspector (no persistido); decide el
  easing de las nuevas claves y volver a grabar en el mismo instante lo actualiza. El desplegable
  no rompe las keys existentes.
- **Límite**: en esta entrega solo `Animation`; las envolventes de audio (`VolumePoint`) y el
  color animado de textos siguen lineales. El easing de las envolventes llegó en B27 y el color
  sigue interpolándose linealmente. No hay editor de curvas Bézier.
- Tests nuevos en `test_easing`; **512 tests** en verde.
- Tras integrar ambas ramas: **520 tests** en verde.

### B27 - Easing de las envolventes de volumen (✅ · S)

Resultados y decisiones completas en [40 — Easing de audio](40-easing-audio.md).

- **Modelo v14**: `VolumePoint.easing` (`linear`, `ease_in`, `ease_out`, `ease_in_out`) en
  `Clip.envelope` y `Element.envelope`, con migración v13 → v14 sin cambios visibles. El easing
  de cada tramo es el de su clave izquierda; la última clave no tiene tramo saliente.
- **Curvas**: `value_at` reutiliza `core.animation.ease` y `expr` las mismas multiplicaciones que
  `Animation.expr`; la rama `linear` conserva la expresión FFmpeg byte a byte.
- **Preview/export**: `build_audio` y `volume=…:eval=frame` leen la misma curva; hay una prueba con
  audio real que muestrea la ganancia en 0.25/0.5/0.75 s frente a la lineal.
- **UI**: selector **Easing** en las dos secciones de envolvente del inspector (clip y elemento),
  con `state.insp_env_easing` no persistido. Decide las claves nuevas (también las del modo Env) y
  volver a grabar en el mismo instante actualiza el easing. Arrastrar un asa lo conserva.
- **Límite**: no hay control para cambiar el easing de una clave seleccionada sin regrabarla (el
  copiado/pegado de claves llegó en B28). Cortar dentro de un tramo curvo reancla los extremos con
  `ease`, así que la subcurva no es exactamente la original (sí lo es en cortes lineales o en
  claves).
- Tests nuevos en `test_audio_envelope_easing`; **528 tests** en verde en su rama.

### B28 - Copiar y pegar claves (✅ · M)

Resultados y decisiones completas en [41 — Copiar y pegar claves](41-copiar-pegar-claves.md).

- **Sin cambios de modelo** (`SCHEMA_VERSION=14`): se reutilizan `animation.set_keyframe` y
  `audio_envelope.set_point`; el portapapeles es un `KeyClipboard` frozen en el controlador, no
  en `Project`.
- **Copiar** (`Ctrl+C` o menú del asa): con una clave visual seleccionada copia valor/easing; con
  un punto de envolvente copia la ganancia. Sin clave/punto seleccionado sigue copiando clips.
- **Pegar** (`Ctrl+V` o menú): usa la capa y el canal activos, convierte el playhead a tiempo local
  y acota a `0…duración`; reemplaza la clave del mismo instante o la inserta. El easing se
  conserva; un valor fuera del rango del canal o un playhead fuera del destino es un no-op con
  aviso. En audio se reemplaza/inserta el punto en el clip o video superpuesto seleccionado.
- **Undo**: `pegar clave X` o `pegar punto de volumen`, una entrada por pegado; los no-op no abren
  transacción. Claves y clips comparten un único portapapeles lógico (gana el último copiado).
- **Límite**: no crea claves nuevas desde el timeline, no convierte valores entre canales y deja
  fuera el color animado de textos. El easing de las envolventes se eligió en B27.
- Tests nuevos en `test_key_clipboard`; **531 tests** en verde en su rama (520 + 11).

> Las herramientas del editor que aún faltan (composición avanzada y export) se analizan en
> [18 — Herramientas del editor](18-herramientas-editor.md). **Lo siguiente de la fase 4** es
> completar composición/export; subtítulos, keyframes, pistas V2/V3 y las envolventes de audio
> de clips y elementos ya están hechos (con easing desde B27).

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

### F1 — Capacidades de Aurora 0.10 que aún no usamos (⏳ · variable)

Ordenadas por relación valor/coste (detalle en la tabla de docs/05):

| Capacidad | Qué desbloquearía | Tamaño |
|---|---|---|
| `on_drop` / `on_drop_hover` (0.6) | Importar videos o carpetas arrastrándolos a la Biblioteca/editor (**panel de medios hecho**, docs/27; falta soltar directo en el timeline) | S |
| `App.window` (varias ventanas) | **Overlay de región sobre el escritorio** (hoy es in-app) y ventana de preview desacoplada | M |
| `splitter` (0.7) | Sustituir los divisores propios del editor (inspector y preview/timeline) | M |
| `table(...)` (ordenación, columnas, virtualización) | La Biblioteca, hoy con `virtual_list` a mano | M |
| `chart(...)` | El sparkline de fps, hoy un `canvas` con `polyline` | S |
| `dialog`/`toast`/`progress`/`menu`/`accordion`/`field` | Sustituir las capas propias de toasts/modales/progreso | M |
| `remember="rec-studio"` (0.7) | Recordar tamaño/posición/maximizado de la ventana entre sesiones | S |
| `on_context_menu` y menús nativos (`App(menu=…)`) | Menú de app y clic derecho (hoy todo en la barra) | S |
| `overflow="hidden"` | Recortes que hoy se hacen con `scroll` de 0 px o canvas | S |
| `App(motion=False)` / `AURORA_MOTION=0` | Snapshots y CI deterministas | S |
| `aurora build --upx` | Bundle más pequeño (ver A5) | S |

### F2 — Propuestas a Aurora (💤 · según necesidad)

Salen de los golpes reales del proyecto (docs/05) y se proponen **cuando el proyecto lo justifica**,
no antes: `canvas.image()` (mataría la capa de `image()` absolutas del filmstrip),
`on_hover` (cursores por zona sin la capa de cajas), `scroll_to` (mantener el playhead a la vista al
hacer zoom) y `rect` del nodo en los handlers (hoy hay que deducir la x con `TL_X0` + scroll).

### F3 — Vigilar aurora-ui 0.11 (⏳ · S cada vez)

La dependencia tiene techo `<0.11` a propósito (Aurora es `0.x` y rompe entre menores). Subir de
versión es un trabajo **deliberado**: `uv lock` + suite completa + `tools/f3_editor_test.py` +
`tools/f4_export_test.py --skip-long` + `tools/check_build.py` + `aurora check main.py` + revisar la
tabla de limitaciones de docs/05. Ya pasó una vez: 0.3 → 0.5 cambió comportamiento sin avisar en el
lock (ver el commit de alineación). **0.5 → 0.9 hecho el 2026-10-31** sin cambios de API; **0.9 →
0.10 hecho el 2026-11-05**: 324 tests, F2/F3/F5 y build/smoke en verde. Los cambios de 0.10 son
compatibles salvo la validación de props (destapó cero errores en la app) y los avisos `no-label`,
que se resolvieron añadiendo `label=` a iconos, switches, campos y fondos de modal; la auditoría de
layout (`Pilot.layout()` sobre pantallas, modales, temas y 3 tamaños) queda en **0 avisos**.

---

## Priorización recomendada

Si hay que elegir un orden (y no simplemente el orden del documento):

1. **A2** — máquina limpia. Es donde aparecen los bugs que importan; el CI ya cubre instalar y el
   smoke del exe, pero no el flujo CU-1→CU-5 con monitor y audio.
2. **A6 + A7 + A8** — cerrar las comprobaciones manuales del bloque A (móvil, soak de 30 min y
   sensación del timeline); los procedimientos están en [docs/16](16-bloque-a-entrega.md).
3. **Fase 4 del editor (resto)** — PiP de video, subtítulos y keyframes; el canvas interactivo,
   los elementos (figuras/imágenes/blur) y los efectos globales ya están hechos
   ([23](23-canvas-resize.md), [25](25-elementos.md), [26](26-efectos-globales.md)).
4. **C1** — webcam PiP, el siguiente salto de producto.
5. **D1** — GIF/WebM para compartir en un clic.
6. **F1** — a medida que se toquen esas pantallas, ir sustituyendo capas propias por componentes de
   Aurora (empezando por `chart` y `overflow`, que son los más baratos).

**Fuera de este roadmap** (para no reabrirlo cada vez): telemetría, multi-ventana por capricho,
internacionalización sin traductor, y cualquier función que no tenga un caso de uso escrito en
docs/01.
