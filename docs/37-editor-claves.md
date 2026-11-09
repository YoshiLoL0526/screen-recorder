# 37 — Edición de claves de envolvente en el timeline

> Implementado el **2026-11-09** sobre las envolventes de [32](32-audio-envelopes.md) y
> [34](34-envolventes-elementos.md). Las claves de `Clip.envelope` y `Element.envelope` se
> pueden **arrastrar directamente** sobre la waveform (V1 y pistas de audio) o sobre la barra de
> un video superpuesto, además de añadirlas y borrarlas desde el timeline. **No cambia el modelo
> ni `SCHEMA_VERSION` (sigue en 11)**: se reutilizan `audio_envelope.set_point`, `value_at` y
> `clear`, y la mezcla/export no cambian para el mismo estado.

## Qué se puede hacer

- **Arrastrar una clave existente** de la envolvente de un clip de V1 o de una pista de audio:
  - `dx` mueve el **tiempo local** (0…duración del clip), sin cruzar la clave vecina;
  - `dy` cambia la **ganancia** 0…4 (el alto de la fila se reparte con 2 px de margen).
- **Añadir** una clave con el modo **Env** (`E` o el botón de la barra del timeline): un clic en
  la waveform del clip seleccionado crea una clave en ese instante con la ganancia que ya tenía la
  curva (o el volumen estático si no había ninguna). Si se sigue arrastrando, la clave recién
  creada se mueve en el mismo gesto.
- **Seleccionar** una clave con un clic (sin mover) y **borrarla** con `Supr`; el menú contextual
  del asa (clic derecho) ofrece «Eliminar clave de volumen».
- Lo mismo sobre la **barra de un elemento de video** (PiP, V2/V3): sin waveform, los puntos se
  dibujan sobre la barra y usan todo su alto para la ganancia.
- Las asas solo aparecen con el clip/elemento **seleccionado**, en pistas desbloqueadas y fuera
  del modo razor, para no llenar el timeline de puntos.

## Geometría (`ui/timeline.py`)

Las funciones son puras y se comparten entre el dibujo y el arrastre:

```python
envelope_gain_y(top, height, gain)    # y del contenido para una ganancia 0…4
envelope_gain_at_y(top, height, y)    # inversa, acotada a 0…4
envelope_x(start, duration, t, zoom)  # x del contenido para un tiempo local
envelope_time(start, duration, x, zoom)  # inversa, acotada a 0…duración
```

- La curva y las asas usan **el mismo mapeo**: 2 px de margen arriba/abajo y el resto reparte
  `GAIN_MAX` (4.0). En la barra de un elemento el «alto» es `row.height - 4`.
- El tiempo es **local** al clip/elemento (`0` = su inicio en el timeline); el `in_` de la fuente
  no interviene, igual que en el modelo.
- Las asas son cajas absolutas de 11 px (clips) o 9 px (elementos) y viven en una **capa propia
  encima del playhead**: la línea de 2 px del playhead bloquearía el centro de un asa justo en el
  instante de la clave (lo normal al crearla desde el inspector). El contenedor de esa capa mide
  1×1 px para no tapar clics del timeline.

## Gestos y undo

- **Primer movimiento**: la transacción se abre con `ProjectStore.begin` solo cuando el puntero
  supera el umbral (`_CLICK_SLOP_PX`, 3 px), con el patrón `begun=False` de [docs/27](27-panel-medios-y-capas.md).
  Un clic sin movimiento solo **selecciona** la clave y no crea una entrada de undo.
- Al soltar se confirma la transacción: **un** undo por gesto, con la etiqueta
  `mover clave de volumen`.
- Añadir abre la transacción al crear la clave (`añadir clave de volumen`) y el mismo gesto puede
  moverla antes de soltar. `Esc` durante el gesto revierte al estado anterior.
- Borrar es un comando atómico (`eliminar clave de volumen`); también entra en undo/redo.
- Los cambios van por `ProjectStore.update` (drag) o `_apply` (añadir/borrar), así que el preview y
  el inspector se repintan con el `Project` nuevo. **No hay una segunda interpolación**: el audio
  del preview (`build_audio`) y el `volume=volume='…':eval=frame` del export leen el mismo modelo.
- Al mover una clave se acota a `[vecina_anterior + 0,0002, vecina_siguiente − 0,0002]`; el modelo
  exige tiempos estrictamente crecientes y no se fusionan claves en silencio.

## Modo Env y menú contextual

Aurora 0.10 no expone un `on_double_click` general (solo el `chart`), así que se elige un **modo
explícito** y un menú nativo:

- Botón **Envolvente** de la barra del timeline (`tl-envelope`) o tecla **E**. Es excluyente con
  razor (`C`) y marquesina (`V`); `Esc` o `V` lo desactivan.
- En modo Env, el clip seleccionado muestra una zona translúcida sobre su waveform/barra:
  - clic en una zona vacía → crea la clave y la deja seleccionada;
  - clic sobre una clave existente → la selecciona y permite arrastrarla;
  - arrastrar desde el primer clic → crea y mueve en un solo gesto.
- Fuera del modo Env las asas siguen visibles y arrastrables; solo se oculta la zona de añadir.
- El clic derecho sobre un asa abre el menú nativo «Eliminar clave de volumen». En Linux no hay
  menús nativos (limitación de Aurora/GTK), pero `Supr` sigue funcionando.

## Límites

- **Solo audio**: las claves de `Animation` (x/y/opacity/scale/rotation/size) tienen sus
  propias asas desde [docs/38](38-asas-animaciones.md), con un selector de canal en el
  inspector; este documento cubre solo `Clip.envelope` y `Element.envelope`.
- No hay copiar/pegar claves, easing ni «grabar automáticamente» al mover el playhead.
- La interpolación sigue siendo **lineal** y la ganancia se acota a 0…4; no hay ganancia negativa.
- Si la pista está bloqueada, el clip no muestra asas; con la razor activa se ocultan para no
  mezclar gestos. El modo Env no cambia el bloqueo ni el modo lectura.
- Cambiar la velocidad de un clip no reescala las claves (tiempos locales al timeline del clip),
  como ya documentaba [docs/32](32-audio-envelopes.md).
- El corte rápido sigue bloqueado por cualquier envolvente o superposición.
- El audio de un clip de V1 con `audio_enabled=False` conserva su curva y sus asas: la curva se
  aplicará si se vuelve a vincular el audio (misma política que docs/32).

## Tests y verificación

- `tests/test_envelope_handles.py` (7):
  - geometría pura ganancia ↔ y y tiempo ↔ x, con acotado en los extremos;
  - un clip sin envolvente no muestra asas y con una clave sí (y desaparecen al deseleccionar);
  - **pilot**: arrastrar un asa de V1 cambia tiempo y ganancia, undo y redo;
  - **pilot**: modo Env añade una clave en la waveform, queda seleccionada y `Supr` la borra;
  - clic en un asa sin mover solo selecciona (no crea undo);
  - **pilot**: asas de un clip de audio y de un elemento de video (barra sin waveform).
- `uv run pytest -q`: **505 passed** (496 en la rama aislada, antes de integrar docs/36).
- `uv run aurora check main.py`: OK; `uv run aurora tree main.py`: sin avisos de layout.
- `git diff --check`: limpio.
- Export/preview: la feature solo edita el modelo; `tests/test_audio_envelope.py` y
  `tests/test_element_audio_envelope.py` siguen verdes con las mismas cadenas de audio.

## Archivos tocados

- `src/recstudio/state.py` (`env_edit_on`, `envelope_key` y `EnvelopeKey`; estado de UI, no se
  persiste).
- `src/recstudio/ui/timeline.py` (geometría pura, asas, capa sobre el playhead, modo Env, menú
  contextual y cableado de drags).
- `src/recstudio/ui/editor_controller.py` (`begin/drag/end_envelope_key_drag`,
  `begin_envelope_add`, `select_envelope_key`, `delete_envelope_key*`, toggle del modo y limpieza
  de la selección de clave).
- `src/recstudio/ui/screens/editor.py` (botón Env, ayuda contextual y textos del inspector).
- `src/recstudio/ui/icons.py` (icono de envolvente).
- `tests/test_envelope_handles.py`.
- `docs/37-editor-claves.md`, `docs/15`, `docs/32`, `docs/34`, `docs/README.md`, `CHANGELOG.md`
  y `AGENTS.md`.
