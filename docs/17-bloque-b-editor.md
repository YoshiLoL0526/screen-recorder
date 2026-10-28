# 17 — Bloque B: editor completo

> Implementado el **2026-10-28** sobre la v1.0.0 y el bloque A. Cubre B1–B6 de
> [docs/15](15-post-v1.md#bloque-b--editor-lo-hecho): música con ducking y crossfades, textos,
> selección múltiple, snap por waveform, proxy de preview y reubicar carpetas.

## Resumen

| # | Pieza | Estado | Dónde |
|---|---|---|---|
| B1 | Música, ducking y crossfades | ✅ | `core/project.py`, `core/graph.py`, `services/playback.py`, `ui/editor_controller.py`, `ui/timeline.py` |
| B2 | Textos y títulos | ✅ | `core/project.py`, `core/graph.py`, `services/playback.py`, inspector |
| B3 | Selección múltiple | ✅ | `ui/timeline.py`, `ui/editor_controller.py`, `state.py` |
| B4 | Snap de audio | ✅ | `services/media.py`, `core/timeline.py`, `ui/editor_controller.py` |
| B5 | Proxy automático | ✅ | `services/media.py`, `services/playback.py`, `ui/editor_controller.py` |
| B6 | Reubicar carpetas | ✅ | `ui/editor_controller.py`, inspector |

**227 tests** en verde (19 nuevos de B). El CI no cambia de forma: `pytest` + build + instalador.

## B1 — Música, ducking y crossfades

- `Track` gana `ducking` (0…1, solo pistas de audio) y `Project.audio` se dibuja en una pista **M**
  propia, debajo de la pista A del video.
- **Importar música**: botón ♪ de la barra del editor o «Añadir música…»; acepta audio suelto
  (WAV/MP3/M4A…) o un video (usa su pista de audio). Se añade a la primera pista de música; si no
  hay, se crea. El probe de audio sin video (`media.probe`) devuelve `width=height=0`, `fps=0`.
- Inspector de música: volumen de pista, **volumen del clip** seleccionado, **ducking**
  (Sin/Suave/Medio/Fuerte/Máximo) y **crossfade** entre clips (0–2 s, acotado a la mitad del clip
  más corto).
- **Export**:
  - los clips de música se unen con `acrossfade` cuando hay transición y con `concat` cuando hay
    corte;
  - `ducking > 0` aplica `sidechaincompress` con el audio del video (voz/sistema) como sidechain y
    `ratio = 2 + 10·ducking`: la música baja sola cuando hay voz;
  - `apad`/`atrim` llevan la pista a la duración exacta del video.
- **Preview**: si hay pistas de audio extra, `AudioPreview` pide a FFmpeg la mezcla completa
  (`core.graph.build_audio`) y reproduce ese WAV cacheado por hash del proyecto. Así el preview
  incluye música, crossfades y ducking, no solo el audio del video.

Uso: importar un video → ♪ → elegir música → en el inspector ajustar volumen/ducking/crossfade.
El criterio de aceptación se cubre con `tests/test_bloque_b.py` (WAV de preview y export real con
música, texto y ducking).

## B2 — Textos y títulos

- `Project.texts` es una tupla de `TextOverlay` (inmutable): texto, `start`/`end`, centro `x`/`y` en
  fracciones del frame, tamaño en píxeles del proyecto, color `#RRGGBB` y negrita.
- Barra del editor → botón **T** añade «Título» en el playhead durante 3 s (o hasta el final). El
  inspector permite editar texto, inicio/fin, tamaño, color, posición, estilo y borrarlo. Los
  textos existentes se listan en el inspector de proyecto para volver a seleccionarlos.
- **Preview**: `FrameRenderer` dibuja los textos activos con PIL sobre el frame RGB, a la misma
  posición/escala relativa (fuente Arial; fallback al font por defecto de Pillow).
- **Export**: `drawtext` después de componer todo el video, con `enable='between(t,…)'`,
  `expansion=none` y el texto escapado. Se aplica al tamaño de salida (720p incluido).
- El corte rápido (`sin recodificar`) se bloquea si hay textos: requieren recodificar.

## B3 — Selección múltiple

- Botón de **marquesina** en la barra del timeline (icono de rectángulo discontinuo). Con la
  herramienta activa, arrastrar sobre el timeline selecciona todos los clips que solapan el rango.
  El rectángulo se dibuja en el canvas y los clips seleccionados se resaltan.
- `Supr` borra todos los seleccionados con **un solo undo** (`tl.ripple_delete_many`).
- `Ctrl+A` selecciona todos los clips; `Esc` limpia la selección.

Limitación consciente: mover en lote no se implementó; el arrastre mueve el clip bajo el cursor
(el criterio de B3 era marquesina + borrado con un undo).

## B4 — Snap de audio por waveform

- `media.waveform_peaks` elige los máximos locales por encima de un umbral (0.3) y devuelve tiempos
  de la fuente.
- `tl.audio_snap_candidates` los mapea al timeline con `in_`/velocidad de cada clip, incluidos los
  de la pista de música.
- `begin_move`/`begin_trim` cachean los picos de los assets con waveform ya calculada; `drag_move`
  y `drag_trim` añaden esos instantes a los candidatos del imán. El umbral sigue siendo 8 px, así
  que se siente igual a cualquier zoom.

## B5 — Proxy automático para el preview

- `media.is_heavy(asset)` → alto > 1080 o fps > 60. Al abrir/importar/reubicar se lanza
  `media.proxy` en un hilo: H.264 540p **sin audio** en `cache/proxies`, cacheado por ruta+tamaño+
  mtime y altura.
- `media.preview_path` devuelve el proxy si está listo; `FrameRenderer` lo usa para el decode. El
  **export sigue usando el archivo original**. Si el proxy falla, el preview usa el original.
- Medición local (2 s de 4K60 sintético): original **5.9 ms/frame** (170 fps) → proxy **1.3 ms/
  frame** (788 fps), **4.6×**; el proxy tardó 0.6 s en generarse. El criterio de ≥ 24 fps se cumple
  con margen incluso sin GPU.

## B6 — Reubicar carpetas

- En el inspector de proyecto (con assets offline) o en el clip offline: **«Reubicar carpeta…»**.
  Busca recursivamente por nombre de archivo (sin distinguir mayúsculas) en la carpeta elegida,
  prueba cada coincidencia con `media.probe` y sustituye **todos** los assets que falten en una
  sola operación de undo. Después recalcula waveforms, miniaturas y proxy.
- Si un nombre aparece en varias subcarpetas, se usa la primera coincidencia; el toast indica
  cuántos se resolvieron de cuántos faltaban.

## Archivos principales

| Archivo | Cambio |
|---|---|
| `core/project.py` | `TextOverlay`, `Track.ducking`, `Project.texts`, schema v2, serialización |
| `core/graph.py` | sección de audio reutilizable, `acrossfade`, `sidechaincompress`, `drawtext`, `build_audio` |
| `core/timeline.py` | `add_text`/`update_text`/`remove_text`, `ripple_delete_many`, `audio_snap_candidates` |
| `core/keyframes.py` | los textos bloquean el corte rápido |
| `services/media.py` | probe de audio, `waveform_peaks`/`audio_peaks`, proxy 540p y cache |
| `services/playback.py` | textos en el preview y mezcla WAV para el audio del preview |
| `ui/editor_controller.py` | importar música, inspector, textos, marquesina, snap, proxy y reubicar carpeta |
| `ui/timeline.py` | pista M, marquesina, selección múltiple |
| `ui/screens/editor.py` | botones ♪/T/selección y secciones de inspector |
| `ui/icons.py` | iconos MUSIC, TEXT y SELECT |

## Tests y verificación

- `tests/test_bloque_b.py` (14): modelo/JSON, textos, borrado múltiple, snap, probe de audio,
  proxy, graph de música/ducking/texto, `build_audio`, preview de texto y **export real** con
  FFmpeg.
- `tests/test_bloque_b_ui.py` (5): importar música + pista M, añadir/editar texto, marquesina con
  undo único, snap en un pico de audio y reubicar una carpeta con dos assets.
- Suite completa: **227 tests** (`uv run pytest`).

## Pendientes / límites

- B1: una sola pista de música y los clips son secuenciales (no se arrastran a posiciones
  arbitrarias); no hay fade manual por clip, solo crossfade entre clips.
- B2: texto de una línea (sin ajuste automático), fuente fija Arial y posición por selects (sin
  arrastrar sobre el preview).
- B3: no hay movimiento en lote; solo borrado.
- B4: el snap usa la waveform cacheada; si aún no llegó, no hay picos hasta que termine el hilo.
- B5: el proxy se genera para >1080p o >60 fps; un 1080p con códec muy exigente no lo usa.
- B6: colisiones de nombre se resuelven con la primera coincidencia.
