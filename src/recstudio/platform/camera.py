"""Captura de webcam por **DirectShow** (FFmpeg CLI), sin OpenCV (docs/49).

Decisiones:
- El build ligero excluye `cv2`, así que la cámara se abre con FFmpeg (`-f dshow`). No se usa
  PyAV porque el resto de la captura ya habla con el binario de FFmpeg y aquí solo necesitamos
  un proceso que escriba vídeo a un MKV temporal.
- Se graba **solo vídeo** (`-an`): el audio de la cara no aporta nada a un PiP y evita abrir el
  micrófono de la cámara por sorpresa.
- La enumeración se hace con `-list_devices true -f dshow -i dummy` y se parsea con tolerancia:
  cualquier cambio de formato de FFmpeg devuelve una lista vacía (la app avisa), nunca rompe.
- Un fallo de la cámara **no** tumba la grabación de pantalla: `CameraRecorder` avisa por
  `on_failure` y el motor lo convierte en un aviso de la sesión.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import CameraError
from . import ffmpeg as ffmpeg_mod

#: El nombre visible va entre comillas y termina en `(video)`; los de audio llevan `(audio)`.
_VIDEO_RE = re.compile(r'"([^"]+)"\s*\(video\)')
_ALT_RE = re.compile(r'Alternative name\s+"([^"]+)"')
_TAIL_LINES = 12


@dataclass(frozen=True)
class CameraDevice:
    """Cámara DirectShow. `id` es el nombre PnP alternativo cuando FFmpeg lo publica."""

    name: str
    id: str = ""

    @property
    def label(self) -> str:
        return self.name


@dataclass(frozen=True)
class CameraTrack:
    """Archivo temporal de la cámara al terminar (aún no remuxado a MP4)."""

    path: Path
    device: str
    failed: bool = False


def parse_camera_list(text: str) -> tuple[CameraDevice, ...]:
    """Extrae las cámaras de vídeo de la salida de `ffmpeg -list_devices` (puro)."""
    devices: list[CameraDevice] = []
    pending: int | None = None
    for line in text.splitlines():
        match = _VIDEO_RE.search(line)
        if match:
            devices.append(CameraDevice(match.group(1)))
            pending = len(devices) - 1
            continue
        alt = _ALT_RE.search(line)
        if alt is not None and pending is not None:
            current = devices[pending]
            devices[pending] = CameraDevice(current.name, alt.group(1))
            pending = None
    return tuple(devices)


def list_cameras(*, exe: str | None = None, timeout: float = 10.0) -> tuple[CameraDevice, ...]:
    """Enumera cámaras DirectShow. Lista vacía si FFmpeg no está o no hay dispositivos."""
    try:
        exe = exe or ffmpeg_mod.ffmpeg_exe()
        proc = ffmpeg_mod.run(
            [exe, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    return parse_camera_list(proc.stderr or proc.stdout or "")


def _device_arg(device: CameraDevice | str) -> str:
    name = device.name if isinstance(device, CameraDevice) else str(device)
    return f"video={name}"


def camera_cmd(
    exe: str,
    device: CameraDevice | str,
    out_path: str | Path,
    *,
    width: int | None = 1280,
    height: int | None = 720,
    fps: int | None = 30,
) -> list[str]:
    """Comando de captura de la cámara a MKV (libx264, sin audio).

    `width`/`height`/`fps` a `None` dejan que DirectShow use el modo nativo: es el reintento
    cuando la cámara no acepta el tamaño o el ritmo pedidos.
    """
    args = [exe, "-hide_banner", "-loglevel", "warning", "-y", "-f", "dshow"]
    if fps:
        args += ["-framerate", str(int(fps))]
    if width and height:
        args += ["-video_size", f"{int(width)}x{int(height)}"]
    args += [
        "-i", _device_arg(device),
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-flush_packets", "1",
        "-f", "matroska", str(out_path),
    ]
    return args


def remux_cmd(exe: str, source: str | Path, dest: str | Path, *, copy: bool = True) -> list[str]:
    """MKV temporal de la cámara → MP4 final. `copy=False` recodifica si el remux falla."""
    args = [exe, "-hide_banner", "-loglevel", "warning", "-y", "-i", str(source), "-an"]
    if copy:
        args += ["-c:v", "copy"]
    else:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    args += ["-movflags", "+faststart", str(dest)]
    return args


def snapshot_cmd(exe: str, device: CameraDevice | str, *, width: int = 320) -> list[str]:
    """Un JPEG suelto de la cámara para la vista previa de la pantalla de Grabar."""
    return [
        exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "dshow",
        "-i", _device_arg(device),
        "-frames:v", "1", "-vf", f"scale={max(32, int(width))}:-2",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "5", "pipe:1",
    ]


def capture_snapshot(
    device: CameraDevice | str, *, width: int = 320, timeout: float = 6.0, exe: str | None = None,
) -> bytes | None:
    """JPEG de un frame (o `None`). Bloquea: se llama desde el hilo de preview, nunca desde UI."""
    try:
        exe = exe or ffmpeg_mod.ffmpeg_exe()
        proc = ffmpeg_mod.run_bytes(snapshot_cmd(exe, device, width=width), timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    data = proc.stdout or b""
    if proc.returncode == 0 and data.startswith(b"\xff\xd8"):
        return data
    return None


def _tail(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-_TAIL_LINES:])


class CameraRecorder:
    """Proceso FFmpeg que graba la cámara a `out_path` mientras dura la sesión.

    `start()` valida que el proceso entregue datos (si no, reintenta sin tamaño/fps) y lanza un
    vigilante que avisa si el dispositivo desaparece a mitad. `stop()` pide a FFmpeg un cierre
    limpio con `q` (así el MKV queda indexado) y devuelve el temporal si tiene contenido.
    """

    def __init__(
        self,
        device: CameraDevice | str,
        out_path: str | Path,
        *,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        exe: str | None = None,
        on_failure: Callable[[str], None] | None = None,
        start_timeout: float = 2.5,
    ) -> None:
        self.device = device
        self.out_path = Path(out_path)
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self._exe = exe
        self._on_failure = on_failure
        self._start_timeout = start_timeout
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_path = self.out_path.with_name(self.out_path.name + ".ffmpeg.log")
        self._watch_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._failed = False
        self.started_at: float | None = None

    @property
    def name(self) -> str:
        return self.device.name if isinstance(self.device, CameraDevice) else str(self.device)

    @property
    def running(self) -> bool:
        return (
            self._proc is not None
            and self._proc.poll() is None
            and not self._stop.is_set()
        )

    @property
    def failed(self) -> bool:
        return self._failed

    def start(self) -> None:
        if self._proc is not None:
            return
        exe = self._exe or ffmpeg_mod.ffmpeg_exe()
        attempts: list[tuple[int | None, int | None, int | None]] = [
            (self.width, self.height, self.fps),
            (None, None, None),
        ]
        errors: list[str] = []
        for width, height, fps in attempts:
            if self._try_start(exe, width, height, fps):
                self.started_at = time.perf_counter()
                self._watch_thread = threading.Thread(
                    target=self._watch, name="camera-watch", daemon=True
                )
                self._watch_thread.start()
                return
            errors.append(_tail(self._log_path))
        detail = next((text for text in reversed(errors) if text), "")
        raise CameraError(
            f"no se pudo abrir la cámara «{self.name}»"
            + (" (ocupada, desconectada o sin permisos)" if not detail else ""),
            details=detail,
        )

    def _try_start(
        self, exe: str, width: int | None, height: int | None, fps: int | None,
    ) -> bool:
        try:
            self.out_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            self._proc = ffmpeg_mod.run_capture_process(
                camera_cmd(exe, self.device, self.out_path, width=width, height=height, fps=fps),
                self._log_path,
            )
        except Exception as exc:
            self._log_path.write_text(str(exc), encoding="utf-8", errors="replace")
            self._proc = None
            return False

        deadline = time.perf_counter() + self._start_timeout
        while time.perf_counter() < deadline:
            assert self._proc is not None
            if self._proc.poll() is not None:
                self._proc = None
                return False
            try:
                if self.out_path.exists() and self.out_path.stat().st_size > 0:
                    return True
            except OSError:
                pass
            time.sleep(0.05)
        # Vivo pero sin datos: no sirve para grabar; se cierra antes del reintento.
        self._kill()
        self._proc = None
        return False

    def _watch(self) -> None:
        assert self._proc is not None
        proc = self._proc
        while not self._stop.wait(0.5):
            code = proc.poll()
            if code is None:
                continue
            self._failed = True
            if self._on_failure is not None:
                detail = _tail(self._log_path)
                message = f"la cámara terminó sola (código {code})"
                if detail:
                    message += f": {detail.splitlines()[-1]}"
                try:
                    self._on_failure(message)
                except Exception:
                    pass
            return

    def stop(self) -> CameraTrack | None:
        """Cierra FFmpeg con `q` y devuelve el MKV si llegó a tener contenido."""
        self._stop.set()
        proc = self._proc
        if proc is not None:
            if proc.poll() is None:
                try:
                    if proc.stdin is not None and not proc.stdin.closed:
                        proc.stdin.write(b"q")
                        proc.stdin.flush()
                        proc.stdin.close()
                except (OSError, ValueError):
                    pass
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._kill()
            self._proc = None
        if self._watch_thread is not None:
            self._watch_thread.join(timeout=2)
            self._watch_thread = None
        failed = self._failed  # el aviso ya se dio; el archivo puede recuperarse igual
        try:
            if self.out_path.exists() and self.out_path.stat().st_size > 0:
                return CameraTrack(self.out_path, self.name, failed=failed)
        except OSError:
            pass
        return None

    def _kill(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
