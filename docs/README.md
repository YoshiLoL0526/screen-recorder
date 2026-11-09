# Documentación — Rec Studio (working title)

Documentación de diseño de una app de escritorio para **grabar la pantalla** y **editar video**
(recortes y transiciones simples), con toda la interfaz construida sobre
[**aurora-ui**](https://yoshilol0526.github.io/aurora/) (`aurora-ui>=0.10.0,<0.11.0`).

> Estado: **v1.2.0 implementada** (F0–F5 + features v6), **bloque A de entrega cerrado** (CI, RNF, instalador
> y bundle ligero), **bloque B del editor** (música/ducking, textos, selección múltiple, snap por
> waveform, proxy y reubicar carpetas) y **fases 0/1/2/3 del editor** (audio absoluto editable,
> lienzo configurable, transformación del frame y flujo de edición), **panel de medios/capas con
> selección directa**, **PiP de video**, **pistas V2/V3**, **subtítulos**, **keyframes**,
> **envolventes de volumen**, **escala/rotación de elementos**, **envolventes de audio de
> elementos**, **escala/rotación de textos**, **color/tamaño de textos**, **edición de claves
> de envolvente en el timeline**, **asas de animaciones visuales**, **easing de las claves** y
> **easing de las envolventes de volumen**, **copiar/pegar de claves**, **añadir claves desde
> el timeline** y **recorte interactivo en el preview** (docs/27-43). Quedan las comprobaciones
> manuales de A y la fase 4+. Los documentos 01-07 son la especificación de diseño; 08-43 recogen
> lo implementado, con mediciones y bugs.

## Índice

| # | Documento | Qué responde |
|---|---|---|
| 01 | [Visión y alcance](01-vision-alcance.md) | Qué construimos, para quién, qué entra en v1 y qué no. Requisitos funcionales y no funcionales. |
| 02 | [Arquitectura](02-arquitectura.md) | Capas, módulos, hilos, flujos de datos, persistencia, dependencias, empaquetado y tests. |
| 03 | [Grabación](03-grabacion.md) | Motor de captura: backends (WGC/DXGI/mss), pipeline a FFmpeg, audio WASAPI, pausa, sincronía A/V, resiliencia. |
| 04 | [Editor](04-editor.md) | Modelo de proyecto, matemática del timeline, preview, waveforms, motor de export con filtros FFmpeg y xfade. |
| 05 | [UI con Aurora](05-ui-aurora.md) | Sistema de diseño, pantallas, componentes Aurora concretos, timeline interactivo, atajos y animaciones. |
| 06 | [Roadmap](06-roadmap.md) | Fases, entregables, criterios de aceptación, riesgos y mitigaciones. |
| 07 | [Decisiones (ADR)](07-decisiones.md) | Las decisiones técnicas con sus alternativas descartadas y consecuencias. |
| 08 | [F0: resultados](08-f0-resultados.md) | Mediciones de los spikes (captura, pipe FFmpeg, audio, preview, drag/scroll, empaquetado) y decisiones ajustadas. |
| 09 | [F1: motor de grabación](09-f1-motor-grabacion.md) | Implementación del grabador (paquete `recstudio` + CLI), bugs encontrados, mediciones y criterios de F1. |
| 10 | [F2: UI de grabación + Biblioteca](10-f2-ui.md) | Pantalla de Grabar y Biblioteca en Aurora, capturas, prueba end-to-end con Pilot y aprendizajes de layout. |
| 11 | [F1.1 + F2.1: arreglos](11-f11-f21-arreglos.md) | Cursor propio (sin la latencia de WGC), compensación de deriva del reloj de audio, región arrastrable y cuenta atrás. |
| 12 | [F3: editor](12-f3-editor.md) | Modelo de proyecto, timeline interactivo, undo/autosave, preview a 30 fps, mediciones y bugs de Aurora encontrados. |
| 13 | [F4: export](13-f4-export.md) | Filter graph (golden), export con progreso/ETA/cancelar, presets, corte rápido byte a byte, efectos y mediciones. |
| 14 | [F5: pulido y v1.0](14-f5-pulido.md) | Layout adaptable, divisores del editor, Ajustes completos, icono, `aurora build` + smoke del exe y bugs de layout. |
| 15 | [Roadmap post-v1](15-post-v1.md) | **Lo que falta por implementar**: entrega (CI, instalador, RNF), editor (música, títulos), captura (webcam), compartir, plataforma y adopciones de Aurora. |
| 16 | [Bloque A: cierre de la entrega](16-bloque-a-entrega.md) | CI en verde, RNF-06/07 con ventana, instalador Inno Setup, bundle sin OpenCV y las comprobaciones manuales que quedan (máquina limpia, móvil, soak y ratón). |
| 17 | [Bloque B: editor completo](17-bloque-b-editor.md) | Música con ducking/crossfades, textos en preview/export, selección múltiple, snap por waveform, proxy 540p y reubicar carpetas. |
| 18 | [Herramientas que faltan en el editor](18-herramientas-editor.md) | Análisis y hoja de ruta post-bloque B: audio libre, lienzo/transformación, flujo, composición y export. |
| 19 | [Fase 0 y 1: audio editable](19-fase-0-1-audio.md) | Modelo v3, pistas absolutas, separar/adjuntar/dividir/editar audio, pistas múltiples, fundidos, export/preview y límites. |
| 20 | [Fase 2: lienzo y frame](20-fase-2-frame.md) | Modelo v4, presets de lienzo, fit/fill/stretch, transformación por clip, auto-rotación, paridad preview/export, UI, tests y límites. |
| 21 | [Fase 3: flujo de edición](21-fase-3-flujo.md) | Modelo v5 (marcadores e In/Out), portapapeles, marquesina que mueve, razor, loop, J/K/L, Q/W, export de rango, miniaturas rotadas, tests y límites. |
| 22 | [Plan de features v6](22-plan-features-editor.md) | Canvas, pistas A/V enlazadas, elementos superpuestos y efectos globales: contrato y reparto de archivos. |
| 23 | [Canvas interactivo](23-canvas-resize.md) | Mover/redimensionar el clip en el preview, asas, ancla opuesta y undo por gesto. |
| 24 | [Pistas A/V](24-pistas-av.md) | Audio del video separado y enlazado por defecto, sincronía y desvincular/vincular. |
| 25 | [Elementos superpuestos](25-elementos.md) | Figuras/imágenes, geometría, PNG de figuras, blur y composición preview/export. |
| 26 | [Efectos globales](26-efectos-globales.md) | Color, blur, viñeta y B/N sobre el video compuesto, con paridad preview/export. |
| 27 | [Panel de medios, selección y capas](27-panel-medios-y-capas.md) | Medios del proyecto, selección directa de clip/elemento/texto, capas en el timeline, inspector numérico y color. |
| 28 | [PiP de video](28-pip-video.md) | Video superpuesto (`Element.kind="video"`), modelo v7, composición preview/export, audio mezclado con volumen propio, UI y límites. |
| 29 | [Pistas de video V2/V3](29-pistas-video.md) | `Element.video_track`, z-order por capas, composición V1→V2→V3→efectos→Elementos, filas del timeline, audio y límites (modelo v8). |
| 30 | [Subtítulos SRT/VTT](30-subtitulos.md) | Parser, `TextOverlay.subtitle`, importación, estilo global y exportación SRT (modelo v9). |
| 31 | [Keyframes](31-keyframes.md) | `Animation`/`Keyframe`, X/Y de elementos/textos, opacidad de textos, expresiones de preview/export y UI (modelo v9). |
| 32 | [Envolventes de volumen](32-audio-envelopes.md) | `Clip.envelope`, claves de ganancia locales, `volume` por frame en el export, mezcla WAV del preview y UI (modelo v10). |
| 33 | [Escala y rotación animadas](33-escala-rotacion.md) | Canales `scale`/`rotation` de elementos, vídeo RGBA intermedio con PIL/qtrle, paridad preview/export, UI y límites (modelo v9). |
| 34 | [Envolventes de audio de elementos](34-envolventes-elementos.md) | `Element.envelope` para PiP y V2/V3, `volume` por frame en el export, mezcla WAV del preview y UI (modelo v11). |
| 35 | [Textos con escala y rotación](35-textos-escala-rotacion.md) | Canales `scale`/`rotation` de textos/subtítulos, rasterizado compartido, bake RGBA y orden de `drawtext`/overlays (modelo v10). |
| 36 | [Color y tamaño de textos](36-color-tamano-textos.md) | Canal `size` y claves de color de textos/subtítulos, interpolación sRGB, bake RGBA, UI y límites (modelo v12). |
| 37 | [Claves de envolvente en el timeline](37-editor-claves.md) | Asas sobre la waveform/barra, modo Env para añadir, Supr/menú para borrar, geometría pura, undo por gesto y capa sobre el playhead (modelo v11 sin cambios). |
| 38 | [Asas de animaciones en el timeline](38-asas-animaciones.md) | Canal elegido en el inspector, polilínea y asas sobre la barra de elementos/textos, geometría de los cuatro rangos, arrastre con undo y límites (modelo v12 sin cambios). |
| 39 | [Easing de las claves](39-easing.md) | Curvas `linear`/`ease_in`/`ease_out`/`ease_in_out` por clave, expresiones FFmpeg, migración v12 → v13, selector del inspector y paridad preview/export. |
| 40 | [Easing de las envolventes de volumen](40-easing-audio.md) | `VolumePoint.easing` en clips y elementos, curvas compartidas con `Animation`, expresiones FFmpeg, migración v13 → v14, selector del inspector y audio real. |
| 41 | [Copiar y pegar claves](41-copiar-pegar-claves.md) | Portapapeles de claves visuales (valor/easing) y puntos de envolvente, pegado en el canal activo y el playhead, menús del asa, no-op con aviso y undo. |
| 42 | [Añadir claves desde el timeline](42-claves-desde-timeline.md) | Modo `N`, clic sobre la barra del elemento/texto, valor base/interpolado, easing del inspector, undo por clic, indicadores y límites (modelo v14 sin cambios). |
| 43 | [Recorte interactivo en el preview](43-crop-interactivo.md) | Modo `T`, preview sin recortar, velo y 8 asas, geometría con rotación/flip, mínimo del 5 %, undo por gesto y límites (modelo v14 sin cambios). |

## Orden de lectura sugerido

1. **01** para entender el producto y fijar expectativas.
2. **02** para el mapa del código (qué módulo hace qué).
3. **03** y **04** son los dos motores; se pueden leer por separado.
4. **05** es la guía de la UI (wireframes y patrones Aurora).
5. **06** y **07** son operativas: cómo avanzamos y por qué se eligió cada cosa.

## Glosario

| Término | Significado |
|---|---|
| **Fuente** | Qué se graba: un monitor, una ventana o una región rectangular. |
| **Captura** | Obtención de fotogramas crudos (BGRA/RGB) del escritorio, sin codificar. |
| **Encoder** | Quien comprime la captura a H.264/H.265 (NVENC, AMF, QSV o libx264). |
| **Asset** | Un archivo de medios importado (una grabación, un video, un audio). |
| **Clip** | Un tramo de un asset colocado en el timeline, con `in`/`out`, velocidad y efectos. |
| **Transición** | Efecto de solape entre dos clips adyacentes (`xfade`: fundido, wipe…). |
| **Timeline** | La línea de tiempo del editor: pistas, clips, playhead y regla. |
| **Playhead** | El cursor de tiempo del editor. |
| **Render/Export** | Producir el archivo final con FFmpeg a partir del modelo del proyecto. |
| **WGC / DXGI** | Windows.Graphics.Capture / Desktop Duplication: las dos APIs de captura de Windows. |
| **xfade** | Filtro de FFmpeg para transiciones entre dos videos. |
| **aurora-ui** | La librería de UI (declarativa, reactiva, núcleo Rust Skia+Taffy) con la que se construye todo. |
