"""Puente UI ↔ editor: comandos del timeline, drags, reproducción y persistencia.

Reglas (las mismas que `controller.py`):
- Los handlers solo llaman a funciones puras de `core.timeline` y al `ProjectStore` (rápido);
  todo lo que toca disco o decodifica (abrir, guardar, importar, waveforms) va en hilos.
- El `ProjectStore` avisa de cada cambio y aquí se reparte a los `Signal` de `state` y al
  `Player` (que vive en su propio hilo).
- Los drags llegan como **deltas** (`DragEvent.dx`) o como x de ventana; la conversión a x del
  contenido usa `timeline.TL_X0` (todo lo que hay a su izquierda tiene ancho fijo).
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

from .. import state
from ..core import store as store_mod
from ..core import timeline as tl
from ..core.errors import ProjectError, RecStudioError
from ..core.project import FPS_CHOICES, MediaAsset, Project
from ..core.store import ProjectStore
from ..services import media
from ..services import playback as playback_mod
from ..services.settings import Settings

EDITOR: "EditorController | None" = None

_AUTOSAVE_POLL = 2.0
_CLICK_SLOP_PX = 3.0
SPEED_CHOICES = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)
TRANSITION_DURATIONS = (0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0)


def _nearest_fps(fps: float) -> int:
    return min(FPS_CHOICES, key=lambda f: abs(f - fps)) if fps > 0 else 60


class EditorController:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = ProjectStore(on_change=self._on_project)
        self.player = playback_mod.Player(
            self._on_frame, self._on_player_time, self._on_player_state,
            audio=playback_mod.AudioPreview(),
        )
        self._drag: dict | None = None
        self._closing = threading.Event()
        self._io_lock = threading.Lock()  # abrir/guardar/importar: de uno en uno
        self._wave_lock = threading.Lock()
        self._wave_pending: set[str] = set()

    # -- ciclo de vida ---------------------------------------------------------

    def boot(self) -> None:
        global EDITOR
        EDITOR = self
        state.preview_audio.set(bool(self.settings.preview_audio))
        self.player.audio.muted = not state.preview_audio()  # type: ignore[union-attr]
        state.selection.subscribe(lambda _v: self._sync_inspector())
        state.insp_speed.subscribe(self._on_insp_speed)
        state.insp_transition.subscribe(self._on_insp_transition)
        state.insp_transition_dur.subscribe(self._on_insp_transition_dur)
        state.insp_fps.subscribe(lambda fps: self.set_project_fps(int(fps)) if state.project_open() else None)
        self.player.start()
        threading.Thread(target=self._autosave_loop, daemon=True, name="autosave").start()
        self.refresh_recent()
        last = self.settings.last_project
        if last and Path(last).exists():
            self.open_project(Path(last), switch=False)

    def shutdown(self) -> None:
        self._closing.set()
        self.player.stop()
        if self.store.dirty and self.store.path is not None and not self.store.read_only:
            try:
                self.store.save()
            except (OSError, ProjectError):
                pass
        self.settings.last_project = str(self.store.path) if self.store.path else ""
        self.settings.preview_audio = bool(state.preview_audio())

    def _autosave_loop(self) -> None:
        while not self._closing.wait(_AUTOSAVE_POLL):
            try:
                if self.store.autosave_due():
                    self.store.save()
                    self._sync_meta()
            except (OSError, ProjectError) as exc:
                state.toast("danger", f"Autoguardado fallido: {exc}")
                time.sleep(10)

    # -- sincronización con el estado -------------------------------------------

    def _on_project(self, project: Project) -> None:
        state.editor_project.set(project)
        self.player.set_project(project)
        self._sync_meta()
        selection = state.selection()
        if selection and selection[0] == "clip" and not project.has_clip(selection[1]):
            state.selection.set(None)
        elif selection and selection[0] == "transition" and selection[1] >= len(project.video.transitions):
            state.selection.set(None)
        total = tl.total_duration(project)
        if state.playhead() > total:
            self.seek(total)
        self._sync_inspector()

    def _sync_meta(self) -> None:
        store = self.store
        state.project_dirty.set(store.dirty)
        state.undo_label.set(store.undo_label or "")
        state.redo_label.set(store.redo_label or "")
        state.project_read_only.set(store.read_only)

    def _sync_inspector(self) -> None:
        """Lleva el modelo a los campos/selects del inspector (sus escuchas ignoran lo igual)."""
        project = self.store.project
        state.insp_fps.set(project.fps)
        clip = self.selected_clip()
        if clip is not None:
            state.insp_in.set(tl.format_time(clip.in_))
            state.insp_out.set(tl.format_time(clip.out_))
            state.insp_speed.set(clip.speed)
        index = self._transition_index()
        if index is not None:
            current = project.video.transitions[index]
            state.insp_transition.set(current.kind if current is not None else "none")
            if current is not None:
                state.insp_transition_dur.set(current.duration)

    def _on_insp_speed(self, speed: float) -> None:
        clip = self.selected_clip()
        if clip is not None and clip.speed != speed:
            self.set_speed(speed)

    def _current_transition(self):  # noqa: ANN202
        index = self._transition_index()
        if index is None:
            return None, None
        return index, self.project.video.transitions[index]

    def _on_insp_transition(self, kind: str) -> None:
        index, current = self._current_transition()
        if index is None or (current.kind if current is not None else "none") == kind:
            return
        self.set_transition(None if kind == "none" else kind, state.insp_transition_dur())

    def _on_insp_transition_dur(self, duration: float) -> None:
        index, current = self._current_transition()
        if index is None or current is None or abs(current.duration - duration) < 1e-6:
            return
        self.set_transition_duration(duration)

    def _on_frame(self, data: bytes) -> None:
        state.preview_frame.set(data)
        kind = self.player.renderer.last_transition
        state.preview_note.set(
            f"transición «{kind}» aproximada en el preview" if kind in playback_mod.APPROXIMATE else ""
        )

    def _on_player_time(self, t: float) -> None:
        state.playhead.set(t)

    def _on_player_state(self, playing: bool) -> None:
        state.playing.set(playing)

    # -- consultas ---------------------------------------------------------------

    @property
    def project(self) -> Project:
        return self.store.project

    def selected_clip(self):  # noqa: ANN201
        selection = state.selection()
        if not selection or selection[0] != "clip":
            return None
        project = self.store.project
        return project.clip(selection[1]) if project.has_clip(selection[1]) else None

    def _guard_editable(self) -> bool:
        if not state.project_open():
            return False
        if self.store.read_only:
            state.toast("warning", "Proyecto en modo lectura (de una versión más nueva de la app)")
            return False
        return True

    def _apply(self, label: str, project: Project) -> bool:
        if not self._guard_editable():
            return False
        try:
            project.validate()
        except ProjectError as exc:
            state.toast("danger", f"Operación inválida: {exc.message}")
            return False
        return self.store.apply(label, project)

    # -- comandos de edición -------------------------------------------------------

    def split(self) -> None:
        if not self._guard_editable():
            return
        try:
            project = tl.split(self.project, state.playhead())
        except ProjectError as exc:
            state.toast("info", exc.message)
            return
        if self._apply("dividir", project):
            pos = tl.locate(project, state.playhead())
            if pos is not None:
                state.selection.set(("clip", pos.clip.id))

    def delete_selected(self) -> None:
        selection = state.selection()
        if not selection or not self._guard_editable():
            return
        if selection[0] == "clip":
            clip_id = selection[1]
            if not self.project.has_clip(clip_id):
                return
            index = self.project.video.index_of(clip_id)
            project = tl.ripple_delete(self.project, clip_id)
            if self._apply("eliminar clip", project):
                clips = project.video.clips
                state.selection.set(("clip", clips[min(index, len(clips) - 1)].id) if clips else None)
        elif selection[0] == "transition":
            self.set_transition(None)

    def undo(self) -> None:
        self.cancel_drag()
        label = self.store.undo()
        if label is None:
            state.toast("info", "Nada que deshacer")
        else:
            state.toast("info", f"Deshacer: {label}")
        self._sync_meta()

    def redo(self) -> None:
        self.cancel_drag()
        label = self.store.redo()
        if label is None:
            state.toast("info", "Nada que rehacer")
        else:
            state.toast("info", f"Rehacer: {label}")
        self._sync_meta()

    def select_clip(self, clip_id: str | None) -> None:
        state.selection.set(("clip", clip_id) if clip_id else None)

    def select_transition(self, index: int) -> None:
        state.selection.set(("transition", index))

    def set_speed(self, speed: float) -> None:
        clip = self.selected_clip()
        if clip is None:
            return
        self._apply("velocidad", tl.set_speed(self.project, clip.id, speed))

    def _transition_index(self) -> int | None:
        selection = state.selection()
        if not selection:
            return None
        if selection[0] == "transition":
            return int(selection[1])
        clip = self.selected_clip()
        if clip is None:
            return None
        index = self.project.video.index_of(clip.id)
        return index if index < len(self.project.video.transitions) else None

    def set_transition(self, kind: str | None, duration: float | None = None) -> None:
        index = self._transition_index()
        if index is None or not self._guard_editable():
            return
        current = self.project.video.transitions[index]
        if duration is None:
            duration = current.duration if current is not None else 0.5
        try:
            project, clamped = tl.set_transition(self.project, index, kind, duration)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply("transición", project) and clamped:
            limit = project.video.transitions[index].duration  # type: ignore[union-attr]
            state.toast("warning", f"Transición acotada a {limit:.2f} s (la mitad del clip más corto)")

    def set_transition_duration(self, duration: float) -> None:
        index = self._transition_index()
        if index is None:
            return
        current = self.project.video.transitions[index]
        self.set_transition(current.kind if current is not None else "fade", duration)

    def _trim_selected(self, label: str, new_in: float | None, new_out: float | None) -> None:
        clip = self.selected_clip()
        if clip is None:
            return
        project = tl.trim(
            self.project, clip.id,
            clip.in_ if new_in is None else new_in,
            clip.out_ if new_out is None else new_out,
        )
        if not self._apply(label, project):
            self._sync_inspector()  # el texto vuelve al valor real

    def submit_in(self, text: str) -> None:
        try:
            value = tl.parse_time(text)
        except ValueError:
            state.toast("warning", "Tiempo inválido: usa mm:ss.mmm")
            self._sync_inspector()
            return
        self._trim_selected("cambiar entrada", value, None)

    def submit_out(self, text: str) -> None:
        try:
            value = tl.parse_time(text)
        except ValueError:
            state.toast("warning", "Tiempo inválido: usa mm:ss.mmm")
            self._sync_inspector()
            return
        self._trim_selected("cambiar salida", None, value)

    def mark_in(self) -> None:
        """Entrada del clip seleccionado = lo que se ve en el playhead."""
        clip = self.selected_clip()
        pos = tl.locate(self.project, state.playhead())
        if clip is None or pos is None or pos.clip.id != clip.id:
            state.toast("info", "Coloca el playhead sobre el clip seleccionado")
            return
        self._trim_selected("marcar entrada", pos.t_source, None)

    def mark_out(self) -> None:
        clip = self.selected_clip()
        pos = tl.locate(self.project, state.playhead())
        if clip is None or pos is None or pos.clip.id != clip.id:
            state.toast("info", "Coloca el playhead sobre el clip seleccionado")
            return
        self._trim_selected("marcar salida", None, pos.t_source)

    def set_project_fps(self, fps: int) -> None:
        if fps != self.project.fps:
            self._apply("fps del proyecto", replace(self.project, fps=int(fps)))

    # -- transporte ---------------------------------------------------------------

    def seek(self, t: float) -> None:
        project = self.project
        total = tl.total_duration(project)
        t = tl.frame_round(max(0.0, min(t, total)), project.fps)
        state.playhead.set(t)
        self.player.seek(t)

    def step(self, frames: int) -> None:
        if self.player.playing:
            self.player.pause()
        fps = self.project.fps
        current = tl.frame_index(state.playhead(), fps)
        self.seek((current + frames) / fps)

    def step_seconds(self, seconds: float) -> None:
        if self.player.playing:
            self.player.pause()
        self.seek(state.playhead() + seconds)

    def go_start(self) -> None:
        self.seek(0.0)

    def go_end(self) -> None:
        self.seek(tl.total_duration(self.project))

    def toggle_play(self) -> None:
        if not self.project.video.clips:
            return
        if self.player.playing:
            self.player.pause()
            self.seek(self.player.time)
        else:
            self.player.play(state.playhead())

    def toggle_preview_audio(self) -> None:
        enabled = not state.preview_audio()
        state.preview_audio.set(enabled)
        audio = self.player.audio
        if audio is not None:
            audio.muted = not enabled
            if not enabled:
                audio.stop()
            elif self.player.playing:
                audio.start(self.project, self.player.time)
        state.toast("info", "Audio del preview activado" if enabled else "Preview en silencio")

    # -- zoom -------------------------------------------------------------------

    def set_zoom(self, zoom: float) -> None:
        state.tl_zoom.set(round(tl.clamp_zoom(zoom), 3))

    def zoom_in(self) -> None:
        self.set_zoom(state.tl_zoom() * 1.5)

    def zoom_out(self) -> None:
        self.set_zoom(state.tl_zoom() / 1.5)

    def zoom_fit(self) -> None:
        self.set_zoom(tl.fit_zoom(tl.total_duration(self.project), state.tl_view_w()))

    def on_timeline_scroll(self, x: float, width: float) -> None:
        state.tl_scroll.set(round(x, 1))
        if width > 0:
            state.tl_view_w.set(round(width, 1))

    def content_x(self, window_x: float) -> float:
        from .timeline import TL_X0

        return window_x - TL_X0 + state.tl_scroll()

    # -- drags del timeline ---------------------------------------------------------

    def _snap(self, t: float, candidates: list[float], invert: bool = False) -> float:
        if state.snap_on() == invert:
            return t
        return tl.snap(t, candidates, state.tl_zoom())

    def begin_move(self, clip_id: str) -> None:
        if not self._guard_editable() or not self.project.has_clip(clip_id):
            return
        self.cancel_drag()
        project = self.project
        index = project.video.index_of(clip_id)
        start = tl.clip_starts(project.video)[index]
        self.store.begin("mover clip")
        self._drag = {"kind": "move", "id": clip_id, "project": project, "start": start, "dx": 0.0}
        state.selection.set(("clip", clip_id))

    def drag_move(self, dx: float) -> None:
        drag = self._drag
        if drag is None or drag["kind"] != "move":
            return
        drag["dx"] = dx
        if abs(dx) < _CLICK_SLOP_PX:
            return
        base: Project = drag["project"]
        clip = base.clip(drag["id"])
        ghost = drag["start"] + dx / state.tl_zoom()
        cands = tl.snap_candidates(base, playhead=state.playhead(), exclude=clip.id)
        snapped_start = self._snap(ghost, cands)
        snapped_end = self._snap(ghost + clip.duration, cands) - clip.duration
        ghost = snapped_start if snapped_start != ghost else snapped_end
        ghost = max(0.0, ghost)
        index = tl.drop_index(base, clip.id, ghost)
        self.store.update(tl.move(base, clip.id, index))
        state.tl_drag.set(state.TimelineDrag("move", clip.id, ghost=round(ghost, 4)))

    def begin_trim(self, clip_id: str, side: str) -> None:
        if not self._guard_editable() or not self.project.has_clip(clip_id):
            return
        self.cancel_drag()
        project = self.project
        index = project.video.index_of(clip_id)
        start = tl.clip_starts(project.video)[index]
        self.store.begin("recortar")
        self._drag = {"kind": f"trim_{side}", "id": clip_id, "project": project, "index": index, "start": start}
        state.selection.set(("clip", clip_id))

    def drag_trim(self, dx: float) -> None:
        drag = self._drag
        if drag is None or not drag["kind"].startswith("trim"):
            return
        base: Project = drag["project"]
        clip = base.clip(drag["id"])
        index, start = drag["index"], drag["start"]
        zoom = state.tl_zoom()
        # Candidatos fijos durante el recorte: lo que hay antes del clip y el playhead
        # (lo de después se desplaza con el propio recorte).
        starts = tl.clip_starts(base.video)
        cands = [0.0, state.playhead()]
        for j in range(index):
            cands += [starts[j], starts[j] + base.video.clips[j].duration]
        if drag["kind"] == "trim_l":
            edge = self._snap(start + dx / zoom, cands)
            new_in = clip.in_ + (edge - start) * clip.speed
            project = tl.trim(base, clip.id, new_in, clip.out_)
            shift = (project.clip(clip.id).in_ - clip.in_) / clip.speed
            self.store.update(project)
            state.tl_drag.set(state.TimelineDrag("trim_l", clip.id, shift_index=index, shift=round(shift, 5)))
            self.player.seek(tl.clip_starts(project.video)[index])  # se ve el nuevo primer frame
        else:
            edge = self._snap(start + clip.duration + dx / zoom, cands)
            new_out = clip.in_ + (edge - start) * clip.speed
            project = tl.trim(base, clip.id, clip.in_, new_out)
            self.store.update(project)
            state.tl_drag.set(state.TimelineDrag("trim_r", clip.id))
            new_clip = project.clip(clip.id)
            self.player.seek(start + new_clip.duration - 1.0 / project.fps)  # último frame

    def end_drag(self) -> None:
        drag, self._drag = self._drag, None
        if drag is None:
            return
        state.tl_drag.set(None)
        if drag["kind"] == "move" and abs(drag.get("dx", 0.0)) < _CLICK_SLOP_PX:
            self.store.cancel()  # fue un clic: solo selecciona
        else:
            self.store.commit()
        self._sync_meta()
        if drag["kind"].startswith("trim"):
            self.player.seek(state.playhead())

    def cancel_drag(self) -> bool:
        """Esc: deshace el drag en curso (vuelve al snapshot del `begin`)."""
        drag, self._drag = self._drag, None
        if drag is None:
            return False
        self.store.cancel()
        state.tl_drag.set(None)
        self.player.seek(state.playhead())
        return True

    def scrub_to_window_x(self, window_x: float) -> None:
        if not state.project_open():
            return
        t = tl.time_at_x(self.content_x(window_x), state.tl_zoom())
        project = self.project
        if state.snap_on():
            t = tl.snap(t, tl.snap_candidates(project), state.tl_zoom(), threshold_px=5)
        self.seek(t)

    def begin_playhead_drag(self) -> None:
        self._playhead_start = state.playhead()

    def drag_playhead(self, dx: float) -> None:
        start = getattr(self, "_playhead_start", state.playhead())
        self.seek(start + dx / state.tl_zoom())

    # -- teclado -------------------------------------------------------------------

    def on_key(self, e, *, from_input: bool = False) -> bool | None:  # noqa: ANN001
        """Atajos del editor (docs/05). Las teclas «de texto» que vienen de un campo enfocado
        se dejan pasar: ahí `s` o `Espacio` se escriben, no dividen ni reproducen."""
        plain = not (e.ctrl or e.meta or e.alt)
        if from_input and plain:
            return None
        if e.matches("escape"):
            if self.cancel_drag():
                return True
            if state.import_open():
                state.import_open.set(False)
                return True
            if state.selection() is not None:
                state.selection.set(None)
                return True
            return None
        if e.matches("mod+z"):
            self.undo()
            return True
        if e.matches("mod+y") or e.matches("mod+shift+z"):
            self.redo()
            return True
        if e.matches("mod+s"):
            self.save()
            return True
        if e.matches("mod+i"):
            self.open_import()
            return True
        if e.matches("mod+e"):
            self.export_hint()
            return True
        if not state.project_open():
            return None
        if e.matches("space"):
            self.toggle_play()
            return True
        if e.matches("left"):
            self.step(-1)
            return True
        if e.matches("right"):
            self.step(1)
            return True
        if e.matches("shift+left"):
            self.step_seconds(-1.0)
            return True
        if e.matches("shift+right"):
            self.step_seconds(1.0)
            return True
        if e.matches("home"):
            self.go_start()
            return True
        if e.matches("end"):
            self.go_end()
            return True
        if e.matches("s"):
            self.split()
            return True
        if e.matches("delete") or e.matches("backspace"):
            self.delete_selected()
            return True
        if e.matches("m"):
            self.toggle_preview_audio()
            return True
        if plain and e.text in ("+", "="):
            self.zoom_in()
            return True
        if plain and e.text == "-":
            self.zoom_out()
            return True
        if plain and e.text == "0":
            self.zoom_fit()
            return True
        return None

    # -- proyectos (IO en hilos) ----------------------------------------------------

    def export_hint(self) -> None:
        state.toast("info", "El export (MP4, presets y transiciones con FFmpeg) llega en F4")

    def refresh_recent(self) -> None:
        def worker() -> None:
            items = []
            for file in store_mod.list_projects(state.projects_dir())[:12]:
                try:
                    result = store_mod.load_project(file)
                except ProjectError:
                    continue
                project = result.project
                items.append(state.RecentProject(
                    path=str(file),
                    name=project.name,
                    modified_label=time.strftime("%Y-%m-%d %H:%M", time.localtime(file.stat().st_mtime)),
                    clips=len(project.video.clips),
                    duration_label=tl.format_time(tl.total_duration(project), millis=False),
                ))
            state.recent_projects.set(tuple(items))

        threading.Thread(target=worker, daemon=True, name="recent-projects").start()

    def _activate(self, project: Project, path: Path | None, *, saved: bool, read_only: bool = False) -> None:
        self.cancel_drag()
        if self.player.playing:
            self.player.pause()
        self.store.reset(project, path, saved=saved)
        self.store.read_only = read_only
        state.selection.set(None)
        state.project_path.set(str(path) if path else "")
        state.project_open.set(True)
        self._sync_meta()
        self._refresh_offline(project)
        self.seek(0.0)
        state.tl_zoom.set(tl.fit_zoom(tl.total_duration(project), state.tl_view_w()))
        for asset in project.assets:
            self._queue_waveform(asset)
        self.settings.last_project = str(path) if path else ""

    def _refresh_offline(self, project: Project) -> None:
        state.offline_assets.set(frozenset(a.id for a in project.assets if media.is_offline(a)))

    def _save_current_locked(self) -> None:
        if self.store.dirty and self.store.path is not None and not self.store.read_only:
            self.store.save()

    def open_project(self, path: Path, *, switch: bool = True) -> None:
        def worker() -> None:
            with self._io_lock:
                try:
                    self._save_current_locked()
                    result = store_mod.load_project(path)
                except (ProjectError, OSError) as exc:
                    state.toast("danger", f"No se pudo abrir el proyecto: {exc}")
                    return
                self._activate(result.project, Path(path), saved=not result.from_backup, read_only=result.read_only)
                for warning in result.warnings:
                    state.toast("warning", warning.capitalize())
                if state.offline_assets():
                    state.toast("warning", f"{len(state.offline_assets())} archivo(s) del proyecto no se encuentran")
                if switch:
                    state.screen.set("editor")
            self.refresh_recent()

        threading.Thread(target=worker, daemon=True, name="open-project").start()

    def open_recording(self, mp4: Path) -> None:
        """«✂ Editar» de la Biblioteca: abre su proyecto (si ya existe) o crea uno con el video."""
        existing = store_mod.project_file(state.projects_dir(), Path(mp4).stem)
        if existing.exists():
            self.open_project(existing)
            return
        self.import_paths([Path(mp4)], new_project=True)

    def _probe_all(self, paths: list[Path]) -> list[MediaAsset]:
        assets: list[MediaAsset] = []
        for path in paths:
            state.importing.set((*state.importing(), path.name))
            try:
                assets.append(media.probe(path))
            except RecStudioError as exc:
                state.toast("danger", exc.message)
            finally:
                state.importing.set(tuple(n for n in state.importing() if n != path.name))
        return assets

    def import_paths(self, paths: list[Path], *, new_project: bool = False) -> None:
        """Añade videos al final del timeline (o crea un proyecto con ellos)."""
        if not paths:
            return

        def worker() -> None:
            with self._io_lock:
                assets = self._probe_all([Path(p) for p in paths])
                if not assets:
                    return
                state.import_open.set(False)
                if new_project or not state.project_open():
                    try:
                        self._save_current_locked()
                    except (OSError, ProjectError):
                        pass
                    first = assets[0]
                    project = Project(
                        name=first.name, fps=_nearest_fps(first.fps),
                        width=first.width or 1920, height=first.height or 1080,
                    )
                    for asset in assets:
                        project, _ = tl.append_clip(project, asset)
                    path = store_mod.unique_project_file(state.projects_dir(), first.name)
                    try:
                        store_mod.save_project(path, project)
                    except OSError as exc:
                        state.toast("danger", f"No se pudo crear el proyecto: {exc}")
                        return
                    self._activate(project, path, saved=True)
                    state.screen.set("editor")
                    state.toast("success", f"Proyecto «{project.name}» creado")
                else:
                    if self.store.read_only:
                        state.toast("warning", "Proyecto en modo lectura: no se puede importar")
                        return
                    project = self.project
                    for asset in assets:
                        project, _ = tl.append_clip(project, asset)
                    self.store.apply("importar", project)
                    self._refresh_offline(project)
                    self.zoom_fit()
                    for asset in assets:
                        self._queue_waveform(project.asset_for_path(asset.path) or asset)
                    state.toast("success", f"{len(assets)} video(s) añadidos al final del timeline")
            self.refresh_recent()

        threading.Thread(target=worker, daemon=True, name="import").start()

    def open_import(self) -> None:
        state.import_open.set(True)

    def browse_import(self) -> None:
        """Diálogo nativo de Windows (en un hilo: no bloquea la UI)."""

        def worker() -> None:
            from ..platform import dialogs

            try:
                chosen = dialogs.open_videos(state.recordings_dir())
            except OSError as exc:
                state.toast("danger", f"No se pudo abrir el diálogo: {exc}")
                return
            if chosen:
                self.import_paths(chosen)

        threading.Thread(target=worker, daemon=True, name="browse").start()

    def save(self) -> None:
        if not state.project_open():
            return

        def worker() -> None:
            with self._io_lock:
                try:
                    path = self.store.save()
                except (OSError, ProjectError) as exc:
                    state.toast("danger", f"No se pudo guardar: {exc}")
                    return
                self._sync_meta()
                state.toast("success", f"Guardado · {path.parent.name}")
            self.refresh_recent()

        threading.Thread(target=worker, daemon=True, name="save").start()

    def close_project(self) -> None:
        def worker() -> None:
            with self._io_lock:
                try:
                    self._save_current_locked()
                except (OSError, ProjectError) as exc:
                    state.toast("danger", f"No se pudo guardar antes de cerrar: {exc}")
                    return
                if self.player.playing:
                    self.player.pause()
                self.store.reset(Project(), None)
                state.project_open.set(False)
                state.project_path.set("")
                state.selection.set(None)
                state.preview_frame.set(b"")
                state.playhead.set(0.0)
                self.settings.last_project = ""
                self._sync_meta()
            self.refresh_recent()

        threading.Thread(target=worker, daemon=True, name="close-project").start()

    # -- waveforms ----------------------------------------------------------------

    def _queue_waveform(self, asset: MediaAsset) -> None:
        if not asset.has_audio or media.is_offline(asset):
            return
        if media.cached_waveform(asset.path) is not None:
            state.waveforms.set(state.waveforms() + 1)
            return
        with self._wave_lock:
            if asset.path in self._wave_pending:
                return
            self._wave_pending.add(asset.path)

        def worker() -> None:
            try:
                media.waveform(asset.path)
                state.waveforms.set(state.waveforms() + 1)
            except RecStudioError:
                pass
            finally:
                with self._wave_lock:
                    self._wave_pending.discard(asset.path)

        threading.Thread(target=worker, daemon=True, name="waveform").start()
