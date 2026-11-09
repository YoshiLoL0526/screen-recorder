# AGENTS.md

Rec Studio: grabador de pantalla + editor de video para Windows. UI con **aurora-ui** 0.10
(núcleo Rust Skia/Taffy), captura WGC/DXGI/mss, audio WASAPI (PyAudioWPatch) y encode por
**FFmpeg** en subprocess. Todo el proyecto —docs, comentarios, UI, tests y commits— está en
**español**; los commits usan prefijos `feat:` / `docs:`.

Estado: **v1.3.0** — F1 (motor), F2 (UI de grabación + Biblioteca), F1.1/F2.1, **F3 (editor)**,
**F4 (export y efectos)** y **F5 (pulido)** implementados. Editor: `core/project.py`, `core/timeline.py`,
`core/store.py`, `services/media.py`/`playback.py`, `ui/editor_controller.py`, `ui/timeline.py`,
`ui/screens/editor.py` (ver `docs/12`). Export: `core/effects.py`, `core/graph.py` (builder puro
del filter graph), `core/keyframes.py` (corte sin recodificar), `services/export.py`,
`ui/export_modal.py` (ver `docs/13`). Pulido: layout adaptable, divisores del editor, Ajustes,
icono, `[tool.aurora.build]` y smoke del exe en `src/recstudio/smoke.py` (ver `docs/14`). Post-v1:
bandeja del sistema y atajos globales (`ui/tray.py`, `platform/hotkeys.py`), filmstrip de miniaturas
(`services/media.thumbstrip`, `ui/timeline.filmstrip_slots`), **bloque A de entrega** (CI verde,
RNF-06/07, instalador Inno Setup y bundle sin OpenCV; ver `docs/16`) y **bloque B del editor**
(música/ducking, textos, selección múltiple, snap por waveform, proxy y reubicar carpetas; ver
`docs/17`), **fases 0/1 de audio** (modelo v3, audio absoluto editable) y **fase 2** (modelo v4,
lienzo configurable, transformación por clip y auto-rotación; ver `docs/19` y `docs/20`) y
**fase 3** (modelo v5, flujo de edición: portapapeles, marquesina que mueve, razor, marcadores,
In/Out, loop, J/K/L, Q/W y export de rango; ver `docs/21`) y las **features v6** (modelo 6):
canvas interactivo (`core/canvas_edit.py`, `ui/screens/editor.py`; `docs/23`), audio del video
separado y enlazado por defecto (`core/timeline.py`, `ui/editor_controller.py`; `docs/24`),
elementos superpuestos (`core/elements.py`, `core/overlays.py`, `services/element_assets.py`;
`docs/25`) y efectos globales (`core/global_effects.py`, `core/color.py`; `docs/26`), con el plan
en `docs/22`. Después se añadió el **panel de medios/capas** (`ui/project_panel.py`), la
**selección directa de clip/elemento/texto** en el preview (`ui/element_overlay.py`,
`ui/screens/editor.py`) y los **carriles de textos/elementos** en el timeline
(`ui/timeline.py`); ver `docs/27`. Después se añadió el **PiP de video** como
`Element.kind="video"` (modelo **v7**, `Element.in_`, audio con `Effect("volume")`,
preview/export; `docs/28`). Después llegaron las **pistas de video V2/V3** (modelo **v8**,
`Element.video_track`, composición por capas y filas propias en el timeline; `docs/29`) y los
**subtítulos SRT/VTT + keyframes** (modelo **v9**, `TextOverlay.subtitle/opacity/animations` y
`Element.animations`; `docs/30` y `docs/31`), las **envolventes de volumen de audio** (modelo
**v10**, `Clip.envelope`, `core/audio_envelope.py`; `docs/32`), la **animación de escala y
rotación de elementos** (`core/animation.py`, `core/elements.py`,
`services/element_animation.py`; `docs/33`), las **envolventes de audio de elementos** (modelo
**v11**, `Element.envelope`; `docs/34`) y la **escala y rotación animadas de textos**
(`TextOverlay.animations`, `services/text_animation.py`; `docs/35`), el **color y tamaño
animados de textos** (`TextOverlay.color_keys`, `core/text_color.py`; modelo **v12**, `docs/36`)
y la **edición de claves de envolvente en el timeline** (asas sobre la waveform/barra, modo Env
y undo por gesto; `docs/37`), las **asas de animaciones visuales** (selector de canal en el
inspector, polilínea y asas sobre la barra de elementos/textos; `docs/38`), el **easing de las
claves de animación** (`Keyframe.easing`, curvas cúbicas en preview/export y selector del
inspector; modelo **v13**, `docs/39`), el **easing de las envolventes de volumen**
(`VolumePoint.easing`, las mismas curvas en preview/export y selector en las dos secciones de
envolvente; modelo **v14**, `docs/40`), el **copiar/pegar de claves** (`KeyClipboard` en el
controlador, menús del asa y `Ctrl+C`/`Ctrl+V`; `docs/41`), el **modo «añadir clave» desde el
timeline** (`state.key_add_on`, tecla **N**, `keyframe_default`/`keyframe_value_at`; `docs/42`),
el **recorte interactivo en el preview** (`core/crop_edit.py`, `state.crop_on`, tecla **T**;
`docs/43`), el **texto multilínea con ancho máximo** (`TextOverlay.wrap_width`,
`core/text_layout.py`, bake RGBA de los textos envueltos; modelo **v15**, `docs/45`), los
**fundidos de audio con crossfade por corte en el timeline** (`ui/timeline.py`,
`ui/editor_controller.py`, `core/timeline.set_audio_transition`; modelo v16 sin cambios,
`docs/46`) y el
**export personalizado** con tamaño/fps/bitrate/códec y presets sociales (`core/graph.py`,
`services/settings.py`, `ui/export_modal.py`; `docs/44`), el **fondo del lienzo** (color, blur
de V1 o imagen; `core/background.py`, modelo **v16**, `docs/47`), los **formatos GIF/WebM**
(`ExportPreset.format`, VP9/Opus y paleta GIF con límites de 20 fps/720 px; `docs/48`) y la
**webcam junto a la pantalla** (`platform/camera.py`, `CameraConfig`, `<nombre>-camara.mp4` y
PiP automático con `core.elements.add_camera_pip`; `docs/49`) y las **pistas de micrófono
separadas** (`AudioConfig.mode`, `<nombre>-microfono.wav`, sidecar `mic` e importación como pista
absoluta «Micrófono»; `docs/50`). Lo siguiente es el resto de la fase 4 (más composición y
export), además del backlog de `docs/15` (A2/A6/A7/A8 manuales y el resto del bloque C).

## Comandos

```bash
uv sync                                         # entorno (Python 3.12, ver .python-version)
uv run pytest                                   # 655 tests; la integración usa FFmpeg real
uv run aurora check main.py                    # construye y pinta un frame: errores + avisos de layout
uv run aurora tree main.py                     # árbol pintado en texto y avisos (texto, tamaño, nombres)
uv run aurora docs <tema>                      # referencia de la versión instalada (button, pilot…)
uv run pytest tests/test_ffmpeg_cmd.py -k mux   # un test concreto
uv run python main.py                           # la app
uv run aurora dev main.py --inspector           # hot reload + inspector (F12)
uv run python tools/rec.py --list               # CLI de grabación (--probe-encoders, --recover…)
uv run python tools/f2_ui_test.py               # e2e de la UI (app real con App.pilot, sin ventana)
uv run python tools/f3_editor_test.py           # aceptación del editor (+ capturas en docs/assets/f3)
uv run python tools/f4_export_test.py           # aceptación del export (NVENC, sync, byte a byte; --skip-long)
uv run python tools/f5_screenshots.py           # capturas de F5 (docs/assets/f5, contenido sintético)
uv run python tools/check_build.py              # build ligero (sin OpenCV) + smoke (--skip-build: solo el smoke)
uv run python tools/build_exe.py                # build suelto; `uv run aurora build` incluye cv2 (dxcam)
uv run python tools/check_installer.py          # Inno Setup + instalación/desinstalación + smoke
uv run python tools/rnf_measure.py --exe "dist/Rec Studio/Rec Studio.exe"  # RNF-06/07
uv run python tools/make_icon.py                # regenera assets/icon.ico
uv run python tools/release_notes.py v1.3.0     # notas del release (falla si el tag ≠ versión)
```

No hay linter, formatter ni typechecker configurado; no inventes uno. `uv run aurora build` usa
`[tool.aurora.build]` (onedir, sin consola) → `dist/Rec Studio/`; el build de release pasa por
`tools/build_exe.py`, que añade `--exclude-module cv2` porque Aurora 0.10 sigue sin exponerlo. CI:
`.github/workflows/ci.yml` (windows-latest: `uv sync --locked` + `pytest`, `tools/check_build.py`
con el exe como artefacto y `tools/check_installer.py` con el instalador); los tests no pueden
depender de monitor, GPU ni audio reales del runner.

## Arquitectura (reglas duras)

- Dirección de dependencias única: `ui → services → core` y `services → platform → core`.
  `core/` es puro (sin aurora, sin PyAV, sin SO) y se testea sin ventana; `platform/` encapsula
  Windows (captura, WASAPI, FFmpeg).
- Composición v6/v7/v8/v9: el modelo es `Element`/`Project.elements` (imágenes, videos, figuras
  y las pistas V2/V3), `Project.texts` (textos y subtítulos) y `Project.effects` (globales).
  `Element.video_track` agrupa los videos en V2/V3 (0 = capa «Elementos»); **no hay `Track`
  nuevas**. El **orden es fijo** y compartido por
  preview y export: V1 compuesto → V2/V3 → efectos globales (`core/global_effects.py`) → capa
  «Elementos» (`core/overlays.py`) → textos. `core/color.py` concentra las curvas de color
  compartidas; `core/transform.py` y `core/elements.py` son la única geometría de clip y de
  elemento, respectivamente. Las figuras y el blur se materializan como PNG RGBA en
  `services/element_assets.py` (cache atómica); el core nunca rasteriza ni toca disco. Los videos
  V2/V3/PiP se decodifican en preview y se añaden como entradas `-ss/-t` en el graph; usan la
  misma caja normalizada (`Element.in_` fija la fuente). Su audio se mezcla con
  `_element_audio_mixes` (reutiliza la entrada del overlay) y el volumen vive en
  `Element.effects` como `Effect("volume")`; el preview usa `build_audio` si oye un video.
  Los keyframes (`core/animation.py`) animan **x/y** de elementos y **x/y/opacity** de textos, y
  **scale/rotation** de elementos (docs/33) y **scale/rotation/size** de textos (docs/35 y
  docs/36): el preview evalúa `value_at` y el export genera expresiones `if(lt(t,…))` para
  `overlay` y `drawtext`; la escala/rotación/tamaño de textos se hornea a un vídeo RGBA
  intermedio (`services/text_animation.py`), igual que la escala/rotación de elementos
  (`services/element_animation.py`), porque `overlay` necesita un tamaño constante. El **color
  animado** de textos es un modelo aparte (`core/text_color.py`, `TextOverlay.color_keys`) y
  también se hornea. No hay animación de color en elementos ni clips. Las **envolventes de
  volumen** (`core/audio_envelope.py`, `Clip.envelope` y `Element.envelope`) son un modelo
  aparte: ganancias 0…4 en tiempo local al clip/elemento, easing por clave (`linear`, `ease_in`,
  `ease_out`, `ease_in_out`; docs/40), `volume=…:eval=frame` en el export y preview por
  `build_audio` (docs/32 y docs/34).
- La UI nunca bloquea: nada de IO/subprocess en handlers. Lo asíncrono comunica por `Signal`
  (único canal cross-thread) o `app.spawn()`. Todo el estado de la app vive en
  `src/recstudio/state.py`.
- `Signal.set` compara con `==`: cualquier dataclass que viaje por un Signal debe ser
  `frozen=True` (regla de proyecto).
- Grabación: hilo de captura → rawvideo por stdin → FFmpeg a `.mkv` temporal; al parar, mux
  (`-c:v copy` + AAC 48 kHz) → MP4 en `~/Videos/RecStudio` + sidecar JSON + miniatura. Las
  sesiones en curso quedan en `%LOCALAPPDATA%\Temp\recstudio` para recuperación.
- El paquete importable es `recstudio` (en `src/`), aunque el proyecto en `pyproject.toml` se
  llame `screen-recorder`; `main.py` es solo el lanzador.

## Gotchas verificados (ver `docs/09` → «Bugs reales encontrados»)

- FFmpeg: `RECSTUDIO_FFMPEG` (env) → binario del wheel de `imageio-ffmpeg`. Los encoders se
  prueban **codificando de verdad** y se cachean en `%LOCALAPPDATA%\RecStudio\cache` (AMF/QSV
  pueden aparecer en `-encoders` y no funcionar sin hardware).
- PyAudio/WASAPI: una única instancia de PyAudio por proceso (`_shared_pa()`), nunca
  terminarla; el callback debe devolver `paContinue` (0) y usar la firma exacta con `time_info`.
- Webcam (docs/49): captura **FFmpeg + DirectShow** (`platform/camera.py`), sin OpenCV. La
  cámara es un proceso aparte a `camera.mkv` (solo vídeo) y se remuxa a `<nombre>-camara.mp4` al
  parar; si falla o desaparece, se avisa y la pantalla sigue. `CameraRecorder.start()` reintenta
  sin `-video_size`/`-framerate` y `stop()` cierra con `q` por stdin. La pausa se deshabilita con
  cámara activa. El `offset_s` del sidecar alinea el PiP automático (`in_`/inicio).
- Micrófono separado (docs/50): `AudioConfig.mode="separate"` muxa **solo la primera pista**
  (sistema) con `mux_cmd(mix=False)` y copia `audio_mic.wav` a `<nombre>-microfono.wav` **antes**
  de borrar el temporal. El sidecar guarda `mic` (`file` solo el nombre, `offset_s` negativo si el
  mic arrancó antes) y `mix` lo deja en `null`. La importación crea una pista **absoluta**
  «Micrófono» en `clip_start + offset_s`; con offset negativo inserta con margen, recorta con
  `trim_audio` y vuelve a `move_audio` para no adelantar el audio. El esquema no cambia (16).
- WGC: `frame_buffer` ya es un numpy BGRA contiguo (copiar: el buffer se recicla);
  `convert_to_bgr()` muta el frame y devuelve el propio `Frame`, no un array.
- Deriva del reloj de audio (~90 ppm): se compensa en el mux con `asetrate` calculado por
  regresión al parar; `aresample=async` **no** la corrige.
- Preferencias en `%APPDATA%\RecStudio\settings.json`. Los tests aíslan rutas con
  `monkeypatch` sobre `paths.settings_path`; sigue ese patrón para persistencia nueva.
- Diálogos nativos: `ui/file_dialogs.py` envuelve `aurora.dialogs` y se llama **desde el hilo de
  UI** (la ventana es el padre y el sistema corre su bucle mientras dura); el trabajo lento que
  sigue va a un hilo. En los tests se sustituye el módulo por un doble (`tests/test_file_dialogs.py`).
- Escritorio (bandeja y atajos globales): se enganchan solo con ventana real
  (`Controller.attach_desktop(app)`, llamado desde `main()`); en los tests `controller._app` es
  `None` y todo es no-op. El hook de `pynput` es **opt-in** y se prueba con un backend falso
  (`tests/test_desktop.py`); si `start()` falla, se revierte el ajuste y se avisa. El icono de la
  bandeja viaja en el exe por `[tool.aurora.build] include = ["assets"]` y el smoke lo comprueba.
- Aurora 0.10: `scroll()` vertical sin `height`/`max_height` añade `grow=1` (pasa `grow=0` en
  filas); columna que debe encoger: `grow=1, width=0`; los `select` no truncan etiquetas. Más
  límites y mitigaciones en `docs/05` («Limitaciones de Aurora 0.10»).
- Aurora 0.10 valida la API al crear el elemento: una prop o un `on_<evento>` desconocidos lanzan
  `TypeError` con sugerencia, y las props enumeradas (`justify`, `align`, `variant`…) lanzan
  `ValueError` si el valor no existe. Los tests cubren casi todas las vistas, pero un error en una
  rama no visitada solo aparece al abrirla: `uv run aurora check main.py` pinta el primer frame y
  `aurora tree` imprime avisos (`text-overflow`, `zero-size`, `offscreen`, `no-label`).
- Accesibilidad (0.10): un botón de solo icono, un `switch` o un `text_input` necesitan `label=`;
  el texto de al lado no cuenta. `icon_button` lo deriva del tooltip y `field`/`_row` lo copian al
  control, así que `aurora check` debe seguir sin avisos `no-label` (los modales llevan `label` en
  el fondo y en los campos).
- Filmstrip del timeline: `canvas()` no dibuja imágenes, así que las miniaturas son una capa de
  `image()` absolutas **entre el canvas y las cajas de interacción** (arriba del canvas para verse,
  debajo de las cajas para no comerse clics ni drags). Tira por **asset** (`media.thumbstrip`,
  cacheada), colocada por `filmstrip_slots` (puro); como tapan el texto del canvas, el nombre y la
  duración se repintan encima con su propio fondo (`_clip_meta` lo comparten ambos).
- Cache de miniaturas en disco: escribe cada JPEG con **tmp + `os.replace`** y
  `cached_thumbstrip` rechaza lo que no empiece por `\xff\xd8`; la UI la lee mientras el hilo
  genera y un archivo a medio escribir dejaba un JPEG de 0 bytes que tumbaba el render (lo
  destapó el CI; no volver a `write_bytes` directo).
- Aurora 0.10 (F3): cualquier caja encima bloquea los clics aunque no tenga handlers (playhead,
  toasts); la raíz recibe las teclas aunque haya un campo enfocado (usa `mark_input_key`);
  `KeyEvent.matches("+")` lanza excepción; `on_drag` llega también con un clic (fase `start`,
  x de ventana → `TL_X0`). En Windows, `Condition.wait` va a saltos de ~15.6 ms: para relojes
  usa `time.sleep`.
- Export (`docs/13`, `docs/44` y `docs/48`): cada clip es **una entrada** (`-ss IN -t LEN -i`) y
  dura un número entero de frames (`tpad` + `trim=end_frame`; el audio, `apad`+`atrim` a lo
  mismo): no cambies eso sin revisar los golden de `tests/test_f4_graph.py`. El concat del corte
  rápido necesita `-auto_convert 0` o los keyframes dejan de ser idénticos. Preview y graph comparten
  las curvas de `core/effects.py` (lo exportado = lo visto). El export personalizado reescala el
  proyecto a `preset.fps` antes de calcular frames (el `fps=<objetivo>` va en la cadena de cada
  clip) y sustituye CRF/CQ por `-b:v`/`-maxrate`/`-bufsize`; sin esas opciones las cadenas
  históricas no cambian. `ExportPreset.format` añade WebM (VP9/Opus) y GIF (paleta, 20 fps y
  720 px como máximo, sin audio); sin `format` el camino sigue siendo el MP4 byte a byte. La
  configuración vive en `Settings`, no en `Project`; `copia` no admite tamaño/fps/bitrate ni
  formatos distintos de MP4.
- v6 (`docs/22`–`26`): al importar un video con audio se separa a «Audio del video» y el clip de
  video queda con `audio_enabled=False`; el audio enlazado se recoloca con
  `tl.sync_linked_audio` en mover/recortar/dividir/cambiar velocidad y **no** se mueve por su
  cuenta (hay que desvincularlo). El preview mide el panel (`state.preview_area`) y pinta un
  escenario con aspecto de proyecto; `state.preview_box` es el tamaño de ese escenario y las cajas
  de clip/elemento viven dentro (`ui/screens/editor.py`). El blur de elementos se hornea en el PNG
  (Pillow) y el de efectos globales se escala al alto de salida; sin efectos/elementos el graph es
  byte a byte el histórico. Los PNG de elementos se escriben con tmp + `os.replace`.
- Panel de medios/capas (docs/27): `Project.assets` ya existía, así que el panel no cambia el
  esquema. `ui/project_panel.py` no decodifica video en el render: los pósters van a
  `state.posters` desde un hilo (`EditorController._queue_posters`) y la miniatura de imagen usa
  `image(path)`. Los drags internos usan `on_drag` con `DragEvent.target == "tl-content"` o
  `"ed-stage"`; el panel acepta archivos del Explorador con `on_drop`. Los drags de preview y de
  las barras de capa abren la transacción **en el primer movimiento** (`begun=False`): un clic que
  solo selecciona no debe dejar `ProjectStore._tx` abierta, o el siguiente `store.apply` se
  convierte en `update` y un `cancel_drag` revierte cambios ajenos.
- Capas en el timeline (docs/27): las filas «Textos»/«Elementos» viven **antes** de la pista de
  video y desplazan `VIDEO_TOP`; usa `Tracks.video_top`, no la constante, en canvas, filmstrip,
  cabeceras y cajas. `max_tracks_extra` descuenta `OVERLAY_H` por grupo para no romper el mínimo
  del preview. Una capa de cada tipo comparte fila: si se solapan, la barra superior gana el clic y
  la oculta se elige desde «Capas».
- El inspector de elementos/textos usa `number_input`/`color_picker` de Aurora 0.10 y sigue
  escuchando los mismos `Signal` que los `select` anteriores; no añadas handlers `on_change` que
  dupliquen la escritura del Signal. El campo de texto del título necesita un `box(grow=1)` (no
  `width=0`) o Taffy lo mide con ~22 px.
- Video superpuesto (docs/28 y docs/29): `Element.kind="video"` exige asset de video y
  `in_ + (end-start) <= asset.duration`; el schema es **9**. `Element.video_track` (0 = Elementos,
  1 = V2, 2 = V3) solo vale en videos y el orden de `Project.elements` debe ser
  V2 < V3 < Elementos. `add_element`, `set_video_track`, `timeline.paste` y `from_dict` mantienen
  ese orden; si tocas la composición, aplícalo en dos pasadas y no metas la capa Elementos antes
  de los efectos globales. El preview decodifica con `FrameRenderer.element_pool` y
  `core/overlays.apply_frame(video_images=…)`; el export usa `-ss/-t` + `setpts` + `overlay` con
  `eof_action=pass`. Los efectos globales van **después** de V2/V3 y antes de Elementos; la pasada
  de V2/V3 usa prefijo `vt` en las etiquetas. El audio usa `core.elements.audio_gain`
  (`Effect("volume")` dentro de `Element.effects`, sin campo nuevo), se mezcla con
  `_element_audio_mixes` y **reutiliza la entrada del overlay** (`video_inputs`) en el export;
  `build_audio` abre la suya. `AudioPreview` elige la mezcla WAV cacheada en cuanto hay audio de
  video superpuesto. Un solo de audio silencia también V2/V3. `element_assets.materialize` debe
  omitir los videos. El recorte izquierdo de la barra (controller `drag_overlay`) avanza `in_`;
  mover no lo cambia. No hay razor ni transiciones en V2/V3: son elementos, no una `Track`.
- Subtítulos y keyframes (docs/30, docs/31, docs/33, docs/35, docs/36 y docs/39):
  `TextOverlay.subtitle` marca los cues importados (SRT/VTT) y `opacity` es la base;
  `Animation`/`Keyframe` viven en `Element.animations` y `TextOverlay.animations`. Los tiempos de
  las claves son **locales** al elemento/texto. El preview y el export comparten
  `core.animation.value_at`/`expr`; si tocas una expresión, cita con `'…'` los valores de
  `overlay`/`drawtext` que lleven comas. Cada `Keyframe` lleva `easing` (el de la clave izquierda
  aplica al tramo) y `linear` debe conservar la cadena histórica byte a byte; las curvas se
  escriben con multiplicaciones y `SCHEMA_VERSION` es **13**. Los
  elementos animan x/y/scale/rotation; los textos, x/y/opacity/scale/rotation/size. El **color**
  de texto no es un `Animation` (float): vive en `TextOverlay.color_keys`
  (`core/text_color.py`) y se interpola en sRGB. La escala/rotación/tamaño se hornea a un `.mov`
  `qtrle` RGBA con PIL (`services/element_animation.py` y `services/text_animation.py`) y se
  superpone con X/Y como siempre; el color animado usa el mismo bake. Sin esos canales, las
  cadenas del graph son las de antes. Los textos se hornean también si son multilínea
  (`wrap_width > 0` o `\n`, docs/45); el resto de estáticos sigue con `drawtext` y el orden de
  `Project.texts` se conserva encadenando cada texto sobre la etiqueta anterior. La caja del
  preview se oculta si hay geometría animada (x/y/scale/rotation/size en textos y
  x/y/scale/rotation en elementos). `text_values` devuelve `(x, y, opacity, scale, rotation)`;
  `text_style_values` devuelve `(size, color)` aparte para no cambiar esa firma;
  `format_srt` exporta solo `subtitle=True`. El import SRT/VTT sigue uniendo los cues con
  espacios y `wrap_width=0.0`; el modelo ya admite `\n` y ajuste automático (docs/45).
- Texto multilínea (docs/45): `TextOverlay.wrap_width` es una fracción del ancho del lienzo
  (`0` = sin límite, mínimo `0.1`). `core/text_layout.wrap_text` es puro (sin PIL) y estima el
  ancho con una tabla de Arial; preview y bake usan **el mismo** helper y el mismo tamaño del
  proyecto, así que el corte no depende de la resolución. `_text_layout`/`text_rgba` dibujan
  todas las líneas centradas con `LINE_HEIGHT = 1.15` y bloque centrado en `(x, y)`;
  `ui/element_overlay.text_box` calcula el bloque completo. Un cambio en el helper o en el
  interlineado afecta a la paridad: cámbialo en un solo sitio.
- Fondo del lienzo (docs/47): `Project.background` sigue siendo el color; `background_mode`
  (`color`/`blur`/`image`), `background_blur` (0…100 px del proyecto) y `background_asset`
  añaden blur de V1 e imagen de fondo (modelo **v16**). `core/background.py` decide el modo
  efectivo (una imagen ausente o que no sea imagen cae a color) y la geometría cover; preview y
  export lo comparten. El fondo se compone **antes de V1** (blur: `split`+`boxblur`+`overlay`;
  imagen: entrada `-loop 1` después de los clips, `split` y `overlay`) y V2/V3, efectos,
  elementos y textos siguen encima en el orden de siempre. El modo color conserva el graph
  byte a byte; `needs_custom_render` y `keyframes.blockers` consideran el fondo especial. La
  imagen del fondo cuenta como archivo usado (`remove_asset`, `export.offline_assets`).
- Envolventes de volumen (docs/32, docs/34 y docs/40): `Clip.envelope` y `Element.envelope` son
  independientes de `Animation`; sus claves son locales al clip/elemento (0…duración) y la
  ganancia 0…4. Cada clave tiene `easing` (el de la clave izquierda aplica al tramo) y `expr`
  reutiliza `animation.ease`/`_curve_expr`: `linear` conserva la cadena histórica byte a byte.
  El export añade `volume=volume='<expr>':eval=frame` **después** del volumen estático, así que
  la curva multiplica; en un clip entrante con transición la expresión usa `start=-skip` para
  alinear el tramo audible y en un elemento usa `start=0` (la fuente se lee desde su `in_`; el
  `adelay` va después de la curva). Sin envolvente la cadena no cambia. El preview usa
  `build_audio` si hay audio de elemento o `graph.has_audio_envelope` (WAV cacheado con el
  proyecto entero). El corte rápido se bloquea. Las claves se editan en el timeline con asas
  (`docs/37`): capa aparte **encima del playhead** (su línea de 2 px bloquearía el centro de un
  asa en ese instante), `dx`/`dy` = tiempo/ganancia, modo **Env** (`E`) para añadir con un clic
  y `Supr`/menú para borrar; la transacción empieza en el primer movimiento (`begun=False`). En
  las barras de elemento se dibujan solo asas (sin polilínea). El audio de un elemento con gain 0
  sigue en silencio aunque tenga curva. El selector **Easing** (`state.insp_env_easing`, no
  persistido) decide las claves nuevas (también las del modo Env) y regrabar el mismo instante
  actualiza el easing; arrastrar un asa lo conserva.
- Fundidos y crossfades de audio en el timeline (docs/46): las asas de `audio_fade_in/out` son
  cajas de la capa `audio_handles_layer`, **encima del playhead** y en el hueco superior del clip
  (no tapan el nombre ni el trim); el límite es `fx.audio_fade_limit` = `min(2 s, duración/2,
  duración − otro fundido)` y el inspector sigue ofreciendo hasta 3 s. Al editar con el asa se
  quita el `fade_in/out` legado del mismo lado para que volver a 0 no lo herede (solo en pistas
  de audio). El crossfade por corte usa `core.timeline.set_audio_transition` sobre
  `Track.transitions` (solo `mode="sequence"`, acotado por `transition_limit`); arrastrar el asa
  a la izquierda alarga y el asa sigue al puntero porque está en el inicio del clip entrante.
  Fundido y crossfade abren `fundido de audio`/`crossfade de audio` con `begun=False`; `Esc`
  revierte y un clic no deja `_tx`. Las pistas absolutas no muestran asa de crossfade.
- Asas de animaciones visuales (docs/38): `state.keyframe_channel` elige el canal (no se
  persiste) y `state.keyframe_key` la clave seleccionada; el controlador corrige el canal al
  cambiar entre elemento y texto. La polilínea se pinta en el canvas (`_draw_animation_curve`) y
  las asas en `animation_handles_layer`, otra capa **encima del playhead**. La geometría es
  `keyframe_value_y`/`keyframe_value_at_y` (rango de `animation.CHANNEL_RANGES`) y
  `keyframe_x`/`keyframe_time`; `dy` se pasa como alto de fila y el controlador descuenta los 4 px
  de margen, no le pases ya el alto interno.
- Copiar/pegar claves (docs/41): `state.KeyClipboard` es un dataclass frozen que vive en
  `EditorController._key_clipboard`, nunca en `Project`; comparte un único portapapeles lógico con
  `tl.Clipboard` (gana el último copiado). `Ctrl+C` copia la clave/punto seleccionado antes de
  caer al copiado de clips; `Ctrl+V` despacha por `kind`. El pegado usa la capa y el canal
  activos, acota el playhead a `0…duración`, valida el valor con `anim.validate_value` y usa
  `store.apply` (`pegar clave X`/`pegar punto de volumen`). Los no-op no abren transacción ni
  dejan `_tx`; el menú del asa llama a `copy_keyframe_key_at`/`copy_envelope_key_at` y
  `paste_key`.
- Añadir claves desde el timeline (docs/42): `state.key_add_on` es UI no persistida y se
  alterna con **N** o el `switch` del inspector; es **excluyente** con razor, marquesina y Env
  (el controlador escucha el Signal y apaga los otros). La zona `anim-add-*` va en
  `animation_handles_layer`, **antes** que las asas y encima del playhead; `begin_keyframe_add`
  solo actúa sobre la capa seleccionada, convierte x con `keyframe_time` y toma el valor de
  `keyframe_value_at` (interpolado o base: `scale` 1.0 y `rotation` la base de la capa, 0.0 en
  textos). El clic abre una transacción `añadir clave X` con `begun=True` y no mueve la capa; al
  arrastrar una clave se conserva su `easing` (`drag["easing"]`), no lo resetees a lineal.
- Recorte interactivo (docs/43): `state.crop_on` es UI no persistida (tecla **T** y `switch` de
  Transformación), excluyente con razor/marquesina/Env/añadir clave y solo con un clip de V1
  seleccionado en pausa. Mientras está activo, `_preview_project` entrega al `Player` una copia
  con el `crop` del clip en `(0, 0, 1, 1)` (`core.crop_edit.uncropped_project`): **no** se aplica
  al store. `core/crop_edit.py` mapea el crop normalizado sobre el layout completo (vale con
  rotación/flip) y `_crop_layer` sustituye las cajas normales por velo + rectángulo + 8 asas. El
  drag usa `begun=False`, una transacción `recortar clip` y `tl.set_transform`; `Esc` cancela el
  gesto y después sale del modo. La capa se oculta si el playhead no está en el clip seleccionado.
- Aurora 0.10: una fila `grow=1` cuyo hijo puede ser más alto que el hueco (inspector) necesita
  `height=0`; en una columna con `align` distinto de `"stretch"`, un contenedor sin ancho ya mide
  como mucho el de la columna (fit-content) y parte el texto, así que no hace falta forzar anchos.
  Los handlers suscritos a `Signal` globales deben ignorar eventos tras `shutdown`
  (los tests crean varias apps por proceso; `subscribe` devuelve un desuscribidor, pero el
  proyecto conserva guardas `live()`/`_closing`).
- Editor: el canvas del timeline usa una función de dibujo de módulo (misma identidad ⇒ Aurora
  no redibuja si no cambian sus `Signal`); el playhead va en su propio `@component`. Proyectos en
  `<grabaciones>/Proyectos/*.rsproj/project.json`.
- Bloque B: `Project.audio` se dibuja en una pista **M** y es secuencial; `Track.ducking` solo
  tiene sentido en pistas de audio. El preview con música se renderiza a un WAV cacheado con
  `core.graph.build_audio` (misma mezcla, crossfades y ducking que el export), no se mezcla en
  Python. Los textos viven en `Project.texts` (no en `Effect`, que solo lleva floats) y se queman
  con `drawtext` (`expansion=none`, texto escapado) y PIL en el preview. Los proxies 540p solo se
  usan en preview (el export usa el original) y se cachean con clave ruta+tamaño+mtime.
- La marquesina (B3) es un modo explícito (`state.marquee_on`): el canvas deja de hacer scrub y
  selecciona por rango de tiempo. `state.multi_selection` es la fuente de la selección múltiple;
  `state.selection` sigue siendo la selección primaria que alimenta el inspector.
- Fase 3 (`docs/21`): la razor (`state.razor_on`) es otro modo explícito, **excluyente** con la
  marquesina. Arrastrar un clip que está en `multi_selection` mueve el bloque (`tl.move_block`,
  drag `block`). El inspector se oculta con **`P`**; `I`/`O` son In/Out del timeline
  (`Project.range_in/out`, distintos de `mark_in`/`mark_out` del clip) y `B` añade marcador (`M`
  es el audio del preview). Los atajos con Ctrl/Alt devuelven `None` si la tecla viene de un
  campo (`from_input`), para no romper copiar/pegar texto. Pegar/mover video **no** desplaza el
  audio absoluto que no va en la selección. El export de rango es un `trim` final sobre el graph
  completo (`graph.build(span=…)`); sin `span` la salida es byte a byte la de siempre.
- Miniaturas: el filmstrip vive en `thumbs2/` y los pósters en `…-{alto}-u.jpg` desde que se
  enderezan los assets rotados (`media.upright_rgb`); si cambias su contenido, cambia la clave.
- Layout (F5): todo contenedor `grow=1` cuyo contenido puede no caber (imágenes, pantallas
  largas) lleva `height=0` (columna) o `width=0` (fila); si no, crece y empuja controles fuera
  de la ventana. El alto útil de la ventana es `state.view_h` (lo da el `on_resize` de la
  navegación, desde Aurora 0.7); la geometría de pistas sale de `ui/timeline.tracks()`, no de
  constantes.
- `settings.json` se escribe atómico (tmp + `os.replace`); los campos nuevos de `Settings` se
  validan en `normalized()`. `platform.ffmpeg.run` usa `stdin=DEVNULL` (exe sin consola).
- Build de release: `tools/build_exe.py` excluye `cv2` (OpenCV, 112 MB) porque `windows-capture`
  solo lo usa en `save_as_image()`; `platform/cv2_shim.py` cubre ese import y `dxcam` falla con
  un `CaptureError` claro en el exe ligero. `uv run aurora build` sí incluye OpenCV (para `dxcam`).
  UPX se midió (78 MB) pero rompe RNF-06 (3.66 s de arranque, +107 MB de RSS): `upx = false`.
- Instalador: `installer/rec-studio.iss` (Inno Setup 6) es **por usuario** (`PrivilegesRequired=lowest`,
  sin UAC) y `tools/check_installer.py` compila, instala en `%TEMP%`, hace smoke y desinstala.
  El CI lo repite en un runner limpio; el instalador de 1.0.0 pesa 72 MB.
- `~/Videos` puede estar bloqueado por el «Acceso controlado a carpetas» de Defender (devuelve
  WinError 2 en el `mkdir`): crea carpetas con `paths.ensure_dir()` (nunca lanza) y no resuelvas
  rutas con efectos de disco al importar módulos (`state.out_dir` arranca vacío). Ver `docs/09` →
  «Bugs reales encontrados» (7).
- Tests de UI: construyen la app real sin ventana con `build_app()` + `app.pilot(size=…)`; los
  que capturan pantalla hacen `pytest.skip` si no hay escritorio disponible. Los scripts de
  `tools/` (soak, av_sync, recovery, f2_ui) son pruebas de aceptación manual y algunos necesitan
  escritorio y audio reales; no forman parte de `pytest`.

## Documentación (fuente de verdad)

- `docs/02-arquitectura.md`: capas, hilos y flujos. `docs/06-roadmap.md`: estado por fases
  (F0–F5). `docs/07-decisiones.md`: ADRs (por qué FFmpeg, PyAV, MKV→MP4, canvas híbrido…).
- `docs/09`–`14`: resultados de F1, F2, F1.1+F2.1, F3, F4 y F5 (bugs, mediciones, aprendizajes
  de UI). `docs/16-bloque-a-entrega.md`: CI, RNF-06/07, instalador, bundle ligero y las pruebas
  manuales pendientes. `docs/17-bloque-b-editor.md`: música/ducking, textos, selección múltiple,
  snap por waveform, proxy y reubicar carpetas. `docs/22-plan-features-editor.md` es el plan de la
  composición v6 y `docs/23`–`26` sus resultados (canvas, pistas A/V, elementos y efectos
  globales). `docs/27` cubre el panel de medios, la selección directa y las capas del timeline;
  `docs/28` el PiP de video (modelo v7), `docs/29` las pistas V2/V3 (modelo v8), `docs/30`/
  `docs/31` los subtítulos y keyframes (modelo v9), `docs/32` las envolventes de volumen de audio
  (modelo v10), `docs/33` la animación de escala/rotación de elementos, `docs/34` las envolventes
  de audio de elementos (modelo v11), `docs/35` la escala/rotación de textos, `docs/36` el color y
  tamaño animados de textos (modelo v12), `docs/37` la edición de claves de envolvente en el
  timeline, `docs/38` las asas de animaciones visuales, `docs/39` el easing de las claves de
  animación (modelo v13), `docs/40` el easing de las envolventes de volumen (modelo v14),
  `docs/41` el copiar/pegar de claves, `docs/42` el modo para añadir claves desde el timeline,
  `docs/43` el recorte interactivo en el preview, `docs/44` el export personalizado (tamaño, fps,
  bitrate, códec y presets sociales), `docs/45` el texto multilínea con ancho máximo (modelo v15),
  `docs/46` los fundidos de audio con crossfade por corte (modelo v16 sin cambios), `docs/47` el
  fondo del lienzo con blur o imagen (modelo v16), `docs/48` los formatos GIF/WebM (VP9/Opus y
  paleta GIF sin audio), `docs/49` la webcam junto a la pantalla con PiP automático (modelo v16
  sin cambios) y `docs/50` las pistas de micrófono separadas (modelo v16 sin cambios).
  **`docs/15-post-v1.md` es el backlog vivo**: qué falta, por bloques, con estado y
  criterio de aceptación. `CHANGELOG.md` resume cada versión.
- `tools/spikes/` son prototipos de F0 ya cerrados; no los toques para trabajo nuevo.
  `build/`, `dist/`, `.venv`, `__pycache__` y `tools/spikes/out/` están ignorados: no son fuente.

<!-- aurora:inicio (generado por `aurora agents`; se actualiza al volver a ejecutarlo) -->
## Aurora (interfaz de escritorio)

Este proyecto usa Aurora 0.10.0 (`aurora-ui`), una librería de UI de escritorio
declarativa y reactiva en Python. Es reciente y no está en tus datos de entrenamiento: no
supongas APIs de otras librerías (React, Flet, Tkinter, Qt, CSS). Antes de escribir código,
lee la documentación de la versión instalada:

- `aurora docs`: chuleta con el modelo mental, las firmas, las props de estilo, los tokens
  y los errores frecuentes. Léela entera antes de empezar.
- `aurora docs <tema>`: una sección de la referencia (`aurora docs button`,
  `aurora docs pilot`, `aurora docs style`).
- `aurora docs --search <texto>`: busca en la guía, las recetas y la referencia.
- `aurora docs --full`: toda la documentación en Markdown.

### Comprobar el trabajo

Sin abrir ventanas (`app.run()` bloquea; no lo llames en tests):

- `App(view, size=(900, 600)).render_png("captura.png")` dibuja la vista igual que la
  ventana: mira la captura para revisar el diseño.
- `pilot = App(view).pilot()` simula al usuario por `key`: `pilot.click("guardar")`,
  `pilot.type("hola")`, `pilot.press("enter")`, `pilot.settle()`, `pilot.a11y_tree()`.
  Escribe tests con pytest así (`aurora docs pilot`).
- Una prop o un token mal escritos lanzan `TypeError` o `ThemeTokenError` al construir la
  vista, con la sugerencia correcta: léela en vez de adivinar.

Desde la terminal:

- `aurora check app.py`: construye y pinta un frame; si falla, dice el archivo y la línea de tu código. Lista los avisos de layout (texto que no cabe, controles fuera de la ventana o sin nombre); `--strict` los hace fallar
- `aurora shot app.py -o captura.png`: renderiza la app a PNG sin ventana (`--key K` recorta a un nodo, `--scale 2`, `--theme light`): mírala para revisar el diseño
- `aurora tree app.py`: el árbol pintado en texto (rol, texto, `#key`, caja y estados) y los avisos; úsalo para encontrar las keys
- `aurora pilot app.py "click guardar; type hola; press enter; expect text hola; shot s.png"`: interacción guionizada; se para en el paso que falla y sugiere keys parecidas (`aurora pilot --help` tiene la gramática)
<!-- aurora:fin -->
