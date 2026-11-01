# 20 — Fase 2: lienzo configurable y transformación del frame

> Implementado el **2026-10-31** sobre el commit de las fases 0/1 (`f2b2af9`). Cubre la fase 2 de
> [18 — Herramientas del editor](18-herramientas-editor.md): lienzo del proyecto, modos de encaje,
> transformación por clip, auto-rotación y paridad preview/export. No incluye las fases 3+ (flujo,
> portapapeles, razor, marcadores, loop, rango de export, PiP, keyframes, subtítulos, imágenes,
> GIF/WebM ni presets de export personalizados).

## Resumen

| Pieza | Estado | Dónde |
|---|---|---|
| Esquema v4: `Project.background`, `Project.default_fit`, `Clip.transform` | ✅ | `core/project.py`, `core/store.py` |
| Migración v3 → v4 (y v1/v2 a través de v3) | ✅ | `core/store.py`, tests |
| Operaciones puras de lienzo y transformación | ✅ | `core/timeline.py` |
| Matemática compartida de fit/fill/stretch, crop, rotación, flip y posición | ✅ | `core/transform.py` |
| Graph con fondo, transformación y `output_size` por aspecto | ✅ | `core/graph.py` |
| Preview con la misma geometría y color de fondo | ✅ | `services/playback.py` |
| Auto-rotación de móviles (probe, preview y export) | ✅ | `services/media.py`, `core/graph.py`, `services/playback.py` |
| Corte rápido bloqueado con lo nuevo | ✅ | `core/keyframes.py` |
| Ajustes de proyecto en el inspector (presets/custom, fit, fondo, FPS) | ✅ | `ui/screens/editor.py`, `ui/editor_controller.py`, `state.py` |
| Inspector de clip → Transformación (fit, escala, X/Y, rotación, flips, crop, reset) | ✅ | ídem |
| Tests puros, graph, preview sintético, export real y UI con `pilot` | ✅ | `tests/test_fase2_frame.py`, `tests/test_fase2_frame_ui.py` |

**267 → 288 tests** (`uv run pytest -q`): 21 nuevos entre modelo/migración, operaciones, layout,
graph, preview con cuadrantes de color, export real (vertical con fill, rotado y con transición),
corte rápido y UI. Los 267 anteriores siguen verdes; los golden del graph histórico no cambian.

## Modelo v4

```python
SCHEMA_VERSION = 4

@dataclass(frozen=True)
class Transform:
    fit: Literal["inherit", "fit", "fill", "stretch"] = "inherit"
    scale: float = 1.0                 # 0.25…4, relativa al encaje
    x: float = 0.5                     # centro en fracción del lienzo
    y: float = 0.5
    rotation: int = 0                  # 0 | 90 | 180 | 270 (horario)
    flip_h: bool = False
    flip_v: bool = False
    crop: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)  # l, t, r, b

@dataclass(frozen=True)
class Clip:
    ...
    transform: Transform | None = None

@dataclass(frozen=True)
class Project:
    ...
    background: str = "#000000"        # #RRGGBB
    default_fit: Literal["fit", "fill", "stretch"] = "fit"
```

Reglas y validación:

- `Transform.__post_init__` valida `fit`, `0.25 ≤ scale ≤ 4`, `0 ≤ x, y ≤ 1`, rotación en
  `{0, 90, 180, 270}` y `crop` con `0 ≤ l < r ≤ 1` y `0 ≤ t < b ≤ 1` (4 valores, no NaN).
- `Project.validate()` valida `background` como `#RRGGBB` y `default_fit` en
  `{fit, fill, stretch}`. El tamaño sigue exigiendo ancho/alto positivos; `set_project_size`
  añade el rango de edición (16…7680, par).
- `Clip.transform is None` significa **sin transformación propia**: el clip usa
  `Project.default_fit` y el resto de valores neutros. Con `default_fit="fit"` y fondo negro es
  exactamente el comportamiento histórico de F4. Un `Transform` explícito que queda neutro se
  normaliza a `None` en `tl.set_transform` para no bloquear el corte rápido sin motivo.
- `to_dict` escribe siempre `background`, `default_fit` y `"transform"` (objeto o `null`);
  `from_dict` acepta campos ausentes (tolerancia con v1/v2/v3).

### Migración

`store.migrate` aplica, en orden, `_migrate_v2_v3` y `_migrate_v3_v4`:

- v1/v2 → v3: rellena `Clip.start/audio_enabled/link_id` y `Track.mode/name/solo/locked`
  (lo ya documentado en [19](19-fase-0-1-audio.md)).
- v3 → v4: `background="#000000"`, `default_fit="fit"` y `transform=None` en cada clip.
- Un archivo con `schema_version > 4` se abre en modo lectura, como antes.

Los proyectos v1/v2/v3 se abren idénticos: fondo negro, encaje `fit`, sin transformación y con
las mismas dimensiones. El JSON de la fase 2 añade, por ejemplo:

```json
{
  "schema_version": 4,
  "width": 1080, "height": 1920,
  "background": "#204060", "default_fit": "fill",
  "video": {
    "clips": [
      {
        "id": "c1", "asset_id": "a1", "in_": 2.0, "out_": 14.2, "speed": 1.0,
        "transform": {
          "fit": "inherit", "scale": 1.0, "x": 0.5, "y": 0.5,
          "rotation": 90, "flip_h": false, "flip_v": false,
          "crop": [0.1, 0.0, 0.9, 1.0]
        }
      }
    ]
  }
}
```

## Operaciones puras (`core/timeline.py`)

| Función | Qué hace |
|---|---|
| `set_project_size(p, width, height)` | Cambia el lienzo sin tocar clips ni transiciones. Exige enteros en 16…7680; las dimensiones impares se redondean hacia abajo a par. |
| `set_project_fit(p, fit)` | Cambia el encaje por defecto (`fit`/`fill`/`stretch`). |
| `set_project_background(p, color)` | Cambia el color del lienzo; acepta `RRGGBB` y normaliza a `#RRGGBB` mayúsculas. |
| `set_transform(p, clip_id, **changes)` | Fusiona los campos en la transformación del clip de video; valida y normaliza el neutro a `None`. |
| `reset_transform(p, clip_id)` | Quita la transformación propia del clip. |

La UI aplica cada cambio con un único `store.apply(...)`, así que cada gesto/ajuste es **un solo
undo**. Cambiar el lienzo no modifica `in_/out_`, orden, velocidad ni transiciones: el render
vuelve a normalizar contra el nuevo tamaño.

## Semántica exacta del render

Orden de la cadena por clip (idéntico en preview y export):

```
auto-rotación del asset (display matrix)
  → rotación del clip (horaria) → flip H → flip V
  → crop normalizado (l, t, r, b)
  → fit/fill/stretch + scale
  → posición (x, y) sobre el lienzo → fondo
  → eq → fundido → (después de concatenar/xfade) textos
```

Modos de encaje:

- **`fit`**: escala uniforme `min(W/cw, H/ch)` manteniendo el aspecto. Si sobra lienzo, se rellena
  con `Project.background`.
- **`fill`**: escala uniforme `max(W/cw, H/ch)` hasta cubrir el lienzo. El excedente se recorta
  contra los bordes; con el centro por defecto (0.5, 0.5) el recorte es simétrico.
- **`stretch`**: escala exactamente a `W × H` (sin mantener el aspecto); `scale` multiplica ese
  tamaño y la posición sigue siendo el centro en fracciones del lienzo.
- En los tres modos `scale` es relativa al encaje: `1.0` = encaje puro; `0.5` reduce a la mitad;
  `2.0` duplica. `x`/`y` son el **centro** del clip en fracciones del lienzo; si el clip se sale,
  se recorta contra el lienzo y si deja huecos se rellena con el color de fondo.

`core/transform.py` calcula un `Layout` con las dimensiones de origen (después de la rotación del
clip), el rectángulo de crop en píxeles, el tamaño escalado y la esquina superior izquierda (que
puede ser negativa). El graph y el preview consumen ese mismo objeto/fórmulas; no hay dos
implementaciones de la geometría.

### Cadena histórica intacta

Si `clip.transform is None`, `default_fit == "fit"`, `background == "#000000"` y el asset no tiene
auto-rotación, el graph usa **exactamente** la cadena de F4:

```
scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:-1:-1:color=black
```

Los golden de `tests/test_f4_graph.py` no cambian. En cuanto hay transformación, fondo no negro,
encaje distinto o auto-rotación, se usa la cadena nueva:

```
scale=OUT_W:OUT_H
pad=PW:PH:PX:PY:color=0xRRGGBB
crop=W:H:OX:OY        # solo si el clip se sale del lienzo
```

El `pad`+`crop` cubre los cuatro casos (clip más pequeño, más grande, desplazado o centrado) sin
introducir una segunda entrada de video; así `xfade`/`concat` siguen recibiendo el mismo tamaño,
fps, formato, SAR y timebase. `eq` va después de la composición en la cadena nueva; los fundidos
siguen al `trim` (sobre el lienzo ya compuesto). En la cadena histórica `eq` conserva su posición
previa a la normalización, porque es un filtro por píxel y así no se altera ningún golden.

### `output_size` y textos

`graph.output_size` ya respetaba el aspecto del proyecto; ahora se prueba también en vertical:
un proyecto 1080×1920 con el preset 720p sale 404×720 (el par más cercano al aspecto). Los textos
se siguen quemando al final con `drawtext`, con posición relativa (`x/y` en fracción) y tamaño
escalado al alto de salida (`size × output_height / project.height`). El preview hace lo mismo
con PIL sobre el frame ya compuesto.

## Auto-rotación: investigación y decisión

`services.media.probe` lee la rotación real del stream con PyAV. La API de PyAV 19 no expone la
display matrix en el `Stream`, pero el **primer frame decodificado** trae `frame.rotation`; se
normaliza a `0/90/180/270` y se guarda en `MediaAsset.rotation`. Si la rotación es 90/270, `probe`
**intercambia `width`/`height`**: las dimensiones de `MediaAsset` son las de visualización.

Se comprobó empíricamente con FFmpeg 7.1 y un video sintético (`-display_rotation 90/270/180`):

- FFmpeg **auto-rota por defecto** al abrir una entrada con display matrix (`transpose=2` para
  +90, `transpose=1` para −90/270, `hflip,vflip` para 180).
- Con `-noautorotate` + `transpose` explícito, el MP4 de salida **conserva la display matrix** de
  la entrada; al reproducirlo se gira otra vez (doble rotación). `-metadata:s:v rotate=0` y
  `-map_metadata -1` no la quitan.
- Dejando la auto-rotación de FFmpeg, la salida queda con rotación 0 y el contenido ya enderezado.

**Decisión:** el export deja que FFmpeg auto-rote la entrada y **no** añade `-noautorotate` ni un
`transpose` por asset. El preview, que usa PyAV y no aplica la display matrix, hace la rotación
explícita (`np.rot90`) antes de la transformación del clip. Así preview y export coinciden y el
MP4 no lleva una rotación duplicada. La rotación **del clip** (`Transform.rotation`) sí se aplica
explícitamente en ambos (horaria: `transpose=1/2` según el caso y `np.rot90` en preview).

El proxy 540p se genera con `-noautorotate` y una clave de caché que incluye la rotación
(`…-r90.mp4`), para que el preview aplique la rotación una sola vez y no reutilice proxies viejos
generados con auto-rotación. Los pósters y el filmstrip mostraban la orientación codificada; se
enderezan desde la fase 3 ([21](21-fase-3-flujo.md)), con la caché invalidada.

## Preview (`services/playback.py`)

- **Camino histórico** (sin lo nuevo): se conserva el decode escalado, zoom, eq, letterbox negro y
  fundido, sin copias extra.
- **Camino nuevo**: se pide al decoder el tamaño necesario para que el crop llene el tamaño de
  salida (sin superar la resolución nativa), se aplica la auto-rotación, la rotación/flip del
  clip, el crop, el zoom heredado, el escalado y la composición sobre un lienzo del color de
  fondo. Después van `eq` y el fundido. El canvas del preview tiene el aspecto del proyecto
  (`preview_size`), así que un cambio a 9:16 se ve inmediatamente.
- El fondo se pinta con `Project.background`; el panel exterior del preview sigue siendo negro
  (es el marco de la UI, no el lienzo).
- Rendimiento (RNF-04): el camino histórico no cambia; el nuevo solo se activa con lo de la fase 2
  y reutiliza el decoder/proxy. Las rotaciones son vistas de numpy y la copia a PIL se hace una
  vez por frame cuando hay que redimensionar.

## Corte rápido (`core/keyframes.blockers`)

Se añaden motivos cuando:

- el lienzo del proyecto no coincide con la resolución de visualización del primer asset
  (`cambia el tamaño del lienzo`);
- `background != "#000000"` o `default_fit != "fit"` (`cambia el fondo o el encaje del lienzo`);
- algún clip tiene `transform` (`hay transformaciones de clip`);
- algún asset tiene auto-rotación (`hay videos con rotación automática`).

El corte rápido copia paquetes y no puede aplicar ninguno de esos cambios; bloquearlo es la
opción segura. Los proyectos históricos (negro, fit, sin transformación ni rotación) siguen
admitiéndolo.

## UI

### Ajustes del proyecto (inspector sin selección)

- **Lienzo**: preset 1920×1080 (16:9), 2560×1440, 3840×2160, 1080×1920 (9:16), 1080×1080 (1:1),
  1080×1350 (4:5) y **Personalizado**.
- **Ancho** y **Alto** editables (texto) para el modo personalizado; el preset pasa a
  «Personalizado» cuando no coincide con ninguno.
- **Encaje**: Encajar / Rellenar / Estirar.
- **Fondo**: Negro, Blanco, Gris, Grafito o un `#RRGGBB` en el campo Hex.
- **FPS**: el selector que ya existía, integrado en la misma sección.
- Cambiar el lienzo no mueve clips; el preview se re-renderiza con el nuevo tamaño y el resumen de
  export (`w×h`) sale de `graph.output_size`.

### Inspector de clip → Transformación

- Encaje (Heredar / Encajar / Rellenar / Estirar), escala (0.25…4), posición X/Y, rotación
  0/90/180/270, volteo H/V y recorte L/T/R/B normalizado (texto, 0…1).
- «Restablecer transformación» vuelve a `transform=None` (heredar del proyecto y neutro).
- Cada cambio es un `store.apply("transformación", …)`: un solo undo. Los valores inválidos no
  tocan el proyecto y muestran un aviso.
- **Limitación**: no hay arrastre ni recorte directo sobre el preview en esta fase; los controles
  numéricos cubren el caso y la geometría ya está centralizada para añadirlo después.

## Tests y verificación

- `tests/test_fase2_frame.py` (18): modelo/defaults/validación, round-trip, migración v3 → v4,
  operaciones de lienzo/transformación, layout fit/fill/stretch/crop/rotación/flip, graph
  (fill/stretch/background/auto-rotación/textos/720p), preview con cuadrantes sintéticos
  (fit/fill/stretch/crop/rotación/flip/posición), auto-rotación preview↔export, export real
  vertical con fill, export con transición + transformación y bloqueos del corte rápido.
- `tests/test_fase2_frame_ui.py` (3): presets y tamaño personalizado, fit/fondo, preview con el
  nuevo tamaño, transformación con undo/crop inválido, reset y guardar/reabrir.
- `uv run pytest -q`: **288 passed**.
- `uv run python tools/f3_editor_test.py --no-shots`: OK (preview a 25.6 fps, guardar/reabrir
  idéntico).
- `uv run python tools/f4_export_test.py --skip-long --no-shots`: OK (export con transiciones y
  efectos, A/V en sync ±10 ms, corte rápido byte a byte).

## Límites y pendientes

- ~~El filmstrip y los pósters de assets rotados muestran la orientación codificada.~~ Resuelto en
  la fase 3 ([21](21-fase-3-flujo.md)).
- No hay arrastre/recorte interactivo sobre el preview (fase 2 lo deja como controles numéricos).
- Solo hay un fondo de color sólido; el fondo desenfocado (blur) y la imagen de fondo quedan para
  más adelante (C8 parcial).
- El lienzo no tiene presets sociales automáticos de export (E9) ni export por rango (fase 3/5).
- El `scale` del clip es estático: no hay keyframes ni animación (fase 4).
- El zoom heredado (`Effect("zoom")`) sigue existiendo por compatibilidad; se aplica después del
  crop de la transformación y antes del encaje. La UI mantiene ambos controles para no romper
  proyectos antiguos.
- Riesgo conocido: FFmpeg podría no auto-rotar formatos exóticos sin display matrix estándar; en
  esos casos `probe` devuelve 0 y no se gira (igual que antes). Los MP4/MOV de móvil, que son el
  caso real, están cubiertos por el test sintético.
