# 24 — Pistas de video y audio separadas por defecto (feature B)

> Implementado sobre la base v6 de [22 — Plan de features del editor](22-plan-features-editor.md).
> Al importar un video con audio, el editor crea una pista absoluta **«Audio del video»** con un
> clip enlazado al video y deja el video mudo (`audio_enabled=False`). Mover, recortar, dividir,
> cambiar la velocidad o borrar el video mantiene el audio en su sitio; el usuario puede
> **desvincularlo** para editarlo libremente y **volver a vincularlo** sin duplicarlo.
>
> No hay cambios de esquema: se usan `Clip.link_id`, `Clip.start`, `Clip.audio_enabled` y
> `Track.mode="absolute"` de la fase 1. `SCHEMA_VERSION` sigue en **6**.

## Resumen

| Pieza | Estado | Dónde |
|---|---|---|
| Separar el audio de todos los clips al importar (una sola pista y un solo undo) | ✅ | `core/timeline.separate_audio_all`, `ui/editor_controller.import_paths` |
| Sincronizar el audio enlazado en mover/recortar/velocidad/transición/Q-W/bloque | ✅ | `core/timeline.sync_linked_audio`, controller |
| Desvincular sin borrar audio ni reactivar el video | ✅ | `core/timeline.unlink_audio` + inspectores |
| Volver a vincular un audio libre conservando sus ediciones | ✅ | `core/timeline.relink_audio` + inspector del audio |
| Dibujo: pista «Audio del video» y chip/«↔» de enlazado | ✅ | `ui/timeline.py`, `ui/screens/editor.py` |
| Mover o recortar un audio enlazado por su cuenta | ⛔ (aviso; se desvincula primero) | controller |
| Corte rápido | Sigue bloqueado con audio separado (sin cambios) | `core/keyframes.py` |
| Tests puros y de UI | ✅ | `tests/test_fase6_av.py`, `tests/test_fase6_av_ui.py` |

## Importación

`EditorController.import_paths` separa el audio **después** de añadir los clips y **antes** de
guardar/activar, tanto en un proyecto nuevo como al final de uno existente:

1. Se añaden los assets y sus clips (secuenciales y magnéticos, como siempre).
2. `separate_audio_all(project)` recorre los clips de video con `audio_enabled=True` y
   `asset.has_audio=True`; cada uno pasa por `detach_audio`, que **crea o reutiliza** la misma
   pista absoluta «Audio del video». No se crea una pista por clip ni se duplica audio.
3. En un proyecto existente, el resultado se aplica con **un único** `store.apply("importar", …)`.
   En uno nuevo, el proyecto separado es el que se guarda y se activa (sin undo de importación,
   como antes).

Consecuencias:

- El video queda mudo (`audio_enabled=False`) y el audio suena desde su clip enlazado: el
  resultado de preview/export es el mismo que antes de separar (mismo offset, sin duplicar).
- Un video sin audio no crea pista; si se importa junto a otros con audio, solo los que suenan
  aportan clips.
- `separate_audio_all` es **todo o nada**. Si el tramo no cabe (p. ej. choca con audio absoluto
  ya existente o la pista está bloqueada), el import sigue adelante con el audio propio del
  video y se muestra un aviso; no se deja un proyecto a medias.
- En un proyecto existente se reutiliza la pista «Audio del video» aunque estuviera vacía.

## Sincronización del enlace

`sync_linked_audio(p, before)` es pura y recibe **el proyecto base del gesto**. No acumula
deltas sobre el estado intermedio: cada vez calcula la posición deseada desde el video actual.
Llamarla dos veces con el mismo `before` da el mismo resultado (idempotente), que es lo que
necesitan los drags (`begin/update/commit`) y el snap.

Reglas:

- **Inicio**: el audio de un video empieza en `inicio_video + solape_de_la_transición_entrante`
  (la misma regla que `detach_audio`). Al reordenar o mover un clip, el audio salta a su nuevo
  inicio; los clips posteriores del video son magnéticos y arrastran también a sus audios.
- **Un solo segmento enlazado**: además de `start`, se copian `in_`, `out_` y `speed` del video,
  ajustando la entrada por el solape (`in_ = video.in_ + solape * speed`). Así recortar por
  izquierda/derecha o cambiar la velocidad mantiene la misma porción de fuente y la misma
  duración relativa.
- **Varios segmentos** (audio dividido con `split_audio`): solo se desplaza `start` por el delta
  del video. No hay correspondencia 1:1 entre trozos, así que no se reescriben sus cortes.
- **Solapes**: si el resultado pisa otro clip de la misma pista absoluta, lanza `ProjectError`
  con el nombre de la pista; la UI avisa y **cancela el gesto** (el proyecto del `begin` sigue
  siendo válido y no se apila un undo a medias).
- No toca el audio sin `link_id` ni las pistas secuenciales (música).

Puntos donde se llama:

| Gesto | Dónde |
|---|---|
| Arrastrar un clip de video | `drag_move` |
| Recortar con las asas | `drag_trim` |
| Mover la selección múltiple | `drag_block` |
| Inspector: entrada/salida, marcar In/Out | `_trim_selected` |
| Velocidad del inspector | `set_speed` |
| Añadir/quitar/ajustar una transición | `set_transition` |
| `Q`/`W` sobre el video | `trim_to_playhead` |
| Importación | `separate_audio_all` antes del `apply` |

Dividir (`S`, razor) ya mantiene los enlaces por parejas en `split_all`; borrar (`Supr`) ya
borra el audio enlazado en cascada (`ripple_delete`). No se reescribieron esas operaciones.

En el arrastre en bloque, el audio enlazado **no** se pasa a `shift_audio_many`: se deja que lo
recoloque `sync_linked_audio` con su video. Así el bloque no choca antes de tiempo con el audio
de un vecino que también se está moviendo, y el audio libre seleccionado sí se desplaza con el
bloque como en la fase 3.

## Desvincular, editar y volver a vincular

- **Desvincular audio** (`unlink_audio(p, video_clip_id)`): limpia el `link_id` de todos los
  segmentos enlazados, **sin borrar el audio** y **sin reactivar** el sonido original del video.
  El audio queda libre en su pista absoluta.
- **Editar libremente**: con el enlace cortado se puede mover, recortar, dividir o borrar sin
  afectar al video. Mientras siga enlazado, el controller **bloquea mover o recortar** el audio
  por su cuenta (arrastre, inicio del inspector, asas, `Q`/`W` o fin del inspector) con el aviso
  «El audio está enlazado al video: pulsa «Desvincular audio»…». La política evita desincronizar
  en silencio sin mover el video a ciegas. Borrar el audio enlazado sí se permite: deja el video
  mudo y se puede recuperar con «Activar audio».
- **Vincular al video** (`relink_audio`): botón del inspector del audio libre. Conserva el clip
  tal cual (posición, cortes y efectos), le devuelve el `link_id` y vuelve a dejar el video mudo.
  Sin id explícito elige el clip de video del **mismo asset** que más solapa con el audio y, a
  igualdad, el más cercano; por eso el caso normal (audio recién desvinculado) es inequívoco.
  Es la forma de «volver a vincular» sin duplicar clips.
- **Vincular audio** (`attach_audio`, el de siempre): en un audio aún enlazado, borra todos sus
  segmentos y reactiva el audio original del video. Sigue funcionando igual; es una acción
  destructiva del audio separado, no un relink. El inspector muestra ambos botones cuando
  corresponde y los tooltips explican la diferencia.

## UI

- La pista absoluta se llama **«Audio del video»**; su cabecera añade `↔` cuando tiene clips
  enlazados. Cada clip enlazado muestra `↔ nombre` y la meta `· enlazado`; el tooltip dice
  «enlazado al video».
- La banda de audio propia del clip de video muestra `audio separado · pista inferior`.
- Inspector del video enlazado: chip «audio enlazado · pista «Audio del video»» y botones
  «Desvincular audio» / «Vincular audio».
- Inspector del audio: chip «enlazado al video · sigue sus cortes», «Desvincular audio»,
  «Vincular audio» (attach) y, si está libre y hay un video del mismo asset, «Vincular al video».
- Si el audio ya está libre, el inspector del video explica que se seleccione para volver a
  enlazarlo o que «Activar audio» recupera el sonido original.

## Límites

- **Audio dividido con `split_audio`**: varios segmentos comparten `link_id`. Al mover el video
  se desplazan todos; al recortarlo o cambiar su velocidad solo se ajusta `start` (no los
  `in_`/`out_` de cada trozo). Si el bloque no cabe, se cancela. Para editar los cortes de esos
  trozos hay que desvincular.
- **Recortar o mover un audio enlazado** no está permitido desde la UI; es deliberado. Las
  operaciones puras (`move_audio`, `trim_audio`) siguen aceptándolo para tests y usos internos,
  pero el controller no las ofrece mientras `link_id` esté puesto.
- La separación automática es todo o nada por importación: si el audio no cabe en la pista
  absoluta (proyecto con audio muy editado o pista bloqueada), el video importado conserva su
  audio propio y se avisa. Se puede separar después a mano.
- El relink automático elige por asset y solape; con el mismo archivo usado en varios clips y
  un audio movido lejos, puede elegir el clip más cercano. El caso normal no tiene ambigüedad.
- Sigue sin haber scroll vertical real en el timeline; los botones del inspector quedan dentro
  de su panel con scroll si la ventana es muy baja (el botón de desvincular se puso arriba para
  que esté siempre a mano).
- El corte rápido sigue bloqueado si hay audio separado o pistas absolutas (docs/19); no se
  tocó `core/keyframes.py`. Los proyectos sin audio mantienen el corte rápido intacto.
- No se cambió la mezcla (`core/graph.py` ni `Project.audio`): el audio separado usa la misma
  ruta `absolute` + `adelay`/`amix` de la fase 1.

## Tests

- `tests/test_fase6_av.py` (12): `separate_audio_all` con transición y sin duplicar pista,
  idempotencia, videos mudos/mixtos, un solo undo, sincronización al mover/recortar/cambiar
  velocidad, `split_all`, solape que cancela, `unlink_audio` (conserva audio y no reactiva),
  `relink_audio` (conserva ediciones y no duplica), round-trip JSON y paridad del graph con la
  separación manual (mismos filtros, 4 entradas, sin audio duplicado).
- `tests/test_fase6_av_ui.py` (6, app real con `build_app()` + `pilot`): importar deja la pista
  «Audio del video» y el video mudo; reimportar reutiliza la pista y es un solo undo; arrastrar
  el video mueve el audio en el mismo undo; recortar por ambos lados lo ajusta; desvincular
  desde los dos inspectores, volver a vincular y deshacer; mover un audio enlazado avisa y no
  lo mueve; guardar/reabrir conserva la pista enlazada.
- Tests existentes adaptados al nuevo valor por defecto: `test_bloque_b_ui` (marquesina elige
  también el audio), `test_f3_editor` (sigue verde sin cambios), `test_f4_export` (el modal usa
  un video mudo para probar «Sin recodificar»), `test_f5_pulido` (aísla el proyecto global) y
  `test_fase1_audio_ui`/`test_fase3_flujo_ui` (parten del audio ya separado y desvinculan antes
  de editar audio).
- Suite completa: **347 tests** (`uv run pytest -q`). `uv run aurora check main.py` sin avisos.
