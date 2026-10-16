"""S4 — Preview del editor: PyAV decode → resize → JPEG → image(bytes).

Mide por frame: decode (to_ndarray), resize a 960×540 y JPEG q80; el coste de seek;
y el render real de la vista de Aurora con el frame en memoria (render_png).

Uso:  uv run python tools/spikes/s4_preview_decode.py [--target 960x540] [--frames 200]
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import time
from pathlib import Path

import numpy as np

from _common import OUT, Series, ffmpeg_exe, save_json

SRC = OUT / "s4_src.mp4"
PREVIEW = (960, 540)


def ensure_src() -> Path:
    if SRC.exists():
        return SRC
    exe = ffmpeg_exe()
    cmd = [
        exe, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=60:duration=8",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-c:a", "aac", "-shortest", str(SRC),
    ]
    subprocess.run(cmd, check=True)
    return SRC


def to_preview(arr: np.ndarray, target: tuple[int, int]):
    """1080p→540p rápido y con calidad: `reduce(k)` entero + resize pequeño si sobra.

    `reduce` es un filtro de caja que PIL aplica por decimación: calidad correcta para bajar
    de resolución y ~3× más rápido que el bilinear genérico (medido en S4c).
    """
    from PIL import Image

    img = Image.fromarray(arr)
    k = max(1, img.height // target[1])
    while k > 1 and img.height // k < target[1]:
        k -= 1
    if k > 1:
        img = img.reduce(k)
    if img.size != target:
        img = img.resize(target, Image.Resampling.BILINEAR)
    return img


def bench_sequential(path: Path, target: tuple[int, int], max_frames: int) -> dict:
    import av
    import io as _io

    decode_ms, ndarray_ms, resize_ms, jpeg_ms, total_ms = Series(), Series(), Series(), Series(), Series()
    jpeg_bytes = Series()

    container = av.open(str(path))
    stream = container.streams.video[0]
    stream.thread_type = "AUTO"  # decode multihilo

    prev = time.perf_counter()
    n = 0
    for frame in container.decode(stream):
        t_dec = time.perf_counter()
        arr = frame.to_ndarray(format="rgb24")
        t_arr = time.perf_counter()
        img = to_preview(arr, target)
        t_img = time.perf_counter()
        buf = _io.BytesIO()
        img.save(buf, "JPEG", quality=80, subsampling=2)
        t_jpeg = time.perf_counter()

        decode_ms.add((t_dec - prev) * 1000)
        ndarray_ms.add((t_arr - t_dec) * 1000)
        resize_ms.add((t_img - t_arr) * 1000)
        jpeg_ms.add((t_jpeg - t_img) * 1000)
        total_ms.add((t_jpeg - prev) * 1000)
        jpeg_bytes.add(len(buf.getvalue()))
        prev = t_jpeg
        n += 1
        if n >= max_frames:
            break
    container.close()
    return {
        "frames": n,
        "decode": decode_ms.summary(),
        "to_ndarray": ndarray_ms.summary(),
        "resize_reduce": resize_ms.summary(),
        "jpeg": jpeg_ms.summary(),
        "total_video_ms": total_ms.summary(),
        "preview_KB": round(jpeg_bytes.summary().get("mean_ms", 0) / 1000, 1),
    }


def bench_to_image(path: Path, target: tuple[int, int], max_frames: int) -> dict:
    """Alternativa: frame.to_image() de PyAV (PIL) + resize + JPEG."""
    import av

    total = Series()
    container = av.open(str(path))
    stream = container.streams.video[0]
    n = 0
    t0 = time.perf_counter()
    for frame in container.decode(stream):
        img = frame.to_image().resize(target)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        t1 = time.perf_counter()
        total.add((t1 - t0) * 1000)
        t0 = t1
        n += 1
        if n >= max_frames:
            break
    container.close()
    return {"frames": n, "total_ms": total.summary()}


def bench_seek(path: Path, seeks: int, target: tuple[int, int]) -> dict:
    import av
    from PIL import Image

    container = av.open(str(path))
    stream = container.streams.video[0]
    duration = float(stream.duration * stream.time_base)
    rng = np.random.default_rng(42)
    times = np.sort(rng.uniform(0.5, duration - 0.5, seeks))
    lat = Series()
    for t in times:
        t0 = time.perf_counter()
        container.seek(int(float(t) * av.time_base), backward=True)
        frame = next(container.decode(stream), None)
        if frame is None:
            continue
        arr = frame.to_ndarray(format="rgb24")
        Image.fromarray(arr).resize(target)
        lat.add((time.perf_counter() - t0) * 1000)
    container.close()
    return {"seeks": seeks, "latency_ms": lat.summary()}


def bench_aurora(jpegs: list[bytes], size: tuple[int, int]) -> dict:
    """Coste de render real de Aurora con image(bytes) (build + layout + paint + PNG)."""
    from aurora import App, column, image

    total = Series()
    for jp in jpegs[:10]:
        app = App(lambda jp=jp: column()[image(jp, fit="contain")], title="s4", size=size)
        t0 = time.perf_counter()
        app.render_png(str(OUT / "s4_view.png"))
        total.add((time.perf_counter() - t0) * 1000)
    png = OUT / "s4_view.png"
    return {"frames": len(jpegs[:10]), "render_ms": total.summary(), "png_kb": round(png.stat().st_size / 1e3, 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="960x540")
    ap.add_argument("--frames", type=int, default=200)
    args = ap.parse_args()
    target = tuple(int(v) for v in args.target.lower().split("x"))  # type: ignore[assignment]

    src = ensure_src()
    print(f"Fuente: {src} ({src.stat().st_size / 1e6:.1f} MB)\n")

    print("· decode secuencial + resize + JPEG …")
    seq = bench_sequential(src, target, args.frames)
    print(f"  {json.dumps(seq, ensure_ascii=False)}")

    print("\n· alternativa frame.to_image() …")
    alt = bench_to_image(src, target, 60)
    print(f"  {json.dumps(alt, ensure_ascii=False)}")

    print("\n· seeks aleatorios …")
    seek = bench_seek(src, 20, target)
    print(f"  {json.dumps(seek, ensure_ascii=False)}")

    print("\n· render de Aurora con image(bytes) …")
    # reutiliza los JPEG de una pasada rápida
    import av
    from PIL import Image

    jpegs: list[bytes] = []
    container = av.open(str(src))
    for frame in container.decode(container.streams.video[0]):
        img = frame.to_image().resize(target)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        jpegs.append(buf.getvalue())
        if len(jpegs) >= 10:
            break
    container.close()
    au = bench_aurora(jpegs, target)
    print(f"  {json.dumps(au, ensure_ascii=False)}")

    total_ms = seq["total_video_ms"].get("p95_ms", 0)
    fps_posible = 1000 / total_ms if total_ms else 0
    verdict = "OK (>=30 fps)" if fps_posible >= 30 else f"INSUFICIENTE ({fps_posible:.1f} fps)"
    print(f"\nPresupuesto preview {target[0]}x{target[1]}: p95 {total_ms} ms/frame → {fps_posible:.1f} fps posibles → {verdict}")

    save_json(
        "s4.json",
        {"target": list(target), "sequential": seq, "to_image": alt, "seek": seek, "aurora": au,
         "fps_posible": round(fps_posible, 1)},
    )
    print(f"Guardado: {OUT / 's4.json'}")


if __name__ == "__main__":
    main()
