# 38 — Asas de animaciones visuales en el timeline

> Implementado el **2026-11-09** sobre los keyframes de [31](31-keyframes.md),
> [33](33-escala-rotacion.md), [35](35-textos-escala-rotacion.md) y
> [36](36-color-tamano-textos.md), y con el patrón de asas de [37](37-editor-claves.md).
> Las claves de `Element.animations` y `TextOverlay.animations` se pueden **arrastrar
> directamente sobre la barra** del elemento/texto seleccionado. **No cambia el modelo ni
> `SCHEMA_VERSION` (sigue en 12)**: se reutilizan `animation.set_keyframe`, `Animation`,
> `Keyframe` y las animaciones existentes; preview y export no cambian para el mismo estado.

## Qué se puede hacer

- Elegir el **canal** que se edita en el timeline desde el inspector, sección **Animación**:
  - elementos: **X**, **Y**, **Escala** y **Rotación**;
  - textos/subtítulos: **X**, **Y**, **Opacidad**, **Escala**, **Rotación** y **Tamaño**.
- Con un elemento/texto seleccionado y claves en ese canal, su barra muestra una **polilínea**
  con el valor interpolado y un **asa por clave**.
- **Arrastrar un asa**:
  - `dx` mueve el **tiempo local** dentro de `0…duración`, sin cruzar la clave vecina;
  - `dy` cambia el **valor** del canal, acotado a su rango.
- **Clic sin mover** en un asa solo la selecciona (no crea undo).
- **Supr** borra la clave seleccionada; el **menú contextual** del asa ofrece la misma acción.
- Las claves nuevas y el borrado de todo el canal siguen en el inspector (**Clave …** y
  **Quitar**), como hasta ahora.
- `Esc` cancela el arrastre en curso y devuelve el proyecto al estado anterior al gesto.

## Canales y geometría (`ui/timeline.py`)

Las funciones son puras y las comparten el dibujo, las asas y el arrastre:

```python
keyframe_value_y(top, height, channel, value)     # y del contenido para un valor del canal
keyframe_value_at_y(top, height, channel, y)      # inversa, acotada al rango
keyframe_x(start, duration, t, zoom)              # x del contenido para un tiempo local
keyframe_time(start, duration, x, zoom)           # inversa, acotada a 0…duración
```

- El mapeo vertical es el mismo para elementos y textos: 2 px de margen arriba/abajo y el resto
  reparte el rango del canal (`core.animation.CHANNEL_RANGES`). Arriba = valor máximo.
- Rangos:
  - `x`, `y`, `opacity`: `0…1`;
  - `scale`: `0.05…4.0`;
  - `rotation`: `-360…360` (grados horarios);
  - `size`: `8…200` (tamaño base del texto).
- El tiempo es **local** al elemento/texto (`0` = su `start`); el `in_` de la fuente de un video
  no interviene, igual que en el modelo.
- La polilínea se dibuja en el **canvas del timeline**, debajo de la barra translúcida, y las
  asas son cajas de 9 px en una **capa propia encima del playhead** (la línea de 2 px del
  playhead bloquearía el centro de un asa justo en ese instante). El contenedor de la capa mide
  1×1 px para no tapar clics del timeline.
- Las asas solo se generan para la capa **seleccionada**, con el **canal elegido** y con claves.
  El resto de barras del timeline no se ensucia.

## Gestos y undo

- **Primer movimiento**: la transacción se abre con `ProjectStore.begin` solo cuando el puntero
  supera el umbral (`_CLICK_SLOP_PX`, 3 px), con el patrón `begun=False` de
  [docs/27](27-panel-medios-y-capas.md). Un clic sin movimiento solo selecciona la clave y no
  crea una entrada de undo.
- Al soltar se confirma la transacción: **un** undo por gesto, con la etiqueta
  `mover clave X` (o `Y`, `opacidad`, `escala`, `rotación`, `tamaño`).
- Al mover una clave se acota a `[vecina_anterior + 0,0002, vecina_siguiente − 0,0002]`; el
  modelo exige tiempos estrictamente crecientes y no se fusionan claves en silencio.
- Borrar es un comando atómico (`eliminar clave X`); si era la única clave del canal, se quita
  la animación entera (`animation.clear_channel`), igual que **Quitar**. También entra en
  undo/redo.
- Los cambios van por `ProjectStore.update` (drag) o `_apply` (borrar), así que preview,
  inspector y timeline se repintan con el `Project` nuevo. **No hay una segunda interpolación**:
  el preview evalúa `animation.value_at` y el export usa las mismas expresiones de siempre.

## Selector de canal

- `state.keyframe_channel` es estado de UI **no persistido** (`"x"` por defecto). El inspector
  lo muestra con un `select` en la sección **Animación** (`insp-element-anim-channel` /
  `insp-text-anim-channel`) y solo ofrece los canales válidos de la capa seleccionada; `color`
  queda fuera porque no es numérico (`TextOverlay.color_keys`, docs/36).
- Al cambiar de texto a elemento (o al revés) el controlador corrige el canal si el anterior no
  es válido: p. ej. `size` en un elemento vuelve a `x`. No se añade ningún campo al proyecto.

## Límites

- Solo se editan **claves existentes** desde el timeline; añadir una clave nueva se hace desde
  el inspector (no hay modo «Añadir clave» como el modo Env de las envolventes).
- No hay copiar/pegar claves ni grabación automática al mover el playhead. El easing de una clave
  se elige en el inspector ([docs/39](39-easing.md)); las envolventes de audio tienen su propio
  selector ([docs/40](40-easing-audio.md)).
- El canal `color` de textos no tiene asas: es un modelo aparte (`color_keys`) sin rango
  numérico. Los canales de transformación de un clip de V1 tampoco son `Animation`.
- Con la razor activa se ocultan las asas para no mezclar gestos, igual que las de las
  envolventes. Las capas de texto/elemento no tienen bloqueo propio de pista.
- Cambiar de canal no borra ni reordena claves; solo cambia lo que se dibuja y se arrastra.
- El corte rápido sigue bloqueado por los elementos/textos animados, como cualquier
  superposición.

## Tests y verificación

- `tests/test_animation_handles.py` (8):
  - geometría pura valor/y con los cuatro rangos y su inversa, incluido el acotado en extremos;
  - geometría pura tiempo/x local;
  - sin claves o sin selección no hay asas, y desaparecen al deseleccionar;
  - **pilot**: arrastrar un asa de X cambia tiempo y valor, undo y redo;
  - clic sin mover solo selecciona (sin undo);
  - **pilot**: `Supr` borra la clave seleccionada y undo la recupera;
  - **pilot**: selector de canal y claves de **tamaño** de un texto (rango 8…200), con el
    controlador corrigiendo `size` → `x` al pasar a un elemento;
  - las asas solo aparecen en la capa seleccionada y para el canal con claves.
- `uv run pytest -q`: **520 passed** (513 en la rama aislada, antes de integrar docs/39).
- `uv run aurora check main.py`: OK (138 nodos).
- `uv run aurora tree main.py`: sin avisos de layout.
- `git diff --check`: limpio.

## Archivos tocados

- `src/recstudio/state.py` (`keyframe_channel`, `keyframe_key` y `KeyframeKey`; estado de UI).
- `src/recstudio/ui/timeline.py` (geometría pura, polilínea, asas, capa encima del playhead y
  cableado de drags).
- `src/recstudio/ui/editor_controller.py` (`begin/drag/end_keyframe_key_drag`,
  `select_keyframe_key`, `delete_keyframe_key_at`, `_cancel_keyframe_drag` y corrección del
  canal válido).
- `src/recstudio/ui/screens/editor.py` (selector de canal y nota del inspector).
- `tests/test_animation_handles.py`.
- `docs/38-asas-animaciones.md`, `docs/15`, `docs/31`, `docs/33`, `docs/35`, `docs/37`,
  `docs/README.md`, `CHANGELOG.md` y `AGENTS.md`.
