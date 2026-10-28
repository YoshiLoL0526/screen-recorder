"""Compila el instalador de Inno Setup y prueba una instalación silenciosa con el smoke del exe.

A4: el bundle onedir se empaqueta en `dist/installer/RecStudio-<versión>-setup.exe`. El instalador
es por usuario (`PrivilegesRequired=lowest`), así que se puede probar sin administrador: se instala
en una carpeta temporal, se ejecuta `Rec Studio.exe --smoke` desde ahí y se desinstala.

Uso:  uv run python tools/check_installer.py              # compila + instala + smoke + desinstala
      uv run python tools/check_installer.py --no-install # solo compila
      uv run python tools/check_installer.py --skip-compile
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "installer" / "rec-studio.iss"
DIST = ROOT / "dist"
NAME = "Rec Studio"


def _version() -> tuple[str, str]:
    sys.path.insert(0, str(ROOT / "src"))
    from recstudio import __version__

    parts = (__version__.split(".") + ["0", "0", "0"])[:4]
    return __version__, ".".join(parts)


def find_iscc() -> Path:
    """Ruta de ISCC.exe (PATH, variable INNO_SETUP_ISCC o instalación típica)."""
    env = os.environ.get("INNO_SETUP_ISCC")
    if env and Path(env).is_file():
        return Path(env)
    found = shutil.which("iscc")
    if found:
        return Path(found)
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit("❌ no encuentro ISCC.exe: instala Inno Setup 6 o define INNO_SETUP_ISCC")


def compile_installer(iscc: Path, version: str, version4: str) -> Path:
    if not (DIST / NAME / f"{NAME}.exe").is_file():
        raise SystemExit(f"❌ falta el bundle {DIST / NAME}: ejecuta tools/check_build.py primero")
    print(f"1) Inno Setup {iscc} · versión {version}…")
    proc = subprocess.run(
        [str(iscc), f"/DMyAppVersion={version}", f"/DMyAppVersion4={version4}", str(ISS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if proc.returncode != 0:
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:], file=sys.stderr)
        raise SystemExit(f"❌ ISCC falló (rc={proc.returncode})")
    installer = DIST / "installer" / f"RecStudio-{version}-setup.exe"
    if not installer.is_file():
        raise SystemExit(f"❌ ISCC terminó pero no creó {installer}")
    print(f"   ✓ {installer} ({installer.stat().st_size / 1e6:.0f} MB)")
    return installer


def _wait_gone(path: Path, timeout: float) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if not path.exists():
            return True
        time.sleep(0.2)
    return not path.exists()


def install_and_smoke(installer: Path, keep: bool) -> None:
    work = Path(tempfile.mkdtemp(prefix="recstudio-install-"))
    app = work / "app"
    log = work / "install.log"
    try:
        print("2) instalación silenciosa…")
        proc = subprocess.run(
            [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
             f"/DIR={app}", f"/LOG={log}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        if proc.returncode != 0:
            print(log.read_text(encoding="utf-8", errors="replace")[-6000:] if log.is_file() else "")
            raise SystemExit(f"❌ el instalador falló (rc={proc.returncode})")
        exe = app / f"{NAME}.exe"
        if not exe.is_file():
            raise SystemExit(f"❌ la instalación no dejó {exe}")
        print(f"   ✓ instalado en {app}")

        print("3) smoke del exe instalado…")
        report = work / "smoke.json"
        run = subprocess.run(
            [str(exe), "--smoke", str(report)],
            timeout=300,
            stdin=subprocess.DEVNULL,
        )
        if not report.is_file():
            raise SystemExit(f"❌ el exe instalado terminó (rc={run.returncode}) sin informe")
        data = json.loads(report.read_text(encoding="utf-8"))
        if run.returncode != 0 or not data.get("ok"):
            raise SystemExit(f"❌ smoke del exe instalado falló (rc={run.returncode})")
        print(f"   ✓ smoke rc=0 · {data['app']} · frozen={data['frozen']}")

        print("4) desinstalación silenciosa…")
        uninstaller = app / "unins000.exe"
        if not uninstaller.is_file():
            raise SystemExit(f"❌ no hay desinstalador en {app}")
        subprocess.run(
            [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            timeout=300,
            stdin=subprocess.DEVNULL,
        )
        if not _wait_gone(exe, 30):
            raise SystemExit(f"❌ el desinstalador no borró {exe}")
        print("   ✓ desinstalado")
    finally:
        if keep:
            print(f"   (se conserva {work})")
        else:
            shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compila y prueba el instalador de Rec Studio")
    parser.add_argument("--no-install", action="store_true", help="solo compilar")
    parser.add_argument("--skip-compile", action="store_true", help="usar el instalador ya compilado")
    parser.add_argument("--keep", action="store_true", help="conservar la carpeta temporal de la prueba")
    args = parser.parse_args()

    version, version4 = _version()
    iscc = find_iscc()
    installer = (DIST / "installer" / f"RecStudio-{version}-setup.exe")
    if not args.skip_compile:
        installer = compile_installer(iscc, version, version4)
    elif not installer.is_file():
        raise SystemExit(f"❌ no existe {installer}: compila sin --skip-compile")

    if not args.no_install:
        install_and_smoke(installer, args.keep)
    print("✅ instalador verificado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
