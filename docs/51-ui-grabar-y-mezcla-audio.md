# 51 — Arreglos de UI en Grabar/Biblioteca y de la mezcla sistema + micrófono

> Implementado el **2026-11-24**. Tres bugs de uso real, sin features nuevas y **sin cambios de
> modelo** (el proyecto sigue en el esquema **16** y el sidecar tampoco cambia): dos de layout en
> la UI y uno de sincronía/nivel en el mux de grabación.

## 1. «Grabar»: controles cortados en el panel de configuración

### Causa

Tres anchos fijos encadenados:

- `ui/components.field()` reservaba **84 px** de etiqueta (más 8 de gap), así que el control solo
  podía medir **180 px** (panel de 310 − `padding` 16+16). Los `select` de Aurora **no truncan sus
  etiquetas**, y el del micrófono necesitaba ~230 px con el nombre real del dispositivo: el
  desplegable se salía por el borde derecho de la ventana. Igual el de «Modo» (~270 px con
  «Pistas separadas (micrófono editable)»).
- La fila de **Región** sumaba **301 px** de anchos fijos (4 campos de 54/58 px + etiquetas +
  gaps) en un hueco de **272**: el campo del alto terminaba fuera de la ventana (el «948» de la
  captura del usuario).
- La columna del preview tenía `min_width=440`: nav (196) + 440 + panel (310) = **946 px**, así
  que por debajo de ~950 de ancho de ventana la fila no cabía y **el panel entero** se recortaba
  (`aurora check --size 900x600` daba 9 avisos `offscreen`).

### Arreglo

- `field(..., stacked=True)`: etiqueta encima y control a todo el ancho, para los desplegables con
  nombres de dispositivo (`Micrófono` y `Dispositivo` de la cámara). El `label` accesible sigue
  puesto (`label_control`), así que `aurora check` no avisa `no-label`.
- La región son **dos filas de dos campos** (`column(gap=2, grow=1, width=0)` +
  `text_input(width="100%")`): X/Y arriba, Ancho/Alto abajo. Se reparten el ancho disponible.
- Etiqueta de modo acortada a **«Pistas separadas»** (la explicación ya está en la nota de debajo
  del desplegable).
- `PANEL_W`/`PANEL_PAD` como constantes de módulo (las usa el test de layout) y `min_width` del
  preview 440 → **300**.

## 2. «Biblioteca»: la lista no hacía scroll

`library_screen()` devolvía `column(grow=1, ...)` **sin `height=0`**. En flexbox la columna mide su
min-content (la lista entera), así que el `virtual_list` recibía un hueco tan alto como su
contenido: el `scroll` interno no tenía nada que desplazar y todo lo que pasaba del borde inferior
de la ventana era inalcanzable (con 8 grabaciones ya se notaba).

Se añade `height=0` a la columna: mide el alto de la ventana, la lista se queda con el hueco libre
y se desplaza hasta el final. La cabecera y la sección «Interrumpida» son hermanas de la lista, así
que **no se mueven**.

## 3. «Sistema» + micrófono: audio solapado y desfasado

### Causa

Dos fallos independientes en `platform/ffmpeg.py:mux_cmd()`:

- **Nivel (`normalize=0`)**: el `amix` sumaba a ganancia unidad. Dos fuentes a −0.6 dBFS dan picos
  de **+3.5 dB** y el AAC recorta (medido: pico 1.47 y ~20 % de muestras por encima de 0 dBFS).
  Suena a voz duplicada y pisada, y empeora si el loopback ya lleva la voz del micrófono
  (altavoces): sumar la misma señal dos veces da +6 dB y filtrado en peine (eco).
- **Desfase (`-itsoffset`)**: el desfase positivo se aplicaba como **opción de entrada**, y ese
  desplazamiento **no sobrevive al `amix`**. Medido con FFmpeg 7.1 y un clic de 20 ms a 1.0 s en
  cada pista:

  | caso (clic esperado) | antes | después |
  |---|---|---|
  | sistema +0.10 / mic −0.20 → 800 / 1100 ms | **821 y 1021 ms** | 800.1 y 1100.1 ms |
  | sistema +0.20 / mic +0.40 → 1200 / 1400 ms | **1021 ms (los dos clics colapsados)** | 1200.1 y 1400.1 ms |
  | dos tonos fuertes (pico ≤ 1.0) | **1.474 (satura)** | 0.771 |

  Con un solo desfase positivo el bloque entero se adelantaba (el micrófono sonaba antes de
  tiempo y encima de lo que ya traía el loopback: «se solapa») y con dos, el `amix` alineaba las
  pistas por el inicio de cada WAV y las **colapsaba en el mismo instante**.

### Arreglo

- El desfase positivo se aplica **dentro del filter graph** con `adelay=<ms>:all=1`, por pista y
  **después** de `atrim`/`asetrate`/`aresample` (el retardo es tiempo real de la pista ya
  corregida). Se elimina `-itsoffset`. El audio final empieza en 0, como el vídeo.
- La mezcla pasa a `amix=inputs=N:normalize=1:duration=longest`: reparte la ganancia entre las
  pistas activas, sin recorte, sin distorsión y **sin latencia añadida** (se descartaron
  `alimiter`, que retrasa 5 ms, y `asoftclip`, que distorsiona).
- El signo de `offset_s` no cambia (+ = el audio arrancó después del primer frame, − = antes y se
  recorta con `atrim`), las dos pistas se siguen midiendo contra el mismo `t0_video`
  (`RecordingSession._audio_offset`) y el modo `separate` (docs/50) no se ve afectado.
- El esquema del **sidecar** y de `session.json` no cambia.

## Verificación

- `tests/test_ui_layout.py` (16 tests, nuevo): cada `source_kind`, tres tamaños de ventana,
  cámara + pistas separadas, y 24 filas sintéticas con scroll hasta la última. Con el layout
  anterior fallaban 8. El fixture dobla también la enumeración de monitores, audio, cámaras y
  encoders: `Controller.boot()` la hace en un hilo y, sin doblarla, publicaba el hardware real
  después de que el test fijara el suyo (en un runner sin cámara, `sel-camera` desaparecía).
- `tests/test_audio_sync.py` (9 tests, nuevo): WAV sintéticos + FFmpeg real, mide dónde caen los
  clics (≤ 10 ms) y cuánto satura la mezcla; cubre desfases de signo distinto, dos positivos, dos
  negativos, deriva + retardo, punta a punta por `RecordingSession._mux` y el modo separado.
- Goldens de `tests/test_ffmpeg_cmd.py` actualizados (`adelay`, `normalize=1`, sin `-itsoffset`).
- `uv run pytest` → **680 passed**; `uv run aurora check main.py` → sin avisos (también a
  900x600 y 820x600); `uv run aurora tree main.py` → «Sin avisos de layout».

## Riesgos residuales

- Por debajo de ~810 px de ancho de ventana las dos columnas de «Grabar» ya no caben (196 nav +
  300 mínimo del preview + 310 del panel). No está acotado con `min_size` en `src/recstudio/app.py`.
- La latencia de captura de cada dispositivo (el offset se toma en el callback, ~1 bloque ≈ 21 ms
  ya capturado, más la del motor WASAPI) sigue siendo un sesgo común que no se corrige: si el
  loopback y el micrófono tienen latencias muy distintas queda un error relativo de decenas de ms
  que solo se puede medir con hardware real (`tools/av_sync_test.py`).
- En modo `separate` el WAV del micrófono se copia tal cual: su `offset_s` es correcto, pero no se
  le aplica la corrección de `drift_ppm`, así que en grabaciones muy largas (>10 min) puede
  derivar respecto al vídeo.
- `normalize=1` reparte la ganancia: con las dos pistas activas cada una entra a −6 dB (nivel
  total correcto). Si se quisiera mantener ganancia unidad habría que añadir un limitador con
  look-ahead y compensación de latencia.
- Si Windows tiene «Escuchar este dispositivo» activo, el loopback ya contiene la voz del
  micrófono y el eco persiste: es configuración del sistema, no del código.
