"""Mide RNF-06/07 con ventana real (A3): arranque, RSS en reposo y RSS grabando.

Dos modos:

- `--exe "dist/Rec Studio/Rec Studio.exe"`: mide el ejecutable empaquetado, que es lo que ve el
  usuario: arranque hasta que la ventana principal es visible y RSS en reposo. Con
  `--record-seconds` graba con F9 usando APPDATA/LOCALAPPDATA temporales (no toca los del usuario).
- Sin `--exe`: abre la app con `build_app()` en una ventana real y mide además el RSS durante una
  grabación (monitor 1, 1080p60, audio del sistema). Usa un `settings.json` y una carpeta de
  salida temporales para no tocar los del usuario.

El arranque es «proceso/build → ventana visible»; el primer frame puede llegar unos ms después.
Uso:  uv run python tools/rnf_measure.py --exe "dist/Rec Studio/Rec Studio.exe"
      uv run python tools/rnf_measure.py --record-seconds 30
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import psutil

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

WM_CLOSE = 0x0010
VK_F9 = 0x78
ROOT = Path(__file__).resolve().parents[1]


def _send_f9(hwnd: int) -> None:
    """Pone la ventana delante y pulsa F9 (atajo in-app de grabar/detener)."""
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SwitchToThisWindow(hwnd, True)  # más fiable que SetForegroundWindow solo
    user32.SetForegroundWindow(hwnd)
    # PostMessage llega aunque Windows no deje robar el foco al proceso de medición.
    user32.PostMessageW(hwnd, 0x0100, VK_F9, 0)  # WM_KEYDOWN
    user32.PostMessageW(hwnd, 0x0101, VK_F9, 0)  # WM_KEYUP


def _window_handle(pid: int) -> int | None:
    """HWND visible con título del proceso `pid` (0 si todavía no hay ventana)."""
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lparam):  # noqa: ANN001
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            if user32.GetWindowTextLengthW(hwnd) > 0:
                found.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def _wait_window(pid: int, timeout: float) -> tuple[int, float] | None:
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        hwnd = _window_handle(pid)
        if hwnd:
            return hwnd, time.perf_counter() - start
        time.sleep(0.01)
    return None


def _rss_mb(process: psutil.Process) -> float:
    try:
        return process.memory_info().rss / 1e6
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def _close_window(hwnd: int, process: psutil.Process) -> None:
    ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    try:
        process.wait(timeout=10)
    except psutil.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except psutil.TimeoutExpired:
            process.kill()


def _exe_env(work: Path) -> tuple[dict[str, str], Path]:
    """Entorno aislado del exe: APPDATA/LOCALAPPDATA temporales y salida a `work/videos`."""
    appdata = work / "appdata"
    local = work / "localappdata"
    videos = work / "videos"
    for folder in (appdata / "RecStudio", local, videos):
        folder.mkdir(parents=True, exist_ok=True)
    settings = {
        "out_dir": str(videos), "countdown_s": 0, "source_kind": "monitor",
        "monitor_index": 1, "fps": 60, "preset": "media", "encoder": "auto",
        "audio_system": True, "keepalive": True, "mic_index": -1,
        "tray": False, "notify_finished": False, "global_hotkeys": False,
    }
    (appdata / "RecStudio" / "settings.json").write_text(
        json.dumps(settings), encoding="utf-8"
    )
    env = os.environ.copy()
    env["APPDATA"] = str(appdata)
    env["LOCALAPPDATA"] = str(local)
    return env, videos


def _record_exe(
    ps: psutil.Process, hwnd: int, work: Path, videos: Path, seconds: float
) -> dict[str, Any]:
    """Graba `seconds` con F9 en el exe aislado y devuelve el pico de RSS y el MP4."""
    temp_root = work / "localappdata" / "Temp" / "recstudio"
    _send_f9(hwnd)
    deadline = time.perf_counter() + 20
    mkv: Path | None = None
    while time.perf_counter() < deadline:
        found = [p for p in temp_root.glob("rec_*/video.mkv") if p.stat().st_size > 0]
        if found:
            mkv = found[0]
            break
        time.sleep(0.2)
    if mkv is None:
        return {"recording_error": "F9 no inició la grabación (¿la ventana no tenía el foco?)"}

    peak = 0.0
    samples = 0
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        peak = max(peak, _rss_mb(ps))
        samples += 1
        time.sleep(0.25)

    _send_f9(hwnd)  # detener
    deadline = time.perf_counter() + 180
    mp4s: list[Path] = []
    last_size = -1
    stable = 0
    while time.perf_counter() < deadline:
        peak = max(peak, _rss_mb(ps))
        found = sorted(videos.glob("*.mp4"))
        if found:
            mp4s = found
            size = found[-1].stat().st_size
            # El mux crea el MP4 al final y lo va llenando: hay que esperar a que deje de crecer
            # (si se cierra la ventana antes, el cierre aborta el muxado).
            stable = stable + 1 if size > 0 and size == last_size else 0
            last_size = size
            if stable >= 4:
                break
        time.sleep(0.25)
    result: dict[str, Any] = {
        "rss_record_peak_mb": round(peak, 1),
        "rss_samples": samples,
    }
    if mp4s and mp4s[-1].stat().st_size > 0:
        result["mp4"] = str(mp4s[-1])
        result["mp4_mb"] = round(mp4s[-1].stat().st_size / 1e6, 1)
    else:
        result["recording_error"] = "no apareció un MP4 completo al detener"
    return result


def measure_exe(exe: Path, idle_seconds: float, record_seconds: float = 0.0) -> dict[str, Any]:
    """Arranque, RSS en reposo y (opcional) RSS grabando del ejecutable empaquetado."""
    if not exe.is_file():
        raise SystemExit(f"❌ no existe {exe}")
    work = Path(tempfile.mkdtemp(prefix="recstudio-rnf-exe-")) if record_seconds else None
    env, videos = _exe_env(work) if work is not None else (None, None)
    started = time.perf_counter()
    proc = subprocess.Popen([str(exe)], stdin=subprocess.DEVNULL, env=env)
    ps = psutil.Process(proc.pid)
    window = _wait_window(proc.pid, timeout=30)
    if window is None:
        proc.kill()
        raise SystemExit("❌ la ventana no apareció en 30 s")
    hwnd, _ = window
    startup = time.perf_counter() - started

    samples: list[float] = []
    deadline = time.perf_counter() + idle_seconds
    while time.perf_counter() < deadline:
        samples.append(_rss_mb(ps))
        time.sleep(0.25)
    idle = max(samples) if samples else 0.0
    report: dict[str, Any] = {
        "mode": "exe",
        "exe": str(exe),
        "startup_s": round(startup, 3),
        "rss_idle_mb": round(idle, 1),
        "idle_seconds": idle_seconds,
    }
    if record_seconds > 0 and work is not None and videos is not None:
        report["record_seconds"] = record_seconds
        report.update(_record_exe(ps, hwnd, work, videos, record_seconds))
    _close_window(hwnd, ps)
    if work is not None:
        report["work_dir"] = str(work)
    return report


def measure_app(record_seconds: float, idle_seconds: float) -> dict[str, Any]:
    """Arranque, RSS en reposo y RSS grabando con la app real (ventana, sin empaquetar)."""
    from recstudio import state
    from recstudio.app import build_app
    from recstudio.platform import paths

    work = Path(tempfile.mkdtemp(prefix="recstudio-rnf-"))
    paths.settings_path = lambda: work / "settings.json"  # type: ignore[assignment]
    paths.temp_root = lambda: work / "temp"  # type: ignore[assignment]
    (work / "temp").mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    app, controller = build_app(size=(1280, 800))
    controller.attach_desktop(app)

    # Ajustes de la medición: sin cuenta atrás, 1080p60, audio del sistema, salida temporal.
    state.out_dir.set(str(work / "videos"))
    (work / "videos").mkdir(parents=True, exist_ok=True)
    state.countdown_s.set(0)
    state.source_kind.set("monitor")
    state.monitor_index.set(1)
    state.fps_target.set(60)
    state.audio_system.set(True)
    state.mic_index.set(-1)

    report: dict[str, Any] = {"mode": "app", "record_seconds": record_seconds}
    done = threading.Event()

    def driver() -> None:
        process = psutil.Process(os.getpid())
        window = _wait_window(os.getpid(), timeout=30)
        if window is None:
            report["error"] = "la ventana no apareció en 30 s"
            app.quit()
            done.set()
            return
        hwnd, _ = window
        report["startup_s"] = round(time.perf_counter() - started, 3)

        idle_samples: list[float] = []
        deadline = time.perf_counter() + idle_seconds
        while time.perf_counter() < deadline:
            idle_samples.append(_rss_mb(process))
            time.sleep(0.25)
        report["rss_idle_mb"] = round(max(idle_samples) if idle_samples else 0.0, 1)

        if record_seconds > 0:
            report.update(_record(controller, state, process, record_seconds))
        _ = hwnd
        app.quit()
        done.set()

    def _record(controller, state, process, seconds: float) -> dict[str, Any]:  # noqa: ANN001
        controller.toggle_record()
        limit = time.perf_counter() + 30
        while state.rec_state() in ("idle", "starting") and time.perf_counter() < limit:
            time.sleep(0.1)
        if state.rec_state() != "recording":
            return {"recording_error": state.rec_error() or f"estado {state.rec_state()}"}

        peak = 0.0
        samples = 0
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            peak = max(peak, _rss_mb(process))
            samples += 1
            time.sleep(0.25)
        fps = state.rec_fps()

        controller.toggle_record()
        limit = time.perf_counter() + 180
        while state.rec_state() not in ("idle", "error") and time.perf_counter() < limit:
            peak = max(peak, _rss_mb(process))
            time.sleep(0.1)
        result: dict[str, Any] = {
            "rss_record_peak_mb": round(peak, 1),
            "rss_samples": samples,
            "fps_real": round(fps, 1),
            "rec_state": state.rec_state(),
        }
        if state.rec_state() == "error":
            result["recording_error"] = state.rec_error()
        mp4s = sorted((Path(state.recordings_dir())).glob("*.mp4"))
        if mp4s:
            result["mp4"] = str(mp4s[-1])
            result["mp4_mb"] = round(mp4s[-1].stat().st_size / 1e6, 1)
        return result

    thread = threading.Thread(target=driver, daemon=True, name="rnf-driver")
    thread.start()
    try:
        app.run()
    finally:
        controller.shutdown()
        done.wait(timeout=5)
    report["work_dir"] = str(work)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Mide RNF-06/07 con ventana real")
    parser.add_argument("--exe", default=None, help="mide este ejecutable (arranque + reposo)")
    parser.add_argument("--idle-seconds", type=float, default=5.0)
    parser.add_argument("--record-seconds", type=float, default=0.0,
                        help="segundos grabando (0 = no grabar)")
    parser.add_argument("--report", default=str(ROOT / "build" / "rnf-report.json"))
    args = parser.parse_args()

    report = (measure_exe(Path(args.exe), args.idle_seconds, args.record_seconds) if args.exe
              else measure_app(args.record_seconds, args.idle_seconds))
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n· informe: {target}")
    ok = "error" not in report and "recording_error" not in report
    if report.get("startup_s", 0) >= 2.0:
        print("⚠ RNF-06: arranque ≥ 2 s")
        ok = False
    for key, limit in (("rss_idle_mb", 600.0), ("rss_record_peak_mb", 1200.0)):
        if key in report and report[key] >= limit:
            print(f"⚠ RNF-07: {key} ≥ {limit:.0f} MB")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
