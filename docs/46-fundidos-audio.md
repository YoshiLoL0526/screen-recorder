# 46 — Fundidos de audio y crossfade por corte en el timeline

> Implementado el **2026-11-09** sobre el editor v15 (docs/32, 37 y 41). Lleva los fundidos de
> audio (`audio_fade_in`/`audio_fade_out`, ya en `Clip.effects` desde docs/19) a **asas directas
> sobre la waveform** y añade un **crossfade por corte** entre clips consecutivos de una pista de
> audio secuencial. **No cambia `SCHEMA_VERSION` (sigue en 15) ni añade campos al proyecto**:
> reutiliza `Effect("audio_fade_in"/"audio_fade_out")` y `Track.transitions` / `Transition("fade")`,
> y el `acrossfade` que ya usaba el export. Es independiente de las envolventes de volumen
> (docs/32), que siguen teniendo sus propias asas en docs/37.

## Qué se puede hacer

- Con un **clip de una pista de audio** seleccionado (música o audio absoluto), aparecen dos asas
  azules en la banda superior del clip:
  - **entrada** arriba-izquierda: arrastrar a la derecha alarga `audio_fade_in`;
  - **salida** arriba-derecha: arrastrar a la izquierda alarga `audio_fade_out`.
- El asa de entrada y el de salida **no se cruzan**: cada una descuenta el fundido del otro lado.
- Un **clic sin movimiento** solo selecciona (las asas ya viven en el clip seleccionado) y no deja
  una transacción abierta. `Esc` cancela el gesto y revierte el proyecto.
- **Un solo undo por gesto** (`fundido de audio`), abierto en el primer movimiento
  (`begun=False`, patrón de docs/27).
- Entre dos clips consecutivos de una pista **secuencial** hay un asa de **crossfade** en el corte:
  arrastrarla a la izquierda alarga el `Transition(kind="fade")` (el asa sigue al puntero porque
  está en el inicio del clip entrante) y a la derecha lo acorta; por debajo de **0.2 s** se quita
  y queda un corte limpio. Un undo por gesto (`crossfade de audio`).
- Los fundidos siguen editables desde el inspector (selects **Fundido audio ↗/↘**); el valor
  efectivo se sincroniza en ambos sentidos. El crossfade de la pista también conserva su select
  **Crossfade** del inspector (aplica la misma duración a todos los cortes; ver «Límites»).

## Geometría (`ui/timeline.py`)

Todo el mapeo es puro y lo comparten dibujo, asas y tests:

```python
audio_fade_x(start, duration, fade, zoom, *, side="in"|"out")  # x del extremo de la rampa
audio_fade_at_x(start, duration, x, zoom, *, side=...)         # inversa, acotada a 0…duración
audio_fade_limit(duration, other)                              # tope del asa (core/effects.py)
```

- El tiempo de las asas es **local al clip** (`0` = su inicio), igual que el modelo de efectos.
- La rampa de entrada se dibuja de `(x, abajo)` a `(x + fin·zoom, arriba)`; la de salida, de
  `(x + w − fout·zoom, arriba)` a `(x + w, abajo)`. La línea y el relleno usan
  `AUDIO_FADE_COLOR` (`#4CC9F0`), distinto del amarillo de las envolventes.
- Las asas son cajas de **9×8 px** en el hueco superior de la fila (`row.top − 6`): no tapan el
  nombre del clip (que empieza en `row.top + 7`) ni las asas de recorte laterales (9 px de ancho
  y alto completo). El asa de crossfade es una caja de **14×10 px** justo encima de la fila, en el
  corte, y también convive con las de trim.
- La capa `audio_handles_layer` es un componente propio **encima del playhead** (como
  `env-layer`/`anim-layer` de docs/37 y 38) y con contenedor de 1×1 px para no tapar clics.
  Las asas de envolvente y animación van por encima: si una clave coincide con un asa de fundido,
  la clave gana el clic.

### Límite del asa de fundido

`fx.audio_fade_limit(duration, other) = max(0, min(2.0, duration/2, duration − other))`:

- **2 s** es el tope elegido para las asas (el inspector sigue ofreciendo hasta **3 s**; el graph
  `fx.audio_fades` reparte proporcionalmente si los dos no caben). Documentado aquí porque el
  límite de la UI no es el mismo que el del modelo.
- `duration/2` evita que un solo fundido se coma el clip.
- `duration − other` impide cruzar el fundido del otro extremo. Si el inspector dejó un fundido
  largo (p. ej. 2.5 s), el asa del otro lado se acota al hueco restante.
- Al editar con el asa se elimina el `fade_in`/`fade_out` **legado** del mismo lado (lo hereda
  `detach_audio`): así, si el efecto propio vuelve a 0 y se quita por neutro, no reaparece el
  valor histórico. Solo se aplica a clips de pistas de audio, donde `fade_*` no afecta a ninguna
  imagen.

### Crossfade por corte

- `core/timeline.set_audio_transition(p, track_index, index, duration)` es la operación pura:
  valida pista desbloqueada y `mode="sequence"`, pone `Transition("fade", d)` en
  `track.transitions[index]` o `None` si `d < TRANSITION_MIN (0.2)`, acota con
  `transition_limit` (`min(duración_a, duración_b)/2`, tope 2 s) y devuelve `(proyecto, acotada)`.
  Si el resultado no cambia, devuelve el mismo proyecto (sin undo).
- El asa se dibuja en `clip_starts(track)[index + 1]` (inicio del clip entrante). Con
  `duration = inicial − dx/zoom`, el asa sigue exactamente al puntero al arrastrar a la izquierda.
- El gráfico dibuja la zona de solape `[inicio del clip B, fin del clip A]` y la etiqueta
  `fade Ns`; con 40 px/s o más se ve el texto.
- **Pistas absolutas**: el modelo prohíbe transiciones (`Track.__post_init__`), así que no se
  muestra el asa y `set_audio_transition` lanza `ProjectError`. Los fundidos de entrada/salida
  sí existen en pistas absolutas.
- **Pistas bloqueadas** y modo **razor** ocultan las asas (fundido y crossfade), igual que las
  claves de envolvente. Un corte con `transition_limit < 0.2` tampoco muestra asa.

## Gestos y undo

- **Fundido**: `begin_audio_fade_drag(track, clip_id, side)` guarda un snapshot y los valores
  efectivos; `drag_audio_fade(dx)` abre la transacción `fundido de audio` solo al superar
  `_CLICK_SLOP_PX` (3 px) y aplica el valor; `end_audio_fade_drag` confirma. `Esc` llama a
  `cancel_drag`, que revierte al snapshot y no deja entrada de historial.
- **Crossfade**: misma estructura (`crossfade de audio`), con el límite y la duración inicial del
  `Transition` actual. Un clic sin movimiento no abre transacción; un arrastre que no cambia la
  duración tampoco crea undo.
- El drag de una transición **mueve los inicios de los clips siguientes** (en secuencia la
  duración total es `Σd − Σt`); el asa se recalcula con `clip_starts` en cada frame. No se mueve
  ningún clip de fuente ni se toca el `in_/out_`.
- Todo va por `ProjectStore` (`begin/update/commit`), así que preview, inspector y los `Signal`
  se repintan con el `Project` nuevo.

## Preview y export

- **No hay una segunda implementación**: los fundidos ya estaban en `_audio_steps` de
  `core/graph.py` (`afade=t=in/out` tras `atempo`/`volume`) y el crossfade en
  `_join_audio_track` (`acrossfade=d=…:c1=tri:c2=tri`). Esta feature solo edita el modelo.
- Con pistas de audio (`Project.audio`) el preview ya renderiza la mezcla exacta del export a un
  WAV cacheado (`AudioPreview.start` → `graph.build_audio`; clave = `to_dict(project)`). Al mover
  un asa cambia el proyecto y se genera un WAV nuevo, así que lo que se oye es lo que se exporta.
- El corte rápido se sigue bloqueando con pistas extra/absolutas/envolventes; esta feature no
  lo cambia.

## Límites

- Las asas de fundido cubren **pistas de audio** (música y audio absoluto). El audio propio de
  un clip de V1 se sigue ajustando desde el inspector; sus asas no se dibujan para no mezclarlas
  con la waveform del video y sus asas de recorte.
- El **crossfade por corte** es de pistas secuenciales. El select **Crossfade** del inspector
  sigue aplicando la misma duración a **todos** los cortes de la pista (compatibilidad con B1);
  las asas permiten duraciones distintas por corte y el inspector muestra la del primer corte.
- No hay curvas de fundido (lineal, la misma de `afade`), ni crossfade en pistas absolutas, ni
  transiciones de otro tipo (`fadeblack`, wipes…) para audio.
- El asa de fundido no cambia el volumen estático ni el de la pista; se multiplican como antes.
- `Esc` durante el gesto revierte, pero no desactiva ningún modo (no hay modo de fundidos).

## Tests y verificación

- `tests/test_audio_fade_handles.py` (9):
  - geometría pura `audio_fade_x`/`audio_fade_at_x` y límites `fx.audio_fade_limit` (2 s,
    duración/2 y hueco del otro fundido);
  - las asas solo aparecen con el clip de audio seleccionado y desaparecen al deseleccionar;
  - **pilot**: arrastre de entrada/salida, valores efectivos, clamp, inspector sincronizado,
    undo/redo y una sola etiqueta `fundido de audio`;
  - clic sin movimiento (sin undo ni transacción) y `Esc` durante el gesto;
  - **pilot**: creación de crossfade en el corte, `acrossfade=d=0.6:c1=tri:c2=tri` en
    `build_audio`, clamp a 1.5 s, undo/redo y vuelta a corte limpio;
  - clic en el asa de crossfade sin undo; sin asas en pista absoluta (aunque tenga dos clips) ni
    en pista secuencial bloqueada.
- `uv run pytest -q`: **588 passed** (579 base + 9 nuevos).
- `uv run aurora check main.py`: OK; `uv run aurora tree main.py`: sin avisos de layout.
- `git diff --check`: limpio.
- Prueba de UI real con `pilot` sin ventana (los tests anteriores); captura manual del editor con
  dos clips de música y un crossfade de 0.6 s.

## Archivos tocados

- `src/recstudio/core/effects.py` (`AUDIO_FADE_HANDLE_MAX`, `audio_fade_limit`).
- `src/recstudio/core/timeline.py` (`set_audio_transition`).
- `src/recstudio/ui/timeline.py` (geometría `audio_fade_x`/`audio_fade_at_x`, rampas y crossfade
  en el canvas, asas, `audio_handles_layer`, handlers de drag).
- `src/recstudio/ui/editor_controller.py` (`begin/drag/end/cancel_audio_fade_drag`,
  `begin/drag/end/cancel_audio_crossfade_drag`, `_project_with_audio_fade`).
- `tests/test_audio_fade_handles.py`.
- `docs/46-fundidos-audio.md`, `docs/15`, `docs/19`, `docs/32`, `docs/37`, `docs/README.md`,
  `CHANGELOG.md` y `AGENTS.md`.
