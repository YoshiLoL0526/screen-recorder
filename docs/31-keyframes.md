# 31 — Keyframes de posición y opacidad

> Implementado el **2026-11-05** sobre textos y elementos (docs/25, docs/28 y docs/29).
> Añade curvas lineales por canal, evaluadas en el preview y traducidas a expresiones de FFmpeg
> en el export. Solo se animan los canales que FFmpeg puede evaluar por frame: **X/Y** de textos
> y elementos y **opacidad** de textos.

## Qué se puede hacer

- Añadir claves a un **elemento** (imagen, figura, PiP o clip V2/V3): **X** e **Y**.
- Añadir claves a un **texto/subtítulo**: **X**, **Y** y **Opacidad**.
- La clave se graba en el **playhead actual** con el valor que tenga el campo en ese momento.
- Repetir en otro instante para crear la curva; el preview y el export interpolan linealmente.
- **Quitar** una animación de un canal con un clic.
- El timeline marca con **◆** las capas con animación; en el preview, la caja de manipulación se
  oculta cuando X/Y están animados (el inspector sigue editando la base).

## Modelo (`SCHEMA_VERSION = 9`)

```python
Keyframe(t=0.0, value=0.2)     # t local: 0 = inicio del elemento/texto
Animation(
    channel="x",               # x | y | opacity
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
- Los elementos solo admiten `x`/`y`; los textos admiten `x`/`y`/`opacity`. Un elemento con
  `opacity` animada lanza `ProjectError` (no se puede exportar con el filtro actual).
- Los proyectos v8 se migran a v9 sin cambios (`animations=()`).

## Preview

- **Elementos**: `core.elements.effective(element, t)` devuelve una copia con `x`/`y` animados y
  la usa `core/overlays.apply_frame` y `playback._video_element_image` para la caja y el overlay.
- **Textos**: `timeline.text_values(overlay, t)` devuelve `(x, y, opacity)`; `_draw_texts` los
  aplica sobre una capa RGBA (la opacidad se compone sin tocar el resto del frame).
- La interpolación es la misma función que usa el export, así que no hay dos fórmulas.

## Export

- **Elementos**: el `overlay` usa expresiones en `t` para `x`/`y`:
  ```
  overlay=x='main_w*if(lt(t,0),0.2,if(lt(t,1),0.2+(0.8-0.2)*(t-0)/1,0.8))-overlay_w/2'
  ```
- **Textos**: `drawtext` usa `x='...'`, `y='...'` y `alpha='...'` con la misma expresión (tiempo
  global; la clave local 0 equivale a `start`).
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
- La caja del preview se oculta si X/Y están animados para no mostrar una posición distinta de la
  que se está editando; el timeline y el inspector siguen funcionando.

## Límites

- No hay animación de **escala, rotación, tamaño, color** ni de la transformación de un clip de
  V1. Solo X/Y (texto y elemento) y opacidad (texto).
- Interpolación **lineal**; no hay curvas Bézier/easing ni copiar/pegar claves.
- Las claves son **explícitas**: no hay modo «grabar automáticamente al mover el playhead».
- La caja del preview no se muestra para capas con X/Y animados; la posición se edita con el
  inspector.
- El corte rápido sigue bloqueado por elementos/textos/efectos (docs/18).

## Tests

- `tests/test_animation.py` (5): interpolación y límites, expresión FFmpeg, alta/actualización/
  borrado de claves, `effective` y canales permitidos, y `drawtext` con opacidad estática/animada.
- `tests/test_animation_export.py` (2): preview + export real de un PiP que se mueve y de un texto
  que se desplaza y se apaga; texto animado solo en X conserva la opacidad.
- `tests/test_project_panel.py`: claves de elemento y de opacidad de texto desde el inspector,
  con undo.
- `uv run pytest -q`: **446 passed**.
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
