"""Punto de entrada de Rec Studio.

    uv run python main.py                  # abre la app
    uv run aurora dev main.py --inspector  # hot reload + inspector (F12)
    "Rec Studio.exe" --smoke informe.json  # smoke test del ejecutable (tools/check_build.py)
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    if "--smoke" in sys.argv:
        from recstudio.smoke import main as smoke

        sys.exit(smoke(sys.argv))
    from recstudio.app import main

    main()
