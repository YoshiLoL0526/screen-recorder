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
from ..core import effects as fx
from ..core import graph as graph_mod
from ..core import keyframes as keyframes_mod
from ..core import store as store_mod
from ..core import timeline as tl
from ..core.errors import ProjectError, RecStudioError
from ..core.project import FPS_CHOICES, MediaAsset, Project
from ..core.store import ProjectStore
from ..platform import paths
from ..services import export as export_mod
from ..services import library, media
from ..services import playback as playback_mod
from ..services.settings import Settings

EDITOR: "EditorController | None" = None

_AUTOSAVE_POLL = 2.0
_CLICK_SLOP_PX = 3.0
SPEED_CHOICES = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)
TRANSITION_DURATIONS = (0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0)

# Campos de efectos del inspector: (signal, efecto, parámetro, valor neutro, etiqueta de undo)
EFFECT_FIELDS = (
    (state.insp_volume, "volume", "gain", 1.0, "volumen"),
    (state.insp_fade_in, "fade_in", "seconds", 0.0, "fundido de entrada"),
    (state.insp_fade_out, "fade_out", "seconds", 0.0, "fundido de salida"),
    (state.insp_zoom, "zoom", "factor", 1.0, "zoom"),
    (state.insp_brightness, "eq", "brightness", 0.0, "brillo"),
    (state.insp_contrast, "eq", "contrast", 1.0, "contraste"),
    (state.insp_saturation, "eq", "saturation", 1.0, "saturación"),
)


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
        self.exporter = export_mod.Exporter(self._on_export)

    # -- ciclo de vida ---------------------------------------------------------

    def boot(self) -> None:
        global EDITOR
        EDITOR = self
        state.preview_audio.set(bool(self.settings.preview_audio))
        self.player.audio.muted = not state.preview_audio()  # type: ignore[union-attr]
        # Los `Signal` son globales y `subscribe` no se deshace: tras `shutdown` un controlador
        # viejo (tests con varias apps en el mismo proceso) no debe reaccionar ni pisar el
        # proyecto del nuevo.
        def live(fn):  # noqa: ANN001, ANN202
            return lambda value: None if self._closing.is_set() else fn(value)

        state.selection.subscribe(live(lambda _v: self._sync_inspector()))
        state.insp_speed.subscribe(live(self._on_insp_speed))
        state.insp_transition.subscribe(live(self._on_insp_transition))
        state.insp_transition_dur.subscribe(live(self._on_insp_transition_dur))
        state.insp_fps.subscribe(live(lambda fps: self.set_project_fps(int(fps)) if state.project_open() else None))
        for signal, kind, name, _neutral, label in EFFECT_FIELDS:
            signal.subscribe(live(lambda value, k=kind, n=name, lb=label: self._on_insp_effect(k, n, lb, value)))
        state.insp_track_gain.subscribe(live(self._on_insp_track_gain))
        if self.settings.export_preset in graph_mod.PRESET_IDS:
            state.export_preset.set(self.settings.export_preset)
        self.player.start()
        threading.Thread(target=self._autosave_loop, daemon=True, name="autosave").start()
        self.refresh_recent()
        last = self.settings.last_project
        if last and Path(last).exists():
            self.open_project(Path(last), switch=False)

    def shutdown(self) -> None:
        self._closing.set()
        if self.exporter.busy:  # el parcial no debe quedarse a medias en la carpeta
            self.exporter.cancel()
            self.exporter.wait(5.0)
        self.player.stop()
        if self.store.dirty and self.store.path is not None and not self.store.read_only:
            try:
                self.store.save()
            except (OSError, ProjectError):
                pass
        self.settings.last_project = str(self.store.path) if self.store.path else ""
        self.settings.preview_audio = bool(state.preview_audio())
        self.settings.export_preset = state.export_preset()

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
        state.insp_track_gain.set(0.0 if project.video.muted else project.video.gain)
        clip = self.selected_clip()
        if clip is not None:
            state.insp_in.set(tl.format_time(clip.in_))
            state.insp_out.set(tl.format_time(clip.out_))
            state.insp_speed.set(clip.speed)
            for signal, kind, name, neutral, _label in EFFECT_FIELDS:
                signal.set(fx.param(clip, kind, name, neutral))
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

    def _on_insp_effect(self, kind: str, name: str, label: str, value: float) -> None:
        clip = self.selected_clip()
        if clip is None:
            return
        neutral = next(n for _s, k, p, n, _l in EFFECT_FIELDS if k == kind and p == name)
        if abs(fx.param(clip, kind, name, neutral) - float(value)) < 1e-6:
            return
        self.set_effect(kind, label, **{name: float(value)})

    def _on_insp_track_gain(self, value: float) -> None:
        video = self.project.video
        current = 0.0 if video.muted else video.gain
        if state.project_open() and abs(current - float(value)) > 1e-6:
            self.set_track_gain(float(value))

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

    def set_effect(self, kind: str, label: str | None = None, **params: float) -> None:
        clip = self.selected_clip()
        if clip is None:
            return
        self._apply(label or kind, fx.set_effect(self.project, clip.id, kind, **params))

    def reset_effects(self) -> None:
        clip = self.selected_clip()
        if clip is None or not clip.effects:
            return
        self._apply("quitar efectos", fx.clear_effects(self.project, clip.id))

    def set_track_gain(self, value: float) -> None:
        """Volumen del audio original de todos los clips (0 = silenciado)."""
        value = max(0.0, min(fx.GAIN_MAX, value))
        video = replace(self.project.video, muted=value <= 0, gain=value if value > 0 else self.project.video.gain)
        self._apply("volumen del audio original", replace(self.project, video=video))

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
        self.set_preview_audio(not state.preview_audio())

    def set_preview_audio(self, enabled: bool, *, notify: bool = True) -> None:
        state.preview_audio.set(enabled)
        audio = self.player.audio
        if audio is not None:
            audio.muted = not enabled
            if not enabled:
                audio.stop()
            elif self.player.playing:
                audio.start(self.project, self.player.time)
        if notify:
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

    # -- disposición (F5): divisores e inspector ---------------------------------------

    def begin_split(self) -> None:
        from .timeline import max_tracks_extra

        # Se parte del alto efectivo (ya topado), no del guardado: si la ventana encogió, el
        # divisor responde desde donde se ve y no desde un valor que ya no cabe.
        extra = min(state.tl_tracks_extra(), max_tracks_extra(state.view_h()))
        self._split_start = (state.inspector_w(), extra)

    def drag_split(self, which: str, dx: float, dy: float) -> None:
        """Aplica el delta acumulado de un divisor (`DragEvent` da deltas desde el inicio)."""
        from .timeline import (
            INSPECTOR_W_MAX,
            INSPECTOR_W_MIN,
            LABEL_W,
            PREVIEW_MIN_W,
            SPLIT_W,
            TRACKS_EXTRA_MIN,
            max_tracks_extra,
        )

        width0, extra0 = getattr(self, "_split_start", (state.inspector_w(), state.tl_tracks_extra()))
        if which == "inspector":
            room = state.tl_view_w() + LABEL_W - SPLIT_W - PREVIEW_MIN_W  # ancho del editor − preview mín.
            limit = max(INSPECTOR_W_MIN, min(INSPECTOR_W_MAX, room))
            state.inspector_w.set(round(max(INSPECTOR_W_MIN, min(limit, width0 - dx))))
        else:
            limit = max_tracks_extra(state.view_h())
            state.tl_tracks_extra.set(round(max(TRACKS_EXTRA_MIN, min(limit, extra0 - dy))))

    def toggle_inspector(self) -> None:
        state.inspector_open.set(not state.inspector_open())

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
            if state.export_open():
                self.close_export()
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
            self.open_export()
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
        if e.matches("i"):
            self.toggle_inspector()
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

    # -- export (F4) ---------------------------------------------------------------

    def open_export(self) -> None:
        if not state.project_open():
            return
        if not self.project.video.clips:
            state.toast("info", "El timeline está vacío: importa un video primero")
            return
        progress = state.export_progress()
        if progress is None or not progress.active:
            state.export_progress.set(None)
            state.export_name.set(self.project.name)
        blockers = tuple(keyframes_mod.blockers(self.project))
        state.export_blockers.set(blockers)
        if blockers and state.export_preset() == "copia":
            state.export_preset.set("rapido")
        state.export_open.set(True)

    def close_export(self) -> None:
        """Cierra el modal; un export en curso sigue (la barra del editor muestra el %)."""
        state.export_open.set(False)
        progress = state.export_progress()
        if progress is not None and not progress.active:
            state.export_progress.set(None)

    def start_export(self) -> None:
        if self.exporter.busy:
            state.toast("info", "Ya hay un export en curso")
            return
        project = self.project
        missing = export_mod.offline_assets(project)
        if missing:
            state.toast("danger", f"Reubica los archivos que faltan antes de exportar: {', '.join(missing)}")
            return
        preset = state.export_preset()
        if preset == "copia" and state.export_blockers():
            state.toast("warning", "«Sin recodificar» no es posible: " + "; ".join(state.export_blockers()))
            return
        name = state.export_name().strip() or project.name
        out = export_mod.unique_output(state.exports_dir(), name)
        self.settings.export_preset = preset
        try:
            self.exporter.start(project, preset, out)
        except RecStudioError as exc:
            state.toast("danger", exc.message)

    def cancel_export(self) -> None:
        self.exporter.cancel()

    def _on_export(self, progress: export_mod.ExportProgress) -> None:
        previous = state.export_progress()
        state.export_progress.set(progress)
        if previous is not None and previous.state == progress.state:
            return
        if progress.state == "done":
            state.toast("success", f"Exportado en {progress.elapsed:.1f} s · {Path(progress.out_path).name}")
        elif progress.state == "error":
            state.toast("danger", f"Export fallido: {progress.message}")
        elif progress.state == "cancelled":
            state.toast("info", "Export cancelado (archivo parcial borrado)")

    def play_export(self) -> None:
        progress = state.export_progress()
        if progress is not None and progress.state == "done":
            library.open_file(Path(progress.out_path))

    def reveal_export(self) -> None:
        progress = state.export_progress()
        if progress is not None and progress.out_path and Path(progress.out_path).exists():
            library.reveal_in_explorer(Path(progress.out_path))
        else:
            folder = state.exports_dir()
            if not paths.ensure_dir(folder):
                state.toast("warning", f"No se pudo crear «{folder}»: revisa los permisos")
                return
            library.open_file(folder)

    # -- archivos offline ---------------------------------------------------------

    def relocate_asset(self, asset_id: str) -> None:
        """«Reubicar…»: elige el archivo movido (diálogo en un hilo)."""
        if not self._guard_editable():
            return

        def worker() -> None:
            from ..platform import dialogs

            try:
                old = self.project.asset(asset_id)
                folder = Path(old.path).parent
                chosen = dialogs.open_videos(folder if folder.exists() else state.recordings_dir(),
                                             title=f"Reubicar «{old.name}»")
            except (OSError, ProjectError) as exc:
                state.toast("danger", f"No se pudo abrir el diálogo: {exc}")
                return
            if chosen:
                self.relocate_to(asset_id, chosen[0])

        threading.Thread(target=worker, daemon=True, name="relocate").start()

    def relocate_to(self, asset_id: str, path: Path) -> bool:
        """Sustituye el archivo del asset (bloqueante: llamar desde un hilo). El nuevo debe
        durar lo suficiente para sus clips (lo comprueba `Project.validate`)."""
        try:
            probed = media.probe(path, asset_id=asset_id)
        except RecStudioError as exc:
            state.toast("danger", exc.message)
            return False
        project = self.project
        assets = tuple(probed if a.id == asset_id else a for a in project.assets)
        if not self._apply("reubicar archivo", replace(project, assets=assets)):
            return False
        self._refresh_offline(self.project)
        self._queue_waveform(probed)
        state.toast("success", f"Archivo reubicado: {Path(path).name}")
        return True

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
