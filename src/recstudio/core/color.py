"""Curvas de color compartidas por el preview y los efectos globales (puro).

Vive en `core/` porque la misma fórmula la necesitan `services.playback` (efectos por clip y
globales) y `core.global_effects` (paridad con el filtro `eq` de FFmpeg). No importa nada de
`services/` para no crear un ciclo.

La aproximación reproduce el `eq` de FFmpeg sobre RGB: contraste alrededor de 0.5, brillo sumado
y saturación respecto a la luma BT.601 (los mismos pesos que usa el propio filtro).
"""

from __future__ import annotations

import numpy as np

#: Pesos de luma BT.601 (`yuv420p`), los mismos que usa `eq`/`hue` de FFmpeg.
LUMA = np.array([0.299, 0.587, 0.114], np.float32)


def apply_eq(rgb: np.ndarray, eq: dict[str, float]) -> np.ndarray:
    """Aproximación RGB del filtro `eq` de FFmpeg (contraste alrededor de 0.5, brillo sumado,
    saturación respecto a la luma)."""
    f = rgb.astype(np.float32) * (1.0 / 255.0)
    contrast, brightness, saturation = eq["contrast"], eq["brightness"], eq["saturation"]
    if contrast != 1.0:
        f = (f - 0.5) * contrast + 0.5
    if brightness:
        f += brightness
    if saturation != 1.0:
        gray = (f @ LUMA)[..., None]
        f = gray + (f - gray) * saturation
    return (np.clip(f, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def to_grayscale(rgb: np.ndarray) -> np.ndarray:
    """Blanco y negro por luma BT.601 (equivalente visual a `hue=s=0` en el graph)."""
    f = rgb.astype(np.float32) * (1.0 / 255.0)
    gray = np.clip(f @ LUMA, 0.0, 1.0)[..., None]
    return (np.repeat(gray, 3, axis=2) * 255.0 + 0.5).astype(np.uint8)


def apply_vignette(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Multiplica la luma y la saturación por una máscara radial, como el filtro `vignette`.

    El graph trabaja en `yuv420p` de rango limitado (Y 16…235), así que el preview convierte a
    YUV BT.601 limitado, aplica la máscara sobre Y y sobre las cromas (alrededor de 128) y vuelve
    a RGB. Multiplicar directamente en RGB daría esquinas más claras que el MP4: el negro de la
    señal (Y=16) no escala con el factor.
    """
    f = rgb.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    y = 16.0 + (65.481 * r + 128.553 * g + 24.966 * b) / 255.0
    u = 128.0 + (-37.797 * r - 74.203 * g + 112.0 * b) / 255.0
    v = 128.0 + (112.0 * r - 93.786 * g - 18.214 * b) / 255.0
    m = mask[..., 0]
    y = y * m
    u = 128.0 + (u - 128.0) * m
    v = 128.0 + (v - 128.0) * m
    yy, uu, vv = y - 16.0, u - 128.0, v - 128.0
    out = np.stack(
        (
            1.164 * yy + 1.596 * vv,
            1.164 * yy - 0.392 * uu - 0.813 * vv,
            1.164 * yy + 2.017 * uu,
        ),
        axis=-1,
    )
    return (np.clip(out, 0.0, 255.0) + 0.5).astype(np.uint8)
