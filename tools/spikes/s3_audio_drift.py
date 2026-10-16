"""S3 — Audio WASAPI loopback: estabilidad y drift.

Graba N segundos del audio del sistema con PyAudioWPatch y mide:
- drift del reloj del dispositivo respecto al reloj del sistema (samples esperados vs reales),
- salud de los callbacks (jitter, overflows, profundidad de cola),
- captura simultánea loopback + micrófono (si hay mic),
- integridad del WAV escrito.

Uso:  uv run python tools/spikes/s3_audio_drift.py [--seconds 120] [--dual-seconds 15]
"""

from __future__ import annotations

import argparse
import json
import queue
import threading
import time
import wave
from pathlib import Path

import numpy as np

from _common import OUT, save_json

import pyaudiowpatch as pyaudio

CHUNK = 1024


class Recorder:
    """Hilo lector + voltcado a WAV, con métricas de callbacks."""

    def __init__(self, pa: pyaudio.PyAudio, device: dict, name: str) -> None:
        self.pa = pa
        self.device = device
        self.name = name
        self.rate = int(device["defaultSampleRate"])
        self.channels = int(device.get("maxInputChannels") or 2)
        self.queue: queue.Queue[bytes] = queue.Queue()
        self.frames = 0
        self.overflow = 0
        self.intervals: list[float] = []
        self._last_t = time.perf_counter()
        self._max_queue = 0
        self._stop = threading.Event()
        self.wav_path = OUT / f"s3_{name}.wav"
        self._writer = threading.Thread(target=self._write, daemon=True)

    def _callback(self, in_data, frame_count, time_info, status):  # noqa: ANN001
        if status:
            self.overflow += 1
        now = time.perf_counter()
        self.intervals.append(now - self._last_t)
        self._last_t = now
        self.frames += frame_count
        self.queue.put(bytes(in_data))
        return (None, pyaudio.paContinue)

    def _write(self) -> None:
        with wave.open(str(self.wav_path), "wb") as w:
            w.setnchannels(self.channels)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            while not (self._stop.is_set() and self.queue.empty()):
                try:
                    data = self.queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                self._max_queue = max(self._max_queue, self.queue.qsize())
                w.writeframes(data)

    def start(self) -> None:
        self._writer.start()
        self.stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=int(self.device["index"]),
            frames_per_buffer=CHUNK,
            stream_callback=self._callback,
        )
        self.t0 = time.perf_counter()
        self.stream.start_stream()

    def stop(self) -> dict:
        elapsed = time.perf_counter() - self.t0
        self.stream.stop_stream()
        self.stream.close()
        self._stop.set()
        self._writer.join(timeout=5)

        with wave.open(str(self.wav_path), "rb") as w:
            wav_frames = w.getnframes()
        wav_seconds = wav_frames / self.rate
        drift_ms = (wav_seconds - elapsed) * 1000
        iv = np.array(self.intervals[1:], dtype=float) * 1000 if len(self.intervals) > 1 else np.array([0.0])
        return {
            "device": self.device["name"],
            "rate": self.rate,
            "channels": self.channels,
            "elapsed_s": round(elapsed, 2),
            "wav_frames": wav_frames,
            "wav_seconds": round(wav_seconds, 2),
            "drift_ms": round(drift_ms, 2),
            "drift_ppm": round(drift_ms / (elapsed * 1000) * 1e6, 1),
            "drift_10min_ms": round(drift_ms * 600 / elapsed, 1),
            "callbacks": len(self.intervals),
            "cb_p50_ms": round(float(np.percentile(iv, 50)), 2),
            "cb_p95_ms": round(float(np.percentile(iv, 95)), 2),
            "cb_max_ms": round(float(iv.max()), 2),
            "overflows": self.overflow,
            "max_queue": self._max_queue,
            "wav": str(self.wav_path),
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=120.0, help="duración de la prueba principal")
    ap.add_argument("--dual-seconds", type=float, default=15.0, help="prueba loopback+mic simultánea")
    args = ap.parse_args()

    pa = pyaudio.PyAudio()
    loopback = pa.get_default_wasapi_loopback()
    print(f"Loopback: {loopback['name']} ({loopback['defaultSampleRate']:.0f} Hz, {loopback['maxInputChannels']} ch)")
    try:
        mic = pa.get_default_input_device_info()
        print(f"Micrófono: {mic['name']}")
    except OSError:
        mic = None
        print("Micrófono: no hay dispositivo de entrada (se omite la prueba dual)")

    result: dict = {"loopback": loopback["name"], "seconds": args.seconds}

    print(f"\n[1/2] Loopback {args.seconds:.0f}s…")
    rec = Recorder(pa, loopback, "loopback")
    rec.start()
    t_end = time.perf_counter() + args.seconds
    last = 0.0
    while (now := time.perf_counter()) < t_end:
        if now - last >= 10.0:
            last = now
            print(f"   … {now - rec.t0:5.0f}s · {rec.frames} frames")
        time.sleep(0.25)
    solo = rec.stop()
    result["loopback_run"] = solo
    print(json.dumps(solo, indent=2, ensure_ascii=False))

    if mic is not None and args.dual_seconds > 0:
        print(f"\n[2/2] Loopback + micrófono {args.dual_seconds:.0f}s…")
        rec_lb = Recorder(pa, loopback, "dual_loopback")
        rec_mic = Recorder(pa, mic, "dual_mic")
        rec_lb.start()
        rec_mic.start()
        time.sleep(args.dual_seconds)
        dual = {"loopback": rec_lb.stop(), "mic": rec_mic.stop()}
        result["dual_run"] = dual
        for k, v in dual.items():
            print(f"  {k}: drift {v['drift_ms']} ms · overflows {v['overflows']} · cb p95 {v['cb_p95_ms']} ms")

    pa.terminate()
    save_json("s3.json", result)
    print(f"\nGuardado: {OUT / 's3.json'}")


if __name__ == "__main__":
    main()
