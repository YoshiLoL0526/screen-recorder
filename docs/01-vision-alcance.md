# 01 — Visión y alcance

## Resumen

**Rec Studio** (nombre de trabajo) es una aplicación de escritorio para Windows, escrita en Python,
que une las dos funciones que hoy obligan a usar dos programas:

1. **Grabar la pantalla** — como Bandicam: monitor, ventana o región; video + audio del sistema
   + micrófono; pausa y reanudación; calidad y FPS configurables.
2. **Editar el video** — dentro de la misma app: cortar, reordenar, ajustar velocidad, aplicar
   transiciones simples y efectos básicos, y exportar a MP4/H.264.

Toda la interfaz se construye con **aurora-ui**, la librería declarativa/reactiva con núcleo
nativo en Rust (Skia + Taffy) de este mismo autor. Esto convierte al proyecto en un *dogfooding*
exigente de Aurora: una app con canvas interactivo (timeline), reproducción de video, listas
virtuales y estado asíncrono continuo.

## Problema y propuesta de valor

| Problema actual | Propuesta |
|---|---|
| Grabar y editar exige 2+ programas (Bandicam + editor), con import/export manual. | **Una sola app**: al detener la grabación, el clip queda listo en la biblioteca y se abre en el editor con un clic. |
| Los editores "simples" (LosslessCut) solo cortan; los completos (Premiere/DaVinci) son enormes para un recorte. | Editor **deliberadamente pequeño**: cortar, reordenar, velocidad, transiciones y poco más, pero bien hecho. |
| Depender de WebView/Electron para la UI (pesado, doble tecnología). | Una sola tecnología: **Python + Aurora**, sin HTML/CSS/JS. |
| Herramientas que solo graban monitor completo. | Fuente = **monitor, ventana o región**, con selector visual. |

**Principio rector del alcance:** hacer poco, pero que grabar y cortar funcione perfecto y sin
sobresaltos (un corte que pierde 200 ms o un desincronizado de audio destruyen la confianza más
que la falta de una feature).

## Usuario y casos de uso

Usuario principal: el propio autor y perfiles técnicos ("grabar la demo de un script, la clase,
el reporte de un bug, un gameplay ligero y cortarlo en el momento").

Casos de uso (CU):

- **CU-1 Grabar una demo de pantalla completa** — iniciar, hablar al micrófono, pausar, seguir, detener.
- **CU-2 Grabar una ventana concreta** (con el cursor visible o no).
- **CU-3 Grabar una región fija** (p. ej. un rectángulo de un dashboard) a 60 fps.
- **CU-4 Grabar solo audio del sistema** (música de fondo de una reunión, sin video).
- **CU-5 Editar el resultado de CU-1**: recortar el inicio, eliminar un silencio, cerrar el corte con un fundido.
- **CU-6 Montar un video de 3 clips**: reordenar por arrastre, transición "wipe" entre cada par, export 1080p60.
- **CU-7 "Corte rápido" sin recodificar**: cortar una grabación larga sin perder calidad ni esperar (modo keyframe).
- **CU-8 Abrir un proyecto guardado días después** y seguir editando (medios referenciados; aviso si faltan).

## Alcance

### En v1 (lo que sí)

**Grabación**
- Fuente: monitor / ventana (Windows) / región seleccionada sobre una captura de referencia.
- FPS: 15 / 24 / 30 / 48 / 60 (objetivo; se documenta el real).
- Calidad: presets (Baja / Media / Alta / Insane) + override de bitrate.
- Encoder: automático con preferencia (NVENC → AMF → QSV → libx264).
- Audio: sistema (WASAPI loopback) y micrófono, activables por separado; pistas separadas opcionales.
- Cursor del ratón incluido (backend que lo soporte); toggle de clic visual **no** en v1.
- Pausa/reanudar; cuenta atrás de 3 s opcional; atajos in-app (F9 iniciar/detener, F10 pausa).
- Preview en vivo (decimado) + stats (fps real, frames duplicados/perdidos, tamaño, tiempo).
- Resiliencia: grabación a `.mkv` en caliente, remux a `.mp4` al detener; recuperación de grabaciones interrumpidas.

**Edición**
- Importar medios (diálogo nativo) y editar grabaciones de la biblioteca.
- Timeline de 1 pista de video + 1–2 pistas de audio, con waveform de las pistas de audio.
- Operaciones: mover, recortar por handles, dividir (split en playhead), eliminar, ripple delete,
  reordenar por arrastre, velocidad 0.25×–4×.
- Transiciones entre clips adyacentes (8): fundido (`fade`), fundido a negro/blanco, wipe
  izq/der/arr/abajo, `slideleft`, `circleopen`, `dissolve` — con duración configurable (0.2–2 s).
- Efectos básicos por clip: volumen/gain, fade in/out de audio, brillo/contraste/saturación, escala/zoom ligero.
- Preview con play/pause, scrub, paso ±1 frame; audio sincronizado (con modo silencio por defecto activable).
- Undo/redo (≥ 50 pasos), autosave cada 30 s, proyecto `.json` versionado.
- Export: presets Rápido (hardware) / Calidad (libx264) / Ligero (720p); progreso con ETA y cancelación.
- Export "sin recodificar" (keyframe-aligned) para cortes puros.

**App**
- Biblioteca de grabaciones con thumbnails, duración y tamaño; abrir/editar/borrar/revelar.
- Ajustes: carpeta destino, encoder preferido, tema claro/oscuro, atajos.
- Tema claro y oscuro (tokens Aurora), español (código e identificadores en inglés).
- Empaquetado con `aurora build` (ejecutable único).

### Fuera de v1 (no-objetivos)

- Multi-track complejo, PiP/webcam, chroma key, keyframes, máscaras, títulos animados.
- Subtítulos/captions automáticos, transcripciones.
- Streaming (RTMP) y grabación de juegos con overlays del sistema (hook de terceros).
- Efectos de audio avanzados (denoise, compresor) y crossfades de audio sofisticados.
- Soporte garantizado macOS/Linux (el diseño es portable; Windows es el objetivo de v1).
- Cloud, cuentas, colaboración, instalador MSI con auto-update.
- Multi-ventana y overlay transparente sobre el escritorio (limitación actual de Aurora, ver 05).

### Más adelante (post-v1, orden tentativo)

1. Hotkeys globales (con `pynput`) y bandeja del sistema.
2. Webcam como PiP + captura de cara separada.
3. Textos/títulos sobre el video (subtítulos quemados opcionales).
4. Pista de música dedicada + ducking básico (bajar música cuando hay voz).
5. Snap de audio y sincronización por waveform.
6. "GIF/WebM optimizado para compartir" en un clic.
7. Publicación directa a disco/yt-dlp.
8. Port a macOS (AVFoundation para audio, ScreenCaptureKit para video).

## Requisitos funcionales (RF)

| ID | Requisito | Prioridad |
|---|---|---|
| RF-01 | Iniciar/parar grabación desde la UI y con atajo in-app. | Must |
| RF-02 | Elegir monitor, ventana o región como fuente. | Must |
| RF-03 | Configurar fps, calidad y encoder antes de grabar. | Must |
| RF-04 | Capturar audio del sistema y/o micrófono; elegir dispositivo. | Must |
| RF-05 | Pausar y reanudar una grabación sin romper el archivo. | Must |
| RF-06 | Mostrar preview, tiempo, fps reales y tamaño en vivo. | Must |
| RF-07 | Al detener: archivo final reproducible, con audio sincronizado (< 80 ms de deriva). | Must |
| RF-08 | Biblioteca de grabaciones con metadatos y acciones básicas. | Must |
| RF-09 | Importar archivos externos al editor. | Must |
| RF-10 | Timeline con mover/trim/split/delete/ripple/reordenar. | Must |
| RF-11 | Transiciones simples entre clips adyacentes. | Must |
| RF-12 | Efectos básicos por clip (velocidad, gain, fades, eq, escala). | Should |
| RF-13 | Preview con play/pause/scrub/paso a paso. | Must |
| RF-14 | Undo/redo y autosave del proyecto. | Must |
| RF-15 | Export MP4 con presets, progreso, ETA y cancelación. | Must |
| RF-16 | Export sin recodificar (corte rápido). | Should |
| RF-17 | Recuperar grabaciones tras crash/cierre forzado. | Should |
| RF-18 | Tema claro/oscuro y tamaño de ventana adaptable (min 1024×680). | Should |
| RF-19 | Hotkeys globales con la app en segundo plano. | Could |
| RF-20 | Webcam PiP. | Could |

## Requisitos no funcionales (RNF)

| ID | Requisito | Criterio medible |
|---|---|---|
| RNF-01 | Grabación estable | 1080p60 durante 30 min con < 1 % de frames descartados sin duplicar en HW de referencia (Ryzen 5 / iGPU o RTX). |
| RNF-02 | Sincronía A/V | Deriva < 80 ms en 30 min; sin drift acumulativo audible. |
| RNF-03 | UI siempre fluida | `pilot.stats["total"]` < 8 ms y sin frames > 33 ms mientras se graba (la captura jamás bloquea el hilo de UI). |
| RNF-04 | Preview del editor | ≥ 24 fps de preview a 960×540 durante reproducción en el HW de referencia. |
| RNF-05 | Export predecible | 1 min de 1080p60 con 2 transiciones: < 40 s con NVENC, < 2 min con libx264 preset rápido. |
| RNF-06 | Arranque | Ventana lista en < 2 s (sin contar carga de proyecto). |
| RNF-07 | Memoria | < 600 MB en reposo; < 1.2 GB grabando 1080p60 (frames en tránsito incluidos). |
| RNF-08 | Recuperación | Tras un crash, la grabación en curso es reproducible (`.mkv`). |
| RNF-09 | Testeo sin pantalla | Todo el núcleo y las pantallas se pueden testear con `render_png()`/`pilot()` en CI. |
| RNF-10 | Portable en el diseño | `core/` y `services/` sin imports de Windows; el backend de captura es intercambiable. |

## Métricas de éxito de la v1

- Grabar → editar → exportar un video de 2 minutos **sin salir de la app** y sin leer ayuda.
- Cero issues bloqueantes de sincronía (audio) o de archivos corruptos en uso normal.
- El timeline se siente "de verdad" (drag preciso, snap útil, sin tirones) — es la feature
  que decide si el editor sirve o no.

## Referencias del mercado (qué tomamos y qué no)

| Producto | Lo que tomamos | Lo que no |
|---|---|---|
| Bandicam | Fuente/región, fps+calidad, hotkeys, preview con stats. | Su editor embebido pobre; licencia. |
| OBS | Presupuesto de rendimiento y resiliencia al grabar. | Complejidad de escenas/filtros/streaming. |
| LosslessCut | "Corte rápido" sin recodificar; snap a keyframes. | Solo corta; nada de transiciones. |
| Clipchamp/DaVinci (para clips cortos) | Formas de transiciones simples y presets de export. | Peso, curva de aprendizaje, cuentas cloud. |

## Éxito = dogfooding de Aurora

El proyecto también se juzga como **prueba de estrés de aurora-ui**: canvas interactivo a 60 fps,
imágenes en memoria a ritmo de video, mucho estado asíncrono y física de interacción realista.
Los hallazgos (API incómoda, límites del core) se documentarán y podrán convertirse en issues o
features de Aurora (`canvas.image()`, ventana secundaria, overlay…).
