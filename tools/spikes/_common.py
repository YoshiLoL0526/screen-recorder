"""Utilidades compartidas de los spikes de F0."""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

# Consola de Windows (cp1252): que los símbolos no rompan los prints de los spikes.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def monitor_size() -> tuple[int, int]:
    """Tamaño físico del monitor primario."""
    import mss

    with mss.mss() as sct:
        mon = sct.monitors[1]
        return mon["width"], mon["height"]


class Series:
    """Acumula muestras y resume (ms)."""

    def __init__(self) -> None:
        self.values: list[float] = []

    def add(self, value: float) -> None:
        self.values.append(value)

    def summary(self) -> dict[str, float]:
        if not self.values:
            return {}
        vs = sorted(self.values)
        return {
            "n": len(vs),
            "mean_ms": round(statistics.mean(vs), 3),
            "p50_ms": round(statistics.median(vs), 3),
            "p95_ms": round(vs[int(len(vs) * 0.95) - 1 if len(vs) > 1 else 0], 3),
            "max_ms": round(vs[-1], 3),
        }


class Stopwatch:
    def __enter__(self) -> "Stopwatch":
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.dt = time.perf_counter() - self.t0

    @property
    def ms(self) -> float:
        return self.dt * 1000


def save_json(name: str, payload: dict[str, Any]) -> Path:
    path = OUT / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    """Tabla ASCII simple para consola."""
    widths = [max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns]
    head = "  ".join(c.ljust(w) for c, w in zip(columns, widths))
    sep = "  ".join("-" * w for w in widths)
    body = "\n".join("  ".join(str(r.get(c, "")).ljust(w) for c, w in zip(columns, widths)) for r in rows)
    return f"{head}\n{sep}\n{body}"


def cpu_percent_of(pid: int, sample_s: float = 0.5) -> float:
    """CPU medio (%) de un proceso en una ventana corta (0-100% por núcleo * núcleos)."""
    import psutil

    proc = psutil.Process(pid)
    proc.cpu_percent(None)
    time.sleep(sample_s)
    return proc.cpu_percent(None)
