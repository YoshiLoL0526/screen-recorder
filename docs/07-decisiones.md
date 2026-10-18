# 07 — Registros de decisiones (ADR)

Formato: contexto → decisión → alternativas descartadas → consecuencias → cuándo se revisa.
Fechadas a 2026-10-16; las marcadas *(pendiente F0)* se confirman con los spikes.

---

## ADR-01 — Stack: Python + aurora-ui

**Contexto.** Hay que hacer una app de escritorio de grabación + edición; el autor es el autor
de Aurora y la UI debe hacerse con `aurora-ui`.

**Decisión.** Aplicación Python 3.12, UI 100 % Aurora (sin WebView ni toolkit de otra época).
El trabajo pesado (captura, encode) en librerías nativas de Python/Rust y FFmpeg.

**Alternativas.** Qt/PySide6 + MLT/libopenshot (maduro pero no es Aurora y el editor C++ pesa);
Electron/Tauri (doble tecnología, WebView); WPF/WinUI (salir de Python).

**Consecuencias.** + Una sola tecnología, dogfooding de Aurora, iteración rápida.
− Se heredan los límites de Aurora 0.5 (sin ventanas frameless, sin `on_hover`, sin `canvas` de
imágenes…); el proyecto los documenta y los convierte en issues. En 0.4/0.5 desaparecieron varios
(slider nativo, accesibilidad, `wrap`, multi-ventana, diálogos, bandeja y menús).

**Revisar.** Si aparecen tres o más bloqueos duros de UI, revisar en conjunto con el roadmap de Aurora.

---

## ADR-02 — Motor de render: FFmpeg como proceso separado

**Contexto.** Se necesita codificar en vivo y renderizar proyectos con transiciones/efectos.

**Decisión.** El encoding y el render corren en un **ejecutable FFmpeg** lanzado por subprocess
(binario provisto por el wheel de `imageio-ffmpeg`, o del sistema si existe y es reciente).
Python construye los argumentos y los `filter_complex` (`core/graph.py`).

**Alternativas.** PyAV para *encodear también*: mismo FFmpeg por dentro, pero atado a las
versiones del wheel y a una API de más superficie para filtros; **moviepy**: abandonado/lento,
inaceptable en tiempo real; **GStreamer**: instalación pesada y curva mayor; **OpenCV
VideoWriter**: pocos códecs y mala calidad/bitrate.

**Consecuencias.** + Filtros completos (`xfade`, `eq`, `concat`, `xfade`…), encoders hardware,
CPU/GPU probados; el proceso aislado no puede tumbar la app; facilita *corte rápido* con `-c copy`.
− Hay que gestionar el binario (buscarlo, incluirlo al empaquetar, versión mínima) y parsear el
progreso (`-progress pipe:1`).
− Licencia: el build GPL (libx264) se distribuye como ejecutable separado; se acredita en "Acerca de".

**Revisar.** Nunca (es el núcleo del producto); sí revisar la *versión mínima* de FFmpeg exigida.

---

## ADR-03 — PyAV para decodificar, sondear y muestrear

**Contexto.** El editor necesita: metadatos de assets (probe), frames para el preview, audio
para waveforms y visualizado.

**Decisión.** `av` (PyAV) para todo lo que sea **leer**: probe, decode de video a numpy, decode
de audio a float32, listado de keyframes.

**Alternativas.** Invocar `ffmpeg` por cada frame (inviable); volcar PNGs temporales a disco
(IO absurdo); OpenCV (otra pila de codecs, y necesita FFmpeg igualmente).

**Consecuencias.** + Un solo API Python, frames como numpy directos (sin archivos temporales),
seek/streaming nativo; wheels con FFmpeg embebido (no depende del binario de ADR-02 para leer).
− Otra copia de FFmpeg en el bundle (tamaño); hay que fijar versión de `av` y probar el decode
de los formatos que produce nuestro propio grabador (test de integración).

**Revisar.** Si el peso del bundle o los choques de versión con el binario de ADR-02 se vuelven
un problema (plan B: usar el mismo FFmpeg para read también, vía subprocess + pipes raw).

---

## ADR-04 — Captura: `windows-capture` + `dxcam` + `mss` con selección automática

**Contexto.** En Windows hay dos APIs modernas (WGC y DXGI) y una portátil (GDI vía `mss`).
Ninguna cubre todo: WGC captura *ventanas* y *cursor*; DXGI es el más rápido por monitor pero
sin cursor; `mss` funciona en todas partes pero con GDI (lento).

**Decisión.** Interfaz `CaptureBackend` común y tres implementaciones. **Actualizado con S1
(F0, medido):** por defecto **WGC (`windows-capture`)** para monitor, ventana y cursor
(~98 fps, 18.6 % CPU, cadencia estable con `minimum_update_interval=1`); `dxcam` queda como
*modo altas fps* para monitor sin cursor (~135 fps, pero ~65 % de un núcleo); `mss` para
screenshots de referencia y portabilidad (48 fps). El usuario puede forzar backend en Ajustes
(y la UI avisa de las limitaciones).

**Alternativas.** Solo WGC (más simple, pero pierde los fps altos de DXGI y su ecosistema);
solo `mss` (simple, pero el peor presupuesto); captura nativa en Rust dentro de Aurora (lo mejor
a largo plazo, pero mezcla dos productos y retrasa v1).

**Consecuencias.** + Cada caso de uso usa la herramienta correcta; el aislamiento hace fácil
añadir macOS/Linux después; el backend por defecto es también el más barato en CPU.
− Tres caminos que testear (matriz pequeña y automatizable); queda documentado que WGC es
damage-driven sin `minimum_update_interval` (4.5 fps medidos en pantalla estática) y que
`monitor_index` es 1-based.

**Revisar.** Si DXGI deja de ser necesario (los fps altos no se piden), simplificar a dos
backends. Si algún día Aurora expone captura nativa, mover aquí (ADR nuevo).

---

## ADR-05 — Audio: PyAudioWPatch (WASAPI loopback)

**Contexto.** Grabar el **audio del sistema** en Windows exige WASAPI loopback. Hay que
capturar además micrófono y medir drift.

**Decisión.** `PyAudioWPatch` (fork de PyAudio con loopback) para entrada; `sounddevice` solo
para **salida** (preview del editor).

**Alternativas.** `soundcard` (API más limpia, históricamente con *glitches*/latencia irregular
en loopback continuo); `sounddevice` para loopback (PortAudio upstream no expone loopback en
Windows de forma estable); escribir COM/WASAPI a mano con `comtypes` (control total,
mucho más código y riesgo).

**Consecuencias.** + Loopback fiable con la API clásica de PyAudio; probado por la comunidad.
− Es Windows-only (encapsulado en `platform/audio.py`); se mantiene `soundcard` en el radar como
cruce (portabilidad mac/linux) y como plan B si aparece un bug bloqueante.

**Revisar.** S3 y el test de 10 min con drift; si el drift o los glitches aparecen, evaluar
`soundcard`/COM en ese orden.

---

## ADR-06 — Grabar en MKV temporal y muxear a MP4 al terminar

**Contexto.** Los códecs en MP4 están pensados para archivos "cerrados" (moov al final);
un crash a mitad de grabación puede dejar el MP4 irrecuperable. MKV está diseñado para streaming
y sobrevive a cortes.

**Decisión.** Grabar siempre a `temp/<sesión>/video.mkv` (+ WAVs de audio) y al detener hacer
mux/remux a `~/Videos/RecStudio/<nombre>.mp4` con `-c:v copy -c:a aac` (instantáneo si el codec
de audio ya es AAC; aquí es PCM→AAC, coste mínimo).

**Alternativas.** MP4 directo (riesgo de pérdida en crash); MP4 fragmentado
(`movflags frag_keyframe+empty_moov`: recuperable, pero peor compatibilidad con editores
intermedios y más overhead de muxing en vivo); grabar en 2 formatos a la vez (IO duplicada).

**Consecuencias.** + Resiliencia real (RNF-08) y coste de conversión cero. − Un paso extra al
detener (mux, < 1 s típico) y archivos temporales que hay que limpiar (política: al recuperar o
al descartar).

**Revisar.** Nunca.

---

## ADR-07 — Encoder automático con fallback universal a libx264

**Contexto.** El rendimiento de grabación depende de usar el encoder hardware de la GPU; pero
no todo el HW lo tiene (o está bloqueado por drivers).

**Decisión.** Detección en el primer arranque (encode de prueba de 0.1 s por encoder) y orden
`h264_nvenc → h264_amf → h264_qsv → libx264`; resultado cacheado en Ajustes, con override manual.
Si el hardware falla al iniciar una grabación, reintento automático con `libx264`.

**Alternativas.** Solo libx264 (simple, pero 30–45 % de CPU y limita a 1080p60 en CPUs modestas);
solo hardware (usuarios sin él se quedan sin app); HEVC por defecto (más eficiente, peor
compatibilidad y más CPU para exportar).

**Consecuencias.** + Calidad/CPU óptimas en cada máquina; decisiones transparentes (se muestra
qué encoder se usó). − Matriz de flags por encoder que mantener; se documentan y testean por HW.

**Revisar.** Al añadir AV1/HEVC como opción de export.

---

## ADR-08 — Timeline: un canvas + capa de cajas interactivas

**Contexto.** El timeline tiene zoom, pan, waveforms, cientos de clips potenciales y drags
precisos (mover/trim/scrub). Opciones: (a) cada clip es un elemento Aurora; (b) todo pintado a
mano en `canvas()`; (c) híbrido.

**Decisión.** (c) Híbrido: **un `canvas()`** dibuja todo (coordenadas de contenido, culling por
rango visible) y encima una **capa de cajas `position="absolute"`** por clip visible
(cuerpo con `cursor="grab"`, dos asas con `cursor="col-resize"`), que aportan hover, cursores y
`on_drag` sin replicar hit-testing para esas zonas. Los `Signal` del timeline
(`zoom`, `px_offset`, `project`, `selection`, `playhead`) gobiernan el dibujo.

**Alternativas.** (a) Elementos por clip: rebuilds masivos con cada zoom/scroll, waveform
seguiría necesitando canvas, y los drags serían N handlers. (b) Solo canvas: sin cursores por
zona (Aurora 0.5 sigue sin `on_hover`) y sin `bg_hover`.

**Consecuencias.** + Rendimiento y control total del detalle visual; los patrones nativos de
Aurora se usan donde aportan. − Dos fuentes de verdad de posiciones (dibujo y cajas) que hay que
mantener sincronizadas (una función `clip_rect(clip) -> (x, w)` única); hit-testing manual para
regla/playhead/vacío.
**Confirmado en F0 (S5/S7):** las cajas absolutas dentro del `scroll` se mueven con el contenido,
reciben clics y `cursor`/`bg_hover` en hover. La interacción se hace **por identidad + deltas**
(`e.dx/dy` acumulados; `e.x/y` vienen en coordenadas de ventana) porque `on_click` no lleva
posición: el "clic para saltar a t" queda fuera de v1 (asa de playhead + scrub relativo + flechas).

**Revisar.** Cuando Aurora incorpore coordenadas de nodo en los eventos (o `on_hover`), se podrá
simplificar la capa de cajas y recuperar el clic posicional.

---

## ADR-09 — Preview del editor como JPEG en memoria (`image(bytes)`)

**Contexto.** Hay que mostrar ~30 fps de video en la UI. Aurora pinta imágenes desde archivo o
**bytes en memoria** (`image()`), decodificando una vez por objeto `bytes`; `canvas()` aún no
pinta imágenes.

**Decisión.** El preview es `image(preview_frame())` con `preview_frame: Signal[bytes]`: el
servicio de playback decodifica (PyAV) → `to_ndarray(rgb24)` → **`Image.reduce(k)`** (decimación
entera, filtro de caja) + JPEG q80 4:2:0 → `bytes` → `Signal.set`. Nunca se escribe a disco para
el preview. **Medido en F0 (S4/S4c): 8.5 ms/frame (p95 9.9) a 960×540 ⇒ >100 fps posibles.**
Se descartaron con datos `frame.to_image()` (30 ms) y `cv2` (8–9 ms solo el escalado, además de
meter opencv en la app).

**Alternativas.** PNG en memoria (10–20× más pesado de codificar para nada: es un frame de
paso); archivos temporales (IO y caché de decode inútil); esperar a un `canvas.image()` futuro
(mejor solución nativa, pero no existe en 0.3).

**Consecuencias.** + Cero IO; el coste real estaba en el escalado, y `reduce(2)` lo resolvió
(4.4 ms → 5.6 ms con JPEG); ni `cv2` ni PNG hacen falta. − Un JPEG por frame sigue siendo CPU
(asumible con el margen medido); si el presupuesto aprieta, bajar resolución (estrategia
adaptativa de 04).

**Revisar.** Cuando el canvas de Aurora pueda pintar RGBA directo (se cambiaría a eso, sin JPEG).

---

## ADR-10 — Undo por snapshots del `Project` (frozen)

**Contexto.** Undo/redo fiable con el mínimo código posible, y el estado ya es inmutable
(requisito para `Signal` y `@component`).

**Decisión.** Pila de snapshots `(etiqueta, Project)`; 50 niveles; las operaciones de drag se
agrupan con `begin()/commit()`. Redo = pila paralela; guardar limpia redo.

**Alternativas.** Patrón command (do/undo por operación): más memoria-eficiente en proyectos
gigantes, pero mucho más código y más superficie de bugs por operación. Diff/CRDT: sobre-ingeniería
para una app local de un usuario.

**Consecuencias.** + Imposible corromper el estado con un undo mal escrito; trivialmente correcto.
− Memoria O(historial × tamaño del proyecto): con el modelo descrito (JSON modesto) es
insignificante; si algún día un proyecto con 10 000 clips lo hiciera pesado, se cambia la pila a
diffs (el API del store no cambia).

**Revisar.** Si el tiempo de `==` del proyecto supera 1 ms en proyectos reales (medir en F3).

---

## ADR-11 — Proyecto en JSON versionado (no SQLite)

**Contexto.** Guardar el proyecto (assets, clips, transiciones) de forma robusta, inspeccionable
y diffable; el usuario es técnico.

**Decisión.** `<nombre>.rsproj/project.json` con `schema_version` + migraciones.

**Alternativas.** SQLite (transaccional, pero opaco, overkill para un documento y complica el
versionado en git); formato binario/pickle (inseguro y no inspeccionable); un solo archivo
comprimido junto a los medios (`tar`/`zip`: complica el autosave atómico).

**Consecuencias.** + Editable a mano, diff en git, migraciones triviales, autosave atómico con
`replace`. − Sin transacciones: mitigado con `.tmp` + `.bak` y validación al cargar.

**Revisar.** Si aparecen necesidades de búsqueda/índices (biblioteca grande) → eso irá a aparte
(un índice SQLite de la *biblioteca*, no del proyecto).

---

## ADR-12 — Dos modos de audio al grabar: mezclar o pistas separadas

**Contexto.** Quien graba tutoriales suele querer voz y sistema separados ("por si regrabo la
voz"), pero el 90 % de los casos quiere un archivo final listo.

**Decisión.** Default `mix` (una sola pista AAC en el MP4 final). Opción `separate`: el MP4
lleva el audio del sistema y el micrófono queda como WAV hermano, importable como pista de audio
del editor (que ya soporta hasta 2 pistas).

**Alternativas.** Siempre separado (más fricción al compartir); siempre mezclado (cierra la
puerta a regrabar voz); mezclar con diferentes gains automáticos (adivinar demasiado).

**Consecuencias.** + Cubre ambos comportamientos con un solo switch; reutiliza el editor de
pistas de audio existente. − El modo separate deja un archivo extra que documentar en la UI.

**Revisar.** Con telemetría de uso (post-v1) o si el editor añade "regrabar voz" guiado.

---

## ADR-13 — Español primero con `strings.py` (no gettext)

**Contexto.** Los textos de la UI hay que centralizarlos, y la decisión de idiomas no debe
bloquear el desarrollo.

**Decisión.** Un módulo `strings.py` con constantes en español; el idioma del código y de los
identificadores, en inglés.

**Alternativas.** `gettext`/Babel desde el día 1 (infraestructura para un solo idioma);
textos hardcodeados (deuda inmediata).

**Consecuencias.** + Refactor de i18n trivial cuando haya un segundo idioma (el 90 % es sustituir
constantes por un lookup). − Los plurales/formatos complejos habrá que resolverlos entonces.

**Revisar.** Con el primer idioma adicional.
