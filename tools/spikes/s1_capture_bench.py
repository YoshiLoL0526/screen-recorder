"""S1 — Bench de captura pura por backend.

Mide fps efectivos, jitter (intervalos entre frames), coste de CPU del proceso y sanidad del
frame para: mss (GDI), DXGI pull (windows-capture), DXGI ring (dxcam) y WGC callback
(windows-capture).

Uso:  uv run python tools/spikes/s1_capture_bench.py [--seconds 5]
"""

from __future__ import annotations

import argparse
import threading
import time
from typing import Any, Callable

import numpy as np

from _common import OUT, Series, monitor_size, save_json, table

Loop = Callable[[float], tuple[int, Series, np.ndarray | None]]


def run_bench(name: str, seconds: float, loop: Loop) -> dict[str, Any]:
    """Ejecuta `loop(seconds)` y resume. `loop` devuelve (frames, intervalos, último frame)."""
    cpu0, wall0 = time.process_time(), time.perf_counter()
    try:
        frames, intervals, last = loop(seconds)
    except Exception as exc:  # el spike reporta, no falla
        return {"backend": name, "error": f"{type(exc).__name__}: {exc}"}
    wall = time.perf_counter() - wall0
    cpu = time.process_time() - cpu0
    summary = intervals.summary()
    row: dict[str, Any] = {
        "backend": name,
        "wall_s": round(wall, 2),
        "frames": frames,
        "fps": round(frames / wall, 1),
        "cpu_pct": round(cpu / wall * 100, 1),
        "frame_p50_ms": summary.get("p50_ms"),
        "frame_p95_ms": summary.get("p95_ms"),
        "frame_max_ms": round(summary.get("max_ms", 0.0), 2),
    }
    if last is not None:
        row["shape"] = "x".join(map(str, last.shape))
        row["dtype"] = str(last.dtype)
        row["mean"] = round(float(np.asarray(last).mean()), 1)
    return row


def bench_mss(seconds: float) -> tuple[int, Series, np.ndarray | None]:
    """GDI/BitBlt con copia a bytes, como en el pipe real."""
    import mss

    intervals, frames, last = Series(), 0, None
    sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
    with sct:
        mon = sct.monitors[1]
        t_end = time.perf_counter() + seconds
        prev = time.perf_counter()
        while time.perf_counter() < t_end:
            frame = sct.grab(mon)
            raw = frame.bgra
            last = np.frombuffer(raw, dtype=np.uint8).reshape(frame.height, frame.width, 4)
            frames += 1
            now = time.perf_counter()
            intervals.add((now - prev) * 1000)
            prev = now
    return frames, intervals, last


def bench_dxgi_pull(seconds: float) -> tuple[int, Series, np.ndarray | None]:
    """Desktop Duplication (pull) vía windows-capture 2.0. OJO: monitor_index es 1-based."""
    from windows_capture import DxgiDuplicationSession

    session = DxgiDuplicationSession(monitor_index=1)
    intervals, frames, last = Series(), 0, None
    prev = time.perf_counter()
    t_end = time.perf_counter() + seconds
    while time.perf_counter() < t_end:
        frame = session.acquire_frame(timeout_ms=16)
        if frame is None:
            continue
        last = frame.to_numpy()
        frames += 1
        now = time.perf_counter()
        intervals.add((now - prev) * 1000)
        prev = now
    return frames, intervals, last


def bench_dxgi_ring(seconds: float) -> tuple[int, Series, np.ndarray | None]:
    """DXGI en modo video (ring buffer) con dxcam."""
    import dxcam

    cam = dxcam.create(device_idx=0, output_idx=None, output_color="BGRA", processor_backend="numpy")
    cam.start(target_fps=144, video_mode=True)
    intervals, frames, last = Series(), 0, None
    t_end = time.perf_counter() + seconds
    prev = time.perf_counter()
    try:
        while time.perf_counter() < t_end:
            got = cam.get_latest_frame(copy=True, with_timestamp=True)
            if got is None:
                continue
            arr, _ts = got
            last = arr
            frames += 1
            now = time.perf_counter()
            intervals.add((now - prev) * 1000)
            prev = now
    finally:
        cam.stop()
    return frames, intervals, last


def _wiggle_cursor(stop: threading.Event) -> None:
    """Mueve el cursor en un círculo pequeño: WGC solo entrega frames cuando la pantalla cambia."""
    import ctypes
    import math

    user32 = ctypes.windll.user32
    t0 = time.perf_counter()
    while not stop.is_set():
        t = time.perf_counter() - t0
        user32.SetCursorPos(960 + int(120 * math.sin(t * 6)), 540 + int(60 * math.cos(t * 6)))
        time.sleep(0.004)


def bench_wgc(seconds: float, *, motion: bool = True) -> tuple[int, Series, np.ndarray | None]:
    """Windows.Graphics.Capture por callback. `motion=True` agita el cursor (provoca daño)."""
    from windows_capture import WindowsCapture

    stop = threading.Event()
    wiggler = threading.Thread(target=_wiggle_cursor, args=(stop,), daemon=True)
    if motion:
        wiggler.start()

    capture = WindowsCapture(cursor_capture=True, monitor_index=1, dirty_region=False,
                             minimum_update_interval=1)
    intervals, frames = Series(), 0
    last: list[np.ndarray | None] = [None]
    prev = [time.perf_counter()]

    @capture.event
    def on_frame_arrived(frame, control) -> None:  # type: ignore[no-untyped-def]
        nonlocal frames
        frames += 1
        now = time.perf_counter()
        intervals.add((now - prev[0]) * 1000)
        prev[0] = now
        if frames % 10 == 0:
            last[0] = frame.to_numpy() if hasattr(frame, "to_numpy") else None

    @capture.event
    def on_closed() -> None:  # type: ignore[no-untyped-def]
        print("  (WGC: la fuente se cerró)")

    control = capture.start_free_threaded()
    time.sleep(seconds)
    control.stop()
    control.wait()
    stop.set()
    return frames, intervals, last[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=5.0)
    args = ap.parse_args()

    w, h = monitor_size()
    print(f"Monitor primario: {w}x{h} · bench {args.seconds:.0f}s por backend\n")

    rows = []
    for name, fn in (
        ("mss (GDI)", bench_mss),
        ("dxgi-pull (windows-capture)", bench_dxgi_pull),
        ("dxgi-ring (dxcam)", bench_dxgi_ring),
        ("wgc-callback (con movimiento)", lambda s: bench_wgc(s, motion=True)),
        ("wgc-callback (pantalla estática)", lambda s: bench_wgc(min(s, 3.0), motion=False)),
    ):
        print(f"· {name} …")
        row = run_bench(name, args.seconds, fn)
        rows.append(row)
        if "error" in row:
            print(f"  ERROR: {row['error']}")
        else:
            print(
                f"  {row['fps']} fps · p50 {row['frame_p50_ms']} ms · p95 {row['frame_p95_ms']} ms"
                f" · CPU {row['cpu_pct']}%"
            )
        print()

    cols = ["backend", "fps", "frame_p50_ms", "frame_p95_ms", "frame_max_ms", "cpu_pct", "shape", "dtype", "error"]
    print(table(rows, cols))
    save_json("s1.json", {"monitor": [w, h], "seconds": args.seconds, "rows": rows})
    print(f"\nGuardado: {OUT / 's1.json'}")


if __name__ == "__main__":
    main()
