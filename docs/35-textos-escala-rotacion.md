# 35 — Escala y rotación animadas de textos

> Implementado el **2026-11-05** sobre los keyframes de textos ([31 — Keyframes](31-keyframes.md))
> y el bake de elementos ([33 — Escala y rotación](33-escala-rotacion.md)).
> Añade los canales **escala** (multiplicador del `size`) y **rotación** (grados horarios
> absolutos) a `TextOverlay.animations`. **No cambia `SCHEMA_VERSION` en su momento (siguió en
> 10)** ni añade campos al proyecto: se reutilizan `TextOverlay.animations` y dos campos de
> inspector no persistidos. El color y el tamaño animados llegaron después con el modelo v12
> ([36 — Color y tamaño de textos](36-color-tamano-textos.md)).

## Qué se puede hacer

- Animar la **escala** de un texto o subtítulo con dos o más claves. `1.0` es el tamaño base y
  `0.05…4.0` el rango permitido; la escala multiplica `TextOverlay.size`.
- Animar la **rotación** en grados horarios absolutos (`-360…360`). El texto no tiene rotación
  base: una clave de `0°` equivale a «sin rotación».
- Combinar escala y rotación con X/Y y opacidad en el mismo texto.
- Grabar una clave en el playhead con el valor de los campos **Escala** o **Rotación** del
  inspector, y borrar el canal con **Quitar**.
- El preview y el export usan el **mismo rasterizado** (PIL) para el texto transformado; la
  interpolación usa el easing de la clave izquierda (por defecto lineal, [docs/39](39-easing.md))
  y se acota a la primera/última clave.

## Modelo (`SCHEMA_VERSION = 10`)

```python
TextOverlay(
    text="Hola",
    start=0.0, end=2.5,
    animations=(
        Animation("scale", (Keyframe(0.0, 1.0), Keyframe(2.0, 2.0))),
        Animation("rotation", (Keyframe(0.0, 0.0), Keyframe(2.0, 45.0))),
    ),
)
```

- `core/animation.py` valida por canal (`CHANNEL_RANGES`): `x`/`y`/`opacity` `0…1`,
  `scale` `0.05…4.0`, `rotation` `-360…360`.
- `TEXT_CHANNELS` pasó a `("x", "y", "opacity", "scale", "rotation")`; desde
  [docs/36](36-color-tamano-textos.md) incluye también `"size"`. `TextOverlay` ya validaba contra
  esa tupla, así que no hubo campos ni migraciones nuevas en esta feature.
- `core.timeline.text_values(overlay, t)` devuelve ahora
  `(x, y, opacity, scale, rotation)`, con los valores por defecto `scale=1.0` y `rotation=0.0`.
- La serialización no cambia (`channel` + `points` con `t`/`value`): un proyecto v10 anterior
  se abre igual y un texto con estos canales se guarda sin tocar el esquema.

## Preview (`services/playback.py`)

- `text_rgba(overlay, t, frame_size, project_height=…)` es la **única** rasterización del texto
  transformado. Calcula el tamaño de fuente (`size · alto_frame / alto_proyecto · escala`),
  el contorno de 2 px escalado, la opacidad y la rotación; dibuja el texto en una imagen RGBA
  aislada y la rota con `core.overlays.rotate_rgba` (misma convención horaria que FFmpeg).
- `_draw_texts` conserva el camino actual para los textos **sin** claves de escala/rotación:
  se dibujan directamente sobre la capa RGBA como hasta ahora. Solo los transformados pasan
  por `text_rgba` y se componen centrados en `(x·ancho, y·alto)` con recorte contra el lienzo.
- La posición sigue siendo el **centro** `(x, y)`; `text_extent` calcula el tamaño ya rotado
  para que el bake reserve el lienzo máximo del intervalo.
- La caja de selección del preview se oculta también cuando el texto tiene claves de escala o
  rotación, igual que con X/Y (`ui/screens/editor.py`): la caja seguiría la animación pero el
  arrastre editaría el valor base.

## Export: vídeo RGBA intermedio

`drawtext` no rota el texto por frame y su `fontsize` no comparte el rasterizado de PIL. Por eso
los textos con `scale`/`rotation` se **hornean** con la misma técnica que los elementos
([docs/33](33-escala-rotacion.md)):

1. `services/text_animation.materialize` selecciona los textos con claves de escala/rotación
   visibles en el timeline y calcula el lienzo máximo que necesitan en todo su intervalo
   (evaluando `playback.text_extent` frame a frame).
2. Cada frame se compone con `playback.text_rgba` (la función del preview) sobre ese lienzo fijo.
3. Los frames RGBA se envían por stdin a FFmpeg y se codifican con **QuickTime Animation**
   (`qtrle`, `-pix_fmt argb`), que conserva el alfa sin pérdidas.
4. `core/graph._text_overlays` añade una entrada `-i <mov>`, la cadena
   `format=rgba,setpts=PTS-STARTPTS+<inicio>/TB,fps=<fps>` y un `overlay` con `eof_action=pass`.
   La opacidad y la rotación ya vienen horneadas; X/Y se aplican con las mismas expresiones
   `if(lt(t,…))` que los elementos (`overlay=x='…':y='…'`). El `enable` es
   `between(t,inicio,fin)*lt(t,fin)`, igual que el preview (`start <= t < end`).

### Orden de los textos

`Project.texts` se recorre en orden y cada texto se aplica sobre la etiqueta anterior: los
textos sin transformar siguen usando un `drawtext` individual y los transformados un `overlay`.
Así el apilado coincide con el preview aunque haya textos estáticos intercalados
(`ABAJO` → animado → `ARRIBA` en los tests). Si **ningún** texto tiene escala/rotación, se usa
la cadena histórica de `text_filters`: los golden de `drawtext` quedan intactos.

Consecuencias:

- **Paridad real**: preview y export comparten `text_rgba`; solo difieren en la codificación del
  intermedio (`qtrle`, sin pérdidas) y en la resolución de salida.
- **Fallo explícito**: si falta el `.mov` de un texto animado, `graph.build` lanza
  `ProjectError`; nunca se ignora una animación en silencio.
- **Limpieza**: los `.mov` viven en el directorio temporal `export-…`, que el `Exporter` borra
  en su `finally`; no se acumulan en la cache de elementos.
- **Audio**: los textos no llevan audio; la mezcla no cambia.

## UI

- Inspector de texto → **Escala** (`insp-text-scale`, `0.05…4.0`, inicial `1.0`) y **Rotación**
  (`insp-text-rotation`, `-360…360`, inicial `0.0`). No se persisten: solo son el valor que se
  grabará en la siguiente clave, como el campo de escala de los elementos.
- Sección **Animación**: filas X, Y, Opacidad, **Escala** y **Rotación** con
  «Clave …» y «Quitar». Keys estables: `insp-text-key-scale`, `insp-text-key-rotation`,
  `insp-text-clear-scale` y `insp-text-clear-rotation`.
- La clave usa el valor del campo en el **playhead**; si está fuera del texto, se avisa. Los
  campos de escala/rotación se reinician al cambiar de texto seleccionado.
- El timeline mantiene el **◆** para cualquier capa con animación.

## Límites

- Sin animación de **color** ni de fuente **en este documento**; la escala es un multiplicador del
  `size` y la rotación es absoluta (no hay rotación base en `TextOverlay`). El color y el tamaño
  animados se añadieron en [docs/36](36-color-tamano-textos.md).
- El tamaño de fuente efectivo se acota a 8 px, como el texto estático (`_font`), así que una
  clave de escala `0.05` sobre un texto pequeño puede no reducirse más.
- Interpolación con easing por clave ([docs/39](39-easing.md)); las claves son explícitas y no se
  graban al mover el playhead.
- El bake es CPU (PIL) y se hace una vez por texto y export; un subtítulo largo con muchas
  claves tarda más en la fase de preparación. El `.mov` sin pérdidas puede ser grande, pero se
  borra con el temporal.
- El corte rápido sigue bloqueado por `Project.texts`, como cualquier superposición.
- Los textos sin escala/rotación (ni color/tamaño animados, docs/36) mantienen el rasterizado de
  `drawtext` en el export (no se hornean), así que su diferencia de antialias con el preview es la
  que ya existía.

## Tests y verificación

- `tests/test_text_transform.py` (9):
  - canales/rangos y `text_values` con escala/rotación;
  - preview real de un texto que escala y rota (área y centro) y de una rotación a 90°;
  - **export real** con `.mov` `qtrle`, muestreo de píxeles y comparación preview/export;
  - el bake conserva el alfa (opacidad 0.5 → 128);
  - proyecto sin transformar: cadena `drawtext` intacta;
  - orden de textos estáticos y horneados, con expresión de X animada;
  - `ProjectError` si falta el intermedio y limpieza del temporal del `Exporter`.
- `tests/test_project_panel.py`: claves de escala y rotación de texto desde el inspector, con
  undo y «Quitar».
- `uv run pytest -q`: **479 passed**.
- `uv run aurora check main.py`: OK.
- `uv run aurora tree main.py`: sin avisos de layout.
- Export real con FFmpeg 7.1 (imageio-ffmpeg) en Windows; la comparación usa tolerancia de 5 px
  en la caja y 22 % en el área.

## Archivos tocados

- `src/recstudio/core/animation.py` (`TEXT_CHANNELS`, `TRANSFORM_CHANNELS` y docstrings).
- `src/recstudio/core/project.py` (comentario del modelo; sin campos nuevos).
- `src/recstudio/core/timeline.py` (`text_values` con escala/rotación).
- `src/recstudio/core/overlays.py` (`TextClip`, alias del vídeo RGBA intermedio).
- `src/recstudio/core/graph.py` (`text_filter`, `_text_overlays`, `text_clips`).
- `src/recstudio/services/playback.py` (`text_rgba`, `text_extent` y preview).
- `src/recstudio/services/rgba_video.py` (nuevo: `qtrle` y nombres seguros compartidos).
- `src/recstudio/services/text_animation.py` (nuevo: bake y limpieza).
- `src/recstudio/services/element_animation.py` (usa `rgba_video`; sin cambios de conducta).
- `src/recstudio/services/export.py` (prepara y pasa los textos horneados).
- `src/recstudio/state.py` (`insp_text_scale`/`insp_text_rotation`).
- `src/recstudio/ui/editor_controller.py`, `ui/screens/editor.py` (campos, claves y caja oculta).
- `tests/test_text_transform.py` (nuevo) y `tests/test_project_panel.py`.
- `docs/15-post-v1.md`, `docs/25`, `docs/30`, `docs/31`, `docs/33`, `docs/README.md`,
  `CHANGELOG.md` y `AGENTS.md`.
