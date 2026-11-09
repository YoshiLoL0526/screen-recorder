# 50 — Pistas de micrófono separadas (C5)

> Implementado el **2026-11-09**. Cierra **C5** del backlog ([docs/15](15-post-v1.md)): al grabar
> con micrófono, el MP4 conserva el audio del sistema y el micrófono queda como **WAV hermano**
> que el editor importa solo como pista editable. El **esquema del proyecto no cambia** (sigue en
> **16**): es una feature de captura y de importación.

## Qué se puede hacer

- Elegir en **Grabar → Audio → Modo** entre **«Mezclar en el MP4»** y **«Pistas separadas
  (micrófono editable)»**. La elección se persiste en `Settings`.
- Grabar con sistema + micrófono en modo separado. Al parar quedan:
  - `<nombre>.mp4` — vídeo + **solo** el audio del sistema (una pista AAC);
  - `<nombre>-microfono.wav` — micrófono PCM 16 bits, tal como lo escribe el grabador;
  - `<nombre>.json` — sidecar con el bloque `mic`.
- Abrir la grabación con **✂ Editar**: el editor añade una pista **absoluta** llamada
  **«Micrófono»** con el WAV colocado en su `offset_s`. El audio del sistema sigue siendo el audio
  propio del vídeo (separado a «Audio del video» como cualquier importación) y **no se duplica**.
- En la **Biblioteca**: chip **🎤 micrófono**, botón para reproducir el WAV, borrado conjunto con
  la grabación y `reveal` de la carpeta.
- La **recuperación** de una sesión interrumpida conserva el micrófono y rellena el sidecar.

## Modos

| `AudioConfig.mode` | MP4 | WAV hermano | Sidecar `mic` |
|---|---|---|---|
| `mix` (default) | sistema + micrófono mezclados (`amix`) | no | `null` |
| `separate` con micrófono | **solo** el sistema | sí | bloque con metadatos |
| `separate` sin micrófono | sistema (equivale a `mix`) | no | `null` |
| `separate` sin sistema | **sin audio** | sí | bloque con metadatos |

Decisiones:

- **No se meten dos streams AAC en el MP4**: se mantiene el patrón de archivo hermano que ya
  describía [docs/03](03-grabacion.md) y que usa la webcam ([docs/49](49-webcam-pip.md)).
- Sin micrófono seleccionado no hay nada que separar: el modo se comporta como mezclar y la UI lo
  explica.
- Sin audio del sistema, el MP4 puede quedar mudo; el micrófono se conserva igual y el editor lo
  importa. Es preferible a inventar una pista vacía.

## Configuración

`core/config.py`:

```python
AudioConfig(
    system=True,
    mic_device_index=None,   # -1 / None = sin micrófono
    keepalive=True,
    mode="mix",              # mix | separate
)

# validate(): mode ∈ {"mix", "separate"} e índice de micrófono ≥ 0 si existe.
```

`RecordingConfig.validate()` valida también el audio. En la app:

- `state.audio_mode` (`Signal("mix")`) alimenta el selector de la pantalla de Grabar.
- `Settings.audio_mode` persiste la elección y `normalized()` la devuelve a `"mix"` si es
  desconocida.
- `Controller._build_config()` construye `AudioConfig(mode=state.audio_mode())`.

## Mux y archivos

`services/recording.py._mux` ordena las pistas con el **sistema primero** y el micrófono después
(los `AudioRecorder` se crean en ese orden). En modo separado:

1. `mux_cmd(..., mix=False)` recibe solo la primera pista; el MP4 lleva **una** pista AAC de
   sistema.
2. Antes de borrar el temporal, `audio_mic.wav` se copia a
   `_unique_path(<nombre>-microfono.wav)` con `shutil.copy2`.
3. El sidecar guarda el bloque `mic` (en `mix` es `null`).

`platform/ffmpeg.mux_cmd` ya soportaba `mix=False`; ahora **filtra las pistas posteriores a la
primera** antes de abrir entradas, para no dejar cadenas de filtro sin conectar. Con `mix=True` y
los cuatro presets MP4 el comando no cambia.

Ejemplo real (2026-11-09, `tools/rec.py`, 3 s, Realtek + loopback):

```json
"mic": {
  "file": "2026-11-09_18-07-26-microfono.wav",
  "device": "Microphone (Realtek(R) Audio)",
  "offset_s": -0.1728,
  "duration_s": 3.477,
  "drift_ppm": 0,
  "rate": 48000
}
```

- `file` es **solo el nombre**: la Biblioteca lo resuelve junto al MP4 y nunca acepta rutas fuera
  de la carpeta.
- `offset_s = t0_mic − t0_video`. Negativo (lo habitual) significa que el micrófono arrancó antes
  que el primer frame de pantalla.
- `duration_s` y `rate` son los del WAV; `drift_ppm` es la deriva medida por el grabador (0 si la
  grabación dura menos de 60 s, cuando no hay muestras suficientes).

## Recuperación

`session.json` incluye un bloque `mic` con `enabled`, `mode`, `device`, `offset_s` y `file`
(`audio_mic.wav`). Al recuperar una sesión:

1. se reconstruye `AudioConfig` desde `config.audio` (incluido `mode`);
2. se conservan los offsets guardados por nombre de WAV;
3. `_mux` copia el micrófono antes de `shutil.rmtree` y escribe el bloque `mic` con el nombre
   real del dispositivo;
4. el MP4 recuperado se llama `<nombre>-recuperada.mp4` y el WAV
   `<nombre>-recuperada-microfono.wav`.

## Biblioteca

`services/library.py` añade:

- `MicInfo(path, device, offset_s, duration_s, drift_ppm, rate)`;
- `mic_info(mp4)` (solo si el WAV existe y **no está vacío**), `mic_declared(mp4)` (el sidecar lo
  declara, exista o no) y `_sidecar_mic`;
- `Recording.mic`, `mic_offset_s`, `mic_duration_s`, `mic_drift_ppm`, `mic_rate` y `has_mic`.

`delete_recording_files` manda también el WAV a la papelera. La fila de la Biblioteca muestra el
chip y el botón de reproducción solo si el archivo existe.

## Importación automática en el editor

`EditorController._add_mic_tracks()` se ejecuta al crear un proyecto o al importar al final del
timeline, después de separar el audio del vídeo y de añadir el PiP de cámara. Por cada grabación:

1. `library.mic_info()` valida el sidecar y el WAV;
2. `media.probe()` obtiene la duración del PCM;
3. `tl.add_audio_track(project, "Micrófono", mode="absolute")`;
4. `tl.insert_audio_clip()` coloca el clip en `clip_start + offset_s` (redondeado a frame);
   - si `offset_s < 0`, se inserta con ese margen y se recorta el arranque con `tl.trim_audio`
     + `tl.move_audio`, de modo que el audio que suena al inicio coincida con el primer frame;
5. se encola la waveform para dibujarla en el timeline.

Si el WAV falta o está vacío se avisa con un toast y **no** se crea la pista; la importación del
vídeo sigue. Si el `offset_s` es positivo, la pista empieza más tarde; si es negativo, se recorta
el trozo anterior al vídeo. El audio del sistema sigue en «Audio del video» (enlazado) y el
micrófono suena una sola vez.

No hay composición nueva: el preview y el export ya mezclan pistas absolutas con `adelay` +
`amix` ([docs/19](19-fase-0-1-audio.md) y [docs/32](32-audio-envelopes.md)). El proyecto guarda el
asset y la pista como cualquier otro audio.

## UI de Grabación

En la sección **Audio**:

- **Modo**: `Mezclar en el MP4` / `Pistas separadas (micrófono editable)` (`sel-audio-mode`).
- Sin micrófono se muestra el aviso «Sin micrófono, «Pistas separadas» se comporta como mezclar:
  el MP4 llevará solo el audio del sistema.»
- Con micrófono y modo separado se explica el archivo que se generará.
- Las etiquetas accesibles siguen viniendo de `field()`/`label_control` (`label` en el control).

## Pruebas

`tests/test_mic_tracks.py` (15):

- validación de `AudioConfig.mode` y `Settings.audio_mode`;
- `mux_cmd(mix=False)` abre solo la primera pista;
- mux real: WAV hermano + sidecar en separado, MP4 de una sola pista AAC, `mix` sin hermano,
  separado sin sistema (MP4 mudo) y separado sin micrófono (igual que mix);
- recuperación que conserva el WAV y el dispositivo;
- Biblioteca (`mic_info`, `mic_declared`, campos de `Recording`, borrado) y archivos ausentes o
  vacíos;
- importación automática en `offset_s` positivo y negativo, y ausencia de pista con aviso;
- export real y `build_audio` (preview) con sistema + micrófono;
- UI con `pilot` (selector de modo y aviso sin micrófono).

Verificación:

- `uv run pytest -q`: **655 passed** (640 base + 15).
- `uv run aurora check main.py`: sin errores.
- `uv run aurora tree main.py`: sin avisos de layout.
- `git diff --check`: limpio.

### Prueba manual real

Con el micrófono Realtek y el loopback (2026-11-09):

```bash
uv run python tools/rec.py --list
uv run python tools/rec.py --duration 3 --fps 30 --preset baja --audio both --mic 11 \
  --audio-mode separate -o salida/
```

Resultado medido: MP4 de 3,03 s con **una** pista AAC de sistema; WAV de 3,48 s / 48 kHz /
estéreo / 16 bits; `offset_s = -0,1728`; sidecar con el bloque `mic`. Al abrir el MP4 en el
editor se crearon «Audio del video» (3,03 s) y «Micrófono» (inicio 0, recorte de 0,167 s), sin
duplicar el sonido. También se probó el caso sin keep-alive: el loopback no entregó muestras, el
MP4 quedó mudo y el WAV del micrófono se conservó.

## Límites

- **Windows/WASAPI**: el micrófono depende de `PyAudioWPatch`; en otros sistemas no hay captura.
- El WAV se guarda **tal como lo escribe el grabador**: tasa del dispositivo (44,1 o 48 kHz),
  hasta 2 canales y 16 bits. No se remuestrea ni normaliza al copiarlo.
- El MP4 separado lleva solo el sistema. No hay dos pistas AAC ni selección de pista dentro del
  MP4: para editar el micrófono se usa el WAV.
- `separate` sin micrófono equivale a `mix`; sin sistema el MP4 puede quedar sin audio.
- La pista «Micrófono» se crea al **importar/crear** el proyecto. Si se importa dos veces la misma
  grabación en un proyecto existente se añade otra pista (el modelo no impone unicidad de
  nombres); el usuario puede borrarla.
- Si el WAV se mueve o se borra, la Biblioteca deja de ofrecerlo y el editor avisa al importar,
  pero un proyecto que ya lo usaba queda con el asset offline como cualquier otro medio.
- La deriva del micrófono se mide con la misma regresión que el resto de pistas (necesita ≥ 60 s
  para ser distinta de 0). El recorte del offset no corrige la deriva en el timeline: el editor
  coloca el WAV con su `offset_s`; para regrabar voz, la pista ya es editable.
- El esquema del proyecto **no cambia**: el micrófono es un `MediaAsset` de audio y una `Track`
  absoluta normales.

## Archivos tocados

- `src/recstudio/core/config.py` (`AUDIO_MODES`, `AudioConfig.mode/validate/separate_mic`).
- `src/recstudio/platform/ffmpeg.py` (`mux_cmd` con `mix=False` y solo la primera pista).
- `src/recstudio/services/recording.py` (`RecordingResult.mic`, `_finalize_mic`, sidecar,
  `session.json` y recuperación).
- `src/recstudio/services/library.py` (`MicInfo`, `mic_info`, `mic_declared`, campos y borrado).
- `src/recstudio/services/settings.py`, `src/recstudio/state.py`.
- `src/recstudio/ui/controller.py`, `ui/editor_controller.py` (`_add_mic_tracks`),
  `ui/screens/record.py` (selector **Modo**), `ui/screens/library.py`, `ui/icons.py`.
- `tools/rec.py` (`--audio-mode`).
- `tests/test_mic_tracks.py` (nuevo).
- Docs: este documento, `docs/03`, `docs/15`, `docs/16`, `docs/19`, `docs/README.md`,
  `CHANGELOG.md` y `AGENTS.md`.
