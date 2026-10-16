"""Punto de entrada de Rec Studio.

    uv run python main.py                  # abre la app
    uv run aurora dev main.py --inspector  # hot reload + inspector (F12)
"""

from __future__ import annotations

from recstudio.app import main

if __name__ == "__main__":
    main()
