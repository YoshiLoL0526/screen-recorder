# AGENTS.md

Rec Studio: grabador de pantalla + editor de video para Windows. UI con **aurora-ui** 0.3
(núcleo Rust Skia/Taffy), captura WGC/DXGI/mss, audio WASAPI (PyAudioWPatch) y encode por
**FFmpeg** en subprocess. Todo el proyecto —docs, comentarios, UI, tests y commits— está en
**español**; los commits usan prefijos `feat:` / `docs:`.

Estado: F1 (motor), F2 (UI de grabación + Biblioteca) y F1.1/F2.1 implementados. La siguiente
fase es **F3 (editor)**: `core/project.py`, `core/timeline.py`, `core/store.py`,
`services/media.py`/`playback.py` y la pantalla Editor aún **no existen**; su diseño está en
`docs/04-editor.md`.

## Comandos

```bash
uv sync                                         # entorno (Python 3.12, ver .python-version)
uv run pytest                                   # 47 tests; la integración usa FFmpeg real
uv run pytest tests/test_ffmpeg_cmd.py -k mux   # un test concreto
uv run python main.py                           # la app
uv run aurora dev main.py --inspector           # hot reload + inspector (F12)
uv run python tools/rec.py --list               # CLI de grabación (--probe-encoders, --recover…)
uv run python tools/f2_ui_test.py               # e2e de la UI (app real con App.pilot, sin ventana)
```

No hay linter, formatter ni typechecker configurado; no inventes uno. `uv run aurora build`
(F5) referencia `assets/icon.ico`, que todavía no existe: el empaquetado no está montado.

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
- Aurora 0.3: `scroll()` añade `grow=1` (pasa `grow=0` en filas); columna que debe encoger:
  `grow=1, width=0`; los `select` no truncan etiquetas. Más límites y mitigaciones en `docs/05`
  («Limitaciones actuales de Aurora 0.3»).
- Tests de UI: construyen la app real sin ventana con `build_app()` + `app.pilot(size=…)`; los
  que capturan pantalla hacen `pytest.skip` si no hay escritorio disponible. Los scripts de
  `tools/` (soak, av_sync, recovery, f2_ui) son pruebas de aceptación manual y algunos necesitan
  escritorio y audio reales; no forman parte de `pytest`.

## Documentación (fuente de verdad)

- `docs/02-arquitectura.md`: capas, hilos y flujos. `docs/06-roadmap.md`: estado por fases
  (F0–F5). `docs/07-decisiones.md`: ADRs (por qué FFmpeg, PyAV, MKV→MP4, canvas híbrido…).
- `docs/09`/`10`/`11`: resultados de F1, F2 y F1.1+F2.1 (bugs, mediciones, aprendizajes de UI).
- La cabecera de `docs/README.md` («Estado: diseño, sin implementación») está **obsoleta**; el
  estado real es el del README raíz y `docs/09`–`11`.
- `tools/spikes/` son prototipos de F0 ya cerrados; no los toques para trabajo nuevo.
  `build/`, `dist/`, `.venv`, `__pycache__` y `tools/spikes/out/` están ignorados: no son fuente.
