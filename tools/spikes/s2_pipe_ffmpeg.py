"""S2 — Captura → pipe FFmpeg → MKV/MP4 (60 s).

Valida el corazón del grabador: encoders disponibles (prueba real, no solo `-encoders`),
captura DXGI alimentando stdin rawvideo BGRA, contención de CPU del proceso ffmpeg,
y salida verificada con ffprobe (duración, fps, tamaño, frames).

Uso:  uv run python tools/spikes/s2_pipe_ffmpeg.py [--seconds 60] [--fps 60] [--also-cpu 15]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import psutil

from _common import OUT, ffmpeg_exe, monitor_size, save_json

ENCODERS: dict[str, list[str]] = {
    "h264_nvenc": ["-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"],
    "h264_amf": ["-quality", "balanced", "-rc", "cqp", "-qp_i", "22", "-qp_p", "24"],
    "h264_qsv": ["-preset", "veryfast", "-global_quality", "22", "-look_ahead", "0"],
    "libx264": ["-preset", "veryfast", "-crf", "21"],
}


def probe_encoders(exe: str) -> dict[str, bool]:
    """Prueba real: codifica 0.5 s de testsrc y comprueba que el encoder abre."""
    result = {}
    for name in ENCODERS:
        cmd = [
            exe, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=0.5",
            "-c:v", name, "-f", "null", "-",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        result[name] = proc.returncode == 0
        if proc.returncode != 0:
            result[name] = False
    return result


def ffmpeg_command(exe: str, encoder: str, size: tuple[int, int], fps: int, out: Path) -> list[str]:
    w, h = size
    return [
        exe, "-hide_banner", "-loglevel", "warning", "-y",
        "-thread_queue_size", "512",
        "-f", "rawvideo", "-pixel_format", "bgra",
        "-video_size", f"{w}x{h}", "-framerate", str(fps), "-i", "-",
        "-an",
        "-c:v", encoder, *ENCODERS[encoder],
        "-pix_fmt", "yuv420p",
        "-flush_packets", "1",
        "-f", "matroska", str(out),
    ]


class FfmpegCpu:
    """CPU del proceso ffmpeg muestreada mientras corre."""

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self.proc = proc
        self.samples: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            p = psutil.Process(self.proc.pid)
            p.cpu_percent(None)
            while not self._stop.wait(1.0):
                self.samples.append(p.cpu_percent(None))
        except psutil.Error:
            pass

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> float:
        self._stop.set()
        return round(sum(self.samples) / len(self.samples), 1) if self.samples else 0.0


def capture_into(exe: str, encoder: str, seconds: float, fps: int, tag: str) -> dict:
    """Graba `seconds` capturando con DXGI ring y alimentando ffmpeg por stdin."""
    import dxcam

    w, h = monitor_size()
    mkv = OUT / f"s2_{tag}.mkv"
    if mkv.exists():
        mkv.unlink()

    cmd = ffmpeg_command(exe, encoder, (w, h), fps, mkv)
    stderr_path = OUT / f"s2_{tag}.log"
    with open(stderr_path, "wb") as errf:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errf)
    cpu = FfmpegCpu(proc)
    cpu.start()

    cam = dxcam.create(device_idx=0, output_idx=None, output_color="BGRA", processor_backend="numpy")
    cam.start(target_fps=fps, video_mode=True)

    frames, bytes_sent = 0, 0
    intervals: list[float] = []
    t0 = time.perf_counter()
    prev = t0
    last_report = t0
    try:
        while True:
            got = cam.get_latest_frame(copy=True, with_timestamp=False)
            if got is None:
                continue
            arr = got[0] if isinstance(got, tuple) else got
            if not isinstance(arr, np.ndarray):
                continue
            proc.stdin.write(arr.tobytes())
            frames += 1
            bytes_sent += arr.nbytes
            now = time.perf_counter()
            intervals.append(now - prev)
            prev = now
            if now - t0 >= seconds:
                break
            if now - last_report >= 5.0:
                last_report = now
                print(f"   … {now - t0:5.1f}s · {frames} frames · {bytes_sent / 1e6:7.1f} MB · {frames / (now - t0):5.1f} fps")
    finally:
        cam.stop()
        proc.stdin.close()
        proc.wait()
    wall = time.perf_counter() - t0
    ff_cpu = cpu.stop()

    iv = np.array(intervals[1:]) * 1000 if len(intervals) > 1 else np.array([0.0])
    result: dict = {
        "encoder": encoder,
        "resolution": f"{w}x{h}",
        "target_fps": fps,
        "wall_s": round(wall, 2),
        "frames_sent": frames,
        "fps_real": round(frames / wall, 1),
        "interval_p50_ms": round(float(np.percentile(iv, 50)), 2),
        "interval_p95_ms": round(float(np.percentile(iv, 95)), 2),
        "interval_max_ms": round(float(iv.max()), 2),
        "mb": round(bytes_sent / 1e6, 1),
        "mb_per_s": round(bytes_sent / 1e6 / wall, 1),
        "ffmpeg_cpu_pct": ff_cpu,
        "exit_code": proc.returncode,
    }

    if proc.returncode != 0 or not mkv.exists():
        result["error"] = f"ffmpeg salió con {proc.returncode}"
        result["stderr_tail"] = stderr_path.read_text(errors="replace")[-1500:]
        return result

    mp4 = OUT / f"s2_{tag}.mp4"
    remux = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(mkv), "-c", "copy",
         "-movflags", "+faststart", str(mp4)],
        capture_output=True, text=True,
    )
    result["remux_ok"] = remux.returncode == 0
    # imageio-ffmpeg no trae ffprobe: `ffmpeg -i` imprime la info de pistas/duración en stderr.
    probe = subprocess.run([exe, "-hide_banner", "-i", str(mp4)], capture_output=True, text=True)
    info = {}
    for line in probe.stderr.splitlines():
        for key in ("Duration:", "Stream #0:0"):
            if key in line:
                info[key] = line.strip()
    result["probe"] = info
    result["file_mb"] = round(mp4.stat().st_size / 1e6, 1) if mp4.exists() else 0
    result["kbps_efectivo"] = round(result["file_mb"] * 8 * 1000 / wall, 0)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--encoder", default="auto")
    ap.add_argument("--also-cpu", type=float, default=0.0, help="segunda pasada con libx264 de N s")
    args = ap.parse_args()

    exe = ffmpeg_exe()
    print(f"ffmpeg: {exe}")
    available = probe_encoders(exe)
    print(f"Encoders disponibles: {json.dumps(available)}\n")

    if args.encoder == "auto":
        encoder = next((e for e in ("h264_nvenc", "h264_amf", "h264_qsv", "libx264") if available[e]), "libx264")
    else:
        encoder = args.encoder
    print(f"Encoder elegido: {encoder} · {args.seconds:.0f}s a {args.fps} fps\n")

    out = {"available": available, "encoder": encoder, "runs": []}
    run = capture_into(exe, encoder, args.seconds, args.fps, encoder)
    out["runs"].append(run)
    print(json.dumps(run, indent=2, ensure_ascii=False))

    if args.also_cpu > 0 and encoder != "libx264":
        print(f"\nSegunda pasada con libx264 ({args.also_cpu:.0f}s)…")
        run2 = capture_into(exe, "libx264", args.also_cpu, args.fps, "libx264")
        out["runs"].append(run2)
        print(json.dumps({k: v for k, v in run2.items() if k != "probe"}, indent=2, ensure_ascii=False))

    save_json("s2.json", out)
    print(f"\nGuardado: {OUT / 's2.json'}")


if __name__ == "__main__":
    main()
