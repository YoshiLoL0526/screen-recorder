# Rec Studio — grabación de pantalla + editor de video

> **Estado: F1 completada** — motor de grabación implementado y medido
> ([docs/09](docs/09-f1-motor-grabacion.md)), tras los spikes de F0
> ([docs/08](docs/08-f0-resultados.md)). Siguiente fase: **F2** (UI de grabación + biblioteca
> con aurora-ui), ver [roadmap](docs/06-roadmap.md).

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
├─ main.py            # punto de entrada de la app (UI en F2)
├─ docs/              # especificación y resultados (leer primero)
├─ src/recstudio/     # paquete: core/ · platform/ · services/  (motor de F1 funcionando)
├─ tools/
│  ├─ rec.py          # CLI de grabación (F1): --list, --region, --recover, --duration…
│  ├─ av_sync_test.py # prueba automática de sincronía A/V (beep + cursor)
│  ├─ soak_test.py    # soak de 10 min con pausa y medición de deriva
│  └─ spikes/         # scripts de F0 (mediciones originales)
└─ tests/             # pytest: config, comandos FFmpeg y mux real
```

## Puesta en marcha

Requiere Python ≥ 3.12 y [uv](https://docs.astral.sh/uv/):

```bash
uv sync                                   # entorno + dependencias
uv run pytest                             # tests (unit + integración FFmpeg)

# Grabador por CLI (motor de F1, sin UI todavía):
uv run python tools/rec.py --list         # monitores, audio y encoders disponibles
uv run python tools/rec.py --duration 30 -o salida/     # 30 s del monitor, con audio del sistema
uv run python tools/rec.py --recover      # recupera una grabación interrumpida

# UI (F2 en adelante):
uv run aurora dev main.py --inspector     # hot reload + inspector (F12)
uv run aurora build --icon assets/icon.ico --include assets   # ejecutable
```

## Licencia

Pendiente de definir (el proyecto usa FFmpeg GPL como proceso separado, lo que no afecta a la
licencia del código propio; ver la sección de empaquetado en [02](docs/02-arquitectura.md)).
