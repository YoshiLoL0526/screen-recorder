# Rec Studio — grabación de pantalla + editor de video

> **Estado: F0 completada** (spikes medidos y decisiones validadas — ver
> [docs/08](docs/08-f0-resultados.md)). La especificación completa del producto está en `docs/`;
> la implementación empieza por la fase F1 del [roadmap](docs/06-roadmap.md).

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

## Estructura del repositorio

```
├─ main.py            # punto de entrada de la app
├─ docs/              # especificación (leer primero)
├─ assets/            # iconos y recursos   (se crea en F2)
├─ src/recstudio/     # código de la app    (se crea en F1)
└─ tests/             # tests (se crean en F1/F3)
```

## Puesta en marcha (desarrollo)

Requiere Python ≥ 3.12 y [uv](https://docs.astral.sh/uv/):

```bash
uv sync                                        # entorno + dependencias
uv run aurora dev main.py --inspector          # app con hot reload e inspector (F12)
uv run pytest                                  # tests
uv run aurora build --icon assets/icon.ico --include assets   # ejecutable distribuible
```

## Licencia

Pendiente de definir (el proyecto usa FFmpeg GPL como proceso separado, lo que no afecta a la
licencia del código propio; ver la sección de empaquetado en [02](docs/02-arquitectura.md)).
