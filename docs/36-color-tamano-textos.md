# 36 — Color y tamaño animados de textos

> Implementado el **2026-11-09** sobre los keyframes de textos
> ([31 — Keyframes](31-keyframes.md)) y el bake de escala/rotación
> ([35 — Textos con escala y rotación](35-textos-escala-rotacion.md)). Añade el canal **tamaño**
> (`size`, 8…200) a `TextOverlay.animations` y un modelo aparte de **color animado**
> (`TextOverlay.color_keys`). **`SCHEMA_VERSION` pasa a 12** (migración v11 → v12 sin cambios
> visibles).

## Qué se puede hacer

- Animar el **tamaño** de un texto o subtítulo con dos o más claves. El valor es el tamaño base
  en píxeles del proyecto (`8…200`); la escala animada de [docs/35](35-textos-escala-rotacion.md)
  se sigue multiplicando por encima.
- Animar el **color** con dos o más claves `#RRGGBB`; la interpolación es lineal en sRGB.
- Combinar tamaño y color con X/Y, opacidad, escala y rotación en el mismo texto.
- Grabar una clave en el playhead con el valor actual de los campos **Tamaño** o **Color** del
  inspector, y borrar el canal con **Quitar**.
- El preview y el export comparten el mismo rasterizado (PIL) para cualquier texto con tamaño o
  color animados; los textos sin esos canales conservan el `drawtext`/PIL históricos.

## Contrato

- `TextOverlay.color_keys: tuple[ColorKeyframe, ...] = ()`; `SCHEMA_VERSION = 12`.
- `core.text_color`: `ColorKeyframe(t, color)`, `value_at(keys, t, default)`,
  `set_keyframe(keys, t, color)`, `clear(keys)` y `validate(keys)`.
- `timeline.text_style_values(overlay, t) -> (size, color)`; `text_values` conserva su firma de
  cinco valores.
- `services.text_animation.needs_bake(overlay)` incluye `size` y `color_keys`; el graph usa la
  misma condición (`anim.TEXT_BAKE_CHANNELS` + `color_keys`).
- Claves de UI estables: `insp-text-key-size`, `insp-text-clear-size`, `insp-text-key-color` e
  `insp-text-clear-color`.

## Modelo (`SCHEMA_VERSION = 12`)

```python
TextOverlay(
    text="Hola", start=0.0, end=2.5,
    size=20, color="#FF0000",              # valores base
    animations=(
        Animation("size", (Keyframe(0.0, 20.0), Keyframe(2.0, 60.0))),
    ),
    color_keys=(
        ColorKeyframe(0.0, "#FF0000"),
        ColorKeyframe(2.0, "#00FF00"),
    ),
)
```

- `core/animation.py`: `CHANNEL_RANGES["size"] = (8.0, 200.0)` y `TEXT_CHANNELS` pasa a ser
  `("x", "y", "opacity", "scale", "rotation", "size")`. Los demás canales no cambian.
- `TEXT_BAKE_CHANNELS = ("scale", "rotation", "size")`: los canales que `drawtext` no puede
  reproducir con el rasterizado de PIL. El color no es un `Animation`: va en `color_keys`.
- `core/text_color.py`:
  - `ColorKeyframe(t, color)` con `t` **local** al texto (0 = su `start`) y `color` `#RRGGBB`
    (se normaliza a mayúsculas).
  - `validate(keys)` exige tipos correctos, `t >= 0`, tiempos crecientes y color hex.
  - `value_at(keys, t, default)` acota a la primera/última clave e interpola **linealmente en
    sRGB** (cada byte 0…255) redondeando al entero más cercano; `rgb_at` devuelve la tupla.
  - `set_keyframe` añade/reemplaza una clave en el mismo instante y ordena; `clear` vacía la
    tupla.
- `TextOverlay.color_keys` por defecto `()`; `from_dict` lo lee con default `()` y `to_dict` lo
  escribe como `[{"t": …, "color": "#RRGGBB"}]`.
- `timeline.add_text` acepta `color_keys=` opcional y `timeline.update_text` acepta el campo
  `"color_keys"`.
- `timeline.text_values(overlay, t)` **no cambia**: sigue devolviendo
  `(x, y, opacity, scale, rotation)`. El estilo se evalúa con la función aparte
  `timeline.text_style_values(overlay, t) -> (size, color)`, que usa el preview y el bake.
- Los proyectos v11 no tienen `color_keys`: la migración v11 → v12 es un no-op y `from_dict`
  deja `()`, así que conservan su `color` base y se ven igual que antes.

## Preview (`services/playback.py`)

- `_text_layout` calcula el tamaño efectivo con `text_style_values`:
  `round(size_animado · alto_frame / alto_proyecto · escala)`, con mínimo 8 px, y el color
  efectivo de las claves.
- `text_rgba` es la única rasterización del texto con estilo animado: pinta con PIL usando ese
  tamaño y color, hornea opacidad y rotación y devuelve la imagen RGBA.
- `_draw_texts` usa `text_rgba` si el texto tiene claves de **escala, rotación, tamaño o color**;
  los textos sin esas animaciones conservan el dibujo directo sobre la capa RGBA (mismos píxeles
  de siempre).
- La posición sigue siendo el **centro** `(x, y)`. La caja de selección del preview se oculta
  también cuando el tamaño está animado (el arrastre editaría el tamaño base).

## Export: vídeo RGBA intermedio

`drawtext` no rota, no cambia `fontsize` con la fidelidad de PIL y no interpola color. Por eso
los textos con `size`, `scale`, `rotation` o `color_keys` se **hornean** igual que en
[docs/35](35-textos-escala-rotacion.md):

1. `services.text_animation.materialize` selecciona los textos visibles que necesitan bake
   (`needs_bake`: color_keys o canales de `TEXT_BAKE_CHANNELS`).
2. Calcula el lienzo máximo del intervalo evaluando `playback.text_extent` frame a frame
   (tamaño, escala y rotación incluidos).
3. Compone cada frame con `playback.text_rgba` (la misma función del preview) y lo codifica por
   stdin a **QuickTime Animation** (`qtrle`, `-pix_fmt argb`), que conserva el alfa sin pérdidas.
4. `core/graph._text_overlays` añade `-i <mov>`, `format=rgba,setpts=…,fps=…` y un `overlay` con
   `eof_action=pass`; X/Y siguen siendo expresiones `if(lt(t,…))`. El `enable` es
   `between(t,inicio,fin)*lt(t,fin)`, igual que el preview (`start <= t < end`).

- **Orden**: `Project.texts` se recorre en orden y cada texto se aplica sobre la etiqueta
  anterior; los estáticos usan `drawtext` y los animados el `overlay`, así que el apilado coincide
  con el preview (`ABAJO` → animado → `ARRIBA`).
- **Golden intacto**: si ningún texto tiene color/tamaño/escala/rotación, se usa la cadena
  histórica de `text_filters`; las cadenas de `drawtext` no cambian.
- **Fallo explícito**: si falta el `.mov` de un texto animado visible, `graph.build` lanza
  `ProjectError`; no se ignora una animación en silencio.
- **Limpieza**: los `.mov` viven en el directorio temporal `export-…`, que el `Exporter` borra
  en su `finally`.
- **Audio**: los textos no llevan audio; la mezcla no cambia.

## UI

- Inspector de texto: los campos existentes **Tamaño** (`insp-text-size`, 8…200) y **Color**
  (`insp-text-color`) siguen editando los valores base; no se añaden campos persistidos.
- Sección **Animación**:
  - filas X, Y, Opacidad, Escala, Rotación y **Tamaño** con «Clave …»/«Quitar»; la clave de
    tamaño usa el valor actual de `insp_text_size`;
  - fila **Color** con contador, **Clave color** y **Quitar**; usa el color actual de
    `insp_text_color`.
- Keys estables: `insp-text-key-size`, `insp-text-clear-size`, `insp-text-key-color` y
  `insp-text-clear-color`.
- La clave se graba en el **playhead local** al texto; si está fuera del intervalo, se avisa y
  no se crea. Mover el playhead no cambia el campo base.

## Límites

- Sin animación de color de elementos ni de clips; esta feature es solo de textos/subtítulos.
- El tamaño animado es el tamaño base: el rango efectivo sigue acotado por `TEXT_SIZE_MIN/MAX`
  (8…200) y luego se multiplica por la escala (0.05…4.0), así que el tamaño de fuente final
  puede superar 200 px.
- La interpolación de color es lineal en sRGB, sin corrección gamma ni easing. Las claves son
  explícitas: no se graban al mover el playhead.
- Un subtítulo con color/tamaño animado se hornea entero; el `.mov` sin pérdidas puede ser grande
  y la preparación es CPU (PIL), aunque se borra con el temporal del export.
- El corte rápido sigue bloqueado por `Project.texts`, como cualquier superposición.
- La exportación SRT no incluye animaciones (solo tiempos y texto).

## Tests y verificación

- `tests/test_text_color_size.py` (9):
  - round-trip de `color_keys`, `SCHEMA_VERSION == 12` y migración v11 → v12 sin curva;
  - validación de tiempos/colores y del rango `size` (8…200), y `set_keyframe`/`clear`;
  - interpolación de color en sRGB y `text_style_values` con tamaño/color;
  - preview real con píxeles rojos → verdes y área creciente por el tamaño;
  - **export real** con FFmpeg: píxeles en dos instantes y paridad con el preview;
  - texto sin color/tamaño animados: cadenas `drawtext` intactas (incluido `fontcolor`);
  - orden de textos estáticos y horneados, y `ProjectError` si falta el intermedio;
  - claves de color y tamaño desde el inspector con `pilot`, contador, undo y «Quitar».
- `uv run pytest -q`: **498 passed**.
- `uv run aurora check main.py`: OK (138 nodos).
- `uv run aurora tree main.py`: sin avisos de layout.
- Export real con FFmpeg 7.1 (imageio-ffmpeg) en Windows; la comparación de píxeles usa tolerancia
  del 35 % en el conteo por color (compresión H.264 y submuestreo de croma).

## Archivos tocados

- `src/recstudio/core/text_color.py` (nuevo: claves, validación e interpolación de color).
- `src/recstudio/core/animation.py` (canal `size`, `TEXT_BAKE_CHANNELS` y docstrings).
- `src/recstudio/core/project.py` (`TextOverlay.color_keys`, schema 12 y serialización).
- `src/recstudio/core/store.py` (migración v11 → v12).
- `src/recstudio/core/timeline.py` (`add_text`/`update_text` y `text_style_values`).
- `src/recstudio/core/graph.py` (bake de textos con color/tamaño).
- `src/recstudio/services/playback.py` (`text_rgba` con tamaño/color y ruta del preview).
- `src/recstudio/services/text_animation.py` (`needs_bake` con color/tamaño).
- `src/recstudio/ui/editor_controller.py` (claves de color/tamaño y métodos de color).
- `src/recstudio/ui/screens/editor.py` (fila Color, canal Tamaño y caja oculta).
- `tests/test_text_color_size.py` (nuevo) y ajustes de esquema en tests existentes.
- `docs/15-post-v1.md`, `docs/30`, `docs/31`, `docs/35`, `docs/README.md`, `CHANGELOG.md` y
  `AGENTS.md`.
