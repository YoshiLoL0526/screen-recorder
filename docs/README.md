# Documentación — Rec Studio (working title)

Documentación de diseño de una app de escritorio para **grabar la pantalla** y **editar video**
(recortes y transiciones simples), con toda la interfaz construida sobre
[**aurora-ui**](https://yoshilol0526.github.io/aurora/) (`aurora-ui>=0.3.0`).

> Estado: **diseño**. Aún no hay implementación; estos documentos son la especificación
> que guía el desarrollo.

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
