# 25 — Elementos superpuestos: figuras, imágenes y blur (v6)

> Feature **C** de [22 — Plan de features del editor](22-plan-features-editor.md), implementada el
> **2026-11-05** sobre la base v6 (`d8683a7`). Añade figuras (rectángulo, elipse, triángulo) e
> imágenes (PNG/JPG/WebP/BMP) sobre el video, con intervalo, posición/tamaño normalizados,
> rotación, opacidad, z-order y blur, editables desde el inspector y desde una caja con asas en
> el preview. No cambia `SCHEMA_VERSION` (sigue en 6) ni la firma pública de `Element`/`Project`.

## Resumen

| Pieza | Estado | Dónde |
|---|---|---|
| Modelo v6 existente: `Element`, `Project.elements`, `MediaAsset.kind="image"` | ✅ (base) | `core/project.py` |
| Geometría pura compartida (caja, encaje, rotación, blur, z-order) | ✅ | `core/elements.py` |
| Composición del filter graph (`-loop 1`, `scale`, `rotate`, alfa, `overlay`) | ✅ | `core/overlays.py` |
| Composición del preview con PIL (mismo PNG materializado) | ✅ | `core/overlays.py`, `services/playback.py` |
| Importación de imágenes sin crear clip (`probe_image`) | ✅ | `services/media.py`, `ui/file_dialogs.py` |
| Rasterización de figuras y cache atómica de PNG | ✅ | `services/element_assets.py` |
| Inspector: lista, alta, edición, borrado, z-order y blur | ✅ | `ui/screens/editor.py`, `ui/editor_controller.py` |
| Caja de manipulación en el preview (mover/escalar) | ✅ | `ui/element_overlay.py`, `ui/screens/editor.py` |
| Export con elementos y blur verificado con FFmpeg real | ✅ | `services/export.py`, tests |
| Tests puros, graph, preview sintético, export real y UI | ✅ | `tests/test_fase6_elementos*.py` |

## Modelo

Se usa **tal cual** el modelo de la base v6. Un `Element` es inmutable y lleva:

```python
Element(
    id="e-…",
    kind="rect" | "ellipse" | "triangle" | "image",
    start=0.0, end=3.0,             # intervalo en segundos del timeline
    x=0.5, y=0.5,                   # CENTRO en fracciones del lienzo
    width=0.35, height=0.25,        # tamaño en fracciones del lienzo
    rotation=0.0,                   # grados horarios
    opacity=1.0,                    # 0…1
    color="#FF5470",                # figuras
    asset_id=None,                  # imágenes: MediaAsset(kind="image")
    fit="fit",                      # imágenes: fit | fill | stretch
    effects=(Effect("blur", (("radius", 8.0),)),),
)
```

- `Project.elements` está ordenado por **z-order**: el último se dibuja encima.
- `MediaAsset(kind="image")` tiene `duration=0`, `fps=0`, `has_audio=False` y las dimensiones
  reales. `timeline.append_clip` sigue rechazándola: una imagen solo puede ser un elemento.
- Las imágenes se añaden a `Project.assets` con `timeline.add_asset` y **no** crean clip ni
  entrada en `Project.texts`.
- No se añadió ningún campo nuevo a `Element` ni a `Project`. El blur se guarda en
  `Element.effects` (rango 0…50 px del lienzo; 0 quita el efecto).
- Las operaciones de edición viven en `core/elements.py` (`add_element`, `update_element`,
  `set_blur`, `remove_element`, `raise_element`, `lower_element`, `bring_to_front`,
  `send_to_back`) y devuelven proyectos nuevos. No tocan clips, audio, textos ni el esquema.

## Geometría (pura)

`core/elements.layout(project, element, canvas)` es la única implementación de la caja. Calcula
en píxeles del lienzo pedido (preview o export):

1. `box_width = round(element.width · canvas_width)` y lo mismo para el alto.
2. Para imágenes, encaje en esa caja:
   - **fit**: escala uniforme `min(caja/ancho, caja/alto)`; `draw_width`/`draw_height` conservan
     el aspecto y pueden dejar huecos transparentes;
   - **fill**: escala uniforme `max(...)`; `draw_width/height` es la caja y
     `scale_width/height` el tamaño previo al recorte central;
   - **stretch**: `draw_width/height = caja` (sin conservar aspecto).
3. Para figuras, `draw_width/height = caja`.
4. Centro `(element.x · canvas_width, element.y · canvas_height)`; la esquina superior izquierda
   es `centro − draw/2`, que puede ser negativa (el overlay recorta contra el lienzo).
5. Blur: `radio · canvas_height / project.height`, para que un radio expresado en píxeles del
   lienzo se vea igual en preview y en export.
6. Rotación: se usa `rotated_size` con la fórmula de `rotw()`/`roth()` de FFmpeg
   (`round(w·|cos| + h·|sin|)`), verificada con FFmpeg 7.1 (100×50 a 30° → 112×93).

`ElementLayout` expone también `prepared_size()`: el PNG materializado mide `draw + 2·margen`,
donde `margen = ceil(3·blur)`. Ese margen transparente es lo que permite que la gaussiana vierta
alfa fuera de la caja sin dejar un borde duro; como es simétrico, el contenido sigue centrado en
`(x, y)` y la geometría no cambia.

## Composición en el export (FFmpeg)

`core/overlays.apply_graph(...)` mantiene la firma de la base y añade un parámetro opcional
`element_sources: Mapping[element_id, ruta_png]`. `services/export.py` materializa los elementos
al tamaño de salida del preset con `services.element_assets.materialize` y se los pasa a
`core/graph.build(..., element_sources=...)`. Si un elemento visible no tiene PNG, el graph lanza
`ProjectError`: nunca se pierde un elemento en silencio.

Por cada elemento visible (`inicio < duración del timeline`, `fin > 0`, `opacity > 0`) se añade:

```
-loop 1 -framerate <fps> -t <duración del timeline> -i <png>
```

y una cadena:

```
[<n>:v]format=rgba,scale=<prepared_w>:<prepared_h>,setsar=1,
       rotate=<rad>:ow=rotw(<rad>):oh=roth(<rad>):c=none,
       colorchannelmixer=aa=<opacity>
[<base>][el]overlay=x=main_w*<x>-overlay_w/2:y=main_h*<y>-overlay_h/2:
                  format=auto:enable='between(t,<inicio>,<fin>)*lt(t,<fin>)'[ov]
```

- El PNG ya viene con el encaje `fit`/`fill`/`stretch` y el blur aplicados; `scale` solo fija el
  tamaño exacto (es un no-op si el PNG ya se materializó para ese preset).
- `rotate` usa `c=none` (fondo transparente para RGBA). El signo positivo de FFmpeg es horario,
  igual que `Element.rotation`.
- `overlay` centra con las dimensiones reales (`overlay_w/overlay_h`), así que el margen del blur
  no desplaza el contenido.
- El `enable` usa `between(...) * lt(t, fin)` para excluir el frame exacto del final, igual que
  el preview (`start <= t < end`).
- El orden es: efectos globales → elementos (en orden de `Project.elements`) → textos. Es el
  mismo orden en `core/graph.py` y `services/playback.py`.
- Sin elementos, `graph.build` genera **exactamente** el mismo graph que antes (goldens de F4
  intactos).

### Decisión sobre el blur: PNG pre-desenfocado (opción a)

Se elige la opción **(a)** de `docs/22`: el blur se hornea con
`PIL.ImageFilter.GaussianBlur` al materializar el PNG, y FFmpeg solo escala, rota, aplica alfa y
compone. Motivos:

1. **Paridad real**: preview y export consumen el **mismo archivo**. La opacidad y la rotación
   se aplican con las mismas reglas, pero el desenfoque no depende de que `gblur`/`boxblur`
   respeten el alfa (punto que `docs/22` marcaba como riesgo).
2. **Coste controlado**: el PNG se genera una vez por clave (tipo/color/tamaño/blur o
   ruta+mtime+tamaño+encaje), no por frame.
3. Se comprobó un **export real de 1 s** a 320×240 con un rectángulo rojo al 50 % y blur 6: el
   centro mezcla rojo y azul, a 2 px del borde el alfa está difuminado y el halo desaparece antes
   del margen. El test `test_export_real_de_un_segundo_con_elemento_y_blur` lo fija con píxeles.

Las figuras se rasterizan a 1–4× según el tamaño (antialias) y se reducen con LANCZOS. La cache
vive en `%LOCALAPPDATA%/RecStudio/cache/elements`, se escribe con `tmp + os.replace` y se
rechaza cualquier archivo que no empiece por la firma PNG (misma lección que las miniaturas).

## Preview

`core/overlays.apply_frame(project, t, rgb, *, sources=None)` compone con PIL:

1. `FrameRenderer` mide el tamaño del preview (`preview_size`) y pide
   `element_assets.materialize(project, size)`; el resultado se cachea por proyecto+tamaño, no
   se recorre el disco en cada frame.
2. Para cada elemento activo, el PNG materializado se carga una vez (`lru_cache` con
   path+mtime+tamaño), se multiplica el alfa por la opacidad, se rota con PIL (horario; 90/180/
   270 con transposiciones exactas) y se pega centrado en `(x·W, y·H)`.
3. La rotación arbitraria usa `expand=True` y luego ajusta el lienzo al tamaño de
   `rotated_size` (la fórmula de FFmpeg), para que la caja y el overlay coincidan.
4. Si un elemento no se puede materializar, el preview lo omite (best-effort). El export, en
   cambio, falla: la UI nunca debe tapar un error de composición en el archivo final.

La caja de selección del preview (`ui/element_overlay.py`) calcula el rectángulo real del lienzo
con el aspecto del proyecto y el tamaño del **escenario** (`state.preview_box`, que tras integrar
la feature A es el rectángulo del lienzo dentro del panel, no el panel entero). Se muestra
solo con un elemento seleccionado, en pausa y si está activo en el playhead. El cuerpo mueve
`x/y`; las cuatro asas escalan `width/height` anclando la esquina opuesta. Un drag usa
`store.begin/update/commit` ⇒ **un solo undo**; un clic sin movimiento no crea entrada. `Esc`
cancela.

## UI

- **Toolbar**: botón «Añadir imagen como elemento» (`ed-image`) y botón de imagen en la sección
  de elementos.
- **Inspector sin selección → «Elementos»**: lista en orden de apilado (arriba = encima),
  intervalo de cada elemento y botones Rectángulo / Elipse / Triángulo / Añadir imagen….
- **Inspector con `selection=("element", id)`**: inicio/fin, duración, X/Y, ancho/alto, rotación,
  opacidad, color (figuras) o encaje (imágenes), blur (0…50) y botones Al frente / Al fondo /
  Subir / Bajar / Eliminar.
- Cada cambio es un `store.apply` con etiqueta propia (`color del elemento`, `desenfoque del
  elemento`, `mover elemento`, `escalar elemento`…): un undo por cambio/gesto.
- La validación de selección (`_on_project`), `delete_selected`, `_selected_ids` y
  `_sync_inspector` conocen el tipo `"element"` sin romper las selecciones anteriores.
- La sección «Atajos» del inspector no cambia: los elementos se manejan con ratón/controles.

## Límites y decisiones pendientes

- Los elementos **no tienen pista propia en el timeline** ni se representan como clips: son
  overlays globales con intervalo. No se pueden recortar arrastrando en el timeline; el intervalo
  se edita en el inspector. Es el límite principal de esta entrega.
- Solo hay **blur** como efecto de elemento (`Element.effects` está preparado para más, pero la
  UI y `services.element_assets` solo implementan `blur`).
- No hay animación/keyframes de posición, tamaño, rotación ni opacidad.
- El color de figura se elige de una paleta (o se conserva un valor personalizado del proyecto);
  no hay selector de color libre en la UI.
- Las imágenes se referencian por ruta (no se copian al `.rsproj`). `export.offline_assets`
  incluye las imágenes de elementos, así que un archivo movido bloquea el export con un aviso;
  `Reubicar carpeta` ya las encuentra por nombre.
- La caja del preview es el rectángulo envolvente de la imagen rotada: para rotaciones que no son
  múltiplo de 90° no dibuja el contorno girado (Aurora 0.10 no expone rotación de cajas), pero el
  arrastre y las asas siguen funcionando sobre ese rectángulo.
- El preview omite un elemento si su PNG no se puede materializar; el export lo reporta como
  error. Es una diferencia deliberada (best-effort en UI, estricto en el archivo final).
- La rotación de la imagen en el preview es PIL y en el export es el filtro `rotate` de FFmpeg;
  el tamaño se alinea con `rotated_size`, pero el remuestreo puede diferir en el orden de un
  píxel en los bordes. Color, posición, tamaño, opacidad y blur sí comparten el mismo PNG.
- No hay soporte de GIF animado; `probe_image` toma la primera imagen y guarda sus dimensiones.

## Tests y verificación

- `tests/test_fase6_elementos.py` (15):
  - geometría normalizada en dos lienzos, fit/fill/stretch, blur escalado, rotación y z-order;
  - edición/borrado/intervalo y round-trip JSON con blur;
  - PNG de figuras con alfa, cache por contenido y sin `*.tmp`;
  - `probe_image` con PNG/JPG/WebP/BMP y archivo inválido, y el filtro de `open_images`;
  - graph con `-loop 1`, `rotate … c=none`, `colorchannelmixer`, `overlay` y `enable`, más
    `ProjectError` si faltan las rutas;
  - preview sintético para rectángulo, opacidad, imagen con alfa y blur (incluido el halo);
  - **export real** de 1 s con rectángulo borroso + imagen con transparencia, comprobando
    frames y píxeles (el alfa deja ver el fondo);
  - graph sin elementos idéntico al histórico.
- `tests/test_fase6_elementos_ui.py` (3): alta de figuras desde el inspector, color/opacidad/
  blur con undo, z-order, borrado/undo, guardar/reabrir, importación de una PNG sintética con el
  diálogo simulado y encaje, y drag de la caja del preview con undo.
- `uv run pytest -q`: **347 passed**.
- `uv run aurora check main.py`: OK, 138 nodos, sin avisos nuevos.
- `uv run aurora tree main.py`: «Sin avisos de layout».
- Export real comprobado con FFmpeg 7.1 (imageio-ffmpeg) en Windows; el test usa `libx264` del
  preset de calidad y verifica 30 frames de 1 s.

## Archivos tocados

- `src/recstudio/core/elements.py` (nuevo).
- `src/recstudio/core/overlays.py`.
- `src/recstudio/core/graph.py` (solo el parámetro opcional `element_sources` y su paso al hook).
- `src/recstudio/services/element_assets.py` (nuevo).
- `src/recstudio/services/media.py` (`probe_image`).
- `src/recstudio/services/playback.py` (materialización por proyecto+tamaño y `sources`).
- `src/recstudio/services/export.py` (assets offline de elementos y materialización).
- `src/recstudio/ui/file_dialogs.py` (`open_images`).
- `src/recstudio/ui/element_overlay.py` (nuevo).
- `src/recstudio/ui/icons.py` (icono de imagen).
- `src/recstudio/ui/screens/editor.py` (sección Elementos, inspector y caja del preview).
- `src/recstudio/ui/editor_controller.py` (comandos, importación, selección, undo y drags).
- `src/recstudio/state.py` (signals del inspector y `preview_box`).
- `tests/test_fase6_elementos.py` y `tests/test_fase6_elementos_ui.py` (nuevos).
- `docs/25-elementos.md` (este documento).

## Conflictos previsibles con las otras features

- **A (canvas resize)**: ambas tocan `ui/screens/editor.py` (preview), `state.py` y
  `ui/editor_controller.py`. En el merge se conservaron ambos conjuntos de handlers:
  `_measure_preview` escribe `preview_area` (hueco del panel) y `preview_box` (tamaño del
  escenario), y tanto la caja del clip como la del elemento viven dentro del mismo `ed-stage` con
  coordenadas relativas a él. Los drags de elemento usan el escenario actual
  (`canvas_edit.fit_stage`), no un panel con letterbox.
- **B (pistas A/V)**: independiente en modelo; C no toca `core/timeline.py` más allá de usar
  `add_asset`/`total_duration` y no toca audio. El merge no debería pisarse.
- **D (efectos globales)**: comparte `core/graph.py` y `services/playback.py` solo en los hooks ya
  existentes; C añade el parámetro opcional `element_sources` a `graph.build` y `sources` a los
  hooks. El orden global → elementos → textos ya está fijado en la base y debe mantenerse al
  integrar D.
- `docs/15-post-v1.md`, `AGENTS.md`, `CHANGELOG.md` y `SCHEMA_VERSION` **no** se tocaron.
