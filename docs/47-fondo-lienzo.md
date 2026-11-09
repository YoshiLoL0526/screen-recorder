# 47 — Fondo del lienzo: color, desenfoque del video e imagen

> Implementado sobre la base v15 (`22220c5`, 579 tests; docs/43–45). Sube el esquema a **v16**.
> Es una de las tres features paralelas de esta tanda; no toca las asas de audio ni el modal de
> export.

## Resumen

| Pieza | Dónde |
|---|---|
| Modelo v16: `background_mode`, `background_blur`, `background_asset` | `core/project.py` |
| Migración v15 → v16 (no-op) | `core/store.py` |
| Modo efectivo, fallback a color, geometría «cover» y radio escalado | `core/background.py` |
| Operaciones puras del fondo y protección del asset en uso | `core/timeline.py` |
| Fondo antes de V1 en el graph (`split` + `boxblur` + `overlay` / imagen) | `core/graph.py` |
| Fondo en el preview (PIL `GaussianBlur` / imagen enderezada) | `services/playback.py` |
| Imagen del fondo como archivo necesario | `services/export.py` |
| Generación personalizada bloqueada por fondo especial | `core/transform.py`, `core/keyframes.py` |
| UI del inspector y diálogo nativo | `state.py`, `ui/editor_controller.py`, `ui/screens/editor.py`, `ui/file_dialogs.py` |
| Tests | `tests/test_background.py` |

El fondo vive **detrás de V1**: V2/V3, los efectos globales, la capa «Elementos» y los textos se
siguen aplicando encima y en el mismo orden. Sin fondo especial (`color`) el graph es **byte a
byte** el de antes (goldens de F4).

## Modelo v16

```python
SCHEMA_VERSION = 16
BACKGROUND_MODES = ("color", "blur", "image")
BACKGROUND_BLUR_MIN, BACKGROUND_BLUR_MAX = 0, 100
DEFAULT_BACKGROUND_BLUR = 24

@dataclass(frozen=True)
class Project:
    ...
    background: str = "#000000"          # color #RRGGBB (como en v4)
    background_mode: Literal["color", "blur", "image"] = "color"
    background_blur: int = 24            # 0…100, px del proyecto
    background_asset: str | None = None  # id de un asset de tipo imagen
```

Reglas:

- `background_mode` debe ser `color`, `blur` o `image`; `background_blur` es un **entero** de
  0 a 100 (un `bool` o un float se rechazan) y `background_asset` es `None` o un id.
- `background_asset` **no se valida contra `assets`** en `Project.validate()`: si el id no
  existe o apunta a un video, el fondo efectivo cae a `color`. Así un proyecto que perdió la
  imagen se sigue abriendo (y se avisa en la UI), en vez de romper la carga.
- El color `background` se conserva siempre: es el fondo del modo `color` y el respaldo del
  modo `image` cuando falta la imagen.
- `to_dict` escribe los tres campos; `from_dict` los lee con defaults (`color`, `24`, `None`).
  Los proyectos v15 y anteriores se abren idénticos.

### Migración v15 → v16

`store.migrate` añade `_migrate_v15_v16`, un paso **no-op**: los campos ausentes los rellena
`from_dict`. El JSON de un proyecto con fondo de imagen se ve así:

```json
{
  "schema_version": 16,
  "background": "#000000",
  "background_mode": "image",
  "background_blur": 24,
  "background_asset": "a-1a2b3c4d"
}
```

## Modo efectivo y geometría compartida

`core/background.py` es puro y lo consumen preview y export:

- `effective_mode(project)`: `blur` se mantiene; `image` solo si `image_asset(project)` devuelve
  un `MediaAsset` con `kind == "image"`; en cualquier otro caso, `color`.
- `has_special_background(project)`: `effective_mode != "color"`.
- `blur_radius(project, canvas_height)`: `background_blur × canvas_height / project.height`.
  La intensidad se guarda en píxeles del proyecto, así que el preview a 540p y el preset 720p
  ven el mismo desenfoque relativo.
- `cover_crop(src_w, src_h, dst_w, dst_h)`: recorte centrado de una imagen escalada para
  **cubrir** el lienzo (la misma regla que `scale=…:force_original_aspect_ratio=increase,crop=…`).

## Preview

En el camino nuevo de `FrameRenderer._clip_frame` (el que ya usan la transformación, el fondo
no negro o la auto-rotación) se sustituye el relleno plano del lienzo por
`playback.background_canvas(project, size, rgb)`:

1. **color**: `np.empty` + `tx.hex_to_rgb(project.background)` (idéntico a antes).
2. **blur**: el frame del clip ya encajado (`out_width × out_height`) se escala a cover, se
   recorta al lienzo y se desenfoca con `PIL.ImageFilter.GaussianBlur(radius)`. El radio sale de
   `background.blur_radius` y se redondea a píxeles del preview.
3. **image**: la imagen del asset se abre con PIL, se endereza con `ImageOps.exif_transpose`,
   se escala a cover y se recorta. La caché en memoria es por `(ruta, mtime, ancho, alto)` y se
   limita a 8 entradas; una imagen ausente o ilegible cae al color.

Después se dibuja el clip normal con `lay.x`/`lay.y` y su recorte contra los bordes, y se
aplican `eq` y los fundidos sobre el lienzo ya compuesto. En una transición se componen los dos
frames (cada uno con su fondo) y se mezclan con `blend`, igual que el `xfade` del export.

El escenario de Aurora sigue pintando `Project.background` como respaldo, pero la imagen del
preview ya trae el fondo compuesto; no hay dos fondos visibles.

## Export

El graph coloca el fondo **antes** de la composición de V1 y dentro de la cadena de cada clip:

### Blur

```text
[0:v]setpts=…,scale=OUT:OUT,split=2[bs0][fg0];
[bs0]scale=W:H:force_original_aspect_ratio=increase,crop=W:H,
     boxblur=luma_radius=R:luma_power=2[bg0];
[bg0][fg0]overlay=x=LAY_X:y=LAY_Y:format=auto:shortest=1,
     eq…,fps=…,tpad…,trim=end_frame=…,fade…,format=yuv420p,setsar=1[v0]
```

- El `split` es por clip: la copia de fondo sale del **mismo frame** que se va a dibujar, así
  que no hay desfase ni frames negros.
- `overlay` sustituye al `pad`/`crop` histórico; admite offsets negativos (un `fill` o una
  escala mayor que el lienzo) y recorta contra los bordes.
- `R` se acota a `min(W, H) // 2 − 1` y, si es 0, no se añade `boxblur`.
- `shortest=1` termina el overlay cuando acaba el clip; `tpad` + `trim` garantizan el número
  entero de frames de siempre.

### Imagen

Una sola entrada de imagen, después de los clips para no cambiar sus índices:

```text
-loop 1 -framerate FPS -t DURACIÓN+1 -i fondo.png
```

y una cadena que la prepara una vez y la reparte con `split`:

```text
[2:v]scale=W:H:force_original_aspect_ratio=increase,crop=W:H,setsar=1,
     fps=FPS,setpts=PTS-STARTPTS,split=2[bg0][bg1];
[0:v]setpts=…,scale=OUT:OUT[fg0];
[bg0][fg0]overlay=x=…:y=…:format=auto:shortest=1,…[v0]
```

Un solo clip no necesita `split`: su cadena de imagen termina directamente en `[bg0]`.

### Garantías

- **Modo color**: `background_mode == "color"` (o `image` sin asset válido) usa exactamente la
  cadena histórica. El golden de F4 y los tests de `test_f4_graph.py` no cambian.
- **Transiciones**: el fondo se compone dentro de cada clip, antes del `concat`/`xfade`; el
  `xfade` mezcla los dos clips ya compuestos. Un export real con fundido y blur se decodifica
  frame a frame en los tests para comprobar que no hay negros.
- **Distintos fits**: el overlay usa el `Layout` de cada clip (`fit`, `fill`, `stretch`,
  posición y escala), así que las barras y los recortes laterales siguen la misma geometría.
- **Cambios de resolución**: el fondo se escala al tamaño de **salida** (preset personalizado o
  720p), no al del proyecto; el radio de blur se escala con el alto de salida.
- **Audio**: no se toca. `build_audio` no compone video y no abre la imagen del fondo.

## UI

En el inspector sin selección, sección **Proyecto**:

- **Fondo**: `Color`, `Desenfoque del video` o `Imagen`.
- Modo color: el selector de color y el campo Hex actuales (sin cambios).
- Modo blur: **Intensidad** (`number_input` 0…100, paso 1).
- Modo imagen: **Elegir imagen…** (diálogo nativo `file_dialogs.open_images` desde el hilo de
  UI), el nombre del archivo y **Quitar** (vuelve a color). Sin imagen se muestra
  «Sin imagen: se usa el color del lienzo»; si el archivo está offline, el aviso lo indica.
- El probe de la imagen va en un hilo; el proyecto se aplica con un solo `store.apply`
  (`imagen de fondo`, `fondo del lienzo`, `desenfoque del fondo`, `quitar imagen de fondo`), así
  que cada gesto es **un undo**.
- `state.proj_bg_mode` y `state.proj_bg_blur` son `Signal` de UI no persistidos; el estado real
  vive en `Project`. `_sync_inspector` los rellena y los handlers ignoran el eco comparando con
  el proyecto.
- Si el asset ya estaba en el proyecto (misma ruta), se reutiliza; el asset de fondo **no se
  puede quitar** con «Quitar» del panel de medios mientras siga referenciado.

## Límites

- El fondo de imagen siempre es **cover** (cubre y recorta centrado); no hay `contain` ni
  posición/zoom propios. El color sigue siendo la única opción para letterbox con un color
  distinto del negro si se quiere evitar el recorte.
- El blur es isotrópico y aproximado: el preview usa `GaussianBlur` de PIL y el export
  `boxblur` de FFmpeg. La intensidad relativa coincide, pero no son el mismo algoritmo píxel a
  píxel (como ya pasa con el blur global, docs/26). Con intensidad 0 el fondo sigue siendo la
  copia del clip escalada a cover, sin desenfoque.
- El blur siempre sale del frame de V1; no se puede desenfocar V2/V3 ni un elemento.
- Si la imagen está offline, el preview cae al color y el export falla con el aviso de
  `services/export.offline_assets` (que ya incluye la imagen del fondo).
- La imagen del fondo no se usa para el «corte rápido»: el modo no-color bloquea ese export.
- Cambiar el tamaño del lienzo no reescala `background_blur` (se interpreta en píxeles del nuevo
  lienzo, igual que el blur global).

## Tests y verificación

`tests/test_background.py` (13):

- modelo v16: defaults, validación de modo/intensidad/asset, round-trip y operaciones puras;
- migración v15 → v16 y fallback a color si el asset falta o es un video;
- geometría cover, radio escalado y `offline_assets`;
- golden del graph por defecto, cadenas de blur/imagen, transición y fits distintos;
- preview sintético con barras: color, blur e imagen (esquinas y centro), imagen ausente;
- export real: color/blur/imagen comparando esquinas y franjas, y export con transición + blur
  sin frames negros;
- UI con `pilot`: modos, intensidad, elegir/quitar imagen, undo y modo imagen sin asset.

Verificación de la tanda:

```text
uv run pytest -q                 # 592 passed
uv run aurora check main.py      # ok, sin avisos
uv run aurora tree main.py       # sin avisos
git diff --check                 # limpio
```

## Riesgos de integración

- `core/project.py`, `core/store.py`, `core/graph.py`, `services/playback.py` y el inspector son
  puntos de conflicto con las otras dos features paralelas (fundidos de audio y GIF/WebM); este
  cambio no toca las asas de audio ni el modal de export.
- `SCHEMA_VERSION` sube a 16; cualquier rama paralela que también suba el esquema tendrá que
  renumerar su migración al integrarse.
