"""S4b — Variantes de compresión JPEG para el preview (PIL vs cv2).

El JPEG domina el presupuesto del preview (13.5 ms p50 con PIL por defecto). opencv ya está
en el stack (lo arrastra dxcam), así que comparamos: PIL por defecto, PIL 4:2:0, PIL q70 y
cv2.imencode. Se elige con datos.

Uso:  uv run python tools/spikes/s4_jpeg_variants.py
"""

from __future__ import annotations

import io
import time

import numpy as np

from _common import OUT, Series, save_json


def main() -> None:
    import av
    from PIL import Image

    src = OUT / "s4_src.mp4"
    if not src.exists():
        raise SystemExit("falta s4_src.mp4: ejecuta antes s4_preview_decode.py")

    arrs: list[np.ndarray] = []
    container = av.open(str(src))
    for frame in container.decode(container.streams.video[0]):
        arrs.append(frame.to_ndarray(format="rgb24"))
        if len(arrs) >= 60:
            break
    container.close()

    target = (960, 540)
    imgs = [Image.fromarray(a).resize(target, Image.Resampling.BILINEAR) for a in arrs]
    bgr = [np.asarray(img)[:, :, ::-1].copy() for img in imgs]

    import cv2

    variants: dict[str, Series] = {k: Series() for k in ("pil_default", "pil_420", "pil_q70_420", "cv2_q80")}
    sizes: dict[str, int] = {}

    for i, img in enumerate(imgs):
        b = bgr[i]

        t0 = time.perf_counter()
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        variants["pil_default"].add((time.perf_counter() - t0) * 1000)
        sizes["pil_default"] = len(buf.getvalue())

        t0 = time.perf_counter()
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80, subsampling=2)
        variants["pil_420"].add((time.perf_counter() - t0) * 1000)
        sizes["pil_420"] = len(buf.getvalue())

        t0 = time.perf_counter()
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70, subsampling=2)
        variants["pil_q70_420"].add((time.perf_counter() - t0) * 1000)
        sizes["pil_q70_420"] = len(buf.getvalue())

        t0 = time.perf_counter()
        ok, enc = cv2.imencode(".jpg", b, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        variants["cv2_q80"].add((time.perf_counter() - t0) * 1000)
        sizes["cv2_q80"] = len(enc)

    rows = {}
    print(f"{'variante':<14} {'p50 ms':>8} {'p95 ms':>8} {'KB':>7} {'fps posibles':>13}")
    for k, s in variants.items():
        sm = s.summary()
        fps = 1000 / sm["p50_ms"] if sm["p50_ms"] else 0
        rows[k] = {**sm, "KB": round(sizes[k] / 1000, 1), "fps_p50": round(fps, 1)}
        print(f"{k:<14} {sm['p50_ms']:>8} {sm['p95_ms']:>8} {sizes[k] / 1000:>7.1f} {fps:>13.1f}")

    save_json("s4_jpeg.json", rows)
    print(f"\nGuardado: {OUT / 's4_jpeg.json'}")


if __name__ == "__main__":
    main()
