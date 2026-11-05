"""Elementos superpuestos al video (v6, docs/22).

Este módulo nació como contrato de integración para la fase de composición: el preview
(`services/playback.py`) y el export (`core/graph.py`) llaman aquí, y no cada uno a su propia
implementación. En la base v6 las funciones son no-op; la feature de elementos (figuras e
imágenes) implementa la geometría, el z-order y los efectos (blur incluido) manteniendo estas
firmas:

- `apply_graph(project, video_out, chains, inputs, size, *, first_input)` añade las entradas de
  imagen y los filtros `overlay` al filter graph. `inputs` es la lista de argumentos FFmpeg que
  ya contiene las entradas de video y audio; `first_input` es el índice de la próxima entrada
  (el número de `-i` acumulados). Devuelve `(etiqueta_de_video, próxima_entrada)`.
- `apply_frame(project, t, rgb)` compone los elementos activos en `t` sobre un frame RGB del
  preview y devuelve el frame resultante.

`Project.elements` guarda el orden de apilado (el último va encima). Los elementos se aplican
después de los efectos globales y antes de los textos: el video se compone, se ajusta y luego se
le ponen encima figuras, imágenes y títulos.
"""

from __future__ import annotations

from .project import Project


def apply_graph(
    project: Project,
    video_out: str,
    chains: list[str],
    inputs: list[str],
    size: tuple[int, int],
    *,
    first_input: int,
) -> tuple[str, int]:
    """Añade los elementos al filter graph (no-op en la base v6)."""
    return video_out, first_input


def apply_frame(project: Project, t: float, rgb):
    """Compone los elementos sobre un frame RGB (no-op en la base v6)."""
    return rgb
