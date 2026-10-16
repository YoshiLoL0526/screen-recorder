"""Prueba end-to-end de la UI de F2: grabar, pausar, reanudar y parar desde la interfaz.

- Arranca la app real con `App.pilot()` (sin ventana).
- Pulsa el botón GRABAR, comprueba el estado y el fps, pausa/reanuda con F10 y para con F9.
- Verifica que la grabación aparece en la biblioteca con miniatura y sidecar.
- Guarda un PNG de la pantalla grabando y otro de la biblioteca (documentación).
- Restaura `settings.json` al terminar.

Uso:  uv run python tools/f2_ui_test.py [--seconds 4] [--out tools/spikes/out/f2]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from recstudio import state  # noqa: E402
from recstudio.app import build_app  # noqa: E402
from recstudio.platform import paths  # noqa: E402


def wait_for(predicate, timeout: float, what: str) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.15)
    print(f"✖ timeout esperando {what}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--out", default="tools/spikes/out/f2")
    args = ap.parse_args()

    out_dir = Path(args.out) / "recordings"
    out_dir.mkdir(parents=True, exist_ok=True)
    settings_backup = None
    settings_file = paths.settings_path()
    if settings_file.exists():
        settings_backup = settings_file.read_text(encoding="utf-8")

    app, controller = build_app(size=(1280, 800))
    state.out_dir.set(str(out_dir))
    p = app.pilot(size=(1280, 800))
    p.settle()
    wait_for(lambda: bool(state.monitors()), 8, "monitores")
    wait_for(lambda: bool(state.preview()), 8, "primer preview")
    print(f"preview listo · monitores={len(state.monitors())} · encoders={state.encoders()}")

    ok = True

    print("\n1) pulsar GRABAR (equivalente a F9)…")
    p.click("rec-button")
    ok &= wait_for(lambda: state.rec_state() == "recording", 20, "estado recording")
    print(f"   estado={state.rec_state()}")
    p.settle()

    time.sleep(1.5)
    p.png(str(Path(args.out) / "ui-grabando.png"))
    time.sleep(max(0.0, args.seconds - 1.5))
    print(f"   fps={state.rec_fps():.1f} · tamaño={state.rec_size_mb():.2f} MB · dup={state.rec_dup()}")

    print("2) pausar con F10…")
    p.press("f10")
    ok &= wait_for(lambda: state.rec_state() == "paused", 5, "estado paused")
    time.sleep(1.2)
    print(f"   estado={state.rec_state()} · tiempo congelado en {state.rec_elapsed():.2f}s")

    print("3) reanudar con F10…")
    p.press("f10")
    ok &= wait_for(lambda: state.rec_state() == "recording", 5, "estado recording tras reanudar")
    time.sleep(1.2)

    print("4) detener con F9…")
    p.press("f9")
    ok &= wait_for(lambda: state.rec_state() == "idle", 30, "estado idle tras detener")
    p.settle()

    print("5) comprobar la biblioteca…")
    ok &= wait_for(lambda: bool(state.recordings()), 10, "listado de la biblioteca")
    items = state.recordings()
    if not items:
        print("✖ la biblioteca está vacía")
        ok = False
    else:
        rec = items[0]
        print(f"   {rec.name} · {rec.duration_label} · {rec.size_mb:.1f} MB · miniatura={'sí' if rec.thumb else 'no'}")
        ok &= rec.duration_s > 1.0 and rec.size_bytes > 10_000 and rec.thumb is not None
        if rec.sidecar and rec.sidecar.exists():
            data = json.loads(rec.sidecar.read_text(encoding="utf-8"))
            print(f"   sidecar: {data.get('backend')} / {data.get('encoder')} / audio={len(data.get('audio', []))}")
        state.screen.set("library")
        p.settle()
        p.png(str(Path(args.out) / "ui-biblioteca.png"))

    print("6) borrar la grabación de prueba (a la papelera)…")
    if items:
        state.confirm.set(state.Confirm(action="delete", title="t", message="m", target=str(items[0].mp4)))
        controller._on_confirm(state.confirm())
        state.confirm.set(None)
        ok &= wait_for(lambda: not state.recordings(), 10, "biblioteca vacía tras borrar")
        print("   borrada ✓")

    controller.shutdown()
    if settings_backup is not None:
        settings_file.write_text(settings_backup, encoding="utf-8")
    shutil.rmtree(Path(args.out) / "recordings", ignore_errors=True)

    print(f"\n{'✅ F2 UI OK' if ok else '✖ F2 UI con fallos'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
