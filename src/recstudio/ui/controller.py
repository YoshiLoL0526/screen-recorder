"""Puente UI ↔ motor: los handlers nunca bloquean; el trabajo corre en hilos.

Reglas:
- La UI solo lee/escribe `state`; aquí se traduce a llamadas al motor.
- `start/stop/recover/delete` corren en hilos propios y publican por `Signal`.
- La persistencia de ajustes se hace con debounce sobre los cambios de los Signals.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from .. import state
from ..core.config import AudioConfig, ConfigError, RecordingConfig, SourceConfig
from ..core.errors import RecStudioError
from ..core.regions import clamp_region
from ..platform import audio as audio_mod
from ..platform import ffmpeg as ffmpeg_mod
from ..platform import screens
from ..services import library
from ..services import preview as preview_mod
from ..services import recording as rec_mod
from ..services.settings import Settings

CONTROLLER: "Controller | None" = None

_POLL_INTERVAL = 0.25
_FPS_WINDOW = 180


def _parse_region() -> tuple[int, int, int, int] | None:
    try:
        x = int(float(state.region_x() or 0))
        y = int(float(state.region_y() or 0))
        w = int(float(state.region_w() or 0))
        h = int(float(state.region_h() or 0))
    except ValueError:
        return None
    if w < 8 or h < 8:
        return None
    return x, y, w, h


class Controller:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session: rec_mod.RecordingSession | None = None
        self._preview: preview_mod.PreviewLoop | None = None
        self._lock = threading.Lock()
        self._save_timer: threading.Timer | None = None
        self._closing = False
        self._countdown_cancel = threading.Event()
        # Arranque/parada en curso (incluye la cuenta atrás): mientras dure, F9 y el botón se
        # ignoran; si no, dos pulsaciones durante «Preparando…» creaban dos sesiones.
        self._busy = False
        self.region_drag_start: tuple[int, int, int, int] | None = None  # región al empezar un drag
        from .editor_controller import EditorController

        self.editor = EditorController(settings)

    # -- ciclo de vida --------------------------------------------------------

    def boot(self) -> None:
        global CONTROLLER
        CONTROLLER = self
        self._apply_settings()
        from .components import bind_modal_accept

        bind_modal_accept(self._on_confirm)
        self._preview = preview_mod.PreviewLoop(
            self._session_frame, self._geometry, state.preview.set, fps=6.0
        )
        self._preview.start()
        threading.Thread(target=self._load_system_info, daemon=True, name="sysinfo").start()
        self.refresh_library()
        self._subscribe_settings()
        self.editor.boot()

    def shutdown(self) -> None:
        self._closing = True
        self.editor.shutdown()
        if self.session is not None:
            try:
                self.session.abort()
            except Exception:
                pass
            self.session = None
        if self._preview is not None:
            self._preview.stop()
        self._save_settings()

    # -- ajustes --------------------------------------------------------------

    def _apply_settings(self) -> None:
        s = self.settings
        state.set_theme(s.theme)
        state.source_kind.set(s.source_kind)
        state.monitor_index.set(s.monitor_index)
        state.window_title.set(s.window_title)
        state.set_region(s.region)
        state.countdown_s.set(s.countdown_s)
        state.fps_target.set(s.fps)
        state.preset.set(s.preset)
        state.encoder.set(s.encoder)
        state.audio_system.set(s.audio_system)
        state.keepalive.set(s.keepalive)
        state.mic_index.set(s.mic_index)
        state.out_dir.set(s.out_dir)

    def _subscribe_settings(self) -> None:
        for signal in (
            state.theme_name, state.source_kind, state.monitor_index, state.window_title,
            state.region, state.countdown_s,
            state.fps_target, state.preset, state.encoder, state.audio_system,
            state.keepalive, state.mic_index, state.out_dir, state.project_path, state.preview_audio,
        ):
            signal.subscribe(lambda _value: self._schedule_save())

    def _schedule_save(self) -> None:
        if self._closing:
            return
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(1.0, self._save_settings)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _save_settings(self) -> None:
        s = self.settings
        s.theme = state.theme_name()
        s.source_kind = state.source_kind()
        s.monitor_index = int(state.monitor_index())
        s.window_title = state.window_title()
        s.region = _parse_region() or state.region()
        s.countdown_s = int(state.countdown_s())
        s.fps = int(state.fps_target())
        s.preset = state.preset()
        s.encoder = state.encoder()
        s.audio_system = bool(state.audio_system())
        s.keepalive = bool(state.keepalive())
        s.mic_index = int(state.mic_index())
        s.out_dir = state.out_dir()
        s.last_project = state.project_path()
        s.preview_audio = bool(state.preview_audio())
        s.save()

    def set_theme(self, name: str) -> None:
        state.set_theme(name)
        self._schedule_save()

    # -- info del sistema y preview -------------------------------------------

    def _load_system_info(self) -> None:
        try:
            state.monitors.set(tuple(screens.list_monitors()))
        except Exception:
            pass
        try:
            state.audio_devices.set(tuple(audio_mod.list_devices()))
        except Exception:
            pass
        try:
            state.encoders.set(dict(ffmpeg_mod.probe_encoders()))
        except Exception:
            pass
        try:
            state.ffmpeg_version.set(ffmpeg_mod.ffmpeg_version())
        except Exception:
            pass

    def _session_frame(self):  # noqa: ANN202
        session = self.session
        return session.preview_frame() if session is not None else None

    def _geometry(self) -> tuple[int, tuple[int, int, int, int] | None]:
        kind = state.source_kind()
        region = _parse_region() if kind == "region" else None
        return int(state.monitor_index()), region

    # -- editor de región -------------------------------------------------------

    def open_region_editor(self) -> None:
        """Captura el monitor y abre el editor de región (imagen + rectángulo arrastrable)."""

        def worker() -> None:
            import io

            from PIL import Image

            from ..platform import screens

            try:
                monitor_index = int(state.monitor_index())
                png = screens.capture_reference_png(monitor_index)
                monitor = next(
                    (m for m in state.monitors() if m.index == monitor_index), None
                )
                bounds = (monitor.width, monitor.height) if monitor else (1920, 1080)
                image = Image.open(io.BytesIO(png)).convert("RGB")
                display_w = min(940, image.width)
                display_h = max(2, round(image.height * display_w / image.width))
                image = image.resize((display_w, display_h), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                image.save(buffer, "PNG", optimize=True)
                state.set_region(clamp_region(state.region(), bounds))
                state.region_bounds.set(bounds)
                state.region_ref_size.set((display_w, display_h))
                state.region_ref.set(buffer.getvalue())
                state.region_editing.set(True)
            except Exception as exc:
                state.toast("danger", f"No se pudo capturar la pantalla: {exc}")

        threading.Thread(target=worker, daemon=True, name="region-ref").start()

    def close_region_editor(self) -> None:
        state.region_editing.set(False)
        state.region_ref.set(b"")

    def begin_region_drag(self) -> None:
        self.region_drag_start = state.region()

    def drag_region(self, dx: float, dy: float, corner: str | None) -> None:
        """Aplica un delta de arrastre (px lógicos del editor) a la región."""
        from ..core.regions import move_region, resize_region

        start = self.region_drag_start or state.region()
        bounds = state.region_bounds() or (1920, 1080)
        display_w, display_h = state.region_ref_size()
        if not display_w or not display_h:
            return
        scale_x = bounds[0] / display_w
        scale_y = bounds[1] / display_h
        step_x = dx * scale_x
        step_y = dy * scale_y
        if corner is None:
            state.set_region(move_region(start, step_x, step_y, bounds))
        else:
            state.set_region(resize_region(start, corner, step_x, step_y, bounds))

    # -- grabación -------------------------------------------------------------

    def toggle_record(self) -> None:
        with self._lock:
            if state.countdown() > 0:  # cancelar la cuenta atrás en curso
                self._countdown_cancel.set()
                state.countdown.set(0)
                state.toast("info", "Cuenta atrás cancelada")
                return
            if self._busy:
                return
            if self.session is None:
                self._busy = True
                seconds = int(state.countdown_s())
                if seconds > 0:
                    self._countdown_cancel.clear()
                    threading.Thread(
                        target=self._countdown_then_start, args=(seconds,), daemon=True, name="countdown"
                    ).start()
                else:
                    threading.Thread(target=self._start_worker, daemon=True, name="rec-start").start()
            else:
                self._request_stop_locked()

    def _request_stop_locked(self) -> None:
        """Lanza la parada una sola vez (llamar con `_lock` tomado)."""
        if self._busy or self.session is None:
            return
        self._busy = True
        threading.Thread(target=self._stop_worker, daemon=True, name="rec-stop").start()

    def _countdown_then_start(self, seconds: int) -> None:
        try:
            for remaining in range(seconds, 0, -1):
                state.countdown.set(remaining)
                if self._countdown_cancel.wait(1.0):
                    state.countdown.set(0)
                    return
            state.countdown.set(0)
            if not self._countdown_cancel.is_set() and not self._closing:
                self._start_worker()
        finally:
            self._busy = False

    def toggle_pause(self) -> None:
        session = self.session
        if session is None:
            return
        if state.rec_state() == "paused":
            session.resume()
            state.rec_state.set("recording")
            state.toast("info", "Grabación reanudada")
        elif state.rec_state() == "recording":
            session.pause()
            state.rec_state.set("paused")
            state.toast("info", "Grabación en pausa")

    def _build_config(self) -> RecordingConfig | None:
        try:
            kind = state.source_kind()
            if kind == "window":
                title = state.window_title().strip()
                if not title:
                    raise ConfigError("indica parte del título de la ventana a grabar")
                source = SourceConfig(kind="window", window_title=title)
            elif kind == "region":
                region = _parse_region()
                if region is None:
                    raise ConfigError("región inválida (x, y, ancho y alto, mínimo 8×8)")
                source = SourceConfig(
                    kind="region", monitor_index=int(state.monitor_index()), region=region
                )
            else:
                source = SourceConfig(kind="monitor", monitor_index=int(state.monitor_index()))

            out_dir = Path(state.out_dir()).expanduser()
            out_dir.mkdir(parents=True, exist_ok=True)

            config = RecordingConfig(
                fps=int(state.fps_target()),
                preset=state.preset(),
                encoder=None if state.encoder() == "auto" else state.encoder(),
                source=source,
                audio=AudioConfig(
                    system=bool(state.audio_system()),
                    mic_device_index=None if int(state.mic_index()) < 0 else int(state.mic_index()),
                    keepalive=bool(state.keepalive()),
                ),
            )
            return config.validate()
        except (ConfigError, OSError, ValueError) as exc:
            state.rec_state.set("error")
            state.rec_error.set(str(exc))
            state.toast("danger", f"Configuración inválida: {exc}")
            return None

    def _start_worker(self) -> None:
        try:
            self._start_session()
        finally:
            self._busy = False

    def _start_session(self) -> None:
        config = self._build_config()
        if config is None:
            state.rec_state.set("idle")
            return
        state.rec_error.set("")
        state.fps_history.set(())
        state.rec_state.set("starting")
        session = rec_mod.RecordingSession(
            config, out_dir=Path(state.out_dir()), on_event=self._engine_event
        )
        try:
            session.start()
        except RecStudioError as exc:
            state.rec_state.set("error")
            state.rec_error.set(exc.message)
            state.toast("danger", f"No se pudo grabar: {exc.message}")
            try:
                session.abort()
            except Exception:
                pass
            return
        self.session = session
        state.rec_state.set("recording")
        state.toast("success", f"Grabando · {session.backend_name} + {session.encoder_name}")
        threading.Thread(target=self._poll_worker, args=(session,), daemon=True, name="rec-poll").start()

    def _stop_worker(self) -> None:
        try:
            self._stop_session()
        finally:
            self._busy = False

    def _stop_session(self) -> None:
        session = self.session
        if session is None:
            return
        state.rec_state.set("stopping")
        try:
            result = session.stop()
        except RecStudioError as exc:
            state.rec_state.set("error")
            state.rec_error.set(exc.message)
            state.toast("danger", f"Error al guardar: {exc.message}")
            self.session = None
            self.refresh_library()  # el temporal puede haber quedado como recuperable
            return
        self.session = None
        state.rec_state.set("idle")
        state.toast(
            "success",
            f"Guardado: {result.mp4.name} · {result.duration_s:.1f}s · {result.mp4.stat().st_size / 1e6:.1f} MB",
        )
        self.refresh_library()

    def _engine_event(self, name: str, data: dict[str, Any]) -> None:
        if name == "warning":
            state.toast("warning", str(data.get("message", "aviso")))

    def _poll_worker(self, session: rec_mod.RecordingSession) -> None:
        while self.session is session and not self._closing:
            stats = session.stats()
            state.rec_elapsed.set(stats.elapsed_s)
            state.rec_fps.set(stats.fps_real)
            state.rec_dup.set(stats.duplicated)
            state.rec_skip.set(stats.skipped)
            state.rec_size_mb.set(stats.size_bytes / 1e6)
            state.rec_disk_gb.set(stats.disk_free_mb / 1000)
            state.rec_level.set(stats.level)
            if stats.state not in ("idle",):
                history = state.fps_history()
                if stats.fps_real > 0:
                    state.fps_history.set((*history[-_FPS_WINDOW:], stats.fps_real))
            if stats.error:
                state.rec_error.set(stats.error)
            if session.stopped_by_itself:  # error, disco lleno, límite o fuente cerrada
                with self._lock:
                    if self.session is not session:
                        return
                    if not self._busy:  # si hay otro arranque/parada en curso, reintentar
                        if stats.error:
                            state.toast("danger", f"La grabación se detuvo: {stats.error}")
                        self._request_stop_locked()
                        return
            time.sleep(_POLL_INTERVAL)

    # -- biblioteca ------------------------------------------------------------

    def refresh_library(self) -> None:
        def worker() -> None:
            out_dir = state.out_dir()
            try:
                items = library.list_recordings(out_dir)
                state.recordings.set(tuple(items))
            except Exception:
                pass
            try:
                state.recoverable.set(tuple(library.list_recoverable()))
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True, name="lib-refresh").start()

    def ask_delete(self, recording: library.Recording) -> None:
        state.confirm.set(state.Confirm(
            action="delete",
            title="Mover a la papelera",
            message=f"«{recording.name}» y su miniatura se moverán a la papelera de reciclaje.",
            target=str(recording.mp4),
        ))

    def ask_recover(self, folder: Path) -> None:
        state.confirm.set(state.Confirm(
            action="recover",
            title="Recuperar grabación interrumpida",
            message=f"Se unirán el video y el audio de «{folder.name}» en un MP4 nuevo.",
            target=str(folder),
        ))

    def recover(self, folder: Path) -> None:
        """Muxea una sesión interrumpida (una sola vez por carpeta aunque se pulse dos veces)."""
        with self._lock:
            if folder.name in state.recovering():
                return
            state.recovering.set((*state.recovering(), folder.name))

        def worker() -> None:
            try:
                result = rec_mod.recover_session(folder, out_dir=state.out_dir())
            except RecStudioError as exc:
                state.toast("danger", f"No se pudo recuperar: {exc.message}")
                return
            finally:
                with self._lock:
                    state.recovering.set(tuple(n for n in state.recovering() if n != folder.name))
            state.toast("success", f"Recuperada: {result.mp4.name} ({result.duration_s:.1f}s)")
            self.refresh_library()

        threading.Thread(target=worker, daemon=True, name="recover").start()

    def open_out_dir(self) -> None:
        """Abre la carpeta de salida configurada (no la de por defecto)."""

        def worker() -> None:
            folder = state.recordings_dir().expanduser()
            if not folder.is_dir():
                state.toast("warning", f"La carpeta «{folder}» no existe todavía: se creará al grabar")
                return
            try:
                library.open_file(folder)
            except OSError as exc:
                state.toast("danger", f"No se pudo abrir la carpeta: {exc}")

        threading.Thread(target=worker, daemon=True, name="open-out-dir").start()

    def _on_confirm(self, confirm: state.Confirm) -> None:
        if confirm.action == "delete":
            self._delete_target(Path(confirm.target))
        elif confirm.action == "recover":
            self.recover(Path(confirm.target))

    def _delete_target(self, mp4: Path) -> None:
        def worker() -> None:
            target = next((r for r in state.recordings() if r.mp4 == mp4), None)
            if target is None:
                return
            removed = library.delete_recording_files(target)
            if removed:
                state.toast("success", f"«{target.name}» movida a la papelera")
            else:
                state.toast("danger", "No se pudo borrar la grabación")
            self.refresh_library()

        threading.Thread(target=worker, daemon=True, name="delete").start()
