# 31 — Keyframes de posición y opacidad

> Implementado el **2026-11-05** sobre textos y elementos (docs/25, docs/28 y docs/29).
> Añade curvas lineales por canal, evaluadas en el preview y traducidas a expresiones de FFmpeg
> en el export. Solo se animan los canales que FFmpeg puede evaluar por frame: **X/Y** de textos
> y elementos y **opacidad** de textos. La escala y la rotación de elementos llegaron después con
> un vídeo intermedio ([docs/33](33-escala-rotacion.md)), y las de textos con el mismo bake
> ([docs/35](35-textos-escala-rotacion.md)); no forman parte de este documento.

## Qué se puede hacer

- Añadir claves a un **elemento** (imagen, figura, PiP o clip V2/V3): **X** e **Y**.
- Añadir claves a un **texto/subtítulo**: **X**, **Y**, **Opacidad**, **Escala**, **Rotación** y
  **Tamaño** (las tres últimas, desde [docs/35](35-textos-escala-rotacion.md) y
  [docs/36](36-color-tamano-textos.md)); el color animado va en un modelo aparte
  (`TextOverlay.color_keys`, [docs/36](36-color-tamano-textos.md)).
- La clave se graba en el **playhead actual** con el valor que tenga el campo en ese momento.
- Repetir en otro instante para crear la curva; el preview y el export interpolan linealmente.
- **Quitar** una animación de un canal con un clic.
- El timeline marca con **◆** las capas con animación; en el preview, la caja de manipulación se
  oculta cuando X/Y (o, desde [docs/33](33-escala-rotacion.md), escala/rotación) están animados.

## Modelo (`SCHEMA_VERSION = 9`)

```python
Keyframe(t=0.0, value=0.2)     # t local: 0 = inicio del elemento/texto
Animation(
    channel="x",               # x | y | opacity | scale | rotation | size (textos)
    points=(Keyframe(0.0, 0.2), Keyframe(1.0, 0.8)),
)
Element(..., animations=(...))
TextOverlay(..., animations=(...))
```

- `points` se ordenan por `t`; no puede haber dos claves en el mismo instante.
- Todos los valores van de **0 a 1** (x/y son fracciones del lienzo; opacity es 0…1).
- Antes de la primera clave vale la primera; después de la última, la última. Entre claves,
  interpolación **lineal**.
- `core/animation.py` es puro: `value_at`, `expr`, `set_keyframe`, `clear_channel` y `validate`.
- Los elementos admiten `x`/`y`/`scale`/`rotation` (los dos últimos llegaron en
  [docs/33](33-escala-rotacion.md)); los textos admiten `x`/`y`/`opacity` y, desde
  [docs/35](35-textos-escala-rotacion.md) y [docs/36](36-color-tamano-textos.md),
  `scale`/`rotation`/`size`. El color de un texto no es un `Animation` (los valores son floats):
  vive en `color_keys` ([docs/36](36-color-tamano-textos.md)). Un elemento con `opacity` animada
  lanza `ProjectError` (no se puede exportar con el filtro actual).
- Los proyectos v8 se migran a v9 sin cambios (`animations=()`).

## Preview

- **Elementos**: `core.elements.effective(element, t)` devuelve una copia con `x`/`y` animados y
  la usa `core/overlays.apply_frame` y `playback._video_element_image` para la caja y el overlay.
- **Textos**: `timeline.text_values(overlay, t)` devuelve `(x, y, opacity, scale, rotation)`;
  `_draw_texts` aplica posición y opacidad sobre una capa RGBA y, si hay escala/rotación, usa
  `text_rgba` para rasterizar el texto transformado ([docs/35](35-textos-escala-rotacion.md)).
- La interpolación es la misma función que usa el export, así que no hay dos fórmulas.

## Export

- **Elementos**: el `overlay` usa expresiones en `t` para `x`/`y`:
  ```
  overlay=x='main_w*if(lt(t,0),0.2,if(lt(t,1),0.2+(0.8-0.2)*(t-0)/1,0.8))-overlay_w/2'
  ```
- **Textos**: `drawtext` usa `x='...'`, `y='...'` y `alpha='...'` con la misma expresión (tiempo
  global; la clave local 0 equivale a `start`). Si el texto tiene escala o rotación, se hornea a
  un vídeo RGBA y X/Y pasan a expresiones de `overlay` ([docs/35](35-textos-escala-rotacion.md)).
- Sin animación, las cadenas son **las de siempre** (los golden de textos y elementos no cambian).
- La expresión se acota en los extremos y se evalúa por frame; verificado con export real de
  posición de un PiP y de opacidad de un texto.

## UI

- Inspector de elemento/texto → sección **Animación**:
  - una fila por canal con el número de claves;
  - **Clave X/Y/Opacidad**: graba el valor actual del campo en el playhead;
  - **Quitar**: borra todas las claves de ese canal.
- La sección recuerda: «la clave usa el valor actual del campo; mover el playhead no cambia el
  campo». Para cambiar una curva se coloca el playhead, se ajusta el campo base y se vuelve a
  grabar en ese instante.
- La barra del timeline añade **◆** cuando la capa tiene animación.
- La caja del preview se oculta si la capa tiene geometría animada (X/Y y, en elementos, escala o
  rotación); el timeline y el inspector siguen funcionando.

## Límites

- No hay animación de **color** en elementos ni de la transformación de un clip de V1. Los
  textos añadieron color y tamaño en [docs/36](36-color-tamano-textos.md); los elementos
  ampliaron los canales con **escala y rotación** en [docs/33](33-escala-rotacion.md) y los
  textos en [docs/35](35-textos-escala-rotacion.md).
- Interpolación **lineal**; no hay curvas Bézier/easing ni copiar/pegar claves.
- Las claves son **explícitas**: no hay modo «grabar automáticamente al mover el playhead».
- La caja del preview no se muestra para capas con geometría animada; la posición y la escala se
  editan con el inspector.
- El corte rápido sigue bloqueado por elementos/textos/efectos (docs/18).

## Tests

- `tests/test_animation.py` (5): interpolación y límites, expresión FFmpeg, alta/actualización/
  borrado de claves, `effective` y canales permitidos, y `drawtext` con opacidad estática/animada.
- `tests/test_animation_export.py` (2): preview + export real de un PiP que se mueve y de un texto
  que se desplaza y se apaga; texto animado solo en X conserva la opacidad.
- `tests/test_text_transform.py` (9): escala/rotación de textos en modelo, preview y export real.
- `tests/test_text_color_size.py` (9): tamaño/color de textos en modelo, preview, export real y UI.
- `tests/test_project_panel.py`: claves de elemento y de texto (incluida escala/rotación) desde el
  inspector, con undo.
- `uv run pytest -q`: **479 passed**.
- `uv run aurora check main.py`: OK.

## Archivos tocados

- `src/recstudio/core/animation.py` (modelo, interpolación y expresiones).
- `src/recstudio/core/project.py` (`animations`, schema 9, serialización).
- `src/recstudio/core/store.py` (migración v8 → v9).
- `src/recstudio/core/elements.py` (`effective`, `add/update` con animaciones).
- `src/recstudio/core/timeline.py` (`text_values`, `update_text`).
- `src/recstudio/core/graph.py` (expresiones en `drawtext`).
- `src/recstudio/core/overlays.py` (expresiones en `overlay`).
- `src/recstudio/services/playback.py` (preview animado).
- `src/recstudio/state.py` (`insp_text_opacity`).
- `src/recstudio/ui/editor_controller.py`, `ui/screens/editor.py`, `ui/timeline.py`.
- `tests/test_animation.py`, `tests/test_animation_export.py`, `tests/test_project_panel.py`.
