# 26 — Efectos globales del proyecto (feature D)

> Implementado sobre la base v6 de `docs/22-plan-features-editor.md`: `Project.effects` ya existía
> y el hook `core/global_effects.py` era un no-op. Esta feature lo rellena, añade los controles
> del inspector y mantiene la paridad preview/export.

## Qué se implementó

| Pieza | Contenido |
|---|---|
| `core/global_effects.py` | Lectura y validación de `Project.effects`, edición (`set_effect`, `set_grayscale`, `clear_effects`), filtros del graph (`apply_graph`) y composición del preview (`apply_frame`). |
| `core/color.py` | Curvas puras compartidas: `apply_eq` (antes vivía en `services.playback`), `to_grayscale` y `apply_vignette` en YUV BT.601 de rango limitado. |
| `core/effects.py` | `find_effect`/`param_in` genéricos para leer efectos de un clip, de un elemento o del proyecto; `find`/`param` siguen siendo la API del clip. |
| `core/graph.py` | Solo la llamada al hook: ahora le pasa `output_height` para escalar el blur al preset de salida. Sin efectos globales no se añade ninguna cadena (goldens de F4 intactos). |
| `services/playback.py` | Sigue llamando a `global_effects.apply_frame` entre `blend` y `overlays`/textos; `apply_eq` se importa de `core.color` conservando el nombre histórico. |
| `state.py` | `glob_brightness`, `glob_contrast`, `glob_saturation`, `glob_blur`, `glob_vignette` y `glob_grayscale` (todos con datos inmutables). |
| `ui/editor_controller.py` | Suscripciones y sincronización de los controles, `set_global_effect`, `set_global_grayscale` y `reset_global_effects` (un `store.apply` por cambio ⇒ un undo). |
| `ui/screens/editor.py` | Subsección «Efectos globales» del inspector sin selección, dentro de «Proyecto». |
| `tests/test_fase6_efectos.py` | Helpers, golden strings del graph, preview sintético, paridad de la viñeta y export real de 1 s. |
| `tests/test_fase6_efectos_ui.py` | Controles, undo por cambio, reset y guardar/reabrir con la app real y `pilot`. |

## Modelo

Se reutiliza `Project.effects` (tupla de `Effect`, como mucho uno de cada tipo, validado por
`Project.validate`). No se añadieron campos ni se cambió `SCHEMA_VERSION`.

| `kind` | `params` | Rango efectivo | Neutro |
|---|---|---|---|
| `eq` | `brightness`, `contrast`, `saturation` | los de `core/effects.py` (`EQ_RANGES`: −1…1, 0…3, 0…3) | 0, 1, 1 |
| `blur` | `radius` (px del lienzo del proyecto) | 0…`BLUR_MAX` = 50 | 0 |
| `vignette` | `strength` | 0…1 | 0 |
| `grayscale` | — (su presencia activa el efecto) | — | ausente |

La UI limita los selects a los rangos pedidos (brillo −0.3…0.3, contraste 0.5…2, saturación
0…2, blur 0/2/5/10/20 px, viñeta 0/0.25/0.5/0.75/1) para que un clic no deje un valor extremo,
pero el modelo admite el rango completo de `eq` (un proyecto editado a mano se sigue leyendo).

Un efecto neutro (eq por defecto, blur 0, viñeta 0) se elimina al editarlo y, si llega desde un
JSON antiguo, `has_effects` lo ignora: no añade filtros.

## Orden exacto de los filtros

El mismo en el graph y en el preview:

1. **Color** (`eq`): brillo, contraste y saturación. Usa `core/color.apply_eq`, la misma curva que
   los efectos de clip del preview.
2. **Blur** (`gblur=sigma=…` en FFmpeg, `ImageFilter.GaussianBlur` en el preview). El radio se
   guarda en píxeles del **lienzo del proyecto** y se escala por `alto_salida / project.height`
   (también en el preset 720p y en el preview a 540p).
3. **Viñeta** (`vignette=angle=…`): `strength` se mapea linealmente a `angle = strength · π/2`
   (0…`π/2`), con centro en el frame. `strength=0.5` ⇒ `angle=0.785398`.
4. **Blanco y negro** (`hue=s=0`): luma BT.601.

El graph aplica la cadena sobre la etiqueta del video ya compuesto (después de `concat`/`xfade`,
de las transformaciones por clip y de las pistas V2/V3 de [docs/29](29-pistas-video.md)) y antes
de la capa «Elementos» y de los textos. El **fondo del lienzo** (color, blur de V1 o imagen,
[docs/47](47-fondo-lienzo.md)) se compone antes que V1; los efectos globales siguen aplicándose
al video ya compuesto, con el fondo ya incluido. Con efectos, el resultado es una sola cadena:

```text
[v0]eq=brightness=0.1:contrast=1.25:saturation=0.5,gblur=sigma=8,
    vignette=angle=0.785398,hue=s=0[vglobal]
```

La etiqueta final (`vglobal`) la consumen los hooks de la capa «Elementos» y de los textos; el
audio y el recorte In/Out no se tocan.

## Decisión de paridad

- **Color**: se movió `apply_eq` de `services/playback.py` a `core/color.py` para que el preview
  de clips y el de efectos globales usen exactamente la misma función (sin duplicar la fórmula ni
  importar `services` desde `core`). `playback.apply_eq` sigue existiendo como reexport.
- **Viñeta**: la máscara del preview reproduce la fórmula real de `vf_vignette`
  (`factor = cos(angle · d/dmax)^4`, con `dmax` la semidiagonal del frame). Además se aplica en
  **YUV BT.601 de rango limitado**, porque el graph trabaja en `yuv420p`: multiplicar directamente
  en RGB daría esquinas más claras que el MP4 (el negro de la señal, Y=16, no escala con el
  factor). El test de export real compara centro y esquina del frame exportado con el preview
  (±10 niveles por YUV/dither).
- **Blur**: FFmpeg `gblur` y `PIL.ImageFilter.GaussianBlur` usan el mismo `sigma` en píxeles. La
  forma del kernel no es idéntica (Pillow aproxima con box blur y FFmpeg usa su Gaussiano), pero
  la diferencia media es < 1 nivel y el radio percibido coincide; se documenta como aproximación
  del preview, igual que las transiciones.
- **Blanco y negro**: `hue=s=0` en YUV y luma BT.601 en RGB; para grises el resultado coincide y
  para colores la diferencia es de redondeo.

El preview sigue siendo una aproximación de algunos filtros (ya lo era para `eq` y las
transiciones): el archivo final lo genera FFmpeg. La regla «lo exportado = lo visto» se cumple en
geometría, orden, radios y presencia de los efectos; las diferencias de redondeo/YUV son las mismas
que ya aceptaba el editor.

## UI

En el inspector **sin selección**, dentro de «Proyecto», se añade la sección «Efectos globales»:

- Brillo, Contraste y Saturación: selects con los mismos valores que los efectos del clip.
- Blur: `Sin blur`, 2, 5, 10 y 20 px.
- Viñeta: `Sin viñeta`, 0.25, 0.5, 0.75 y 1.
- Blanco y negro: No/Sí (extra barata sobre el mínimo pedido).
- «Restablecer efectos globales»: borra todos los efectos en **un solo undo**.

Cada cambio de un select pasa por `store.apply("…", …)`, así que genera exactamente una entrada de
undo (`blur global`, `brillo global`, `viñeta global`, `blanco y negro`, …). Los `Signal` nuevos
se sincronizan en `_sync_inspector` y sus handlers ignoran el eco comparando con el valor real del
proyecto (el mismo patrón que los `insp_*` de clip). No se reutilizan los `insp_*` para no mezclar
la selección del clip con la del proyecto.

## Límites y decisiones pendientes

- No hay control de UI para `sharpen` (el modelo lo admite como `Effect`, pero no se aplica). Si
  se implementa más adelante, el orden razonable sería `eq → sharpen → blur → vignette →
  grayscale`, con `unsharp` en el graph y una máscara equivalente en el preview; hoy se ignora y
  no se ofrece.
- La viñeta es radial y centrada; no se expone `x0`/`y0` ni el modo inverso.
- El blur es isotrópico; no se expone `sigmaV` ni un blur direccional.
- El corte rápido sigue bloqueado por `core/keyframes.blockers` en cuanto hay `Project.effects`
  (no se tocó esa regla).
- Los efectos globales se aplican después de las transformaciones por clip y de V2/V3, y antes de
  la capa «Elementos» y de los textos; mover un elemento o un texto no cambia su orden.
- Al cambiar el tamaño del lienzo, el radio guardado se interpreta en píxeles del **nuevo**
  lienzo; no se reescala el valor del modelo (es una decisión de esta feature, documentada aquí).
- No se editaron `docs/15-post-v1.md`, `AGENTS.md`, `CHANGELOG.md` ni `SCHEMA_VERSION`.

## Tests y verificación

- `tests/test_fase6_efectos.py` (8 tests): helpers/edición, graph histórico sin efectos, golden
  con los cuatro filtros y su orden, escalado del blur en el preset 720p, efectos antes de textos,
  preview (color, blur de un borde, viñeta, blanco y negro, no-op) y **export real de 1 s** con
  blur + viñeta comprobando MP4, duración, tamaño y paridad centro/esquina.
- `tests/test_fase6_efectos_ui.py` (3 tests): controles presentes, cambio de blur/brillo/contraste/
  viñeta/gris, undo en un paso, reset con un solo undo y guardar/reabrir.
- `uv run pytest -q`: **340 passed**.
- `uv run aurora check main.py`: sin avisos nuevos (`138 nodos`, 1280×800).
- `uv run aurora tree main.py`: «Sin avisos de layout».

## Archivos tocados

`core/global_effects.py`, `core/color.py` (nuevo), `core/effects.py`, `core/graph.py` (solo la
llamada), `services/playback.py` (import de `apply_eq`), `state.py`, `ui/editor_controller.py`,
`ui/screens/editor.py`, `tests/test_fase6_efectos.py`, `tests/test_fase6_efectos_ui.py` y este
documento.
