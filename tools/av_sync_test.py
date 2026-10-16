"""Prueba de sincronía A/V de extremo a extremo (el "test de la palmada" automatizado).

Dispara **dos eventos en el mismo instante** mientras graba:
- un beep de 1 kHz por el altavoz (capturado por el loopback),
- un salto del cursor al centro de la pantalla (capturado por WGC).

Después localiza cada evento **dentro del MP4**:
- el beep con `silencedetect` (primer fin de silencio del audio),
- el cursor difereciando frames (el primer frame donde aparece en el centro).

La diferencia entre ambos instantes es el **desfase A/V real**. Criterio (RNF-02): < 80 ms.

Uso:  uv run python tools/av_sync_test.py [--seconds 6] [--event-at 2.0] [--fps 60]
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

import numpy as np  # noqa: E402

from recstudio.core.config import AudioConfig, RecordingConfig, SourceConfig  # noqa: E402
from recstudio.platform import ffmpeg as ffmpeg_mod  # noqa: E402
from recstudio.services import recording as rec  # noqa: E402

SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)")
CENTER = (960, 540)


class BeepPlayer:
    """Stream de salida pre-abierto: el beep se encola al instante (sin latencia de apertura)."""

    def __init__(self, frequency: float = 1000.0, ms: int = 150, volume: float = 0.3) -> None:
        import pyaudiowpatch as pyaudio

        self._pa = pyaudio.PyAudio()
        dev = self._pa.get_default_output_device_info()
        self.rate = int(dev["defaultSampleRate"])
        self.channels = min(2, max(1, int(dev.get("maxOutputChannels") or 2)))
        samples = int(self.rate * ms / 1000)
        t = np.arange(samples, dtype=np.float32) / self.rate
        wave = (np.sin(2 * math.pi * frequency * t) * volume * 32767).astype(np.int16)
        if self.channels == 2:
            wave = np.repeat(wave[:, None], 2, axis=1)
        self._data = wave.tobytes()
        self.stream = self._pa.open(
            format=pyaudio.paInt16, channels=self.channels, rate=self.rate, output=True,
            output_device_index=int(dev["index"]),
        )

    def beep(self) -> None:
        self.stream.write(self._data)

    def close(self) -> None:
        try:
            self.stream.stop_stream()
            self.stream.close()
        finally:
            self._pa.terminate()


def set_cursor(x: int, y: int) -> None:
    ctypes.windll.user32.SetCursorPos(x, y)


BLINK_CODE = r"""
import threading, time
from aurora import App, Signal, box

color = Signal("#FF3355")

def loop():
    while True:
        even = int(time.time()) % 2 == 0
        color.set("#FF3355" if even else "#33FF88")
        time.sleep(0.02)

threading.Thread(target=loop, daemon=True).start()
App(lambda: box(bg=color(), key="blink"), title="marcador-av", size=(420, 320)).run()
"""


def start_blink_window() -> "subprocess.Popen[bytes]":
    """Ventana Aurora que alterna rojo/verde en segundos pares del reloj del sistema."""
    return subprocess.Popen([sys.executable, "-c", BLINK_CODE])


def find_blinks(video: Path, t0_wall: float, scale: int = 4) -> list[tuple[float, float]]:
    """Detecta los parpadeos del marcador: (file_t, latencia_ms) usando el reloj epoch."""
    import av

    container = av.open(str(video))
    stream = container.streams.video[0]
    time_base = float(stream.time_base)

    series: list[tuple[float, float]] = []  # (t, señal R-G media)
    for frame in container.decode(stream):
        t = float(frame.pts * time_base) if frame.pts is not None else 0.0
        rgb = frame.to_ndarray(format="rgb24")[::scale, ::scale]
        signal = float(rgb[:, :, 0].mean()) - float(rgb[:, :, 1].mean())
        series.append((t, signal))
    container.close()
    if len(series) < 4:
        return []

    values = np.array([s for _, s in series])
    mid = (values.max() + values.min()) / 2
    if values.max() - values.min() < 8.0:  # el marcador no se ve (o no parpadea)
        return []

    flips: list[tuple[float, float]] = []
    prev_state = values[0] > mid
    for (t, signal), value in zip(series, values):
        state = value > mid
        if state != prev_state:
            # instante nominal: segundo par del epoch más cercano a t0_wall + t
            wall = t0_wall + t
            nominal = round(wall)
            nominal += (nominal % 2) and 1 or 0  # segundo par
            if abs(nominal - wall) > 0.9:
                nominal += 2 if nominal < wall else -2
            flips.append((t, (t - (nominal - t0_wall)) * 1000))
            prev_state = state
    return flips


def find_content_change(video: Path, event_at: float, scale: int = 4) -> tuple[float | None, dict]:
    """Primer frame con un cambio de contenido grande (banner) respecto a la referencia quieta."""
    import av

    container = av.open(str(video))
    stream = container.streams.video[0]
    time_base = float(stream.time_base)

    refs: list[np.ndarray] = []
    frames: list[tuple[float, np.ndarray]] = []
    for frame in container.decode(stream):
        t = float(frame.pts * time_base) if frame.pts is not None else 0.0
        arr = frame.to_ndarray(format="gray")[::scale, ::scale]
        if event_at - 1.2 <= t <= event_at - 0.3:
            refs.append(arr)
        frames.append((t, arr))
    container.close()
    if not refs:
        return None, {"error": "sin referencia"}

    ref = np.median(np.stack(refs), axis=0)
    detail = {}
    for t, arr in frames:
        if t < event_at - 0.4:
            continue
        changed = int((np.abs(arr.astype(np.int16) - ref) > 30).sum())
        if t <= event_at + 0.6 and changed > 100:
            detail.setdefault("primero", {"t": round(t, 3), "px": changed})
        if changed > 300:
            return t, detail
    return None, detail


def find_beep_t(video: Path, exe: str) -> float | None:
    """Primer fin de silencio (el beep del primer evento)."""
    beeps = find_beeps(video, exe)
    return beeps[0] if beeps else None


def find_beeps(video: Path, exe: str) -> list[float]:
    """Todos los fines de silencio del audio (cada beep sobre un fondo silencioso)."""
    proc = subprocess.run(
        [exe, "-hide_banner", "-i", str(video), "-af", "silencedetect=noise=-45dB:d=0.05",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return [float(m) for m in SILENCE_END_RE.findall(proc.stderr)]


def find_empty_spots(count: int = 2, patch: int = 120, min_distance: int = 500) -> list[tuple[int, int]]:
    """Centros de parches uniformes del escritorio (sin texto ni bordes) para el marcador."""
    import mss

    with (mss.MSS() if hasattr(mss, "MSS") else mss.mss()) as sct:
        mon = sct.monitors[1]
        shot = sct.grab(mon)
    img = np.frombuffer(shot.bgra, np.uint8).reshape(shot.height, shot.width, 4)[:, :, :3].astype(np.int16)

    height, width = img.shape[:2]
    candidates: list[tuple[float, tuple[int, int]]] = []
    step = patch
    for y in range(patch, height - patch, step):
        for x in range(patch, width - patch, step):
            region = img[y : y + patch, x : x + patch]
            if float(region.std()) < 6.0 and int(region.max() - region.min()) < 40:
                candidates.append((float(region.std()), (x + patch // 2, y + patch // 2)))

    chosen: list[tuple[int, int]] = []
    for _, (x, y) in sorted(candidates):
        if all((x - cx) ** 2 + (y - cy) ** 2 > min_distance**2 for cx, cy in chosen):
            chosen.append((x, y))
            if len(chosen) == count:
                return chosen

    # respaldos garantizados: esquinas/centro, evitando los ya elegidos
    fallbacks = [
        (150, 150),
        (width - 200, height - 200),
        (width - 200, 150),
        (150, height - 200),
        (width // 2, height // 3),
        (width // 3, 2 * height // 3),
    ]
    for point in fallbacks:
        if len(chosen) >= count:
            break
        if all((point[0] - cx) ** 2 + (point[1] - cy) ** 2 > 60**2 for cx, cy in chosen):
            chosen.append(point)
    while len(chosen) < count:
        chosen.append(fallbacks[len(chosen) % len(fallbacks)])
    return chosen[:count]


def _patch_changed(a: np.ndarray, b: np.ndarray) -> bool:
    diff = np.abs(a - b)
    return int(diff.max()) > 60 and int((diff > 30).sum()) > 6


def find_cursor_move(
    video: Path,
    pos_from: tuple[int, int],
    pos_to: tuple[int, int],
    event_at: float,
    patch: int = 120,
) -> tuple[float | None, dict]:
    """Frame del salto: patch B distinto de su referencia quieta y patch A también.

    La referencia se toma del tramo silencioso previo al evento (mediana de varios frames),
    así no se exige que ambos cambios caigan en el mismo diff (los frames duplicados no sesgan).
    """
    import av

    container = av.open(str(video))
    stream = container.streams.video[0]
    time_base = float(stream.time_base)

    def region_of(arr: np.ndarray, cx: int, cy: int) -> np.ndarray:
        half = patch // 2
        h, w = arr.shape
        return arr[max(0, cy - half) : min(h, cy + half), max(0, cx - half) : min(w, cx + half)].astype(np.int16)

    ref_a: list[np.ndarray] = []
    ref_b: list[np.ndarray] = []
    frames: list[tuple[float, np.ndarray, np.ndarray]] = []
    for frame in container.decode(stream):
        t = float(frame.pts * time_base) if frame.pts is not None else 0.0
        arr = frame.to_ndarray(format="gray")
        ra, rb = region_of(arr, *pos_from), region_of(arr, *pos_to)
        # referencia: frames entre 1.2 s y 0.3 s antes del evento
        if event_at - 1.2 <= t <= event_at - 0.3:
            ref_a.append(ra)
            ref_b.append(rb)
        frames.append((t, ra, rb))

    if not ref_a:
        container.close()
        return None, {"error": "sin tramo de referencia"}
    med_a = np.median(np.stack(ref_a), axis=0)
    med_b = np.median(np.stack(ref_b), axis=0)

    result_t: float | None = None
    detail: dict = {}
    for t, ra, rb in frames:
        if t < event_at - 0.4:
            continue
        diff_a = np.abs(ra - med_a)
        diff_b = np.abs(rb - med_b)
        changed_a = int(diff_a.max()) > 60 and int((diff_a > 30).sum()) > 6
        changed_b = int(diff_b.max()) > 60 and int((diff_b > 30).sum()) > 6
        if t <= event_at + 0.5 and (changed_a or changed_b):
            detail.setdefault("primero", {"t": round(t, 3), "a": changed_a, "b": changed_b})
        if changed_a and changed_b:
            result_t = t
            break
    container.close()
    return result_t, detail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--event-at", type=float, default=2.0)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("-o", "--out", default="tools/spikes/out/f1")
    args = ap.parse_args()

    player = BeepPlayer()
    marker = start_blink_window()
    time.sleep(2.5)  # deja que la ventana marcador aparezca
    spots = find_empty_spots(2)
    pos_from, pos_to = spots[0], spots[1]
    print(f"marcador visual: cursor de {pos_from} → {pos_to} · ventana parpadeante (rojo/verde)")

    config = RecordingConfig(
        fps=args.fps,
        preset="media",
        source=SourceConfig(kind="monitor", monitor_index=1),
        audio=AudioConfig(system=True, keepalive=True),
    )
    session = rec.RecordingSession(config, out_dir=args.out)
    session.start()
    t0_wall = time.time() - session.stats().elapsed_s
    set_cursor(*pos_from)  # el cursor empieza en el punto de origen

    event_at: float | None = None
    t_start = time.perf_counter()
    quiet_announced = False
    try:
        while time.perf_counter() - t_start < args.seconds:
            elapsed = session.stats().elapsed_s
            if event_at is None and elapsed >= args.event_at:
                event_at = elapsed
                player.beep()           # evento de audio
                set_cursor(*pos_to)     # evento de vídeo (cursor)
                print("\n" + "█" * 96 + f"\n  beep + cursor a {event_at:.3f}s")
                quiet_announced = True  # ya estamos en silencio de terminal
            # silencio de terminal ±0.8s alrededor del evento (patches limpios en el vídeo)
            if event_at is not None and event_at - 0.8 <= elapsed <= event_at + 0.8:
                if not quiet_announced:
                    print("   (silencio de terminal para el marcador visual…)")
                    quiet_announced = True
                time.sleep(0.03)
                continue
            print(f"\r  {elapsed:6.2f}s", end="", flush=True)
            time.sleep(0.03)
    except KeyboardInterrupt:
        pass
    print()
    player.close()
    marker.terminate()
    result = session.stop()

    exe = ffmpeg_mod.ffmpeg_exe()
    beep_t = find_beep_t(result.mp4, exe)
    cursor_t, detail = find_cursor_move(result.mp4, pos_from, pos_to, event_at)
    flips = find_blinks(result.mp4, t0_wall)
    if beep_t is None or event_at is None:
        print(f"✖ no se localizaron los eventos (beep={beep_t})")
        return 1

    audio_lat = (beep_t - event_at) * 1000
    cursor_lat = (cursor_t - event_at) * 1000 if cursor_t else None
    content_lat = None
    # el parpadeo más cercano al evento para el desfase A/V de contenido
    content_skew = None
    content_lat_nearest = None
    if flips:
        nearest = min(flips, key=lambda f: abs(f[0] - event_at))
        content_lat_nearest = nearest[1]
        content_skew = (beep_t - nearest[0]) * 1000
        content_lat = float(np.mean([lat for _, lat in flips]))

    print(f"· evento disparado a los {event_at:.3f}s del primer frame")
    print(f"· latencia de audio (beep): {audio_lat:+.0f} ms")
    if content_lat is not None:
        lat_list = ", ".join(f"{lat:+.0f}" for _, lat in flips)
        print(f"· latencia de vídeo (contenido, {len(flips)} parpadeos: {lat_list} ms) → media {content_lat:+.0f} ms")
        print(f"·   → desfase A/V de contenido (parpadeo más cercano): {content_skew:+.0f} ms")
    else:
        print("· vídeo (contenido): no se detectó la ventana parpadeante")
    if cursor_lat is not None:
        print(f"· latencia de vídeo (cursor): {cursor_lat:+.0f} ms → desfase A/V: {(beep_t - cursor_t) * 1000:+.0f} ms")
    verdict = "OK" if content_skew is not None and abs(content_skew) < 80 else "REVISAR"
    print(f"· veredicto (contenido, RNF-02 < 80 ms): {verdict}")
    print(f"· vídeo: {result.mp4.name} ({result.duration_s:.2f}s, {result.width}×{result.height})")
    print(json.dumps({
        "event_at": round(event_at, 3),
        "beep_t": round(beep_t, 3),
        "cursor_t": round(cursor_t, 3) if cursor_t else None,
        "flips": [[round(t, 3), round(lat, 1)] for t, lat in flips],
        "audio_latency_ms": round(audio_lat, 1),
        "content_latency_ms": round(content_lat, 1) if content_lat is not None else None,
        "cursor_latency_ms": round(cursor_lat, 1) if cursor_lat is not None else None,
        "skew_content_ms": round(content_skew, 1) if content_skew is not None else None,
        "detalle_cursor": detail,
        "video": str(result.mp4),
    }))
    return 0 if verdict == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
