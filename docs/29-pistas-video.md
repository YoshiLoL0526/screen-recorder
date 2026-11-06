# 29 — Pistas de video V2/V3

> Implementado el **2026-11-05** sobre el PiP de video de [docs/28](28-pip-video.md).
> Añade **dos pistas de video superpuestas** (V2 y V3) con varios clips, z-order, audio mezclado
> y edición en el timeline. Modelo **v8** con migración v7 → v8 sin cambios visibles.

## Qué se puede hacer

- Elegir el destino de inserción en la pestaña **Capas**: **Elementos**, **V2** o **V3**.
- Añadir un video al destino desde:
  - el botón **PiP** del panel de medios;
  - el botón **+** del panel de medios (inserta en el playhead);
  - arrastrar el medio al timeline o al preview;
  - **Video en V2… / V3…** de la pestaña Capas;
  - el **+** de la cabecera de una pista V2/V3 ya existente.
- Los clips de V2/V3 ocupan el lienzo completo por defecto (`fit="fill"`), pero se pueden mover,
  escalar, rotar, recortar y cambiar de opacidad como cualquier elemento: al reducir la caja se
  convierten en un PiP sobre el video base.
- Cada pista admite **varios clips** en el tiempo (huecos incluidos) y se dibuja en su propia fila
  del timeline, encima de V1.
- El inspector muestra **Pista** para mover un video entre Elementos, V2 y V3.
- La pestaña **Capas** prefija los videos con `V2`/`V3` y los ordena como se apilan.

## Modelo (`SCHEMA_VERSION = 8`)

```python
Element(
    kind="video",
    asset_id="a-…",
    start=1.0, end=4.0,
    video_track=1,        # 0 = Elementos; 1 = V2; 2 = V3
    x=0.5, y=0.5, width=1.0, height=1.0, fit="fill",
)
```

- `video_track` es el único campo nuevo. `0` significa la capa **Elementos** (imágenes, figuras y
  PiP suelto, el comportamiento de docs/28).
- `Project.validate` (y `Element.__post_init__`) exige que solo los videos tengan
  `video_track > 0` y que esté en `0…VIDEO_TRACKS_MAX` (2 → V2/V3).
- Los proyectos v7 se abren en v8 con `video_track=0`; `_migrate_v7_v8` solo sube la versión.
- **No hay `Track` nuevos**: las pistas V2/V3 son grupos de `Element` de video. Es una decisión
  deliberada para reutilizar la composición, el audio, la selección y el drag que ya existían y
  no duplicar el motor de V1. `Project.elements` sigue siendo el z-order real.

### Orden de capas

El orden de `Project.elements` es **V2 → V3 → Elementos**. La composición y el z-order leen ese
orden; no se ordena en cada frame.

- `add_element` inserta el elemento en su capa (misma capa: al final).
- `set_video_track` lo saca y lo vuelve a insertar en la capa nueva.
- Pegar (`timeline.paste`) inserta cada elemento respetando la capa.
- `from_dict` hace un orden estable por capa (los proyectos viejos, todos en 0, conservan su
  orden relativo).

## Composición

El orden es el mismo en preview y export:

```
V1 (clips + transiciones) → V2 → V3 → efectos globales → Elementos → textos
```

- V2/V3 se componen con el mismo `overlays.apply_graph` / `overlays.apply_frame` del PiP
  (`setpts` al inicio, `overlay`, `eof_action=pass`, `enable` por intervalo).
- Los **efectos globales** (color, blur, viñeta, grayscale) se aplican después de V2/V3, así que
  afectan a todas las pistas de video. La capa «Elementos» (imágenes, figuras y PiP suelto) queda
  por encima y no se ve afectada, como antes.
- Para que no colisionen las etiquetas del filter graph, la pasada de V2/V3 usa el prefijo `vt`
  (`vtel0`, `vtov0`); la capa Elementos conserva `el0`/`ov0`.
- El audio de un clip de V2/V3 se mezcla por el mismo camino que el PiP: `element_audio` +
  `_element_audio_mixes`, con `in_`, volumen del elemento y microfundidos. Un solo de audio
  silencia también estas pistas; la opacidad es visual y no mutea.
- El preview usa `FrameRenderer.render` con dos pasadas de `overlays.apply_frame` (V2/V3 antes de
  los efectos globales; Elementos después). El export hace lo mismo con dos pasadas de
  `overlays.apply_graph`.

## UI

- **Timeline**: una fila por pista usada, en orden V3, V2, V1. Las cabeceras muestran el nombre,
  el número de clips y un **+** para añadir otro video a esa pista. Las barras se mueven y
  recortan con los mismos gestos que los elementos (incluido el recorte izquierdo que avanza
  `in_`). Las pistas vacías no ocupan sitio.
- **Panel Capas**: botones **Elementos / V2 / V3** para fijar el destino; el botón inferior pasa a
  decir **Video en V2…** / **Video en V3…**. Los videos de V2/V3 se prefijan en la lista.
- **Inspector** del video: selector **Pista** (Elementos/V2/V3), además de Fuente, Volumen,
  posición, tamaño, rotación, opacidad y encaje. **Al frente/Al fondo/Subir/Bajar** mueven dentro
  de la capa actual; para cambiar de capa se usa **Pista**.
- Añadir a V2/V3 usa `width=height=1.0` y `fit="fill"`; el resto de valores son los del
  elemento/PiP.

## Límites

- **Dos pistas extra** (V2 y V3). El modelo está preparado para ampliar `VIDEO_TRACKS_MAX`.
- No hay **razor/división** de un clip V2/V3: se recorta con las asas o se edita el intervalo en
  el inspector. El razor sigue cortando V1 y las pistas de audio.
- No hay **transiciones** entre clips de la misma pista V2/V3 (los clips son elementos
  independientes; si se solapan, gana el último del z-order).
- No hay mute/solo/volumen/lock **por pista** V2/V3: el volumen es del elemento y el bloqueo de V1
  no bloquea V2/V3. Un solo de las pistas de audio sí las silencia.
- El audio de un clip V2/V3 va **dentro del video** (no se separa a «Audio del video» como en V1).
- No se arrastra un clip de **V1 a V2/V3**: se añade el medio a la pista o se mueve un video entre
  Elementos/V2/V3 desde el inspector.
- El **filmstrip** de miniaturas sigue siendo solo de V1; las barras de V2/V3 muestran nombre y
  duración.
- El corte rápido sigue bloqueado por `Project.elements`, como con cualquier superposición.

## Tests

- `tests/test_modelo_v6_base.py`: `video_track` en round-trip, validación de tipo y rango, orden
  V2 < V3 < Elementos, cambio de pista y z-order (al frente/al fondo/subir/bajar) que no cruza de
  capa.
- `tests/test_video_tracks.py` (7):
  - preview por capas: V2 tapa V1 y V3 tapa V2;
  - export real por capas con los mismos píxeles;
  - efectos globales sobre V2 pero no sobre la capa Elementos, en preview y export;
  - audio de un clip V2 mezclado en su intervalo;
  - pegar un V2 conserva el orden de capas;
  - geometría del timeline: filas V3 encima de V2 y V1 debajo.
- `tests/test_project_panel.py`: destino en Capas, alta en V2/V3 a pantalla completa, cabeceras
  `+`, caja en el timeline y cambio de pista desde el inspector con undo.
- `uv run pytest -q`: **431 passed**.
- `uv run aurora check main.py`: OK.

## Archivos tocados

- `src/recstudio/core/project.py` (schema 8, `Element.video_track`, validación, serialización).
- `src/recstudio/core/store.py` (migración v7 → v8).
- `src/recstudio/core/elements.py` (alta/edición por capa, `set_video_track`, orden).
- `src/recstudio/core/graph.py` (dos pasadas de elementos y efectos globales entre ellas).
- `src/recstudio/core/overlays.py` (prefijo de etiquetas).
- `src/recstudio/core/timeline.py` (pegado por capas).
- `src/recstudio/services/playback.py` (composición en dos pasadas).
- `src/recstudio/state.py` (`insp_el_track`, `video_track_target`).
- `src/recstudio/ui/editor_controller.py`, `ui/screens/editor.py`, `ui/project_panel.py`,
  `ui/timeline.py`.
- `tests/test_modelo_v6_base.py`, `tests/test_video_tracks.py`, `tests/test_project_panel.py`.
