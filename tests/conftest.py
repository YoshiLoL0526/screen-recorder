"""Asegura que `recstudio` (src/) se pueda importar aunque el paquete no esté instalado."""

from __future__ import annotations

import sys
from pathlib import Path

try:  # con `uv sync` el paquete queda instalado en editable
    import recstudio  # noqa: F401
except ImportError:  # pragma: no cover - conveniencia en entornos sin instalar
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
