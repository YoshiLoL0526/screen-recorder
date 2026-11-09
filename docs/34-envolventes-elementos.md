# 34 — Envolventes de volumen del audio de elementos

> Implementado el **2026-11-05** sobre las envolventes de clips de [32 — Envolventes de volumen](32-audio-envelopes.md)
> y el audio de [28 — PiP](28-pip-video.md) / [29 — Pistas V2/V3](29-pistas-video.md).
> Extiende la curva de volumen al audio de los **videos superpuestos**: PiP de la capa «Elementos»
> (`video_track=0`) y clips de **V2/V3** (`video_track=1/2`). Preview y export comparten el mismo
> graph; no hay una segunda fórmula de interpolación.

## Qué se puede hacer

- Añadir claves de volumen al **audio de un video superpuesto** (PiP o clip de V2/V3).
- La clave se graba en el **playhead** con el volumen estático actual del elemento; el tiempo es
  **local al elemento** (`0` = su `start` en el timeline, independientemente de `in_`).
- Repetir en otro instante para crear la curva; preview y export interpolan linealmente.
- **Quitar** la envolvente completa con un clic.
- La barra del elemento en el timeline muestra **◆ env** cuando tiene curva.
- Fuera del intervalo del elemento, la UI avisa y no graba.

## Modelo (`SCHEMA_VERSION = 11`)

```python
VolumePoint(t=0.0, gain=1.0)   # t local al elemento (s), gain 0…4
Element(
    kind="video",
    asset_id="a-…",
    start=1.0, end=4.0,
    in_=2.0,
    video_track=0,                         # 0 = Elementos; 1 = V2; 2 = V3
    envelope=(VolumePoint(0.0, 1.0), VolumePoint(2.0, 0.25)),
)
```

- `Element.envelope` reutiliza `core/audio_envelope.py`: `VolumePoint`, `validate`, `value_at`,
  `expr`, `set_point` y `clear`. No hay modelo nuevo ni campos adicionales.
- Las claves se ordenan por `t`; no puede haber dos en el mismo instante. `gain` va de **0 a
  `GAIN_MAX` (4.0)**, el mismo límite que `Effect("volume")`.
- Antes de la primera clave vale la primera; después de la última, la última. Entre claves,
  interpolación **lineal**.
- La envolvente **multiplica** el volumen estático del elemento (`el.audio_gain`, que sale de
  `Element.effects`) y los microfundidos de 30 ms de sus bordes; no lo sustituye. `1.0` deja el
  sonido como está y `0` lo silencia.
- Solo los videos pueden tener envolvente: un elemento de imagen o una figura con `envelope`
  lanza `ProjectError`. Un video con `audio_gain=0` sigue siendo silencio (no hay audio que
  mezclar), tenga o no curva.
- Los proyectos **v10 se migran a v11 sin cambios** (`envelope=()`), así que suenan exactamente
  igual. `to_dict` escribe `"envelope": [{"t": …, "gain": …}]` y `from_dict` acepta su ausencia.
- `elements.update_element` acepta `"envelope"` y valida el resultado.

## Export (`core/graph.py`)

El audio del elemento se sigue construyendo en `_element_audio_mixes` y reutiliza la entrada del
overlay cuando existe (`[N:a]`, la misma que abrió `overlays.apply_graph`). La única diferencia es
un filtro adicional **después** del volumen estático y **antes** de los microfundidos:

```
[N:a]asetpts=PTS-STARTPTS,aresample=48000,aformat=fltp:stereo,
     volume=GAIN,                                  # volumen estático del elemento
     volume=volume='<expr>':eval=frame,            # solo con envolvente (docs/34)
     afade in/out de 30 ms,
     apad/atrim a la duración del elemento,
     adelay hasta `start`, apad/atrim al total     # el retraso va DESPUÉS de la curva
```

- La fuente se lee con `-ss in_ -t duración` y empieza en `t=0` del tramo, así que la expresión
  se genera con `start=0`: el tiempo de FFmpeg coincide con el tiempo local de la curva. El
  `adelay` posterior no desplaza la evaluación.
- `env.expr` produce los mismos `if(lt(t,…))` anidados que en los clips, con ganancias 0…4.
- Cubre **PiP** (`video_track=0`) y **V2/V3** (`video_track=1/2`): ambos pasan por
  `element_audio` + `_element_audio_mixes`. En el export de V2/V3 la entrada del overlay usa el
  prefijo `vt`, pero el audio sigue siendo `pipN`.
- Sin envolvente, la cadena es **byte a byte** la de antes (los tests de docs/28 comprueban la
  cadena exacta).
- `has_audio_envelope(project)` sigue cubriendo solo los clips de V1 y las pistas de audio; el
  audio de un elemento ya se detecta con `has_element_audio`. El corte rápido ya estaba bloqueado
  por `Project.elements`, así que no cambia su comportamiento.

## Preview (`services/playback.py`)

- El audio de elementos ya obligaba a la ruta de **mezcla WAV cacheada** (`build_audio`) cuando
  `has_element_audio` era cierto. La envolvente no añade una ruta nueva: `build_audio` llama a
  `_audio_section` → `_element_audio_mixes`, exactamente la misma sección de audio del export.
- `project_has_audio` sigue siendo correcto: un elemento con `audio_gain > 0` cuenta como audio
  aunque el video base esté en silencio. `AudioPreview.start` elige `_feed_mixed` con
  `bool(project.audio) or has_element_audio(project) or has_audio_envelope(project)`.
- La clave de cache de `_mix_file` incluye el `to_dict` completo, así que mover una clave genera
  un WAV nuevo.
- No se duplica la interpolación en `_feed_segment`: la ruta directa no se usa cuando hay audio
  de elemento.

## UI

- **Inspector del elemento de video con audio** (`asset.has_audio`) → sección **Envolvente de
  volumen**:
  - número de claves y **Clave volumen** (`insp-el-env-key`);
  - **Quitar** (`insp-el-env-clear`) cuando hay al menos una.
- El tiempo local es `playhead - element.start`; si el playhead queda fuera del intervalo, se
  avisa con un toast y no se toca el proyecto.
- La clave guarda el volumen que muestra **Volumen** (`insp_el_gain`, 0…4). Como la curva
  multiplica, con volumen estático 0.5 y clave 0.5 el resultado audible es 0.25.
- **Timeline**: la barra del elemento añade **◆ env** (además del **◆** de animaciones) y el
  tooltip indica cuántas claves tiene. Con el video superpuesto seleccionado, cada clave se dibuja
  como un **asa sobre la barra** ([docs/37](37-editor-claves.md)): `dx` mueve el tiempo local y
  `dy` la ganancia 0…4 sobre el alto de la fila. El modo **Env** (`E`) permite añadir una clave
  con un clic en la barra; `Supr` borra la seleccionada.
- Los botones y el contador se repintan con el `Project` del `Signal`; `_sync_inspector` mantiene
  el campo de volumen sincronizado tras cada cambio. Las operaciones van por `_apply`, así que
  entran en el undo (`añadir clave de volumen del elemento` / `quitar envolvente de volumen del
  elemento`). Los arrastres usan una transacción que empieza en el primer movimiento y se confirma
  al soltar (`mover clave de volumen`).

## Límites

- No hay **polilínea** de la curva en la barra del elemento (los clips de V1 y las pistas de audio
  sí la tienen, docs/32); las claves se editan con las asas de [docs/37](37-editor-claves.md).
  Tampoco hay copiar/pegar claves ni modo de grabación automática.
- La curva no cambia el volumen de la pista V2/V3 ni el ducking; solo el audio del elemento. El
  ducking sigue usando la mezcla del video base + elementos como sidechain.
- Un elemento con `audio_gain=0` no suena aunque tenga envolvente; «Silenciar» sigue siendo el
  interruptor de su audio.
- El audio de un elemento no se separa a una pista absoluta ni tiene fundidos de usuario: solo
  los microfundidos de 30 ms de sus bordes.
- El corte rápido sigue bloqueado por `Project.elements`, como cualquier superposición.

## Tests

- `tests/test_element_audio_envelope.py` (10):
  - modelo: round-trip, migración v10 → v11, validación (orden, rango, solo videos) y
    `update_element`;
  - graph: cadena exacta sin envolvente, curva multiplicando el volumen estático, alineación
    local (`start=0`), PiP y V2, y detección de audio del elemento (`has_element_audio` /
    `project_has_audio`);
  - **export real** de un PiP y de un clip V2 con un tono que baja de 1.0 a 0.1 (RMS por
    ventanas);
  - `build_audio` (la ruta del preview) con la misma atenuación relativa;
  - UI con `pilot`: claves en el playhead local, undo, aviso fuera del intervalo, **◆ env** en la
    barra y «Quitar».
- `tests/test_envelope_handles.py` (7): asas sobre la barra del elemento (tiempo y ganancia),
  selección con clic, `Supr` y undo/redo; ver [docs/37](37-editor-claves.md).
- `tests/test_pip_video.py`, `tests/test_video_tracks.py`, `tests/test_audio_envelope.py` y
  `tests/test_modelo_v6_base.py` siguen en verde con el esquema 11.
- `uv run pytest -q`: **480 passed** en el momento de esta fase (hoy 496 con docs/33–37).
- `uv run aurora check main.py`: OK; `uv run aurora tree main.py`: sin avisos de layout.

## Archivos tocados

- `src/recstudio/core/project.py` (schema 11, `Element.envelope`, serialización y validación).
- `src/recstudio/core/store.py` (migración v10 → v11).
- `src/recstudio/core/elements.py` (`update_element` acepta `envelope`).
- `src/recstudio/core/graph.py` (`volume` por frame en `_element_audio_mixes`).
- `src/recstudio/ui/editor_controller.py` (`add_element_audio_keyframe`,
  `clear_element_audio_envelope`).
- `src/recstudio/ui/screens/editor.py` (sección del inspector).
- `src/recstudio/ui/timeline.py` (**◆ env** y tooltip).
- `tests/test_element_audio_envelope.py`, `tests/test_audio_envelope.py` y
  `tests/test_modelo_v6_base.py`.
- `docs/34-envolventes-elementos.md`, `docs/15`, `docs/04`, `docs/README.md`, `CHANGELOG.md` y
  `AGENTS.md`.
