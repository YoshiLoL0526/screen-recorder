"""S3b — Loopback con "keep-alive" de silencio.

Hipótesis (de S3): WASAPI loopback deja de entregar datos cuando el motor de audio está
inactivo (0 callbacks durante decenas de segundos). Mitigación: reproducir silencio por el
dispositivo de salida por defecto mientras se graba.

Mide con más rigor que S3:
- cobertura (segundos de audio / tiempo real),
- drift entre el primer y el último callback (sin el sesgo de arranque/parada),
- salud de callbacks y overflows.

Uso:  uv run python tools/spikes/s3b_silence_keepalive.py [--seconds 60]
"""

from __future__ import annotations

import argparse
import json
import queue
import threading
import time
import wave

import numpy as np

from _common import OUT, save_json

import pyaudiowpatch as pyaudio

CHUNK = 1024


class LoopbackRecorder:
    def __init__(self, pa: pyaudio.PyAudio, device: dict) -> None:
        self.pa = pa
        self.device = device
        self.rate = int(device["defaultSampleRate"])
        self.channels = int(device.get("maxInputChannels") or 2)
        self.queue: queue.Queue[bytes] = queue.Queue()
        self.times: list[float] = []
        self.frames = 0
        self.overflow = 0
        self._stop = threading.Event()
        self.wav_path = OUT / "s3b_loopback.wav"
        self._writer = threading.Thread(target=self._write, daemon=True)

    def _callback(self, in_data, frame_count, time_info, status):  # noqa: ANN001
        if status:
            self.overflow += 1
        self.times.append(time.perf_counter())
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
                    w.writeframes(self.queue.get(timeout=0.2))
                except queue.Empty:
                    continue

    def start(self) -> None:
        self._writer.start()
        self.stream = self.pa.open(
            format=pyaudio.paInt16, channels=self.channels, rate=self.rate, input=True,
            input_device_index=int(self.device["index"]), frames_per_buffer=CHUNK,
            stream_callback=self._callback,
        )
        self.stream.start_stream()


class SilencePlayer:
    """Reproduce ceros por el dispositivo de salida por defecto (inaudible)."""

    def __init__(self, pa: pyaudio.PyAudio) -> None:
        self.pa = pa
        dev = pa.get_default_output_device_info()
        self.device_name = dev["name"]
        self.rate = int(dev["defaultSampleRate"])
        self.channels = min(2, int(dev.get("maxOutputChannels") or 2))

    def start(self) -> None:
        self.stream = self.pa.open(
            format=pyaudio.paInt16, channels=self.channels, rate=self.rate, output=True,
            output_device_index=int(self.pa.get_default_output_device_info()["index"]),
            frames_per_buffer=CHUNK,
            stream_callback=lambda *a: (b"\x00" * (CHUNK * 2 * self.channels), pyaudio.paContinue),
        )
        self.stream.start_stream()

    def stop(self) -> None:
        self.stream.stop_stream()
        self.stream.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60.0)
    args = ap.parse_args()

    pa = pyaudio.PyAudio()
    loopback = pa.get_default_wasapi_loopback()
    print(f"Loopback: {loopback['name']} ({loopback['defaultSampleRate']:.0f} Hz)")

    player = SilencePlayer(pa)
    print(f"Keep-alive de silencio por: {player.device_name} ({player.rate} Hz)")
    player.start()
    time.sleep(0.5)

    rec = LoopbackRecorder(pa, loopback)
    rec.start()
    t0 = time.perf_counter()
    print(f"Grabando {args.seconds:.0f}s…")
    time.sleep(args.seconds)
    rec.stop_time = time.perf_counter()
    rec.stream.stop_stream()
    rec.stream.close()
    rec._stop.set()
    rec._writer.join(timeout=5)
    player.stop()

    elapsed = rec.stop_time - t0
    with wave.open(str(rec.wav_path), "rb") as w:
        wav_frames = w.getnframes()
    wav_seconds = wav_frames / rec.rate

    times = rec.times
    if len(times) >= 2:
        span = times[-1] - times[0]
        drift_ms = (wav_seconds - elapsed) * 1000
        # drift "limpio": excluye arranque/parada (muy por debajo de una llamada)
        drift_span_ms = (wav_seconds - span) * 1000
        iv = np.diff(times) * 1000
        cb = {
            "callbacks": len(times),
            "cb_p50_ms": round(float(np.percentile(iv, 50)), 2),
            "cb_p95_ms": round(float(np.percentile(iv, 95)), 2),
            "cb_max_ms": round(float(iv.max()), 2),
        }
    else:
        span = drift_ms = drift_span_ms = 0.0
        cb = {"callbacks": len(times)}

    result = {
        "device": loopback["name"],
        "rate": rec.rate,
        "elapsed_s": round(elapsed, 2),
        "wav_seconds": round(wav_seconds, 2),
        "coverage": round(wav_seconds / elapsed, 4),
        "drift_ms": round(drift_ms, 2),
        "drift_span_ms": round(drift_span_ms, 2),
        "drift_10min_ms": round(drift_span_ms * 600 / max(span, 0.001), 1),
        **cb,
        "overflows": rec.overflow,
        "wav": str(rec.wav_path),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    pa.terminate()
    save_json("s3b.json", result)
    print(f"Guardado: {OUT / 's3b.json'}")


if __name__ == "__main__":
    main()
