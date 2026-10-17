# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/); versiones con
[SemVer](https://semver.org/lang/es/) desde la 1.0.0 (las fases anteriores no se publicaron como versión). El
detalle de cada fase está en `docs/08`–`docs/14`.

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
