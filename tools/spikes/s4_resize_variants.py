"""S4c — Variantes de escalado del preview 1080p → 960×540.

El coste real del preview está en el *resize* (el JPEG es ~1 ms). Se comparan:
PIL bilinear, PIL box, PIL reduce(2) (factor entero exacto), PIL bilinear con
reducing_gap, y cv2 (INTER_AREA / INTER_LINEAR). Se elige el más rápido con
calidad de bajada correcta (box/area, no lineal sobre píxeles muestreados).

Uso:  uv run python tools/spikes/s4_resize_variants.py
"""

from __future__ import annotations

import io
import time

import numpy as np

from _common import OUT, Series, save_json

TARGET = (960, 540)


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

    import cv2

    variants: dict[str, Series] = {
        k: Series()
        for k in (
            "pil_bilinear",
            "pil_bilinear_gap2",
            "pil_box",
            "pil_reduce2",
            "cv2_area",
            "cv2_linear",
            "pil_reduce2+jpeg",
            "cv2_area+jpeg",
        )
    }

    for arr in arrs:
        img0 = Image.fromarray(arr)

        t0 = time.perf_counter()
        Image.fromarray(arr).resize(TARGET, Image.Resampling.BILINEAR)
        variants["pil_bilinear"].add((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        Image.fromarray(arr).resize(TARGET, Image.Resampling.BILINEAR, reducing_gap=2.0)
        variants["pil_bilinear_gap2"].add((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        Image.fromarray(arr).resize(TARGET, Image.Resampling.BOX)
        variants["pil_box"].add((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        img_r = Image.fromarray(arr).reduce(2)
        variants["pil_reduce2"].add((time.perf_counter() - t0) * 1000)

        bgr = np.asarray(img0)[:, :, ::-1]
        t0 = time.perf_counter()
        cv2.resize(bgr, TARGET, interpolation=cv2.INTER_AREA)
        variants["cv2_area"].add((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        cv2.resize(bgr, TARGET, interpolation=cv2.INTER_LINEAR)
        variants["cv2_linear"].add((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        img = Image.fromarray(arr).reduce(2)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80, subsampling=2)
        variants["pil_reduce2+jpeg"].add((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        small = cv2.resize(bgr, TARGET, interpolation=cv2.INTER_AREA)
        ok, enc = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        variants["cv2_area+jpeg"].add((time.perf_counter() - t0) * 1000)

    print(f"{'variante':<20} {'p50 ms':>8} {'p95 ms':>8}")
    rows = {}
    for k, s in variants.items():
        sm = s.summary()
        rows[k] = sm
        print(f"{k:<20} {sm['p50_ms']:>8} {sm['p95_ms']:>8}")

    best = min(rows, key=lambda k: rows[k]["p50_ms"])
    print(f"\nMás rápido: {best} ({rows[best]['p50_ms']} ms p50)")
    save_json("s4_resize.json", rows)
    print(f"Guardado: {OUT / 's4_resize.json'}")


if __name__ == "__main__":
    main()
