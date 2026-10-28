"""Empaqueta la app con la configuración de Aurora más las exclusiones que `aurora build` no expone.

Aurora 0.5 solo excluye `tkinter`; para el resto no hay clave en `[tool.aurora.build]` ni opción
en la CLI. El bundle arrastra `cv2` (OpenCV, ~112 MB) porque `windows-capture` lo importa en su
`__init__`, aunque solo lo usa `Frame.save_as_image()` (Rec Studio copia `frame_buffer` y nunca
lo llama). `platform/cv2_shim.py` cubre ese import y deja `dxcam` con un error claro. Aquí se
parchea `aurora.build.pyinstaller_args` para añadir `--exclude-module cv2` y se llama al mismo
`build()` de Aurora: el resto del empaquetado (icono, includes, onedir, UPX) sigue siendo el de
`[tool.aurora.build]`.

Uso:  uv run python tools/build_exe.py [--clean] [--dist dist] [--upx|--no-upx]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aurora import build as aurora_build

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

#: Módulos que no deben viajar en el bundle. `cv2` solo lo necesita el backend `dxcam`
#: (el resto de backends y todo el editor/export usan numpy/PIL/PyAV).
EXCLUDES = ("cv2",)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build de Rec Studio con exclusiones de PyInstaller")
    parser.add_argument("--clean", action="store_true", help="borrar la caché de PyInstaller")
    parser.add_argument("--dist", default=None, help="carpeta de salida (por defecto: la del pyproject)")
    parser.add_argument("--upx", dest="upx", action="store_true", help="comprimir con UPX")
    parser.add_argument("--no-upx", dest="upx", action="store_false", help="no comprimir con UPX")
    parser.set_defaults(upx=None)
    args = parser.parse_args()

    try:
        config = aurora_build.resolve(dist=args.dist, upx=args.upx, clean=args.clean)
    except aurora_build.BuildError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    original = aurora_build.pyinstaller_args

    def with_excludes(cfg: aurora_build.BuildConfig) -> list[str]:
        extra = [arg for module in EXCLUDES for arg in ("--exclude-module", module)]
        return [*original(cfg), *extra]

    aurora_build.pyinstaller_args = with_excludes
    print(f"Empaquetando {config.script.name} en {config.name} (excluye: {', '.join(EXCLUDES)})…")
    try:
        output = aurora_build.build(config)
    except aurora_build.BuildError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    size_mb = sum(f.stat().st_size for f in output.rglob("*") if f.is_file()) / 1e6
    print(f"✅ {output} ({size_mb:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
