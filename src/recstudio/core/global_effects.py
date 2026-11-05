"""Efectos globales del proyecto (v6, docs/22).

`Project.effects` es una tupla de `Effect` que se aplica al video **ya compuesto** (tras cortes,
transiciones y transformaciones de clip) y **antes** de los elementos superpuestos y los textos.
El preview y el export comparten este módulo para que lo visto sea lo exportado:

- `apply_graph(project, video_out, chains)` añade los filtros FFmpeg al filter graph y devuelve la
  nueva etiqueta de video.
- `apply_frame(project, rgb)` aplica la misma cadena sobre un frame RGB del preview.

La base v6 deja ambas funciones como no-op; la feature de efectos globales implementa los
efectos (brillo/contraste/saturación, blur, viñeta, etc.) con parámetros de `Effect`.
"""

from __future__ import annotations

from .project import Project


def apply_graph(project: Project, video_out: str, chains: list[str]) -> str:
    """Añade los efectos globales al filter graph (no-op en la base v6)."""
    return video_out


def apply_frame(project: Project, rgb):
    """Aplica los efectos globales a un frame RGB (no-op en la base v6)."""
    return rgb
