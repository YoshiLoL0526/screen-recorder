# 04 — Editor de video

## Objetivo

Que cortar, reordenar, ajustar velocidad y poner transiciones simples sea **inmediato y fiable**,
y que el resultado exportado sea exactamente lo que se vio en el preview (sin sorpresas de 1 s
de desfase).

El editor es un *no-lineal mínimo*: modelo de proyecto propio + motor de render sobre FFmpeg.
Todo el estado vive en `core/`; el timeline se dibuja con `canvas()` de Aurora; el preview usa
el decoder de PyAV y se pinta con `image(bytes)`.

## Alcance funcional (editor)

| Área | v1 |
|---|---|
| Pistas | 1 de video + hasta 2 de audio (clip + música/segunda pista). Sin overlays. |
| Clips | `in`/`out` frame-accurate, velocidad 0.25×–4×, gain, fades de audio, eq (brillo/contraste/saturación), zoom/escala. |
| Transiciones | 8 del catálogo `xfade`, entre clips adyacentes, 0.2–2 s, con preview aproximado. |
| Timeline | Zoom (5–600 px/s), pan con rueda/scrollbar, snap, playhead frame-accurate, waveform de audio. |
| Preview | Play/pause, scrub, ±1 frame, loop de selección, resolución adaptativa, audio best-effort. |
| Proyecto | `.rsproj/project.json` versionado, autosave, undo/redo por snapshots. |
| Export | MP4 H.264, presets, progreso/ETA/cancelar, "corte rápido" sin recodificar. |

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
    width: int
    height: int
    fps: float
    has_audio: bool
    audio_channels: int = 2
    rotation: int = 0

@dataclass(frozen=True)
class Effect:
    kind: Literal["eq", "volume", "fade_in", "fade_out", "speed", "zoom"]
    params: tuple[tuple[str, float], ...] = ()   # tupla → hashable
    # eq: brightness, contrast, saturation | volume: gain | fade_*: seconds | zoom: factor

@dataclass(frozen=True)
class Transition:
    kind: Literal["fade", "fadeblack", "fadewhite", "wipeleft",
                  "wiperight", "slideleft", "circleopen", "dissolve"]
    duration: float = 0.5
    # pertenece al "hueco" entre el clip k y k+1 del mismo track
    # convención: transición SALIENTE del clip k (transitions[k] une k con k+1)

@dataclass(frozen=True)
class Clip:
    id: str
    asset_id: str
    in_: float                  # segundos dentro del asset
    out_: float
    speed: float = 1.0
    effects: tuple[Effect, ...] = ()

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

@dataclass(frozen=True)
class Project:
    schema_version: int = 1
    name: str = "Sin título"
    assets: tuple[MediaAsset, ...] = ()
    video: Track = Track("video")
    audio: tuple[Track, ...] = ()           # pistas de audio (música / segunda voz)
    fps: int = 60                           # fps del proyecto (render)
    width: int = 1920
    height: int = 1080
```

Invariantes que valida `core/project.py` al construir (y que los tests cubren):

- `0 ≤ in_ < out_ ≤ asset.duration`; `0.25 ≤ speed ≤ 4`.
- `len(transitions) == max(0, len(clips) − 1)`; cada transición `0.2 ≤ d ≤ min(dur_a, dur_b) / 2`.
- Assets referenciados existen (o se marcan offline).
- IDs únicos.

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
```

Reglas finas:

- **Snap** en píxeles (no en segundos): candidatos = bordes de clips y transiciones, playhead,
  `0`, y segundos enteros si el zoom es bajo. Umbral 8 px convertido a segundos con el zoom
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

1. Frame de video del clip activo (fuente).
2. Si `t` cae dentro de una transición: se decodifica también el clip vecino y se **aproxima el
   efecto** (los 4 fundidos y los 4 wipes con mezcla alfa/cortina en numpy). El render final
   siempre es el de FFmpeg; el preview avisa "aproximado" en transiciones no lineales.
3. Escalado con **`Image.reduce(k)`** (filtro de caja por decimación entera; para 1080p→540p es
   un solo `reduce(2)`) + ajuste final pequeño si el factor no es exacto, y compresión a **JPEG
   q80 4:2:0** → `bytes`. Medido en F0 (S4/S4c): **8.5 ms por frame** (p95 9.9) ⇒ >100 fps
   posibles a 960×540; el bilinear genérico de PIL tardaba 13 ms y `cv2` 8–9 ms, así que `reduce`
   gana y evita depender de `opencv` en la app.
4. `preview_frame.set(bytes)`; la vista pinta `image(preview_frame(), fit="contain")`.

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

Cadena canónica **por clip de video** (el orden importa):

```
trim=start=IN:end=OUT          # corte exacto (frame-accurate)
setpts=(PTS-STARTPTS)/SPEED    # velocidad (divide el tiempo)
eq=brightness=…:contrast=…:saturation=…     # efectos (si hay)
scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:-1:-1:color=black   # normalización
fps=FPS,format=yuv420p,setsar=1               # fps, formato y SAR para xfade
```

Cadena **por clip de audio**:

```
atrim=start=IN:end=OUT, asetpts=PTS-STARTPTS, atempo=SPEED (0.5–2.0 en cadena),
volume=GAIN, afade=t=in/out:d=0.03            # micro-fades anti-click
```

**Transiciones** (`xfade`) encadenadas con la fórmula de offsets de arriba; y concatenación de
audio con `concat` (los huecos con `anullsrc` si un clip no tiene audio). Ejemplo completo para
el caso de los tests golden (2 clips, fade 0.5 s, clip 2 al doble de velocidad):

```bash
ffmpeg -hide_banner -y -i a.mp4 -i b.mp4 -filter_complex "
[0:v]trim=start=10:end=25,setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=decrease,
     pad=1920:1080:-1:-1:color=black,fps=60,format=yuv420p,setsar=1[v0];
[1:v]trim=start=3.2:end=12,setpts=(PTS-STARTPTS)/2,scale=1920:1080:force_original_aspect_ratio=decrease,
     pad=1920:1080:-1:-1:color=black,fps=60,format=yuv420p,setsar=1[v1];
[v0][v1]xfade=transition=fade:duration=0.5:offset=14.5[vout];
[0:a]atrim=start=10:end=25,asetpts=PTS-STARTPTS,afade=t=out:st=14.97:d=0.03[a0];
[1:a]atrim=start=3.2:end=12,asetpts=PTS-STARTPTS,atempo=2,afade=t=in:st=0:d=0.03[a1];
[a0][a1]concat=n=2:v=0:a=1[aout]
" -map "[vout]" -map "[aout]" \
  -c:v h264_nvenc -preset p5 -rc vbr -cq 20 -b:v 0 -pix_fmt yuv420p \
  -c:a aac -b:a 192k -movflags +faststart out.mp4
```

(El audio del clip 2 dura 4.4 s tras `atempo=2`: por eso `afade out` no lleva `st=14.97` pero sí
el clip 1. Los golden tests fijan el string exacto para detectar regresiones.)

### Reglas de oro del graph (errores clásicos que evitamos)

1. **Todo igual antes de `xfade`**: misma resolución, fps, pixel format, SAR y timebase.
2. **`setpts=PTS-STARTPTS`** en cada clip (si no, los timestamps absolutos enloquecen los offsets).
3. `atempo` solo admite 0.5–2 por instancia: para 0.25× o 4× se encadenan dos (`atempo=0.5,atempo=0.5`).
4. Offsets de `xfade` relativos al timeline **de la cadena**, no del proyecto (de ahí la fórmula).
5. Sin audio en un clip ⇒ segmento de silencio de la misma duración; `concat` exige segmentos.
6. Los clips de video sin efectos y sin velocidad y con cortes en keyframe pueden ir por el
   camino rápido (abajo).

### "Corte rápido" sin recodificar (`core/keyframes.py`)

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
  "schema_version": 1,
  "name": "Demo del dashboard",
  "fps": 60, "width": 1920, "height": 1080,
  "assets": [
    { "id": "a1", "path": "C:/Users/Raid/Videos/RecStudio/2026-10-16 10-30 demo.mp4",
      "duration": 42.3, "width": 1920, "height": 1080, "fps": 60.0, "has_audio": true }
  ],
  "video": {
    "kind": "video",
    "clips": [
      { "id": "c1", "asset_id": "a1", "in_": 2.0, "out_": 14.2, "speed": 1.0,
        "effects": [{ "kind": "eq", "params": [["brightness", 0.03]] }] },
      { "id": "c2", "asset_id": "a1", "in_": 20.0, "out_": 30.0, "speed": 1.5,
        "effects": [] }
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
| Asset borrado / movido | Modo offline + reubicación. |
| Proyecto con fps distinto a los assets | Todo se normaliza al fps del proyecto en el render (aviso en el primer export lento). |
| Muchos clips (100+) | El canvas culling solo dibuja lo visible; el graph sigue siendo lineal. |
| Export cancelado | Parcial borrado; el proyecto no se toca. |
