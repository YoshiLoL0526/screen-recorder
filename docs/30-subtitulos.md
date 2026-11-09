# 30 — Subtítulos SRT/VTT

> Implementado el **2026-11-05** sobre los textos de [docs/27](27-panel-medios-y-capas.md).
> Importa SRT/VTT como `TextOverlay` marcados con `subtitle=True`, los dibuja quemados en el
> export (`drawtext`) y en el preview (PIL), y permite exportar de vuelta los cues a SRT.

## Qué se puede hacer

- **Importar** uno o varios `.srt`/`.vtt` desde la pestaña **Capas** con **Subtítulos…**.
- Cada cue aparece como un texto en el timeline (fila «Textos», color teal), en la capa
  «Capas» con el prefijo `Subtítulo ·` y en el inspector como texto normal.
- **Editar** texto, intervalo, tamaño, color, X/Y, opacidad y negrita por cue.
- **Aplicar estilo a todos los subtítulos**: copia Y, tamaño, color, negrita y opacidad del cue
  seleccionado al resto; X se centra en 0.5.
- **Exportar SRT…**: guarda los textos `subtitle=True` como `.srt` en UTF-8, ordenados por inicio.
- Los subtítulos pueden **animarse** con keyframes igual que cualquier texto
  ([docs/31](31-keyframes.md)): posición, opacidad, escala, rotación y tamaño
  ([docs/35](35-textos-escala-rotacion.md) y [docs/36](36-color-tamano-textos.md)), además de
  claves de color.

## Parser (`core/subtitles.py`)

- Acepta SRT (`HH:MM:SS,mmm`) y WebVTT (`HH:MM:SS.mmm` y `MM:SS.mmm`).
- Tolera BOM, CRLF, índices SRT, ajustes de cue VTT (`align:start position:10%`), bloques
  `NOTE`/`STYLE`/`REGION`, etiquetas HTML (`<i>`, `<v Ana>`) y etiquetas ASS (`{\an8}`).
- Junta las líneas de un cue con un espacio (el modelo de texto es de una línea).
- Descarta cues sin texto, con fin ≤ inicio o con tiempos ilegibles; ordena por inicio.
- `cues_to_texts(cues, total=…)` recorta al timeline, salta los cues que empiezan después del final
  y aplica el estilo por defecto: centrado, `y=0.88`, tamaño 44, blanco, negrita.
- `format_srt(texts)` es el inverso: `HH:MM:SS,mmm`, numeración y UTF-8.

## Modelo (v12; el subtítulo nació en v9)

```python
TextOverlay(
    text="Hola",
    start=1.0, end=2.5,
    x=0.5, y=0.88, size=44, color="#FFFFFF", bold=True,
    subtitle=True,      # v9: cue importada (SRT/VTT)
    opacity=1.0,        # v9: opacidad base
    animations=(),      # v9: x/y/opacity (docs/31); scale/rotation (docs/35) y size (docs/36)
    color_keys=(),      # v12: color animado (docs/36)
)
```

- `subtitle` distingue los cues para el estilo global y la exportación SRT. Un texto normal
  puede convertirse en subtítulo editando el JSON o desde una futura UI; no cambia el dibujo.
- Los proyectos v8 se migran a v9 con `subtitle=False`, `opacity=1` y sin animaciones.
- `TextOverlay` valida `opacity` en `[0, 1]` y los canales de animación (`x`, `y`, `opacity`,
  `scale`, `rotation`, `size`); `color_keys` se valida por separado (docs/36).

## Importación y exportación

- **Importar**: `EditorController.browse_subtitles()` abre el diálogo en el hilo de UI; el archivo
  se lee y parsea en un hilo (`utf-8-sig`, `utf-8`, `cp1252`, `latin-1` como reserva) y todos los
  cues se aplican en **una sola entrada de undo** (`importar N subtítulo(s)`).
- **Exportar**: `export_subtitles()` abre «Guardar como», escribe en un hilo y avisa. El nombre
  por defecto es `<proyecto>.srt`.
- El texto se quema con el mismo `drawtext` de los textos (`expansion=none`, texto escapado,
  borde negro) y el preview usa PIL con el mismo contorno. La opacidad se comparte.

## UI

- Pestaña **Capas** → **Subtítulos…** (abrir SRT/VTT) y **SRT…** (exportar).
- Timeline: los textos subtítulo usan color teal y el inspector muestra el chip **Subtítulo** y el
  botón **Aplicar estilo a todos los subtítulos**.
- El selector de texto normal sigue funcionando; no hay una vista de lista de cues aparte.

## Límites

- **Una línea por cue**: las líneas múltiples se unen con un espacio. No hay salto de línea real
  en `drawtext`/preview.
- Fuente **Arial fija** (regular/negrita) y sin caja de fondo, alineación por cue ni estilos ASS.
- No se importan animaciones ASS (`\move`, `\fad`); los keyframes se añaden desde la app.
- La exportación SRT no incluye animaciones ni estilos (solo tiempos y texto).
- Los subtítulos se queman en el video; no hay pista de subtítulos blanda en el MP4.

## Tests

- `tests/test_subtitles.py` (5): SRT con BOM/CRLF/multilínea, VTT con cabecera/NOTE/ajustes,
  cues inválidos, recorte al timeline con `subtitle=True` y round-trip SRT.
- `tests/test_project_panel.py`: importación desde el diálogo, chip/estilo global y export SRT.
- `tests/test_modelo_v6_base.py`: round-trip de `subtitle/opacity/animations` y migración v8 → v9.
- `uv run pytest -q`: **446 passed**.
- `uv run aurora check main.py`: OK.

## Archivos tocados

- `src/recstudio/core/subtitles.py` (parser, cues, SRT).
- `src/recstudio/core/project.py` (`subtitle`, `opacity`, `animations`, schema 9).
- `src/recstudio/core/store.py` (migración v8 → v9).
- `src/recstudio/core/timeline.py` (`add_text`/`update_text`, `text_values`).
- `src/recstudio/ui/file_dialogs.py` (abrir/guardar SRT/VTT).
- `src/recstudio/ui/editor_controller.py` (importar/exportar/estilo).
- `src/recstudio/ui/project_panel.py`, `ui/screens/editor.py`, `ui/timeline.py`.
- `tests/test_subtitles.py`, `tests/test_project_panel.py`, `tests/test_modelo_v6_base.py`.
