# 22 — Plan de features del editor (v6)

> Análisis y contrato de integración para cuatro features pedidas sobre la v1.1.0 + fases 0–3:
> **(A)** redimensionar un clip sobre el canvas del preview, **(B)** pistas de video y audio
> separadas por defecto pero vinculadas, **(C)** elementos superpuestos (figuras, imágenes y
> efectos como blur) y **(D)** efectos globales sobre todo el video.
>
> La **base v6** está implementada en `main`: `SCHEMA_VERSION = 6`, `Project.elements`,
> `Project.effects`, `Element`, `MediaAsset.kind` y los hooks `core/overlays.py` +
> `core/global_effects.py`. Las cuatro features se desarrollaron en worktrees independientes y se
> integraron después; resultados en [23 — Canvas](23-canvas-resize.md),
> [24 — Pistas A/V](24-pistas-av.md), [25 — Elementos](25-elementos.md) y
> [26 — Efectos globales](26-efectos-globales.md). Este documento conserva el contrato y el
> reparto de archivos que se usó para que los merges no se pisaran.

## Diagnóstico de partida

| Lo pedido | Lo que ya existe | Lo que falta |
|---|---|---|
| A. Resizar un clip en el canvas | `Transform` (fit/fill/stretch, escala, x/y, rotación, flip, crop), inspector numérico y preview con la misma geometría (docs/20) | Interacción directa sobre el preview: caja del clip, asas, arrastre y un undo por gesto |
| B. Video/audio separados y vinculados | `Clip.audio_enabled`, `Track.mode="absolute"`, `detach_audio`/`attach_audio`, `link_id`, pista «Audio del video» y dibujo de la banda A | Que la separación sea **automática al importar** y que mover/recortar/dividir/borrar el video mantenga el audio enlazado en su sitio |
| C. Figuras, imágenes y blur | Textos con intervalo/posición y `drawtext`/PIL; assets de video/audio | `Element` (base v6), importación de imágenes, geometría, z-order, composición en preview y export, blur y edición |
| D. Efectos sobre todo el video | `Effect` por clip y `eq`/fades/zoom | `Project.effects` (base v6), filtros globales, paridad preview/export y controles en el inspector |

El editor sigue siendo un NLE mínimo: una pista de video secuencial, pistas de audio
secuenciales/absolutas y un render FFmpeg por clip. Las cuatro features son **composición y
flujo**, no una reescritura del motor. La regla dura se mantiene: **lo exportado = lo visto**, y
toda matemática nueva vive en `core/` para que preview y graph la compartan.

## Base v6 (ya en `main`)

- `SCHEMA_VERSION = 6`; migración `v5 → v6` en `core/store.py` (sin tocar lo visible).
- `MediaAsset.kind`: `"video" | "audio" | "image"`. Una imagen tiene `duration=0`, `fps=0` y
  `has_audio=False`; no se puede añadir como clip (`timeline.append_clip` lo rechaza).
- `Element`: imagen o figura con intervalo, centro y tamaño normalizados, rotación, opacidad,
  color/asset, `fit` y efectos. `Project.elements` está ordenado por z-order (el último va encima).
- `Project.effects`: tupla de `Effect` globales (como mucho uno de cada tipo).
- Hooks de composición:
  - `core/overlays.py`: `apply_graph(...)` y `apply_frame(...)`.
  - `core/global_effects.py`: `apply_graph(...)` y `apply_frame(...)`.
  - El graph aplica **efectos globales → elementos → textos**; el preview hace lo mismo.
- `core/keyframes.blockers` bloquea el corte rápido con elementos o efectos globales.
- Tests de la base: `tests/test_modelo_v6_base.py`.

## A — Redimensionar el clip en el canvas

**Objetivo:** seleccionar un clip y ver su caja sobre el preview; arrastrar el centro para
moverlo y las asas para escalarlo (manteniendo el aspecto), con un solo undo por gesto y sin
tocar el modelo de datos (ya existe `Clip.transform`).

**Alcance mínimo**
- Capa de interacción sobre el preview, alineada con el rectángulo real del lienzo (no con el
  panel entero). El preview actual usa `image(..., fit="contain")`; para colocar asas hace falta
  medir el panel (`on_resize`) y calcular el rectángulo del proyecto con su aspecto.
- Caja visible solo con un clip seleccionado y el preview en pausa (o siempre; decidir y
  documentar). Asas en las cuatro esquinas + cuerpo; opcionalmente rotación.
- Arrastrar el cuerpo cambia `Transform.x/y`; arrastrar una esquina cambia `Transform.scale`
  (anclada a la esquina opuesta o al centro, decidir y documentar). Respetar 0.25…4 y los
  límites del lienzo.
- Un `store.begin/update/commit` por drag ⇒ **un undo**. `Esc` cancela.
- La caja debe seguir al clip durante el drag y no debe tapar los clics del preview cuando no
  hay selección.
- Sin cambios de esquema. Si hace falta, añadir utilidades puras en `core/transform.py` o un
  módulo nuevo (`core/canvas_edit.py`) con tests.
- El inspector numérico sigue siendo la alternativa accesible.

**Criterios de aceptación**
- Con un clip seleccionado, `pilot.drag` sobre un asa cambia `transform.scale` y el frame del
  preview; undo vuelve al valor anterior en un paso.
- Arrastrar el cuerpo cambia x/y y el clip se mueve en preview y export (misma geometría).
- Un clic sin movimiento no crea una entrada de undo.
- Funciona con el proyecto en 16:9 y 9:16 y con el inspector oculto (`P`).
- Tests de UI con `build_app()` + `pilot` (sin ventana) y tests puros de la matemática.

**Riesgos:** Aurora 0.10 no expone el rectángulo de un nodo en los handlers; la posición del
lienzo se calcula con el tamaño medido del panel y el aspecto del proyecto. Evitar bucles de
`on_resize` (solo escribir el `Signal` si cambia).

## B — Pistas de video y audio separadas por defecto, vinculadas

**Objetivo:** al importar un video con audio, el timeline muestra una pista de video y una pista
de audio propia («Audio del video»), enlazadas: mover, recortar, dividir o borrar el video
mantiene el audio sincronizado. El usuario puede desvincular el audio para editarlo libremente.

**Alcance mínimo**
- Al crear un proyecto o importar clips, separar automáticamente el audio de cada clip con audio
  (equivalente a `detach_audio`, pero en una sola transacción por importación). Una sola pista
  «Audio del video» en modo `absolute` con los clips enlazados (`link_id`).
- Sincronización del enlace en las operaciones de video:
  - mover/reordenar en la pista secuencial: el audio enlazado se recoloca en el nuevo inicio
    (sin solapes; si no cabe, cancelar con aviso);
  - recortar por la izquierda/derecha: el audio enlazado ajusta su `start`/`in_`/`out_` según
    corresponda (misma regla que el video, sin desplazar audio no enlazado);
  - dividir: ambos lados (ya lo hace `split_all`);
  - borrar: cascada (ya lo hace `_remove_linked_audio`).
- Comando explícito **Desvincular** (mantiene el audio en su pista y limpia `link_id`, sin borrar
  nada) y **Vincular** (el actual `attach_audio` reincorpora el audio al video). El botón actual
  «Separar audio» puede quedar como acción manual para un clip concreto.
- La pista enlazada se dibuja como cualquier pista absoluta, con el indicador «↔»; la cabecera
  puede llamarse «Audio del video».
- El corte rápido sigue bloqueado con audio separado (ya lo hace).
- **No** cambiar la semántica de `Project.audio` ni el esquema; usar `link_id`, `start` y
  `audio_enabled`. No tocar la matemática de mezcla salvo lo necesario.

**Criterios de aceptación**
- Importar un video con audio deja el video mudo (`audio_enabled=False`) y un clip de audio
  enlazado en «Audio del video» en la misma posición.
- Preview y export suenan exactamente igual que antes de separar (mismo offset, sin duplicar).
- Mover el clip de video a otra posición mueve su audio; undo en un paso.
- Recortar/dividir/borrar el video mantiene la sincronía o cancela con un aviso claro.
- Desvincular y volver a vincular conserva el audio y no crea clips duplicados.
- Proyecto guardado/reabierto idéntico; migración v6 intacta.
- Tests puros de timeline, de importación y de UI (un caso de mover y uno de recortar).

**Riesgo principal:** el video es una secuencia magnética y el audio enlazado es absoluto. Hay que
decidir qué pasa al mover audio enlazado por su cuenta: recomendación **desvincularlo
implícitamente con aviso** (o pedir confirmación) en vez de mover el video a ciegas. Documentar la
decisión.

## C — Elementos superpuestos: figuras, imágenes y blur

**Objetivo:** añadir imágenes y figuras (rectángulo, elipse, triángulo) sobre el video, con
intervalo, posición, tamaño, rotación, opacidad, z-order y efectos (blur como mínimo), editables
desde el inspector y el preview, y con paridad preview/export.

**Alcance mínimo**
- Importar imágenes (`PNG/JPG/WebP/BMP`) con `media.probe_image` → `MediaAsset(kind="image")`.
  Añadirlas al proyecto sin convertirlas en clips. El diálogo de importación debe aceptar
  imágenes además de videos.
- Panel/sección «Elementos» en el inspector sin selección, con lista de elementos, añadir figura
  (rect/elipse/triángulo) y añadir imagen, seleccionar, borrar y editar:
  inicio/fin (o duración), x/y, ancho/alto, rotación, opacidad, color (figuras), fit (imágenes) y
  blur.
- Composición en preview y export:
  - geometría pura compartida en `core/elements.py` (o `core/overlays.py`), en píxeles del
    lienzo de salida, igual que `core/transform.py`;
  - preview con PIL (RGBA, rotación, opacidad, blur gaussiano/box);
  - export con FFmpeg: una entrada por imagen (o PNG de figura cacheado), `scale`, `rotate`,
    `colorchannelmixer=aa=...`, `gblur`/`boxblur`, `overlay=...:enable='between(t,a,b)'`;
  - z-order = orden de `Project.elements`;
  - los elementos van **después** de los efectos globales y **antes** de los textos.
- Las figuras se rasterizan con Pillow a un PNG transparente cacheado (en `services/`), porque
  el graph es puro y no puede generar imágenes. La clave de caché incluye tipo, color, tamaño y
  radio de blur; usar escritura atómica (tmp + `os.replace`) como las miniaturas.
- Los efectos de elemento se guardan en `Element.effects` (`blur` con `radius`); validar ranges
  razonables (0…50 px en el espacio del lienzo).
- Edición sobre el preview: mover/redimensionar el elemento seleccionado. Si la feature A ya
  ofrece una capa reutilizable, integrarse con ella; si no, una capa propia mínima.
- El corte rápido se bloquea con elementos (ya en la base).

**Criterios de aceptación**
- Añadir un rectángulo rojo semitransparente con blur en 1–4 s: se ve en el preview en ese
  intervalo y el MP4 exportado coincide en posición, tamaño, color, opacidad y desenfoque.
- Importar un PNG con transparencia: se compone respetando el alfa, el fit y el z-order.
- Un elemento fuera de su intervalo no aparece; dos elementos se apilan en el orden esperado.
- Borrar/deshacer y guardar/reabrir conservan los elementos y sus efectos.
- Tests puros de geometría y validación, test de frame sintético de preview, golden del graph y
  **export real** de 1 s con un elemento (preferiblemente con blur).
- `uv run pytest -q` y `uv run aurora check main.py` sin avisos nuevos.

**Riesgo principal:** el filtro `overlay` de FFmpeg exige que la imagen de entrada tenga alfa y
que las etiquetas de entrada estén bien numeradas. Medir y documentar el orden exacto; si el blur
con alfa no funciona en `gblur`, usar `boxblur` o pre-multiplicar el alfa en el PNG cacheado.

## D — Efectos globales sobre todo el video

**Objetivo:** aplicar efectos al video completo (no por clip) desde el inspector del proyecto, con
paridad preview/export y sin romper el corte rápido (se bloquea).

**Alcance mínimo**
- Usar `Project.effects` (base v6). Implementar en `core/global_effects.py` al menos:
  - **Color**: brillo/contraste/saturación globales (reutilizar las curvas de `core/effects.py`);
  - **Blur** global (radio en px del lienzo);
  - **Viñeta** (intensidad);
  - opcional: **grayscale** o **sharpen** si el coste es razonable.
- Orden: después de cortes/transiciones/transformaciones y antes de elementos/textos.
- Graph: filtros FFmpeg encadenados sobre la etiqueta de video compuesta (`eq`, `gblur`,
  `vignette`, `hue=s=0`…). Preview: mismas curvas en numpy/PIL, sin duplicar la matemática cuando
  sea posible.
- UI en el inspector sin selección, sección «Efectos globales»: controles de color, blur y
  viñeta; un solo undo por cambio; «Restablecer efectos globales».
- El corte rápido se bloquea si hay efectos globales (ya en la base).
- Tests: modelo/serialización, filtros del graph, frame sintético de preview (canales o
  cuadrantes), export real de 1 s con un efecto, UI con `pilot` y undo.

**Criterios de aceptación**
- Aplicar blur global y exportar: el MP4 se ve desenfocado en todo el metraje, también en
  transiciones; el preview coincide.
- Cambiar brillo/contraste/saturación global se refleja al instante en el preview y en el export.
- Un proyecto sin efectos globales genera **exactamente** el mismo filter graph que antes
  (golden de F4 intacto).
- Guardar/reabrir conserva los efectos; undo en un paso.
- `uv run pytest -q` y `uv run aurora check main.py` sin avisos nuevos.

## Reparto de archivos y merges

| Feature | Dueña principal | Evitar tocar |
|---|---|---|
| A · canvas resize | `ui/screens/editor.py` (preview), `ui/editor_controller.py`, `state.py`, `core/transform.py`/`core/canvas_edit.py`, `ui/canvas_edit.py`, tests A | `core/project.py`, `core/store.py`, `core/graph.py` |
| B · A/V linked | `ui/editor_controller.py` (import/move/trim/unlink), `core/timeline.py`, `ui/timeline.py` (indicador), tests B | `core/project.py`, `core/graph.py`, `core/keyframes.py` |
| C · elementos | `core/overlays.py`, `core/elements.py` nuevo, `services/media.py` (probe_image), `services/element_assets.py` nuevo, `core/graph.py` (solo la llamada ya existe), `services/playback.py` (solo la llamada ya existe), `ui/screens/editor.py` (sección Elementos), `state.py`, tests C | `core/global_effects.py`, `core/project.py` (la base ya tiene el modelo) |
| D · global effects | `core/global_effects.py`, `core/graph.py` (solo la llamada ya existe), `services/playback.py` (solo la llamada ya existe), `ui/screens/editor.py` (sección Proyecto), `state.py`, tests D | `core/overlays.py`, `core/project.py` |

Reglas para las sesiones:

1. Trabajar **solo** en el worktree asignado; no tocar `main` ni los worktrees de otras features.
2. Una rama por feature, commits en español con prefijo `feat:`; el commit final debe dejar
   `uv run pytest -q` verde.
3. Documentación: crear `docs/23-canvas-resize.md`, `docs/24-pistas-av.md`,
   `docs/25-elementos.md` o `docs/26-efectos-globales.md` según corresponda. **No** editar
   `docs/15-post-v1.md`, `AGENTS.md` ni `CHANGELOG.md`: eso se hace en la integración final para
   evitar conflictos.
4. No cambiar `SCHEMA_VERSION` (ya es 6) ni la firma pública de `Project`/`Element` de la base.
   Si hace falta un campo nuevo, documentedlo en el doc de la feature y decidlo en el commit;
   la integración lo revisará.
5. Tests nuevos en archivos propios (`tests/test_fase6_*.py`), sin renombrar los existentes.
6. Respetar las reglas del proyecto: `core/` puro, UI sin IO/subprocess en handlers, `Signal`
   solo con datos frozen, un undo por gesto, preview y export con la misma matemática.
7. Al terminar, resumir: archivos tocados, tests ejecutados, decisiones, límites y cualquier
   conflicto previsible con las otras features.

## Orden de integración previsto

1. **B** (audio enlazado) — toca el flujo de importación y el timeline; conviene integrarlo antes
   de que A/C/D añadan complejidad encima.
2. **A** (canvas) — independiente del modelo; su capa de preview puede reutilizarse en C.
3. **C** (elementos) — depende de la base v6 y, si existe, de la capa de interacción de A.
4. **D** (efectos globales) — toca el hook de composición; se integra al final para revisar el
   orden final (global → elementos → textos).

Cada merge debe repetir `uv run pytest -q`, `uv run aurora check main.py` y, si toca render,
`uv run python tools/f4_export_test.py --skip-long --no-shots`. Al final se actualizan
`docs/15-post-v1.md`, `AGENTS.md` y `CHANGELOG.md` con el conjunto.
