# Rec Studio — grabación de pantalla + editor de video

> **Estado: F3 implementado** — además del grabador (F1/F2 con sus arreglos), la app ya tiene
> **editor**: timeline con cortes, recortes, reordenar, velocidad y transiciones, undo/redo,
> autoguardado y preview a 30 fps ([docs/12](docs/12-f3-editor.md)). Siguiente fase: **F4**
> (export y efectos), ver [roadmap](docs/06-roadmap.md).

Aplicación de escritorio para **grabar la pantalla** (como Bandicam: monitor, ventana o región,
con audio del sistema y micrófono) y **editar el resultado sin salir de la app** (cortes,
reordenar, velocidad y transiciones simples). Toda la interfaz está construida con
**[aurora-ui](https://yoshilol0526.github.io/aurora/)** — declarativa, reactiva y con núcleo
nativo en Rust (Skia + Taffy).

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ⏺ Grabar          demo .mp4            ✂ Editor            ⬇ Exportar    │
├───────────────────────────────────────────────┬──────────────────────────┤
│                PREVIEW                        │   INSPECTOR              │
├───────────────────────────────────────────────┴──────────────────────────┤
│  V ▐██████████▌ ╲fade╱ ▐███████▌        ▐████████████▌                  │
│  A   ⌇∿∿∿∿∿∿∿⌇            ⌇∿∿∿∿∿∿⌇                                    │
└──────────────────────────────────────────────────────────────────────────┘
```

## Stack

| Pieza | Tecnología |
|---|---|
| UI | **aurora-ui** (Python declarativo + núcleo Rust Skia/Taffy) |
| Captura | Windows Graphics Capture (`windows-capture`), Desktop Duplication (`dxcam`), `mss` |
| Audio | WASAPI loopback y micrófono con `PyAudioWPatch`; salida de preview con `sounddevice` |
| Decode/probe | PyAV (`av`) + NumPy + Pillow |
| Encode/render | **FFmpeg** (subprocess; binario del wheel `imageio-ffmpeg` o del sistema) |
| Empaquetado | `aurora build` (PyInstaller) |

## Documentación

Empieza por el [índice de documentación](docs/README.md). Los documentos clave:

- [01 — Visión y alcance](docs/01-vision-alcance.md): qué es, para quién, requisitos y no-objetivos.
- [02 — Arquitectura](docs/02-arquitectura.md): capas, módulos, hilos, dependencias y empaquetado.
- [03 — Grabación](docs/03-grabacion.md): backends de captura, pipeline FFmpeg y sincronía A/V.
- [04 — Editor](docs/04-editor.md): modelo de proyecto, timeline y motor de export (`xfade`).
- [05 — UI con Aurora](docs/05-ui-aurora.md): pantallas, wireframes, patrones y límites de Aurora 0.3.
- [06 — Roadmap](docs/06-roadmap.md): fases F0–F5, riesgos y criterios de aceptación.
- [07 — Decisiones (ADR)](docs/07-decisiones.md): por qué FFmpeg, PyAV, MKV→MP4, canvas híbrido, etc.
- [08 — F0: resultados](docs/08-f0-resultados.md): mediciones reales de captura, pipe FFmpeg, audio, preview y empaquetado.
- [12 — F3: editor](docs/12-f3-editor.md): lo implementado del editor, mediciones y aprendizajes.

## Estructura del repositorio

```
├─ main.py            # punto de entrada de la app (uv run python main.py)
├─ docs/              # especificación, resultados y capturas
├─ src/recstudio/     # paquete: core/ · platform/ · services/ · ui/ (F1 + F2 + F3)
├─ tools/
│  ├─ rec.py          # CLI de grabación (F1): --list, --region, --recover, --duration…
│  ├─ f2_ui_test.py   # prueba end-to-end de la UI (grabar/pausar/parar con Pilot)
│  ├─ f2_screenshots.py  # regenera las capturas de la documentación
│  ├─ f3_editor_test.py  # aceptación del editor (3 clips, drags+undo, fps del preview) + capturas
│  ├─ av_sync_test.py # prueba automática de sincronía A/V (beep + cursor)
│  ├─ soak_test.py    # soak con pausa y medición de deriva
│  ├─ recovery_test.py# recuperación de sesiones interrumpidas
│  └─ spikes/         # scripts de F0 (mediciones originales)
└─ tests/             # pytest: config, FFmpeg, mux real, settings, biblioteca, UI y editor
```

## Puesta en marcha

Requiere Python ≥ 3.12 y [uv](https://docs.astral.sh/uv/):

```bash
uv sync                                   # entorno + dependencias
uv run pytest                             # tests (unit + integración FFmpeg)

# La app: grabar con preview en vivo, F9/F10, biblioteca y editor (Ctrl+3)
uv run python main.py
uv run aurora dev main.py --inspector     # hot reload + inspector (F12)

# Grabador por CLI (sin UI / automatizable):
uv run python tools/rec.py --list
uv run python tools/rec.py --duration 30 -o salida/
uv run python tools/rec.py --recover      # recupera una grabación interrumpida

uv run aurora build --icon assets/icon.ico --include assets   # ejecutable (F5)
```

## Licencia

Pendiente de definir (el proyecto usa FFmpeg GPL como proceso separado, lo que no afecta a la
licencia del código propio; ver la sección de empaquetado en [02](docs/02-arquitectura.md)).
