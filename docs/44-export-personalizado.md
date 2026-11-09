# 44 — Export personalizado

> Implementado el **2026-11-09**. Extiende el modal de export de [docs/13](13-f4-export.md) con
> tamaño, fps, bitrate y códec elegidos a mano, más accesos rápidos a tamaños sociales. No cambia
> el modelo del proyecto (`SCHEMA_VERSION` sigue en **15**): la configuración vive en `Settings` y
> en los `Signal` de la UI. Los cuatro presets históricos conservan sus cadenas y goldens byte a
> byte cuando no se usa el modo personalizado.

## Qué se implementó

| Pieza | Contenido |
|---|---|
| `core/graph.py` | `ExportPreset` gana `width`, `height`, `fps` y `video_bitrate` opcionales. `custom_error`/`custom_preset` validan y construyen el preset; `output_size` usa el tamaño explícito (par y acotado) y `export_fps` decide la cadencia efectiva. `build` reescala el proyecto a `fps` antes de calcular frames/transiciones y añade `-b:v`/`-maxrate`/`-bufsize` cuando hay bitrate. |
| `core/graph.py` | `SOCIAL_PRESETS`: 9:16 (1080×1920), 1:1 (1080×1080), 4:5 (1080×1350) y 16:9 (1920×1080), en ese orden. |
| `services/export.py` | `Exporter.start` acepta un `ExportPreset` ya construido además del id histórico; los materiales horneados (elementos/textos animados) se generan al fps de salida y la tolerancia se calcula con `GraphSpec.fps`. |
| `services/settings.py` | `export_custom_width/height/fps/video_bitrate/audio_bitrate/codec`, validados en `normalized()` y persistidos en `settings.json`. |
| `ui/export_modal.py` | Tarjeta **Personalizado** con ancho, alto, fps, bitrate de video, bitrate de audio y códec (GPU automática o x264); accesos sociales; error inline y botón **Exportar** deshabilitado con el motivo. El resumen muestra tamaño, fps, duración, bitrate de video y audio efectivos. |
| `ui/editor_controller.py` | Construye el preset personalizado al exportar, recuerda la configuración en `Settings` y la recupera en `boot()`. |

## Modelo

`ExportPreset` mantiene los cuatro presets de siempre:

```python
ExportPreset("rapido", ...)   # hw, CQ 21
ExportPreset("calidad", ...)  # x264, CRF 18
ExportPreset("ligero", ...)   # x264, CRF 24, max_height=720
ExportPreset("copia", ...)    # -c copy, sin filter graph
```

Los campos nuevos son opcionales y solo los usa el preset `personalizado`:

- `width`/`height`: tamaño exacto de salida. Deben venir juntos, ser enteros pares y estar entre
  **16 y 7680 px**. `output_size` los acota y los redondea a par; sin ellos se conserva el camino
  histórico (`project.width/height` y `max_height` del preset Ligero).
- `fps`: cadencia de salida entre **1 y 240**. `build` sustituye temporalmente
  `project.fps` por el objetivo **antes** de calcular `timeline_frames`, transiciones y
  `GraphSpec`; así cada clip se normaliza con `fps=<objetivo>` (la regla de oro 1: todo igual
  antes de `concat`/`xfade`) y los offsets/trim quedan en la cadencia final. `GraphSpec.frames` y
  `GraphSpec.duration` son los de salida, de modo que el progreso, la ETA y la tolerancia de
  verificación (`2 / fps`) siguen siendo coherentes.
- `video_bitrate`: texto tipo `6M`, `6000k` o `6000` (sin sufijo = kbps). En vez de CRF/CQ se
  emiten `-b:v <rate> -maxrate <1.5×> -bufsize <2×>` para todos los encoders
  (NVENC/AMF/QSV/libx264). Sin bitrate, `video_codec_args` devuelve exactamente la cadena
  histórica.
- `audio_bitrate`: se normaliza a un argumento de FFmpeg (`192k`) y se usa en `-b:a`.

`custom_error` devuelve el primer problema en español y `custom_preset` lanza `ProjectError` con
ese mensaje. La UI y `Settings.normalized()` comparten el mismo criterio. El preset `copia` no
admite tamaño, fps ni bitrate: `build` y `Exporter.start` lo rechazan con un aviso explícito.

### Presets sociales

| Acceso | Tamaño | Uso |
|---|---|---|
| **9:16** | 1080×1920 | Reels, Shorts, TikTok |
| **1:1** | 1080×1080 | Feed cuadrado |
| **4:5** | 1080×1350 | Feed vertical de Instagram |
| **16:9** | 1920×1080 | Horizontal/YouTube |

Cada botón rellena ancho y alto y selecciona **Personalizado**; no crea presets nuevos ni altera
el orden de los cuatro históricos. El usuario puede ajustar después fps y bitrates.

## UI

La tarjeta **Personalizado** va **después** de los cuatro presets históricos, que no cambian de
orden ni de comportamiento. Contiene:

- campos de ancho, alto, fps, bitrate de video, bitrate de audio y códec;
- accesos sociales;
- validación inline (`export-custom-error`) con el mismo texto que usa el controlador;
- resumen (`export-summary`) con `ancho×alto · fps · duración · H.264 · bitrate de video · AAC
  bitrate de audio` (o `sin audio`).

Si la configuración no es válida, **Exportar** queda atenuado, sin `on_click`, y el motivo se
muestra en la propia tarjeta. `start_export` vuelve a validar antes de lanzar nada. El modal tiene
altura máxima `min(state.view_h(), 720)` y el cuerpo hace scroll: en ventanas pequeñas no se
salen los botones de acción.

Las keys existentes (`preset-*`, `export-*`) se conservan. Las nuevas son `preset-personalizado`
(la tarjeta), `export-custom-head` (cabecera clicable), `export-width`, `export-height`,
`export-fps`, `export-vbitrate`, `export-abitrate`, `export-codec`, `export-custom-error`,
`export-social-*` y `export-scroll`.

## Persistencia

`Settings` guarda la última configuración personalizada:

```
export_custom_width, export_custom_height, export_custom_fps,
export_custom_video_bitrate, export_custom_audio_bitrate, export_custom_codec
```

`normalized()` acota dimensiones/fps, exige bitrates con formato y rango válidos y deja
`export_custom_codec` en `auto` si no es `auto`/`x264`. El editor vuelca los `Signal` en
`start_export` y al cerrar (`_remember_export`) y los recupera en `boot()`, así que al reabrir la
app se mantienen el preset y los valores. **No se toca `Project`**: cambiar la resolución de
salida no modifica el lienzo del proyecto.

## Límites

- El fps personalizado **no** cambia el preview: este sigue a `Project.fps`; el cambio solo afecta
  al archivo exportado. Los fps intermedios (p. ej. 24 sobre un proyecto de 30) se obtienen con el
  filtro `fps`, no con interpolación de movimiento.
- El bitrate es una media con `maxrate`/`bufsize`; no se promete CBR exacto. Para calidad
  constante siguen existiendo Rápido/Calidad.
- `copia` sigue exigiendo las condiciones del corte rápido y no acepta el modo personalizado.
- El tamaño personalizado no reencuadra elementos ni textos: usa la misma caja normalizada que el
  resto del export (letterbox si cambia la relación de aspecto).
- No hay campo de códec H.265/AV1 ni de contenedor: el objetivo es H.264 (GPU o x264) en MP4.

## Tests

`tests/test_export_custom.py` (10 tests):

- `output_size` con tamaño explícito, par, clamp y fallback histórico;
- formatos de bitrate (`6M`, `6000k`, `6000`, `1,5M`) y rangos;
- validación de pares, fps, bitrates y códec;
- `graph` con 320×180, 24 fps y 800k: frames/duración, `fps=24` en las cadenas, `-b:v` y
  ausencia de `-crf`;
- los cuatro presets históricos mantienen sus campos vacíos y su cadena/golden;
- `copia` rechaza opciones personalizadas en `build` y en `Exporter.start`;
- presets sociales y orden;
- **export real** de 1 s (fuente 320×240/30 fps) a 320×180/24 fps/400k, verificado con
  `probe_media` y conteo de frames con PyAV (24 frames, audio presente);
- modal con `pilot`: selección, presets sociales, error inline por impar/fps/bitrate, botón
  bloqueado, resumen efectivo y vuelta a un preset histórico;
- persistencia y normalización en `Settings`, recuperación en `boot()` y volcado al cerrar.

Suite completa: **555 tests** en verde (545 previos + 10 nuevos). Los goldens de
`tests/test_f4_graph.py` no cambian.
