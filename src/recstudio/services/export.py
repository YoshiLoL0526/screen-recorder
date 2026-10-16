"""Export del proyecto a MP4 con FFmpeg en subprocess: progreso, ETA, cancelar y verificación.

Máquina de estados (`ExportProgress.state`):

    preparing → rendering → verifying → done
                    ↘ cancelled | error (desde cualquier punto)

- Render normal: `core.graph.build` → un único FFmpeg con `-progress pipe:1`; se parsea
  `out_time_us` y se divide por la duración esperada. ETA con la velocidad media de los
  últimos 20 ticks (docs/04).
- «Sin recodificar»: `core.keyframes.plan` → un FFmpeg `-c copy` por clip y un `concat` final.
- Se escribe en `<nombre>.part.mp4` y solo al verificar (`probe_media`: duración ±tolerancia,
  video y audio presentes) se renombra al destino. Cancelar = `terminate()` + borrar el parcial.
- Si el encoder por hardware falla a mitad (drivers), se reintenta una vez con libx264.

Agnóstico de la UI: avisa por un callback (`on_update`) desde su propio hilo.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from ..core import graph as G
from ..core import keyframes as K
from ..core.errors import FfmpegError, MediaError, ProjectError, RecStudioError
from ..core.project import Project
from ..platform import ffmpeg as F
from ..platform import paths
from . import media

ETA_WINDOW = 20
DURATION_TOLERANCE = 0.2  # s (docs/04: verificación con ffprobe ±0.2 s)

STATES_ACTIVE = ("preparing", "rendering", "verifying")


@dataclass(frozen=True)
class ExportProgress:
    state: str = "idle"  # idle | preparing | rendering | verifying | done | cancelled | error
    fraction: float = 0.0
    eta_s: float | None = None
    speed: float = 0.0  # × tiempo real
    elapsed: float = 0.0
    out_path: str = ""
    message: str = ""
    encoder: str = ""
    duration: float = 0.0  # duración esperada del resultado (s)

    @property
    def active(self) -> bool:
        return self.state in STATES_ACTIVE


class ExportCancelled(RecStudioError):
    code = "cancelled"


class EtaEstimator:
    """Velocidad media (s de salida por s de reloj) de los últimos `window` ticks."""

    def __init__(self, total: float, window: int = ETA_WINDOW) -> None:
        self.total = total
        self._ticks: deque[tuple[float, float]] = deque(maxlen=window)

    def add(self, wall: float, done: float) -> None:
        self._ticks.append((wall, done))

    @property
    def speed(self) -> float:
        if len(self._ticks) < 2:
            return 0.0
        (w0, d0), (w1, d1) = self._ticks[0], self._ticks[-1]
        return (d1 - d0) / (w1 - w0) if w1 > w0 else 0.0

    @property
    def eta(self) -> float | None:
        speed = self.speed
        if speed <= 0 or not self._ticks:
            return None
        return max(0.0, (self.total - self._ticks[-1][1]) / speed)


def partial_path(out_path: Path) -> Path:
    return out_path.with_name(f"{out_path.stem}.part{out_path.suffix or '.mp4'}")


def unique_output(folder: Path, name: str) -> Path:
    """`<carpeta>/<nombre>.mp4` sin pisar nada (` (2)`, ` (3)`…)."""
    safe = "".join(ch for ch in name if ch not in '<>:"/\\|?*').strip() or "export"
    candidate = folder / f"{safe}.mp4"
    n = 2
    while candidate.exists():
        candidate = folder / f"{safe} ({n}).mp4"
        n += 1
    return candidate


def offline_assets(project: Project) -> list[str]:
    used = {c.asset_id for t in (project.video, *project.audio) for c in t.clips}
    return [a.name for a in project.assets if a.id in used and media.is_offline(a)]


class Exporter:
    def __init__(self, on_update: Callable[[ExportProgress], None] | None = None) -> None:
        self.on_update = on_update
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._proc_lock = threading.Lock()
        self._proc = None
        self._progress = ExportProgress()
        self._done = threading.Event()
        self._done.set()

    # -- API ----------------------------------------------------------------------

    @property
    def progress(self) -> ExportProgress:
        return self._progress

    @property
    def busy(self) -> bool:
        return not self._done.is_set()

    def start(self, project: Project, preset_id: str, out_path: str | Path) -> None:
        if self.busy:
            raise ProjectError("ya hay un export en curso")
        preset = G.preset_by_id(preset_id)
        self._cancel = threading.Event()
        self._done.clear()
        self._started = time.perf_counter()
        self._emit(ExportProgress("preparing", out_path=str(out_path), message="Preparando…"))
        self._thread = threading.Thread(
            target=self._run, args=(project, preset, Path(out_path)), daemon=True, name="export"
        )
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()
        with self._proc_lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def wait(self, timeout: float | None = None) -> ExportProgress:
        self._done.wait(timeout)
        return self._progress

    # -- hilo -----------------------------------------------------------------------

    def _emit(self, progress: ExportProgress) -> None:
        if progress.active or progress.state != "idle":
            progress = replace(progress, elapsed=round(time.perf_counter() - getattr(self, "_started", 0.0), 2))
        self._progress = progress
        if self.on_update is not None:
            try:
                self.on_update(progress)
            except Exception:  # la UI nunca tumba el export
                pass

    def _run(self, project: Project, preset: G.ExportPreset, out_path: Path) -> None:
        partial = partial_path(out_path)
        workdir = Path(tempfile.mkdtemp(prefix="export-", dir=paths.temp_root()))
        base = ExportProgress("preparing", out_path=str(out_path))
        try:
            missing = offline_assets(project)
            if missing:
                raise MediaError("faltan archivos del proyecto: " + ", ".join(missing))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if preset.video == "copy":
                base, expected, has_audio = self._run_copy(project, out_path, partial, workdir, base)
            else:
                base, expected, has_audio = self._run_render(project, preset, out_path, partial, workdir, base)
            self._check_cancel()
            self._emit(replace(base, state="verifying", fraction=1.0, eta_s=None, message="Verificando…"))
            self._verify(partial, expected, has_audio, tolerance=self._tolerance)
            os.replace(partial, out_path)
            self._emit(replace(base, state="done", fraction=1.0, eta_s=None))  # `message`: nota del modo
        except ExportCancelled:
            self._emit(replace(base, state="cancelled", eta_s=None, message="Export cancelado"))
        except RecStudioError as exc:
            state = "cancelled" if self._cancel.is_set() else "error"
            self._emit(replace(base, state=state, eta_s=None,
                               message="Export cancelado" if state == "cancelled" else exc.message))
        except Exception as exc:  # nunca un traceback crudo hacia la UI
            self._emit(replace(base, state="error", eta_s=None, message=f"Error inesperado: {exc}"))
        finally:
            if self._progress.state != "done":
                _unlink(partial)
            shutil.rmtree(workdir, ignore_errors=True)
            self._done.set()

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise ExportCancelled("cancelado")

    # -- render con filter graph -----------------------------------------------------

    def _run_render(self, project, preset, out_path, partial, workdir, base):  # noqa: ANN001, ANN202
        exe = F.ffmpeg_exe()
        try:
            available = F.probe_encoders()
        except (OSError, FfmpegError):
            available = {"libx264": True}
        encoder = G.resolve_encoder(preset, available)
        while True:
            spec = G.build(project, preset, partial, encoder=encoder, exe=exe)
            base = replace(base, encoder=encoder, duration=spec.duration, message="")
            self._tolerance = max(DURATION_TOLERANCE, 2.0 / spec.fps)
            try:
                self._run_ffmpeg(list(spec.args), workdir / "render.log", spec.duration, base, 0.0, 1.0)
                return base, spec.duration, spec.has_audio
            except FfmpegError:
                if self._cancel.is_set() or encoder == "libx264":
                    raise
                encoder = "libx264"  # el encoder HW falló de verdad: CPU
                self._emit(replace(base, state="rendering", fraction=0.0,
                                   message="El encoder por hardware falló; reintentando con x264…"))

    # -- corte rápido ---------------------------------------------------------------------

    def _run_copy(self, project, out_path, partial, workdir, base):  # noqa: ANN001, ANN202
        exe = F.ffmpeg_exe()
        frames = {}
        for asset in project.assets:
            if any(c.asset_id == asset.id for c in project.video.clips):
                self._check_cancel()
                frames[asset.id] = media.keyframes(asset.path)
        plan = K.plan(project, frames)
        total = plan.duration
        shift = plan.max_shift
        note = f" · inicios movidos hasta {shift:.2f} s (keyframes)" if shift > 0.5 / project.fps else ""
        base = replace(base, encoder="copia", duration=total, message=f"Copiando segmentos{note}")
        self._tolerance = max(DURATION_TOLERANCE, len(plan.segments) * 2.0 / max(1.0, project.fps))
        files: list[Path] = []
        done = 0.0
        for i, segment in enumerate(plan.segments):
            self._check_cancel()
            target = workdir / f"seg_{i:04d}.mp4"
            self._run_ffmpeg(K.segment_cmd(exe, segment, target), workdir / f"seg_{i:04d}.log",
                             None, base, done / total * 0.8, segment.duration / total * 0.8)
            files.append(target)
            done += segment.duration
        list_file = workdir / "list.txt"
        list_file.write_text(K.concat_list(files), encoding="utf-8")
        self._run_ffmpeg(K.concat_cmd(exe, list_file, partial), workdir / "concat.log", total,
                         replace(base, message=f"Uniendo segmentos{note}"), 0.8, 0.2)
        has_audio = any(project.asset(c.asset_id).has_audio for c in project.video.clips)
        return replace(base, message=f"Sin recodificar{note}"), total, has_audio

    # -- proceso ----------------------------------------------------------------------

    def _run_ffmpeg(self, cmd: list[str], log: Path, duration: float | None, base: ExportProgress,
                    offset: float, weight: float) -> None:
        """Ejecuta un FFmpeg; con `duration` reparte su progreso en `[offset, offset + weight]`."""
        self._check_cancel()
        eta = EtaEstimator(duration or 0.0)
        proc = F.run_progress_process(cmd, log)
        with self._proc_lock:
            self._proc = proc
        try:
            self._emit(replace(base, state="rendering", fraction=offset))
            assert proc.stdout is not None
            for line in proc.stdout:
                done = F.progress_seconds(line)
                if done is None or not duration:
                    continue
                eta.add(time.perf_counter(), done)
                part = min(1.0, done / duration)
                self._emit(replace(base, state="rendering", fraction=round(offset + part * weight, 4),
                                   eta_s=eta.eta, speed=round(eta.speed, 2)))
            code = proc.wait()
        finally:
            with self._proc_lock:
                self._proc = None
        self._check_cancel()
        if code != 0:
            tail = F.stderr_tail(log, lines=8)
            raise FfmpegError(f"FFmpeg terminó con código {code}" + (f": {tail.splitlines()[-1]}" if tail else ""),
                              details=tail)

    def _verify(self, path: Path, expected: float, has_audio: bool, *, tolerance: float) -> None:
        try:
            info = F.probe_media(path)
        except FfmpegError as exc:
            raise FfmpegError(f"el archivo exportado no se puede leer: {exc.message}") from exc
        if info.video_codec is None:
            raise FfmpegError("el archivo exportado no tiene video")
        if has_audio and not info.has_audio:
            raise FfmpegError("el archivo exportado no tiene audio")
        if abs(info.duration_s - expected) > tolerance:
            raise FfmpegError(
                f"duración inesperada: {info.duration_s:.2f} s (se esperaban {expected:.2f} s)"
            )


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
