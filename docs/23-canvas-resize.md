# 23 — Redimensionar y mover el clip sobre el canvas (feature A)

> Implementado sobre la base v6 de [22 — Plan de features](22-plan-features-editor.md). No cambia
> el esquema (`SCHEMA_VERSION = 6`): usa el `Clip.transform` de la fase 2.

## Resumen

| Pieza | Dónde |
|---|---|
| Matemática pura de mover/escalar y geometría del escenario | `core/canvas_edit.py` |
| Medida del hueco del preview (`on_resize`) | `state.PreviewArea`, `state.preview_area` |
| Escenario de tamaño fijo + capa de cajas absolutas | `ui/screens/editor.py` → `preview_panel`, `_canvas_layer` |
| Drags, transacción de undo y cancelación con `Esc` | `ui/editor_controller.py` → `begin_canvas_drag`, `drag_canvas`, `end_canvas_drag`, `cancel_drag` |
| Tests puros y de UI | `tests/test_fase6_canvas.py`, `tests/test_fase6_canvas_ui.py` |

Con un clip de video seleccionado y el preview en pausa aparece su caja sobre el lienzo. Arrastrar
el **cuerpo** cambia `Transform.x`/`y` (centro en fracciones del lienzo); arrastrar una de las
**cuatro esquinas** cambia `Transform.scale` manteniendo el aspecto. Todo el gesto es **un solo
undo**; un clic sin movimiento no toca el historial y `Esc` lo revierte.

## Geometría

### Escenario medido

El preview deja de ser una `image(fit="contain")` que ocupa el panel entero: ahora el cuerpo del
preview mide su hueco con `on_resize` (`state.preview_area`, un `PreviewArea` frozen) y calcula un
**escenario** de tamaño fijo con el aspecto del proyecto (`canvas_edit.fit_stage`). El escenario se
centra y la imagen se pinta con `fit="fill"` al 100 %, así los píxeles del escenario y los de la
imagen coinciden. El panel exterior sigue siendo negro; el fondo del proyecto lo pinta el escenario.

`on_resize` solo escribe el `Signal` si el tamaño redondeado cambia. El escenario se calcula hacia
abajo (`floor`) para no pasarse del hueco medido y no realimentar el layout. Mientras no hay medida
(primer frame), se mantiene el `fit="contain"` histórico; en cuanto llega la medida se cambia al
escenario fijo.

La caja del clip sale de `core.transform.layout(project, clip, (stage_w, stage_h))`: `x`, `y`,
`out_width` y `out_height` en píxeles del preview. El escenario lleva `overflow="hidden"`, de modo
que la caja y las asas se recortan contra el lienzo si el clip se sale.

### Asas

- Cuatro asas en las esquinas (`canvas-handle-tl/tr/bl/br`) y un cuerpo (`canvas-body`).
- Las esquinas se calculan de la geometría **real** del clip, pero su centro se mete dentro del
  escenario si el clip sobresale (`canvas_edit.handle_centres`). Así siguen siendo pulsables en un
  `fill` o con `scale > 1`; la escala se ancla igualmente a la esquina real opuesta.
- `cursor` coherente (`grab`, `nwse-resize`, `nesw-resize`), `label` y `tooltip` en las cinco cajas
  (no generan avisos `no-label`).

### Mover

`canvas_edit.move_transform` convierte los deltas acumulados (`DragEvent.dx/dy`, píxeles lógicos)
en fracciones del escenario y recorta el centro a `0…1`. Se parte siempre del proyecto del
`begin`, no del estado del frame anterior, para que el clamp no acumule deriva.

### Escalar

`canvas_edit.scale_transform`:

1. Proyecta el delta sobre la diagonal del rectángulo inicial; así el gesto responde igual en
   horizontal y en vertical.
2. Multiplica la escala de partida y la acota a `0.25…4` (el rango de `Transform`).
3. Pide el layout real de la nueva escala y coloca el centro para que la **esquina opuesta** quede
   exactamente donde estaba (el ancla se conserva al píxel en los casos normales).
4. Recorta `x`/`y` a `0…1`. Si el ancla quedara fuera del lienzo al crecer mucho (clip que ya
   sobresalía por un borde), el centro se recorta y el ancla se mueve lo justo; es la única
   concesión para no construir un `Transform` inválido.

`Transform.scale` multiplica el encaje en ambos ejes, así que el aspecto se mantiene en `fit`,
`fill` y `stretch` sin lógica especial.

## Visibilidad (decisiones)

La capa **solo** se muestra si se cumplen todas:

- `selection[0] == "clip"` y el clip existe;
- el proyecto no es de solo lectura;
- el reproductor está **en pausa**;
- el clip intersecta el escenario (`visible_rect`; defensivo, porque el centro siempre está en
  0…1 y por tanto el clip siempre toca el lienzo).

Durante la reproducción la capa se oculta. Si un gesto estuviera abierto al arrancar la
reproducción, `_on_player_state` lo cierra (`store.commit`). Con el inspector oculto (`P`) o en
9:16 la capa sigue funcionando porque todo se recalcula con el escenario medido.

La caja es la del **clip seleccionado**, aunque el playhead esté mostrando otro frame (el editor
no mueve el playhead al seleccionar). En ese caso se ve la geometría del clip que se va a editar,
no la del frame de fondo; el inspector numérico sigue siendo la referencia exacta y la alternativa
accesible.

Cuando no se cumplen las condiciones **no se crea ninguna caja**: no hay capa transparente que
tape clics.

## Undo y cancelación

- `begin_canvas_drag` abre una transacción con `store.begin("mover clip en el lienzo")` o
  `"redimensionar clip en el lienzo"`.
- Cada `move` del ratón hace `store.update` con el proyecto recalculado desde el snapshot inicial.
- Al soltar, `store.commit()` si el puntero superó el umbral de clic (`_CLICK_SLOP_PX`, 3 px); si
  no, `store.cancel()`: un clic solo selecciona y no crea undo.
- `Esc` pasa por `EditorController.cancel_drag`, que primero cancela el gesto del canvas (restaura
  el snapshot) y repinta el frame. `Ctrl+Z`/`Ctrl+Y` también cancelan el gesto en curso porque
  `undo`/`redo` ya llamaban a `cancel_drag`.

## Límites

- No hay asa de rotación ni de recorte sobre el canvas; se siguen editando en el inspector.
- Si el clip seleccionado no es el que se ve en el playhead, la caja se dibuja igualmente sobre
  el frame actual (geometría del clip seleccionado). Es la contrapartida de mostrar siempre la
  selección; mover el playhead al clip es lo que alinea frame y caja.
- La marquesina/selección múltiple no mueve el bloque desde el canvas: la capa es del clip
  primario (`state.selection`), no de `state.multi_selection`.
- Solo se edita la pista de video (los clips de audio no tienen transformación).
- Las asas se desplazan hacia dentro cuando su esquina real cae fuera; en ese caso la escala sigue
  anclada a la esquina real, no a la dibujada (documentado en `handle_centres`).
- El escenario se redondea a píxeles enteros; la diferencia de aspecto con el frame es < 1 px.
- `on_resize` mide el hueco del preview, no el rectángulo del nodo de la imagen: es la misma
  información que Aurora expone en los handlers y evita pedir geometry que la versión 0.10 no da.

## Tests

- `tests/test_fase6_canvas.py` (11): `fit_stage`, recorte de la caja, asas dentro del escenario,
  mover con clamp, no-cambio = mismo proyecto, escalar desde las cuatro esquinas con ancla exacta,
  clamp 0.25/4, `x/y` en 0…1, asa inválida y vuelta al neutro (`transform=None`).
- `tests/test_fase6_canvas_ui.py` (5, app real con `pilot` y video sintético): las asas existen al
  seleccionar; arrastrar `canvas-handle-br` cambia `transform.scale` y el frame, con un solo undo;
  arrastrar `canvas-body` cambia `x/y`; un clic no crea undo; `Esc` restaura; 9:16 con inspector
  oculto; sin selección no hay capa; en reproducción se oculta; y seleccionar un clip que no está
  bajo el playhead también muestra su caja (la del seleccionado).
- `uv run pytest -q`: **345 passed** (329 previos + 11 puros + 5 de UI; 329 incluye la base v6,
  que añadió tests sobre los 324 de la v1.1.0).
- `uv run aurora check main.py`: OK, sin avisos.

### Ajustes a tests existentes

- `tests/test_f5_pulido.py::test_preview_del_editor_aprovecha_la_ventana` comprobaba que el nodo
  `ed-preview-img` ocupara el panel entero. Con el escenario de aspecto fijo la imagen llena el eje
  limitante (el alto en un panel ancho) y queda centrada; el test ahora comprueba eso y que el
  aspecto se conserve. El objetivo (aprovechar la ventana) sigue verificado.

## Riesgos de integración

- `ui/screens/editor.py` (preview) y `ui/editor_controller.py` (sección nueva + `cancel_drag` y
  `_on_player_state`) son puntos de conflicto con C y D, que también tocan el preview y el
  inspector. La capa `_canvas_layer` está pensada para que C pueda reutilizarla con elementos.
- `state.py` añade `PreviewArea`/`preview_area` en el bloque del editor; no cambia ningún `Signal`
  existente.
- `core/canvas_edit.py` es nuevo y no toca `core/project.py`, `core/store.py` ni `core/graph.py`.
- `tests/test_f5_pulido.py` se modifica a propósito (geometría del preview); no se renombra nada.
