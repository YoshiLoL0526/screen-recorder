# 48 — GIF y WebM como formatos de export

> Implementado el **2026-11-09**. Extiende el modal de export de [docs/13](13-f4-export.md) y el
> export personalizado de [docs/44](44-export-personalizado.md) con **WebM (VP9/Opus)** y **GIF
> (paleta, sin audio)** además del MP4 histórico. No cambia el modelo del proyecto
> (`SCHEMA_VERSION` sigue en **15**): el formato vive en `ExportPreset`, en la UI y en `Settings`.
> Los cuatro presets MP4 conservan sus cadenas y goldens **byte a byte**.

## Qué se implementó

| Pieza | Contenido |
|---|---|
| `core/graph.py` | `ExportPreset.format` (`mp4`/`webm`/`gif`, por defecto `mp4`), `format_error`/`check_format`, `with_format`, `format_extension`, `export_fps` (GIF a 20 fps como máximo) y `output_size` (GIF con lado mayor ≤ 720 px). `build` genera VP9/Opus o el graph de paleta de GIF sin tocar el camino MP4. |
| `platform/ffmpeg.py` | `probe_format_encoders()` prueba **codificando de verdad** `libvpx-vp9` y `libopus`, con cache propia (`format-encoders.json`). `probe_encoders()` sigue devolviendo solo H.264 para no contaminar la elección de encoder de captura. |
| `services/export.py` | La extensión del destino y del `.part` sale del formato; verificación y progreso siguen igual. WebM se valida con `libvpx-vp9`/`libopus` y da un error claro si faltan; el reintento con x264 solo existe en MP4. |
| `services/settings.py` | `export_format`, validado en `normalized()` (`mp4` si el valor no existe). |
| `state.py` | `export_format` (UI) y el preset personalizado lo recibe al construirse. |
| `ui/export_modal.py` | Selector **Formato** (MP4/WebM/GIF), extensión visible junto al nombre, resumen con códecs/calidad/audio, campos de audio ocultos en GIF, VP9 + Opus en WebM y «Sin recodificar» deshabilitado fuera de MP4. |
| `ui/editor_controller.py` | Aplica el formato al preset, calcula la extensión de salida, persiste la elección y la recupera al arrancar. |

## Formatos y calidad

### MP4 (histórico)

H.264 (NVENC/AMF/QSV/libx264) + AAC 48 kHz, `-movflags +faststart`. Sin `format` explícito, el
preset y el comando son exactamente los de antes: los golden de `tests/test_f4_graph.py` no
cambian.

### WebM

- **Video VP9** (`libvpx-vp9`). Con calidad constante: `-crf <quality> -b:v 0` (se reutiliza el
  CRF del preset: 18 en Calidad, 24 en Ligero, 21 en Rápido/personalizado). Con
  `video_bitrate`: `-b:v`, `-maxrate` (1,5×) y `-bufsize` (2×), sin `-crf`.
- Ajustes de velocidad: `-deadline good -cpu-used 2 -row-mt 1`. VP9 no usa los encoders H.264
  por hardware: es codificación por CPU y tarda más que NVENC en un MP4 equivalente.
- **Audio Opus** (`libopus`, 48 kHz) al bitrate del preset (`160k`–`256k`) o al del export
  personalizado. Sin audio en el proyecto se emite `-an`. No hay AAC ni `faststart` (no aplica
  al contenedor Matroska/WebM).

### GIF

- Una pasada con `split=2`: una rama alimenta `palettegen` y la otra se colorea con
  `paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle`.
- **Sin audio**: se fuerza `-an` y el resumen/UI no ofrecen pista de sonido.
- **`-loop 0`**: bucle infinito (el GIF se repite solo).
- **Cadencia**: `min(fps del proyecto/preset, 20)`. A 50/60 fps el peso y el tiempo de paleta se
  disparan sin mejora visible en una grabación de pantalla.
- **Tamaño**: el lado mayor se acota a **720 px** (el otro se escala en proporción y se
  redondea a par). Un GIF de 1080p cuesta varias veces más bytes y memoria para la paleta.
- Se conserva la **duración** del timeline o del rango In/Out; los frames se calculan a la
  cadencia GIF, así que el progreso/ETA y la verificación usan la duración real del archivo.

### Combinaciones válidas

| Formato | Presets | Códec de video | Audio | `copia` |
|---|---|---|---|---|
| MP4 | los cuatro + personalizado | H.264 (GPU/x264) | AAC | sí |
| WebM | los cuatro recodificando + personalizado | VP9 (`libvpx-vp9`) | Opus | no |
| GIF | los cuatro recodificando + personalizado | GIF con paleta | ninguno | no |

`format_error`/`check_format` rechazan un formato desconocido y `copia` fuera de MP4. `build`
también rechaza un encoder H.264 en WebM/GIF y `video_codec_args` explica el codec correcto. La
UI evita la combinación antes de llegar al servicio: elegir un formato distinto de MP4 cambia
«Sin recodificar» a Rápido, y elegir «Sin recodificar» vuelve a MP4.

## UI

El modal (`ui/export_modal.py`) añade, debajo del nombre:

- una fila **Formato** con tres botones (`export-format-mp4`, `export-format-webm`,
  `export-format-gif`; contenedor `export-format`);
- la extensión visible (`export-ext`): `.mp4`, `.webm` o `.gif`;
- el resumen (`export-summary`) con el códec y el audio efectivos:
  - `H.264 + AAC 192k` en MP4;
  - `VP9 + Opus 256k`, o `VP9 · sin audio` en WebM;
  - `GIF · sin audio` en GIF.

La tarjeta **Personalizado** se adapta al formato:

- **MP4**: ancho, alto, fps, bitrate de video, bitrate de audio y códec H.264 (GPU/x264).
- **WebM**: los mismos campos, pero el códec se muestra como **VP9 + Opus** (sin selector).
- **GIF**: ancho, alto y fps; se ocultan bitrates y códec y se explica el límite de 720 px/20 fps
  (`export-gif-note`).

Las keys existentes (`preset-*`, `export-*`) se conservan. Las nuevas son `export-format`,
`export-format-mp4`, `export-format-webm`, `export-format-gif`, `export-ext` y
`export-gif-note`.

## Servicio y verificación

- `unique_output(folder, name, extension)` usa la extensión del formato y mantiene la regla de
  no pisar (` (2)`, ` (3)`…). `partial_path` produce `nombre.part.webm` / `nombre.part.gif`, y
  `os.replace` renombra al terminar.
- El progreso (`-progress pipe:1`), ETA, cancelación y borrado del parcial son los mismos: el
  graph de GIF/WebM también recibe `-progress` y su duración sale de `GraphSpec`.
- `probe_media` lee los tres contenedores con `ffmpeg -i`: VP9/Opus, GIF/pal8 y MP4. La
  verificación exige video, audio si el proyecto lo tiene (salvo GIF) y duración ±0,2 s.
- Antes de renderizar WebM se comprueba `libvpx-vp9`; si hay audio, también `libopus`. Si faltan,
  el export termina con un mensaje explicable, no con el error crudo de FFmpeg. El fallback a
  x264 solo se aplica a MP4: WebM/GIF nunca reintentan con H.264.
- «Mostrar en carpeta» y «Reproducir» usan `progress.out_path`, así que abren el `.webm`/`.gif`
  correcto con la aplicación asociada de Windows.

## Persistencia

`Settings` guarda `export_format` (`"mp4"`, `"webm"` o `"gif"`). `normalized()` lo deja en `mp4`
si el valor es desconocido; el editor lo vuelca en `start_export`/`_remember_export` y lo
recupera en `boot()`. **No se toca `Project`**: el formato es una decisión de salida y no altera
el lienzo, los clips ni el schema.

## Límites

- GIF no lleva audio y su paleta es de 256 colores: los degradados y el texto pequeño pueden
  verse con bandas. No hay transparencia ni bucle configurable (siempre `-loop 0`).
- El GIF se acota a 720 px de lado mayor y 20 fps. Para máxima calidad sigue siendo mejor MP4 o
  WebM; GIF es para compartir un momento corto.
- WebM usa VP9 por CPU (no hay encoder por hardware en el pipeline actual) y puede tardar más
  que el MP4 con NVENC. Opus no se usa en MP4.
- «Sin recodificar» (corte rápido) sigue siendo **solo MP4**. Tampoco hay AV1, H.265 ni
  selección de contenedor fuera de los tres formatos.
- El export personalizado mantiene tamaño/fps/bitrate, pero en GIF el tamaño se acota y el
  bitrate no aplica; en WebM el códec es siempre VP9 + Opus.
- El preview no cambia: el formato solo afecta al archivo exportado.

## Tests

`tests/test_export_formats.py` (14 tests):

- MP4 por defecto: presets con `format="mp4"`, filtro sin paleta, AAC y `+faststart` (golden de
  F4 intacto);
- WebM: VP9/Opus, CRF o bitrate, `-an` sin audio, mismo filter graph que MP4 y resumen VP9/Opus;
- GIF: `split` + `palettegen` + `paletteuse`, `-loop 0`, `-an`, 20 fps, 720 px y duración
  conservada; tamaño personalizado acotado;
- validaciones: formato desconocido, `copia` fuera de MP4 en `build` y `Exporter.start`, encoder
  H.264 en GIF/WebM, VP9 ausente;
- extensiones de `unique_output`/`partial_path`;
- **exports reales** de 2 s: WebM VP9+Opus (30 fps, 60 frames, audio Opus 48 kHz) y GIF sin audio
  (20 fps, 40 frames), verificados con `probe_media` y PyAV; error claro si falta Opus;
- UI con `pilot`: cambio de formato, extensión visible, resumen, campos de audio ocultos en GIF,
  «Sin recodificar» deshabilitado fuera de MP4 y auto-vuelta a MP4;
- controlador: el preset y la ruta de salida usan `.gif`;
- GIF ignora bitrates ocultos del personalizado (no bloquean el export);
- `Settings`: persistencia y normalización de `export_format`.

Suite completa: **593 tests** en verde (579 base + 14 nuevos). Comprobado además con
`uv run aurora check main.py`, `uv run aurora tree main.py` (sin avisos) y `git diff --check`.

## Verificación manual

```bash
uv run pytest tests/test_export_formats.py -q
uv run pytest -q
uv run aurora check main.py
uv run aurora tree main.py
```

Los exports reales del test generan un WebM reproducible (`vp9`+`opus`) y un GIF de 2 s
(`gif`, sin audio, 20 fps). En una máquina sin `libvpx-vp9`/`libopus`, el modal sigue mostrando
los formatos pero el export termina con el aviso de encoder no disponible.
