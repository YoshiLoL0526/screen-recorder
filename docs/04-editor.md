# 04 — Editor de video

## Objetivo

Que cortar, reordenar, ajustar velocidad y poner transiciones simples sea **inmediato y fiable**,
y que el resultado exportado sea exactamente lo que se vio en el preview (sin sorpresas de 1 s
de desfase).

El editor es un *no-lineal mínimo*: modelo de proyecto propio + motor de render sobre FFmpeg.
Todo el estado vive en `core/`; el timeline se dibuja con `canvas()` de Aurora; el preview usa
el decoder de PyAV y se pinta con `image(bytes)`.

## Alcance funcional (editor)

| Área | v1 + bloques + fases 0–3 + features v6 |
|---|---|
| Pistas | 1 de video + hasta 6 de audio. Las pistas pueden ser secuenciales (música) o absolutas (audio separado/voz) y tienen mute, solo, bloqueo y volumen. Al importar, el audio del video se separa a «Audio del video» y queda enlazado (`link_id`). |
| Clips | `in`/`out` frame-accurate, velocidad 0.25×–4×, gain, fades de audio (imagen y audio independientes), eq (brillo/contraste/saturación), zoom/escala, lienzo y transformación (fit/fill/stretch, posición, rotación, flip y crop). Se mueven/redimensionan sobre el canvas del preview (docs/23). |
| Transiciones | 8 del catálogo `xfade`, entre clips adyacentes, 0.2–2 s, con preview aproximado; en música, `acrossfade`. |
| Textos | `TextOverlay` con intervalo, posición, tamaño, color y negrita; `drawtext` en export y PIL en preview. |
| Elementos | `Element` (imagen, rectángulo, elipse, triángulo) con intervalo, posición/tamaño normalizados, rotación, opacidad, z-order, fit y blur; composición en preview y export (docs/25). |
| Efectos globales | `Project.effects`: color, blur, viñeta y blanco y negro sobre el video compuesto, antes de elementos y textos (docs/26). |
| Timeline | Zoom (5–600 px/s), pan con rueda/scrollbar, snap (bordes + picos de audio + marcadores + In/Out), playhead frame-accurate, waveform, filmstrip, marquesina que mueve en bloque, razor, marcadores, In/Out, portapapeles (copiar/cortar/pegar/duplicar), Q/W y drags de audio. |
| Preview | Play/pause, scrub, ±1 frame, J/K/L (2×–8×, marcha atrás sin audio), loop del rango In/Out o del timeline, resolución adaptativa, audio (mezcla completa best-effort), proxy 540p y edición directa del clip/elemento sobre el lienzo. |
| Proyecto | `.rsproj/project.json` versionado (schema 6), autosave, undo/redo por snapshots, lienzo/fondo/encaje configurables, marcadores, In/Out, elementos y efectos globales. |
| Export | MP4 H.264, presets, todo el timeline o el rango In/Out, progreso/ETA/cancelar, "corte rápido" sin recodificar. |

## Modelo de datos (`core/project.py`)

Todo `frozen=True` (comparables, hashables: requisito para que `Signal.set` y `@component`
detecten cambios de verdad y para el undo por snapshots).

```python
FPS_CHOICES = (24, 30, 60)

@dataclass(frozen=True)
class MediaAsset:
    id: str                    # uuid corto
    path: str                  # ruta absoluta (o relativa al proyecto si se copió)
    duration: float            # segundos (probe)
    width: int                 # dimensiones de VISUALIZACIÓN (rotadas si hace falta)
    height: int
    fps: float
    has_audio: bool
    audio_channels: int = 2
    rotation: int = 0          # 0/90/180/270; lo lee `media.probe` de PyAV
    kind: Literal["video", "audio", "image"] = "video"  # v6: imágenes de elementos

@dataclass(frozen=True)
class Effect:
    kind: Literal["eq", "volume", "fade_in", "fade_out",
                  "audio_fade_in", "audio_fade_out", "speed", "zoom",
                  "blur", "vignette", "sharpen", "grayscale"]
    params: tuple[tuple[str, float], ...] = ()   # tupla → hashable
    # eq: brightness, contrast, saturation | volume: gain | fade_*: seconds
    # audio_fade_*: solo audio (docs/19) | blur/vignette: elementos y globales (docs/25/26)

@dataclass(frozen=True)
class Transition:
    kind: Literal["fade", "fadeblack", "fadewhite", "wipeleft",
                  "wiperight", "slideleft", "circleopen", "dissolve"]
    duration: float = 0.5
    # pertenece al "hueco" entre el clip k y k+1 del mismo track
    # convención: transición SALIENTE del clip k (transitions[k] une k con k+1)

@dataclass(frozen=True)
class Transform:                             # fase 2, docs/20
    fit: Literal["inherit", "fit", "fill", "stretch"] = "inherit"
    scale: float = 1.0                       # 0.25…4, relativa al encaje
    x: float = 0.5                           # centro, fracción del lienzo
    y: float = 0.5
    rotation: int = 0                        # 0/90/180/270 (horario)
    flip_h: bool = False
    flip_v: bool = False
    crop: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)  # l, t, r, b

@dataclass(frozen=True)
class Clip:
    id: str
    asset_id: str
    in_: float                  # segundos dentro del asset
    out_: float
    speed: float = 1.0
    effects: tuple[Effect, ...] = ()
    start: float | None = None  # solo pistas `absolute` (docs/19)
    audio_enabled: bool = True  # solo pista de video
    link_id: str | None = None  # audio separado → clip de video
    transform: Transform | None = None  # None = sin transformación propia (docs/20)

    @property
    def duration(self) -> float:            # duración en el timeline
        return (self.out_ - self.in_) / self.speed

@dataclass(frozen=True)
class Track:
    kind: Literal["video", "audio"]
    clips: tuple[Clip, ...] = ()
    transitions: tuple[Transition | None, ...] = ()  # len(clips)-1 huecos
    muted: bool = False
    gain: float = 1.0
    ducking: float = 0.0                    # 0…1, solo pistas de audio (B1)
    mode: Literal["sequence", "absolute"] = "sequence"  # docs/19
    name: str = ""
    solo: bool = False
    locked: bool = False

@dataclass(frozen=True)
class TextOverlay:                          # B2
    id: str
    text: str
    start: float
    end: float
    x: float = 0.5                          # centro, fracción del frame
    y: float = 0.82
    size: int = 42                          # px a la altura del proyecto
    color: str = "#FFFFFF"
    bold: bool = False

@dataclass(frozen=True)
class Marker:                               # fase 3, docs/21
    id: str
    t: float                                # segundos del timeline (frame exacto)
    name: str = ""

@dataclass(frozen=True)
class Element:                              # v6, docs/22/25
    id: str
    kind: Literal["image", "rect", "ellipse", "triangle"]
    start: float
    end: float
    x: float = 0.5                          # centro, fracción del lienzo
    y: float = 0.5
    width: float = 0.3                      # tamaño, fracción del lienzo
    height: float = 0.2
    rotation: float = 0.0                   # grados horarios
    opacity: float = 1.0                    # 0…1
    color: str = "#FFFFFF"                  # figuras
    asset_id: str | None = None             # imágenes: MediaAsset(kind="image")
    fit: Literal["fit", "fill", "stretch"] = "fit"
    effects: tuple[Effect, ...] = ()        # p. ej. blur

@dataclass(frozen=True)
class Project:
    schema_version: int = 6
    name: str = "Sin título"
    assets: tuple[MediaAsset, ...] = ()
    video: Track = Track("video")
    audio: tuple[Track, ...] = ()           # pistas de audio (música / audio separado)
    texts: tuple[TextOverlay, ...] = ()     # textos quemados (B2)
    fps: int = 60                           # fps del proyecto (render)
    width: int = 1920                       # lienzo de salida
    height: int = 1080
    background: str = "#000000"             # color del lienzo (#RRGGBB)
    default_fit: Literal["fit", "fill", "stretch"] = "fit"
    markers: tuple[Marker, ...] = ()        # fase 3: ordenados por tiempo
    range_in: float | None = None           # In/Out del timeline (no del clip)
    range_out: float | None = None
    elements: tuple[Element, ...] = ()      # v6: z-order, el último va encima
    effects: tuple[Effect, ...] = ()        # v6: efectos globales (uno por tipo)
```

Invariantes que valida `core/project.py` al construir (y que los tests cubren):

- `0 ≤ in_ < out_ ≤ asset.duration`; `0.25 ≤ speed ≤ 4`.
- `len(transitions) == max(0, len(clips) − 1)`; cada transición `0.2 ≤ d ≤ min(dur_a, dur_b) / 2`.
- Assets referenciados existen (o se marcan offline); un asset de solo audio tiene `width=height=0`
  y `fps=0`.
- `0 ≤ ducking ≤ 1`; textos con `0 ≤ start < end`, posición 0…1, tamaño 8–200 y color `#RRGGBB`.
- `Project.background` es `#RRGGBB` y `default_fit` es `fit`/`fill`/`stretch`; `Transform` valida
  escala 0.25–4, posición 0…1, rotación 0/90/180/270 y crop `0 ≤ l < r ≤ 1`, `0 ≤ t < b ≤ 1`.
- IDs únicos (assets, clips, textos, marcadores y elementos); `range_out > range_in` si están los
  dos.
- `Track.mode="absolute"`: `Clip.start ≥ 0`, sin solapes en la pista, sin transiciones; `link_id`
  existe y apunta a un clip de video (varios trozos de un audio dividido pueden compartirlo) y
  `audio_enabled` solo se usa en la pista de video. Detalle en [19](19-fase-0-1-audio.md).
- `MediaAsset.kind` es `video`/`audio`/`image`; una imagen tiene `duration=0` y no se puede añadir
  como clip. `Element` valida intervalo, centro 0…1, tamaño 0.01…4, rotación finita, opacidad 0…1,
  color `#RRGGBB` o asset de imagen existente, y efectos conocidos. `Project.effects` no repite
  tipo. Detalle en [22](22-plan-features-editor.md) y [25](25-elementos.md).

## Matemática del timeline (`core/timeline.py`, puro y testeado)

Notación: clips `1..n` con duraciones `d₁..dₙ` (ya con velocidad aplicada), transiciones
`t₁..tₙ₋₁` donde `tᵢ` une el clip `i` con `i+1`.

```
D₀ = 0
Dₖ = Dₖ₋₁ + dₖ − tₖ₋₁            (duración acumulada tras unir k clips)
Duración total = Dₙ = Σ dᵢ − Σ tᵢ

start (global) del clip k+1 = Dₖ − tₖ
```

Ejemplo numérico (el que usan los tests golden):

```
d = [5, 4, 3], t = [0.5, 0.4]
D₁ = 5
D₂ = 5 + 4 − 0.5 = 8.5
D₃ = 8.5 + 3 − 0.4 = 11.1        → duración total 11.1 s
offset xfade₁ = D₁ − t₁ = 4.5
offset xfade₂ = D₂ − t₂ = 8.1
```

API del módulo (funciones puras):

```python
def total_duration(p: Project) -> float: ...
def clip_start(p: Project, track: Track, index: int) -> float: ...
def locate(p: Project, t: float) -> ClipPos | None:      # (track, index, t_local, t_source)
def time_at_x(x: float, zoom: float, scroll: float) -> float: ...
def x_at_time(t: float, zoom: float, scroll: float) -> float: ...

def split(p: Project, t: float) -> Project: ...
def trim(p: Project, clip_id: str, new_in: float, new_out: float) -> Project: ...
def move(p: Project, clip_id: str, to_index: int) -> Project: ...
def ripple_delete(p: Project, clip_id: str) -> Project: ...
def snap(t: float, candidates: list[float], zoom: float, threshold_px: float = 8) -> float: ...

# fase 2 (docs/20)
def set_project_size(p: Project, width: int, height: int) -> Project: ...
def set_project_fit(p: Project, fit: str) -> Project: ...
def set_project_background(p: Project, color: str) -> Project: ...
def set_transform(p: Project, clip_id: str, **changes) -> Project: ...
def reset_transform(p: Project, clip_id: str) -> Project: ...

# fase 3 (docs/21)
def add_marker(p: Project, t: float, name: str = "") -> tuple[Project, Marker]: ...
def set_range_in(p: Project, t: float) -> Project: ...      # y set_range_out, clear_range, play_range
def razor(p: Project, t: float, track: int | None = None) -> Project: ...
def split_everything(p: Project, t: float) -> Project: ...
def trim_to(p: Project, t: float, side: str, track: int | None = None) -> Project: ...  # Q/W
def copy_items(p: Project, ids) -> Clipboard: ...
def paste(p: Project, clipboard: Clipboard, t: float) -> tuple[Project, tuple[str, ...]]: ...
def duplicate(p: Project, ids) -> tuple[Project, tuple[str, ...]]: ...
def move_block(p: Project, ids, ghost: float) -> Project: ...
```

Reglas finas:

- **Snap** en píxeles (no en segundos): candidatos = bordes de clips y transiciones, playhead,
  `0`, marcadores, In/Out (fase 3) y segundos enteros si el zoom es bajo. Umbral 8 px convertido a segundos con el zoom
  actual (así el imán se siente igual a cualquier escala).
- **Frame accuracy**: el playhead vive en *frames enteros* de `project.fps`; `split` corta en
  frontera de frame. `t_frame = round(t * fps) / fps`.
- `split` reparte las transiciones: el clip izquierdo conserva entrante y efectos; el derecho
  hereda saliente; la nueva frontera nace sin transición.

## Operaciones, undo y autosave (`core/store.py`)

- **Undo por snapshots** del `Project` (frozen): la pila guarda `(etiqueta, Project)`.
  Límite 50; las operaciones de drag se agrupan en una transacción:

  ```python
  store.begin("mover clip")      # snapshot del estado ANTES
  ... (el drag actualiza `project.set(...)` muchas veces) ...
  store.commit()                 # empuja el estado final si cambió
  ```
- Redo = pila paralela. `Ctrl+Z` / `Ctrl+Y` con toast de la etiqueta ("Deshacer: mover clip").
- **Autosave**: escritura atómica (`project.json.tmp` → `replace`) si pasaron 30 s y
  `project != last_saved`. Al abrir, si el archivo está corrupto se conserva `.bak` (la versión
  anterior) y se avisa.
- Guardado manual `Ctrl+S` + indicador "sin guardar" en el header.

## Preview (`services/playback.py`)

### Decodificación

- **PyAV** (`av.open` por asset, contenedores cacheados y cerrados por LRU ≥ 2).
- `seek(t)` → `container.seek(int(t / time_base), backward=True)` (keyframe) y decodifica hacia
  delante hasta `t`; en reproducción secuencial **no se vuelve a hacer seek** (se avanza el
  generador de frames, que es lo rápido).
- **Mapeo del timeline virtual** con `core.timeline.locate`: t global → (asset, t_source) →
  frame concreto. Cambiar de clip en reproducción = abrir/usar el decoder del asset destino.

### Composición del preview

1. Frame de video del clip activo (fuente). Si el asset trae rotación 90/270, PyAV entrega la
   orientación codificada y el preview la gira explícitamente (FFmpeg sí auto-rota en el export;
   ver [20](20-fase-2-frame.md)).
2. Si `t` cae dentro de una transición: se decodifica también el clip vecino y se **aproxima el
   efecto** (los 4 fundidos y los 4 wipes con mezcla alfa/cortina en numpy). El render final
   siempre es el de FFmpeg; el preview avisa "aproximado" en transiciones no lineales.
3. Transformación del clip (rotación/flip → crop → fit/fill/stretch + escala → posición sobre el
   lienzo con `Project.background`), con la misma geometría de `core/transform.py` que el graph.
4. Escalado con **`Image.reduce(k)`** (filtro de caja por decimación entera; para 1080p→540p es
   un solo `reduce(2)`) + ajuste final pequeño si el factor no es exacto, y compresión a **JPEG
   q80 4:2:0** → `bytes`. Medido en F0 (S4/S4c): **8.5 ms por frame** (p95 9.9) ⇒ >100 fps
   posibles a 960×540; el bilinear genérico de PIL tardaba 13 ms y `cv2` 8–9 ms, así que `reduce`
   gana y evita depender de `opencv` en la app.
5. `preview_frame.set(bytes)`; la vista pinta `image(preview_frame(), fit="contain")`.

Presupuesto y adaptación:

| Situación | Estrategia |
|---|---|
| Decode a 1080p sobra (> 60 fps de decode) | preview 960×540, cascade |
| Decode lento (HEVC 4K, velocidad 0.25×) | bajar a 720×405 y a 480×270 por pasos si el tick se salta |
| Reproducción con clip a 0.25× | pedir frames por *tiempo de timeline*, no por frame de origen (se repiten frames: correcto). |

El tick de reproducción es una tarea `app.spawn()` a **30 Hz** con reloj `time.perf_counter()`:
al dar a play se fija `t_inicio` y `t_wall_0`; en cada tick `t = t_inicio + (perf_counter() − t_wall_0)`
y se pide el frame de ese `t`. Si el decode de un frame tarda más que el tick, se descartan
ticks (nunca se acumula retraso).

### Audio del preview

- Decode de audio con PyAV → float32 → **ring-buffer** consumido por un `OutputStream` de
  `sounddevice` (latencia pedida ≤ 80 ms).
- v1: **activado con un switch**, marcado como "best-effort"; si el buffer se queda sin datos o
  se desborda, se silencia y se muestra un aviso discreto (un desfase audible es peor que silencio).
- El audio del export no depende de esto (siempre es el de FFmpeg).

### Waveform y thumbnails (`services/media.py`)

- **Waveform**: decodificar el audio del asset completo una sola vez (streaming, sin cargar todo
  a memoria) → `float32` mono → **peaks min/max por bucket** → cache `NPZ` en `.cache/`.
  El timeline dibuja `polyline` con un array numpy `(n, 2)` por canal visible (Aurora acepta
  arrays de numpy sin copiarlos, pensado justo para esto).
- **Thumbnails**: filmstrip por asset (1 thumb cada 2 s, cap 300, altura 96 px, JPEG cacheado).
  El timeline v1 dibuja **carátulas de clip** (color + nombre + waveform); la tira de
  thumbnails dentro del clip se añade post-v1 (limitación: `canvas()` no pinta imágenes todavía;
  ver ADR-05). El thumbnail del asset sí se usa en la Biblioteca y en el inspector del clip.

## Timeline (UI)

Detalle de interacción en [05 — UI con Aurora](05-ui-aurora.md). Contrato con `core/`:

- El canvas **nunca** recalcula modelo: lee `project()`, `zoom()`, `px_offset()`, `playhead()`,
  `selection()` y dibuja; en los drags calcula el *intento* con funciones puras y llama a los
  commands del store.
- Hit-testing manual (una sola función `hit_test(x, y) -> HIT`) con zonas: regla/hoja de
  transición (esquinas de clip ±7 px), cuerpo del clip, asa de trim izquierda/derecha (±6 px),
  playhead (±5 px), vacío.
- Estados de drag: `scrub | move | trim_l | trim_r | playhead` en un `Signal`; `Esc` cancela
  (restaura el snapshot del `begin`).

## Export (`core/graph.py` + `services/export.py`)

### Builder de filter graph

`core/graph.build(project, preset) -> GraphSpec(args, filter_complex, total_duration)` — función
pura sobre el modelo: genera el comando completo y es 100 % testeable (golden strings).

> Implementado en F4: ver [13 — F4](13-f4-export.md). La cadena real difiere del primer diseño
> en dos puntos (motivos en 13): **una entrada por clip** con seek de entrada (`-ss IN -t LEN`)
> en vez de `trim=start:end`, y **duraciones en frames enteros** (`tpad` + `trim=end_frame`, y
> el audio a esa misma duración) para que ni los offsets ni el audio deriven.

Cadena canónica **por clip de video** (el orden importa). La rama histórica (sin transformación,
`default_fit="fit"`, fondo negro y sin auto-rotación) se conserva byte a byte:

```
-ss IN -t LEN -i archivo                     # seek preciso de entrada (una entrada por clip)
setpts=(PTS-STARTPTS)/SPEED                  # velocidad (divide el tiempo)
crop=w=iw/Z:h=ih/Z:x=(iw-ow)*CX:y=(ih-oh)*CY # zoom (si hay)
eq=brightness=…:contrast=…:saturation=…      # color (si hay)
scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:-1:-1:color=black   # normalización
fps=FPS,tpad=stop_mode=clone:stop=FPS,trim=end_frame=N    # N = round(duración·FPS) frames exactos
fade=t=in|out (si hay) ,format=yuv420p,setsar=1           # fundidos, formato y SAR para xfade
```

Con lo de la fase 2 (transformación, fit/fill/stretch, fondo de color o auto-rotación) la rama
nueva es:

```
transpose/hflip/vflip (rotación del clip; la del asset la aplica FFmpeg al abrir)
crop=w=CW:h=CH:x=CX:y=CCY                    # crop normalizado (si no es el clip entero)
crop=…                                       # zoom heredado (si hay)
scale=OUT_W:OUT_H
pad=PW:PH:PX:PY:color=0xRRGGBB               # lienzo + posición
crop=W:H:OX:OY                               # recorte contra el lienzo (si se sale)
eq=…                                         # después de la composición
fps/tpad/trim/fade/format/setsar             # igual que la rama histórica
```

`core/transform.py` decide `CW/CH/CX/CCY/OUT_W/OUT_H/PX/PY/…`; el preview usa los mismos valores.
La auto-rotación se apoya en la que FFmpeg hace por defecto al leer la display matrix (sin
`-noautorotate`, para no copiar la matriz al MP4 y girar dos veces). Detalle en
[20](20-fase-2-frame.md).

Después de componer los clips (cortes/transiciones) y antes de los textos, el graph aplica en este
orden: **efectos globales** (`core/global_effects.py`) → **elementos** (`core/overlays.py`) →
`drawtext`. El preview (`services/playback.FrameRenderer`) llama a los mismos hooks en el mismo
orden. Sin efectos globales ni elementos, el graph es byte a byte el de F4. Detalle en
[25](25-elementos.md) y [26](26-efectos-globales.md).

Cadena **por clip de audio** (tramo audible: el entrante de una transición empieza tras el solape):

```
atrim=start=SOLAPE·SPEED (si hay), asetpts=PTS-STARTPTS, aresample=48000, aformat=fltp:stereo,
atempo=SPEED (0.5–2.0 en cadena), volume=GAIN, afade in/out (efecto o 30 ms anti-click),
apad=whole_dur=D, atrim=duration=D           # D = la duración en frames del tramo de video
```

**Transiciones** (`xfade`) encadenadas con la fórmula de offsets de arriba (en frames); los cortes
consecutivos se unen con un solo `concat`; audio con `concat` (silencio de `anullsrc` si un clip
no tiene audio) y `amix` con las pistas extra. Golden test (`tests/test_f4_graph.py`) para el
caso de 2 clips, fundido de 0.5 s y clip 2 al doble de velocidad:

```bash
ffmpeg -hide_banner -nostdin -loglevel error -y \
  -ss 10 -t 15.1 -i a.mp4 -ss 3.2 -t 8.9 -i b.mp4 -filter_complex "
[0:v]setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:-1:-1:color=black,
     fps=60,tpad=stop_mode=clone:stop=60,trim=end_frame=900,format=yuv420p,setsar=1[v0];
[1:v]setpts=(PTS-STARTPTS)/2,scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:-1:-1:color=black,
     fps=60,tpad=stop_mode=clone:stop=60,trim=end_frame=264,format=yuv420p,setsar=1[v1];
[v0][v1]xfade=transition=fade:duration=0.5:offset=14.5[x1];
[0:a]asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,
     afade=t=out:st=14.97:d=0.03,apad=whole_dur=15,atrim=duration=15[a0];
[1:a]atrim=start=1,asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,
     atempo=2,afade=t=in:st=0:d=0.03,apad=whole_dur=3.9,atrim=duration=3.9[a1];
[a0][a1]concat=n=2:v=0:a=1[amain]
" -map "[x1]" -map "[amain]" \
  -c:v h264_nvenc -preset p5 -rc vbr -cq 21 -b:v 0 -pix_fmt yuv420p \
  -c:a aac -b:a 192k -ar 48000 -movflags +faststart -progress pipe:1 -nostats out.mp4
```

(Total 900 + 264 − 30 = 1134 frames = 18.9 s. El audio del clip 2 salta los 0.5 s del solape
— 1 s de fuente a 2× — y dura 3.9 s: 15 + 3.9 = 18.9 s, igual que el video.)

### Reglas de oro del graph (errores clásicos que evitamos)

1. **Todo igual antes de `xfade`**: misma resolución, fps, pixel format, SAR y timebase.
2. **`setpts=PTS-STARTPTS`** en cada clip (si no, los timestamps absolutos enloquecen los offsets).
3. `atempo` solo admite 0.5–2 por instancia: para 0.25× o 4× se encadenan dos (`atempo=0.5,atempo=0.5`).
4. Offsets de `xfade` relativos al timeline **de la cadena**, no del proyecto (de ahí la fórmula).
5. Sin audio en un clip ⇒ segmento de silencio de la misma duración; `concat` exige segmentos.
6. Los clips de video sin efectos y sin velocidad y con cortes en keyframe pueden ir por el
   camino rápido (abajo).

### "Corte rápido" sin recodificar (`core/keyframes.py`)

> Implementado en F4. El inicio se imanta al keyframe **al exportar** (el proyecto no cambia) y
> el concat usa `-auto_convert 0` para que los paquetes sean idénticos byte a byte (ver 13).

Cuando el proyecto es solo cortes (sin transiciones, efectos ni velocidad) y **los cortes caen
en keyframes**, se puede exportar sin tocar el video:

```bash
# por segmento
ffmpeg -ss A -to B -i in.mp4 -c copy -avoid_negative_ts make_zero -f mp4 seg_i.mp4
# unión
ffmpeg -f concat -safe 0 -i list.txt -c copy -movflags +faststart out.mp4
```

Al importar un asset, un pase de indexación (packets con `is_keyframe`) guarda la lista de
keyframes en cache; al cortar en modo rápido, el trim **se imanta al keyframe** más cercano y se
avisa si movió el corte (típico ≤ 1 s, el precio de cortar sin recodificar).

### Preset de export

| Preset | Video | Audio | Uso |
|---|---|---|---|
| Rápido (HW) | encoder HW, `-cq 21` | AAC 192k | por defecto si hay NVENC/AMF/QSV. |
| Calidad | `libx264 -preset medium -crf 18` | AAC 256k | archivo definitivo. |
| Ligero | `libx264 -preset veryfast -crf 24`, 1280×720 | AAC 160k | compartir rápido. |
| Sin recodificar | `-c copy` (solo cortes en keyframe) | `-c copy` | instantáneo. |

- Progreso: `-progress pipe:1 -nostats`; se parsea `out_time_us` y se divide por la duración
  total → `Signal(export_progress)`, ETA con media móvil de los últimos 20 ticks.
- Cancelar: `terminate()` + borrar parcial + estado `CANCELLED`.
- Al terminar: verificación con `ffprobe` (duración ±0.2 s respecto a la esperada, 1 pista de
  video, ≥ 1 de audio si el proyecto tiene audio) → toast con "Abrir carpeta" / "Reproducir".

## Persistencia (`project.json`)

```json
{
  "schema_version": 4,
  "name": "Demo del dashboard",
  "fps": 60, "width": 1920, "height": 1080,
  "background": "#000000", "default_fit": "fit",
  "assets": [
    { "id": "a1", "path": "C:/Users/Raid/Videos/RecStudio/2026-10-16 10-30 demo.mp4",
      "duration": 42.3, "width": 1920, "height": 1080, "fps": 60.0, "has_audio": true }
  ],
  "video": {
    "kind": "video",
    "clips": [
      { "id": "c1", "asset_id": "a1", "in_": 2.0, "out_": 14.2, "speed": 1.0,
        "effects": [{ "kind": "eq", "params": [["brightness", 0.03]] }],
        "transform": null },
      { "id": "c2", "asset_id": "a1", "in_": 20.0, "out_": 30.0, "speed": 1.5,
        "effects": [],
        "transform": { "fit": "inherit", "scale": 1.0, "x": 0.5, "y": 0.5,
                       "rotation": 0, "flip_h": false, "flip_v": false,
                       "crop": [0.0, 0.0, 1.0, 1.0] } }
    ],
    "transitions": [ { "kind": "fade", "duration": 0.5 } ]
  },
  "audio": [ { "kind": "audio", "clips": [], "transitions": [], "muted": false, "gain": 1.0 } ]
}
```

- `schema_version` + `core/store.migrate(data)`: cualquier cambio de esquema añade una migración;
  un archivo más nuevo que la app se abre en modo lectura con aviso.
- Assets offline: `path` inexistente → el asset se marca, los clips se dibujan rayados y el
  export se bloquea con un diálogo "Reubicar archivo…" (búsqueda por nombre en la carpeta del proyecto).

## Casos límite (y qué se hace)

| Caso | Comportamiento |
|---|---|
| Transición más larga que los clips | Se acota a `min(dur_a, dur_b)/2` y se avisa. |
| Clip de 0 frames tras trim | Trim tiene mínimo 1 frame; el drag anterior no permite cerrar más. |
| Audio más corto que el video | El video manda; el audio rellena con silencio al final (no se recorta el video). |
| Asset borrado / movido | Modo offline + «Reubicar…» en el inspector; el export avisa y no arranca. |
| Proyecto con fps distinto a los assets | Todo se normaliza al fps del proyecto en el render (aviso en el primer export lento). |
| Muchos clips (100+) | El canvas culling solo dibuja lo visible; el graph sigue siendo lineal. |
| Export cancelado | Parcial borrado; el proyecto no se toca. |
