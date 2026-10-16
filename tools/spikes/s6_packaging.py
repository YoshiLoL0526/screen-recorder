"""S6 — Empaquetado con `aurora build` del stack real.

Empaqueta `s6_app.py` (numpy + av + Pillow + imageio-ffmpeg + sounddevice + dxcam +
windows-capture + mss) en modo onedir/console, ejecuta el .exe y valida que todo funciona
dentro del bundle (incluido generar un MP4 con el FFmpeg incluido).

Uso:  uv run python tools/spikes/s6_packaging.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from _common import OUT, save_json

ROOT = Path(__file__).resolve().parents[2]
APP = Path(__file__).resolve().parent / "s6_app.py"
DIST = OUT / "s6dist"


def main() -> None:
    cmd = [
        sys.executable, "-m", "aurora", "build", str(APP),
        "--name", "s6app", "--onedir", "--console",
        "--dist", str(DIST), "--clean",
    ]
    print("Construyendo:", " ".join(cmd))
    t0 = time.perf_counter()
    build = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1800)
    build_s = time.perf_counter() - t0
    print(f"aurora build: rc={build.returncode} en {build_s:.0f}s")
    if build.returncode != 0:
        tail = (build.stderr or build.stdout)[-2500:]
        print(tail)
        save_json("s6.json", {"build_ok": False, "build_s": round(build_s, 1), "tail": tail})
        return

    exe = DIST / "s6app" / "s6app.exe"
    size_mb = sum(f.stat().st_size for f in (DIST / "s6app").rglob("*") if f.is_file()) / 1e6
    print(f"Bundle: {exe} ({size_mb:.0f} MB)")

    t0 = time.perf_counter()
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
    run_s = time.perf_counter() - t0
    ok = "PACKAGING_OK" in run.stdout
    print(f"Ejecución: rc={run.returncode} en {run_s:.1f}s · PACKAGING_OK={'sí' if ok else 'NO'}")
    print(run.stdout.strip()[-1200:])
    if run.stderr.strip():
        print("stderr (tail):", run.stderr.strip()[-800:])

    save_json(
        "s6.json",
        {
            "build_ok": True,
            "build_s": round(build_s, 1),
            "bundle_mb": round(size_mb, 1),
            "run_ok": ok,
            "run_s": round(run_s, 1),
            "run_rc": run.returncode,
            "stdout": run.stdout.strip()[-2000:],
            "stderr_tail": run.stderr.strip()[-2000:],
        },
    )
    print(f"Guardado: {OUT / 's6.json'}")


if __name__ == "__main__":
    main()
