"""Captura de audio (WASAPI loopback y micrófono) con PyAudioWPatch.

Requisitos medidos en F0 (ver `docs/08-f0-resultados.md`):
- **Keep-alive de silencio obligatorio** para el loopback: sin audio sonando, WASAPI deja de
  entregar datos (cobertura 14.7 %, huecos de 11.3 s). Con silencio: 99.96 %, hueco máx. 33 ms.
- Abrir **al rate del dispositivo** (44.1 kHz en F0; puede variar) y dejar que FFmpeg remuestree.
- Los callbacks entregan **bloques de tamaño variable**: contar `frame_count` real.
- Ring de escritura en un hilo aparte: el callback nunca hace IO.
"""

from __future__ import annotations

import queue
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..core.errors import AudioError

CHUNK = 1024

# Flags del callback de PortAudio (¡no confundir!): paContinue=0, paComplete=1, paAbort=2.
# Devolver 1 en vez de 0 detiene el stream tras el primer bloque (bug cazado en F1).
PA_CONTINUE = 0

AudioKind = Literal["loopback", "input", "output"]


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    rate: int
    channels: int
    kind: AudioKind

    @property
    def label(self) -> str:
        return f"{self.name} · {self.rate} Hz · {self.channels} ch"


def _pa():  # pragma: no cover - depende del sistema
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as exc:  # fuera de Windows o sin instalar
        raise AudioError("PyAudioWPatch no está disponible (solo Windows)") from exc
    return pyaudio


# ⚠ Instancia única de PyAudio para todo el proceso.
# Gotcha crítico (F1): crear y `terminate()` una instancia de PyAudio mientras otra tiene
# streams vivos llama a `Pa_Terminate` globalmente y **mata** esos streams (en F0 el loopback
# dejaba de entregar datos: 1 callback y silencio). Un solo PyAudio, sin terminarlo.
_PA = None
_PA_LOCK = threading.Lock()


def _shared_pa():
    global _PA
    with _PA_LOCK:
        if _PA is None:
            api = _pa()
            _PA = api.PyAudio()
        return _PA


def shutdown() -> None:
    """Libera la instancia compartida (tests; la app no lo necesita)."""
    global _PA
    with _PA_LOCK:
        if _PA is not None:
            try:
                _PA.terminate()
            except Exception:
                pass
            _PA = None


def list_devices() -> list[AudioDevice]:
    """Dispositivos útiles: loopback del altavoz por defecto y micrófonos."""
    pa_api = _pa()
    pa = _shared_pa()
    devices: list[AudioDevice] = []
    try:
        loop = pa.get_default_wasapi_loopback()
        devices.append(
            AudioDevice(
                index=int(loop["index"]),
                name=str(loop["name"]),
                rate=int(loop["defaultSampleRate"]),
                channels=int(loop.get("maxInputChannels") or 2),
                kind="loopback",
            )
        )
    except OSError:
        pass
    for dev in pa.get_device_info_generator():
        if dev.get("isLoopbackDevice"):
            continue
        if int(dev.get("maxInputChannels") or 0) > 0:
            devices.append(
                AudioDevice(
                    index=int(dev["index"]),
                    name=str(dev["name"]),
                    rate=int(dev["defaultSampleRate"]),
                    channels=int(dev["maxInputChannels"]),
                    kind="input",
                )
            )
    return devices


def default_loopback() -> AudioDevice | None:
    return next((d for d in list_devices() if d.kind == "loopback"), None)


def device_by_index(index: int) -> AudioDevice:
    for dev in list_devices():
        if dev.index == index:
            return dev
    raise AudioError(f"no existe el dispositivo de audio {index}")


@dataclass
class AudioTrack:
    """Resultado de una pista grabada."""

    path: Path
    device: str
    rate: int
    channels: int
    frames: int
    first_callback_t: float | None  # perf_counter del primer callback
    last_callback_t: float | None = None
    drift_ppm: float = 0.0  # reloj del dispositivo vs reloj del sistema (medido)

    @property
    def seconds(self) -> float:
        return self.frames / self.rate if self.rate else 0.0


class AudioRecorder:
    """Una pista WAV incremental: callback → cola → hilo escritor."""

    def __init__(self, device: AudioDevice, out_path: Path) -> None:
        self.device = device
        self.path = out_path
        self.rate = device.rate
        self.channels = min(2, max(1, device.channels))
        self.frames = 0
        self.first_callback_t: float | None = None
        self.last_callback_t: float | None = None
        self._level = 0.0
        self._samples: list[tuple[float, int]] = []  # (t, frames) para medir la deriva del reloj
        self._callbacks = 0
        self._paused = threading.Event()
        self._queue: "queue.Queue[bytes]" = queue.Queue()
        self._stop = threading.Event()
        self._writer: threading.Thread | None = None
        self._stream = None

    # -- pausa -----------------------------------------------------------------

    def pause(self) -> None:
        """Deja de acumular audio sin cerrar el stream (los tiempos no avanzan)."""
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    # -- niveles (vúmetro) ---------------------------------------------------

    @property
    def level(self) -> float:
        """RMS 0..1 del último bloque (aprox., sin coste extra en el callback)."""
        return self._level

    def _callback(self, in_data, frame_count, time_info, status):  # noqa: ANN001, pragma: no cover
        if self._paused.is_set():
            return (None, PA_CONTINUE)  # en pausa se descarta el bloque: el WAV no avanza
        now = time.perf_counter()
        if self.first_callback_t is None:
            self.first_callback_t = now
        self.last_callback_t = now
        self.frames += frame_count
        self._callbacks += 1
        if self._callbacks % 64 == 0:  # ~1,5 s: muestreo para la deriva (regresión al parar)
            self._samples.append((now, self.frames))
        # RMS barato del bloque (int16): muestrear como máximo 512 valores del primer canal
        chunk = memoryview(in_data).cast("h")
        stride = max(1, self.channels)
        limit = min(len(chunk) // stride, 512)
        if limit > 0:
            acc = 0
            for i in range(0, limit):
                value = chunk[i * stride]
                acc += value * value
            self._level = min(1.0, (acc / limit) ** 0.5 / 32768.0)
        self._queue.put(bytes(in_data))
        return (None, PA_CONTINUE)

    def _write_loop(self) -> None:
        with wave.open(str(self.path), "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)
            wav.setframerate(self.rate)
            while not (self._stop.is_set() and self._queue.empty()):
                try:
                    data = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                wav.writeframes(data)

    def start(self) -> None:
        pa_api = _pa()
        self._pa = _shared_pa()
        self._writer = threading.Thread(target=self._write_loop, name=f"audio-{self.path.stem}", daemon=True)
        self._writer.start()
        try:
            self._stream = self._pa.open(
                format=pa_api.paInt16,
                channels=self.channels,
                rate=self.rate,
                input=True,
                input_device_index=self.device.index,
                frames_per_buffer=CHUNK,
                stream_callback=self._callback,
            )
            self._stream.start_stream()
        except Exception as exc:
            self._stop.set()
            self._writer.join(timeout=2)
            raise AudioError(f"no se pudo abrir {self.device.name}: {exc}") from exc

    def stop(self) -> AudioTrack:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._stop.set()
        if self._writer is not None:
            self._writer.join(timeout=5)
        return AudioTrack(
            path=self.path,
            device=self.device.name,
            rate=self.rate,
            channels=self.channels,
            frames=self.frames,
            first_callback_t=self.first_callback_t,
            last_callback_t=self.last_callback_t,
            drift_ppm=self._measure_drift(),
        )

    def _measure_drift(self) -> float:
        """ppm del reloj del dispositivo respecto al del sistema (regresión sin extremos).

        Los buffers de los extremos sesgan la medida (su contenido precede/sucede al instante
        del callback), así que se descartan los dos primeros y los dos últimos muestreos.
        Necesita ≥ 60 s de grabación para tener precisión útil.
        """
        samples = self._samples
        if len(samples) < 6 or self.first_callback_t is None or self.last_callback_t is None:
            return 0.0
        span = self.last_callback_t - self.first_callback_t
        if span < 60.0:
            return 0.0
        interior = samples[2:-2]
        if len(interior) < 3:
            return 0.0
        try:
            import numpy as np

            times = np.array([t for t, _ in interior], dtype=np.float64)
            seconds = np.array([f for _, f in interior], dtype=np.float64) / self.rate
            slope = float(np.polyfit(times, seconds, 1)[0])  # segundos de audio por segundo real
        except Exception:
            return 0.0
        ppm = (slope - 1.0) * 1e6
        return max(-5000.0, min(5000.0, round(ppm, 1)))


class SilenceKeepAlive:
    """Reproduce ceros por la salida por defecto para que el loopback siga entregando datos."""

    def __init__(self) -> None:
        self._pa = None
        self._stream = None
        self.device_name = ""

    def start(self) -> None:
        pa_api = _pa()
        self._pa = _shared_pa()
        try:
            dev = self._pa.get_default_output_device_info()
        except OSError as exc:
            raise AudioError(f"no hay dispositivo de salida por defecto: {exc}") from exc
        self.device_name = str(dev["name"])
        rate = int(dev["defaultSampleRate"])
        channels = min(2, max(1, int(dev.get("maxOutputChannels") or 2)))
        silence = b"\x00" * (CHUNK * 2 * channels)
        try:
            self._stream = self._pa.open(
                format=pa_api.paInt16,
                channels=channels,
                rate=rate,
                output=True,
                output_device_index=int(dev["index"]),
                frames_per_buffer=CHUNK,
                stream_callback=lambda *_: (silence, PA_CONTINUE),
            )
            self._stream.start_stream()
        except Exception as exc:
            raise AudioError(f"keep-alive de audio: {exc}") from exc
        # Deja que el motor de audio arranque antes de abrir el loopback.
        time.sleep(0.2)

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._pa = None
