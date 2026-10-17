"""Empaqueta la app con `aurora build` y hace el smoke test del ejecutable (F5).

1. `aurora build --clean` con la configuración de `[tool.aurora.build]` (onedir, sin consola,
   icono de `assets/icon.ico`; si falta, lo genera `tools/make_icon.py`).
2. Ejecuta `dist/Rec Studio/Rec Studio.exe --smoke <informe>`: importa todo el stack dentro
   del bundle, codifica un MP4 de 2 s con el FFmpeg incluido y construye la UI sin ventana
   (ver `src/recstudio/smoke.py`).
3. Imprime el informe y el tamaño del bundle; código de salida ≠ 0 si algo falla.

Uso:  uv run python tools/check_build.py              # build + smoke
      uv run python tools/check_build.py --skip-build # solo el smoke del exe ya construido
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

ROOT = Path(__file__).resolve().parents[1]
NAME = "Rec Studio"
DIST = ROOT / "dist"


def build() -> float:
    if not (ROOT / "assets" / "icon.ico").is_file():
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)
    t0 = time.perf_counter()
    proc = subprocess.run([sys.executable, "-m", "aurora", "build", "--clean"], cwd=ROOT)
    if proc.returncode != 0:
        raise SystemExit(f"❌ aurora build falló (rc={proc.returncode})")
    return time.perf_counter() - t0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true", help="no reconstruir: probar el exe que ya hay")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    if not args.skip_build:
        print("1) aurora build…")
        print(f"   ✓ build en {build():.0f} s")
    exe = DIST / NAME / f"{NAME}.exe"
    if not exe.is_file():
        raise SystemExit(f"❌ no existe {exe}: ejecuta sin --skip-build")
    bundle_mb = sum(f.stat().st_size for f in exe.parent.rglob("*") if f.is_file()) / 1e6
    print(f"   bundle: {exe.parent} ({bundle_mb:.0f} MB)")

    print("2) smoke test del ejecutable…")
    report = Path(tempfile.mkdtemp(prefix="recstudio-build-")) / "smoke.json"
    t0 = time.perf_counter()
    proc = subprocess.run([str(exe), "--smoke", str(report)], timeout=args.timeout, stdin=subprocess.DEVNULL)
    elapsed = time.perf_counter() - t0
    if not report.is_file():
        raise SystemExit(f"❌ el exe terminó (rc={proc.returncode}) sin escribir el informe")
    data = json.loads(report.read_text(encoding="utf-8"))
    for name, check in data["checks"].items():
        mark = "✓" if check["ok"] else "✗"
        extra = {k: v for k, v in check.items() if k not in ("ok", "trace")}
        print(f"   {mark} {name}: {extra}")
        if not check["ok"]:
            print(check.get("trace", ""))
    print(f"   rc={proc.returncode} · {elapsed:.1f} s · frozen={data['frozen']} · app {data['app']}")
    if not data["ok"] or proc.returncode != 0:
        print("❌ smoke test FALLIDO")
        return 1
    print("✅ build verificado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
