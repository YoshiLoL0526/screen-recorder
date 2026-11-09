# 33 — Animación de escala y rotación de elementos

> Implementado el **2026-11-05** sobre los keyframes de [31 — Keyframes](31-keyframes.md).
> Añade dos canales nuevos a `Element.animations`: **escala** (multiplicador de la caja) y
> **rotación** (grados horarios absolutos). El preview y el export comparten la misma geometría y
> el mismo rasterizado; el export hornea el elemento animado a un vídeo RGBA intermedio para no
> depender de filtros con tamaño de salida variable. **No cambia `SCHEMA_VERSION` (sigue en 9)**
> ni añade campos al modelo.

## Qué se puede hacer

- Animar la **escala** de una imagen, figura, PiP o clip V2/V3 con dos o más claves. `1.0` es el
  tamaño base; `0.05…4.0` el rango permitido.
- Animar la **rotación** en grados horarios absolutos (`-360…360`). El valor base del canal es
  `Element.rotation`, así que una clave de 0° equivale a «sin rotación».
- Combinar escala y rotación en el mismo elemento (y con X/Y, opacidad, blur, encaje y audio del
  PiP).
- Grabar una clave en el playhead con el valor actual del campo: **Escala** o **Rotación** en el
  inspector, y **Quitar** para borrar todas las claves del canal.
- La interpolación usa el easing de cada clave (por defecto lineal, [docs/39](39-easing.md)) y se
  acota a la primera/última clave, igual que X/Y/opacidad.

## Modelo (`SCHEMA_VERSION = 9`)

```python
Animation(
    channel="scale",              # x | y | scale | rotation (elementos)
    points=(Keyframe(0.0, 1.0), Keyframe(2.0, 2.0)),
)
Animation(
    channel="rotation",
    points=(Keyframe(0.0, 0.0), Keyframe(2.0, 45.0)),
)
Element(..., animations=(...))
```

- `core/animation.py` valida **por canal** (`CHANNEL_RANGES`):
  - `x`, `y`, `opacity`: `0…1`;
  - `scale`: `0.05…4.0` (multiplicador de `width`/`height`);
  - `rotation`: `-360…360` (grados horarios).
- `Keyframe` sigue siendo `frozen` y solo comprueba tiempo finito/no negativo y valor finito; el
  rango depende del canal y lo valida `Animation`. La serialización (`channel` + `points` con
  `t`/`value`) no cambia: los proyectos v9 anteriores se abren igual.
- `Animation` rechaza canales desconocidos/duplicados y tiempos no crecientes.
- `Element.animations` admite `x`, `y`, `scale` y `rotation`; `TextOverlay.animations` añadió
  `scale`/`rotation` después con la misma técnica ([docs/35](35-textos-escala-rotacion.md)).
- `core.elements.effective(element, t)` aplica x/y, escala y rotación. La escala multiplica
  `width`/`height` y la caja resultante se acota a `ELEMENT_SIZE_MIN…ELEMENT_SIZE_MAX` (4.0); no
  se añade `Element.scale` ni se persiste el valor de la clave.

## Preview

- `core.overlays.apply_frame` sigue usando `effective(element, t)`.
- **Imágenes y figuras**: el PNG materializado (con fit y blur horneados) se escala al tamaño
  efectivo con LANCZOS, se multiplica el alfa por la opacidad y se rota con PIL
  (`rotate_rgba`, la misma convención que FFmpeg). La cache de `_prepare` incluye el tamaño
  efectivo, así que el scrub no reescala en cada frame si no cambia la clave.
- **PiP y V2/V3**: `services.playback.video_element_image` decodifica con la caja efectiva,
  aplica encaje, auto-rotación del asset, rotación animada y opacidad. Es la única implementación
  de la imagen de un PiP y la reutiliza el bake del export.
- La caja de selección del preview se **oculta** si el elemento tiene claves de x, y, escala o
  rotación: la caja seguiría la animación pero el arrastre editaría la base. La edición sigue en
  el inspector y en el timeline (◆ y asas del canal elegido, [docs/38](38-asas-animaciones.md)).

## Export: vídeo RGBA intermedio (opción b)

El filtro `overlay` necesita un tamaño de entrada constante para centrar el elemento, y
`scale`/`rotate` no permiten cambiar el tamaño de salida por frame sin recortes ni artefactos.
Por eso se elige la opción **(b)** de docs/22:

1. `services.element_animation.materialize` recorre el intervalo del elemento frame a frame y
   calcula el **lienzo máximo** que necesita (escala + rotación + margen de blur).
2. Cada frame se compone con PIL exactamente igual que el preview: PNG materializado o frame del
   PiP, escala, opacidad y rotación, centrado en ese lienzo fijo.
3. Los frames RGBA se envían por stdin a FFmpeg y se codifican con **QuickTime Animation**
   (`qtrle`, `-pix_fmt argb`), que conserva el alfa sin pérdidas.
4. `core.overlays.apply_graph` añade la entrada del `.mov` y la superpone con las mismas
   expresiones de X/Y que ya usaba; el `enable` y el `eof_action=pass` no cambian. La rotación y
   la opacidad ya vienen horneadas, así que no se aplican dos veces.

Consecuencias:

- **Paridad real**: preview y export usan el mismo rasterizado (PIL) y la misma geometría
  (`core.elements`). El único paso distinto es la codificación/decodificación del intermedio.
- **Sin animación de geometría, las cadenas del graph son las de siempre**: los PNG estáticos y
  la rotación base siguen usando `rotate=…:c=none` y los golden de F4/docs/25 no cambian.
- **Audio del PiP**: el `.mov` horneado no lleva audio y no se registra en `video_inputs`; la
  mezcla abre el archivo original como antes. El volumen, los microfundidos y el ducking no
  cambian.
- **Limpieza**: los `.mov` viven en el directorio temporal del export (`export-…`), que el
  `Exporter` borra en su `finally`; no se acumulan en la cache de elementos.
- Si falta el vídeo animado, `apply_graph` lanza `ProjectError`: el export nunca ignora en
  silencio una animación de escala/rotación.

## UI

- Inspector de elemento → **Escala** (`insp-el-scale`, `0.05…4.0`, valor inicial `1.0`) y
  **Rotación** (`insp-el-rotation`, el campo que ya existía). El campo de escala **no se
  persiste**: solo es el valor que se grabará en la siguiente clave, como el resto de campos.
- Sección **Animación**: filas X, Y, **Escala** y **Rotación** con «Clave …» y «Quitar». Las keys
  son estables: `insp-element-key-scale`, `insp-element-key-rotation`,
  `insp-element-clear-scale` y `insp-element-clear-rotation`.
- La escala se reinicia a `1.0` al cambiar de elemento seleccionado; el campo de rotación se
  sincroniza con `Element.rotation`.
- El timeline mantiene el **◆** para cualquier capa con animación.
- Los textos tienen sus propios campos y claves de escala/rotación
  ([docs/35](35-textos-escala-rotacion.md)).

## Límites

- **Texto**: `drawtext` no ofrece una rotación fiable con el mismo rasterizado que PIL; los
  textos resuelven la escala/rotación con el mismo bake RGBA en
  [docs/35](35-textos-escala-rotacion.md).
- La caja efectiva se acota al máximo del modelo (`4.0`): un elemento que ya ocupa 4.0 de ancho
  no crece más aunque la clave de escala llegue a 4.
- Interpolación con easing por clave ([docs/39](39-easing.md)); no hay curvas Bézier libres. Las
  claves son explícitas (no se graban al mover el playhead).
- El bake es CPU (PIL) y se hace una vez por elemento y export; un PiP largo con muchas claves
  tarda más en la fase de preparación. El resultado se codifica sin pérdidas, así que el archivo
  intermedio puede ser grande, pero se borra con el temporal.
- El corte rápido sigue bloqueado por `Project.elements`, como cualquier superposición.
- La rotación base estática (sin claves) sigue siendo el filtro `rotate` de FFmpeg; la animada
  es PIL dentro del intermedio. El muestreo de los bordes puede diferir en el orden de un píxel.

## Tests y verificación

- `tests/test_scale_rotation.py` (8):
  - rangos por canal, expresiones, rechazo de valores/canales/tiempos inválidos y `effective`;
  - preview real de una figura que escala y rota (área y centro);
  - preview real de un PiP que escala;
  - **export real** de figura y de PiP, con muestreo de píxeles y comparación preview/export;
  - el bake de un `.mov` `qtrle` conserva el alfa (opacidad 0.5 → 128);
  - `ProjectError` si falta el vídeo animado;
  - el `Exporter` borra los intermedios del directorio temporal.
- `tests/test_project_panel.py`: claves de escala y rotación desde el inspector, con undo y
  «Quitar».
- `tests/test_animation.py`: la validación de valores se comprueba en `Animation` (por canal).
- `uv run pytest -q`: **454 passed**.
- `uv run aurora check main.py`: OK.
- `uv run aurora tree main.py`: sin avisos de layout.
- Export real con FFmpeg 7.1 (imageio-ffmpeg) en Windows; la comparación de píxeles usa
  tolerancia de 4–5 px en la caja y 18–22 % en el área.

## Archivos tocados

- `src/recstudio/core/animation.py` (rangos por canal y validación).
- `src/recstudio/core/elements.py` (`effective` con escala/rotación).
- `src/recstudio/core/overlays.py` (`ElementClip`, `element_clips`, escala del PNG en preview).
- `src/recstudio/core/graph.py` (paso de `element_clips`).
- `src/recstudio/services/element_animation.py` (nuevo: bake y limpieza).
- `src/recstudio/services/playback.py` (`video_element_image` compartido).
- `src/recstudio/services/export.py` (prepara y pasa los intermedios).
- `src/recstudio/state.py` (`insp_el_scale`).
- `src/recstudio/ui/editor_controller.py`, `ui/screens/editor.py` (campo, claves y caja oculta).
- `tests/test_scale_rotation.py`, `tests/test_animation.py`, `tests/test_project_panel.py`.
- `docs/15-post-v1.md`, `docs/25-elementos.md`, `docs/31-keyframes.md`, `docs/README.md`,
  `CHANGELOG.md` y `AGENTS.md`.
