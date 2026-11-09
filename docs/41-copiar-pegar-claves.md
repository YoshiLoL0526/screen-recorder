# 41 — Copiar y pegar claves

> Implementado el **2026-11-09** sobre las claves de animación de [31](31-keyframes.md),
> [33](33-escala-rotacion.md), [35](35-textos-escala-rotacion.md), [36](36-color-tamano-textos.md),
> [38](38-asas-animaciones.md) y [39](39-easing.md), y sobre las envolventes de audio de
> [32](32-audio-envelopes.md), [34](34-envolventes-elementos.md) y [37](37-editor-claves.md).
> La clave visual seleccionada o el punto de envolvente seleccionado se pueden copiar y pegar en
> el playhead sobre la capa/canal activo. **No cambia el modelo ni `SCHEMA_VERSION` (sigue en
> 13)**: se reutilizan `animation.set_keyframe`, `audio_envelope.set_point` y los `Keyframe` /
> `VolumePoint` existentes.

## Qué se puede hacer

- **Copiar** con `Ctrl+C`:
  - si hay una **clave visual seleccionada** (`state.keyframe_key`), se copia su valor y su
    easing;
  - si hay un **punto de envolvente seleccionado** (`state.envelope_key`), se copia su ganancia;
  - si no hay ninguna clave ni punto seleccionado, `Ctrl+C` sigue copiando clips, textos o
    elementos como en [docs/21](21-fase-3-flujo.md).
- **Pegar** con `Ctrl+V`: si el último copiado fue una clave, se pega en la capa/canal activo en
  el playhead; si fue un clip, se mantiene el pegado de clips de siempre.
- **Menú contextual** de las asas: «Copiar clave» y «Pegar clave», además de la entrada de
  borrado que ya existía. El clic derecho sobre un asa copia esa clave exacta y la deja
  seleccionada.
- **Undo/redo**: un pegado es una sola entrada (`pegar clave X` o `pegar punto de volumen`).
  Pegar en el mismo instante **reemplaza** valor/easing/ganancia; no duplica la clave.

## Modelo del portapapeles

```python
@dataclass(frozen=True)
class KeyClipboard:
    kind: str          # "visual" | "audio"
    channel: str       # canal de Animation de origen; "" en audio
    value: float       # valor de la clave o ganancia 0…4
    easing: str = "linear"
    source_label: str = ""
```

- Vive en `EditorController._key_clipboard`; **nunca** se guarda en `Project` ni en
  `project.json`. Es estado de UI no persistido.
- Es `frozen=True` por la regla del proyecto, aunque no viaje por un `Signal`.
- El portapapeles de claves y el de clips (`tl.Clipboard`) son **excluyentes**: gana el último
  copiado. Copiar una clave vacía el contador de clips (`state.clipboard_count = 0`) y copiar o
  cortar un clip olvida la clave. Así `Ctrl+V` siempre pega lo que se copió por última vez.
- No se usa el portapapeles del sistema ni se comparte entre instancias de la app.

## Semántica del pegado visual

- **Destino**: la capa seleccionada (`("element", id)` o `("text", id)`) y el canal activo del
  inspector (`state.keyframe_channel`). El canal copiado no tiene por qué coincidir con el de
  destino: se conserva el **valor** y el **easing**, no el nombre del canal.
- **Canales válidos**: elementos `x`, `y`, `scale`, `rotation`; textos `x`, `y`, `opacity`,
  `scale`, `rotation`, `size`. `color` queda fuera porque no es un `Animation` numérico
  ([docs/36](36-color-tamano-textos.md)).
- **Valor incompatible**: si el valor copiado no cabe en el rango del canal de destino (p. ej.
  un `size=80` pegado en `x`, que solo admite 0…1), el pegado es un **no-op** con aviso; no se
  toca el proyecto ni se abre una transacción.
- **Tiempo**: el playhead se convierte a tiempo **local** de la capa (resta su `start`) y se
  acota a `0…duración` con la misma tolerancia de `_key_time`. Si el playhead cae fuera del
  elemento/texto, es un no-op con aviso.
- **Inserción/reemplazo**: `animation.set_keyframe` sustituye la clave del mismo instante
  (tolerancia `1e-9`, la misma del modelo) o la inserta ordenada. El easing copiado se conserva
  en la clave pegada.
- Al terminar, la clave pegada queda seleccionada (`state.keyframe_key`) para poder moverla,
  borrarla o volver a copiarla.

## Semántica del pegado de audio

- **Destino**: el clip de video (V1), el clip de audio o el elemento de video (PiP/V2/V3)
  seleccionado. Los elementos que no son video no tienen audio y el pegado avisa.
- **Tiempo**: local al clip/elemento, acotado a `0…duración`; el playhead fuera del destino es un
  no-op con aviso.
- Se reemplaza el punto del mismo instante o se inserta con `audio_envelope.set_point`. La
  ganancia copiada se mantiene (0…4); las envolventes siguen siendo **lineales**
  ([docs/32](32-audio-envelopes.md)).
- Se respetan las pistas bloqueadas y el modo lectura: no se escribe nada y se avisa.
- Al terminar, el punto pegado queda seleccionado (`state.envelope_key`).

## Atajos y avisos

| Gesto | Acción |
|---|---|
| `Ctrl+C` con clave visual/punto de envolvente seleccionado | Copia la clave o el punto (no el clip) |
| `Ctrl+C` sin clave/punto seleccionado | Copia clips/textos/elementos como antes |
| `Ctrl+V` con una clave copiada | Pega en la capa/canal activo en el playhead |
| `Ctrl+V` con clips copiados | Pega clips como antes |
| Clic derecho en un asa → **Copiar clave** | Copia esa clave exacta y la selecciona |
| Clic derecho en un asa → **Pegar clave** | Pega en la capa/canal activo en el playhead |

Los atajos con `Ctrl` **no se interceptan** si el foco está en un campo de texto
(`from_input`): ahí `Ctrl+C`/`Ctrl+V` copian y pegan texto. Los avisos de no-op son toasts
`warning` explícitos («Selecciona el elemento…», «El playhead está fuera…», «No se puede pegar la
clave…»).

## Undo y transacciones

- Cada pegado válido usa `ProjectStore.apply` con una única etiqueta:
  - `pegar clave X` (o Y, opacidad, escala, rotación, tamaño);
  - `pegar punto de volumen`.
- Un no-op (sin destino, canal incompatible, valor fuera de rango, playhead fuera, pista
  bloqueada) **no llama** a `begin`/`apply`, no deja `ProjectStore._tx` abierta y no crea una
  entrada de undo.
- Si la clave pegada es idéntica a la que ya había (mismo instante, valor y easing), `apply`
  detecta que el proyecto no cambió y no crea undo; se avisa con un toast informativo.
- El pegado no mueve clips, no cambia selecciones múltiples ni abre transacciones de drag.

## Límites

- Solo se copian **claves existentes**: no crea claves nuevas desde el timeline (eso es otra
  feature) ni graba automáticamente al mover el playhead.
- El pegado visual no convierte valores entre canales: si el rango no encaja, se rechaza. Para
  pasar de `rotation` a `opacity`, por ejemplo, hay que crear la clave con un valor válido.
- `color` de textos (`TextOverlay.color_keys`) no entra en este portapapeles: no es un
  `Animation` numérico y tiene su propio editor en el inspector.
- Las envolventes de audio copian solo la ganancia; el easing no aplica (siguen lineales).
- No se puede pegar una clave visual en una capa de audio ni un punto de audio en una capa
  visual: el `kind` del portapapeles decide el destino y, si no hay uno válido, se avisa.
- El portapapeles es interno del proceso: no sobrevive a cerrar la app ni se comparte con otra
  instancia.

## Tests y verificación

- `tests/test_key_clipboard.py` (11):
  - modelo `KeyClipboard` frozen y comparable;
  - **pilot**: copiar una clave visual y pegarla en el mismo canal, con undo/redo;
  - pegar en otro canal válido conservando el easing;
  - reemplazo en el mismo instante (sin duplicar);
  - acotado al final del elemento y no-op con aviso si el playhead queda fuera;
  - canal incompatible (valor fuera de rango) sin undo;
  - no-op sin destino válido y sin transacción abierta;
  - punto de envolvente de un clip, reemplazo y undo/redo;
  - punto de envolvente en un elemento de video (PiP);
  - el portapapeles de clips sigue copiando/pegando y ambos son excluyentes;
  - **pilot**: clic en el asa, `Ctrl+C`, mover el playhead, `Ctrl+V`, y `Ctrl+C`/`Ctrl+V`
    dentro de un campo de texto sin tocar la clave ni la capa.
- `uv run pytest -q`: **531 passed**.
- `uv run aurora check main.py`: OK.
- `uv run aurora tree main.py`: sin avisos de layout.
- `git diff --check`: limpio.

## Archivos tocados

- `src/recstudio/state.py` (`KeyClipboard`; estado de UI, no se persiste).
- `src/recstudio/ui/editor_controller.py` (`_key_clipboard`, `copy_selected_key`,
  `copy_keyframe_key_at`, `copy_envelope_key_at`, `paste_key`, `_paste_visual_key`,
  `_paste_audio_key` y el despacho de `copy`/`cut`/`paste`).
- `src/recstudio/ui/timeline.py` («Copiar clave» y «Pegar clave» en los menús de las asas de
  animación y de envolvente).
- `tests/test_key_clipboard.py`.
- `docs/41-copiar-pegar-claves.md`, `docs/15`, `docs/37`, `docs/38`, `docs/README.md`,
  `CHANGELOG.md` y `AGENTS.md`.
