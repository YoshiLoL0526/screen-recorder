# AGENTS.md

Rec Studio: grabador de pantalla + editor de video para Windows. UI con **aurora-ui** 0.5
(núcleo Rust Skia/Taffy), captura WGC/DXGI/mss, audio WASAPI (PyAudioWPatch) y encode por
**FFmpeg** en subprocess. Todo el proyecto —docs, comentarios, UI, tests y commits— está en
**español**; los commits usan prefijos `feat:` / `docs:`.

Estado: **v1.0.0** — F1 (motor), F2 (UI de grabación + Biblioteca), F1.1/F2.1, **F3 (editor)**,
**F4 (export y efectos)** y **F5 (pulido)** implementados. Editor: `core/project.py`, `core/timeline.py`,
`core/store.py`, `services/media.py`/`playback.py`, `ui/editor_controller.py`, `ui/timeline.py`,
`ui/screens/editor.py` (ver `docs/12`). Export: `core/effects.py`, `core/graph.py` (builder puro
del filter graph), `core/keyframes.py` (corte sin recodificar), `services/export.py`,
`ui/export_modal.py` (ver `docs/13`). Pulido: layout adaptable, divisores del editor, Ajustes,
icono, `[tool.aurora.build]` y smoke del exe en `src/recstudio/smoke.py` (ver `docs/14`). Post-v1:
bandeja del sistema y atajos globales (`ui/tray.py`, `platform/hotkeys.py`, ver `docs/05`);
lo siguiente es el resto del backlog de `docs/06`.

## Comandos

```bash
uv sync                                         # entorno (Python 3.12, ver .python-version)
uv run pytest                                   # 192 tests; la integración usa FFmpeg real
uv run pytest tests/test_ffmpeg_cmd.py -k mux   # un test concreto
uv run python main.py                           # la app
uv run aurora dev main.py --inspector           # hot reload + inspector (F12)
uv run python tools/rec.py --list               # CLI de grabación (--probe-encoders, --recover…)
uv run python tools/f2_ui_test.py               # e2e de la UI (app real con App.pilot, sin ventana)
uv run python tools/f3_editor_test.py           # aceptación del editor (+ capturas en docs/assets/f3)
uv run python tools/f4_export_test.py           # aceptación del export (NVENC, sync, byte a byte; --skip-long)
uv run python tools/f5_screenshots.py           # capturas de F5 (docs/assets/f5, contenido sintético)
uv run python tools/check_build.py              # aurora build + smoke del exe (--skip-build: solo el smoke)
uv run python tools/make_icon.py                # regenera assets/icon.ico
```

No hay linter, formatter ni typechecker configurado; no inventes uno. `uv run aurora build` usa
`[tool.aurora.build]` (onedir, sin consola) → `dist/Rec Studio/`. CI: `.github/workflows/ci.yml`
(windows-latest: `uv sync --locked` + `pytest`, y luego `tools/check_build.py` con el exe como
artefacto); los tests no pueden depender de monitor, GPU ni audio reales del runner.

## Arquitectura (reglas duras)

- Dirección de dependencias única: `ui → services → core` y `services → platform → core`.
  `core/` es puro (sin aurora, sin PyAV, sin SO) y se testea sin ventana; `platform/` encapsula
  Windows (captura, WASAPI, FFmpeg).
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
- Aurora 0.5: `scroll()` añade `grow=1` (pasa `grow=0` en filas); columna que debe encoger:
  `grow=1, width=0`; los `select` no truncan etiquetas. Más límites y mitigaciones en `docs/05`
  («Limitaciones de Aurora 0.5»).
- Aurora 0.5 (F3): cualquier caja encima bloquea los clics aunque no tenga handlers (playhead,
  toasts); la raíz recibe las teclas aunque haya un campo enfocado (usa `mark_input_key`);
  `KeyEvent.matches("+")` lanza excepción; `on_drag` llega también con un clic (fase `start`,
  x de ventana → `TL_X0`). En Windows, `Condition.wait` va a saltos de ~15.6 ms: para relojes
  usa `time.sleep`.
- Export (`docs/13`): cada clip es **una entrada** (`-ss IN -t LEN -i`) y dura un número entero
  de frames (`tpad` + `trim=end_frame`; el audio, `apad`+`atrim` a lo mismo): no cambies eso sin
  revisar los golden de `tests/test_f4_graph.py`. El concat del corte rápido necesita
  `-auto_convert 0` o los keyframes dejan de ser idénticos. Preview y graph comparten las
  curvas de `core/effects.py` (lo exportado = lo visto).
- Aurora 0.5: una fila `grow=1` cuyo hijo puede ser más alto que el hueco (inspector) necesita
  `height=0`; los handlers suscritos a `Signal` globales deben ignorar eventos tras `shutdown`
  (los tests crean varias apps por proceso y `subscribe` no se deshace).
- Editor: el canvas del timeline usa una función de dibujo de módulo (misma identidad ⇒ Aurora
  no redibuja si no cambian sus `Signal`); el playhead va en su propio `@component`. Proyectos en
  `<grabaciones>/Proyectos/*.rsproj/project.json`.
- Layout (F5): todo contenedor `grow=1` cuyo contenido puede no caber (imágenes, pantallas
  largas) lleva `height=0` (columna) o `width=0` (fila); si no, crece y empuja controles fuera
  de la ventana. El alto útil de la ventana es `state.view_h` (lo da el `on_scroll` de la
  navegación); la geometría de pistas sale de `ui/timeline.tracks()`, no de constantes.
- `settings.json` se escribe atómico (tmp + `os.replace`); los campos nuevos de `Settings` se
  validan en `normalized()`. `platform.ffmpeg.run` usa `stdin=DEVNULL` (exe sin consola).
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
  de UI). `CHANGELOG.md` resume cada versión.
- `tools/spikes/` son prototipos de F0 ya cerrados; no los toques para trabajo nuevo.
  `build/`, `dist/`, `.venv`, `__pycache__` y `tools/spikes/out/` están ignorados: no son fuente.
