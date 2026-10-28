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
import os
import threading
from pathlib import Path

import numpy as np

from ..core.errors import MediaError
from ..core.project import MediaAsset, new_id
from ..platform import paths

BUCKETS_PER_S = 100
_WAVE_RATE = 8000  # Hz de análisis: sobra para picos a 100 buckets/s
THUMB_H = 72  # alto de cada miniatura del filmstrip (px)
THUMB_COUNT = 24  # miniaturas por asset, repartidas por toda su duración

_lock = threading.Lock()
_waveforms: dict[str, np.ndarray] = {}
_thumbstrips: dict[str, tuple[bytes, ...]] = {}


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


# -- keyframes (corte rápido) ------------------------------------------------------------


def keyframes(path: str | Path) -> tuple[float, ...]:
    """Instantes (s, ordenados) de los keyframes del video, cacheados en disco.

    Solo demultiplexa (no decodifica): `packet.is_keyframe`. Los tiempos se dan respecto al
    inicio del contenedor, que es como los interpreta `ffmpeg -ss` (ver `core.keyframes`).
    """
    import av

    path = Path(path)
    try:
        file = _cache_dir("keyframes") / f"{_cache_key(path)}.npy"
    except OSError as exc:
        raise MediaError(f"no se pudo leer «{path.name}»: {exc}") from exc
    if file.exists():
        try:
            return tuple(float(t) for t in np.load(file))
        except Exception:
            pass
    try:
        container = av.open(str(path))
    except (av.FFmpegError, OSError) as exc:
        raise MediaError(f"no se pudo abrir «{path.name}»: {exc}") from exc
    try:
        stream = container.streams.video[0]
        origin = (container.start_time or 0) / 1_000_000
        times = sorted(
            float(packet.pts * stream.time_base) - origin
            for packet in container.demux(stream)
            if packet.is_keyframe and packet.pts is not None
        )
    except av.FFmpegError as exc:
        raise MediaError(f"no se pudo indexar «{path.name}»: {exc}") from exc
    finally:
        container.close()
    try:
        np.save(file, np.asarray(times, np.float64))
    except OSError:
        pass
    return tuple(times)


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


# -- filmstrip del timeline --------------------------------------------------------


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    """Escribe `data` y publica el archivo de golpe (tmp + `os.replace`).

    La UI lee la cache de miniaturas mientras el hilo las genera. Un `write_bytes` directo deja
    el archivo visible a medio escribir; si la última miniatura se leía en ese instante, la tira
    quedaba cacheada en memoria con un JPEG de 0 bytes y el siguiente frame fallaba al decodificar
    (lo destapó el CI, que es más lento que esta máquina: `test_drags_de_trim_y_mover_con_undo`).
    """
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def cached_thumbstrip(path: str | Path) -> tuple[bytes, ...] | None:
    """Miniaturas ya calculadas (memoria o disco) sin decodificar nada; `None` si no hay."""
    key = str(Path(path))
    with _lock:
        thumbs = _thumbstrips.get(key)
    if thumbs is not None:
        return thumbs
    try:
        folder = _cache_dir("thumbs") / _cache_key(Path(path))
    except OSError:
        return None
    files = [folder / f"{i:02d}.jpg" for i in range(THUMB_COUNT)]
    if not all(f.is_file() for f in files):
        return None
    try:
        thumbs = tuple(f.read_bytes() for f in files)
    except OSError:
        return None
    # Una cache vieja o a medio escribir no se usa: se regenera con `thumbstrip`.
    if any(not data.startswith(b"\xff\xd8") for data in thumbs):
        return None
    with _lock:
        _thumbstrips[key] = thumbs
    return thumbs


def _frame_at(container, stream, target: float, origin: float):  # noqa: ANN001, ANN202
    """Primer frame desde la posición actual con `time - origin >= target` (o el último)."""
    last = None
    for frame in container.decode(stream):
        last = frame
        if frame.time is not None and float(frame.time) - origin >= target:
            return frame
    return last


def thumbstrip(path: str | Path) -> tuple[bytes, ...]:
    """`THUMB_COUNT` miniaturas (JPEG) repartidas por toda la duración, cacheadas en disco.

    Es la tira del filmstrip del timeline: se calcula **por asset**, no por clip, así que recortar,
    mover o reordenar clips reutiliza las mismas miniaturas (cada clip elige las que caen en su
    tramo de fuente).
    """
    import av
    from PIL import Image

    path = Path(path)
    cached = cached_thumbstrip(path)
    if cached is not None:
        return cached
    try:
        folder = _cache_dir("thumbs") / _cache_key(path)
    except OSError as exc:
        raise MediaError(f"no se pudo escribir la cache de miniaturas: {exc}") from exc
    try:
        container = av.open(str(path))
    except (av.FFmpegError, OSError) as exc:
        raise MediaError(f"no se pudo abrir «{path.name}»: {exc}") from exc
    thumbs: list[bytes] = []
    try:
        stream = container.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration else 0.0
        if duration <= 0:
            duration = (container.duration or 0) / 1_000_000
        origin = (container.start_time or 0) / 1_000_000
        folder.mkdir(parents=True, exist_ok=True)
        for index in range(THUMB_COUNT):
            target = duration * (index + 0.5) / THUMB_COUNT if duration else 0.0
            container.seek(int((origin + target) / stream.time_base), stream=stream, backward=True)
            frame = _frame_at(container, stream, target, origin)
            if frame is None:
                break
            width = max(2, round(frame.width * THUMB_H / frame.height))
            width -= width % 2
            rgb = frame.to_ndarray(format="rgb24", width=width, height=THUMB_H)
            buffer = io.BytesIO()
            Image.fromarray(rgb).save(buffer, "JPEG", quality=78)
            data = buffer.getvalue()
            if not data:
                continue
            thumbs.append(data)
            try:
                _write_bytes_atomic(folder / f"{index:02d}.jpg", data)
            except OSError:
                pass
    except av.FFmpegError as exc:
        raise MediaError(f"no se pudo leer «{path.name}»: {exc}") from exc
    finally:
        container.close()
    if not thumbs:
        raise MediaError(f"«{path.name}» no tiene frames para el filmstrip")
    result = tuple(thumbs)
    with _lock:
        _thumbstrips[str(path)] = result
    return result
