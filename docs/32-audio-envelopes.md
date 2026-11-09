# 32 — Envolventes de volumen (keyframes de audio)

> Implementado el **2026-11-05** sobre el editor v9 (docs/27–31). Añade una curva de volumen por
> clip de audio y por el audio propio de un clip de video, con el preview y el export usando
> **la misma mezcla** de FFmpeg. Es independiente de los keyframes de posición/opacidad de
> `core/animation.py`: no comparte modelo ni canales (solo reutiliza `animation.ease` para el
> easing, [docs/40](40-easing-audio.md)).

## Qué se puede hacer

- Añadir claves de volumen a un **clip de video** (su audio original) y a un **clip de una pista
  de audio** (música, audio separado o voz). El audio de un video superpuesto (PiP/V2/V3) tiene
  su propia envolvente en `Element.envelope` ([docs/34](34-envolventes-elementos.md)).
- La clave se graba en el **playhead** con el volumen estático actual del clip; el tiempo es
  **local al clip** (`0` = inicio del clip en el timeline).
- Repetir en otro instante para crear la curva; preview y export interpolan con la curva elegida
  en **Easing** ([docs/40](40-easing-audio.md)); sin easing, lineal.
- **Quitar** la envolvente completa con un clic.
- El timeline dibuja la polilínea sobre la waveform y marca con **◆ env** la barra del clip.
- Fuera del clip seleccionado o con el playhead fuera de su intervalo, la UI avisa y no graba.

## Modelo (`SCHEMA_VERSION = 10` esta fase; hoy 14 con easing, [docs/40](40-easing-audio.md))

```python
VolumePoint(t=0.0, gain=1.0, easing="linear")   # t local al clip (s), gain 0…4
Clip(..., envelope=(VolumePoint(0.0, 1.0), VolumePoint(1.0, 0.25)))
```

- `points` se ordenan por `t`; no puede haber dos claves en el mismo instante.
- `gain` va de **0 a `GAIN_MAX` (4.0)**, el mismo límite que `Effect("volume")`. El modelo vive
  en `core/audio_envelope.py` y es puro (sin UI, sin FFmpeg, sin disco).
- Antes de la primera clave vale la primera; después de la última, la última. Entre claves se
  usa el easing de la clave **izquierda** (`linear`, `ease_in`, `ease_out` o `ease_in_out`);
  sin easing especificado, interpolación **lineal** ([docs/40](40-easing-audio.md)).
- La envolvente **multiplica** el volumen estático del clip (`fx.gain`) y el de su pista; no los
  sustituye. `1.0` deja el sonido como está y `0` lo silencia.
- Los proyectos v9 se migran a v10 sin cambios (`envelope=()`), así que suenan exactamente
  igual. `to_dict` escribe `"envelope": [{"t": …, "gain": …, "easing": …}]` y `from_dict`
  acepta su ausencia (default `"linear"`). La migración v13 → v14 también es un no-op.

Helpers puros: `value_at`, `expr`, `set_point`, `clear`, `validate` y `slice_envelope` (recorta y
reancla la curva al dividir, recortar o separar el audio).

## Export (`core/graph.py`)

Cada clip sigue siendo **una entrada** con `-ss/-t`; la única diferencia es un filtro adicional
en su cadena de audio cuando `clip.envelope` no está vacío:

```
...,atempo=SPEED,volume=GAIN,volume=volume='<expr>':eval=frame,afade…,apad,atrim
```

- La expresión es la misma que genera `core.audio_envelope.expr`: `if(lt(t,…))` anidados, con
  ganancias 0…4 y tiempos locales. `volume` la evalúa **por frame** (`eval=frame`). Cada tramo
  usa el easing de su clave izquierda; la rama lineal conserva la cadena histórica.
- El volumen estático (`fx.gain(clip) * track.gain`) se aplica antes, con el `volume=GAIN` de
  siempre; la curva es un multiplicador adicional.
- **Transiciones de V1**: el tramo audible de un clip entrante empieza tras el solape, así que
  `_audio_steps` recibe `skip` y la expresión se genera con `start=-skip`. Una clave local en
  `t=1` con `skip=0.5` se evalúa en `t=0.5` del tramo.
- Cubre la pista de video (V1) y las pistas de audio **secuenciales y absolutas**. En las
  absolutas, el `adelay` va después de la curva: el tiempo de la expresión es el del clip, no el
  del timeline.
- Sin envolvente, las cadenas del graph son **byte a byte** las de siempre (goldens de F4).
- El **corte rápido** se bloquea si algún clip de V1 tiene envolvente (`core/keyframes.py`),
  porque `-c copy` no puede aplicar el filtro.

## Preview (`services/playback.py`)

- `graph.has_audio_envelope(project)` detecta cualquier clip (video o pista extra) con curva.
- `AudioPreview.start` fuerza la ruta de **mezcla WAV cacheada** (`build_audio`) cuando hay
  envolvente, aunque no haya música ni audio de PiP. Es la misma sección de audio del export, así
  que la curva, los fundidos y el ducking coinciden.
- La clave de cache incluye el proyecto completo (`to_dict`), de modo que al mover una clave se
  genera un WAV nuevo.
- No se duplica la interpolación en `_feed_segment`: la ruta directa (sin pistas extra) no se usa
  cuando hay envolvente.

## UI

- **Inspector de clip de video** y **de clip de audio** → sección **Envolvente de volumen**:
  - selector **Easing** (`insp-env-easing`, no persistido): decide la curva de las claves nuevas;
  - número de claves y **Clave volumen** (`insp-env-key`);
  - **Quitar** (`insp-env-clear`) cuando hay al menos una.
- El tiempo se calcula con `playhead - inicio del clip` (`tl.clip_starts` para V1 y para la
  pista de audio). Si el playhead queda fuera, se avisa con un toast y no se toca el proyecto.
- La clave guarda `fx.gain(clip)` (el volumen estático que muestra el inspector). Como la curva
  multiplica, si el volumen estático es 0.5 y la clave vale 0.5 el resultado audible es 0.25;
  la sección lo explica en el texto de ayuda.
- **Timeline**: se dibuja la polilínea de la envolvente sobre la waveform (V1 y pistas de audio)
  y la barra muestra `◆ env`; las claves se marcan con puntos cuando el zoom es suficiente. La
  línea se acota a los extremos con el mismo `value_at` del export.
- **Asas de edición** ([docs/37](37-editor-claves.md)): con el clip seleccionado, cada clave es un
  asa arrastrable sobre la waveform. `dx` mueve el tiempo local (0…duración) y `dy` la ganancia
  0…4; un clic sin movimiento selecciona la clave y `Supr` la borra. El modo **Env** (`E` o el
  botón de la barra) permite añadir una clave con un clic en la waveform. Todo va por el store:
  undo/redo por gesto y la misma mezcla para preview y export.
- La barra secuencial/absoluta no cambia: la curva es local al clip en ambos modos.

## Límites y pendientes

- El **audio de un video superpuesto** (PiP, V2/V3) usa `Element.envelope`, implementado en
  [docs/34](34-envolventes-elementos.md); este documento cubre solo los clips de V1 y las pistas
  de audio.
- Las claves se pueden **arrastrar, añadir y borrar desde el timeline** ([docs/37](37-editor-claves.md));
  no hay edición de una clave existente desde el inspector más allá de volver a grabarla en el
  mismo instante (lo que también actualiza su easing).
- Interpolación con cuatro curvas fijas por clave (`linear`, `ease_in`, `ease_out`,
  `ease_in_out`, [docs/40](40-easing-audio.md)); no hay Bézier libres. No hay copiar/pegar claves
  ni modo de grabación automática.
- La curva no cambia el volumen de la pista ni el ducking; solo el del clip.
- Cambiar la velocidad de un clip no reescala las claves: sus tiempos son locales al timeline
  del clip, así que las que caen más allá de la nueva duración dejan de sonar (y no se dibujan).
- El corte rápido sigue bloqueado con audio separado, pistas extra o cualquier envolvente.

## Tests

- `tests/test_audio_envelope.py` (16):
  - modelo puro: interpolación, extremos, `expr` normal y desplazada por solape, `set_point`,
    `clear`, `slice_envelope`, validación y `Clip` inválido;
  - dividir un clip conserva la curva en los dos trozos y `detach_audio` la copia al audio
    separado;
  - schema 10, round-trip y migración v9 → v10;
  - golden de la cadena de audio sin envolvente y con ella (estática + curva);
  - alineación del clip entrante en una transición y pista absoluta;
  - `AudioPreview.start` fuerza la mezcla WAV;
  - **export real** con un tono que baja de 1.0 a 0.1 (RMS en ventanas), **export real con
    transición** que comprueba el desplazamiento `t + skip`, y WAV de `build_audio` con la misma
    atenuación relativa;
  - UI con `pilot`: claves en clip de video y en clip de audio, undo y «Quitar».
- `tests/test_envelope_handles.py` (7): geometría de las asas y edición directa en el timeline
  (arrastrar, añadir en modo Env, `Supr` y undo/redo); ver [docs/37](37-editor-claves.md).
- `tests/test_audio_envelope_easing.py` (8): curvas, `value_at`/`expr` con easing, migración
  v13 → v14, audio real y selector del inspector; ver [docs/40](40-easing-audio.md).
- `uv run pytest -q`: **462 passed** en el momento de esta fase (hoy 528 con docs/33–40).
- `uv run aurora check main.py`: OK; `uv run aurora tree main.py`: sin avisos de layout.

## Archivos tocados

- `src/recstudio/core/audio_envelope.py` (nuevo: modelo, interpolación y expresión).
- `src/recstudio/core/project.py` (`Clip.envelope`, schema 10, serialización).
- `src/recstudio/core/store.py` (migración v9 → v10).
- `src/recstudio/core/effects.py` (`GAIN_MAX` compartido).
- `src/recstudio/core/graph.py` (`has_audio_envelope`, filtro `volume` por frame).
- `src/recstudio/core/keyframes.py` (bloqueo del corte rápido).
- `src/recstudio/services/playback.py` (fuerza la mezcla WAV del preview).
- `src/recstudio/ui/editor_controller.py` (`add_audio_keyframe`, `clear_audio_envelope`).
- `src/recstudio/ui/screens/editor.py` y `ui/timeline.py` (inspector, polilínea y ◆).
- `tests/test_audio_envelope.py` y `tests/test_modelo_v6_base.py` (schema 10).
- `docs/32-audio-envelopes.md`, `docs/15`, `docs/04`, `docs/README.md`, `CHANGELOG.md`,
  `AGENTS.md` y `docs/40-easing-audio.md`.
