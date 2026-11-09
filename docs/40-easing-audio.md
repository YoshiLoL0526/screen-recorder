# 40 — Easing de las envolventes de volumen

> Implementado el **2026-11-09** sobre las envolventes de [32](32-audio-envelopes.md) y
> [34](34-envolventes-elementos.md), reutilizando las curvas de [39](39-easing.md). Añade
> `easing` a `VolumePoint` (`linear`, `ease_in`, `ease_out`, `ease_in_out`). **`SCHEMA_VERSION`
> pasa a 14** (migración v13 → v14 sin cambios visibles) y el preview/export comparten la misma
> función de interpolación, sin una segunda fórmula.

## Qué se puede hacer

- Elegir el easing de las **nuevas claves** de volumen de un clip (`Clip.envelope`) y del audio
  de un video superpuesto (`Element.envelope`) desde el inspector.
- Mezclar curvas en la misma envolvente: cada tramo usa el easing de su clave **izquierda**.
- Volver a grabar una clave en el mismo instante para **cambiar su easing** sin duplicarla.
- Añadir claves desde el timeline en modo **Env**: también usan el easing elegido en el selector.
- Mantener intactos los proyectos anteriores: sin `easing` se abre en `linear` y las cadenas
  de FFmpeg son **byte a byte** las mismas.

## Curvas

`core.animation.ease(k, kind)` recibe `k` 0…1 (fracción del tramo) y devuelve la fracción
interpolada. La envolvente reutiliza esa función, no una copia:

| `kind` | Fórmula | 0.25 | 0.5 | 0.75 |
|---|---|---|---|---|
| `linear` | `k` | 0.25 | 0.5 | 0.75 |
| `ease_in` | `k³` | 0.015625 | 0.125 | 0.421875 |
| `ease_out` | `1-(1-k)³` | 0.578125 | 0.875 | 0.984375 |
| `ease_in_out` | `4k³` si `k<0.5`; `1-(2-2k)³/2` si no | 0.0625 | 0.5 | 0.9375 |

- El easing se aplica al **tramo que sale** de la clave; la última clave no tiene tramo saliente.
  Su easing se conserva para no perderlo si se vuelve a grabar el mismo instante.
- Con ganancias `a` y `b`, la ganancia en un instante del tramo es `a + (b-a) * ease(k)`.
  `linear` conserva exactamente la recta histórica.

## Modelo (`SCHEMA_VERSION = 14`)

```python
VolumePoint(
    t=0.0,                # segundos locales al clip/elemento
    gain=0.25,            # 0…GAIN_MAX (4.0)
    easing="ease_in",     # linear | ease_in | ease_out | ease_in_out
)
Clip(envelope=(
    VolumePoint(0.0, 1.0, "linear"),
    VolumePoint(1.0, 0.25, "ease_out"),
))
```

- `VolumePoint.__post_init__` valida el nombre contra `animation.EASING_KINDS`; un valor
  desconocido lanza `ProjectError`. Es el mismo criterio que `Keyframe` (docs/39).
- `value_at` aplica `ease((t - a.t) / (b.t - a.t), a.easing)` entre dos puntos y acota a la
  primera/última clave, igual que antes.
- `set_point(points, t, gain, easing="linear")` reemplaza la clave del mismo instante, incluido
  su easing. Los llamadores antiguos sin `easing` siguen creando claves lineales.
- `slice_envelope` (dividir, recortar o separar audio) conserva el easing de los puntos que
  sobreviven y el ancla inicial hereda el de la clave que cubre el corte. Al cortar **dentro**
  de un tramo con curva, la parte restante se renormaliza con la misma curva: los extremos y la
  continuidad se conservan, pero la forma exacta cambia (ver «Límites»).

## Expresión de FFmpeg

`audio_envelope.expr` genera la misma curva que `ease`, con las mismas multiplicaciones que
`Animation.expr` (sin `pow`). La rama lineal es exactamente la de antes:

```
if(lt(t,0),1,if(lt(t,1),(1+(0.25-1)*(t-0)/1),0.25))
```

Con `ease_in` en la primera clave:

```
if(lt(t,0),1,if(lt(t,1),(1+(0.25-1)*((t-0)/1)*((t-0)/1)*((t-0)/1)),0.25))
```

- `start` desplaza las claves para el tramo audible de un clip entrante con transición
  (`start=-skip`), también dentro de la curva.
- `ease_out` usa `(1-(1-k)*(1-k)*(1-k))` y `ease_in_out` un `if(lt(k,0.5),4k³,…)` anidado.
- Una sola clave devuelve su ganancia; sin claves, el `default`.

## Preview y export

- **Export**: `core.graph._audio_steps` (V1 y pistas de audio) y
  `core.graph._element_audio_mixes` (PiP/V2/V3) siguen añadiendo
  `volume=volume='<expr>':eval=frame` después del volumen estático. Solo cambia la expresión
  cuando la clave izquierda no es lineal.
- **Preview**: `services.playback.AudioPreview` renderiza la mezcla con `build_audio`, la misma
  sección de audio del export. No hay una segunda interpolación en `_feed_segment`.
- **Paridad**: `value_at` y `expr` comparten `ease`; la curva del WAV del preview y la del MP4
  exportado coinciden. Los tests lo comprueban con audio real en 0.25/0.5/0.75 s.
- El corte rápido sigue bloqueado por cualquier envolvente (`core/keyframes.py`): `-c copy` no
  puede aplicar la curva.

## Serialización y migración

- `to_dict` escribe `easing` en **todos** los puntos de `Clip.envelope` y `Element.envelope`:
  ```json
  "envelope": [
    {"t": 0.0, "gain": 1.0, "easing": "ease_in"},
    {"t": 1.5, "gain": 0.25, "easing": "linear"}
  ]
  ```
- `from_dict` acepta la ausencia de `easing` y usa `"linear"`.
- `store._migrate_v13_v14` es un no-op: un proyecto v13 se abre con todas las claves lineales y
  suena exactamente igual. La migración no reescribe los puntos.

## UI

- Inspector de clip de video/audio → **Envolvente de volumen** → fila **Easing**
  (`insp-env-easing`).
- Inspector de un video superpuesto con audio → **Envolvente de volumen** → fila **Easing**
  (`insp-el-env-easing`).
- Opciones: Lineal, Suave al entrar, Suave al salir y Suave entrada/salida.
- El selector decide el easing de las **nuevas claves** (botón **Clave volumen** y modo **Env**
  del timeline). Volver a grabar en el mismo instante actualiza el easing de esa clave.
- `state.insp_env_easing` es un `Signal` de UI **no persistido**: no viaja al `.rsproj` ni cambia
  el esquema por sí solo. Las dos secciones comparten el mismo selector.
- Las keys existentes (`insp-env-key`, `insp-env-clear`, `insp-el-env-key`,
  `insp-el-env-clear`) no cambian. Las asas del timeline siguen moviendo tiempo/ganancia y
  conservan el easing de la clave arrastrada.

## Límites

- El easing solo se elige para **claves nuevas** o al volver a grabar en el mismo instante; no
  hay un control para cambiar el easing de una clave seleccionada sin regrabarla. Tampoco hay
  copiar/pegar claves.
- Cortar un clip dentro de un tramo curvo reancla los extremos con `ease` sobre el tramo nuevo:
  mantiene el valor en los bordes, pero no es una subcurva exacta de la original. En cortes en
  claves o en tramos lineales la curva sí es idéntica.
- La última clave no tiene tramo saliente; su easing no afecta al sonido, solo se conserva como
  metadato para futuras grabaciones.
- No hay Bézier libres ni más curvas que las cuatro de `animation.EASING_KINDS`.
- La envolvente sigue multiplicando el volumen estático del clip/elemento y el de su pista; el
  easing no cambia el ducking ni los fundidos.
- El audio de un elemento con `audio_gain=0` sigue en silencio aunque tenga curva.

## Tests y verificación

- `tests/test_audio_envelope_easing.py` (8):
  - curvas compartidas con `animation.ease` y validación del nombre;
  - `value_at` por tramo con la clave izquierda, incluida la última sin tramo;
  - `expr` lineal byte a byte, substrings de las tres curvas, desplazamiento por solape y
    última clave ignorada;
  - `set_point` actualiza el easing en el mismo instante y `slice_envelope` lo conserva;
  - round-trip de clip y elemento con `"easing"`, migración v13 → v14 y default lineal;
  - **audio real**: WAV del preview y MP4 exportado con dos claves (`1.0 → 0.1`) y `ease_in`
    frente a `linear`, muestreados en 0.25/0.5/0.75 s;
  - **UI con `pilot`**: selector de clip y de elemento, clave nueva con easing, regrabación en
    el mismo instante y clave añadida en modo Env con el easing elegido.
- `uv run pytest -q`: **528 passed**.
- `uv run aurora check main.py`: OK; `uv run aurora tree main.py`: sin avisos de layout.
- `git diff --check`: limpio.

## Archivos tocados

- `src/recstudio/core/audio_envelope.py` (`VolumePoint.easing`, `value_at`, `expr`,
  `set_point` y `slice_envelope`).
- `src/recstudio/core/project.py` (`SCHEMA_VERSION = 14` y serialización de `easing`).
- `src/recstudio/core/store.py` (migración v13 → v14).
- `src/recstudio/state.py` (`insp_env_easing`, no persistido).
- `src/recstudio/ui/editor_controller.py` (pasa el easing del selector a las claves nuevas y
  conserva el de las arrastradas).
- `src/recstudio/ui/screens/editor.py` (selector **Easing** en las dos secciones).
- `tests/test_audio_envelope_easing.py`, `tests/test_audio_envelope.py`,
  `tests/test_element_audio_envelope.py`, `tests/test_easing.py`,
  `tests/test_modelo_v6_base.py` y `tests/test_text_color_size.py`.
- `docs/40-easing-audio.md`, `docs/15`, `docs/32`, `docs/34`, `docs/37`, `docs/38`, `docs/39`,
  `docs/README.md`, `CHANGELOG.md` y `AGENTS.md`.
