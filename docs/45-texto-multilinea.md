# 45 — Texto multilínea con ancho máximo

> Implementado el **2026-11-09** sobre textos/subtítulos ([docs/30](30-subtitulos.md)) y el
> rasterizado compartido de [docs/35](35-textos-escala-rotacion.md) y
> [docs/36](36-color-tamano-textos.md). Añade `TextOverlay.wrap_width` (modelo **v15**), un
> helper puro de ajuste de línea y el horneado de los textos envueltos para que preview y export
> compartan exactamente el mismo corte.

## Qué se puede hacer

- **Ajustar automáticamente** un texto o subtítulo largo a un ancho máximo del lienzo. El texto
  se parte por palabras y las palabras que no caben se rompen por caracteres.
- **Respetar saltos explícitos** (`\n`) dentro del texto, además de los que introduce el ajuste.
- Elegir el ancho desde el inspector con **Ancho máx.**: `0` = sin límite (comportamiento
  histórico), `0.1…1.0` = fracción del ancho del lienzo.
- Ver el bloque completo en el preview (líneas centradas horizontalmente y bloque centrado en
  `(x, y)`) y en la caja de selección, que cubre todas las líneas.
- Exportar con el mismo aspecto: los textos envueltos se **hornean** a un vídeo RGBA con PIL y
  se superponen con FFmpeg. Los textos de una línea sin `wrap_width` siguen usando `drawtext`
  con las cadenas históricas byte a byte.

## Modelo (`SCHEMA_VERSION = 15`)

```python
TextOverlay(
    text="hola mundo largo", start=0.0, end=2.5,
    x=0.5, y=0.5, size=30, color="#FFFFFF",
    wrap_width=0.3,   # fracción del ancho del lienzo; 0 = sin límite
)
```

- `wrap_width` es un `float` en `[0, 1]`. Si es `> 0`, el mínimo es `0.1`
  (`TEXT_WRAP_MIN`): anchos menores no son útiles y se rechazan en el modelo; el inspector los
  normaliza a `0.1` y `0`/valores no finitos vuelven a «sin límite».
- `to_dict` escribe `wrap_width` siempre; `from_dict` lo lee con default `0.0`, así que un
  proyecto v14 se abre sin límite y se ve exactamente igual que antes.
- `core/store.py` añade la migración **v14 → v15** no-op: los textos antiguos no tienen el campo
  y `from_dict` lo deja en `0.0`.
- `timeline.add_text(..., wrap_width=...)` y `timeline.update_text(..., wrap_width=...)` aceptan
  el campo; el resto de campos y canales no cambia.

## Helper puro (`core/text_layout.py`)

```python
wrap_text(text, *, size, wrap_width, project_width) -> tuple[str, ...]
```

- Sin PIL, sin disco y determinista: `core/` sigue siendo puro.
- Normaliza CRLF/CR, respeta los `\n` explícitos, colapsa los espacios de cada párrafo y
  conserva las líneas en blanco entre dos `\n` (ocupan su interlineado).
- `wrap_width <= 0`, `size <= 0` o un proyecto sin ancho: no ajusta; solo limpia espacios y
  respeta los `\n`.
- Con ajuste, el máximo en píxeles es `wrap_width * project_width`. El reparto es voraz por
  palabras; una palabra más ancha que el máximo se parte en trozos que quepan (con al menos un
  carácter por trozo).
- El ancho se **estima** con una tabla de anchos relativos de Arial (`estimate_width`, en em):
  no es una medida tipográfica exacta, pero preview y export usan el mismo helper, así que el
  corte es idéntico en ambos. El ancho real puede diferir en unos píxeles del máximo.
- `LINE_HEIGHT = 1.15`: el interlineado es `1.15 × tamaño efectivo` en píxeles de cada salida.

## Preview (`services/playback.py`)

- `_text_layout` obtiene las líneas con `wrap_text` usando el **tamaño efectivo del proyecto**
  (`text_style_values`, con tamaño animado incluido, multiplicado por la escala animada) y el
  ancho del proyecto. Así el corte no depende de la resolución del preview ni de la salida y el
  bloque respeta el máximo también cuando la escala lo agranda.
- Calcula la caja de cada línea con la fuente real (Arial regular/negrita), las desplaza
  `1.15 × size` y une los rectángulos: el bloque queda centrado en `(x, y)`, igual que antes
  estaba centrada la única línea.
- `text_rgba` dibuja **todas** las líneas en una sola imagen RGBA y aplica rotación, escala,
  opacidad y color como hasta ahora. Es la única rasterización del texto envuelto.
- `_draw_texts` usa `text_rgba` si el texto tiene animaciones de escala/rotación/tamaño o
  `color_keys`, **o si es multilínea** (`wrap_width > 0` o `\n`). Los textos de una línea sin
  wrap ni animaciones que exijan bake conservan el dibujo directo y sus píxeles históricos.
- `ui/element_overlay.text_box` usa el mismo corte y la misma fuente: la caja de selección cubre
  el bloque completo y su centro sigue siendo `(x, y)`.

## Export: horneado de los textos envueltos

`drawtext` no ajusta líneas y su métrica no coincide con la de PIL. Por eso los textos
multilínea se **hornean** con la misma estrategia que los animados:

1. `services.text_animation.needs_bake` devuelve `True` si hay `color_keys`, canales
   `scale`/`rotation`/`size` animados, `wrap_width > 0` o un `\n` explícito.
2. `materialize` selecciona los textos visibles que lo necesitan y calcula el lienzo máximo
   evaluando `playback.text_extent` frame a frame.
3. Cada frame se compone con `playback.text_rgba` (la función del preview) y se codifica por
   stdin a **QuickTime Animation** (`qtrle`, `-pix_fmt argb`), que conserva el alfa.
4. `core/graph._text_overlays` añade `-i <mov>`, `setpts`, `fps` y un `overlay` con
   `eof_action=pass`; X/Y siguen siendo expresiones `if(lt(t,…))` si están animados. El
   `enable` es `between(t,inicio,fin)*lt(t,fin)`, igual que el preview.

- **Orden**: `Project.texts` se recorre en orden y cada texto se aplica sobre la etiqueta
  anterior. Los estáticos usan `drawtext` y los envueltos/animados el `overlay`, así que el
  apilado coincide con el preview.
- **Golden intacto**: si ningún texto es multilínea ni tiene animaciones no dibujables, se usa
  la cadena histórica de `text_filters` y las cadenas de `drawtext` no cambian.
- **Fallo explícito**: si falta el `.mov` de un texto envuelto visible, `graph.build` lanza
  `ProjectError`; no se ignora el ajuste en silencio.
- **Limpieza**: los `.mov` viven en el directorio temporal del export, que el `Exporter` borra
  en su `finally`.
- **Audio**: los textos no llevan audio; la mezcla no cambia.

## UI

- Inspector de texto → **Ancho máx.** (`insp-text-wrap`, `0…1`, paso `0.05`, dos decimales).
  `0` = sin límite. El valor vive en `state.insp_text_wrap` (Signal no persistido) y el
  controlador lo escribe en `TextOverlay.wrap_width` con undo (`editar texto`).
- Si el campo emite un valor entre `0` y `0.1`, el controlador lo normaliza a `0.1`; si emite
  `0`, un valor negativo o no finito, vuelve a `0` (sin límite). El proyecto nunca queda en un
  estado inválido.
- Los subtítulos importados siguen funcionando: `cues_to_texts` crea `TextOverlay` con
  `wrap_width=0.0` por defecto, así que se ven igual que antes. El parser SRT/VTT sigue uniendo
  las líneas del cue con espacios; el modelo ya admite multilínea, pero el import no cambia.
- La caja de selección del preview cubre el bloque completo (varias líneas), no solo la primera.

## Migración y compatibilidad

- Proyectos v14: sin `wrap_width` → `0.0`; preview y export idénticos a antes (una línea,
  `drawtext` o bake animado si ya lo tenían).
- Proyectos v15 con `wrap_width > 0`: preview y export usan el mismo helper y la misma
  rasterización; solo cambia la codificación del intermedio (`qtrle`, sin pérdidas) y la
  resolución de salida.
- Un texto con `\n` y `wrap_width=0` también se hornea: el preview dibuja las líneas y el
  export no puede colapsarlas en `drawtext` sin romper la paridad. Es un cambio deliberado
  respecto a v14 (antes el `\n` se convertía en espacio al exportar).

## Límites

- El ajuste es **aproximado**: la estimación de Arial no mide el kerning real. El corte es
  estable y compartido, pero el ancho visual puede diferir en unos píxeles del máximo.
- El interlineado es fijo (`1.15 × size`); no hay control de interlineado ni de alineación
  (todas las líneas van centradas horizontalmente).
- No hay edición multilínea en el campo de texto del inspector (Aurora usa un `text_input` de
  una línea); los `\n` se pueden introducir desde el modelo/API o importando un proyecto.
- Los textos envueltos se hornean enteros: un subtítulo largo con muchas líneas tarda más en la
  fase de preparación y el `.mov` sin pérdidas puede ser grande (se borra con el temporal).
- El corte rápido sigue bloqueado por `Project.texts`, como cualquier superposición.
- La exportación SRT no incluye `wrap_width` (solo tiempos y texto).

## Tests y verificación

- `tests/test_text_multiline.py` (10):
  - wrap por palabras y límites, `\n` explícitos, espacios colapsados y palabras largas;
  - serialización, validación (`0`, `0.1…1.0`, rechazo de `0.05`/negativos/NaN), `add_text`/
    `update_text` y migración v14 → v15;
  - preview real de tres líneas, caja de selección que las cubre y texto de una línea sin wrap
    con `drawtext` histórico;
  - orden de textos estáticos y envueltos;
  - **export real** con FFmpeg: el texto envuelto aparece en `text_clips`, no usa `drawtext` y
    los bounds/centro/área coinciden con el preview;
  - UI con `pilot`: campo `insp-text-wrap`, escritura con undo y normalización de valores.
- `uv run pytest -q`: **555 passed**.
- `uv run aurora check main.py`: OK.
- `uv run aurora tree main.py`: sin avisos de layout.
- `git diff --check`: limpio.
- Export real con FFmpeg 7.1 (imageio-ffmpeg) en Windows; comparación con tolerancia de 6 px en
  los bounds, 4 px en el centro y 35 % en el área (compresión H.264 y submuestreo de croma).

## Archivos tocados

- `src/recstudio/core/text_layout.py` (nuevo: wrap puro, estimación y `breaks_lines`).
- `src/recstudio/core/project.py` (`TextOverlay.wrap_width`, validación y schema 15).
- `src/recstudio/core/store.py` (migración v14 → v15 no-op).
- `src/recstudio/core/timeline.py` (`add_text`/`update_text` con `wrap_width`).
- `src/recstudio/core/graph.py` (`_text_needs_bake` incluye multilínea).
- `src/recstudio/services/playback.py` (layout multilínea, `text_rgba` y preview).
- `src/recstudio/services/text_animation.py` (`needs_bake` y paso del ancho del proyecto).
- `src/recstudio/state.py` (`insp_text_wrap`).
- `src/recstudio/ui/editor_controller.py` (Signal, normalización y sincronización).
- `src/recstudio/ui/screens/editor.py` (campo **Ancho máx.** y ayuda).
- `src/recstudio/ui/element_overlay.py` (caja del bloque completo).
- `tests/test_text_multiline.py` (nuevo) y ajustes de esquema en tests existentes.
- `docs/15-post-v1.md`, `docs/30`, `docs/31`, `docs/35`, `docs/36`, `docs/README.md`,
  `CHANGELOG.md` y `AGENTS.md`.
