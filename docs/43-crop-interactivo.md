# 43 — Recorte interactivo en el preview

> Implementado el **2026-11-09** sobre el `Transform.crop` de la fase 2
> ([docs/20](20-fase-2-frame.md)) y el patrón de drags del canvas ([docs/23](23-canvas-resize.md)).
> **No cambia el modelo ni `SCHEMA_VERSION` (sigue en 15)**: se edita el `crop` normalizado
> `(l, t, r, b)` existente con las mismas operaciones puras de `core/timeline.set_transform`.
> Preview y export no cambian para el mismo estado; el recorte real lo siguen aplicando
> `core/transform.py`, `services/playback.py` y el filter graph.

## Qué se puede hacer

- Activar **Recortar en el preview** desde el `switch` de la sección **Transformación** del
  inspector de un clip o con la tecla **T**. Es un estado de UI **no persistido**
  (`state.crop_on`).
- Con un **clip de V1** seleccionado y el preview en pausa, el preview muestra el clip
  **sin recortar** y superpone:
  - un velo oscuro fuera del rectángulo de recorte;
  - el rectángulo del recorte con borde blanco;
  - **ocho asas**: cuatro esquinas y cuatro lados, con el cursor de redimensionado que toca.
- Arrastrar un asa ajusta el lado o los dos lados de la esquina; arrastrar **dentro** mueve el
  rectángulo completo sin cambiar su tamaño.
- Los campos **Recorte L/T/R/B** del inspector siguen funcionando y se actualizan tras el
  arrastre. El modo no se cierra al escribir en ellos.
- `Esc` cancela el gesto en curso; sin gesto, sale del modo. Cambiar de selección, entrar en
  razor (`C`), marquesina (`V`), Env (`E`) o «añadir clave» (`N`), o reproducir, también lo apaga.

## Modo y estado

| Pieza | Dónde |
|---|---|
| Señal de UI no persistida | `state.crop_on` |
| Geometría pura y proyecto de preview | `core/crop_edit.py` |
| Capa de velo/recuadro/asas | `ui/screens/editor.py` → `_crop_layer` |
| Drags, preview sin recortar y undo | `ui/editor_controller.py` |
| `switch` del inspector y ayuda | `ui/screens/editor.py` → `_transform_controls` |
| Atajo | `T` en `EditorController.on_key` |
| Tests | `tests/test_crop_interactive.py` |

El modo es **excluyente** con las otras herramientas de ratón: al activarlo se apagan razor,
marquesina, Env y «añadir clave»; al activar cualquiera de ellas se apaga el recorte. Con
`Esc` se sale; con `V` se vuelve a la herramienta normal y también se apaga.

La tecla **T** estaba libre: no la usan J/K/L, R, E, B, M, I, O, P, Q, W, C, V ni los atajos con
Ctrl/Alt. En un campo de texto enfocado no se intercepta, como el resto de letras sueltas.

### Cuándo no se activa

- Sin proyecto abierto.
- Sin un clip de V1 seleccionado (con un elemento o un texto seleccionado avisa y no entra).
- Con el proyecto en **solo lectura** o la pista de video **bloqueada**.
- Mientras el preview se está **reproduciendo**: hay que pausar. Al arrancar la reproducción con
  el modo activo, se cierra solo.
- Si el preview aún no se ha medido (primer frame), las asas no se dibujan; en cuanto llega el
  tamaño del escenario aparecen.

## Geometría

### El preview muestra el clip completo

Mientras el modo está activo, el controlador entrega al `Player` una **copia** del proyecto con
el `crop` del clip seleccionado en `(0, 0, 1, 1)` (`crop_edit.uncropped_project`). Esa copia
**no se guarda ni se aplica al store**: `state.editor_project`, la serialización y el undo
conservan el recorte real. Si el clip no tenía `Transform`, el preview ya lo mostraba completo y
no se crea ninguna copia.

Así el usuario ve la imagen de origen sobre la que está recortando y el recuadro puede ocupar
todo el clip. Al salir del modo, el reproductor vuelve a recibir el proyecto real y el preview
muestra el resultado recortado.

### Mapeo `crop` ↔ escenario

`crop_edit.full_layout` calcula el `Layout` de `core/transform.py` con el `crop` completo. El
rectángulo del recorte es una fracción lineal de esa imagen ya escalada:

```python
left   = layout.x + l * layout.out_width
top    = layout.y + t * layout.out_height
right  = layout.x + r * layout.out_width
bottom = layout.y + b * layout.out_height
```

Es exacto para `fit`, `fill`, `stretch`, cualquier `scale`/posición y también para **rotación y
flip**: el `crop` se aplica después de la rotación/volteo del clip, así que las fracciones se
miden sobre la imagen ya transformada que se está viendo. No hay casos especiales para 90/270.

El escenario del preview (`core/canvas_edit.fit_stage`) es la referencia de píxeles. Si el
recuadro se sale del escenario (un `fill` o `scale > 1`), se dibuja solo la intersección visible
y las asas se meten dentro del escenario para que sigan siendo pulsables
(`crop_edit.visible_box` y `handle_centres`). La geometría del arrastre sigue usando el layout
real, no la caja recortada por el escenario.

### Límites por lado y movimiento

- `MIN_CROP = 0.05`: cada lado conserva al menos un **5 %** de ancho/alto; el recorte nunca queda
  degenerado.
- Todos los valores se acotan a `0…1`; `Transform.__post_init__` sigue siendo la última defensa.
- **Mover** no cambia el tamaño: el desplazamiento se acota a lo que queda hasta el borde y el
  rectángulo se pega al lado correspondiente.
- La geometría se calcula siempre desde el **crop del `begin`** y los deltas acumulados del
  ratón, así que el clamp no acumula deriva. Los helpers puros son `move_crop`, `resize_crop`,
  `drag_crop`, `crop_box`, `visible_box` y `handle_centres`.

## Gestos, undo y cancelación

- `begin_crop_drag(handle)` guarda el proyecto, el layout completo, el crop inicial y el asa.
  `handle` es `move` o uno de `tl/tr/bl/br/t/b/l/r`.
- La transacción **no se abre hasta el primer movimiento real** (`_CLICK_SLOP_PX = 3 px`,
  `begun=False`). Un clic que solo activa o selecciona no deja `ProjectStore._tx` abierta ni crea
  undo.
- Cada `move` hace `store.update` desde el proyecto del `begin`, con
  `tl.set_transform(..., crop=...)`. Al soltar se confirma **una sola** entrada con la etiqueta
  `recortar clip`; si el resultado no cambió, `commit` no crea historial.
- `Esc` pasa por `EditorController.cancel_drag` → `_cancel_crop_drag`: restaura el snapshot,
  repinta y deja el modo activo. Un segundo `Esc` sale del modo.
- `Ctrl+Z`/`Ctrl+Y` cancelan el gesto en curso como el resto de drags.
- Si el proyecto es de solo lectura o la pista está bloqueada, el gesto no empieza.

## Límites

- Solo el clip de **V1** seleccionado. Los elementos, textos y el audio no tienen `crop`.
- Solo se edita el `Transform.crop`; no hay máscara, proporción fija, rotación del recuadro ni
  recorte por keyframes.
- El **zoom heredado** (`Effect("zoom")`) se aplica después del crop en el render. El recuadro
  edita el crop del modelo, así que con `zoom > 1` el encuadre final incluye además ese zoom; la
  guía visual sigue siendo el rectángulo del `crop`.
- Si el playhead no está en el clip seleccionado, la capa de recorte se oculta para no dibujar
  asas sobre otro frame. Al activar el modo, si hace falta, el editor salta al inicio del clip.
- El recuadro visible se recorta contra el escenario: si el crop cae fuera por un `fill`/`scale`
  extremo, las asas se quedan en el borde visible (la edición sigue siendo correcta).
- No hay atajo para «recortar a la selección» ni detección automática de bordes.

## Tests y verificación

`tests/test_crop_interactive.py` (14):

- geometría pura: mapeo normalizado → rectángulo del escenario, incluido escenario reducido y
  rotación 90 + flips; `visible_box` y las ocho asas dentro del escenario;
- mínimo del 5 %, clamp, movimiento sin cambiar tamaño y asa inválida;
- `uncropped_project` no muta el modelo, conserva la serialización y devuelve el mismo proyecto
  si no hay transformación;
- **pilot**: activar muestra velo/recuadro/8 asas y oculta las cajas normales; el `switch` del
  inspector activa y desactiva el modo; arrastre de esquina, lado e interior con undo/redo y
  actualización de los campos L/T/R/B;
- clic sin movimiento: ni undo ni transacción abierta;
- `Esc` cancela el gesto y después sale del modo; cambio de selección y herramientas exclusivas;
- con elemento/texto seleccionado avisa y no se activa;
- el preview sin recortar no persiste nada, el frame se repinta y los campos siguen funcionando;
- la caja del `crop` coincide con el layout completo del escenario.

Verificación: `uv run pytest -q` → **559 passed** (545 base + 14), `uv run aurora check main.py`
OK y `uv run aurora tree main.py` sin avisos.

## Archivos tocados

- `src/recstudio/core/crop_edit.py` (geometría pura y copia de preview sin recortar).
- `src/recstudio/state.py` (`crop_on`; estado de UI no persistido).
- `src/recstudio/ui/editor_controller.py` (modo, preview sin recortar, drags, undo, `Esc`,
  exclusividad y atajo `T`).
- `src/recstudio/ui/screens/editor.py` (`switch` del inspector, capa de velo/recuadro/asas,
  nota del preview y ayuda de la barra).
- `tests/test_crop_interactive.py`.
- `docs/43-crop-interactivo.md`, `docs/15`, `docs/20`, `docs/27`, `docs/README.md`,
  `CHANGELOG.md` y `AGENTS.md`.
