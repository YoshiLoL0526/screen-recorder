"""Motor de grabación: une captura, audio y FFmpeg en una sesión con máquina de estados.

Flujo (ver `docs/03-grabacion.md`):

    start() → [captura: loop a fps objetivo → stdin rawvideo] → video.mkv
              [audio: loopback (+ keep-alive) y/o mic → WAV por pista]
    pause()/resume() → se deja de alimentar video y audio (tiempos continuos)
    stop()  → mux (`-c:v copy`, AAC 48 k, offsets medidos) → MP4 + miniatura + sidecar JSON

Si la app muere a mitad, el subdirectorio temporal queda con `video.mkv` + WAVs y el
`session.json` en estado `recording`: `scan_recoverable()` lo detecta y `recover_session()`
lo muxea.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

import numpy as np

from ..core.config import AudioConfig, RecordingConfig
from ..core.errors import CaptureError, ConfigError, DiskSpaceError, FfmpegError, RecStudioError
from ..platform import audio as audio_mod
from ..platform import camera as camera_mod
from ..platform import capture as capture_mod
from ..platform import cursor as cursor_mod
from ..platform import ffmpeg as ffmpeg_mod
from ..platform import paths
from ..platform import screens as screens_mod

EventFn = Callable[[str, dict], None]


def _write_json_atomic(path: Path, payload: str) -> None:
    """JSON con tmp + `os.replace`: un lector nunca ve el archivo truncado o vacío.

    `session.json` lo lee `tests/test_revision` y la recuperación mientras el hilo de captura
    puede reescribirlo; con `write_text` directo se veía un archivo vacío a mitad de escritura.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class RecState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"
    MUXING = "muxing"
    DONE = "done"
    ERROR = "error"


@dataclass
class RecordingStats:
    state: str = RecState.IDLE.value
    elapsed_s: float = 0.0  # tiempo grabado (sin pausas)
    frames_sent: int = 0
    duplicated: int = 0
    skipped: int = 0  # frames que el slot de captura descartó
    fps_real: float = 0.0
    size_bytes: int = 0
    disk_free_mb: float = 0.0
    level: float = 0.0  # vúmetro: máximo de las pistas activas
    camera_active: bool = False  # la webcam está grabando junto a la pantalla (docs/49)
    error: str | None = None

    def copy(self) -> "RecordingStats":
        return RecordingStats(**asdict(self))


@dataclass
class RecordingResult:
    mp4: Path
    thumbnail: Path | None
    sidecar: Path
    duration_s: float
    width: int
    height: int
    fps: float
    backend: str
    encoder: str
    frames: int
    duplicated: int
    skipped: int
    audio: list[audio_mod.AudioTrack]
    temp_dir: Path
    started_at: str
    ended_at: str
    warnings: list[str] = field(default_factory=list)
    camera: Path | None = None  # `<nombre>-camara.mp4` (o .mkv si el remux falló)
    camera_offset_s: float = 0.0  # t0_video − t0_cámara: desfase para el PiP automático
    mic: Path | None = None  # `<nombre>-microfono.wav` (solo modo «pistas separadas»)
    mic_offset_s: float = 0.0  # t0_mic − t0_video: desfase para la pista «Micrófono»


class RecordingSession:
    def __init__(
        self,
        config: RecordingConfig,
        *,
        out_dir: str | Path | None = None,
        temp_dir: str | Path | None = None,
        on_event: EventFn | None = None,
    ) -> None:
        self.config = config.validate()
        configured = str(out_dir).strip() if out_dir is not None else ""
        self.out_dir = Path(configured) if configured else paths.videos_dir()
        if not paths.ensure_dir(self.out_dir):
            raise DiskSpaceError(
                f"no se puede escribir en «{self.out_dir}» (el sistema la bloquea o no hay "
                "permisos); elige otra carpeta de grabaciones"
            )
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        self.temp_dir = Path(temp_dir) if temp_dir is not None else paths.temp_root() / f"rec_{stamp}"
        if not paths.ensure_dir(self.temp_dir):
            raise DiskSpaceError(f"no se puede crear la carpeta temporal «{self.temp_dir}»")
        self.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.base_name = stamp

        self._state = RecState.IDLE
        self._stats = RecordingStats()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._on_event = on_event
        self._warnings: list[str] = []

        self._backend: capture_mod.CaptureBackend | None = None
        self._backend_name = ""
        self._encoder = ""
        self._proc: subprocess.Popen[bytes] | None = None
        self._capture_thread: threading.Thread | None = None
        self._recorders: list[audio_mod.AudioRecorder] = []
        self._keepalive: audio_mod.SilenceKeepAlive | None = None
        # Webcam (docs/49): vídeo aparte, nunca dentro del pipe de la pantalla.
        self._camera: camera_mod.CameraRecorder | None = None
        self._camera_track: camera_mod.CameraTrack | None = None
        self._camera_temp = self.temp_dir / "camera.mkv"
        self._camera_device = str(self.config.camera.device_name or "")
        self._camera_started = False
        self._camera_started_at: float | None = None
        self._camera_offset = 0.0
        self._camera_failed = False
        self._camera_meta: dict | None = None
        # Micrófono separado (docs/50): solo se copia al lado del MP4 en modo `separate`.
        self._mic_device = ""
        self._mic_meta: dict | None = None

        self._size = (0, 0)
        self._crop: tuple[int, int, int, int] | None = None
        self._last_frame: np.ndarray | None = None
        self._cursor_mode = "off"
        self._cursor: cursor_mod.CursorProvider | None = None
        self._cursor_origin = (0, 0)
        self._t0_video: float | None = None
        self._pause_total = 0.0
        self._paused_at = 0.0
        self._stderr_path = self.temp_dir / "ffmpeg.log"
        self._mkv = self.temp_dir / "video.mkv"

    # -- estado ---------------------------------------------------------------

    @property
    def state(self) -> RecState:
        return self._state

    @property
    def backend_name(self) -> str:
        return self._backend_name

    @property
    def encoder_name(self) -> str:
        return self._encoder

    @property
    def camera_active(self) -> bool:
        """La cámara arrancó y sigue viva (si falló, la grabación de pantalla continúa)."""
        return self._camera is not None and self._camera.running

    @property
    def stopped_by_itself(self) -> bool:
        """El bucle de captura se paró solo (error, disco, tamaño, fuente): falta `stop()`."""
        return self._stop.is_set() and self._state in (
            RecState.RECORDING, RecState.PAUSED, RecState.ERROR
        )

    def stats(self) -> RecordingStats:
        with self._lock:
            return self._stats.copy()

    def preview_frame(self) -> np.ndarray | None:
        """Copia del último frame (ya recortado a la región) para el preview de la UI."""
        frame = self._last_frame
        if frame is None:
            return None
        try:
            return self._prepare(frame).copy()
        except Exception:
            return None

    def _event(self, name: str, **data: object) -> None:
        if self._on_event is not None:
            try:
                self._on_event(name, data)
            except Exception:
                pass

    def _warn(self, message: str) -> None:
        self._warnings.append(message)
        self._event("warning", message=message)

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._stats.error = message
            self._stats.state = RecState.ERROR.value
        self._state = RecState.ERROR

    # -- ciclo de vida ---------------------------------------------------------

    def start(self) -> None:
        if self._state is not RecState.IDLE:
            raise RecStudioError(f"la sesión ya está en {self._state.value}")
        self._state = RecState.STARTING
        self._event("starting")
        try:
            self._start_internal()
        except Exception as exc:
            self._set_error(str(exc))
            self._cleanup()
            raise
        self._state = RecState.RECORDING
        with self._lock:
            self._stats.state = RecState.RECORDING.value
        self._event("recording", backend=self._backend_name, encoder=self._encoder)

    def pause(self) -> bool:
        """Pausa el vídeo y el audio. Con cámara activa devuelve `False` y avisa.

        La cámara es un proceso FFmpeg aparte: pausarla exigiría cerrar y concatenar segmentos
        (con riesgo de desfase). Se elige la opción simple y honesta: no se pausa y se explica
        en la UI; el usuario puede detener y volver a grabar (docs/49).
        """
        if self._state is not RecState.RECORDING:
            return False
        if self.config.camera.enabled:
            self._warn(
                "la pausa no está disponible con la cámara activa: detén la grabación y vuelve "
                "a empezar sin cámara si necesitas pausar"
            )
            return False
        self._paused.set()
        self._paused_at = time.perf_counter()
        for recorder in self._recorders:
            recorder.pause()
        self._state = RecState.PAUSED
        with self._lock:
            self._stats.state = RecState.PAUSED.value
        self._event("paused")
        return True

    def resume(self) -> None:
        if self._state is not RecState.PAUSED:
            return
        self._pause_total += time.perf_counter() - self._paused_at
        self._paused.clear()
        for recorder in self._recorders:
            recorder.resume()
        self._state = RecState.RECORDING
        with self._lock:
            self._stats.state = RecState.RECORDING.value
        self._event("resumed")

    def stop(self) -> RecordingResult:
        # ERROR solo si llegó a grabar (FFmpeg murió a mitad, etc.): se libera todo y se muxea
        # lo que haya en el MKV, que es resiliente a cortes.
        started = self._capture_thread is not None
        if self._state not in (RecState.RECORDING, RecState.PAUSED) and not (
            self._state is RecState.ERROR and started
        ):
            raise RecStudioError(f"no se puede detener una sesión en {self._state.value}")
        if self._state is RecState.PAUSED:
            self.resume()
        error = self._stats.error if self._state is RecState.ERROR else None
        if error:
            self._warnings.append(f"la grabación se interrumpió: {error}")
        self._state = RecState.STOPPING
        with self._lock:
            self._stats.state = RecState.STOPPING.value
        self._event("stopping")

        tracks = self._release(join_timeout=10)

        if not self._mkv.exists() or self._mkv.stat().st_size == 0:
            self._set_error("no se escribió ningún frame")
            raise FfmpegError("no se escribió ningún frame", details=ffmpeg_mod.stderr_tail(self._stderr_path))

        self._state = RecState.MUXING
        with self._lock:
            self._stats.state = RecState.MUXING.value
        self._event("muxing")

        offsets = {
            track.path.name: round(offset, 4)
            for track, offset in ((t, self._audio_offset(t)) for t in tracks)
        }
        result = self._mux(tracks, offsets, recover=False)

        # sesión completada: marcarla y limpiar temporales (los aborted/crashed se conservan)
        self._write_session_json("done", offsets)
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

        self._state = RecState.DONE
        with self._lock:
            self._stats.state = RecState.DONE.value
        self._event("done", mp4=str(result.mp4))
        return result

    def abort(self) -> None:
        """Detiene sin muxear (los temporales quedan para recuperación manual)."""
        self._release(join_timeout=5)
        self._write_session_json("aborted", {})
        self._state = RecState.IDLE

    def _release(self, *, join_timeout: float) -> list[audio_mod.AudioTrack]:
        """Para captura, FFmpeg, audio y keep-alive (idempotente); devuelve las pistas."""
        self._stop.set()
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=join_timeout)
        self._close_ffmpeg()
        tracks = [recorder.stop() for recorder in self._recorders]
        self._recorders.clear()
        if self._keepalive is not None:
            self._keepalive.stop()
            self._keepalive = None
        if self._camera is not None:
            try:
                self._camera_track = self._camera.stop()
            except Exception:
                self._camera_track = None
            self._camera = None
        if self._backend is not None:
            self._backend.stop()
            self._backend = None
        return tracks

    # -- arranque (interno) -----------------------------------------------------

    def _start_internal(self) -> None:
        cfg = self.config
        available = ffmpeg_mod.probe_encoders()
        self._encoder = ffmpeg_mod.pick_encoder(cfg.encoder, available)
        exe = ffmpeg_mod.ffmpeg_exe()

        self._backend = capture_mod.create_backend(cfg.source, fps=cfg.fps)
        self._backend_name = self._backend.name
        first = self._wait_first_frame()
        self._last_frame = first

        height, width = first.shape[:2]
        width &= ~1
        height &= ~1  # H.264/yuv420p exige dimensiones pares
        if cfg.source.is_region:
            x, y, rw, rh = cfg.source.region  # type: ignore[misc]
            rw &= ~1
            rh &= ~1
            x = max(0, min(x, width - rw))
            y = max(0, min(y, height - rh))
            self._crop = (x, y, rw, rh)
            self._size = (rw, rh)
        else:
            self._crop = None
            self._size = (width, height)

        # Cursor propio: se dibuja en el loop con la posición leída en el instante de captura.
        mode = cfg.source.cursor
        if mode == "software":
            if cfg.source.kind == "window":
                self._warn("el cursor propio no está disponible en ventanas: se usa el del sistema")
                mode = "native"
            else:
                monitor = next(
                    (m for m in screens_mod.list_monitors() if m.index == cfg.source.monitor_index), None
                )
                if monitor is None:
                    mode = "off"
                else:
                    self._cursor = cursor_mod.CursorProvider()
                    crop_x, crop_y = (self._crop[0], self._crop[1]) if self._crop else (0, 0)
                    self._cursor_origin = (monitor.left + crop_x, monitor.top + crop_y)
        self._cursor_mode = mode

        cmd = ffmpeg_mod.capture_cmd(
            exe,
            encoder=self._encoder,
            size=self._size,
            fps=cfg.fps,
            pixel_format=self._backend.pixel_format,
            out_path=self._mkv,
            cq=cfg.quality["cq"],
            crf=cfg.quality["crf"],
            max_height=cfg.quality["max_height"],
        )
        self._proc = ffmpeg_mod.run_capture_process(cmd, self._stderr_path)
        self._start_camera()

        if cfg.audio.any:
            if cfg.audio.system and cfg.audio.keepalive:
                self._keepalive = audio_mod.SilenceKeepAlive()
                self._keepalive.start()
            if cfg.audio.system:
                device = audio_mod.default_loopback()
                if device is None:
                    self._warn("no hay dispositivo loopback: se graba sin audio del sistema")
                else:
                    recorder = audio_mod.AudioRecorder(device, self.temp_dir / "audio_system.wav")
                    recorder.start()
                    self._recorders.append(recorder)
            if cfg.audio.mic_device_index is not None:
                device = audio_mod.device_by_index(cfg.audio.mic_device_index)
                recorder = audio_mod.AudioRecorder(device, self.temp_dir / "audio_mic.wav")
                recorder.start()
                self._mic_device = device.name
                self._recorders.append(recorder)
            time.sleep(0.2)  # deja llegar los primeros callbacks (t0 medible)

        self._capture_thread = threading.Thread(target=self._capture_loop, name="rec-capture", daemon=True)
        self._capture_thread.start()
        self._write_session_json("recording", self._current_offsets())

    def _wait_first_frame(self, timeout: float = 6.0) -> np.ndarray:
        assert self._backend is not None
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            frame = self._backend.grab()
            if frame is not None and frame.size:
                return frame
            if self._backend.lost_source:
                raise CaptureError("la fuente de captura se cerró antes de empezar")
            time.sleep(0.01)
        raise CaptureError("no llegó ningún frame de la fuente en 6 s")

    # -- webcam -----------------------------------------------------------------

    def _start_camera(self) -> None:
        """Arranca la cámara; si falla, avisa y sigue con la pantalla (nunca lanza)."""
        cfg = self.config.camera
        if not cfg.enabled:
            return
        try:
            recorder = camera_mod.CameraRecorder(
                cfg.device_name or "",
                self._camera_temp,
                width=cfg.width,
                height=cfg.height,
                fps=cfg.fps,
                on_failure=self._camera_failure,
            )
            recorder.start()
        except Exception as exc:  # hardware, permisos, FFmpeg…: la sesión sigue sin cámara
            self._camera = None
            self._camera_failed = True
            detail = getattr(exc, "message", str(exc))
            self._warn(
                f"la cámara «{cfg.device_name}» no se pudo iniciar ({detail}); "
                "se graba solo la pantalla"
            )
            return
        self._camera = recorder
        self._camera_started = True
        self._camera_started_at = time.perf_counter()
        self._camera_offset = 0.0  # se fija con el primer frame de pantalla
        self._camera_failed = False
        with self._lock:
            self._stats.camera_active = True

    def _camera_failure(self, message: str) -> None:
        """Callback del vigilante de la cámara (hilo propio): solo avisa, no detiene nada."""
        self._camera_failed = True
        with self._lock:
            self._stats.camera_active = False
        self._warn(f"{message}; la grabación de pantalla continúa")

    # -- bucle de captura --------------------------------------------------------

    def _capture_loop(self) -> None:
        cfg = self.config
        frame_interval = 1.0 / cfg.fps
        next_tick = time.perf_counter()
        last_housekeeping = 0.0
        skipped_seen = 0

        while not self._stop.is_set():
            now = time.perf_counter()
            if now < next_tick:
                time.sleep(min(0.004, next_tick - now))
                continue
            if now - next_tick > 0.5:  # retraso grande (suspensión): realinear
                next_tick = now
            next_tick += frame_interval

            assert self._backend is not None and self._proc is not None
            if self._backend.lost_source:
                self._warn("la fuente de captura se cerró")
                self._stop.set()
                break

            try:
                frame = self._backend.grab()
            except RecStudioError as exc:
                self._set_error(str(exc))
                self._stop.set()
                break

            duplicated = False
            if frame is None:
                frame = self._last_frame
                duplicated = True
            else:
                self._last_frame = frame

            if self._paused.is_set():
                continue  # pausa: no se escribe nada (los tiempos del archivo no avanzan)

            if frame is None:
                continue

            prepared = self._prepare(frame)
            if self._cursor is not None:
                snapshot = self._cursor.snapshot()
                if snapshot is not None:
                    # on_copy: `_prepare` puede devolver una vista de `_last_frame`; dibujar
                    # in place quemaría el cursor en el frame base y dejaría rastro (F1.1).
                    prepared = cursor_mod.draw_cursor(
                        prepared, snapshot, self._cursor_origin, on_copy=True
                    )
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.write(prepared.tobytes())
            except (BrokenPipeError, ValueError, OSError):
                self._set_error("FFmpeg terminó de forma inesperada")
                self._stop.set()
                break
            if self._t0_video is None:
                self._t0_video = time.perf_counter()
                if self._camera_started_at is not None:
                    # Positivo: la cámara lleva más tiempo grabando que la pantalla; el PiP
                    # automático avanza `in_` ese desfase para alinear la cara (docs/49).
                    self._camera_offset = round(self._t0_video - self._camera_started_at, 4)
                # ya hay t0: guardar los offsets reales para una posible recuperación (al
                # arrancar aún valían 0 y el MP4 recuperado salía desfasado ~200 ms)
                self._write_session_json("recording", self._current_offsets())

            skipped = getattr(self._backend, "skipped", 0)
            with self._lock:
                stats = self._stats
                stats.frames_sent += 1
                if duplicated:
                    stats.duplicated += 1
                if skipped_seen != skipped:
                    stats.skipped += skipped - skipped_seen
                    skipped_seen = skipped
                if self._t0_video is not None:
                    elapsed = time.perf_counter() - self._t0_video - self._pause_total
                    stats.elapsed_s = elapsed
                    stats.fps_real = stats.frames_sent / elapsed if elapsed > 0.5 else 0.0
                stats.level = max((r.level for r in self._recorders), default=0.0)
                stats.camera_active = self.camera_active

            now = time.perf_counter()
            if now - last_housekeeping >= 1.0:
                last_housekeeping = now
                if not self._housekeeping():
                    self._stop.set()
                    break

        # el consumidor terminó; el `stop()` cierra el resto

    def _housekeeping(self) -> bool:
        """Comprueba disco, tamaño máximo y salud de ffmpeg. False ⇒ pedir parada."""
        assert self._proc is not None
        rc = self._proc.poll()
        if rc is not None:
            self._set_error(f"FFmpeg salió con código {rc}")
            return False

        try:
            size = self._mkv.stat().st_size
        except OSError:
            size = 0
        try:
            usage = shutil.disk_usage(self.temp_dir)
            free_mb = usage.free / 1e6
        except OSError:
            free_mb = float("inf")

        with self._lock:
            self._stats.size_bytes = size
            self._stats.disk_free_mb = free_mb

        if free_mb < self.config.min_free_disk_mb:
            self._warn(f"disco casi lleno ({free_mb:.0f} MB libres): se detiene la grabación")
            return False
        if self.config.max_size_gb is not None and size / 1e9 >= self.config.max_size_gb:
            self._warn(f"límite de tamaño alcanzado ({self.config.max_size_gb} GB): se detiene")
            return False
        return True

    def _prepare(self, frame: np.ndarray) -> np.ndarray:
        """Recorta a la región y garantiza el tamaño exacto del pipe (ventanas que cambian)."""
        x, y, w, h = self._crop if self._crop is not None else (0, 0, self._size[0], self._size[1])
        fh, fw = frame.shape[:2]
        if fw < x + w or fh < y + h:
            canvas = np.zeros((y + h, x + w, frame.shape[2]), dtype=frame.dtype)
            copy_h = min(fh, y + h)
            copy_w = min(fw, x + w)
            canvas[:copy_h, :copy_w] = frame[:copy_h, :copy_w]
            frame = canvas
        return frame[y : y + h, x : x + w]

    # -- audio / mux ------------------------------------------------------------

    def _audio_offset(self, track: audio_mod.AudioTrack) -> float:
        if track.first_callback_t is None or self._t0_video is None:
            return 0.0
        return track.first_callback_t - self._t0_video

    def _current_offsets(self) -> dict[str, float]:
        return {
            recorder.path.name: round(self._audio_offset(recorder), 4) for recorder in self._recorders
        }

    def _close_ffmpeg(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin is not None and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        self._proc = None

    def _mux(self, tracks: list[audio_mod.AudioTrack], offsets: dict[str, float], *, recover: bool) -> RecordingResult:
        exe = ffmpeg_mod.ffmpeg_exe()
        info = ffmpeg_mod.probe_media(self._mkv, exe)
        if info.duration_s <= 0.05:
            raise FfmpegError("el video temporal no tiene duración", details=ffmpeg_mod.stderr_tail(self._stderr_path))

        # Pistas separadas (docs/50): el MP4 lleva solo el sistema (primera pista) y el
        # micrófono se copia aparte como `<nombre>-microfono.wav`. Sin micrófono grabado, el
        # resultado es idéntico a `mix` (solo hay sistema). Sin sistema, el MP4 queda sin audio.
        separate = self.config.audio.mode == "separate"
        mic_track = next((t for t in tracks if t.path.name == "audio_mic.wav"), None)
        mux_sources = tracks
        if separate:
            mux_sources = [t for t in tracks if t.path.name != "audio_mic.wav"]
        mux_tracks = [
            ffmpeg_mod.MuxTrack(
                path=track.path,
                offset_s=offsets.get(track.path.name, 0.0),
                rate=track.rate,
                drift_ppm=float(getattr(track, "drift_ppm", 0.0) or 0.0),
            )
            for track in mux_sources
        ]
        suffix = "-recuperada" if recover else ""
        out_mp4 = self._unique_path(self.out_dir / f"{self.base_name}{suffix}.mp4")
        cmd = ffmpeg_mod.mux_cmd(
            exe, video=self._mkv, tracks=mux_tracks, out_path=out_mp4,
            duration_s=info.duration_s, mix=not separate,
        )
        proc = ffmpeg_mod.run(cmd)
        if proc.returncode != 0 or not out_mp4.exists():
            raise FfmpegError("falló el mux final", details=(proc.stderr or "")[-1500:])

        final = ffmpeg_mod.probe_media(out_mp4, exe)
        if final.duration_s <= 0.05 or not final.video_codec:
            raise FfmpegError("el MP4 final no es válido", details=str(final))

        mic_path, mic_offset = self._finalize_mic(out_mp4, mic_track, offsets, separate=separate)

        thumbnail_path = out_mp4.with_suffix(".jpg")
        has_thumb = ffmpeg_mod.make_thumbnail(exe, out_mp4, thumbnail_path, at_s=min(1.0, final.duration_s / 3))
        if not has_thumb:
            thumbnail_path = None

        camera_path = self._finalize_camera(out_mp4, exe)

        ended_at = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            stats = self._stats.copy()
        result = RecordingResult(
            mp4=out_mp4,
            thumbnail=thumbnail_path,
            sidecar=out_mp4.with_suffix(".json"),
            duration_s=final.duration_s,
            width=final.width,
            height=final.height,
            fps=final.fps,
            backend=self._backend_name,
            encoder=self._encoder,
            frames=stats.frames_sent,
            duplicated=stats.duplicated,
            skipped=stats.skipped,
            audio=tracks,
            temp_dir=self.temp_dir,
            started_at=self.started_at,
            ended_at=ended_at,
            warnings=list(self._warnings),
            camera=camera_path,
            camera_offset_s=round(self._camera_offset, 4),
            mic=mic_path,
            mic_offset_s=round(mic_offset, 4),
        )
        self._write_sidecar(result, stats)
        return result

    def _finalize_camera(self, out_mp4: Path, exe: str) -> Path | None:
        """MKV temporal de la cámara → `<nombre>-camara.mp4` junto al MP4 principal.

        Si el remux `-c copy` falla, se conserva el MKV tal cual (es reproducible y no se pierde
        la cara); si tampoco se puede copiar, se avisa y la grabación de pantalla sigue siendo
        válida. La cámara **nunca** es un motivo para perder la grabación principal.
        """
        self._camera_meta = None
        temp = self._camera_temp
        try:
            if not temp.is_file() or temp.stat().st_size == 0:
                if self.config.camera.enabled and self._camera_started:
                    self._warn("la cámara no dejó ningún archivo utilizable")
                return None
        except OSError:
            return None

        dest = self._unique_path(out_mp4.with_name(f"{out_mp4.stem}-camara.mp4"))
        try:
            proc = ffmpeg_mod.run(camera_mod.remux_cmd(exe, temp, dest), timeout=120)
        except Exception as exc:
            self._warn(f"no se pudo convertir el vídeo de la cámara: {exc}")
            proc = None
        if proc is None or proc.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
            fallback = self._unique_path(out_mp4.with_name(f"{out_mp4.stem}-camara.mkv"))
            try:
                shutil.copy2(temp, fallback)
                dest = fallback
            except OSError as exc:
                self._warn(f"no se pudo guardar el vídeo de la cámara: {exc}")
                return None

        try:
            info = ffmpeg_mod.probe_media(dest, exe)
        except FfmpegError as exc:
            self._warn(f"el vídeo de la cámara no se pudo validar: {exc.message}")
            return None
        if not info.video_codec or info.duration_s <= 0.05:
            self._warn("el vídeo de la cámara quedó vacío")
            return None

        self._camera_meta = {
            "file": dest.name,
            "device": self._camera_device,
            "width": info.width,
            "height": info.height,
            "fps": round(info.fps, 2),
            "duration_s": round(info.duration_s, 3),
            "size_bytes": info.size_bytes,
            "offset_s": round(self._camera_offset, 4),
        }
        if self._camera_track is not None and self._camera_track.failed:
            self._warn("la cámara se cortó antes de terminar; se conserva lo grabado")
        return dest

    def _finalize_mic(
        self,
        out_mp4: Path,
        mic_track: audio_mod.AudioTrack | None,
        offsets: dict[str, float],
        *,
        separate: bool,
    ) -> tuple[Path | None, float]:
        """Copia el WAV del micrófono junto al MP4 y prepara el bloque `mic` del sidecar.

        Solo en modo `separate` y con una pista de micrófono válida. El WAV se copia **antes**
        de borrar el temporal (`stop()` y `recover_session()` limpian la carpeta después del
        mux); si no se puede, se avisa y la grabación principal sigue siendo válida.
        """
        self._mic_meta = None
        if not separate or mic_track is None:
            return None, 0.0
        temp = mic_track.path
        try:
            if not temp.is_file() or temp.stat().st_size == 0:
                self._warn("el micrófono no dejó ningún archivo utilizable")
                return None, 0.0
        except OSError:
            return None, 0.0
        dest = self._unique_path(out_mp4.with_name(f"{out_mp4.stem}-microfono.wav"))
        try:
            shutil.copy2(temp, dest)
        except OSError as exc:
            self._warn(f"no se pudo guardar el micrófono separado: {exc}")
            return None, 0.0
        offset = float(offsets.get(temp.name, 0.0) or 0.0)
        self._mic_meta = {
            "file": dest.name,
            "device": self._mic_device or mic_track.device,
            "offset_s": round(offset, 4),
            "duration_s": round(mic_track.seconds, 3),
            "drift_ppm": round(float(getattr(mic_track, "drift_ppm", 0.0) or 0.0), 1),
            "rate": mic_track.rate,
        }
        return dest, offset

    def _unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        for i in range(2, 100):
            candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
            if not candidate.exists():
                return candidate
        return path

    # -- persistencia -----------------------------------------------------------

    def _write_session_json(self, status: str, offsets: dict[str, float]) -> None:
        mic_enabled = self.config.audio.mic_device_index is not None
        payload = {
            "status": status,
            "started_at": self.started_at,
            "config": _config_to_dict(self.config),
            "encoder": self._encoder,
            "backend": self._backend_name,
            "size": list(self._size),
            "offsets": offsets,
            "mic": {
                "enabled": mic_enabled,
                "mode": self.config.audio.mode,
                "device": self._mic_device,
                "offset_s": round(float(offsets.get("audio_mic.wav", 0.0) or 0.0), 4),
                "file": "audio_mic.wav" if mic_enabled else "",
            },
            "camera": {
                "enabled": bool(self.config.camera.enabled),
                "device": self._camera_device,
                "offset_s": round(self._camera_offset, 4),
                "file": self._camera_temp.name if self.config.camera.enabled else "",
                "failed": self._camera_failed,
            },
            "warnings": self._warnings,
        }
        try:
            _write_json_atomic(
                self.temp_dir / "session.json",
                json.dumps(payload, indent=2, ensure_ascii=False),
            )
        except OSError:
            pass

    def _write_sidecar(self, result: RecordingResult, stats: RecordingStats) -> None:
        payload = {
            "schema_version": 1,
            "app": "recstudio",
            "started_at": result.started_at,
            "ended_at": result.ended_at,
            "duration_s": round(result.duration_s, 3),
            "width": result.width,
            "height": result.height,
            "fps": round(result.fps, 2),
            "backend": result.backend,
            "encoder": result.encoder,
            "frames": result.frames,
            "duplicated": result.duplicated,
            "skipped": result.skipped,
            "audio": [
                {
                    "device": track.device,
                    "rate": track.rate,
                    "channels": track.channels,
                    "seconds": round(track.seconds, 3),
                    "offset_s": round(self._audio_offset(track), 4),
                    "drift_ppm": round(float(getattr(track, "drift_ppm", 0.0) or 0.0), 1),
                }
                for track in result.audio
            ],
            "cursor": self._cursor_mode,
            "camera": self._camera_meta,
            "mic": self._mic_meta,
            "config": _config_to_dict(self.config),
            "stats": asdict(stats),
            "warnings": result.warnings,
        }
        try:
            _write_json_atomic(
                result.sidecar, json.dumps(payload, indent=2, ensure_ascii=False),
            )
        except OSError:
            pass

    # -- limpieza ---------------------------------------------------------------

    def _cleanup(self) -> None:
        try:
            if self._proc is not None:
                self._close_ffmpeg()
            if self._camera is not None:
                try:
                    self._camera_track = self._camera.stop()
                except Exception:
                    pass
                self._camera = None
            if self._backend is not None:
                self._backend.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Recuperación de sesiones interrumpidas


def scan_recoverable() -> list[Path]:
    """Sesiones temporales con `video.mkv` cuyo `session.json` quedó en `recording`."""
    root = paths.temp_root()
    found: list[Path] = []
    for folder in sorted(root.glob("rec_*")):
        mkv = folder / "video.mkv"
        if not mkv.exists() or mkv.stat().st_size == 0:
            continue
        status = "recording"
        session_file = folder / "session.json"
        if session_file.exists():
            try:
                status = json.loads(session_file.read_text(encoding="utf-8")).get("status", "recording")
            except (json.JSONDecodeError, OSError):
                status = "recording"
        if status == "recording":
            found.append(folder)
    return found


def recover_session(
    temp_dir: str | Path,
    *,
    out_dir: str | Path | None = None,
    base_name: str | None = None,
) -> RecordingResult:
    """Muxea una sesión interrumpida con los offsets guardados (o 0 si no hay)."""
    temp_dir = Path(temp_dir)
    mkv = temp_dir / "video.mkv"
    if not mkv.exists() or mkv.stat().st_size == 0:
        raise FfmpegError(f"no hay video recuperable en {temp_dir}")

    session_file = temp_dir / "session.json"
    offsets: dict[str, float] = {}
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    config = RecordingConfig()
    camera_data: dict = {}
    mic_data: dict = {}
    backend = "?"
    encoder = "?"
    if session_file.exists():
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            offsets = {k: float(v) for k, v in (data.get("offsets") or {}).items()}
            started_at = data.get("started_at", started_at)
            backend = data.get("backend", backend)
            encoder = data.get("encoder", encoder)
            camera_data = data.get("camera") or {}
            mic_data = data.get("mic") or {}
            raw = data.get("config") or {}
            audio_raw = raw.get("audio") or {}
            try:
                mic_index = audio_raw.get("mic_device_index")
                mic_index = None if mic_index is None else int(mic_index)
            except (TypeError, ValueError):
                mic_index = None
            audio = AudioConfig(
                system=bool(audio_raw.get("system", True)),
                mic_device_index=mic_index,
                keepalive=bool(audio_raw.get("keepalive", True)),
                mode=str(audio_raw.get("mode", "mix")),  # type: ignore[arg-type]
            ).validate()
            config = RecordingConfig(
                fps=int(raw.get("fps", config.fps)),
                preset=str(raw.get("preset", config.preset)),
                audio=audio,
            )
        except (json.JSONDecodeError, OSError, TypeError, ValueError, ConfigError):
            camera_data = {}
            mic_data = {}

    if "audio_mic.wav" not in offsets:
        try:
            offsets["audio_mic.wav"] = float(mic_data.get("offset_s") or 0.0)
        except (TypeError, ValueError):
            pass

    tracks: list[audio_mod.AudioTrack] = []
    for wav in sorted(temp_dir.glob("audio_*.wav")):
        info = _wav_info(wav)
        if info is not None:
            rate, channels, frames = info
            desktop = "sistema" if "system" in wav.stem else "micrófono"
            tracks.append(
                audio_mod.AudioTrack(
                    path=wav,
                    device=desktop,
                    rate=rate,
                    channels=channels,
                    frames=frames,
                    first_callback_t=None,
                )
            )

    session = RecordingSession(config, out_dir=out_dir, temp_dir=temp_dir)
    session.base_name = base_name or f"{session.base_name}"
    session._backend_name = backend
    session._encoder = encoder
    # La recuperación conserva el desfase y el nombre del dispositivo para que el PiP
    # automático del editor alinee la cara igual que en la grabación original (docs/49).
    session._camera_device = str(camera_data.get("device") or "")
    try:
        session._camera_offset = float(camera_data.get("offset_s") or 0.0)
    except (TypeError, ValueError):
        session._camera_offset = 0.0
    session._camera_failed = bool(camera_data.get("failed") or False)
    # El nombre real del dispositivo viaja en session.json; si falta, la pista recuperada se
    # etiqueta «micrófono» (mismo texto que usaba la recuperación antes de docs/50).
    session._mic_device = str(mic_data.get("device") or "")
    ended_at = time.strftime("%Y-%m-%d %H:%M:%S")
    result = session._mux(tracks, offsets, recover=True)  # valida el MP4 o lanza
    result.started_at = started_at
    result.ended_at = ended_at
    # MP4 válido: el temporal (GB de MKV + WAV) ya no hace falta. Si no se puede borrar
    # entero, se marca para que no vuelva a aparecer como recuperable.
    try:
        shutil.rmtree(temp_dir)
    except OSError:
        session._write_session_json("recovered", offsets)
    return result


def _wav_info(path: Path) -> tuple[int, int, int] | None:
    import wave

    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getframerate(), wav.getnchannels(), wav.getnframes()
    except (wave.Error, OSError):
        return None


def _config_to_dict(config: RecordingConfig) -> dict:
    data = asdict(config)
    return data
