# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/); versiones con
[SemVer](https://semver.org/lang/es/) desde la 1.0.0 (las fases anteriores no se publicaron como versión). El
detalle de cada fase está en `docs/08`–`docs/16`.

## [Sin publicar]

### Añadido
- **Añadir claves de animación desde el timeline** (docs/42; modelo v13 sin cambios): modo
  **Añadir clave con clic** (`N` o switch de la sección Animación), excluyente con razor,
  marquesina y Env. Un clic sobre la barra del elemento/texto seleccionado crea la clave del
  canal activo en ese instante con el valor interpolado o su base y el easing del inspector; si
  ya existe, la actualiza, y queda seleccionada para arrastrarla. Una entrada de undo por clic
  (`añadir clave X`), sin mover la capa; `Esc` cancela el gesto y sale del modo.
- **Easing de las claves de animación** (docs/39; modelo v13 con migración v12 → v13):
  `Keyframe.easing` (`linear`, `ease_in`, `ease_out`, `ease_in_out`) con curvas cúbicas
  compartidas por `value_at` y las expresiones de FFmpeg, selector **Easing** en la sección
  Animación del inspector (decide las nuevas claves y no se persiste) y paridad preview/export,
  incluidos los canales horneados. Las claves lineales conservan las cadenas históricas; las
  envolventes de audio y el color animado de textos siguen lineales.
- **Asas de animaciones visuales en el timeline** (docs/38; modelo v12 sin cambios): selector de
  canal en el inspector, polilínea y asas arrastrables sobre la barra de un elemento/texto para
  `x`, `y`, `opacity`, `scale`, `rotation` y `size`. `dx` mueve el tiempo local y `dy` el valor
  con su rango; un undo por gesto, clic sin undo, `Supr`/menú contextual para borrar y `Esc` para
  cancelar. Las asas viven encima del playhead.
- **Color y tamaño animados de textos/subtítulos** (docs/36; modelo v12 con migración v11 → v12):
  canal `size` (`8…200`) en `TextOverlay.animations` y `TextOverlay.color_keys` con
  `ColorKeyframe(t, color)` e interpolación lineal en sRGB. El preview y el export comparten el
  rasterizado de PIL y el bake `.mov` `qtrle`; los textos sin estas animaciones conservan
  `drawtext`. Claves **Color** y **Tamaño** desde el inspector.
- **Edición de claves de envolvente en el timeline** (docs/37; modelo v11 sin cambios): asas
  arrastrables sobre la waveform de clips de V1/pistas de audio y sobre la barra de PiP/V2/V3
  (`dx` = tiempo local, `dy` = ganancia 0…4), modo **Env** (`E`) para añadir claves con un clic,
  `Supr`/menú contextual para borrarlas y undo/redo por gesto. Las asas viven encima del playhead
  para que su línea no bloquee el clic.
- **Animación de escala y rotación de textos** (docs/35; modelo v10 sin cambios): canales `scale`
  (`0.05…4.0`) y `rotation` (`-360…360`) en `TextOverlay.animations`, campos no persistidos y
  claves desde el inspector. El texto transformado se rasteriza con PIL (la misma función del
  preview) y se hornea a un vídeo RGBA `qtrle` para el export; los textos estáticos siguen usando
  `drawtext` en el mismo orden.
- **Envolventes de audio de elementos** (docs/34; modelo v11 con migración v10 → v11):
  `Element.envelope` lleva claves locales de ganancia 0…4 al audio de un PiP o de un clip de
  V2/V3, multiplica su volumen estático, usa `volume=…:eval=frame` en el export y la misma mezcla
  WAV en el preview; se edita desde el inspector y la barra del timeline marca **◆ env**.
- **Animación de escala y rotación de elementos** (docs/33; modelo v9): canales `scale`
  (`0.05…4.0`) y `rotation` (`-360…360`) en `Element.animations`, claves desde el inspector y un
  vídeo RGBA intermedio (`qtrle` con PIL) para que preview y export compartan la geometría.
- **Subtítulos SRT/VTT** (docs/30; modelo v9): importación desde Capas, cues como textos
  `subtitle=True` (timeline teal, estilo global) y exportación a SRT.
- **Keyframes de posición y opacidad** (docs/31; modelo v9): X/Y de elementos y textos + opacidad
  de textos, con claves en el inspector, interpolación lineal y expresiones reales en preview y
  export.
- **Envolventes de volumen de audio** (docs/32; modelo v10 con migración v9 → v10):
  `Clip.envelope` con claves locales de ganancia 0…4 en clips de video y pistas de audio,
  `volume` evaluado por frame en el export, mezcla WAV idéntica en el preview, claves desde el
  inspector, polilínea sobre la waveform y bloqueo del corte rápido.
- **Pistas de video V2/V3** (docs/29; modelo v8 con migración v7 → v8): varios clips de video
  superpuestos en dos pistas, con z-order (V2 → V3 → Elementos), filas propias en el timeline,
  inserción desde el panel/Capas/arrastre, cambio de pista en el inspector y audio mezclado. Los
  efectos globales se aplican a V2/V3 antes de la capa «Elementos».
- **PiP de video** (docs/28; modelo v7 con migración v6 → v7): un video se puede añadir como
  elemento superpuesto desde el panel de medios o desde «Capas». Se compone en preview y export
  con posición, tamaño, rotación, opacidad, encaje e intervalo; `Element.in_` permite recortar la
  fuente por la izquierda. Su **audio se mezcla** con el del proyecto (volumen propio, botones
  Silenciar/Original y ducking) en preview y export.
- **Panel de medios y capas del editor** (docs/27): columna izquierda con todos los assets del
  proyecto, miniaturas, inserción en el playhead, arrastre al timeline/preview, `on_drop` del
  Explorador y quitar medios sin usar. Pestaña «Capas» siempre visible con video, textos y
  elementos en orden de apilado.
- **Selección directa en el preview** de clip, elemento y texto: caja, asas, mover/escalar,
  undo por gesto y `Esc` para cancelar. Los textos tienen caja real y asas de tamaño; los drags
  abren la transacción solo al moverse.
- **Carriles de textos y elementos en el timeline**, con barras movibles/recortables, snap y
  cabeceras; la geometría usa `Tracks.video_top`.
- **Inspector**: `number_input` para posición/tamaño/rotación/opacidad, **volumen del PiP**,
  **opacidad de texto** y **keyframes**; `color_picker` para elemento/texto; campo de texto del
  título con ancho correcto.
- **Portapapeles**: copiar/pegar/duplicar elementos, además de clips, audio y textos.
- **Paridad de texto**: el preview dibuja el mismo contorno que `drawtext`.

### Cambiado
- Atajo **Ctrl+B** para mostrar/ocultar el panel de medios.
- La importación rápida sigue añadiendo al final; importar al panel es una acción explícita.

### Corregido
- Clic en el preview sin selección ya no deja una transacción de undo abierta.
- El campo de edición de texto ya no se pinta truncado a ~22 px.

## [1.2.0] — 2026-11-05

### Añadido
- **Features v6 del editor** (plan en [docs/22](docs/22-plan-features-editor.md); modelo v6 con
  migración v5 → v6):
  - **Canvas interactivo** (docs/23): mover y redimensionar el clip seleccionado directamente
    sobre el preview, con caja/asas, ancla opuesta, un undo por gesto y `Esc` para cancelar.
  - **Audio del video separado y enlazado por defecto** (docs/24): al importar se crea la pista
    «Audio del video»; mover/recortar/dividir/cambiar velocidad mantiene la sincronía y hay
    comandos para desvincular y volver a vincular sin perder el audio editado.
  - **Elementos superpuestos** (docs/25): figuras (rectángulo, elipse, triángulo) e imágenes con
    intervalo, posición/tamaño, rotación, opacidad, z-order y blur; composición idéntica en preview
    y export, con PNG de figuras/blur en cache atómica.
  - **Efectos globales** (docs/26): brillo/contraste/saturación, blur, viñeta y blanco y negro
    sobre todo el video compuesto, antes de elementos y textos; sin efectos el filter graph queda
    byte a byte como antes.
- **394 tests** y `aurora check`/`aurora tree` sin avisos; `tools/f3_editor_test.py --no-shots` y
  `tools/f4_export_test.py --skip-long --no-shots` verificados tras la integración.

### Cambiado
- **aurora-ui actualizado de 0.9.0 a 0.10.0** (`>=0.10.0,<0.11.0`). La API que usa la app no
  cambió, pero 0.10 valida props/handlers al crear el elemento y avisa de controles sin nombre
  accesible (`no-label`). Se añadió `label=` a botones de icono, switches, campos de texto, fondos
  de modal, transiciones y marcadores; `aurora check main.py` queda **sin avisos**. La auditoría con
  `Pilot.layout()` sobre todas las pantallas, modales, temas y 3 tamaños da **0 avisos de layout**.
  Se verificaron 324 tests, F2/F3/F5 y `tools/check_build.py` (256 MB, smoke OK con `aurora
  0.10.0`). Docs actualizados: `docs/02`, `docs/05`, `docs/15` y `AGENTS.md`.

## [1.1.0] — 2026-11-01

Bandeja y atajos globales, filmstrip, bloques A y B y las fases 0–3 del editor sobre la 1.0.0.
Es el primer release publicado en GitHub (instalador y zip portable).

### Documentación
- **[docs/21 — Fase 3: flujo de edición](docs/21-fase-3-flujo.md)**: modelo v5, reglas del pegado y
  del bloque, loop/J/K/L, export de rango, decisiones de teclas y límites (A17 por Aurora 0.9).
- **[docs/15 — Roadmap post-v1](docs/15-post-v1.md)**: fuente de verdad de lo que falta, por bloques
  (entrega y distribución, editor, captura, compartir, plataforma y adopciones de Aurora), con estado,
  tamaño y criterio de aceptación de cada pieza, más una priorización recomendada. `docs/06` conserva
  las fases F0–F5 y apunta al 15 para el backlog.
- **[docs/16 — Bloque A: cierre de la entrega](docs/16-bloque-a-entrega.md)**: resultados de A1–A5
  (CI en verde, RNF-06/07, instalador, bundle ligero) y procedimiento de las comprobaciones manuales
  que quedan (A2, A6, A7, A8). `docs/15` actualiza estados y prioridades.
- **[docs/17 — Bloque B: editor completo](docs/17-bloque-b-editor.md)**: música/ducking, textos,
  selección múltiple, snap por waveform, proxy y reubicar carpetas, con archivos, tests y límites.

### Añadido
- **Releases en GitHub**: `.github/workflows/release.yml` se lanza con un tag `vX.Y.Z`, repite
  tests, build con smoke e instalador, y publica el instalador, un zip portable y estas notas
  (`tools/release_notes.py`, que falla si el tag no coincide con la versión del código).
- **Fase 3 del editor: flujo de edición** (docs/18 → [docs/21](docs/21-fase-3-flujo.md)):
  - **Modelo v5**: marcadores e In/Out del timeline en `project.json`, con migración v4 → v5 (los
    proyectos v1–v4 se abren idénticos).
  - **Portapapeles**: `Ctrl+C`/`Ctrl+X`/`Ctrl+V`/`Ctrl+D` para clips de video, de audio y textos,
    también con selección múltiple; pega en el playhead conservando transformación, efectos y el
    enlace audio↔video, con un solo undo.
  - **Marquesina que mueve**: arrastrar un clip de la selección mueve el bloque (un undo, respeta
    pistas bloqueadas y snap).
  - **Razor** (`C`, botón en la barra del timeline): corta donde se pulsa, en una pista o en todas
    desde la regla; excluyente con la marquesina (`V` vuelve a selección).
  - **Marcadores** (`B`): banderas en la regla, ↑/↓ para saltar, renombrar/borrar en el inspector
    y snap.
  - **In/Out y loop**: `I`/`O` marcan el rango del timeline, `Ctrl+L` reproduce en bucle el rango
    (o todo) y el modal de export ofrece **exportar solo el rango** (render y corte rápido).
  - **J/K/L** con velocidad creciente (hasta 8×, marcha atrás sin audio) y **Q/W** para recortar
    hasta el playhead.
- **Bloque B del editor** (docs/15 → docs/17):
  - **Música**: importar audio, pista M en el timeline, volumen de pista y de clip, **ducking**
    (`sidechaincompress` con la voz del video) y **crossfades** (`acrossfade`). El preview
    renderiza la mezcla completa a un WAV cacheado y el export usa el mismo graph.
  - **Textos**: `TextOverlay` (posición, tamaño, color, negrita, intervalo), `drawtext` en el
    export y composición con PIL en el preview; bloquean el corte rápido.
  - **Selección múltiple**: marquesina, `Ctrl+A` y borrado de N clips con un solo undo.
  - **Snap por waveform**: los picos de audio de los clips vecinos y de la música son candidatos
    del imán al mover o recortar.
  - **Proxy 540p** automático para >1080p o >60 fps, cacheado; el preview lo usa y el export sigue
    usando el original.
  - **Reubicar carpeta**: resuelve todos los assets offline por nombre en una sola operación.
- **Bloque A de entrega** (docs/15 → docs/16):
  - **Instalador Inno Setup** (`installer/rec-studio.iss`): por usuario, sin UAC, en español, con
    accesos directos y desinstalador. `tools/check_installer.py` compila, instala en `%TEMP%`, hace
    el smoke del exe instalado y desinstala; el CI lo repite y sube `RecStudio-1.0.0-setup.exe`
    (72 MB).
  - **Bundle ligero**: `tools/build_exe.py` envuelve `aurora build` para excluir `cv2` (OpenCV,
    112 MB) y `platform/cv2_shim.py` cubre el único import de `windows-capture`
    (`Frame.save_as_image`). El bundle pasa de 370 a **255 MB**; `dxcam` avisa si se elige en el
    exe ligero. `uv run aurora build` sigue incluyendo OpenCV para desarrollo.
  - **`tools/rnf_measure.py`**: mide RNF-06/07 con ventana real (arranque, RSS en reposo y RSS
    grabando 30 s) en el exe empaquetado o en la app sin empaquetar.
- **Filmstrip de miniaturas en el timeline** (backlog post-v1 nº2): cada clip muestra una tira de
  miniaturas sobre la pista de video, con su nombre y duración encima. `canvas()` no dibuja imágenes,
  así que es una capa de `image()` absolutas **entre el canvas y las cajas de interacción** (arriba
  del canvas para verse, debajo de las cajas para no comerse los clics ni los drags).
  - `services/media.thumbstrip(path)`: 24 miniaturas JPEG repartidas por toda la duración del
    **asset** (no del clip), con cache en memoria y en disco; recortar, mover o reordenar clips
    reutiliza la misma tira. Se calcula en un hilo al abrir/importar/reubicar (`_queue_thumbs`).
  - `ui/timeline.filmstrip_slots` (puro): qué miniaturas caen dentro del clip y de la vista, con el
    tiempo de fuente y la velocidad; culling por vista, tope de 60 imágenes por frame y ensanchado
    con solape cuando el zoom deja los huecos muy estrechos.
  - 8 tests en `tests/test_filmstrip.py` (colocación pura + la tira real con FFmpeg) y uno de UI en
    `tests/test_f3_editor.py`.
- **Bandeja del sistema y atajos globales** (backlog post-v1 nº1):
  - `ui/tray.py`: icono en la bandeja con un menú que sigue al estado («Grabar/Detener», «Pausar/Reanudar»,
    «Abrir Rec Studio», «Salir») y aviso del sistema al terminar. Cerrar la ventana **mientras se graba**
    deja la grabación en marcha (se vuelve desde el icono, su menú o la notificación); sin grabación en
    curso, la X termina la app.
  - `platform/hotkeys.py`: F9/F10 globales con `pynput`, **opt-in** (instalan un hook del teclado del
    sistema). Si el hook no se puede instalar, se revierte el ajuste y se avisa.
  - Ajustes → «Escritorio»: atajos globales, bandeja y aviso al terminar.
  - `[tool.aurora.build] include = ["assets"]`, para que el icono de la bandeja viaje en el exe (el smoke
    del bundle comprueba que está).
  - 16 tests nuevos en `tests/test_desktop.py` (hook con backend falso, menú, cierre de ventana y avisos).

### Cambiado
- **El inspector se oculta con `P`** (antes `I`, que ahora marca la entrada del timeline). El proyecto pasa a **schema v5**.
- **Proyecto schema v2**: `Track.ducking` y `Project.texts`; los `.rsproj` v1 se abren y se
  migran al guardar (los de una versión más nueva siguen abriéndose en solo lectura).
- **CI en verde y más completo** (`40701857907`): 208 tests, build ligero + smoke del exe e
  instalador (compilación, instalación silenciosa, smoke y desinstalación). El bundle y el
  instalador se suben como artefactos. `upx = false` documentado: 78 MB pero arranque de 3.66 s
  y +107 MB de RSS (RNF-06 exige < 2 s).
- **Diálogos nativos de Aurora** en vez de ctypes: `platform/dialogs.py` (Windows: `GetOpenFileNameW`
  y `SHBrowseForFolderW`) desaparece y lo sustituye `ui/file_dialogs.py`, que envuelve
  `aurora.dialogs` (`open_files`, `pick_folder`) con los filtros de video y devuelve `Path`.
  Los tres usos (`browse_out_dir`, `browse_import`, `relocate_asset`) pasan a llamarlos desde el
  hilo de UI —la ventana es el padre del diálogo y el sistema corre su propio bucle mientras dura—
  y el trabajo lento que sigue (probe, importación, refresco de la biblioteca) se queda en sus
  hilos. Se gana: código multiplataforma, sin `ctypes`, y el diálogo sale modal sobre la ventana.
  Tests nuevos en `tests/test_file_dialogs.py` y uno de reubicar en `tests/test_f3_editor.py`.
- **aurora-ui alineado a 0.5.0**: la dependencia lleva techo (`>=0.5.0,<0.6.0`) porque Aurora es
  `0.x` y rompe compatibilidad entre versiones menores. Se re-validó contra el paquete instalado la
  tabla de «Limitaciones» de [docs/05](docs/05-ui-aurora.md): desde 0.3 ya existen _slider_ nativo,
  accesibilidad con AccessKit, `wrap` en filas y multi-ventana, y 0.4/0.5 añadieron diálogos
  nativos, bandeja, menús, notificaciones, `dialog`/`toast`/`progress`/`menu`/`accordion` y más.
  El resto de límites (ventanas frameless, `on_hover`, `canvas` sin imágenes, cajas que bloquean
  clics, etc.) siguen vigentes. Las capacidades nuevas que aún no usamos quedan listadas como
  candidatos en docs/05, sin cambiar código de la app en este paso.
- **aurora-ui actualizado a 0.9.0** (`>=0.9.0,<0.10.0`): se revisó el changelog 0.6–0.9 y se
  revalidó la tabla de límites de docs/05 contra el paquete instalado. Ninguna API usada se eliminó
  (0.6–0.9 son aditivas salvo detalles internos); el build sigue necesitando el parche de
  `aurora.build.pyinstaller_args` para excluir `cv2`, porque `[tool.aurora.build]` no expone
  `--exclude-module`. Se adaptó `ui/shell` para medir `state.view_h` con `on_resize` (0.7), que
  sustituye el rodeo por el `on_scroll` de la navegación, y `pytest` convierte en error
  `aurora.AuroraDeprecationWarning` (política de 0.6). Verificado con 227 tests, F3/F4 de
  aceptación, `tools/check_build.py` y `tools/check_installer.py` (bundle de 256 MB e instalador
  de 72 MB con `aurora 0.9.0`). Las capacidades
  nuevas (on_drop, splitter, remember, ventanas sin marco, listas de alto variable…) quedan como
  candidatos en docs/05 y docs/15.

### Corregido
- **Filmstrip y pósters de videos rotados** (móvil, 90/270°): se enderezan como en el preview; la caché cambia de clave (`thumbs2/`, `…-u.jpg`) para no servir las miniaturas viejas.
- **Carrera en la cache de miniaturas** (el test que tumbaba el CI): la UI leía el JPEG mientras
  el hilo lo escribía y la tira quedaba cacheada con 0 bytes. Ahora se publica con tmp +
  `os.replace` y `cached_thumbstrip` valida el SOI de JPEG; tiene test de regresión.
- Arranque con el «Acceso controlado a carpetas» de Windows Defender activado: la app moría al
  importar `state` con `FileNotFoundError [WinError 2]` al crear `~/Videos/RecStudio`. Ahora
  `paths.ensure_dir()` nunca lanza, la carpeta por defecto cae a `%LOCALAPPDATA%\RecStudio\videos`
  (con aviso al arrancar) y la grabación falla con un mensaje claro en vez de un traceback. Detalle
  en [docs/09](docs/09-f1-motor-grabacion.md) → «Bugs reales encontrados» (7).
- El guardado con debounce ya no escribe los ajustes después de cerrar: los tests pisaban el
  `settings.json` real (`Controller.shutdown()` cancela el `Timer` pendiente).
- Dos tests dependían del equipo y fallaban fuera del hardware de desarrollo: el de la región
  (en un monitor de 1366×768 la región inicial quedaba a ras del borde inferior y el redimensionado
  era un no-op) y el del `draw` del timeline con 100 clips (umbral absoluto de 8 ms, medido en
  desarrollo; una máquina modesta ronda los 16 ms). Ahora el primero parte de una región con margen
  y el segundo exige un frame de 30 fps más el *culling*, sin milisegundos fijos (ver [docs/05](docs/05-ui-aurora.md)).

## [1.0.0] — 2026-10-16

Primera versión completa: F5 (pulido) sobre F0–F4. Detalle en [docs/14](docs/14-f5-pulido.md).

### Añadido
- Divisores arrastrables en el editor: preview ↔ timeline (alto de las pistas) y preview ↔
  inspector (ancho), con topes según el tamaño de la ventana; se recuerdan entre sesiones.
- Inspector ocultable con `I` o el botón de panel de la barra del editor.
- Ajustes completos: selector nativo de carpeta, FPS/calidad/encoder/cuenta atrás por defecto,
  audio del sistema, audio del preview, preset de export, información del sistema (FFmpeg,
  encoders, monitores), «Restablecer disposición» y «Restablecer ajustes…».
- Botón «Cambiar…» para la carpeta de salida en la pantalla de Grabar.
- `Esc` cierra la confirmación y el editor de región; `Intro` acepta la región.
- Icono de la app (`assets/icon.ico`, generado por `tools/make_icon.py`) y `[tool.aurora.build]`.
- Smoke test del ejecutable (`Rec Studio.exe --smoke informe.json`) y `tools/check_build.py`.
- `tools/f5_screenshots.py` y 13 tests de F5 (`tests/test_f5_pulido.py`).
- CI de GitHub Actions en Windows: pytest y, si pasa, build + smoke del exe (artefacto).

### Corregido
- Con una región muy vertical el botón **GRABAR** quedaba fuera de la ventana.
- El preview del editor no aprovechaba el espacio al agrandar la ventana (quedaba pequeño y a
  la izquierda) y no se podía redimensionar.
- La pantalla de Ajustes no hacía scroll: lo de abajo era inalcanzable con ventanas bajas.
- El editor de región se salía de la ventana con monitores verticales.
- `settings.json` podía quedar vacío si dos guardados coincidían (ahora es atómico).
- FFmpeg desde el exe sin consola: `stdin=DEVNULL` para no heredar un handle inválido.
- La etiqueta del monitor se salía del panel de configuración.

## F4 — 2026-10-16
Export con FFmpeg (filter graph, presets, progreso/ETA, cancelar), transiciones `xfade`,
efectos (eq, volumen, fundidos, zoom) y corte rápido sin recodificar. Ver [docs/13](docs/13-f4-export.md).

## F3 — 2026-10-16
Editor: modelo de proyecto, timeline interactivo, undo/redo, autoguardado y preview a 30 fps.
Ver [docs/12](docs/12-f3-editor.md).

## F2, F1.1 y F2.1 — 2026-10-16
UI de grabación y Biblioteca con Aurora, cursor propio, compensación de deriva del audio,
región arrastrable y cuenta atrás. Ver [docs/10](docs/10-f2-ui.md) y [docs/11](docs/11-f11-f21-arreglos.md).

## F0 y F1 — 2026-10-16
Spikes medidos y motor de grabación (WGC/DXGI/mss → FFmpeg, WASAPI, mux a MP4, recuperación)
con CLI. Ver [docs/08](docs/08-f0-resultados.md) y [docs/09](docs/09-f1-motor-grabacion.md).
