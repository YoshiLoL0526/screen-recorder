"""Utilidades compartidas para los vídeos RGBA intermedios del export (docs/33 y docs/35).

Los usan `services.element_animation` y `services.text_animation`: ambos codifican frames RGBA
por stdin con QuickTime Animation (`qtrle`), que conserva el alfa sin pérdidas.
"""

from __future__ import annotations

import re
from pathlib import Path


def safe_name(value: str) -> str:
    """Nombre de archivo seguro para un id de elemento/texto."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value) or "layer"


def encoder_args(exe: str, width: int, height: int, fps: int, target: Path) -> list[str]:
    """Argumentos de FFmpeg para codificar frames RGBA crudos a un `.mov` `qtrle`."""
    return [
        exe, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}",
        "-r", str(fps), "-i", "-",
        "-an", "-c:v", "qtrle", "-pix_fmt", "argb", str(target),
    ]


__all__ = ["encoder_args", "safe_name"]
