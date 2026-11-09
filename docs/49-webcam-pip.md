# 49 — Webcam junto a la pantalla y PiP automático

> Implementado el **2026-11-09**. Cierra **C1** del backlog ([docs/15](15-post-v1.md)): grabar la
> pantalla y la cara a la vez, guardar la cámara como archivo aparte y montarla como **PiP** en el
> editor sin lógica de composición nueva. Modelo del proyecto **sin cambios** (sigue en 16).

## Qué se puede hacer

- Activar la **Cámara** en la pantalla de Grabar, elegir dispositivo, resolución y fps, y ver un
  **snapshot periódico** en el panel (cada 3 s; no compite con la grabación).
- Grabar pantalla + cámara. Los dos vídeos se escriben por separado y al parar quedan:
  - `<nombre>.mp4` — grabación principal;
  - `<nombre>-camara.mp4` — cámara, solo vídeo, reproducible por FFmpeg/PyAV.
- Abrir la grabación en el editor con **✂ Editar**: se crea sola la capa **Vídeo · <nombre>-camara**
  como PiP abajo a la derecha, a ~28 % del lienzo, **sin audio** y en la capa «Elementos».
- El PiP es un `Element.kind="video"` normal: se puede mover, escalar, rotar, animar, cambiar de
  capa o **borrar**. El archivo de cámara sigue en la Biblioteca.
- En la Biblioteca la grabación muestra el chip **🎥 cámara** y un botón para reproducir el archivo
  de cámara; borrar la grabación manda a la papelera también la cámara.

## Flujo

```
Grabar:
  pantalla ── WGC/DXGI ── rawvideo ── FFmpeg ── temp/video.mkv ─┐
  cámara   ── DirectShow ─────────── FFmpeg ── temp/camera.mkv ─┤
                                                                 ├─ stop → mux de pantalla
                                                                 └─ remux de cámara
  ~/Videos/RecStudio/<nombre>.mp4
  ~/Videos/RecStudio/<nombre>-camara.mp4
  ~/Videos/RecStudio/<nombre>.json        (sidecar con camera.file y offset_s)

Editor:
  sidecar → services.library.camera_info() → media.probe(cámara)
          → core.elements.add_camera_pip(project, asset, clip_start, offset_s)
          → Element(kind="video", x≈0.82, y≈0.78, width≈0.28, audio_gain=0)
```

## Configuración

`core/config.py` añade `CameraConfig` (frozen, comparable como exige `Signal`):

```python
CameraConfig(
    enabled=False,          # el switch de la pantalla de Grabar
    device_name=None,       # nombre DirectShow de list_cameras()
    width=1280, height=720, # pares, 160…4096
    fps=30,                 # 5…120
)
RecordingConfig.camera = CameraConfig()
```

`validate()` solo exige dispositivo/rangos cuando `enabled=True`: así una configuración antigua
sin cámara sigue siendo válida. `Settings` persiste `camera_enabled`, `camera_device`,
`camera_width`, `camera_height` y `camera_fps`; `normalized()` acota los valores (dimensiones
pares) y apaga la cámara si no hay dispositivo elegido. Nada de esto entra en `Project`.

## Captura (`platform/camera.py`)

Se usa **FFmpeg CLI con DirectShow**. El build de release excluye `cv2` (docs/16), así que no hay
OpenCV; PyAV tampoco hace falta porque solo se lanza un encoder a un MKV temporal, igual que la
captura de pantalla.

- `list_cameras()` ejecuta `ffmpeg -list_devices true -f dshow -i dummy` y `parse_camera_list()`
  extrae los `"Nombre" (video)` y su `Alternative name` (id PnP). Es tolerante: si FFmpeg cambia
  el formato o no hay dispositivos, devuelve una lista vacía y la UI avisa.
- `CameraRecorder` lanza:

  ```bash
  ffmpeg -f dshow -framerate 30 -video_size 1280x720 -i video="XWF-1080P" \
         -an -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p \
         -flush_packets 1 -f matroska temp/camera.mkv
  ```

  Si el dispositivo no acepta ese tamaño/fps, **reintenta sin `-video_size`/`-framerate`** (modo
  nativo). `start()` espera a que el MKV tenga datos (2,5 s por intento) y lanza un vigilante que,
  si la cámara desaparece, llama a `on_failure`; el motor lo convierte en un aviso y **la
  grabación de pantalla sigue**.
- `stop()` pide a FFmpeg un cierre limpio con `q` por stdin (el MKV queda indexado), espera y, si
  hace falta, mata el proceso. El archivo se conserva aunque la cámara se haya cortado.
- La vista previa usa `capture_snapshot()`: un proceso corto `-frames:v 1` a MJPEG por `pipe:1`,
  en el hilo `CameraPreviewLoop` (nunca en el de UI) y solo en reposo.

### Pausa con cámara: deshabilitada

La cámara es un proceso aparte. Pausarla exigiría cerrar y concatenar segmentos y podría dejar la
cara desalineada. Se elige la opción explícita: **con la cámara activada no se puede pausar**.
`RecordingSession.pause()` devuelve `False`, emite un aviso y la UI lo muestra; la pantalla de
Grabar también lo avisa junto al switch. Sin cámara, la pausa funciona igual que siempre.

## Archivos y sidecar

El MP4 de cámara se crea al final, no durante la grabación:

1. `temp/camera.mkv` se remuxa con `-c:v copy -an -movflags +faststart`;
2. si el remux falla, se copia el MKV tal cual como `<nombre>-camara.mkv` (reproducible y sin
   pérdida) en vez de perder la cara;
3. se valida con `probe_media` y se escribe en el sidecar:

```json
"camera": {
  "file": "2026-11-09_17-35-40-camara.mp4",
  "device": "XWF-1080P",
  "width": 1280, "height": 720, "fps": 15.44,
  "duration_s": 4.53, "size_bytes": 1352121,
  "offset_s": 0.0068
}
```

`file` es **solo el nombre**: la Biblioteca lo resuelve junto al MP4 y nunca acepta rutas fuera de
la carpeta. `offset_s = t0_video − t0_cámara` se mide con el primer frame escrito en el pipe de la
pantalla y el arranque de la cámara. Sirve para alinear el PiP: si la cámara empezó antes, el
elemento avanza `in_`; si empezó después, el PiP empieza más tarde.

La grabación de pantalla conserva su sidecar/MP4 y el temporal se borra al terminar. El vídeo de
la cámara **no** se borra al limpiar temporales: se mueve antes de `shutil.rmtree`.

### Recuperación

`session.json` incluye el bloque `camera` (dispositivo, desfase, nombre del temporal y si falló).
`scan_recoverable()` sigue detectando la carpeta por `video.mkv`; al recuperar, `_mux` remuxa
también `camera.mkv`, escribe el sidecar con la cámara y solo después borra el temporal. El
resultado expone `RecordingResult.camera` y `camera_offset_s`.

## PiP automático en el editor

`EditorController.import_paths()` consulta el sidecar de cada grabación importada. Si hay cámara:

1. `media.probe()` obtiene sus metadatos y `tl.add_asset()` la registra;
2. `core.elements.add_camera_pip()` reutiliza `add_element("video", …)`:
   - caja de ~28 % con `default_image_box` (respeta el aspecto real de la cámara),
   - centro abajo a la derecha (`x≈0.82`, `y≈0.78`) sin salirse del lienzo,
   - `audio_gain=0` (la cámara se graba sin audio),
   - `in_`/inicio según `offset_s`;
3. si el probe o el alta fallan, se avisa y la importación principal sigue.

No hay composición nueva: preview y export ya soportan `Element.kind="video"` ([docs/28](28-pip-video.md)
y [docs/29](29-pistas-video.md)). Si el usuario borra el elemento, el archivo sigue en la Biblioteca
y se puede volver a añadir con el botón **PiP** del panel de medios.

## UI

- **Grabar → Cámara**: switch, selector de dispositivo, resolución, fps y snapshot. Si no hay
  cámaras se muestra «No hay cámaras. Conecta una y pulsa «Actualizar».»; el botón **Actualizar**
  vuelve a enumerar en un hilo.
- Badge **● CÁM** en el preview mientras la cámara graba; si falla, se ve el aviso y el badge
  desaparece.
- **Biblioteca**: chip **🎥 cámara**, botón de reproducción del archivo de cámara y borrado
  conjunto (MP4, miniatura, sidecar y cámara) a la papelera.

## Pruebas

- `tests/test_webcam.py` (25): configuración y `Settings`, parseo de la salida de DirectShow,
  comandos, arranque/parada con backend falso, cámara que falla sin tumbar la sesión, pausa
  deshabilitada, mux real con sidecar, recuperación con cámara, Biblioteca (lectura y borrado),
  PiP automático (posición, audio, desfases), **export real con frame compuesto**, y UI con
  `pilot` (activar, selector, aviso sin dispositivos, PiP al importar).
- `uv run pytest -q`: **640 passed**.
- `uv run aurora check main.py` y `uv run aurora tree main.py`: sin avisos.

### Prueba manual (hardware)

```bash
uv run python tools/rec.py --list-cameras
uv run python tools/rec.py --duration 4 --fps 30 --preset baja --audio none \
  --camera "XWF-1080P" --camera-size 1280x720 --camera-fps 30 -o salida/
```

Comprobar que aparecen `<nombre>.mp4`, `<nombre>-camara.mp4` y `<nombre>.json`; abrir el MP4 de
pantalla y el de cámara; pulsar **✂ Editar** en la Biblioteca y verificar que el PiP está abajo a
la derecha; exportar y comprobar que la cara se compone. En esta máquina (2026-11-09, XWF-1080P)
la prueba de 4 s dio pantalla 4,03 s / 1280×720 / 30 fps, cámara 4,53 s / 1280×720 / 15,44 fps
(el dispositivo entrega ~15 fps aunque se pidan 30), desfase +0,0068 s y PiP visible en el frame
exportado.

## Límites

- **Windows/DirectShow** únicamente; en otros sistemas `list_cameras()` devuelve vacío.
- Una cámara por grabación. No hay cambio de dispositivo a mitad de sesión: si se desconecta, se
  avisa, se conserva lo grabado y la pantalla sigue.
- La cámara se graba **sin audio**; su micrófono no se abre (C5, pistas de audio separadas, queda
  fuera de esta entrega).
- La sincronía es la del primer frame de pantalla + `offset_s`; la latencia interna del
  dispositivo puede añadir algunas decenas de ms. Para una cara en un PiP es aceptable y el
  usuario puede recortar la barra del elemento.
- El fps real lo decide la cámara (y la luz): pedir 30 no garantiza 30.
- El PiP automático se crea al **importar/crear** el proyecto; si ya existía un proyecto abierto,
  no se duplica.
- La pausa está deshabilitada con cámara (decisión documentada arriba).

## Archivos tocados

- `src/recstudio/core/config.py` (`CameraConfig`, `RecordingConfig.camera`).
- `src/recstudio/core/errors.py` (`CameraError`).
- `src/recstudio/core/elements.py` (`add_camera_pip`).
- `src/recstudio/platform/camera.py` (nuevo: enumeración, comandos, grabador y snapshot).
- `src/recstudio/platform/ffmpeg.py` (`run_bytes` para el JPEG del snapshot).
- `src/recstudio/services/recording.py` (arranque/parada, pausa, mux, sidecar y recuperación).
- `src/recstudio/services/library.py` (`CameraInfo`, `Recording.camera`, lectura y borrado).
- `src/recstudio/services/preview.py` (`CameraPreviewLoop`).
- `src/recstudio/services/settings.py`, `src/recstudio/state.py`.
- `src/recstudio/ui/controller.py`, `ui/editor_controller.py`, `ui/screens/record.py`,
  `ui/screens/library.py`.
- `tools/rec.py` (`--list-cameras`, `--camera`, `--camera-size`, `--camera-fps`).
- `tests/test_webcam.py` (nuevo).
- Docs: este documento, `docs/03`, `docs/15`, `docs/16`, `docs/28`, `docs/README.md`,
  `CHANGELOG.md` y `AGENTS.md`.
