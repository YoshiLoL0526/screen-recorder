# 19 — Fase 0 y 1: fundación del modelo y audio editable

> Implementado el **2026-10-31** sobre la v1.0.0 + bloques A/B, siguiendo el análisis de
> [18 — Herramientas del editor](18-herramientas-editor.md). Cubre las fases 0 (modelo v3 y
> pistas absolutas) y 1 (audio separable, editable y mezclado en preview/export).

## Resumen

| Fase | Pieza | Estado | Dónde |
|---|---|---|---|
| 0 | Esquema v3: `Clip.start`, `audio_enabled`, `link_id`; `Track.mode`, `name`, `solo`, `locked` | ✅ | `core/project.py`, `core/store.py` |
| 0 | `clip_starts`/`track_duration` en modo secuencia y absoluto; validación y migración v1/v2 | ✅ | `core/project.py`, `core/timeline.py` |
| 1 | Separar/adjuntar audio con enlace y un solo undo | ✅ | `core/timeline.py`, `ui/editor_controller.py` |
| 1 | Dividir todo / solo audio / por pista | ✅ | `core/timeline.py`, `ui/editor_controller.py` |
| 1 | Mover, recortar y borrar clips de audio (secuencial y absoluto) | ✅ | `core/timeline.py`, `ui/timeline.py` |
| 1 | Varias pistas visibles con cabeceras M/S/L/volumen; añadir/quitar pista | ✅ | `ui/timeline.py`, `ui/screens/editor.py` |
| 1 | Solo/mute/lock en mezcla, preview y export | ✅ | `core/project.py`, `core/graph.py`, `services/playback.py` |
| 1 | Fundidos de audio independientes del video | ✅ | `core/effects.py`, `core/graph.py`, inspector |
| 1 | Export con `adelay`/`amix`; preview con `build_audio`; corte rápido bloqueado | ✅ | `core/graph.py`, `core/keyframes.py` |
| 1 | Persistencia de audio separado, pistas, solo/mute/lock y fundidos | ✅ | `core/project.py`, `core/store.py` |

**227 → 267 tests** (`uv run pytest`), 40 nuevos entre modelo, operaciones, graph, integración
real con FFmpeg y UI con `pilot`.

## Modelo: decisión secuencia vs. absoluto

La pista de video sigue siendo **secuencial** y magnética: no se tocó `clip_starts`, `move`,
`drop_index`, las transiciones ni los offsets de `xfade`. Eso mantiene intactos los proyectos y
los golden de F4.

Las pistas de audio pueden ser:

- `mode="sequence"` (por defecto, compatible con v1/v2): los clips se pegan en orden y
  `clip_starts` deriva su posición de la duración de los anteriores. Es el modo de la música y
  conserva `concat`/`acrossfade` y el crossfade de pista.
- `mode="absolute"`: cada clip lleva `start` en segundos y puede haber huecos. `clip_starts`
  devuelve esos `start` y `track_duration` es `max(start + duration)`. Es el modo del audio
  separado, la voz y cualquier audio que deba caer en un instante concreto.

`Clip.start=None` se interpreta como `0` dentro de una pista absoluta (tolerancia de lectura),
pero las operaciones de la UI siempre escriben un `start` redondeado a frame. Las pistas
absolutas no admiten transiciones (`transitions` todo `None`), así que no hay solapes ni
`xfade` que resolver.

`Project.total_duration` sigue siendo la duración del **video** (lo que se exporta y lo que
reproduce el `Player`). Para dibujar el timeline existe `tl.project_extent`, que incluye audio
absoluto que sobresalga del video.

### Enlace audio ↔ video

`Clip.link_id` es un enlace **débil** (varios segmentos pueden apuntar al mismo video):

- El clip de audio separado apunta al `id` del clip de video original.
- Varios segmentos pueden compartir el mismo `link_id`: es lo que deja un audio dividido con
  `split_audio`. `Project.validate` exige que el video exista y que el campo solo esté en audio,
  pero ya no exige unicidad.
- `detach_audio` crea el clip de audio con `start` = inicio del video **más el solape de la
  transición entrante** (el audio del clip entrante empieza cuando termina el solape, igual que
  en el export). Ajusta `in_ = clip.in_ + solape * speed`, deja `out_` igual y copia `asset_id`,
  `speed` y los efectos de audio (`volume`, fundidos); marca el video con `audio_enabled=False`
  (el validador no obliga a ese estado, pero es la única forma de que la UI lo produzca y evita
  duplicar el sonido).
- `attach_audio` borra **todos** los clips con ese `link_id` (incluidos los trozos de un audio
  dividido), reactiva el audio del video y deja limpio el hueco.
- Mover, recortar o dividir el audio conserva el enlace; el trozo derecho de `split_audio` sigue
  apuntando al mismo video. El enlace solo deja de existir si se borra el audio o se usa
  `attach_audio`.
- Borrar un clip de video borra también su audio enlazado (misma operación de undo); si la pista
  de audio está bloqueada, el borrado se rechaza.
- `split_all` mantiene la correspondencia por parejas: el trozo derecho del audio se enlaza al
  trozo derecho del video.

### `audio_enabled` y coherencia

- `audio_enabled` solo tiene sentido en clips de la pista de video. Un `False` silencia ese
  tramo en export y preview.
- Los clips de pistas de audio deben tener `audio_enabled=True`; el validador lo exige para no
  duplicar semántica.
- Un `audio_enabled=False` **sin enlace** es válido: representa audio separado cuyo enlace se
  rompió o se borró. El usuario puede reactivar el audio original desde el inspector.

### Solo, mute y bloqueo

- `Track.solo`: si **alguna** pista de audio tiene solo, solo suenan las pistas con solo; las
  demás pistas de audio **y el audio del video** se silencian. Es el comportamiento clásico de
  aislar una pista y queda documentado aquí porque el video no tiene un botón de solo propio.
- `Track.muted` y `gain` siguen funcionando como antes; `muted` gana sobre `solo`.
- `Track.locked` impide mover, recortar, borrar, dividir y quitar la pista, tanto en las
  operaciones puras (`ProjectError`) como en la UI. No impide cambiar volumen, fundidos o
  efectos: el candado es de **estructura**, no de mezcla.
- Quitar una pista de audio que contenía clips enlazados **reactiva** el audio del video de esos
  clips, para no dejar el proyecto mudo por accidente; si se prefiere el silencio, se puede
  volver a separar o silenciar la pista de video.

## Esquema v3 y migración

`SCHEMA_VERSION = 3`. Campos nuevos, todos con valor por defecto para que un proyecto v1/v2 se
lea sin cambios visibles:

```json
{
  "video": {
    "mode": "sequence", "name": "", "solo": false, "locked": false,
    "clips": [
      {"start": null, "audio_enabled": true, "link_id": null}
    ]
  },
  "audio": [
    {"mode": "absolute", "name": "Audio del video", "solo": false, "locked": false}
  ]
}
```

`store.migrate` aplica `_migrate_v2_v3`: rellena solo las claves que faltan en `video`, en cada
`audio` y en cada clip. `from_dict` sigue siendo tolerante con campos ausentes (proyectos v1
incluso sin `audio`/`texts`). `clip_starts`, `track_duration`, el graph y el preview aceptan
ambos modos; los tests de migración usan JSON v1 y v2 construidos a mano y comprueban que el
proyecto resultante es igual al esperado y que `validate()` pasa.

## Operaciones puras (`core/timeline.py`)

| Función | Qué hace |
|---|---|
| `clip_starts`, `track_duration` | Modo secuencia (fórmula actual) o absoluto (`start`, `max end`). |
| `detach_audio(p, clip_id)` | Crea/reutiliza «Audio del video», copia el clip, enlaza y silencia el video. Devuelve `(p, audio_id)`. |
| `attach_audio(p, video_clip_id)` | Borra el clip enlazado y reactiva el audio del video. |
| `split_track(p, track, t)` | Corta una pista concreta (`None`/video, índice o `Track`). |
| `split_all(p, t)` | Video + audio enlazado; conserva el enlace en el trozo derecho. |
| `split_audio(p, t)` | Solo audio de todas las pistas; conserva el `link_id` en ambos trozos. |
| `move_audio(p, track, clip, start=…, to_index=…)` | `start` en absoluto (sin solapes); `to_index` en secuencia. |
| `trim_audio(p, track, clip, in_, out_)` | Trim de fuente; en absoluto, recortar solo por la izquierda mueve `start` y fija el borde derecho. |
| `remove_audio(p, track, clip)` | Borra; deja hueco en absoluto y cierra en secuencia. |
| `add_audio_track`, `remove_audio_track`, `set_audio_track` | Pistas: crear/quitar y nombre/mute/solo/lock/ganancia/ducking. |
| `find_audio_clip`, `linked_audio_clip` | Consultas para la UI y los tests. |

`split` se mantiene **solo video** por compatibilidad con los tests y con la API de F3. La UI
usa `split_all` para `S` y `split_audio` para `Mayús+S`; así el atajo antiguo conserva el
comportamiento cuando no hay audio enlazado y gana el corte conjunto cuando lo hay.

## UI

- **Timeline**: todas las pistas de `Project.audio` se dibujan, cada una con su cabecera
  (`Track.name` o «Audio N»), mute, solo, candado y un botón de volumen que cicla
  `0/25/50/75/100/150/200 %`. La selección es única:
  `("audio", track_index, clip_index)`; `("clip", id)`, `("transition", i)` y `("text", id)`
  siguen igual.
- **Drags de audio**: el cuerpo mueve (posición absoluta o reordenación en secuencia) y las
  asas recortan, con snap a bordes, playhead y picos de audio, y una sola entrada de undo por
  gesto (`store.begin/update/commit`); `Esc` cancela. En una pista absoluta, el asa izquierda
  desplaza el inicio y deja fijo el borde derecho.
- **Inspector de clip de audio**: inicio/fin en el timeline, duración, pista, volumen del clip,
  fundidos de audio, volumen/ducking/crossfade de la pista, «Vincular audio» si está enlazado y
  «Eliminar».
- **Inspector de clip de video**: botón «Separar audio» en la ficha del clip y sección «Audio del
  clip» con fundidos independientes; cuando el audio está separado, «Vincular audio» o
  «Activar audio».
- **Barra**: dividir todo, dividir solo audio, añadir pista, quitar la pista del clip
  seleccionado. Atajos: `S` dividir todo, `Mayús+S` dividir solo audio.
- **Pistas**: se pueden añadir y quitar desde la barra y desde el inspector de proyecto. El
  límite es **6 pistas de audio**: ver «Límites».

## Export y preview

- El audio del video se genera como antes, pero respeta `audio_enabled`, `muted`, `gain` y el
  solo (un solo de audio silencia también el video).
- Las pistas `sequence` siguen con `concat`/`acrossfade` y `sidechaincompress` para el ducking.
- Las pistas `absolute` añaden una entrada por clip y lo mezclan así:
  `atrim/asetpts/aresample/atempo → volume → afade → apad/atrim (duración del clip) →
  adelay=delays=<muestras>S:all=1 → amix (si hay varios clips) → apad/atrim (duración total)`.
  El retardo va en **muestras** (48 kHz) para no perder exactitud con los frames.
- La mezcla final es `amix=inputs=N:normalize=0:duration=first`, igual que la música.
- `core.graph.build_audio` usa la misma sección de audio, así que el WAV cacheado del preview
  incluye las pistas absolutas y el preview y el export no divergen.
- El ducking sigue usando el audio del video como sidechain cuando suena; si un solo lo
  silencia, no se aplica (no hay señal de control).
- El **corte rápido** se bloquea si hay audio separado (`audio_enabled=False`), pistas
  absolutas con clips, pistas de audio adicionales o cualquier solo activo.

## Fundidos de audio independientes

Se añadieron los efectos `audio_fade_in` y `audio_fade_out` (`seconds`). La cadena de audio usa
`fx.audio_fades`; si el clip no tiene el efecto nuevo, hereda el `fade_in`/`fade_out` histórico
para no cambiar los proyectos existentes. El video sigue usando `fx.fades` para la imagen, así
que ahora se puede fundir solo el audio sin tocar el video. El inspector permite ajustarlos en
cualquier clip (video o audio). Desde [46](46-fundidos-audio.md) los clips de las pistas de audio
también tienen **asas de fundido** en el timeline (entrada arriba-izquierda, salida arriba-derecha)
y un **crossfade por corte** entre clips consecutivos de una pista secuencial; el modelo sigue
siendo el mismo (`Effect` y `Track.transitions`).

## Persistencia

`to_dict` escribe siempre los campos nuevos y `from_dict` los acepta ausentes. Un proyecto con
audio separado, pistas absolutas, mute/solo/lock, nombres y fundidos se guarda y reabre
idéntico (`tests/test_fase1_audio_ui.py::test_guardar_y_reabrir...`). El guardado sigue siendo
atómico y el `.bak` no cambia.

## Tests y verificación

- `tests/test_fase0_modelo.py` (8): defaults v3, pistas absolutas con huecos, solapes, enlaces
  válidos (varios segmentos pueden apuntar al mismo video), round-trip, migración v1/v2 y carga de
  un JSON v2.
- `tests/test_fase1_audio.py` (25): detach/attach (también con transición), split all/audio/track, mover/recortar/borrar,
  pistas, solo/mute/lock, graph con `adelay`/`amix`, `build_audio`, fundidos, corte rápido y
  **export real** con un pitido movido +0.5 s y el video original en silencio.
- `tests/test_fase1_audio_ui.py` (7): separar desde el inspector con undo único, drags de mover
  y recortar, `S`/`Mayús+S`, varias pistas con M/S/L, añadir/quitar pista, límite de pistas y
  guardar/reabrir.
- Un test existente cambió de clave: `music-<id>` → `audio-<id>` en
  `tests/test_bloque_b_ui.py`, porque la selección `"music"` se unificó en `"audio"`.
- Suite completa: **267 passed** (`uv run pytest`).
  `uv run python tools/f3_editor_test.py --no-shots` y
  `uv run python tools/f4_export_test.py --skip-long --no-shots` también pasan: preview a 30 fps,
  3 clips con undo, export real con A/V en sync (desfase máx. 10 ms) y corte rápido byte a byte.

## Límites y pendientes

- **Scroll vertical**: se probó el `scroll(axis="y")` exterior con `pilot` y el desplazamiento
  quedaba en 0 (el scroll horizontal interior se comía la rueda). Se aplicó la alternativa que
  autoriza docs/18: el panel crece con las pistas y la UI limita a **6 pistas de audio** las
  nuevas. Proyectos antiguos con más pistas se siguen dibujando, pero el panel puede crecer de
  más en ventanas pequeñas.
- Las asas de fundido sobre la waveform y el crossfade por corte llegaron en
  [46](46-fundidos-audio.md); las envolventes/keyframes de audio, en [32](32-audio-envelopes.md) y
  [37](37-editor-claves.md). Siguen pendientes normalización/compresor/EQ/pan (B7), medidores (B12)
  y export solo audio (B13).
- La velocidad de un clip de audio no se edita desde la UI; `detach_audio` copia la del video.
  El graph soporta `atempo` para esos clips.
- `split_audio` conserva el enlace en ambos trozos; `attach_audio` los borra todos. Si se borra
  el audio a mano, el video se reactiva con «Activar audio».
- El crossfade por clip sigue siendo global de la pista secuencial; las pistas absolutas no
  usan transiciones.
- El micrófono de una grabación con **pistas separadas** (C5, [docs/50](50-pistas-microfono.md))
  se importa como pista absoluta «Micrófono» con `tl.add_audio_track` + `tl.insert_audio_clip`;
  reutiliza este modelo sin cambios. El audio del sistema sigue en «Audio del video».
- No se implementó el scroll vertical real ni una pista de video superpuesta (fase 2+).
