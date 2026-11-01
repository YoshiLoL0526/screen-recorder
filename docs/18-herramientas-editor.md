# 18 — Herramientas que faltan en el editor

> Análisis a **2026-10-31**, sobre la v1.0.0 + bloques A y B. Las **fases 0, 1, 2 y 3 ya están
> implementadas** (modelo v3 con audio absoluto, audio editable, modelo v4 con lienzo/
> transformación y modelo v5 con el flujo de edición; resultados en
> [19 — Fase 0 y 1](19-fase-0-1-audio.md), [20 — Fase 2](20-fase-2-frame.md) y
> [21 — Fase 3](21-fase-3-flujo.md)). El resto del documento conserva el análisis y la
> hoja de ruta propuesta.
>
> Complementa [04 — Editor](04-editor.md), [12 — F3](12-f3-editor.md), [13 — F4](13-f4-export.md),
> [17 — Bloque B](17-bloque-b-editor.md) y el backlog de [15](15-post-v1.md). Cuando una fase se
> implemente, sus criterios y resultados deben acabar en un `docs/` de fase, no aquí.

## Resumen ejecutivo

El editor actual es un **NLE mínimo**: una pista de video magnética, el audio del video enlazado a
sus clips y una pista de música secuencial. Eso cubre bien «cortar, reordenar, velocidad,
transiciones y textos», pero deja fuera casi todo lo que se espera de un editor de video moderno.

Las dos peticiones concretas —**picar/separar el audio** y **redimensionar el frame**— no son dos
botones sueltos: tocan el modelo de datos, el timeline, el preview, el export y la persistencia.
El diagnóstico corto es:

1. **No existe una pista de audio libre**: el audio del video está pegado al clip y la música es
   una secuencia sin posición absoluta. Sin eso no se puede separar, mover ni cortar audio de
   forma independiente.
2. **El frame se decide al crear el proyecto** y el render siempre escala «fit» con barras negras.
   No hay lienzo configurable, ni «fill», ni transformación libre del clip.
3. **Faltan herramientas de flujo**: copiar/pegar/duplicar, razor, marquesina que mueva, marcadores,
   loop, rango de export.
4. **Faltan herramientas de composición**: PiP/overlay, keyframes, subtítulos, imágenes, color.
5. **El export está cerrado a cuatro presets**: no hay resolución/bitrate personalizados, rango,
   audio solo, GIF/WebM.

La recomendación es hacerlo en fases, empezando por una **fundación de modelo** (posición absoluta
y pistas múltiples), luego **audio editable**, luego **lienzo/transformación**, y después flujo,
composición y export. No conviene empezar por la UI de un botón: sin el modelo, el botón no tiene
dónde guardar el resultado.

## 1. Qué hay hoy (verificado en el código)

| Área | Estado real |
|---|---|
| Pista de video | **Una**, magnética y sin huecos (`Project.video`). |
| Audio del video | Va **enlazado** al clip: se dibuja como banda A y se puede **separar** a una pista absoluta enlazada (`audio_enabled=False` en el video). El volumen es global de la pista o por clip; `fade_in/out` afectan al video y `audio_fade_in/out` solo al audio. |
| Pistas de audio extra | `Project.audio` admite varias y **todas** se dibujan y editan (mutear, solo, bloquear, volumen, mover/recortar/dividir/borrar). La importación de música va a la primera pista secuencial. |
| Clips de música | Secuenciales desde 0; se pueden seleccionar, mover (reordenar), recortar, dividir, borrar y ajustar volumen/ducking/crossfade. |
| Timeline | Mover/reordenar, trim por asas, split en playhead (todo o solo audio), ripple delete, velocidad, transiciones, snap, waveform, filmstrip, marquesina (solo borrado) y drags de audio. |
| Frame | `Project.width/height` se fija al crear el proyecto con el primer asset; el inspector permite cambiar lienzo, fondo, encaje y FPS. |
| Render | Cada clip se normaliza con `fit`/`fill`/`stretch`; el fondo sale de `Project.background` y la transformación de `Clip.transform`. El preview usa la misma geometría. |
| Transformación | `Transform` por clip: fit/fill/stretch, escala, posición, rotación, flip y crop (docs/20). El efecto `zoom` histórico se conserva. |
| Rotación | `MediaAsset.rotation` se lee en `probe`; preview y export enderezan la imagen y `keyframes.blockers` lo detecta. |
| Textos | `TextOverlay` de una línea, Arial fija, posición por selects del inspector; no se arrastra sobre el preview. No hay subtítulos. |
| Efectos | Estáticos por clip: eq, volume, fade in/out, zoom. No hay keyframes, curvas, LUT, temperatura, denoise… |
| Multi-selección | Marquesina + `Supr` con un undo; **no** se mueve en lote (docs/17). |
| Portapapeles | No hay copiar/pegar/duplicar. |
| Marcadores / loop | No hay marcadores, ni loop, ni puntos in/out. docs/04 menciona «loop de selección», pero el código no lo implementa. |
| Preview | Play/pausa, scrub, ±1 frame, audio best-effort, transiciones aproximadas, textos y proxy 540p. No hay loop, medidores, scopes, zoom/pan ni segunda ventana. |
| Proyecto | `.rsproj/project.json` schema 4, autosave, undo/redo, reubicar offline y ajustes de lienzo/fondo/encaje. No hay «nuevo», «guardar como» ni renombrar. |
| Export | Presets Rápido/Calidad/Ligero 720p/Sin recodificar. No hay resolución/bitrate personalizados, rango, audio solo ni GIF/WebM. |
| Medios | Assets implícitos en el proyecto; no hay panel de medios, imágenes ni reemplazo de un asset online. |

## 2. Diagnóstico: las limitaciones estructurales

### 2.1 Una pista de video y audio «pegado»

`Project.video` es la única pista que compone imagen. El audio del video no es un `Track`: sale de
los mismos clips de video. Por eso:

- no se puede separar el audio y dejarlo en el timeline;
- no se puede cortar solo el audio;
- no se puede silenciar un clip de video y conservar su audio (solo bajar el volumen);
- no se puede tener una segunda pista de voz/música sincronizada de forma independiente.

`Project.audio` sí existe, pero es una secuencia: `track_duration` y `clip_starts` derivan las
posiciones del orden de los clips. No hay `start` absoluto ni huecos. Eso impide representar «este
audio empieza en 0:07.3» sin inventar clips de silencio, y bloquea mover/recortar música con libertad.

> **Resuelto en la fase 1:** las pistas de audio pueden ser `absolute` (con `Clip.start`) o
> seguir `sequence`, y el audio del video se puede separar a una pista absoluta enlazada. Ver
> [19](19-fase-0-1-audio.md).

### 2.2 El frame se decide al crear el proyecto

`Project.width/height` se copia del primer asset en `EditorController.import_paths`. No hay una
operación para cambiarlo. El graph siempre hace «fit» y rellena con negro; el preview también.
No existe un modelo de transformación por clip más allá del zoom centrado. Por eso:

- no se puede cambiar a 9:16 para redes sin crear otro proyecto;
- no se puede «llenar» el frame sin barras negras;
- no se puede colocar un clip más pequeño, moverlo, rotarlo o voltearlo;
- un video de móvil con rotación 90/270 no se endereza.

> **Resuelto en la fase 2:** el lienzo es configurable (`Project.width/height`, `background`,
> `default_fit`) y cada clip puede llevar un `Transform` (fit/fill/stretch, escala, posición,
> rotación, flip y crop). La auto-rotación se lee en `media.probe`. Ver
> [20 — Fase 2](20-fase-2-frame.md).

### 2.3 Efectos estáticos y textos rígidos

`Effect` es una tupla de parámetros fijos por clip. No hay keyframes ni curvas. `TextOverlay` no
tiene fuente, interlineado, caja de fondo, alineación ni animación. Esto limita títulos, subtítulos
y cualquier movimiento (Ken Burns, zoom animado, fades parciales).

### 2.4 Flujo de edición incompleto

El timeline es bueno para el caso «cortar y reordenar», pero no para trabajar rápido:

- no hay portapapeles ni duplicar;
- la marquesina solo borra;
- no hay razor como herramienta (solo `S` en el playhead);
- no hay marcadores, loop, in/out ni export de un rango;
- no hay panel de medios ni drag & drop desde el Explorador.

### 2.5 Export y preview comparten efectos, pero no todas las decisiones

La regla del proyecto es que **lo exportado sea lo visto**. Hoy se cumple para zoom, eq, fades,
transiciones y textos. Cualquier herramienta nueva (transformación, rotación, fondo, audio libre)
tiene que implementarse en **dos sitios** (`core/graph.py` y `services/playback.py`) y compartir la
matemática en `core/effects.py` o en un módulo puro nuevo. Si no, el preview miente.

### 2.6 El «corte rápido» hay que actualizarlo con cada efecto

`core/keyframes.blockers` bloquea el corte sin recodificar con transiciones, efectos, textos,
pistas extra o formatos mezclados. Transformación, rotación, fondo o audio separado también deben
bloquearlo (o el resultado no sería el mismo). Es fácil olvidarlo y exportar un archivo incorrecto.

## 3. Catálogo de funcionalidades que faltan

Estados: ✅ hecho · ◐ parcial · ⛔ falta. Prioridad: **P0** = bloquea lo pedido o es fundación ·
**P1** = siguiente · **P2** = después · **P3** = fuera de alcance por ahora. Tamaño orientativo:
S ≤ 1 día, M 2–4 días, L 1–2 semanas, XL más (a tiempo parcial y con IA).

### 3.1 Timeline y flujo de edición

| # | Función | Estado | Qué falta / dónde | Prio | Tam |
|---|---|---|---|---|---|
| A1 | Cortar/dividir el clip de video en el playhead | ✅ | `core/timeline.split`, `S` | — | — |
| A2 | Cortar **todas** las pistas o solo el audio en el playhead | ✅ | `split_all`/`split_audio`; `S` y `Mayús+S` (docs/19) | — | — |
| A3 | Varias pistas de audio visibles y editables | ✅ | `ui/timeline.py` dibuja todas; cabeceras y controles (docs/19) | — | — |
| A4 | Posición absoluta y huecos en pistas de audio/overlay | ✅ | `Clip.start` + `Track.mode` (docs/19) | — | — |
| A5 | Cabeceras de pista con mute/solo/bloqueo/volumen | ✅ | Cabeceras del timeline y sección de pistas del inspector | — | — |
| A6 | Editar clips de música: mover, recortar, dividir, borrar | ✅ | Drags de cuerpo/asas, `trim_audio`, `remove_audio` | — | — |
| A7 | Copiar/pegar/duplicar clips | ✅ | `Ctrl+C/X/V/D` con video, audio y textos; `tl.copy_items/paste/duplicate` (docs/21) | — | — |
| A8 | Mover una selección múltiple en bloque | ✅ | Arrastrar un clip de la selección mueve el bloque (`tl.move_block`, docs/21) | — | — |
| A9 | Ripple trim, roll, slip, slide, insert/overwrite | ⛔ | Solo trim simple y ripple delete | P2 | L |
| A10 | Marcadores y capítulos | ✅ (marcadores; capítulos en el MP4, no) | `Project.markers`, `B`, ↑/↓, regla e inspector (docs/21) | P2 | S |
| A11 | Loop e in/out de reproducción | ✅ | `I`/`O`, `Ctrl+L` y `Player.set_loop` (docs/21) | — | — |
| A12 | Scroll vertical cuando hay muchas pistas | ✅ (alternativa) | El panel crece con las pistas y se limita a 6 nuevas; el scroll anidado de Aurora 0.9 no se pudo usar (docs/19) | — | — |
| A13 | Drag & drop desde el Explorador | ⛔ | `on_drop` de Aurora disponible (docs/05) | P1 | S |
| A14 | Panel de medios del proyecto | ⛔ | Los assets solo se ven en el inspector de cada clip | P2 | M |
| A15 | Menú contextual (clic derecho) | ⛔ | `on_context_menu` de Aurora disponible, sin usar | P2 | S–M |
| A16 | Herramienta razor visible (cortar donde se pulsa) | ✅ | Botón y `C`; por pista o en todas (docs/21) | — | — |
| A17 | Zoom que mantenga el playhead/selección a la vista | ◐ | Aurora 0.9 no permite fijar el scroll horizontal (spike en docs/21) | P1 | S |

### 3.2 Audio

| # | Función | Estado | Qué falta / dónde | Prio | Tam |
|---|---|---|---|---|---|
| B1 | **Separar el audio del video** (detach) | ✅ | `Clip.audio_enabled` + clip en pista absoluta + enlace; graph/preview (docs/19) | — | — |
| B2 | Dividir el audio de forma independiente | ✅ | `split_audio`; `Mayús+S` (docs/19) | — | — |
| B3 | Mover/recortar/borrar clips de audio | ✅ | Drags de audio en pistas secuenciales y absolutas (docs/19) | — | — |
| B4 | Fundidos de audio independientes del video | ✅ | `audio_fade_in`/`audio_fade_out`; fallback a los fundidos históricos | — | — |
| B5 | Asas de fundido sobre la waveform | ⛔ | No hay UI de asas de audio (los fundidos se ajustan en el inspector) | P1 | M |
| B6 | Envolvente de volumen / keyframes de audio | ⛔ | Solo gain estático por clip/pista | P2 | L |
| B7 | Normalizar, compresor, EQ, denoise, gate, pan | ⛔ | Solo `volume` y `ducking` | P2 | M–L |
| B8 | Solo/mute por pista | ✅ | `Track.solo`/`muted` en mezcla, preview y export (docs/19) | — | — |
| B9 | Voz en off / importar audio a una pista concreta | ⛔ | Importar música siempre va a la primera pista | P1 | M |
| B10 | Pistas separadas de sistema y micrófono al grabar | ⛔ | Backlog C5; el micrófono queda como WAV hermano | P1 | M |
| B11 | Sincronizar por waveform / ajustar offset | ◐ | Hay snap a picos, pero no alineación ni offset por clip | P1 | M |
| B12 | Medidores de nivel | ⛔ | No hay VU/peak meter en el preview ni en las pistas | P1 | S–M |
| B13 | Exportar solo audio o stems | ⛔ | El export siempre es MP4 con video | P2 | S–M |
| B14 | Crossfade por clip (no uniforme por pista) | ◐ | Solo hay un crossfade global de la pista de música | P1 | S–M |
| B15 | Elegir la fuente del ducking | ◐ | Ducking usa todo el audio del video; no se elige pista/fuente | P2 | M |
| B16 | Mono/estéreo y pan | ⛔ | Todo se fuerza a estéreo; no hay pan | P2 | S–M |

### 3.3 Frame, transformación y composición

| # | Función | Estado | Qué falta / dónde | Prio | Tam |
|---|---|---|---|---|---|
| C1 | Cambiar resolución y aspecto del proyecto | ✅ | `Project.width/height` + `tl.set_project_size`; presets y custom en el inspector (docs/20) | — | — |
| C2 | Presets de lienzo 16:9, 9:16, 1:1, 4:5 y personalizado | ✅ | Inspector de proyecto (`CANVAS_PRESETS`); custom con ancho/alto | — | — |
| C3 | Encajar / rellenar / estirar por clip | ✅ | `Project.default_fit` + `Transform.fit`; misma geometría en preview/export (docs/20) | — | — |
| C4 | Escala y posición libres por clip | ✅ | `Transform.scale`, `x`, `y`; controles en el inspector | — | — |
| C5 | Crop interactivo | ◐ | Crop normalizado L/T/R/B por campos; falta arrastre sobre el preview | P1 | S–M |
| C6 | Rotar y voltear | ✅ | `Transform.rotation` (0/90/180/270), `flip_h`, `flip_v` | — | — |
| C7 | Auto-rotación de videos de móvil | ✅ | `media.probe` lee `frame.rotation`; preview y export enderezan (docs/20) | — | — |
| C8 | Fondo del lienzo: color, blur, imagen | ◐ | Color sólido (`Project.background`); blur/imagen pendientes | P1 | M |
| C9 | PiP / pista de video superpuesta | ⛔ | Una sola pista de video; C1 webcam PiP del backlog | P2 | L |
| C10 | Opacidad, borde y sombra de overlays | ⛔ | No hay overlays | P2 | M |
| C11 | Color avanzado: temperatura, LUT, curvas, HSL, viñeta | ◐ | Solo brillo/contraste/saturación | P2 | M–L |
| C12 | Scopes: histograma, waveform, vectorscopio | ⛔ | No hay | P2 | M |
| C13 | Speed ramps / keyframes de velocidad | ⛔ | `Clip.speed` es estático | P2 | L |
| C14 | Reverse y freeze frame | ⛔ | No hay | P2 | M |
| C15 | Más transiciones y control de curva | ◐ | 8 `xfade` y duración; sin curvas | P2 | S–M |
| C16 | Guías de área segura / rejilla | ⛔ | No hay | P1 | S |
| C17 | Zoom/pan del preview | ⛔ | El preview siempre encaja el lienzo | P1 | S–M |
| C18 | Arrastrar textos/transformaciones sobre el preview | ⛔ | Solo selects en el inspector | P1 | M |

### 3.4 Textos, gráficos y subtítulos

| # | Función | Estado | Qué falta / dónde | Prio | Tam |
|---|---|---|---|---|---|
| D1 | Texto multilínea y ajuste automático | ⛔ | `TextOverlay.text` es una línea; `drawtext` y PIL sin wrap | P1 | S–M |
| D2 | Fuente, tamaño, color, contorno, caja y alineación | ◐ | Arial fija, tamaño/color/negrita; sin fuente ni caja | P1 | M |
| D3 | Presets y animaciones de título | ⛔ | Solo texto estático | P2 | M |
| D4 | Subtítulos SRT/VTT (importar/exportar) | ⛔ | No hay parser ni modelo de cues | P1 | M |
| D5 | Imágenes, logos y stickers | ⛔ | `probe` solo video/audio; no hay assets de imagen | P2 | M |
| D6 | Formas, máscaras y chroma key | ⛔ | Fuera del alcance simple | P3 | L–XL |
| D7 | Carril de textos y snap a sus bordes | ⛔ | Los textos solo se listan en el inspector | P2 | M |

### 3.5 Proyecto, medios y export

| # | Función | Estado | Qué falta / dónde | Prio | Tam |
|---|---|---|---|---|---|
| E1 | Ajustes de proyecto: nombre, resolución, fps, fondo | ✅ (resolución/fps/fondo; nombre aún no editable) | Inspector de proyecto: lienzo, fit, fondo y FPS (docs/20) | — | — |
| E2 | Nuevo proyecto / guardar como / duplicar | ⛔ | Se crea al importar; no hay «guardar como» | P1 | S–M |
| E3 | Panel de medios y reemplazo de asset online | ⛔ | Solo «reubicar» cuando falta el archivo | P2 | M |
| E4 | Importar imágenes al timeline | ⛔ | Solo videos y audio de música | P2 | M |
| E5 | Export personalizado (resolución, bitrate, fps, códec) | ⛔ | Cuatro presets cerrados | P1 | M |
| E6 | Exportar un rango / la selección | ✅ (rango In/Out) | Opción del modal; render y corte rápido (docs/21) | — | — |
| E7 | Exportar solo audio | ⛔ | Siempre MP4 con video | P2 | S–M |
| E8 | GIF/WebM optimizado | ⛔ | Backlog D1 | P1 | M |
| E9 | Presets sociales (9:16, 1:1, 4:5) | ⛔ | Depende de C1/C2 | P2 | S |
| E10 | Exportar un fotograma (PNG) | ⛔ | No hay | P2 | S |
| E11 | «Corte rápido» sin recodificar | ✅ | `core/keyframes`; hay que ampliar blockers con lo nuevo | — | — |

### 3.6 UX, preview y plataforma

| # | Función | Estado | Qué falta / dónde | Prio | Tam |
|---|---|---|---|---|---|
| F1 | Loop de reproducción | ✅ | Igual que A11 (docs/21) | — | — |
| F2 | Pantalla completa / segunda ventana de preview | ⛔ | `App.window` de Aurora; docs/05 | P3 | M |
| F3 | Recordar tamaño/posición/maximizado | ⛔ | `remember="rec-studio"` de Aurora | P2 | S |
| F4 | Atajos configurables | ⛔ | Atajos fijos en `EditorController.on_key` | P3 | M |
| F5 | Nombres/roles de accesibilidad en el timeline | ◐ | Aurora 0.5+ tiene AccessKit; el timeline es canvas | P2 | S–M |

## 4. Arquitectura propuesta para desbloquearlo

### 4.1 Modelo de timeline: opción recomendada

Hay dos caminos:

- **Opción A (recomendada): posición absoluta solo donde hace falta.** `Track` gana
  `mode: "sequence" | "absolute"`. La pista de video sigue siendo `sequence` (compatibilidad total
  con `clip_starts`, transiciones y `xfade`). Las pistas de audio libres (audio separado, voz en
  off, efectos) son `absolute` y cada `Clip` lleva `start` en segundos. La música puede seguir
  siendo `sequence` al principio.
- **Opción B: todas las pistas absolutas.** Más potente y uniforme, pero obliga a reescribir
  `clip_starts`, transiciones, `move`, `drop_index`, export y casi todos los tests. No compensa
  para el primer corte.

La opción A permite lo pedido sin romper el caso «cortar y reordenar»:

```python
# core/project.py (implementado en docs/19)
@dataclass(frozen=True)
class Clip:
    id: str
    asset_id: str
    in_: float
    out_: float
    speed: float = 1.0
    effects: tuple[Effect, ...] = ()
    start: float | None = None       # solo pistas absolute
    audio_enabled: bool = True       # solo pista de video
    link_id: str | None = None       # audio separado ↔ clip de video

@dataclass(frozen=True)
class Track:
    kind: Literal["video", "audio"] = "video"
    mode: Literal["sequence", "absolute"] = "sequence"
    name: str = ""
    clips: tuple[Clip, ...] = ()
    transitions: tuple[Transition | None, ...] = ()
    muted: bool = False
    solo: bool = False
    locked: bool = False
    gain: float = 1.0
    ducking: float = 0.0
```

Reglas:

- `clip_starts(track)`: si `mode == "sequence"`, la fórmula actual; si `absolute`, `c.start` de cada
  clip (con `None` tratado como 0 solo al migrar).
- `track_duration`: secuencia actual; absoluta = `max(start + duration)`.
- Validación: `start >= 0`, sin solapes dentro de la misma pista absoluta, `link_id` existente
  (varios trozos de un audio dividido pueden compartirlo) y `audio_enabled` solo con sentido en
  video.
- Serialización: `SCHEMA_VERSION = 3`; `from_dict` acepta campos ausentes (proyectos v1/v2) y
  `store.migrate` rellena `mode="sequence"`, `start=None`, `audio_enabled=True`, `link_id=None`.
- Tests de migración con fixtures v1 y v2, y round-trip de una pista absoluta.

### 4.2 Separar audio: semántica

`tl.detach_audio(project, clip_id)`:

1. Exige que el clip sea de video y que el asset tenga audio.
2. Crea (o reutiliza) una pista `audio` en modo `absolute`, por ejemplo «Audio del video».
3. Añade un clip con el mismo `asset_id`, `out_` y `speed`, `link_id = clip.id`,
   `start = clip_start + solape_entrante` e `in_ = clip.in_ + solape_entrante * speed` (el audio
   audible empieza tras la transición, igual que el export).
4. Marca el clip de video original con `audio_enabled=False`.
5. Devuelve el proyecto y el id del clip de audio; **un solo undo**.

`tl.attach_audio` hace lo inverso: borra todos los clips de audio enlazados y vuelve a poner
`audio_enabled=True` en el video. Mover, recortar o dividir conserva el enlace; los trozos de un
audio dividido comparten el mismo `link_id` y se borran juntos al vincular.

Para **picar**:

- `tl.split_track(project, track, t)` corta un clip de una pista en `t` (secuencia o absoluta).
- `tl.split_all(project, t)` corta el video y todas las pistas de audio enlazadas; una entrada de
  undo. Si el audio está enlazado, corta ambos lados y mantiene la correspondencia.
- `tl.split_audio(project, t)` corta solo la pista de audio bajo el playhead.
- La UI puede ofrecer `S` (dividir todo lo enlazado), `Mayús+S` (solo audio) y una herramienta
  razor que corta la pista donde se pulsa.

### 4.3 Frame y transformación

> **Implementado en la fase 2** ([20](20-fase-2-frame.md)). Resumen de la decisión: `Project`
> gana `background`/`default_fit`, `Clip` gana un `Transform` tipado y el render comparte la
> geometría en `core/transform.py`. La cadena real es
> `rotación/flip → crop → fit/fill/stretch + scale → posición/fondo → eq → fades → texto`.

Separar dos conceptos que hoy están mezclados:

1. **Lienzo del proyecto**: `Project.width/height` + `background` (color) + `default_fit`
   (`fit`, `fill`, `stretch`). Cambiarlo no toca los clips: el render los vuelve a normalizar.
   Añadir un diálogo «Ajustes del proyecto» con presets 16:9, 9:16, 1:1, 4:5, 16:9 1440p/4K y
   «personalizado».
2. **Transformación por clip**: un `Transform` tipado en `Clip` (mejor que meterlo en `Effect`,
   porque tiene booleanos y un modo) con:
   - `fit`: `fit | fill | stretch` (hereda el del proyecto si es `None`);
   - `scale`, `x`, `y`;
   - `rotation` (0/90/180/270 o grados);
   - `flip_h`, `flip_v`;
   - `crop` (rectángulo normalizado) o un `Effect("crop")` separado.

La cadena de render debe ser la misma en preview y graph:

```
rotar/voltear → crop → scale (fit/fill/stretch) → posición/fondo → eq → fades → texto
```

`core/effects.py` (o un `core/transform.py` puro) debe devolver los parámetros calculados
(`crop`, `scale`, `pad`, offsets) y **ambos** consumidores usarlos. Tests golden del graph y tests
de frame sintético para el preview.

Para el fondo:

- primero color sólido (negro/`#RRGGBB`) con `pad`;
- después blur: `split` + `boxblur` + `overlay` (más caro; medir RNF-04);
- el preview puede aproximar el blur con PIL si no se quiere duplicar el coste.

Auto-rotación: leer la rotación real en `media.probe` (metadata/side data del stream), guardar
`MediaAsset.rotation`, intercambiar width/height si es 90/270 y aplicar `transpose` en graph y
`Image.rotate`/numpy en preview. Es un bug de correctitud, no solo una herramienta.

### 4.4 Pistas, UI y scroll

`ui/timeline.py` tiene la geometría fija (`VIDEO_TOP`, `AUDIO_H`, `MUSIC_H`, `Tracks`). Para N
pistas:

- un `TrackRow` calculado desde `Project.audio` (y futuros overlays): `top`, `height`, `kind`,
  `index`, `label`;
- cabeceras con nombre, M/S, candado y volumen; la barra lateral deja de ser solo `V/A/M`;
- `content_h` dinámico; el playhead cruza todas las filas;
- **scroll vertical**: envolver el contenido en un `scroll(axis="y")` con altura fija y mantener el
  scroll horizontal dentro. Hay que probarlo con Aurora 0.9 (scroll anidado) antes de comprometer
  la UI; si no funciona, alternativa: limitar a 4–6 pistas y hacer que el panel crezca.
- El filmstrip y las miniaturas solo se dibujan en la pista de video; el audio usa waveform.

La selección debe unificarse: hoy `("music", track_index, clip_index)` se usa solo para la primera
pista. Propuesta: `("audio", track_index, clip_index)` para cualquier pista, `("clip", id)` para
video, `("text", id)` y `("transition", index)`. Actualizar `state.selection`, inspector, timeline
y tests.

### 4.5 Export con audio absoluto

La pista de video sigue con `concat`/`xfade`. Para una pista de audio absoluta:

- una entrada por clip (`-ss in -t len -i asset`), como ya se hace con la música;
- `atrim`, `asetpts`, `aresample`, `atempo`, fades de audio;
- `adelay` hasta `start` (en muestras o ms) y `apad`/`atrim` a la duración total;
- `amix` con el audio del video y las demás pistas, `normalize=0`;
- si el clip está enlazado a un video con `audio_enabled=False`, el audio del video va en silencio
  y el sonido sale de la pista absoluta.

El preview puede seguir usando `core.graph.build_audio` para la mezcla completa cacheada (B1), que
ya reproduce exactamente el export. Eso evita duplicar la lógica de mezcla en Python y mantiene la
regla «lo exportado = lo visto». Hay que ampliar `build_audio` para pistas absolutas y medir el
coste de regenerar el WAV al mover un clip.

### 4.6 Keyframes, composición y subtítulos

No son la fundación, pero condicionan el diseño:

- **Keyframes**: un `Keyframe` por parámetro con interpolación; el graph necesita expresiones
  (`t`, `between`, `lerp`) o `sendcmd`. Es un salto grande; dejarlo para después de que transform y
  audio funcionen. Para un primer «Ken Burns» se puede hacer un transform con dos puntos fijos
  (inicio/fin) sin keyframes genéricos.
- **PiP/overlay**: requiere una pista de video absoluta con z-order y composición `overlay` en el
  graph. Es el mismo `Track.mode="absolute"` de 4.1, pero con un `Project.overlays` o
  `Project.video_tracks`. C1 (webcam) puede ser el primer caso de uso; no empezar por ahí si el
  objetivo es audio/frame.
- **Subtítulos**: se pueden modelar como `TextOverlay` con `start/end`; importar SRT/VTT es un
  parser + crear overlays; exportar `drawtext` ya funciona. Es más barato que keyframes y aporta
  mucho.

## 5. Hoja de ruta propuesta

### Fase 0 — Fundación del modelo (M) — ✅ implementada

> Resultado y decisiones: [19 — Fase 0 y 1](19-fase-0-1-audio.md).

- `SCHEMA_VERSION = 3`: `Clip.start`, `Clip.audio_enabled`, `Clip.link_id`, `Track.mode`,
  `Track.name`, `Track.solo`, `Track.locked`.
- `clip_starts`/`track_duration` para pistas absolutas; validación y round-trip.
- Migración v2 → v3 y tests con proyectos antiguos.
- Sin UI nueva (o solo la mínima para no romper).

**Criterio:** un proyecto v2 se abre idéntico; una pista absoluta con huecos se guarda y se
reabre; los tests actuales siguen verdes.

### Fase 1 — Audio editable (L) — ✅ implementada

> Resultado y decisiones: [19 — Fase 0 y 1](19-fase-0-1-audio.md).

- Separar/adjuntar audio; cortar todo / solo audio; mover, recortar, dividir y borrar clips de
  audio.
- Varias pistas de audio visibles; cabeceras con M/S/lock/volumen; scroll vertical.
- Fundidos de audio independientes y asas sobre la waveform (si cabe en la fase).
- Export con `adelay`/`amix` y preview con `build_audio`; tests de sincronía.

**Criterio:** un video de 10 s con un pitido en 0:05; «Separar audio»; mover el audio +1 s;
exportar; el pitido suena en 0:06 y el video original queda mudo; undo en un paso; preview y
export coinciden; el proyecto reabre igual.

### Fase 2 — Lienzo y frame (M) — ✅ implementada

> Resultado y decisiones: [20 — Fase 2](20-fase-2-frame.md).

- Ajustes de proyecto (resolución/aspecto/fps/fondo) y presets. ✅
- `fit`/`fill`/`stretch`, escala/posición, rotación/flip, crop. ✅
- Auto-rotación de móviles; fondo de color (blur después). ✅ color; blur pendiente.
- Preview y graph con la misma matemática; blockers del corte rápido. ✅
- UI de transformación con undo; falta arrastre/recorte directo sobre el preview (no bloqueante).

**Criterio:** el mismo proyecto en 16:9 se cambia a 9:16; un clip 16:9 se ve «fill» sin barras o
«fit» con fondo; un texto conserva su posición relativa; un video rotado 90° se endereza; el
export vertical coincide con el preview; el corte rápido se bloquea si hay transformación. ✅

### Fase 3 — Flujo de edición (M) — ✅ implementada

> Resultado y decisiones: [21 — Fase 3](21-fase-3-flujo.md).

- Copiar/pegar/duplicar; razor; marquesina que mueve; marcadores; loop e in/out; export de rango. ✅
- J/K/L (marcha atrás sin audio y a menos fps) y Q/W. ✅ Zoom que sigue al playhead (A17): ⛔ por
  Aurora 0.9.
- Panel de medios y drag & drop: pendientes (A13/A14).

**Criterio:** copiar dos clips y pegarlos en el playhead; duplicar con `Ctrl+D`; cortar con razor
en el punto pulsado; marcar y reproducir en loop; exportar solo el rango marcado; todo con undo
único y sin romper el snap. ✅

### Fase 4 — Composición y texto (L)

- PiP/overlay de video (C1 webcam como primer caso).
- Subtítulos SRT/VTT y mejoras de títulos (multilínea, fuente, caja).
- Keyframes/transform animado, color avanzado y scopes (por orden de valor).

**Criterio:** un PiP de webcam se compone en preview y export; un SRT importado genera textos
sincronizados; un zoom animado se ve igual en ambos; el proyecto reabre.

### Fase 5 — Export y compartir (M)

- Presets personalizados (resolución/bitrate/fps/códec), export de audio, GIF/WebM, presets
  sociales, exportar fotograma.

**Criterio:** exportar 9:16 a la resolución elegida; exportar WAV; GIF de 10 s con peso y calidad
aceptables; el progreso/ETA/cancelar siguen funcionando.

## 6. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Migración de proyectos v1/v2 | Pérdida de proyectos | `migrate` con tests de fixtures; `from_dict` tolerante; `.bak` ya existe |
| Preview y export divergen | Se exporta otra cosa | Matemática compartida en `core/`; golden tests de graph; tests de frame sintético |
| Audio absoluto desafina o deriva | Mala experiencia | Duraciones en frames/muestras; tests con FFmpeg real; medir offsets |
| Muchas pistas bajan el preview | RNF-04 | Proxy, culling, no decodificar pistas ocultas; medir con 4–6 pistas |
| Scroll vertical anidado en Aurora | UI rota | Spike corto con `pilot`; plan B: panel que crece / límite de pistas |
| Transformación cambia el corte rápido | Export incorrecto | Ampliar `keyframes.blockers` y tests |
| Alcance «mini NLE» | Nunca se termina | Fases con criterio; no meter máscaras/chroma/streaming |
| Rotación y metadatos | Videos de móvil mal | Leer side data, tests con sample rotado, fallback |

## 7. Decisiones pendientes antes de implementar

> **Resueltas en la fase 1:** «picar» audio con pista absoluta (decisión 2) y alcance del audio
> libre (decisión 5, primera iteración). **Resuelta en la fase 2:** «resizar el frame» se hizo con
> lienzo **y** transformación por clip (decisión 1, ver [20](20-fase-2-frame.md)). Las decisiones
> 3 (pistas de video) y 4 (keyframes) siguen abiertas para la fase 4.

1. **«Resizar el frame»**: ¿cambiar el lienzo del proyecto (resolución/aspecto), transformar el
   clip (escala/posición/crop), o ambos? La recomendación es **ambos**, empezando por el lienzo.
2. **«Picar» audio**: ¿basta con dividir el audio enlazado y separarlo, o se quiere una pista de
   audio libre con huecos? La recomendación es **pista absoluta para el audio separado** y mantener
   la música secuencial al principio.
3. **Pistas de video múltiples**: ¿se quieren ya (PiP) o se dejan para después de audio/frame? La
   recomendación es **después**; C1 webcam puede esperar a la fase 4.
4. **Keyframes**: ¿son prioritarios para zoom/títulos o se aceptan presets estáticos primero? La
   recomendación es **presets primero**, keyframes después.
5. **Alcance del editor**: seguir siendo un editor simple y bien hecho, o competir en herramientas
   con un mini NLE. Cada fase debe mantener la interfaz progresiva (lo avanzado, oculto hasta que
   se necesita).

## 8. Primer corte recomendado

> **Hecho:** fases 0, 1, 2 y 3 (modelo absoluto + audio editable + lienzo/transformación + flujo
> de edición). Ver [19 — Fase 0 y 1](19-fase-0-1-audio.md), [20 — Fase 2](20-fase-2-frame.md) y
> [21 — Fase 3](21-fase-3-flujo.md). Lo siguiente es la fase 4 (composición y texto).

Si hay que elegir una sola entrega después de este análisis:

**Fase 0 + Fase 1**: modelo absoluto + audio editable (separar, cortar, mover, recortar, pistas
múltiples y cabeceras). Es lo que convierte «puedo cortar video» en «puedo montar el audio», y
además deja preparada la base para overlays y PiP.

Después, **Fase 2** para el frame. El lienzo y la transformación son más autocontenidos, pero
necesitan el mismo cuidado de paridad preview/export; hacerlos después de audio permite reutilizar
la infraestructura de pistas absolutas. **Ambas entregas están hechas** (ver [19](19-fase-0-1-audio.md)
y [20](20-fase-2-frame.md)).

No empezaría por copiar/pegar, marcadores o GIF: son útiles, pero no desbloquean el resto.
