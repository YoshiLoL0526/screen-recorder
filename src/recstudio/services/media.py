"""Medios del editor (PyAV): probe, waveforms y pósters, con cache en disco.

- `probe(path)` → `MediaAsset` (duración, resolución, fps, audio).
- `waveform(path)` decodifica el audio **una vez** (en streaming), lo baja a mono y guarda los
  picos min/max por bucket (`BUCKETS_PER_S` por segundo) en un `.npz` de la cache; el timeline
  los dibuja como un polígono (array numpy `(n, 2)` sin copias).
- `poster(path)` → JPEG pequeño (un frame cerca del inicio) para el inspector y la importación.

Todo esto es IO pesado: el controlador lo llama desde hilos, nunca desde un handler.
"""

from __future__ import annotations

import hashlib
import io
import threading
from pathlib import Path

import numpy as np

from ..core.errors import MediaError
from ..core.project import MediaAsset, new_id
from ..platform import paths

BUCKETS_PER_S = 100
_WAVE_RATE = 8000  # Hz de análisis: sobra para picos a 100 buckets/s

_lock = threading.Lock()
_waveforms: dict[str, np.ndarray] = {}


def _cache_key(path: Path) -> str:
    stat = path.stat()
    raw = f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}".encode()
    return hashlib.sha1(raw).hexdigest()[:20]


def _cache_dir(kind: str) -> Path:
    folder = paths.cache_dir() / kind
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def is_offline(asset: MediaAsset) -> bool:
    return not Path(asset.path).is_file()


def probe(path: str | Path, asset_id: str | None = None) -> MediaAsset:
    """Sonda un video con PyAV. `MediaError` si no se puede abrir o no tiene video."""
    import av

    path = Path(path)
    try:
        container = av.open(str(path))
    except (av.FFmpegError, OSError) as exc:
        raise MediaError(f"no se pudo abrir «{path.name}»: {exc}") from exc
    try:
        if not container.streams.video:
            raise MediaError(f"«{path.name}» no tiene pista de video")
        video = container.streams.video[0]
        duration = 0.0
        if video.duration is not None and video.time_base is not None:
            duration = float(video.duration * video.time_base)
        if duration <= 0 and container.duration:
            duration = container.duration / 1_000_000
        if duration <= 0:
            raise MediaError(f"«{path.name}»: duración desconocida")
        rate = video.average_rate or video.guessed_rate or 0
        audio = container.streams.audio
        return MediaAsset(
            id=asset_id or new_id("a"),
            path=str(path.resolve()),
            duration=round(duration, 6),
            width=int(video.codec_context.width),
            height=int(video.codec_context.height),
            fps=float(rate) if rate else 0.0,
            has_audio=bool(audio),
            audio_channels=int(audio[0].codec_context.channels) if audio else 0,
        )
    finally:
        container.close()


# -- waveforms -------------------------------------------------------------------


def cached_waveform(path: str | Path) -> np.ndarray | None:
    """Picos ya calculados (memoria o disco) sin decodificar nada; `None` si no hay."""
    key = str(Path(path))
    with _lock:
        peaks = _waveforms.get(key)
    if peaks is not None:
        return peaks
    try:
        file = _cache_dir("waveforms") / f"{_cache_key(Path(path))}.npz"
    except OSError:
        return None
    if not file.exists():
        return None
    try:
        peaks = np.load(file)["peaks"]
    except Exception:
        return None
    with _lock:
        _waveforms[key] = peaks
    return peaks


def waveform(path: str | Path) -> np.ndarray:
    """Picos `(n, 2)` float32 (min, max en [-1, 1]) a `BUCKETS_PER_S`; vacío si no hay audio."""
    import av

    path = Path(path)
    peaks = cached_waveform(path)
    if peaks is not None:
        return peaks
    bucket = _WAVE_RATE // BUCKETS_PER_S
    mins: list[np.ndarray] = []
    maxs: list[np.ndarray] = []
    carry = np.zeros(0, np.float32)
    try:
        container = av.open(str(path))
    except (av.FFmpegError, OSError) as exc:
        raise MediaError(f"no se pudo abrir «{path.name}»: {exc}") from exc
    try:
        if not container.streams.audio:
            peaks = np.zeros((0, 2), np.float32)
        else:
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="flt", layout="mono", rate=_WAVE_RATE)

            def consume(samples: np.ndarray) -> None:
                nonlocal carry
                data = np.concatenate((carry, samples)) if carry.size else samples
                whole = (data.size // bucket) * bucket
                if whole:
                    blocks = data[:whole].reshape(-1, bucket)
                    mins.append(blocks.min(axis=1))
                    maxs.append(blocks.max(axis=1))
                carry = data[whole:]

            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    consume(out.to_ndarray().reshape(-1).astype(np.float32, copy=False))
            for out in resampler.resample(None):
                consume(out.to_ndarray().reshape(-1).astype(np.float32, copy=False))
            if carry.size:
                mins.append(np.array([carry.min()], np.float32))
                maxs.append(np.array([carry.max()], np.float32))
            if mins:
                peaks = np.stack((np.concatenate(mins), np.concatenate(maxs)), axis=1)
                peaks = np.clip(peaks, -1.0, 1.0).astype(np.float32)
            else:
                peaks = np.zeros((0, 2), np.float32)
    except av.FFmpegError as exc:
        raise MediaError(f"no se pudo leer el audio de «{path.name}»: {exc}") from exc
    finally:
        container.close()
    try:
        np.savez_compressed(_cache_dir("waveforms") / f"{_cache_key(path)}.npz", peaks=peaks)
    except OSError:
        pass
    with _lock:
        _waveforms[str(path)] = peaks
    return peaks


def waveform_slice(peaks: np.ndarray, t0: float, t1: float, columns: int) -> np.ndarray:
    """Remuestrea los picos del tramo `[t0, t1)` de la fuente a `columns` columnas (min, max)."""
    if peaks.size == 0 or columns <= 0 or t1 <= t0:
        return np.zeros((0, 2), np.float32)
    i0 = max(0, int(t0 * BUCKETS_PER_S))
    i1 = min(len(peaks), max(i0 + 1, int(np.ceil(t1 * BUCKETS_PER_S))))
    part = peaks[i0:i1]
    if len(part) == 0:
        return np.zeros((0, 2), np.float32)
    edges = np.linspace(0, len(part), columns + 1).astype(int)
    edges[-1] = len(part)
    # `reduceat` reduce cada tramo [starts[k], starts[k+1]); con más columnas que buckets,
    # los tramos vacíos repiten el bucket (la onda se estira en vez de llenarse de huecos).
    starts = np.minimum(edges[:-1], len(part) - 1)
    mins = np.minimum.reduceat(part[:, 0], starts)
    maxs = np.maximum.reduceat(part[:, 1], starts)
    return np.stack((mins, maxs), axis=1)


# -- pósters -----------------------------------------------------------------------


def poster(path: str | Path, *, at_s: float = 1.0, height: int = 180) -> bytes:
    """JPEG de un frame (cacheado en disco)."""
    import av
    from PIL import Image

    path = Path(path)
    file = _cache_dir("posters") / f"{_cache_key(path)}-{height}.jpg"
    if file.exists():
        return file.read_bytes()
    try:
        container = av.open(str(path))
    except (av.FFmpegError, OSError) as exc:
        raise MediaError(f"no se pudo abrir «{path.name}»: {exc}") from exc
    try:
        stream = container.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration else 0.0
        target = min(at_s, duration / 2) if duration else 0.0
        if target > 0:
            container.seek(int(target / stream.time_base), stream=stream, backward=True)
        frame = next(container.decode(stream), None)
        if frame is None:
            raise MediaError(f"«{path.name}» no tiene frames")
        width = max(2, round(frame.width * height / frame.height))
        rgb = frame.to_ndarray(format="rgb24", width=width - width % 2, height=height)
    except av.FFmpegError as exc:
        raise MediaError(f"no se pudo decodificar «{path.name}»: {exc}") from exc
    finally:
        container.close()
    buffer = io.BytesIO()
    Image.fromarray(rgb).save(buffer, "JPEG", quality=82)
    data = buffer.getvalue()
    try:
        file.write_bytes(data)
    except OSError:
        pass
    return data
