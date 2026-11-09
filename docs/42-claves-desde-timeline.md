# 42 — Añadir claves de animación desde el timeline

> Implementado el **2026-11-09** sobre las asas de [38](38-asas-animaciones.md), el easing de
> [39](39-easing.md) y el patrón del modo Env de [37](37-editor-claves.md). Permite **crear una
> clave visual con un clic** sobre la barra del elemento/texto seleccionado, sin pasar por los
> botones **Clave X/Y/…** del inspector. **No cambia el modelo ni `SCHEMA_VERSION` (sigue en
> 13)**: se reutilizan `animation.set_keyframe`, `Animation`, `Keyframe` y las animaciones
> existentes; preview y export no cambian para el mismo estado.

## Qué se puede hacer

- Activar el modo **Añadir clave con clic (N)** desde la sección **Animación** del inspector o
  con la tecla **N**. Es un estado de UI **no persistido** (`state.key_add_on`).
- Con un elemento/texto **seleccionado** y un canal elegido (`state.keyframe_channel`), hacer
  clic sobre su **barra** en el timeline:
  - crea una clave **en ese instante** para el canal activo;
  - usa el **valor interpolado actual** o, si el canal aún no tiene claves, el **valor base**
    del elemento/texto;
  - usa el **easing** elegido en el inspector (`state.insp_easing`, [docs/39](39-easing.md));
  - deja la clave **seleccionada** para poder arrastrarla;
  - **no mueve la capa** ni abre una transacción de arrastre: el clic es un comando atómico.
- Seguir haciendo clic para añadir más claves: el modo **no se apaga solo**.
- Si ya había una clave en ese instante, se **actualiza** (valor interpolado y easing) en vez de
  duplicarla, porque `animation.set_keyframe` reemplaza el punto del mismo tiempo.
- `Esc` cancela el gesto en curso (si lo hay) y, si no, **sale del modo**. `V` también vuelve a
  la herramienta normal; activar razor (`C`), marquesina (`V`) o Env (`E`) apaga el modo.

## Modo y atajo

- La tecla **N** estaba libre: no la usan J/K/L, R, E, B, M, I, O, P, Q, W ni los atajos con
  Ctrl/Alt. En un campo de texto enfocado no se intercepta, como el resto de letras sueltas.
- El modo es **excluyente** con la razor (`state.razor_on`), la marquesina
  (`state.marquee_on`) y el modo Env (`state.env_edit_on`). El controlador escucha
  `state.key_add_on` y, al activarse, apaga los otros tres y limpia el rectángulo de marquesina.
- El toggle del inspector es un `switch` de la sección **Animación** de elemento y de texto
  (`insp-element-key-add` / `insp-text-key-add`). El texto de ayuda del modo aparece en el
  inspector y en la barra del timeline.
- **Indicador visual**:
  - la barra del elemento/texto seleccionado cambia a borde y fondo rosa de animación y el
    cursor pasa a cruz;
  - una zona translúcida cubre la barra entera (`anim-add-element-…` / `anim-add-text-…`);
  - la barra del timeline muestra «Añadir clave activa…» y el inspector recuerda qué hace el
    clic. La clave recién creada aparece como asa rosa en el instante pulsado.

## Semántica del clic

1. Solo actúa sobre la capa **seleccionada** y el canal activo. Sin selección, con una selección
   que no es elemento/texto o con un canal que no vale para esa capa (p. ej. `size` en un
   elemento), no cambia nada y avisa con un toast.
2. La `x` del clic se convierte a **tiempo local** con la geometría de [38](38-asas-animaciones.md):
   `keyframe_time(start, duración, content_x, zoom)`, acotado a `0…duración`. El `in_` de la
   fuente de un video no interviene, igual que en el modelo.
3. El valor sale de `animation.value_at(item.animations, channel, t, base)`:
   - si el canal ya tiene claves, el **interpolado** en ese instante (con el easing de la clave
     izquierda);
   - si no, el **valor base** de la capa.

| Capa | Canales | Base |
|---|---|---|
| Elemento (figura, imagen, PiP, V2/V3) | `x`, `y` | `element.x`, `element.y` |
| Elemento | `scale` | `1.0` |
| Elemento | `rotation` | `element.rotation` (0.0 si no tiene rotación base) |
| Texto/subtítulo | `x`, `y` | `text.x`, `text.y` |
| Texto/subtítulo | `opacity` | `text.opacity` |
| Texto/subtítulo | `size` | `text.size` |
| Texto/subtítulo | `scale` | `1.0` |
| Texto/subtítulo | `rotation` | `0.0` |

4. El easing es `state.insp_easing` (Lineal, Suave al entrar, Suave al salir o Suave
   entrada/salida). Volver a pulsar en el mismo instante actualiza la curva de esa clave.
5. La clave queda seleccionada (`state.keyframe_key`) con su tiempo redondeado a 4 decimales,
   que es el mismo redondeo de `animation.set_keyframe`.

### Undo y gestos

- Cada clic genera **una** entrada de undo con la etiqueta `añadir clave X` (o `Y`, `opacidad`,
  `escala`, `rotación`, `tamaño`). Si el punto ya existía y el valor/easing no cambia, solo se
  selecciona y no se crea historial.
- El gesto se implementa con `on_drag` sobre la zona translúcida, como el modo Env
  ([docs/37](37-editor-claves.md)): la transacción se abre al crear la clave (`begun=True`), así
  que arrastrar en el mismo gesto mueve la clave **sin** añadir un segundo undo. Soltar confirma
  una única entrada `añadir clave X`.
- Un clic sin movimiento no toca la barra ni el `start` de la capa: la zona de añadir está por
  encima del gesto de mover la barra, y al terminar no queda ninguna transacción abierta.
- `Esc` durante el gesto llama a `store.cancel()` y devuelve el proyecto al estado anterior a la
  clave. Sin gesto, `Esc` cierra el modo.
- Las claves ya existentes se siguen editando con sus asas ([docs/38](38-asas-animaciones.md)):
  `dx` mueve el tiempo, `dy` el valor y `Supr`/menú contextual las borran. Al mover una clave se
  **conserva su easing** (el arrastre ya no lo resetea a `linear`).

## Geometría y helpers (`ui/timeline.py`)

Las funciones nuevas son puras y las comparten el clic, los tests y el dibujo:

```python
keyframe_time(start, duration, x, zoom)       # ya existía: x del contenido → tiempo local
keyframe_default(item, channel)               # base de x/y/opacity/size/scale/rotation
keyframe_value_at(item, channel, t)           # value_at o base si el canal no tiene claves
```

- `scale` empieza en `1.0`; `rotation` usa la rotación base de la capa (0.0 en textos, que no
  tienen campo) para no dar un salto al crear la primera clave; el resto usa su campo homónimo.

- La zona de añadir vive en `animation_handles_layer`, la misma capa **encima del playhead** que
  las asas: la línea de 2 px del playhead no bloquea el clic en el instante exacto.
- La zona va **antes** que las asas en esa capa, así que un clic sobre un asa existente la
  selecciona/arrastra como siempre y un clic en el resto de la barra añade.
- La barra mantiene sus asas de recorte debajo; en modo añadir el clic sobre la zona crea la
  clave, no recorta ni mueve la capa.

## Límites

- Solo se animan los canales de `Animation` de [docs/31](31-keyframes.md),
  [docs/33](33-escala-rotacion.md), [docs/35](35-textos-escala-rotacion.md) y
  [docs/36](36-color-tamano-textos.md). El **color** de textos (`TextOverlay.color_keys`) sigue
  fuera: no es un `Animation` numérico y se graba desde el inspector.
- Solo elementos y textos/subtítulos; los clips de V1 y el audio no tienen `Animation`. Las
  envolventes de audio siguen con su modo Env ([docs/37](37-editor-claves.md)).
- No hay grabación automática al mover el playhead, ni copiar/pegar claves, ni editor de curvas
  Bézier. El easing se elige antes de crear la clave o volviendo a pulsar en el mismo instante.
- El modo no cambia el bloqueo de pistas ni el modo lectura: si el proyecto es de solo lectura,
  el clic no modifica nada.
- El corte rápido sigue bloqueado por elementos/textos animados, como cualquier superposición.
- El clic en la barra **no mueve el playhead**; la guía visual es el asa de la clave creada.

## Tests y verificación

- `tests/test_key_add_mode.py` (6):
  - conversión x → tiempo local y valor base/interpolado de los seis canales en elemento y
    texto, incluido un canal sin animación;
  - **pilot**: clic en la barra de un elemento crea la clave con el valor base, el easing del
    inspector y la deja seleccionada; undo/redo y `start` intacto;
  - **pilot**: clic en el mismo instante actualiza el easing sin duplicar la clave;
  - no-op sin selección o con `size` en un elemento, sin transacción abierta;
  - exclusividad con razor, marquesina y Env, y `Esc` sale del modo;
  - **pilot**: clic en la barra de un texto crea una clave de `size`, queda arrastrable y el
    arrastre conserva el easing.
- `uv run pytest -q`: **526 passed** (520 base + 6 nuevos).
- `uv run aurora check main.py`: OK.
- `uv run aurora tree main.py`: sin avisos de layout.
- `git diff --check`: limpio.

## Archivos tocados

- `src/recstudio/state.py` (`key_add_on`; estado de UI no persistido).
- `src/recstudio/ui/timeline.py` (`keyframe_default`, `keyframe_value_at`, zona de añadir,
  resaltado de la barra y cableado del drag).
- `src/recstudio/ui/editor_controller.py` (`begin_keyframe_add`, `toggle_key_add`,
  exclusividad, `Esc`, corrección del easing al arrastrar y reset al abrir/cerrar proyecto).
- `src/recstudio/ui/screens/editor.py` (switch del inspector, ayuda del modo, barra del timeline
  y lista de atajos).
- `tests/test_key_add_mode.py`.
- `docs/42-claves-desde-timeline.md`, `docs/15`, `docs/37`, `docs/38`, `docs/39`,
  `docs/README.md`, `CHANGELOG.md` y `AGENTS.md`.
