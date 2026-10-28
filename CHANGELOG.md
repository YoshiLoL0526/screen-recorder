# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/); versiones con
[SemVer](https://semver.org/lang/es/) desde la 1.0.0 (las fases anteriores no se publicaron como versión). El
detalle de cada fase está en `docs/08`–`docs/16`.

## Sin publicar

### Documentación
- **[docs/15 — Roadmap post-v1](docs/15-post-v1.md)**: fuente de verdad de lo que falta, por bloques
  (entrega y distribución, editor, captura, compartir, plataforma y adopciones de Aurora), con estado,
  tamaño y criterio de aceptación de cada pieza, más una priorización recomendada. `docs/06` conserva
  las fases F0–F5 y apunta al 15 para el backlog.
- **[docs/16 — Bloque A: cierre de la entrega](docs/16-bloque-a-entrega.md)**: resultados de A1–A5
  (CI en verde, RNF-06/07, instalador, bundle ligero) y procedimiento de las comprobaciones manuales
  que quedan (A2, A6, A7, A8). `docs/15` actualiza estados y prioridades.

### Añadido
- **Bloque A de entrega** (docs/15 → docs/16):
  - **Instalador Inno Setup** (`installer/rec-studio.iss`): por usuario, sin UAC, en español, con
    accesos directos y desinstalador. `tools/check_installer.py` compila, instala en `%TEMP%`, hace
    el smoke del exe instalado y desinstala; el CI lo repite y sube `RecStudio-1.0.0-setup.exe`
    (71 MB).
  - **Bundle ligero**: `tools/build_exe.py` envuelve `aurora build` para excluir `cv2` (OpenCV,
    112 MB) y `platform/cv2_shim.py` cubre el único import de `windows-capture`
    (`Frame.save_as_image`). El bundle pasa de 370 a **253 MB**; `dxcam` avisa si se elige en el
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
- **CI en verde y más completo** (`40700123750`): 206 tests, build ligero + smoke del exe e
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

### Corregido
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
