# 05 — UI con Aurora

## Principios de diseño

1. **La grabación manda**: iniciar/parar debe estar siempre a un clic o a un `F9`, sin diálogos
   intermedios. Todo lo demás es secundario.
2. **Una ventana, cuatro zonas**: Grabar · Biblioteca · Editor · Ajustes. Nada de ventanas
   secundarias (Aurora 0.3 no las tiene): los modales y toasts viven en `layer="overlay"`.
3. **Cero pantallas en blanco**: cada panel tiene estado vacío con acción ("Importa un video
   para empezar").
4. **Feedback inmediato**: cualquier acción de más de 300 ms muestra progreso (grabación: stats;
   export: barra + ETA; import: fila "analizando…").
5. **El timeline es el producto**: se afina al milímetro (drags, snap, zoom, cursores).

## Sistema de diseño

### Tema propio `STUDIO` (oscuro y claro)

Tokens propios añadidos a los de Aurora (⚠️ `Theme.resolve` lanza `KeyError` con un token
desconocido: todos deben estar definidos en los dos temas).

```python
STUDIO_DARK = Theme(
    colors={
        **MIDNIGHT.colors,
        "rec": "#FF5470",  "rec_hover": "#FF6B84",  "rec_pressed": "#E63E5C",
        "track_bg": "#10131C",      # fondo de las pistas del timeline
        "track_alt": "#141827",     # fila alterna
        "clip": "#2A3350",          # clip de video
        "clip_selected": "#7C5CFF", # clip seleccionado (usa $primary)
        "clip_audio": "#1F4B45",    # clip/fragmento de audio
        "wave": "#5EEAD4",          # waveform
        "playhead": "#FF5470",      # playhead (mismo que REC: "aquí estamos")
        "grid": "#1B2030",          # líneas de la regla
    },
    radius={**MIDNIGHT.radius, "timeline": 10.0},
    font_family="Inter",
)
STUDIO_LIGHT = Theme(colors={**DAYLIGHT.colors, "rec": "#E63E5C", ...}, radius=STUDIO_DARK.radius)
```

- **Tipografía**: Inter 400/600/700 (la del sistema si falta). Números de tiempo con
  `font_family="Consolas"` + `letter_spacing=0.2` para que no bailen (00:12:34.567).
- **Espaciado**: escala 4/8/12/16/24/32 (padding y gap siempre de la escala).
- **Radios**: `$sm` chips · `$md` botones/campos · `$lg` tarjetas/paneles · `14` modales.
- **Iconos**: SVG como `bytes` con `image()` (nítidos, tema-agnósticos). Set mínimo (~24):
  rec, pause, stop, play, cut, zoom±, undo, redo, export, folder, trash, settings, monitor,
  window, crop, mic, speaker, import, save, chevron.

### Estructura de la app

```python
def app_view():
    return column(grow=1, on_key=global_keys)[      # atajos globales en la raíz
        header(),                                    # 52 px: logo · nombre · acciones
        row(grow=1)[
            sidebar(),                               # 208 px: navegación
            content(),                               # grow=1: pantalla activa
        ],
        toast_layer(),                               # layer="overlay", absolute bottom-right
    ]
```

Navegación: `screen = Signal("record")`; el sidebar son `row`s con `on_click` y estado activo
(`bg="$surface_hover"`); el contenido es un `match` sobre `screen()` dentro de `@component`s.
Cambios de pantalla con `enter=animate.slide(x=8)` + `fade`.

## Pantallas

### 1. Grabar

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ ● Rec Studio         Sin título                            ▤ Biblioteca   ⚙   ☾/☀       │
├───────────┬──────────────────────────────────────────────────────────┬─────────────────┤
│ ⏺ Grabar  │  ┌────────────────────────────────────────────────────┐  │ FUENTE          │
│ ▤ Biblio  │  │                                                    │  │ [ Monitor 1   ▾]│
│ ✂ Editor  │  │           preview en vivo (o captura de            │  │   Ventana…      │
│ ⚙ Ajustes │  │           referencia con el rectángulo             │  │   Región…       │
│           │  │           de la región seleccionada)               │  ├─────────────────┤
│           │  │                                                    │  │ VIDEO           │
│           │  └────────────────────────────────────────────────────┘  │ FPS      [60  ▾]│
│           │  ┌────────────────────────────────────────────────────┐  │ Calidad  [Alta ▾]│
│           │  │  ● 00:12:04   60.1 fps   dup 3   ⌀ 1.2 GB   ▂▄▆▅▆█  │  │ Encoder  [Auto ▾]│
│           │  └────────────────────────────────────────────────────┘  ├─────────────────┤
│           │                                                        │ AUDIO           │
│           │        [ ⏺ GRABAR ]        [F9]  [cuenta atrás ▾]       │ [x] Sistema     │
│           │                                                        │ [x] Micrófono ▾│
│           │        Carpeta: ~/Videos/RecStudio            [cambiar] │ [x] Cursor      │
└───────────┴──────────────────────────────────────────────────────────┴─────────────────┘
```

- **Preview**: `image(rec_preview(), fit="contain")` (JPEG a ~6 fps desde el hilo de preview).
  Sin grabación activa y modo región: se muestra la **captura de referencia** con el rectángulo
  editable encima (capa de cajas absolute + `on_drag`, cursor `crosshair`; asas en las 4
  esquinas y bordes).
- **Durante la grabación**: el panel derecho se colapsa a solo-lectura, el botón pasa a
  "DETENER" (variant `danger` propio con el token `rec`), aparece una barra de control flotante
  (`layer="overlay"`, abajo-centro: pausa, detener, tiempo) por si la pantalla de Grabar pierde
  el foco visual. El borde del preview late con `opacity` animada (1.0 ↔ 0.72, `tween(0.9)`).
- **Stats**: contadores + `canvas` sparkline de fps real (últimos 120 muestreos).
- **Aviso de límites**: si el backend elegido no soporta cursor (dxcam) o fps objetivo (mss), un
  `badge` ámbar junto al select explica el motivo y la alternativa.

Componentes Aurora: `select`, `switch`, `checkbox`, `button`, `badge`, `stat`, `card`,
`tooltip`, `image`, `canvas`, `divider`.

### 2. Biblioteca

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ ● Rec Studio        13 grabaciones · 4.2 GB                     ⟳ actualizar   ☾/☀      │
├───────────┬────────────────────────────────────────────────────────────────────────────┤
│ ⏺ Grabar  │  ┌──────┐  Demo del dashboard.mp4                          00:04:12      │
│ ▤ Biblio  │  │thumb │  2026-10-16 10:30 · 1920×1080 · 60 fps · 812 MB   [✂ Editar] │
│ ✂ Editor  │  └──────┘  [▶] [carpeta] [🗑]                                            │
│ ⚙ Ajustes │  ───────────────────────────────────────────────────────────────────────  │
│           │  ┌──────┐  Clase de cálculo.mp4                             00:52:30     │
│           │  │thumb │  2026-10-15 18:02 · 1920×1080 · 30 fps · 3.1 GB    [✂ Editar] │
│           │  └──────┘  [▶] [carpeta] [🗑]                                            │
│           │  ⚠ Grabación interrumpida (2026-10-14)                    00:03:20      │
│           │     [Recuperar]  [Descartar]                                            │
└───────────┴────────────────────────────────────────────────────────────────────────────┘
```

- `virtual_list(count=len(recs), item_height=96, key="lib")`: cada fila es un `@component`
  (thumbnail `image`, textos, botones). Botón principal **Editar →** abre el editor con ese
  asset importado (es el flujo "grabar y cortar").
- Borrado con **modal** (`layer="overlay"` + `capture=True`): "¿Mover a la papelera?" con
  checkbox "borrar también el proyecto asociado".
- La sección de recuperación aparece si `library.scan_recoverable()` encontró MKVs huérfanos.

### 3. Editor

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ ● Rec Studio   Demo del dashboard.rsproj      [💾] [↶] [↷]     [Importar]  [Exportar…]  │
├──────────────────────────────────────────────────────────┬─────────────────────────────┤
│                                                          │ INSPECTOR                   │
│              ┌──────────────────────────────┐            │ ┌ Clip │ Efectos ┐        │
│              │                              │            │ Entrada   00:02.000  [⇤]    │
│              │        PREVIEW               │            │ Salida    00:14.200  [⇥]    │
│              │        (image 960×540)        │            │ Duración  00:12.200         │
│              │                              │            │ Velocidad [1.0× ▾]          │
│              └──────────────────────────────┘            │ Volumen   ────●──── 0 dB    │
│   ⏮  ◀  [ ▶ / ⏸ ]  ▶  ⏭    |  00:07.234 / 11:02.100      │ Fade in  [0.3 s]  [0.0 s]   │
│                                                          │ Brillo/Contraste/Sat.       │
├──────────────────────────────────────────────────────────┴─────────────────────────────┤
│ [−] [+] [ajustar] [snap ✓] [🔇]                     ×──────●──────────×  00:07.234     │
│  00:00        00:05        00:10        00:15        00:20        00:25        00:30   │
│  V ▐████████████▌ ╲fade╱ ▐███████▌                ▐███████████████████▌               │
│  A ⌇⌇∿∿∿∿∿∿∿∿∿∿⌇⌇        ⌇⌇∿∿∿∿∿∿∿∿⌇⌇                                             │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

Layout: `column[ toolbar, row(grow=1)[ column(grow=1)[preview, transport], inspector(280) ], timeline(≈ 260) ]`.

- **Preview**: `image(preview_frame(), fit="contain")` sobre un fondo `$background` con borde;
  al reproducir, sin transiciones CSS: el frame nuevo entra directo (a 30 fps no se anima nada).
- **Transport**: play/pause (botón icono), ±1 frame, ±1 s, tiempo `mm:ss.mmm`. El botón de play
  alterna icono; el estado es `playing: Signal[bool]`.
- **Inspector**: `tabs` (Clip | Efectos). Campos con `text_input` de tiempo validado (`mm:ss.mmm`),
  `select` para transición/velocidad, y **sliders propios** (no hay slider en Aurora: widget
  reutilizable `slider()` hecho con `canvas` + `on_drag`, 24 px de alto, track `$border`, thumb
  `$primary`).
- **Timeline** (abajo): la pieza central; especificación detallada en la sección siguiente.
- Estados vacíos: sin clips → panel central "Importa o abre una grabación" con dos botones.

### 4. Ajustes

`card`s apiladas: **Apariencia** (`switch` tema claro/oscuro → cambia `theme: Signal[Theme]` en
caliente), **Grabación** (carpeta destino, encoder preferido, backend preferido, límite de
tamaño), **Editor** (silencio al reproducir, resolución de preview, autosave), **Avanzado**
(mostrar inspector F12, logs, "abrir carpeta de cache"), **Acerca de** (versiones de Aurora,
PyAV, FFmpeg y licencia del binario).

## El timeline en detalle

### Arquitectura en dos capas

1. **Capa de dibujo — `canvas()`**: regla (canvas HUD aparte), fondos de pista, clips (color,
   nombre, waveform), transiciones (rombo/etiqueta), playhead, selección. Todo en coordenadas
   de *contenido* (0…`total_px`).
2. **Capa de interacción — cajas `position="absolute"`**: transparentes, una por clip visible
   (cuerpo, `cursor="grab"`) y dos asas de trim (`width=8`, `cursor="col-resize"`, `bg_hover`
   sutil). Se generan **solo para el rango visible** (calculado con `px_offset`, que llega del
   `on_scroll` del contenedor). Así los cursores hover nativos, `bg_hover` y los `on_drag`
   funcionan elemento a elemento (Aurora 0.3 no tiene `on_hover`, este patrón lo suple).
   ✅ **Validado en F0** (S5/S7): las cajas absolutas dentro del `scroll` se mueven con el
   contenido y reciben clics y cursores correctos (`col-resize` sobre el asa).

```
timeline_panel (column)
├─ toolbar            canvas sparkline de zoom, botones [−][+][Fit][Snap]
├─ ruler              canvas h=26 · on_drag=scrub · dibuja t desplazado por px_offset
└─ scroll(axis="x", key="tl-scroll", on_scroll=→px_offset)     ← pan nativo: rueda,
   └─ box(width=total_px, height=3 tracks)                        trackpad, barra, inercia
      ├─ canvas(draw_tracks, height=…, on_drag=canvas_drag, on_click=…)
      └─ [cajas absolutas de interacción, solo rango visible]
```

- **Zoom**: 5–600 px/s (botones, `+`/`-`, `0` = ajustar al ancho). En zoom < 20 px/s no se
  dibujan waveforms (se degradan a banda de color).
- **Snap**: imán a bordes, playhead y transiciones a 8 px; `Ctrl` al arrastrar lo invierte.
- **Hit-test** (una función pura `hit_test(x_content, y) -> HIT`): prioridad playhead (±5) →
  asas de trim (±6) → hoja de transición (esquinas ±7) → cuerpo de clip → vacío.
- **Drags** (`DragEvent` con `phase`): `scrub` (regla: mueve playhead, snap a frames),
  `move` (fantasma del clip con apertura de hueco), `trim_l` / `trim_r` (mínimo 1 frame,
  previsualiza duración), `playhead`. `Esc` cancela (restaura snapshot); al soltar, `commit`
  al undo. Durante un drag, el repintado se limita a `project.set(...)` (snapshot nuevo) y las
  stats de drag (`dragging`, `ghost`).
  ⚠️ **Semántica real de `DragEvent` (medida en F0, S5)**: `e.x/y` van en **coordenadas de
  ventana** y `e.dx/dy` son **deltas acumulados desde el `start`**. Por eso toda la interacción
  se implementa **por identidad (qué caja) + deltas (÷ zoom)**, nunca por posición absoluta.
  Consecuencia: **no hay "clic en la regla → saltar a t"** (ver limitaciones abajo); el scrub
  se hace arrastrando (deltas) y con las flechas (por frame).
- **Selección**: clic en clip (`selection = Signal(clip_id)`); marquee en vacío (post-v1).
- **Reproducción y timeline están desacoplados**: el playhead es un `Signal` que el playback
  escribe a 30 Hz; el timeline solo lo dibuja.

### Qué se dibuja (guía de estilo)

- Fondos: `$track_bg` con filas alternas `$track_alt`; una línea horizontal por frontera.
- Clip de video: rectángulo `$clip` (radio 6) + barra de estado superior de 3 px si está
  seleccionado (`$primary`); nombre con elipsis (`max_lines` no aplica en canvas: corte manual
  con `text_width` estimado por `len * size * 0.55` y `…`).
- Waveform: `polyline` con array numpy `(n, 2)` por buckets min/max, `stroke="$wave"` a 1 px;
  clip de audio con relleno `$clip_audio` al 45 % + waveform al 100 %.
- Transiciones: entre clips, un rombo/par de triángulos de 14 px centrado en la frontera con
  etiqueta de duración al hacer zoom ≥ 40 px/s; seleccionable (inspector → duración y tipo).
- Playhead: `line` vertical de 2 px `$playhead` + triángulo en la regla (arrastrable).
- Regla: ticks cada 1/2/5/10 s según zoom, etiquetas `mm:ss`; línea vertical tenue por tick
  en las pistas (`$grid`).

### Rendimiento del timeline

- Un solo `canvas` de pistas: cada scroll/zoom reconstruye el elemento, pero el dibujo se limita
  a lo visible (culling por `px_offset` + ancho). Objetivo: < 3 ms de `draw` con 100 clips.
- Las waveforms se cachean como arrays y se copian por referencia mientras el asset no cambie.
- Los cambios que no afectan al dibujo (p. ej. `export_progress`) no tocan el timeline: cada
  `@component` solo depende de los `Signal` que lee.

## Atajos de teclado

Se registran en la raíz (`on_key`) y se delegan por pantalla; `e.matches("mod+s")` con
`mod` = Ctrl en Windows. Se consumen devolviendo `True`.

| Atajo | Acción | Ámbito |
|---|---|---|
| `F9` / `F10` | Grabar / pausar | Grabar (global en v1.1 vía pynput) |
| `Space` | Play / pausa | Editor |
| `←` / `→` | ±1 frame | Editor |
| `Shift+←/→` | ±1 s | Editor |
| `Home` / `End` | Ir al inicio / final | Editor |
| `S` | Dividir (split) en el playhead | Editor |
| `Del` / `Backspace` | Eliminar clip y cerrar hueco (ripple) | Editor |
| `Ctrl+Z` / `Ctrl+Y` | Deshacer / rehacer | Global |
| `Ctrl+S` | Guardar proyecto | Editor |
| `Ctrl+I` | Importar medios | Editor |
| `Ctrl+E` | Exportar… | Editor |
| `+` / `-` / `0` | Zoom + / − / ajustar | Editor |
| `M` | Silenciar preview | Editor |
| `L` | Loop de la selección | Editor |
| `Esc` | Cancelar drag / cerrar modal | Global |
| `Ctrl+1..4` | Ir a Grabar/Biblioteca/Editor/Ajustes | Global |
| `F12` | Inspector de Aurora (dev) | Dev (via `aurora dev --inspector`) |

## Animaciones (catálogo de uso)

| Elemento | Animación |
|---|---|
| Cambio de pantalla | `enter=animate.slide(x=10)`, `exit=animate.fade()` en el contenedor. |
| Botón REC | `transition=animate.tween(0.15)` para hover/pressed; punto rojo con `opacity` pulsante (`tween(0.9)`) mientras graba. |
| Toast | `layer="overlay"` arriba al centro (contenedor de 0 px de alto: no tapa clics; ver docs/12), `enter=animate.slide(y=-14)`, auto-cierre 4 s, máximo 3. |
| Modal | Fondo `#00000080` + tarjeta `enter=animate.zoom(0.96)`, `capture=True`. |
| Filas de la biblioteca | `enter=animate.slide(y=8, transition=animate.spring())` escalonado (`delay=i*0.02`, cap 0.2 s). |
| Cambio de clip seleccionado | La barra superior del clip aparece con `transition` (el canvas no anima; el efecto lo da el asa/handle overlay). |
| Barra de export | `width` en % actualizada por `Signal` (4 Hz) + texto ETA. |

## Patrones de estado (cómo se escribe esto con Aurora)

```python
# 1) Un solo sitio para los Signals (state.py) y dataclasses frozen para el estado complejo.
# 2) La pantalla activa como componente memoizado por su estado:
@component
def editor_screen(pid: str):          # args hashables → memo correcto
    return column(grow=1)[
        editor_toolbar(),
        row(grow=1)[column(grow=1)[preview_panel(), transport()], inspector()],
        timeline_panel(),
    ]

# 3) Listas largas con virtual_list; filas con @component y key estable.
# 4) Tareas largas por servicios + Signals; la vista jamás hace IO:
def start_export():
    export.start(project(), preset())     # retorna al instante
    # el progreso llega por export_progress()
```

## Limitaciones actuales de Aurora 0.3 y cómo las esquivamos

| Limitación | Impacto | Mitigación elegida |
|---|---|---|
| Sin multi-ventana ni ventanas frameless/transparentes | No hay overlay de región sobre el escritorio ni mini-controles fuera de la ventana | Selección de región in-app sobre captura de referencia; controles de grabación en `layer="overlay"` dentro de la ventana |
| `canvas()` no dibuja imágenes… | Sin filmstrip de thumbnails dentro de los clips | Clips con color+nombre+waveform (v1); filmstrip como capa de `image()` absolutas (post-v1, spike pendiente) |
| Sin `on_hover` (solo `bg_hover`/`tooltip`) | Cursores por zona | Capa de cajas interactivas sobre el canvas — ✅ validado en F0 (S7): los cursores por elemento funcionan |
| Sin coordenadas de nodo en `on_click`/`DragEvent` (la posición va en coordenadas de ventana; el clic no trae posición) | "Clic en la regla → saltar a ese t" no es directo | ✅ Resuelto en F3: `on_drag` llega también con un clic (fase `start`) y la x de ventana se convierte con el origen fijo del timeline (`TL_X0`) + `scroll`. *Mejora propuesta a Aurora: `rect` del nodo en handlers.* |
| Cualquier caja encima bloquea los clics (aunque no tenga handlers) | Playhead y toasts tapaban asas y controles | Línea de playhead de 2 px con el asa solo en la regla; toasts en un contenedor de 0 px de alto (F3, docs/12). |
| La raíz recibe las teclas aunque haya un campo enfocado | `S`/`Espacio` dividían o reproducían al escribir | Los campos marcan sus pulsaciones (`mark_input_key`) y los atajos sin modificador las ignoran. |
| Sin scroll programático | El zoom no puede mantener el playhead a la vista | «Ajustar» (`0`) y zoom automático al importar. *Mejora propuesta: `scroll_to`.* |
| `view()` completo por evento | Coste de reconstrucción | `@component` + `key` estables + Signals de grano fino + `virtual_list` |
| Sin `slider` nativo | Controles continuos | Widget `slider()` propio con `canvas` + `on_drag` (reutilizable) |
| Sin accesibilidad | — | Foco visible (`focus_color`), atajos completos, tooltips, buen contraste (post-v1: revisar con Aurora) |
| `Signal.set` compara con `==` | Dataclasses mutables no dispararían | Todo el estado complejo es `frozen=True` (regla de proyecto) |
| Un hijo `grow=1` usa su contenido como base del flex | Una imagen alta o una pantalla larga estiraban su contenedor y empujaban controles fuera de la ventana (F5) | `height=0` (columna) / `width=0` (fila) en todo contenedor que crece y cuyo contenido puede no caber; ver docs/14. |
| Sin API para el tamaño de la ventana | Los divisores no saben cuánto pueden crecer | `on_scroll` también se emite al hacer layout: la navegación es un `scroll` y su alto es `state.view_h` (F5). |
| Sin `wrap` en filas | Listas de chips de longitud variable | Texto unido con « · ». |

## Tests de UI

- `app.pilot()`: navegación (`click`), timeline (`drag` de asas, `click` en clips), modales,
  teclado (`press("space")`), scroll del timeline (`scroll("tl-scroll", dx=…)`).
- `app.render_png()`: un snapshot por pantalla y por estado clave (grabando, editor con
  selección, modal de export) → `tests/snapshots/*.png` versionados; cualquier cambio visual
  aparece en el diff del PR.
- `pilot.stats`: presupuesto de frame del timeline (test de regresión de rendimiento suave:
  falla si `draw` del timeline supera 8 ms con 100 clips sintéticos).
