# 21 — Fase 3: flujo de edición

> Implementado el **2026-11-01** sobre el commit de la fase 2 (`22db193`). Cubre la fase 3 de
> [18 — Herramientas del editor](18-herramientas-editor.md): portapapeles y duplicar, marquesina
> que mueve, razor, marcadores, In/Out y loop, export de rango y atajos de flujo (J/K/L, Q/W),
> más el arreglo del filmstrip/pósters de assets rotados que quedó pendiente en
> [20](20-fase-2-frame.md). No incluye el panel de medios ni el drag & drop desde el Explorador,
> ni las fases 4+ (PiP, keyframes, subtítulos, GIF/WebM, presets de export personalizados).

## Resumen

| Pieza | Estado | Dónde |
|---|---|---|
| Esquema v5: `Project.markers`, `Project.range_in`, `Project.range_out` | ✅ | `core/project.py` |
| Migración v4 → v5 (y v1–v3 a través de las anteriores) | ✅ | `core/store.py`, tests |
| Portapapeles (copiar/cortar/pegar/duplicar) de video, audio y textos | ✅ | `core/timeline.py`, `ui/editor_controller.py` |
| Marquesina que mueve la selección en bloque (un undo, bloqueo y snap) | ✅ | `core/timeline.move_block`, controller, `ui/timeline.py` |
| Razor visible, excluyente con la marquesina, por pista o en todas | ✅ | `core/timeline.razor/split_everything`, `ui/timeline.py`, barra del timeline |
| Marcadores: añadir, dibujar en la regla, navegar, renombrar, borrar y snap | ✅ | `core/timeline.py`, `ui/timeline.py`, inspector |
| In/Out del timeline (distintos de `mark_in`/`mark_out` del clip) | ✅ | `core/timeline.py`, transporte |
| Loop (In/Out o todo el timeline) y velocidad/sentido en `Player` | ✅ | `services/playback.py` |
| Export de rango en el modal (render y corte rápido) | ✅ | `core/graph.py`, `core/keyframes.py`, `services/export.py`, `ui/export_modal.py` |
| J/K/L con velocidad creciente y marcha atrás | ✅ | controller + `Player.set_rate` |
| Q/W: recortar hasta el playhead (ripple en video) | ✅ | `core/timeline.trim_to` |
| A17: zoom que mantiene el playhead a la vista | ⛔ (límite de Aurora 0.9) | ver «Límites» |
| Filmstrip y pósters de assets rotados enderezados (caché invalidada) | ✅ | `services/media.py` |
| Tests puros, player, graph, export real y UI con `pilot` | ✅ | `tests/test_fase3_flujo.py`, `tests/test_fase3_flujo_ui.py` |

**288 → 320 tests** (`uv run pytest -q`): 25 en `test_fase3_flujo.py` (modelo, migración,
operaciones, player, graph, keyframes, export real y miniaturas rotadas) y 7 en
`test_fase3_flujo_ui.py` (atajos y modos con la app real). Los golden de `tests/test_f4_graph.py`
no cambian: sin rango, el graph es idéntico byte a byte.

## Modelo v5

```python
SCHEMA_VERSION = 5

@dataclass(frozen=True)
class Marker:
    id: str                       # prefijo "k"
    t: float                      # segundos del timeline, redondeado a frame, ≥ 0
    name: str = ""

@dataclass(frozen=True)
class Project:
    ...
    markers: tuple[Marker, ...] = ()        # ordenados por (t, id)
    range_in: float | None = None           # In/Out del timeline (no del clip)
    range_out: float | None = None
```

- `Marker.__post_init__` rechaza `t` NaN o negativo. `Project.validate()` exige ids de marcador
  únicos, `range_in`/`range_out` no NaN ni negativos y `range_out > range_in` si están los dos.
- `range_in`/`range_out` pueden ir sueltos: sin In el rango empieza en 0 y sin Out acaba al final
  (`tl.play_range`). Un rango vacío (In ≥ total) vuelve a «todo el timeline».
- `to_dict` escribe siempre `markers`, `range_in` y `range_out`; `from_dict` tolera que falten y
  ordena los marcadores.

### Migración

`store.migrate` encadena `_migrate_v2_v3`, `_migrate_v3_v4` y `_migrate_v4_v5`; la última solo
hace `setdefault` de `markers=[]`, `range_in=None` y `range_out=None`. Los proyectos v1–v4 se
abren idénticos (mismo timeline, sin marcadores ni rango). Un archivo con `schema_version > 5` se
sigue abriendo en modo lectura.

## Operaciones puras (`core/timeline.py`)

| Función | Qué hace |
|---|---|
| `add_marker(p, t, name="")` | Añade un marcador en el frame de `t` (uno por frame; nombre por defecto «Marcador N»). Devuelve `(project, marker)`. |
| `rename_marker` / `remove_marker` / `marker_at` / `adjacent_marker` | Renombrar, borrar, buscar cerca de un instante y el anterior/siguiente (↑/↓). |
| `set_range_in` / `set_range_out` / `clear_range` / `has_range` / `play_range` | In/Out del timeline. Poner In después del Out borra el Out (y al revés), como en los NLE; Out en 0 es un error. |
| `razor(p, t, track=None)` | Corte de la razor: `track=None` = pista de video (vía `split_all`, que corta también su audio enlazado); un índice = esa pista de audio (como `split_audio`, conservando el `link_id`). |
| `split_everything(p, t)` | Video + todas las pistas de audio desbloqueadas (la razor sobre la regla o una zona vacía). |
| `trim_to(p, t, side, track=None)` | Q (`start`) / W (`end`): recorta el clip bajo `t` hasta `t`. En video es ripple (el timeline es magnético); en audio absoluto, el clip conserva su sitio. |
| `copy_items(p, ids)` → `Clipboard` | Copia clips de video, de audio y textos con su posición relativa (`offset`), la pista de origen, la transición interna y los assets que usan. |
| `paste(p, clipboard, t)` | Pega en `t` (el playhead) con ids nuevos. Devuelve `(project, new_ids)`. |
| `duplicate(p, ids)` | Copia y pega justo detrás de lo seleccionado (detrás del último clip de video si lo hay). |
| `remove_items(p, ids)` | Borra clips de video/audio y textos de una vez (Ctrl+X, Supr con selección múltiple). |
| `move_many` / `block_drop_index` / `shift_audio_many` / `move_block(p, ids, ghost)` | Mover la selección: los clips de video se reordenan como bloque y el audio absoluto de la selección se desplaza lo mismo que el video. |
| `snap_candidates(..., exclude=ids)` | Añade los marcadores y el In/Out a los candidatos del imán; `exclude` acepta ahora un id o un conjunto (el bloque no se imanta a sí mismo). |

La UI aplica cada gesto con un único `store.apply(...)` (o `begin`/`update`/`commit` en los
drags): pegar N clips, mover un bloque o cortar todas las pistas es **un solo undo**.

### Reglas del pegado

- **Video** (pista secuencial y magnética): se inserta en el hueco entre clips más cercano a `t`
  (regla del centro: si `t` cae en la primera mitad de un clip, antes; si no, después). Los clips
  pegados mantienen su orden y las transiciones que había **entre ellos**; con los vecinos
  nacen sin transición.
- **Audio absoluto**: `start = ancla + offset`, donde el ancla es el instante en que quedó el
  primer clip de video pegado (o `t` si no hay video). Si el audio estaba enlazado a un clip de
  video también copiado, se enlaza al **nuevo** clip y se alinea con él. Un solape en la pista
  destino lanza `ProjectError` (el proyecto no cambia y la UI avisa).
- **Pista destino**: la misma pista (por índice) si existe y tiene el mismo modo; si no, se crea
  una con `add_audio_track`. Las pistas bloqueadas rechazan el pegado.
- **Música** (pista secuencial): se inserta en el hueco más cercano, como el video.
- **Textos**: se recolocan en `t + offset` y se recortan al final del timeline.
- **Enlace audio↔video**: se conservan `transform`, `effects`, `speed`, `link_id` y
  `audio_enabled`. Un clip de video con el audio separado cuyo audio **no** viaja en el
  portapapeles se pega con `audio_enabled=True` (si no, se pegaría mudo).
- Los assets del portapapeles se reincorporan si el proyecto destino ya no los tiene (cortar,
  borrar el último clip de un asset y volver a pegar).

### Mover en bloque

`move_block` trabaja sobre el proyecto del `begin` del drag (no acumula error): calcula el índice
de inserción del bloque de video con `block_drop_index` (el hueco cuyo inicio queda más cerca del
inicio del fantasma), lo reordena con `move_many` y desplaza el audio absoluto seleccionado por el
**delta real** que sufrió el video. Si solo hay audio en la selección, se desplaza al fantasma.
Las pistas bloqueadas cancelan el drag con un aviso; un solape de audio deja el bloque en la
última posición válida.

## Reproducción: loop y J/K/L (`services/playback.Player`)

- `set_loop((a, b) | None)`: con el loop activo, al llegar a `b` el reloj vuelve a `a` y se
  re-ancla (marcha atrás: de `a` a `b`). Sin loop se para al final (o en 0 hacia atrás). El
  controller sincroniza el rango (`_sync_loop`) en cada cambio del proyecto, así que mover el
  In/Out con el loop puesto se nota al instante.
- `set_rate(rate)` cambia velocidad y sentido **sin salto**: re-ancla el reloj
  (`t0 + (ahora − wall0) × rate`) en el instante actual. `play(t, rate=…)` arranca con esa
  velocidad; `pause()` vuelve a 1×.
- **J/K/L**: L reproduce a 1× y, pulsado otra vez, dobla (2×, 4×, 8×); J igual hacia atrás; K
  pausa. Cambiar de sentido vuelve a 1×. El transporte muestra la velocidad (`2× ▶`, `◀ 1×`).
- **Audio**: solo a 1× hacia delante. A otras velocidades se para el audio del preview (no hay
  `atempo` en tiempo real) y vuelve al pasar a 1×.
- **Marcha atrás**: es viable pero cara. PyAV decodifica hacia delante, así que cada frame
  hacia atrás es un seek + decode desde el keyframe anterior: el reloj es exacto, pero el preview
  muestra menos fps (lo que dé el decoder; el reloj no espera al render). Se acepta así: J sirve para localizar, no para
  revisar a tiempo real.

## Export de rango

- **Modal**: si hay In/Out, ofrece «Todo el timeline» o «Rango In/Out · a–b» (`export-range-*`),
  con el rango preseleccionado al abrirlo; el resumen (duración y tamaño) usa la duración del
  rango. Sin In/Out solo explica cómo marcarlo (`export-range-hint`).
- **Render** (`graph.build(..., span=(a, b))`): el graph completo se construye igual que siempre
  (mismos clips, mismo `tpad`/`trim=end_frame` por clip, mismas transiciones) y se añade al final
  `trim=start_frame=A:end_frame=B,setpts=PTS-STARTPTS` en video y
  `atrim=start=a:end=b,asetpts=PTS-STARTPTS` en audio. `range_frames` redondea a frames contra
  `timeline_frames`, así que el rango dura un número entero de frames y lo exportado es
  exactamente lo que se ve en el preview. Para no codificar lo que sobra, `crop_tail` quita antes
  los clips de video que empiezan después del Out (y los `link_id` que quedan colgando). Sin
  `span` la salida es idéntica a la de antes (golden intactos).
- **Corte rápido** (`keyframes.plan(..., span)`): no se bloquea. Como el corte rápido ya exige
  velocidad 1 y sin transiciones, cada segmento se recorta en tiempo de timeline y el inicio se
  imanta al keyframe anterior, igual que los cortes normales (con el aviso de desplazamiento de
  siempre). Si el rango no deja segmentos, error claro.

## Miniaturas de assets rotados

`media.upright_rgb(rgb, rotation)` endereza con `np.rot90` antihorario (`{90: 1, 180: 2, 270: -1}`),
la misma convención que `playback.rotate_rgb(clockwise=False)` que usa el preview, y
`_upright_frame` decodifica al tamaño que corresponde a la rotación de visualización. Lo usan
`thumbstrip`, `cached_thumbstrip` y `poster`. Para que no se sirvan las miniaturas viejas
(torcidas) la caché del filmstrip pasa a `thumbs2/` y el póster a `…-{alto}-u.jpg`; las antiguas se
quedan huérfanas en la caché y no se leen.

## UI

### Atajos (nuevos o cambiados)

| Tecla | Acción |
|---|---|
| `Ctrl+C` / `Ctrl+X` / `Ctrl+V` / `Ctrl+D` | Copiar / cortar / pegar en el playhead / duplicar (selección simple o múltiple, textos incluidos) |
| `C` / `V` | Herramienta razor / herramienta de selección (sale de razor y marquesina) |
| `I` / `O` | Punto de entrada / salida del timeline |
| `Mayús+I` / `Mayús+O` | Ir al In / Out |
| `Alt+X` | Quitar In/Out |
| `Ctrl+L` | Loop on/off (In/Out o todo el timeline) |
| `B` | Marcador en el playhead (`M` ya silencia el audio del preview) |
| `↑` / `↓` | Marcador anterior / siguiente (lo selecciona y salta) |
| `J` / `K` / `L` | Atrás / pausa / adelante; repetir dobla la velocidad (hasta 8×) |
| `Q` / `W` | Recortar el inicio / el final del clip bajo el playhead hasta el playhead |
| **`P`** | Mostrar/ocultar el inspector (**antes `I`**, que ahora es el punto de entrada) |
| `Esc` | Además de cancelar, sale de razor y marquesina |

Los atajos con modificador (`Ctrl+C/X/V/D`, `Ctrl+L`, `Alt+X`) no se interceptan si el foco está
en un campo de texto (`from_input`), para no romper copiar/pegar texto. Las letras sueltas ya
seguían esa regla. El panel «Atajos» del inspector y los tooltips están actualizados.

### Timeline y transporte

- **Barra del timeline**: botón razor (`tl-razor`, tijera) junto a la marquesina; son modos
  excluyentes (activar uno apaga el otro) y el texto de ayuda cambia mientras la razor está
  activa. Botón de marcador (`tl-marker`).
- **Razor**: las cajas de los clips pierden las asas de trim y el cursor es una cruz; el clic
  (fase `start` del `on_drag`, que trae la x de ventana) corta esa pista en ese punto, con snap.
  Sobre la regla o una zona vacía, corta todas las pistas desbloqueadas.
- **Marquesina que mueve**: tras seleccionar con la marquesina (o `Ctrl+A`), arrastrar cualquier
  clip de la selección mueve el bloque; un fantasma discontinuo cubre todas las pistas. Un clic
  sin movimiento selecciona como siempre.
- **Regla**: el rango In/Out es una banda con líneas discontinuas y las etiquetas «In»/«Out»;
  cada marcador es una bandera ámbar con una línea punteada (sólida si está seleccionado) y el
  nombre a partir de 30 px/s. Cada bandera tiene una caja de 12 px (`marker-{id}`, con tooltip):
  clic = seleccionar y saltar; el resto de la regla sigue haciendo scrub.
- **Inspector de marcador**: nombre editable, tiempo, «Ir al marcador» y «Eliminar» (también
  `Supr`).
- **Transporte**: botones In/Out y loop (`tp-in`, `tp-out`, `tp-loop`) y la velocidad de J/K/L.
  El rango actual se lee en la barra del timeline (`tl-range`), donde sobra sitio.

## Decisiones

- **Teclas**: `B` para marcadores (`M` ya era el audio del preview y es también la «M» de las
  cabeceras de pista); `I`/`O` siguen el estándar de los NLE y el inspector pasa a `P` (panel).
  `C`/`V` son las de Premiere/Resolve para razor/selección; `Q`/`W` y J/K/L, las de siempre.
- **Audio absoluto y ripple**: pegar o mover video no desplaza el audio absoluto que no está en la
  selección (igual que `ripple_delete` desde la fase 1). Solo se mueve el audio enlazado que viaja
  con el bloque. Es predecible y evita mover voces que el usuario colocó a mano.
- **Marquesina**: selecciona por rango de tiempo en video y en las pistas absolutas
  desbloqueadas. La música (secuencial) se queda fuera, porque moverla en bloque con el video no
  tiene una semántica clara. Los textos no están en el timeline: se copian desde la selección del
  inspector.
- **Export de rango por trim final** en vez de recortar los clips: reutiliza el graph completo y
  sus garantías (frames enteros, transiciones y fundidos en su sitio). El precio es decodificar
  los clips anteriores al In (ver límites).
- **Corte rápido con rango**: se soporta con el mismo imán a keyframe que ya tenían los cortes, en
  vez de bloquearlo; avisa si el inicio se movió.
- **A17**: se investigó (spike con `pilot`) y Aurora 0.9 no expone scroll horizontal programático.
  El estilo `reveal=` de `scroll` se ignora (el offset siguió en 0,0) y
  `_core.inspect_reveal` solo funciona con el inspector de desarrollo activo. Queda como límite.

## Tests y verificación

- `tests/test_fase3_flujo.py` (25): modelo v5, round-trip, validación, migración v4 → v5 y modo
  lectura para esquemas futuros; marcadores, In/Out y snap; razor por pista y en todas, Q/W;
  copiar/pegar (transiciones, transformación, efectos, hueco más cercano, audio enlazado, solape,
  pistas bloqueadas), duplicar, `remove_items` y undo único; `move_block`/`move_many` y bloque
  solo de audio; loop y velocidad/marcha atrás del `Player` (con un renderer falso); strings del
  graph con `span` y `crop_tail`; `keyframes.plan` con rango; **export real** de un rango en
  render y en corte rápido; miniaturas y póster enderezados de un video rotado y paridad de
  `upright_rgb` con `rotate_rgb`.
- `tests/test_fase3_flujo_ui.py` (7, app real con `build_app()` + `pilot`): Ctrl+C/V/D/X con un
  solo undo; marquesina + arrastre del bloque con un undo y con la pista bloqueada; razor
  excluyente con la marquesina, sin asas y cortando en el punto pulsado; `B`, ↑/↓, inspector,
  renombrar y borrar marcadores y snap del scrub a un marcador; `I`/`O`, `Mayús+I`, `Ctrl+L`,
  botón de loop, `Alt+X` y `P`; J/K/L y Q/W; modal de export con la opción de rango y un export
  real de 1 s.
- `uv run pytest -q`: **320 passed**.

## Límites y pendientes

- **A17** (zoom que mantiene el playhead a la vista): bloqueado por Aurora 0.9, que no permite
  fijar el scroll horizontal desde el código. Se revisará con la siguiente versión de Aurora
  (docs/15 → F1).
- **Marcha atrás** sin audio y a menos fps (seek por frame en PyAV); el reloj es exacto.
- **Export de rango**: los clips anteriores al In se decodifican (se descartan con el `trim`
  final). Con un In muy tarde en un proyecto largo, el export tarda más de lo que dura el rango.
  Recortar también la cabeza (como `crop_tail` hace con la cola) exigiría reajustar transiciones
  y fundidos; queda como optimización.
- El rango no se dibuja en el preview ni hay «exportar la selección» directa (E6 cubre el rango
  In/Out; marcar In/Out en los bordes de la selección es un atajo pendiente).
- Los marcadores no se exportan como capítulos del MP4 (sería `-map_chapters` con un archivo
  FFMETADATA; encaja con la fase 5).
- No hay panel de medios ni drag & drop desde el Explorador (siguen en el backlog).
- El portapapeles es interno del proceso: no se comparte entre proyectos abiertos en otra
  instancia ni con el portapapeles del sistema.
- La sensación con ratón real (razor, bloque, banderas de 12 px) sigue sin validarse a mano (A8).
