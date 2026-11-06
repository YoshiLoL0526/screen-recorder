# 28 — PiP de video (video superpuesto)

> Implementado el **2026-11-05** sobre el panel de medios/capas de [docs/27](27-panel-medios-y-capas.md).
> Añade **video encima del video base** con posición, tamaño, rotación, opacidad, recorte de
> intervalo y audio mezclado con volumen propio. Lo hace como un `Element` de tipo `video`,
> reutilizando la caja, las capas y el timeline que ya existían. Las pistas **V2/V3** llegaron
> después agrupando estos elementos ([docs/29](29-pistas-video.md)); no son `Track` nuevas.

## Qué se puede hacer

- Añadir un video como **PiP** de dos formas:
  - botón **PiP** en la fila de un video del panel de medios;
  - botón **Video en Elementos…** en la pestaña «Capas» (abre el diálogo de video y lo añade al
    playhead). Si el destino de Capas es V2/V3, se añade a esa pista ([docs/29](29-pistas-video.md)).
- El video superpuesto aparece en la pestaña **Capas**, en la fila **Elementos** del timeline y en
  el preview con caja y asas.
- Se puede mover, escalar, rotar, cambiar opacidad, cambiar el encaje (`fit/fill/stretch`) y
  recortar el intervalo arrastrando la barra del timeline.
- Recortar por la izquierda **avanza la fuente**: `Element.in_` guarda el segundo de entrada del
  video. Mover la barra conserva la fuente.
- El intervalo se limita a la duración real del archivo.
- El **audio del PiP** se mezcla con el del proyecto, con volumen propio (0 = silencio) y botones
  «Silenciar»/«Original» en el inspector.

## Modelo (`SCHEMA_VERSION = 7`)

```python
Element(
    kind="video",
    asset_id="a-…",          # MediaAsset(kind="video")
    start=1.0, end=4.0,      # intervalo del timeline
    in_=2.0,                 # segundo de la fuente al que empieza start
    x=0.8, y=0.2,            # centro normalizado
    width=0.25, height=0.25, # caja normalizada
    fit="fit",               # fit | fill | stretch
    rotation=0.0, opacity=1.0,
    effects=(Effect("volume", (("gain", 0.6),)),),  # audio del PiP (opcional)
)
```

- `Element.in_` es el único campo nuevo. Los proyectos v6 se migran a v7 sin cambios visibles:
  `from_dict` deja `in_=0` y `_migrate_v6_v7` solo sube la versión.
- `Project.validate` comprueba que el asset existe, es de tipo `video` y que
  `in_ + (end - start)` no se sale del archivo.
- `core/elements.layout` trata `image` y `video` igual para el encaje en la caja; el resto de la
  geometría (rotación, z-order, blur de figuras) no cambia.
- El **volumen del audio** reutiliza `Element.effects` con el mismo `Effect("volume")` de los
  clips: `core.elements.audio_gain()` lo lee y `set_audio_gain()` lo edita. No hay campo ni
  migración nuevos; el esquema sigue en **7** y un `gain=1.0` se guarda como «sin efecto».

## Composición

### Export

`core/overlays.apply_graph` añade una entrada por video visible:

```
-ss <in> -t <duración + 0.2> -i <archivo>
```

y una cadena:

```
format=rgba,setpts=PTS-STARTPTS+<start>/TB,fps=<fps>,
[scale/crop según fit],setsar=1,
[rotate], [colorchannelmixer=aa=<opacity>]
```

que se compone con `overlay=...:eof_action=pass:enable='between(t,a,b)*lt(t,b)'`.

- El `setpts` desplaza el stream hasta el inicio del elemento. Probado con FFmpeg 7.1: aparece en
  su intervalo y desaparece en el frame exacto del final.
- `eof_action=pass` deja pasar el video base cuando el PiP termina.
- El orden de composición es: video base → V2/V3 (docs/29) → efectos globales → capa
  «Elementos» (imágenes, figuras y PiP suelto, en orden de `Project.elements`) → textos. Un PiP
  con `video_track=0` sigue en la capa «Elementos».

### Preview

`services.playback.FrameRenderer` decodifica el video del elemento con un `DecoderPool` propio,
aplica la auto-rotación del asset, el encaje, la rotación y la opacidad, y entrega una imagen RGBA
a `core/overlays.apply_frame(..., video_images=...)`. La geometría de la caja es la misma que la
del export (`core/elements.layout`).

### Audio

La mezcla la construye `core/graph._element_audio_mixes()` y la comparten export y preview:

```
[-ss in_ -t duración + 0.2 -i archivo]  (se reutiliza la entrada del overlay si existe)
asetpts=PTS-STARTPTS, aresample=48000, aformat=stereo,
[volume=<gain>], afade in/out de 30 ms,
apad/atrim a la duración, adelay hasta el inicio, apad/atrim al total
```

- En el **export**, el video del PiP y su audio comparten la misma entrada (`-ss/-t`), así que el
  archivo no se abre dos veces. `overlays.apply_graph` deja el índice en `video_inputs`.
- En `build_audio` (preview con música o PiP) no hay overlay y la entrada de audio se añade aquí.
- El audio se suma al `amix` final con `normalize=0`, igual que las pistas extra. Los microfundidos
  de 30 ms evitan clics al entrar o salir a mitad de una onda.
- El **ducking** usa como sidechain la suma del audio del video y de los PiP (`aside`), así una
  voz superpuesta también baja la música.
- Un solo de audio (`Track.solo`) silencia también los PiP, igual que silencia el audio del video.
  La opacidad del elemento es visual: no mutea su audio.
- **Preview**: si hay audio de PiP, `AudioPreview` usa la mezcla exacta del export renderizada a un
  WAV cacheado (`build_audio`), la misma ruta que ya se usaba para música/ducking. Sin PiP audible
  se mantiene la decodificación ligera por segmentos.

## UI

- Fila de medio de video: botón **+** (clip base), botón **PiP** (video superpuesto) y papelera si
  el medio no se usa.
- Pestaña **Capas**: los videos superpuestos se listan como «Vídeo · nombre» y se seleccionan
  igual que imágenes y figuras.
- Inspector del elemento de video: inicio/fin, duración, X/Y, ancho/alto, rotación, opacidad,
  encaje, **Fuente** (segundo de entrada, solo lectura) y **Volumen** (0…4; 0 = silencio) cuando
  el asset tiene audio. Los botones **Silenciar** y **Original** fijan 0 y 1. Si el asset no
  tiene audio se muestra un aviso. El desenfoque se oculta porque todavía no está implementado
  para video.
- Recortar la barra del timeline por la izquierda avanza `in_`; recortar por la derecha no cambia
  la fuente.

## Límites

- Un PiP con `video_track=0` comparte la fila «Elementos» con imágenes y figuras. Para varias
  capas de video con varios clips por capa están las pistas **V2/V3**
  ([docs/29](29-pistas-video.md)), que siguen siendo elementos agrupados por `video_track`.
- El audio del PiP no tiene **handles de fundido propios** ni animación de volumen: solo el
  control lineal del inspector y los microfundidos de 30 ms de los bordes.
- El blur de un elemento de video no se aplica; la UI lo oculta.
- El corte rápido sigue bloqueado por `Project.elements` (como cualquier elemento).
- El volumen del PiP no aparece en la pista «Elementos» del timeline (solo en el inspector).

## Tests

- `tests/test_modelo_v6_base.py`: round-trip de un elemento de video, validación de asset/intervalo,
  migración de esquema y audio como `Effect("volume")` (incluye quitar el neutro y conservar el
  blur).
- `tests/test_pip_video.py` (9):
  - `element_assets.materialize` no intenta rasterizar videos;
  - preview: el PiP aparece solo en su intervalo y en la zona correcta;
  - graph con `setpts`, `eof_action=pass` y **export real** de un proyecto con PiP;
  - graph de audio: reutiliza la entrada del overlay (`[1:a]`), añade `adelay` y mezcla;
  - **export real con audio**: el tono del PiP suena en su intervalo, el resto queda en silencio y
    `gain=0` lo mutea;
  - **webcam**: video base sin audio + PiP con voz; el export lleva pista de audio y suena solo en
    el intervalo del PiP;
  - `build_audio` (la ruta del preview) genera el WAV con el tono del PiP;
  - `AudioPreview` elige la mezcla del export cuando hay PiP audible y `_feed_mixed` entrega el
    tono en su intervalo (preview real, sin dispositivo de sonido).
- `tests/test_project_panel.py`: alta desde el botón PiP del panel, caja en el preview, capa en
  «Capas», recorte izquierdo que avanza `in_` y volumen/silencio desde el inspector.
- `uv run pytest -q`: **421 passed**.
- `uv run aurora check main.py`: OK.

## Archivos tocados

- `src/recstudio/core/project.py` (schema 7, `Element.in_`, validación).
- `src/recstudio/core/store.py` (migración v6 → v7).
- `src/recstudio/core/elements.py` (alta/edición de video, layout, `audio_gain`/`set_audio_gain`).
- `src/recstudio/core/overlays.py` (entrada de video, `video_inputs`, `video_images`,
  `rotate_rgba`).
- `src/recstudio/core/graph.py` (`element_audio`, `_element_audio_mixes`, mezcla y ducking).
- `src/recstudio/services/playback.py` (decodificación/composición del PiP y elección de mezcla).
- `src/recstudio/services/element_assets.py` (omite video).
- `src/recstudio/ui/editor_controller.py` (alta PiP, import directo, recorte de fuente, volumen).
- `src/recstudio/state.py` (`insp_el_gain`).
- `src/recstudio/ui/project_panel.py`, `ui/screens/editor.py`, `ui/icons.py`.
- `tests/test_modelo_v6_base.py`, `tests/test_pip_video.py`, `tests/test_project_panel.py`.
