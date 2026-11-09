# 39 — Easing de las claves de animación

> Implementado el **2026-11-09** sobre los keyframes de [31](31-keyframes.md),
> [33](33-escala-rotacion.md), [35](35-textos-escala-rotacion.md) y
> [36](36-color-tamano-textos.md). Añade una **curva de interpolación por clave** (`linear`,
> `ease_in`, `ease_out`, `ease_in_out`) al modelo `core/animation.py`. **`SCHEMA_VERSION` pasa a
> 13** (migración v12 → v13 sin cambios visibles). El preview y el export comparten la misma
> función: `Animation.value_at` para la geometría que se hornea y `Animation.expr` para las
> expresiones de `overlay`/`drawtext`.

## Qué se puede hacer

- Elegir el easing de las **nuevas claves** de X/Y/opacidad/escala/rotación/tamaño desde el
  inspector de elemento y de texto.
- Mezclar curvas en la misma animación: cada tramo usa el easing de su clave **izquierda**.
- Volver a grabar una clave en el mismo instante para **cambiar su easing** sin duplicarla.
- Mantener intactos los proyectos anteriores: sin `easing` se abre en `linear` y las cadenas de
  FFmpeg son **byte a byte** las mismas.
- Animar escala/rotación/tamaño con easing: el bake RGBA evalúa `value_at`, así que la curva
  también se aplica a los canales que no se pueden expresar en `drawtext`.

## Curvas

`ease(k, kind)` recibe `k` 0…1 (fracción del tramo) y devuelve la fracción interpolada:

| `kind` | Fórmula | 0.25 | 0.5 | 0.75 |
|---|---|---|---|---|
| `linear` | `k` | 0.25 | 0.5 | 0.75 |
| `ease_in` | `k³` | 0.015625 | 0.125 | 0.421875 |
| `ease_out` | `1-(1-k)³` | 0.578125 | 0.875 | 0.984375 |
| `ease_in_out` | `4k³` si `k<0.5`; `1-(2-2k)³/2` si no | 0.0625 | 0.5 | 0.9375 |

- Son cúbicas: arrancan/frenan suave y no se extrapolan fuera de 0…1.
- `ease_in_out` es simétrica, así que en el punto medio vale lo mismo que la lineal (0.5). Para
  notar la diferencia hay que muestrear fuera del centro (0.25/0.75).
- `linear` conserva exactamente la fórmula histórica; no hay cambios en los goldens de
  `overlay`/`drawtext`.

## Modelo (`SCHEMA_VERSION = 13`)

```python
Keyframe(
    t=0.0,                 # segundos locales al elemento/texto
    value=0.2,
    easing="ease_in",      # linear | ease_in | ease_out | ease_in_out
)
Animation("x", (
    Keyframe(0.0, 0.2, "ease_in"),
    Keyframe(1.0, 0.8, "linear"),  # su easing no tiene tramo saliente
))
```

- `easing` vive en **cada punto**, no en `Animation`: dos tramos consecutivos pueden usar curvas
  distintas.
- `Keyframe.__post_init__` valida el valor contra `EASING_KINDS`; un valor desconocido lanza
  `ProjectError`.
- `Animation.value_at(t, default)` acota a la primera/última clave y, entre dos puntos, aplica
  `ease((t - a.t) / (b.t - a.t), a.easing)`. El último punto no tiene tramo saliente; su easing se
  conserva para no perderlo al volver a grabar.
- `set_keyframe(animations, channel, t, value, easing="linear")` reemplaza la clave del mismo
  instante, incluido su easing. Los llamadores existentes sin `easing` siguen creando claves
  lineales.
- `Animation.expr(start, default)` genera la misma curva para FFmpeg. `linear` mantiene la
  expresión histórica; las demás usan multiplicaciones (sin `pow`) para que el parser de FFmpeg
  evalúe la misma fórmula que Python.

## Serialización y migración

- `to_dict` escribe `"easing"` en **todos** los puntos:
  ```json
  "animations": [
    {"channel": "x", "points": [
      {"t": 0.0, "value": 0.2, "easing": "ease_in"},
      {"t": 1.0, "value": 0.8, "easing": "linear"}
    ]}
  ]
  ```
- `from_dict` acepta la ausencia de `easing` y usa `"linear"`.
- `store._migrate_v12_v13` es un no-op: un proyecto v12 se abre con todas las claves lineales y
  se ve exactamente igual. La migración no reescribe los puntos.

## Preview y export

- **Preview**: `core.elements.effective` y `core.timeline.text_values`/`text_style_values` llaman a
  `Animation.value_at`, así que X/Y/opacidad y los canales horneados (escala/rotación/tamaño) usan
  la curva elegida.
- **Export**: `overlay` y `drawtext` reciben `Animation.expr`; el tramo lineal es el mismo string
  de siempre. Ejemplo con `ease_in` entre 0.2 y 0.8:
  ```
  if(lt(t,0),0.2,if(lt(t,1),(0.2+(0.8-0.2)*((t-0)/1)*((t-0)/1)*((t-0)/1)),0.8))
  ```
  `ease_out` usa `(1-(1-k)*(1-k)*(1-k))` y `ease_in_out` un `if(lt(k,0.5),4k³,1-(2-2k)³/2)`
  anidado.
- La expresión se evalúa por frame, igual que antes; los valores se acotan a la primera/última
  clave con el `if(lt(t,…))` exterior.
- **Paridad**: no hay una segunda fórmula. Los canales con bake (`services/element_animation`,
  `services/text_animation`) también leen `value_at`, así que una escala o un tamaño con easing se
  hornea con la curva correcta.

## UI

- Inspector de elemento/texto → sección **Animación** → fila **Easing** (`insp-easing`):
  - Lineal (`linear`);
  - Suave al entrar (`ease_in`);
  - Suave al salir (`ease_out`);
  - Suave entrada/salida (`ease_in_out`).
- El selector decide el easing de las **nuevas claves**; no hace falta seleccionar una clave
  existente. Los botones «Clave X/Y/…» pasan el valor a `set_keyframe`.
- Volver a grabar en el mismo instante actualiza el easing de esa clave (no crea duplicados).
- `state.insp_easing` es un `Signal` de UI **no persistido**: no viaja al `.rsproj` y no cambia el
  esquema por sí solo.
- Las claves ya existentes conservan su easing al moverlas o al cambiar de selección; para
  cambiarlo se vuelve a grabar en el mismo instante con el selector elegido.
- Las keys de UI anteriores (`insp-element-key-*`, `insp-text-key-*`, `insp-*-clear-*`) no cambian.

## Límites

- **Solo animaciones visuales** (`Element.animations` y `TextOverlay.animations`). Las envolventes
  de audio (`Clip.envelope`, `Element.envelope`, [docs/32](32-audio-envelopes.md) y
  [docs/34](34-envolventes-elementos.md)) siguen siendo **lineales**: no se amplió `VolumePoint`.
- El **color animado** de textos (`TextOverlay.color_keys`, [docs/36](36-color-tamano-textos.md))
  sigue interpolándose linealmente en sRGB; no es un `Animation`.
- No hay editor gráfico de curvas ni Bézier libres: son cuatro curvas fijas por clave.
- Las claves siguen siendo **explícitas** (no se graban al mover el playhead); el copiar/pegar
  de claves llegó en [docs/41](41-copiar-pegar-claves.md) y las asas de animación en el timeline
  en [docs/38](38-asas-animaciones.md).
- El corte rápido sigue bloqueado por elementos/textos/efectos, como antes.

## Tests y verificación

- `tests/test_easing.py` (7):
  - curvas puras y acotado de `k`, y validación del nombre;
  - `value_at` con cada easing, clave izquierda y último punto sin tramo;
  - expresión lineal idéntica a la histórica y substrings de las tres curvas;
  - round-trip con `"easing"` en cada punto y migración v12 → v13;
  - `set_keyframe` actualiza el easing en el mismo instante;
  - **preview + export real** de un PiP con `ease_in` (100 px en t=0.5 frente a 160 lineales) y de
    un texto con `ease_in_out` (90 px en t=0.25 frente a 120 lineales), comprobando las posiciones
    en el MP4;
  - **UI con `pilot`**: el desplegable elige `ease_in`/`ease_out`/`ease_in_out`, las claves nuevas
    lo guardan y volver a grabar el mismo instante lo actualiza.
- `uv run pytest -q`: **520 passed** (512 en la rama aislada, antes de integrar docs/38).
- `uv run aurora check main.py`: OK; `uv run aurora tree main.py`: sin avisos de layout.
- `git diff --check`: limpio.

## Archivos tocados

- `src/recstudio/core/animation.py` (`Easing`, `EASING_KINDS`, `ease`, `_curve_expr`,
  `Keyframe.easing`, `value_at`, `expr` y `set_keyframe`).
- `src/recstudio/core/project.py` (`SCHEMA_VERSION = 13`, serialización de `easing`).
- `src/recstudio/core/store.py` (migración v12 → v13).
- `src/recstudio/state.py` (`insp_easing`, no persistido).
- `src/recstudio/ui/editor_controller.py` (pasa el easing a `set_keyframe`).
- `src/recstudio/ui/screens/editor.py` (selector **Easing** y texto de ayuda).
- `tests/test_easing.py`, `tests/test_modelo_v6_base.py` y `tests/test_text_color_size.py`
  (esquema 13).
- `docs/39-easing.md`, `docs/15`, `docs/31`, `docs/32`, `docs/33`, `docs/35`, `docs/36`, `docs/37`,
  `docs/README.md`, `CHANGELOG.md` y `AGENTS.md`.
