"""Notas de un release a partir de `CHANGELOG.md` (lo usa `.github/workflows/release.yml`).

    uv run python tools/release_notes.py v1.1.0            # imprime la sección [1.1.0]
    uv run python tools/release_notes.py v1.1.0 --out notas.md

Antes de imprimir comprueba que el tag coincide con `recstudio.__version__` y con la versión de
`pyproject.toml`: si no, sale con error y el release no se publica con un número equivocado.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "YoshiLoL0526/screen-recorder"  # en Actions manda GITHUB_REPOSITORY


def versions() -> dict[str, str]:
    sys.path.insert(0, str(ROOT / "src"))
    from recstudio import __version__

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {"recstudio.__version__": __version__, "pyproject.toml": pyproject["project"]["version"]}


def section(changelog: str, version: str) -> str:
    """Cuerpo de `## [version] — fecha` hasta el siguiente `## ` (sin el título)."""
    lines = changelog.splitlines()
    head = re.compile(rf"^## \[{re.escape(version)}\]")
    start = next((i for i, line in enumerate(lines) if head.match(line)), None)
    if start is None:
        raise SystemExit(f"✗ CHANGELOG.md no tiene la sección «## [{version}]»")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = "\n".join(lines[start + 1:end]).strip()
    if not body:
        raise SystemExit(f"✗ la sección [{version}] del CHANGELOG está vacía")
    return body + "\n"


def absolute_links(notes: str, tag: str) -> str:
    """`](docs/…)` → enlace al archivo en el tag: en la página del release no hay rutas relativas."""
    repo = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)
    return re.sub(r"\]\((?!https?://|#)([^)]+)\)", rf"](https://github.com/{repo}/blob/{tag}/\1)", notes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="tag del release, p. ej. v1.1.0")
    parser.add_argument("--out", type=Path, help="escribir las notas en este archivo")
    args = parser.parse_args()

    version = args.tag.removeprefix("v")
    wrong = {where: found for where, found in versions().items() if found != version}
    if wrong:
        detail = ", ".join(f"{where} = {found}" for where, found in wrong.items())
        raise SystemExit(f"✗ el tag {args.tag} no coincide con la versión del código ({detail})")

    notes = section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    notes = absolute_links(notes, args.tag)
    if args.out:
        args.out.write_text(notes, encoding="utf-8")
        print(f"✓ notas de {version} en {args.out}")
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
