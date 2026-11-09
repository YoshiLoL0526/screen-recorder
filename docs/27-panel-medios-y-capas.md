# 27 — Panel de medios, selección directa y capas (revisión del editor)

> Implementado el **2026-11-05** después de una revisión del editor centrada en lo que faltaba
> para que las features existentes se pudieran **ver, elegir y manipular**. No cambia
> `SCHEMA_VERSION` (sigue en 6): el trabajo es de UI e interacción sobre `Project.assets`,
> `Project.texts`, `Project.elements` y `Clip.transform`.

## Por qué

La revisión detectó tres problemas de producto, no de motor:

1. **Los medios importados no tenían un lugar propio**. `Project.assets` existía, pero el usuario
   solo veía un asset dentro del inspector del clip que lo usaba.
2. **Textos y elementos no se podían elegir en el preview**. La lista de elementos estaba al fondo
   del inspector y solo aparecía sin selección; los textos no tenían caja. El primer clic en el
   preview no hacía nada.
3. **Texto y elementos no estaban en el timeline**. Su intervalo solo se editaba con campos de
   texto en el inspector, y no había forma de moverlos o recortarlos como una capa.

La regla que guía este bloque es: **si una capacidad existe en el modelo, tiene que existir un
camino visible para usarla**.

## Lo que se implementó

### 1. Panel de medios y capas (`ui/project_panel.py`)

- Columna a la izquierda del preview, colapsable con **Ctrl+B** o el botón de la barra.
- Pestaña **Medios**: todos los `Project.assets` con miniatura, tipo, duración y estado de uso.
  Los pósters de video se generan en un hilo (`media.poster` → `state.posters`); la UI nunca
  decodifica un video en un handler.
- Cada medio tiene un botón **+** para insertarlo en el playhead:
  - video: clip en la posición más cercana, con el audio separado/enlazado como al importar;
  - audio: clip en la primera pista secuencial (se crea «Música» si no existe);
  - imagen: elemento en el playhead (no un clip).
- Los medios se pueden **arrastrar** desde el panel al timeline (en la x soltada) o al preview.
  El panel también acepta archivos del Explorador con `on_drop`.
- Un medio **sin usar** se puede quitar del proyecto; uno en uso se conserva y el comando avisa.
- Pestaña **Capas**: textos, elementos y video base, de arriba (encima) abajo (video), en orden de
  apilado. Las filas están siempre visibles, también con una selección activa. Al seleccionar una
  capa fuera del playhead, el editor salta a su inicio.

### 2. Selección y manipulación directa en el preview

- El preview pinta una capa de cajas por objeto visible, en el orden de composición:
  clip base → elementos → textos. La caja del clip seleccionado ya no tapa los elementos.
- **Clic** sobre un clip, elemento o texto lo selecciona.
- **Arrastrar** el cuerpo mueve; las asas escalan:
  - clip: `Transform.x/y` y `Transform.scale` (comportamiento de docs/23);
  - elemento: `Element.x/y` y `width/height`;
  - texto: `TextOverlay.x/y` y `size` (las asas recalculan el tamaño de fuente).
- Los textos tienen una caja calculada con las métricas reales de la fuente
  (`ui/element_overlay.text_box`, misma fuente que el preview).
- Los drags de preview usan **transacción perezosa**: no abren una entrada de undo hasta que hay
  movimiento real. Un clic que solo selecciona no ensucia el historial ni deja una transacción
  abierta.
- `Esc` cancela el gesto; `Supr` borra; `Ctrl+C/X/V/D` incluye elementos (además de clips, audio y
  textos).
- El **recorte interactivo** ([docs/43](43-crop-interactivo.md)) sustituye temporalmente las
  cajas de selección por un velo, el rectángulo del `Transform.crop` y ocho asas; el preview
  muestra el clip completo sin tocar el modelo mientras el modo está activo.

### 3. Capas de texto y elemento en el timeline

- Dos filas nuevas sobre la pista de video, en el mismo orden que la composición:
  **Textos** y **Elementos**. Solo aparecen cuando el proyecto tiene capas de ese tipo.
- Cada capa es una barra con su intervalo:
  - arrastrar el cuerpo la mueve en el tiempo;
  - las asas izquierda/derecha recortan el inicio/fin;
  - el imán engancha a clips, marcadores, playhead y a los bordes de otras capas.
- Cabeceras propias («Textos», «Elementos») con el número de capas.
- La geometría se deriva de `Tracks.video_top`: las filas de overlay empujan la pista de video y
  el resto del timeline, y `max_tracks_extra` descuenta su alto para no romper el mínimo del
  preview.

### 4. Inspector contextual

- Los campos de posición/tamaño/rotación/opacidad de elementos pasan de `select` de presets a
  `number_input` (se puede escribir el valor exacto).
- El color de elemento y de texto usa `color_picker` (paleta, hexadecimal y selector).
- El tamaño de texto y sus X/Y también son numéricos.
- Se corrigió el campo de edición del texto, que se pintaba con 22 px de ancho («Tí» en lugar de
  «Título»).
- La lista de elementos/textos sigue existiendo en el inspector como acceso secundario, pero el
  panel de capas es ahora la vía principal y no desaparece al seleccionar.

### 5. Paridad de texto entre preview y export

- El preview dibuja el mismo contorno negro de 2 px que el `borderw` de `drawtext`, en lugar de
  una sombra desplazada que el archivo exportado no tenía.

## Decisiones

- **Sin cambios de esquema.** El panel de medios usa `Project.assets`; las capas usan `texts`,
  `elements` y sus `start/end`; el preview usa `Transform` y las operaciones existentes.
- **El panel de medios no crea clips al importar.** `add_media` solo añade assets; insertarlos es
  una acción explícita. La importación rápida desde «Importar» conserva el comportamiento de
  añadir al final del timeline.
- **Una fila por tipo de capa.** Los textos comparten fila y los elementos comparten fila. Si dos
  capas del mismo tipo se solapan en el tiempo, la barra superior tapa a la inferior; para elegir
  la oculta se usa el panel de capas. Una fila por capa queda para una fase posterior si hace falta.
- **El clip base se puede seleccionar en el preview** aunque no tuviera selección previa. La capa
  de texto/elemento va por encima y gana el clic.
- **Los pósters son best-effort.** Si no se pueden generar, el panel muestra el icono de cámara;
  no bloquea nada.

## Límites y pendientes

- El video superpuesto es un `Element` PiP ([docs/28](28-pip-video.md)); las pistas **V2/V3** de
  [docs/29](29-pistas-video.md) agrupan esos elementos por `video_track`, no son `Track` nuevas.
  El panel elige el destino (Elementos/V2/V3) y el timeline dibuja una fila por pista usada.
- Los textos siguen siendo de **una línea**, Arial fija y sin fuente/alineación/caja; ya tienen
  opacidad y keyframes de X/Y/opacidad ([docs/31](31-keyframes.md)) y los subtítulos se importan
  desde [docs/30](30-subtitulos.md).
- Las capas de un mismo tipo comparten fila (ver decisión anterior).
- No hay menú contextual ni «Nuevo proyecto/Guardar como»; tampoco envolventes/keyframes de
  audio.
- El panel de medios permite quitar assets sin usar, pero no **reemplazar** un asset online
  conservando sus clips (sí existe «Reubicar» para offline).
- El `Ctrl+B` no cambia el texto seleccionado en un campo (se deja pasar cuando viene de un input).

## Verificación

- `tests/test_project_panel.py` (13 tests): inserción de clip/audio, `remove_asset`, geometría del
  texto, copiar/pegar elementos, panel visible, inserción en playhead, arrastre de un medio al
  timeline, `on_drop` de archivos, clic directo en clip/texto/elemento, arrastre de texto y elemento
  en el timeline, asas de tamaño del texto y recorte de una capa.
- `tests/test_bloque_b.py` conserva el test de preview del texto; el cambio de contorno no altera
  la comprobación (el frame sigue cambiando).
- `uv run pytest -q`: **407 passed**.
- `uv run aurora check main.py`: OK.
- `uv run aurora tree main.py`: sin avisos de layout.

## Archivos tocados

- `src/recstudio/ui/project_panel.py` (nuevo).
- `src/recstudio/ui/screens/editor.py`.
- `src/recstudio/ui/editor_controller.py`.
- `src/recstudio/ui/timeline.py`.
- `src/recstudio/ui/element_overlay.py`.
- `src/recstudio/ui/icons.py`.
- `src/recstudio/ui/file_dialogs.py`.
- `src/recstudio/services/media.py`.
- `src/recstudio/services/playback.py`.
- `src/recstudio/core/timeline.py`.
- `src/recstudio/state.py`.
- `tests/test_project_panel.py` (nuevo).
