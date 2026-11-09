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

import os
import threading
import time
from dataclasses import replace
from pathlib import Path

from .. import state
from ..core import animation as anim
from ..core import audio_envelope as env
from ..core import canvas_edit as ce
from ..core import effects as fx
from ..core import elements as element_mod
from ..core import global_effects
from ..core import graph as graph_mod
from ..core import keyframes as keyframes_mod
from ..core import store as store_mod
from ..core import subtitles as subtitles_mod
from ..core import text_color as text_color_mod
from ..core import timeline as tl
from ..core.errors import ProjectError, RecStudioError
from ..core.project import (
    FPS_CHOICES,
    VIDEO_TRACKS_MAX,
    Clip,
    Element,
    MediaAsset,
    Project,
    Track,
    Transform,
    Transition,
    new_id,
)
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
MAX_AUDIO_TRACKS = 6  # alternativa al scroll vertical anidado (docs/19)

# Presets de lienzo (fase 2). `custom` deja los campos editables tal como están.
CANVAS_PRESETS = (
    ("1920x1080", 1920, 1080, "1920×1080 (16:9)"),
    ("2560x1440", 2560, 1440, "2560×1440 (16:9)"),
    ("3840x2160", 3840, 2160, "3840×2160 (4K)"),
    ("1080x1920", 1080, 1920, "1080×1920 (9:16)"),
    ("1080x1080", 1080, 1080, "1080×1080 (1:1)"),
    ("1080x1350", 1080, 1350, "1080×1350 (4:5)"),
)
CANVAS_SIZES = {key: (w, h) for key, w, h, _label in CANVAS_PRESETS}
CANVAS_LABELS = {key: label for key, _w, _h, label in CANVAS_PRESETS}

# Campos de la transformación del clip: (signal, campo, etiqueta de undo)
TRANSFORM_FIELDS = (
    (state.insp_fit, "fit", "encaje"),
    (state.insp_scale, "scale", "escala"),
    (state.insp_pos_x, "x", "posición X"),
    (state.insp_pos_y, "y", "posición Y"),
    (state.insp_rotation, "rotation", "rotación"),
    (state.insp_flip_h, "flip_h", "volteo horizontal"),
    (state.insp_flip_v, "flip_v", "volteo vertical"),
)
CROP_FIELDS = (
    (state.insp_crop_l, 0, "recorte izquierdo"),
    (state.insp_crop_t, 1, "recorte superior"),
    (state.insp_crop_r, 2, "recorte derecho"),
    (state.insp_crop_b, 3, "recorte inferior"),
)

# Campos numéricos del elemento seleccionado: (signal, campo, etiqueta de undo)
ELEMENT_FIELDS = (
    (state.insp_el_x, "x", "posición X del elemento"),
    (state.insp_el_y, "y", "posición Y del elemento"),
    (state.insp_el_width, "width", "ancho del elemento"),
    (state.insp_el_height, "height", "alto del elemento"),
    (state.insp_el_rotation, "rotation", "rotación del elemento"),
    (state.insp_el_opacity, "opacity", "opacidad del elemento"),
)
ELEMENT_DEFAULTS = {
    "rect": (0.4, 0.25, "#FF5470", "rectángulo"),
    "ellipse": (0.3, 0.3, "#7C5CFF", "elipse"),
    "triangle": (0.35, 0.3, "#F5B851", "triángulo"),
}

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
AUDIO_EFFECT_FIELDS = (
    (state.insp_audio_fade_in, "audio_fade_in", "seconds", 0.0, "fundido de audio de entrada"),
    (state.insp_audio_fade_out, "audio_fade_out", "seconds", 0.0, "fundido de audio de salida"),
)
# Efectos globales del proyecto: (signal, efecto, parámetro, valor neutro, etiqueta de undo).
GLOBAL_EFFECT_FIELDS = (
    (state.glob_brightness, "eq", "brightness", 0.0, "brillo global"),
    (state.glob_contrast, "eq", "contrast", 1.0, "contraste global"),
    (state.glob_saturation, "eq", "saturation", 1.0, "saturación global"),
    (state.glob_blur, "blur", "radius", 0.0, "blur global"),
    (state.glob_vignette, "vignette", "strength", 0.0, "viñeta global"),
)


def _nearest_fps(fps: float) -> int:
    return min(FPS_CHOICES, key=lambda f: abs(f - fps)) if fps > 0 else 60


def _int_or(value: str, fallback: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def _valid_audio_selection(project: Project, selection) -> bool:  # noqa: ANN001
    try:
        track_index, clip_index = int(selection[1]), int(selection[2])
    except (IndexError, TypeError, ValueError):
        return False
    return (
        0 <= track_index < len(project.audio)
        and 0 <= clip_index < len(project.audio[track_index].clips)
    )


class EditorController:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = ProjectStore(on_change=self._on_project)
        self.player = playback_mod.Player(
            self._on_frame, self._on_player_time, self._on_player_state,
            audio=playback_mod.AudioPreview(),
        )
        self._drag: dict | None = None
        self._canvas_drag: dict | None = None  # mover/escalar sobre el preview (docs/23)
        self._text_drag: dict | None = None  # mover/escalar un texto sobre el preview
        self._overlay_drag: dict | None = None  # mover/recortar una capa en el timeline
        self._envelope_drag: dict | None = None  # mover/añadir una clave de volumen (docs/37)
        self._keyframe_drag: dict | None = None  # mover una clave de animación (docs/38)
        self._closing = threading.Event()
        self._io_lock = threading.Lock()  # abrir/guardar/importar: de uno en uno
        self._wave_lock = threading.Lock()
        self._wave_pending: set[str] = set()
        self._thumb_lock = threading.Lock()
        self._thumb_pending: set[str] = set()
        self._proxy_lock = threading.Lock()
        self._proxy_pending: set[str] = set()
        self._poster_lock = threading.Lock()
        self._poster_pending: set[str] = set()
        self._poster_failed: set[str] = set()
        self._marquee_start: float | None = None
        self._clipboard: tl.Clipboard | None = None  # portapapeles del editor (fase 3)
        self._key_clipboard: state.KeyClipboard | None = None  # clave copiada (docs/41)
        self._last_element_id: str | None = None  # reinicia la escala de la próxima clave
        self._last_text_id: str | None = None  # reinicia escala/rotación de la próxima clave
        self.exporter = export_mod.Exporter(self._on_export)

    # -- ciclo de vida ---------------------------------------------------------

    def boot(self) -> None:
        global EDITOR
        EDITOR = self
        state.preview_audio.set(bool(self.settings.preview_audio))
        state.video_track_target.set(0)
        state.insp_el_track.set(0)
        self.player.audio.muted = not state.preview_audio()  # type: ignore[union-attr]
        # Los `Signal` son globales y aquí no se guardan los desuscribidores de `subscribe`: tras
        # `shutdown` un controlador viejo (tests con varias apps en el mismo proceso) no debe
        # reaccionar ni pisar el proyecto del nuevo.
        def live(fn):  # noqa: ANN001, ANN202
            return lambda value: None if self._closing.is_set() else fn(value)

        state.selection.subscribe(live(self._on_selection))
        state.key_add_on.subscribe(live(self._on_key_add_mode))
        state.insp_speed.subscribe(live(self._on_insp_speed))
        state.insp_transition.subscribe(live(self._on_insp_transition))
        state.insp_transition_dur.subscribe(live(self._on_insp_transition_dur))
        state.insp_fps.subscribe(live(lambda fps: self.set_project_fps(int(fps)) if state.project_open() else None))
        state.proj_canvas.subscribe(live(self._on_proj_canvas))
        state.proj_fit.subscribe(live(self._on_proj_fit))
        state.proj_background.subscribe(live(self._on_proj_background))
        for signal, field, _label in TRANSFORM_FIELDS:
            signal.subscribe(live(lambda value, f=field: self._on_insp_transform(f, value)))
        for signal, kind, name, _neutral, label in EFFECT_FIELDS:
            signal.subscribe(live(lambda value, k=kind, n=name, lb=label: self._on_insp_effect(k, n, lb, value)))
        for signal, kind, name, _neutral, label in AUDIO_EFFECT_FIELDS:
            signal.subscribe(live(lambda value, k=kind, n=name, lb=label: self._on_insp_audio_effect(k, lb, value)))
        for signal, kind, name, neutral, label in GLOBAL_EFFECT_FIELDS:
            signal.subscribe(live(
                lambda value, k=kind, n=name, nu=neutral, lb=label: self._on_global_effect(k, n, nu, lb, value)
            ))
        state.glob_grayscale.subscribe(live(self._on_global_grayscale))
        state.insp_track_gain.subscribe(live(self._on_insp_track_gain))
        state.insp_music_gain.subscribe(live(self._on_insp_music_gain))
        state.insp_music_duck.subscribe(live(self._on_insp_music_duck))
        state.insp_music_cross.subscribe(live(self._on_insp_music_cross))
        state.insp_music_clip_gain.subscribe(live(self._on_insp_music_clip_gain))
        state.insp_text_size.subscribe(live(lambda value: self._on_insp_text("size", value)))
        state.insp_text_color.subscribe(live(lambda value: self._on_insp_text("color", value)))
        state.insp_text_x.subscribe(live(lambda value: self._on_insp_text("x", value)))
        state.insp_text_y.subscribe(live(lambda value: self._on_insp_text("y", value)))
        state.insp_text_opacity.subscribe(live(lambda value: self._on_insp_text("opacity", value)))
        state.insp_text_bold.subscribe(live(lambda value: self._on_insp_text("bold", value)))
        for signal, field, label in ELEMENT_FIELDS:
            signal.subscribe(live(lambda value, f=field, lb=label: self._on_insp_element(f, value, lb)))
        state.insp_el_color.subscribe(live(lambda value: self._on_insp_element("color", value, "color del elemento")))
        state.insp_el_fit.subscribe(live(lambda value: self._on_insp_element("fit", value, "encaje del elemento")))
        state.insp_el_blur.subscribe(live(self._on_insp_element_blur))
        state.insp_el_gain.subscribe(live(self._on_insp_element_gain))
        state.insp_el_track.subscribe(live(self._on_insp_element_track))
        if self.settings.export_preset in (*graph_mod.PRESET_IDS, graph_mod.CUSTOM_PRESET_ID):
            state.export_preset.set(self.settings.export_preset)
        state.export_custom_width.set(str(self.settings.export_custom_width))
        state.export_custom_height.set(str(self.settings.export_custom_height))
        state.export_custom_fps.set(str(self.settings.export_custom_fps))
        state.export_custom_video_bitrate.set(self.settings.export_custom_video_bitrate)
        state.export_custom_audio_bitrate.set(self.settings.export_custom_audio_bitrate)
        state.export_custom_codec.set(self.settings.export_custom_codec)
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
        self._remember_export()

    def _remember_export(self) -> None:
        """Guarda el preset y la última configuración personalizada en `Settings` (docs/44)."""
        self.settings.export_preset = state.export_preset()
        self.settings.export_custom_width = _int_or(
            state.export_custom_width(), self.settings.export_custom_width)
        self.settings.export_custom_height = _int_or(
            state.export_custom_height(), self.settings.export_custom_height)
        self.settings.export_custom_fps = _int_or(
            state.export_custom_fps(), self.settings.export_custom_fps)
        if graph_mod.parse_bitrate(state.export_custom_video_bitrate()):
            self.settings.export_custom_video_bitrate = state.export_custom_video_bitrate()
        if graph_mod.parse_bitrate(state.export_custom_audio_bitrate()):
            self.settings.export_custom_audio_bitrate = state.export_custom_audio_bitrate()
        if state.export_custom_codec() in graph_mod.CUSTOM_CODECS:
            self.settings.export_custom_codec = state.export_custom_codec()

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
        elif selection and selection[0] == "audio":
            if not _valid_audio_selection(project, selection):
                state.selection.set(None)
        elif selection and selection[0] == "text" and not project.has_text(selection[1]):
            state.selection.set(None)
        elif selection and selection[0] == "marker" and not project.has_marker(selection[1]):
            state.selection.set(None)
        elif selection and selection[0] == "element" and not project.has_element(selection[1]):
            state.selection.set(None)
        clip_ids = {c.id for track in (project.video, *project.audio) for c in track.clips}
        if not state.multi_selection() <= clip_ids:
            state.multi_selection.set(frozenset(state.multi_selection() & clip_ids))
        total = tl.total_duration(project)
        if state.playhead() > total:
            self.seek(total)
        self._sync_loop()
        self._sync_inspector()
        self._queue_posters(project.assets)

    def _sync_meta(self) -> None:
        store = self.store
        state.project_dirty.set(store.dirty)
        state.undo_label.set(store.undo_label or "")
        state.redo_label.set(store.redo_label or "")
        state.project_read_only.set(store.read_only)

    def _on_selection(self, _value) -> None:  # noqa: ANN001
        """Al cambiar de capa, la clave de animación seleccionada deja de ser válida."""
        key = state.keyframe_key()
        selection = state.selection()
        if key is not None and (
            selection is None or selection[0] != key.scope or selection[1] != key.item
        ):
            state.keyframe_key.set(None)
        self._sync_inspector()

    def _on_key_add_mode(self, on: bool) -> None:
        """El modo «añadir clave» es excluyente con razor, marquesina y Env (docs/42)."""
        if not on:
            return
        state.razor_on.set(False)
        state.marquee_on.set(False)
        state.env_edit_on.set(False)
        state.marquee.set(None)
        selection = state.selection()
        if selection is None or selection[0] not in ("element", "text"):
            state.toast("info", "Selecciona un elemento o texto para añadir claves en el timeline")

    def _sync_inspector(self) -> None:
        """Lleva el modelo a los campos/selects del inspector (sus escuchas ignoran lo igual)."""
        project = self.store.project
        state.insp_fps.set(project.fps)
        state.insp_track_gain.set(0.0 if project.video.muted else project.video.gain)
        state.proj_canvas.set(self._canvas_key(project.width, project.height))
        state.proj_width.set(str(project.width))
        state.proj_height.set(str(project.height))
        state.proj_fit.set(project.default_fit)
        state.proj_background.set(project.background)
        state.proj_bg_hex.set(project.background)
        eq = global_effects.color_eq(project) or fx.EQ_DEFAULTS
        state.glob_brightness.set(eq["brightness"])
        state.glob_contrast.set(eq["contrast"])
        state.glob_saturation.set(eq["saturation"])
        state.glob_blur.set(global_effects.blur_radius(project))
        state.glob_vignette.set(global_effects.vignette_strength(project))
        state.glob_grayscale.set(global_effects.grayscale_on(project))
        clip = self.selected_clip()
        if clip is not None:
            state.insp_in.set(tl.format_time(clip.in_))
            state.insp_out.set(tl.format_time(clip.out_))
            state.insp_speed.set(clip.speed)
            for signal, kind, name, neutral, _label in EFFECT_FIELDS:
                signal.set(fx.param(clip, kind, name, neutral))
            for signal, kind, name, neutral, _label in AUDIO_EFFECT_FIELDS:
                signal.set(fx.param(clip, kind, name, neutral))
            transform = clip.transform or Transform()
            for signal, field, _label in TRANSFORM_FIELDS:
                signal.set(getattr(transform, field))
            for signal, index, _label in CROP_FIELDS:
                signal.set(f"{float(transform.crop[index]):.3f}".rstrip("0").rstrip("."))
        index = self._transition_index()
        if index is not None:
            current = project.video.transitions[index]
            state.insp_transition.set(current.kind if current is not None else "none")
            if current is not None:
                state.insp_transition_dur.set(current.duration)
        music = self.selected_music()
        if music is not None:
            state.insp_music_gain.set(0.0 if music.muted else music.gain)
            state.insp_music_duck.set(music.ducking)
            state.insp_music_cross.set(self._music_crossfade(music))
        audio = self.selected_audio()
        if audio is not None:
            _track_index, _clip_index, audio_clip = audio
            state.insp_music_clip_gain.set(fx.gain(audio_clip))
            for signal, kind, name, neutral, _label in AUDIO_EFFECT_FIELDS:
                signal.set(fx.param(audio_clip, kind, name, neutral))
            start = tl.clip_starts(self.project.audio[_track_index])[_clip_index]
            state.insp_audio_start.set(tl.format_time(start))
            state.insp_audio_end.set(tl.format_time(start + audio_clip.duration))
        overlay = self.selected_text()
        if overlay is not None:
            if overlay.id != self._last_text_id:
                # Escala y rotación son solo el valor de la próxima clave (no hay campo en el
                # modelo): al cambiar de texto vuelven a los valores neutros.
                state.insp_text_scale.set(1.0)
                state.insp_text_rotation.set(0.0)
                self._last_text_id = overlay.id
            state.insp_text.set(overlay.text)
            state.insp_text_start.set(tl.format_time(overlay.start))
            state.insp_text_end.set(tl.format_time(overlay.end))
            state.insp_text_size.set(overlay.size)
            state.insp_text_color.set(overlay.color)
            state.insp_text_x.set(overlay.x)
            state.insp_text_y.set(overlay.y)
            state.insp_text_opacity.set(overlay.opacity)
            state.insp_text_bold.set(overlay.bold)
        else:
            self._last_text_id = None
        marker = self.selected_marker()
        if marker is not None:
            state.insp_marker_name.set(marker.name)
        element = self.selected_element()
        if element is not None:
            if element.id != self._last_element_id:
                # `insp_el_scale` es solo el valor de la próxima clave (no hay campo en el
                # modelo): al cambiar de elemento vuelve a 1.0 para no arrastrar el anterior.
                state.insp_el_scale.set(1.0)
                self._last_element_id = element.id
            state.insp_el_start.set(tl.format_time(element.start))
            state.insp_el_end.set(tl.format_time(element.end))
            state.insp_el_x.set(element.x)
            state.insp_el_y.set(element.y)
            state.insp_el_width.set(element.width)
            state.insp_el_height.set(element.height)
            state.insp_el_rotation.set(element.rotation)
            state.insp_el_opacity.set(element.opacity)
            state.insp_el_blur.set(element_mod.blur_radius(element))
            state.insp_el_gain.set(element_mod.audio_gain(element))
            state.insp_el_track.set(element.video_track)
            state.insp_el_color.set(element.color)
            state.insp_el_fit.set(element.fit)
        else:
            self._last_element_id = None
        self._sync_keyframe_channel()

    def _sync_keyframe_channel(self) -> None:
        """El selector de canal solo admite los canales válidos de la capa seleccionada."""
        selection = state.selection()
        if selection is None:
            return
        if selection[0] == "element":
            allowed = anim.ELEMENT_CHANNELS
        elif selection[0] == "text":
            allowed = anim.TEXT_CHANNELS
        else:
            return
        if state.keyframe_channel() not in allowed:
            state.keyframe_channel.set(allowed[0])

    def _canvas_key(self, width: int, height: int) -> str:
        for key, (w, h) in CANVAS_SIZES.items():
            if (w, h) == (int(width), int(height)):
                return key
        return "custom"

    def _on_proj_canvas(self, key: str) -> None:
        if key in CANVAS_SIZES and state.project_open():
            self.set_project_size(*CANVAS_SIZES[key])

    def _on_proj_fit(self, fit: str) -> None:
        if state.project_open() and fit != self.project.default_fit:
            self.set_project_fit(str(fit))

    def _on_proj_background(self, color: str) -> None:
        if state.project_open() and str(color) != self.project.background:
            self.set_project_background(str(color))

    def _on_insp_transform(self, field: str, value) -> None:  # noqa: ANN001
        clip = self.selected_clip()
        if clip is None:
            return
        current = clip.transform or Transform()
        if getattr(current, field) == value:
            return
        self.set_transform(**{field: value})

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

    def _on_insp_audio_effect(self, kind: str, label: str, value: float) -> None:
        audio = self.selected_audio()
        clip = self.selected_clip()
        target = audio[2] if audio is not None else clip
        if target is None:
            return
        if abs(fx.param(target, kind, "seconds", 0.0) - float(value)) < 1e-6:
            return
        self.set_audio_effect(kind, label, seconds=float(value))

    def _on_global_effect(
        self, kind: str, name: str, neutral: float, label: str, value: float
    ) -> None:
        """Select de «Efectos globales»: ignora el eco de `_sync_inspector`."""
        if not state.project_open():
            return
        current = fx.param_in(self.project.effects, kind, name, neutral)
        if abs(current - float(value)) < 1e-6:
            return
        self.set_global_effect(kind, label, **{name: float(value)})

    def _on_global_grayscale(self, enabled: bool) -> None:
        self.set_global_grayscale(bool(enabled))

    def _on_insp_audio_start(self, text: str) -> None:
        if self.selected_audio() is None:
            return
        try:
            start = tl.parse_time(text)
        except ValueError:
            state.toast("warning", "Tiempo inválido: usa mm:ss.mmm")
            self._sync_inspector()
            return
        self.set_audio_clip_start(start)

    def _on_insp_audio_end(self, text: str) -> None:
        audio = self.selected_audio()
        if audio is None:
            return
        try:
            end = tl.parse_time(text)
        except ValueError:
            state.toast("warning", "Tiempo inválido: usa mm:ss.mmm")
            self._sync_inspector()
            return
        track_index, clip_index, clip = audio
        if clip.link_id:
            state.toast("warning", "El audio está enlazado al video: desvincúlalo para recortarlo")
            self._sync_inspector()
            return
        start = tl.clip_starts(self.project.audio[track_index])[clip_index]
        delta = end - (start + clip.duration)
        new_out = clip.out_ + delta * clip.speed
        self._apply("recortar audio", tl.trim_audio(self.project, track_index, clip.id, clip.in_, new_out))
        self._sync_inspector()

    def _on_insp_track_gain(self, value: float) -> None:
        video = self.project.video
        current = 0.0 if video.muted else video.gain
        if state.project_open() and abs(current - float(value)) > 1e-6:
            self.set_track_gain(float(value))

    def _on_insp_music_gain(self, value: float) -> None:
        track = self.selected_music()
        if track is None:
            return
        current = 0.0 if track.muted else track.gain
        if abs(current - float(value)) > 1e-6:
            self.set_music_gain(float(value))

    def _on_insp_music_duck(self, value: float) -> None:
        track = self.selected_music()
        if track is not None and abs(track.ducking - float(value)) > 1e-6:
            self.set_music_ducking(float(value))

    def _on_insp_music_cross(self, value: float) -> None:
        track = self.selected_music()
        if track is not None and abs(self._music_crossfade(track) - float(value)) > 1e-6:
            self.set_music_crossfade(float(value))

    def _on_insp_music_clip_gain(self, value: float) -> None:
        selected = self._selected_music_clip()
        if selected is not None and abs(fx.gain(selected[2]) - float(value)) > 1e-6:
            self.set_music_clip_gain(float(value))

    def _on_insp_text(self, name: str, value) -> None:  # noqa: ANN001
        overlay = self.selected_text()
        if overlay is None:
            return
        current = getattr(overlay, name)
        if current == value:
            return
        self._update_text(**{name: value})

    def _on_insp_element(self, name: str, value, label: str) -> None:  # noqa: ANN001
        element = self.selected_element()
        if element is None:
            return
        if name in ("x", "y", "width", "height", "rotation", "opacity"):
            value = round(float(value), 4)
        if getattr(element, name) == value:
            return
        self._update_element(label, **{name: value})

    def _on_insp_element_blur(self, radius: float) -> None:
        element = self.selected_element()
        if element is None:
            return
        radius = round(max(0.0, min(element_mod.BLUR_MAX, float(radius))), 3)
        if abs(element_mod.blur_radius(element) - radius) < 1e-6:
            return
        try:
            project = element_mod.set_blur(self.project, element.id, radius)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            self._sync_inspector()
            return
        if self._apply("desenfoque del elemento", project):
            self._sync_inspector()

    def _on_insp_element_gain(self, gain: float) -> None:
        element = self.selected_element()
        if element is not None:
            self._set_element_audio_gain(element, float(gain))

    def _on_insp_element_track(self, track: int) -> None:
        element = self.selected_element()
        if element is None or element.kind != "video":
            return
        track = max(0, min(VIDEO_TRACKS_MAX, int(track)))
        if track == element.video_track:
            return
        try:
            project = element_mod.set_video_track(self.project, element.id, track)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            self._sync_inspector()
            return
        label = element_mod.video_track_label(track)
        if self._apply(f"mover video a {label}", project):
            self._sync_inspector()
            state.toast("info", f"Video movido a {label}")

    def _set_element_audio_gain(self, element: Element, gain: float) -> None:
        if element.kind != "video":
            return
        gain = round(max(0.0, min(element_mod.AUDIO_GAIN_MAX, float(gain))), 3)
        if abs(element_mod.audio_gain(element) - gain) < 1e-6:
            return
        try:
            project = element_mod.set_audio_gain(self.project, element.id, gain)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            self._sync_inspector()
            return
        if self._apply("volumen del video superpuesto", project):
            self._sync_inspector()

    def mute_element_audio(self) -> None:
        """Silencia el audio del PiP seleccionado (sin tocar su imagen)."""
        element = self.selected_element()
        if element is not None and element.kind == "video":
            self._set_element_audio_gain(element, 0.0)

    def restore_element_audio(self) -> None:
        """Devuelve el audio del PiP seleccionado a su volumen original (1.0)."""
        element = self.selected_element()
        if element is not None and element.kind == "video":
            self._set_element_audio_gain(element, 1.0)

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
        if playing:
            self.end_canvas_drag()  # la capa se oculta al reproducir: cierra el gesto en curso
        else:
            state.play_rate.set(1.0)

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

    def selected_audio(self):  # noqa: ANN201
        """`(track_index, clip_index, Clip)` del clip de audio seleccionado (cualquier pista)."""
        selection = state.selection()
        if not selection or selection[0] != "audio":
            return None
        if not _valid_audio_selection(self.project, selection):
            return None
        track_index, clip_index = int(selection[1]), int(selection[2])
        return track_index, clip_index, self.project.audio[track_index].clips[clip_index]

    def selected_music(self):  # noqa: ANN201
        """Pista de audio seleccionada (compatibilidad con el inspector de música de B1)."""
        selection = state.selection()
        if not selection or selection[0] != "audio":
            return None
        index = int(selection[1])
        return self.project.audio[index] if 0 <= index < len(self.project.audio) else None

    def _selected_music_clip(self):  # noqa: ANN201
        return self.selected_audio()

    def selected_text(self):  # noqa: ANN201
        selection = state.selection()
        if not selection or selection[0] != "text":
            return None
        return self.project.text(selection[1]) if self.project.has_text(selection[1]) else None

    def selected_element(self):  # noqa: ANN201
        selection = state.selection()
        if not selection or selection[0] != "element":
            return None
        return self.project.element(selection[1]) if self.project.has_element(selection[1]) else None

    def selected_marker(self):  # noqa: ANN201
        selection = state.selection()
        if not selection or selection[0] != "marker":
            return None
        return self.project.marker(selection[1]) if self.project.has_marker(selection[1]) else None

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

    def _sync_audio(self, before: Project, project: Project, *, notify: bool = True) -> Project | None:
        """Sincroniza el audio enlazado con `project`; `None` si el resultado no cabe (se cancela)."""
        try:
            return tl.sync_linked_audio(project, before)
        except ProjectError as exc:
            if notify:
                state.toast("warning", exc.message)
            return None

    # -- comandos de edición -------------------------------------------------------

    def split(self) -> None:
        if not self._guard_editable():
            return
        if self.project.video.locked:
            state.toast("warning", "La pista de video está bloqueada")
            return
        try:
            project = tl.split_all(self.project, state.playhead())
        except ProjectError as exc:
            state.toast("info", exc.message)
            return
        if self._apply("dividir", project):
            pos = tl.locate(project, state.playhead())
            if pos is not None:
                state.selection.set(("clip", pos.clip.id))

    def split_audio_only(self) -> None:
        if not self._guard_editable():
            return
        try:
            project = tl.split_audio(self.project, state.playhead())
        except ProjectError as exc:
            state.toast("info", exc.message)
            return
        if self._apply("dividir audio", project):
            for track_index, track in enumerate(project.audio):
                starts = tl.clip_starts(track)
                for clip_index, (clip, start) in enumerate(zip(track.clips, starts)):
                    if abs(start - state.playhead()) < 1e-6 or start < state.playhead() < start + clip.duration:
                        self.select_audio(track_index, clip_index)
                        state.toast("success", f"Audio dividido en «{track.name or 'pista de audio'}»")
                        return
            state.toast("success", "Audio dividido en el playhead")

    def detach_audio(self) -> None:
        """«Separar audio»: saca el audio del clip de video a una pista absoluta enlazada."""
        if not self._guard_editable():
            return
        clip = self.selected_clip()
        if clip is None:
            audio = self.selected_audio()
            if audio is not None and audio[2].link_id:
                self.attach_audio()
            else:
                state.toast("info", "Selecciona un clip de video con audio")
            return
        if self.project.video.locked:
            state.toast("warning", "La pista de video está bloqueada")
            return
        has_track = any(
            t.mode == "absolute" and t.name == "Audio del video" for t in self.project.audio
        )
        target = next(
            (t for t in self.project.audio if t.mode == "absolute" and t.name == "Audio del video"),
            None,
        )
        if target is not None and target.locked:
            state.toast("warning", "La pista «Audio del video» está bloqueada")
            return
        if not has_track and len(self.project.audio) >= MAX_AUDIO_TRACKS:
            state.toast("warning", f"Máximo de {MAX_AUDIO_TRACKS} pistas de audio: quita una antes")
            return
        try:
            project, audio_id = tl.detach_audio(self.project, clip.id)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply("separar audio", project):
            found = tl.find_audio_clip(project, audio_id)
            if found is not None:
                self.select_audio(found[0], found[1])
            state.toast("success", "Audio separado en una pista absoluta (enlazado al video)")

    def attach_audio(self) -> None:
        """«Vincular audio»: borra el audio separado y reactiva el del clip de video."""
        if not self._guard_editable():
            return
        audio = self.selected_audio()
        video_id = audio[2].link_id if audio is not None else None
        if audio is not None and self.project.audio[audio[0]].locked:
            state.toast("warning", "La pista de audio está bloqueada")
            return
        if video_id is None:
            clip = self.selected_clip()
            video_id = clip.id if clip is not None else None
        if self.project.video.locked and video_id is not None:
            state.toast("warning", "La pista de video está bloqueada")
            return
        if video_id is not None:
            linked = tl.linked_audio_clips(self.project, video_id)
            if any(self.project.audio[track_index].locked for track_index, _index, _clip in linked):
                state.toast("warning", "La pista de audio está bloqueada")
                return
        if video_id is None:
            state.toast("info", "Selecciona el clip de video o su audio separado")
            return
        try:
            project = tl.attach_audio(self.project, video_id)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply("vincular audio", project):
            if project.has_clip(video_id):
                self.select_clip(video_id)
            state.toast("success", "Audio vinculado al video de nuevo")

    def unlink_audio(self) -> None:
        """«Desvincular audio»: mantiene el audio en su pista pero deja de seguir al video."""
        if not self._guard_editable():
            return
        audio = self.selected_audio()
        video_id = audio[2].link_id if audio is not None else None
        video = self.selected_clip()
        if video_id is None and video is not None and tl.linked_audio_clips(self.project, video.id):
            video_id = video.id
        if video_id is None:
            state.toast("info", "Selecciona el clip de video o su audio enlazado")
            return
        try:
            project = tl.unlink_audio(self.project, video_id)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply("desvincular audio", project):
            state.toast(
                "success",
                "Audio desvinculado: el video queda mudo y el audio puede editarse por separado",
            )

    def relink_audio(self) -> None:
        """«Vincular al video»: recupera el enlace de un audio libre sin borrarlo ni duplicarlo."""
        if not self._guard_editable():
            return
        audio = self.selected_audio()
        if audio is None:
            state.toast("info", "Selecciona el clip de audio que quieres volver a enlazar")
            return
        track_index, _clip_index, clip = audio
        track = self.project.audio[track_index]
        if track.locked:
            state.toast("warning", f"La pista «{track.name or 'audio'}» está bloqueada")
            return
        if self.project.video.locked:
            state.toast("warning", "La pista de video está bloqueada")
            return
        try:
            project = tl.relink_audio(self.project, clip.id)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply("vincular audio", project):
            state.toast("success", "Audio vinculado al video: vuelve a seguir sus cortes")

    def delete_selected(self) -> None:
        selection = state.selection()
        if not self._guard_editable():
            return
        if state.envelope_key() is not None:  # Supr con una clave de envolvente seleccionada
            self.delete_envelope_key()
            return
        if state.keyframe_key() is not None:  # Supr con una clave de animación seleccionada
            self.delete_keyframe_key()
            return
        multi = state.multi_selection()
        if multi:
            count = len(multi & {c.id for t in (self.project.video, *self.project.audio) for c in t.clips})
            if count == 0:
                state.multi_selection.set(frozenset())
            else:
                try:
                    project = tl.remove_items(self.project, multi)
                except ProjectError as exc:
                    state.toast("warning", exc.message)
                    return
                if self._apply(f"eliminar {count} clip{'s' if count != 1 else ''}", project):
                    state.multi_selection.set(frozenset())
                    state.selection.set(None)
            return
        if not selection:
            return
        if selection[0] == "clip":
            clip_id = selection[1]
            if not self.project.has_clip(clip_id):
                return
            if self.project.video.locked:
                state.toast("warning", "La pista de video está bloqueada")
                return
            index = self.project.video.index_of(clip_id)
            try:
                project = tl.ripple_delete(self.project, clip_id)
            except ProjectError as exc:
                state.toast("warning", exc.message)
                return
            if self._apply("eliminar clip", project):
                clips = project.video.clips
                state.selection.set(("clip", clips[min(index, len(clips) - 1)].id) if clips else None)
        elif selection[0] == "transition":
            self.set_transition(None)
        elif selection[0] == "text":
            self.delete_text()
        elif selection[0] == "element":
            self.delete_element()
        elif selection[0] == "marker":
            self.delete_marker()
        elif selection[0] == "audio":
            audio = self.selected_audio()
            if audio is None:
                return
            track_index, clip_index, clip = audio
            track = self.project.audio[track_index]
            if track.locked:
                state.toast("warning", f"La pista «{track.name or 'audio'}» está bloqueada")
                return
            project = tl.remove_audio(self.project, track_index, clip.id)
            if self._apply("eliminar audio", project):
                new_track = project.audio[track_index]
                if new_track.clips:
                    self.select_audio(track_index, min(clip_index, len(new_track.clips) - 1))
                else:
                    state.selection.set(None)

    def undo(self) -> None:
        self.cancel_drag()
        state.envelope_key.set(None)
        state.keyframe_key.set(None)
        label = self.store.undo()
        if label is None:
            state.toast("info", "Nada que deshacer")
        else:
            state.toast("info", f"Deshacer: {label}")
        self._sync_meta()

    def redo(self) -> None:
        self.cancel_drag()
        state.envelope_key.set(None)
        state.keyframe_key.set(None)
        label = self.store.redo()
        if label is None:
            state.toast("info", "Nada que rehacer")
        else:
            state.toast("info", f"Rehacer: {label}")
        self._sync_meta()

    def select_clip(self, clip_id: str | None) -> None:
        state.multi_selection.set(frozenset())
        state.envelope_key.set(None)
        state.keyframe_key.set(None)
        state.selection.set(("clip", clip_id) if clip_id else None)

    def select_transition(self, index: int) -> None:
        state.multi_selection.set(frozenset())
        state.envelope_key.set(None)
        state.keyframe_key.set(None)
        state.selection.set(("transition", index))

    def select_music(self, index: int = 0, clip_index: int = 0) -> None:
        """Compatibilidad: selecciona un clip de audio (la UI ya no distingue «music»)."""
        self.select_audio(index, clip_index)

    def select_audio(self, track_index: int, clip_index: int) -> None:
        state.multi_selection.set(frozenset())
        state.envelope_key.set(None)
        state.keyframe_key.set(None)
        state.selection.set(("audio", int(track_index), int(clip_index)))

    def select_text(self, text_id: str) -> None:
        state.multi_selection.set(frozenset())
        state.envelope_key.set(None)
        state.keyframe_key.set(None)
        state.selection.set(("text", text_id))

    def select_element(self, element_id: str) -> None:
        state.multi_selection.set(frozenset())
        state.envelope_key.set(None)
        state.keyframe_key.set(None)
        state.selection.set(("element", element_id))

    def focus_text(self, text_id: str) -> None:
        """Selecciona un texto desde Capas; si el playhead queda fuera, salta a su inicio."""
        if not self.project.has_text(text_id):
            return
        self.select_text(text_id)
        overlay = self.project.text(text_id)
        if not (overlay.start <= state.playhead() < overlay.end):
            self.seek(overlay.start)

    def focus_element(self, element_id: str) -> None:
        """Selecciona un elemento desde Capas; si el playhead queda fuera, salta a su inicio."""
        if not self.project.has_element(element_id):
            return
        self.select_element(element_id)
        element = self.project.element(element_id)
        if not (element.start <= state.playhead() < element.end):
            self.seek(element.start)

    def select_all(self) -> None:
        ids = frozenset(c.id for c in self.project.video.clips)
        if not ids:
            return
        state.multi_selection.set(ids)
        state.selection.set(("clip", self.project.video.clips[0].id))

    def clear_selection(self) -> None:
        state.multi_selection.set(frozenset())
        state.envelope_key.set(None)
        state.keyframe_key.set(None)
        state.selection.set(None)

    def toggle_marquee(self) -> None:
        state.marquee_on.set(not state.marquee_on())
        if state.marquee_on():
            state.razor_on.set(False)  # las herramientas son excluyentes
            state.env_edit_on.set(False)
            state.key_add_on.set(False)
        else:
            state.marquee.set(None)

    def toggle_razor(self) -> None:
        """Herramienta razor (C): un clic en un clip lo corta ahí; en la regla, todas las pistas."""
        state.razor_on.set(not state.razor_on())
        if state.razor_on():
            state.marquee_on.set(False)
            state.env_edit_on.set(False)
            state.key_add_on.set(False)
            state.marquee.set(None)

    def toggle_envelope_edit(self) -> None:
        """Modo envolvente (E): un clic en la waveform crea una clave en ese punto (docs/37)."""
        if not state.project_open():
            return
        state.env_edit_on.set(not state.env_edit_on())
        if state.env_edit_on():
            state.razor_on.set(False)
            state.marquee_on.set(False)
            state.key_add_on.set(False)
            state.marquee.set(None)

    def toggle_key_add(self) -> None:
        """Modo «añadir clave» (N): clic en la barra del elemento/texto crea una clave (docs/42)."""
        if not state.project_open():
            return
        state.key_add_on.set(not state.key_add_on())

    def select_tool(self) -> None:
        """V: vuelve a la herramienta normal (ni marquesina, ni razor, ni Env ni añadir clave)."""
        state.razor_on.set(False)
        state.marquee_on.set(False)
        state.env_edit_on.set(False)
        state.key_add_on.set(False)
        state.marquee.set(None)

    def set_speed(self, speed: float) -> None:
        clip = self.selected_clip()
        if clip is None:
            return
        before = self.project
        try:
            project = tl.set_speed(before, clip.id, speed)
            project = tl.sync_linked_audio(project, before)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            self._sync_inspector()
            return
        self._apply("velocidad", project)

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

    # -- efectos globales del proyecto (feature D, docs/26) -------------------------

    def set_global_effect(self, kind: str, label: str | None = None, **params: float) -> None:
        try:
            project = global_effects.set_effect(self.project, kind, **params)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            self._sync_inspector()
            return
        if project != self.project:
            self._apply(label or kind, project)
        else:
            self._sync_inspector()  # p. ej. un valor acotado: el select vuelve al real

    def set_global_grayscale(self, enabled: bool) -> None:
        project = global_effects.set_grayscale(self.project, bool(enabled))
        if project != self.project:
            self._apply("blanco y negro" if enabled else "color original", project)
        else:
            self._sync_inspector()

    def reset_global_effects(self) -> None:
        if not self.project.effects:
            return
        self._apply("restablecer efectos globales", global_effects.clear_effects(self.project))

    # -- lienzo y transformación (fase 2) -----------------------------------------

    def set_project_size(self, width: int, height: int) -> None:
        try:
            project = tl.set_project_size(self.project, width, height)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            self._sync_inspector()
            return
        self._apply("lienzo del proyecto", project)

    def submit_project_size(self, width: str, height: str) -> None:
        try:
            w, h = int(str(width).strip()), int(str(height).strip())
        except ValueError:
            state.toast("warning", "El ancho y el alto del lienzo deben ser números enteros")
            self._sync_inspector()
            return
        self.set_project_size(w, h)

    def set_project_fit(self, fit: str) -> None:
        try:
            project = tl.set_project_fit(self.project, fit)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            self._sync_inspector()
            return
        self._apply("encaje del proyecto", project)

    def set_project_background(self, color: str) -> None:
        try:
            project = tl.set_project_background(self.project, color)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            self._sync_inspector()
            return
        self._apply("fondo del proyecto", project)

    def submit_project_background(self, text: str) -> None:
        value = str(text).strip()
        if not value:
            self._sync_inspector()
            return
        self.set_project_background(value)

    def set_transform(self, **changes) -> None:  # noqa: ANN003
        clip = self.selected_clip()
        if clip is None:
            return
        try:
            project = tl.set_transform(self.project, clip.id, **changes)
        except ProjectError as exc:
            state.toast("warning", f"Transformación inválida: {exc.message}")
            self._sync_inspector()
            return
        self._apply("transformación", project)

    def reset_transform(self) -> None:
        clip = self.selected_clip()
        if clip is None or clip.transform is None:
            return
        self._apply("restablecer transformación", tl.reset_transform(self.project, clip.id))

    def submit_transform_crop(self, side: int, text: str) -> None:
        clip = self.selected_clip()
        if clip is None:
            return
        try:
            value = float(str(text).strip().replace(",", "."))
        except ValueError:
            state.toast("warning", "El recorte debe ser un número entre 0 y 1")
            self._sync_inspector()
            return
        current = clip.transform or Transform()
        crop = list(current.crop)
        crop[int(side)] = value
        self.set_transform(crop=tuple(crop))

    # -- edición directa sobre el canvas del preview (feature A, docs/23) -------------

    def _canvas_stage(self) -> tuple[int, int]:
        area = state.preview_area()
        return ce.fit_stage(self.project.width, self.project.height, area.width, area.height)

    def begin_canvas_drag(self, kind: str, handle: str | None = None) -> None:
        """Empieza a mover (`kind="move"`) o escalar (`"scale"` + `handle`) sobre el preview."""
        if not self._guard_editable():
            return
        clip = self.selected_clip()
        if clip is None or self.player.playing:
            return
        if kind == "scale" and handle not in ce.HANDLES:
            return
        if self.project.video.locked:
            state.toast("warning", "La pista de video está bloqueada")
            return
        stage = self._canvas_stage()
        if stage[0] <= 1 or stage[1] <= 1:
            return  # el preview aún no se ha medido (primer frame)
        self.cancel_drag()
        self._canvas_drag = {
            "kind": "canvas_move" if kind == "move" else "canvas_scale",
            "id": clip.id,
            "project": self.project,
            "stage": stage,
            "handle": handle,
            "dx": 0.0,
            "dy": 0.0,
            "begun": False,
        }

    def drag_canvas(self, dx: float, dy: float) -> None:
        drag = self._canvas_drag
        if drag is None:
            return
        drag["dx"], drag["dy"] = float(dx), float(dy)
        if abs(dx) < _CLICK_SLOP_PX and abs(dy) < _CLICK_SLOP_PX:
            return
        if not drag.get("begun"):
            self.store.begin(
                "mover clip en el lienzo"
                if drag["kind"] == "canvas_move"
                else "redimensionar clip en el lienzo"
            )
            drag["begun"] = True
        base: Project = drag["project"]
        clip = base.clip(drag["id"])
        if drag["kind"] == "canvas_move":
            transform = ce.move_transform(base, clip, drag["stage"], dx, dy)
        else:
            transform = ce.scale_transform(base, clip, drag["stage"], drag["handle"], dx, dy)
        # Se parte siempre del proyecto del `begin`: deltas acumulados, sin deriva por el clamp.
        self.store.update(tl.set_transform(base, clip.id, **ce.as_changes(transform)))

    def end_canvas_drag(self) -> None:
        drag, self._canvas_drag = self._canvas_drag, None
        if drag is None:
            return
        if not drag.get("begun"):
            return  # un clic solo selecciona: no se abrió transacción
        moved = (
            abs(drag.get("dx", 0.0)) >= _CLICK_SLOP_PX
            or abs(drag.get("dy", 0.0)) >= _CLICK_SLOP_PX
        )
        if moved:
            self.store.commit()
        else:
            self.store.cancel()  # un clic sin movimiento no crea undo
        self._sync_meta()

    def _cancel_canvas_drag(self) -> bool:
        if self._canvas_drag is None:
            return False
        drag, self._canvas_drag = self._canvas_drag, None
        if drag.get("begun"):
            self.store.cancel()
            self._sync_meta()
        self.player.seek(state.playhead())
        return True

    def set_track_gain(self, value: float) -> None:
        """Volumen del audio original de todos los clips (0 = silenciado)."""
        value = max(0.0, min(fx.GAIN_MAX, value))
        video = replace(self.project.video, muted=value <= 0, gain=value if value > 0 else self.project.video.gain)
        self._apply("volumen del audio original", replace(self.project, video=video))

    def toggle_video_mute(self) -> None:
        video = self.project.video
        self._apply(
            "silenciar video" if not video.muted else "activar audio del video",
            replace(self.project, video=replace(video, muted=not video.muted)),
        )

    def toggle_video_lock(self) -> None:
        video = self.project.video
        self._apply(
            "bloquear video" if not video.locked else "desbloquear video",
            replace(self.project, video=replace(video, locked=not video.locked)),
        )
        state.toast("info", "Pista de video bloqueada" if not video.locked else "Pista de video desbloqueada")

    def cycle_track_gain(self) -> None:
        video = self.project.video
        current = 0.0 if video.muted else video.gain
        choices = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
        nxt = next((v for v in choices if v > current + 1e-6), choices[0])
        self.set_track_gain(nxt)

    def set_audio_effect(self, kind: str, label: str | None = None, **params: float) -> None:
        """Efecto de un clip de audio (o del audio de un clip de video seleccionado)."""
        audio = self.selected_audio()
        if audio is not None:
            track_index, clip_index, clip = audio
            track = self.project.audio[track_index]
            if track.locked:
                state.toast("warning", f"La pista «{track.name or 'audio'}» está bloqueada")
                return
            clips = list(track.clips)
            clips[clip_index] = fx.with_effect(clip, kind, **params)
            tracks = list(self.project.audio)
            tracks[track_index] = replace(track, clips=tuple(clips))
            self._apply(label or kind, replace(self.project, audio=tuple(tracks)))
            return
        clip = self.selected_clip()
        if clip is not None:
            self.set_effect(kind, label, **params)

    def set_audio_clip_start(self, value: float) -> None:
        audio = self.selected_audio()
        if audio is None:
            return
        track_index, _clip_index, clip = audio
        track = self.project.audio[track_index]
        if track.locked:
            state.toast("warning", f"La pista «{track.name or 'audio'}» está bloqueada")
            return
        if clip.link_id:
            state.toast(
                "warning",
                "El audio está enlazado al video: pulsa «Desvincular audio» para moverlo",
            )
            self._sync_inspector()
            return
        if track.mode != "absolute":
            state.toast("info", "La música es secuencial: muévela arrastrando en el timeline")
            self._sync_inspector()
            return
        try:
            project = tl.move_audio(self.project, track_index, clip.id, start=value)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            self._sync_inspector()
            return
        self._apply("mover audio", project)

    # -- pistas de audio (fase 1) ---------------------------------------------------

    def _selected_audio_track_index(self) -> int | None:
        selection = state.selection()
        if selection is not None and selection[0] == "audio":
            index = int(selection[1])
            return index if 0 <= index < len(self.project.audio) else None
        return None

    def add_audio_track(self) -> None:
        if not self._guard_editable():
            return
        if len(self.project.audio) >= MAX_AUDIO_TRACKS:
            state.toast("warning", f"Máximo de {MAX_AUDIO_TRACKS} pistas de audio")
            return
        project, index = tl.add_audio_track(self.project, f"Audio {len(self.project.audio) + 1}")
        if self._apply("añadir pista de audio", project):
            state.toast("success", f"Pista «{project.audio[index].name}» añadida")

    def remove_selected_audio_track(self) -> None:
        index = self._selected_audio_track_index()
        if index is None:
            state.toast("info", "Selecciona un clip de la pista que quieres quitar")
            return
        self.remove_audio_track(index)

    def remove_audio_track(self, index: int) -> None:
        if not self._guard_editable():
            return
        track = self.project.audio[index]
        if track.locked:
            state.toast("warning", f"La pista «{track.name or 'audio'}» está bloqueada")
            return
        name = track.name or f"Audio {index + 1}"
        project = tl.remove_audio_track(self.project, index)
        if self._apply("quitar pista de audio", project):
            state.selection.set(None)
            state.toast("success", f"Pista «{name}» eliminada")

    def toggle_audio_mute(self, index: int) -> None:
        track = self.project.audio[index]
        self._apply(
            "silenciar pista" if not track.muted else "activar pista",
            tl.set_audio_track(self.project, index, muted=not track.muted),
        )

    def toggle_audio_solo(self, index: int) -> None:
        track = self.project.audio[index]
        self._apply(
            "solo de pista" if not track.solo else "quitar solo",
            tl.set_audio_track(self.project, index, solo=not track.solo),
        )
        if not track.solo:
            state.toast("info", f"Solo en «{track.name or 'audio'}»: el resto se silencia")

    def toggle_audio_lock(self, index: int) -> None:
        track = self.project.audio[index]
        self._apply(
            "bloquear pista" if not track.locked else "desbloquear pista",
            tl.set_audio_track(self.project, index, locked=not track.locked),
        )
        state.toast("info", "Pista bloqueada" if not track.locked else "Pista desbloqueada")

    def cycle_audio_gain(self, index: int) -> None:
        track = self.project.audio[index]
        current = 0.0 if track.muted else track.gain
        choices = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
        nxt = next((v for v in choices if v > current + 1e-6), choices[0])
        self.set_audio_track_gain(index, nxt)

    def set_audio_track_gain(self, index: int, value: float) -> None:
        value = max(0.0, min(fx.GAIN_MAX, float(value)))
        track = self.project.audio[index]
        self._apply(
            "volumen de la pista de audio",
            tl.set_audio_track(
                self.project, index, muted=value <= 0, gain=value if value > 0 else track.gain
            ),
        )

    # -- música (B1) ---------------------------------------------------------------

    def _music_crossfade(self, track: Track) -> float:
        for transition in track.transitions:
            if transition is not None:
                return transition.duration
        return 0.0

    def browse_music(self) -> None:
        """Diálogo de audio (modal, hilo de UI); la importación pesada va a un hilo."""
        from . import file_dialogs

        try:
            chosen = file_dialogs.open_audio(state.recordings_dir(), title="Importar música")
        except Exception as exc:  # diálogo nativo: cualquier fallo debe acabar en un toast
            state.toast("danger", f"No se pudo abrir el diálogo: {exc}")
            return
        if chosen:
            self.import_music(chosen)

    def import_music(self, paths: list[Path]) -> None:
        """Añade archivos de audio a una pista secuencial (la primera de música o una nueva)."""
        if not paths:
            return

        def worker() -> None:
            with self._io_lock:
                project = self.project
                tracks = list(project.audio)
                index = next(
                    (i for i, t in enumerate(tracks) if t.mode == "sequence"), None
                )
                if index is None:
                    if len(tracks) >= MAX_AUDIO_TRACKS:
                        state.toast("warning", f"Máximo de {MAX_AUDIO_TRACKS} pistas de audio")
                        return
                    tracks.append(Track(kind="audio", mode="sequence", name="Música"))
                    index = len(tracks) - 1
                track = tracks[index]
                if track.locked:
                    state.toast("warning", f"La pista «{track.name or 'audio'}» está bloqueada")
                    return
                clips = list(track.clips)
                transitions = list(track.transitions)
                added: list[MediaAsset] = []
                for path in paths:
                    try:
                        asset = media.probe(path)
                    except RecStudioError as exc:
                        state.toast("danger", exc.message)
                        continue
                    if not asset.has_audio:
                        state.toast("warning", f"«{Path(path).name}» no tiene audio")
                        continue
                    project, asset = tl.add_asset(project, asset)
                    if clips:
                        transitions.append(None)
                    clips.append(Clip(id=new_id("m"), asset_id=asset.id, in_=0.0, out_=asset.duration))
                    added.append(asset)
                if not added:
                    return
                track = replace(track, clips=tuple(clips), transitions=tuple(transitions))
                tracks[index] = track
                project = replace(project, audio=tuple(tracks))
                if self._apply("importar música", project):
                    self.select_audio(index, 0)
                    for asset in added:
                        self._queue_waveform(asset)
                    state.toast(
                        "success",
                        f"{len(added)} pista(s) de música añadida(s) · {len(clips)} clip(s) en la pista",
                    )

        threading.Thread(target=worker, daemon=True, name="import-music").start()

    def set_music_gain(self, value: float) -> None:
        index = self._selected_music_index()
        if index is None:
            return
        self.set_audio_track_gain(index, value)

    def set_music_clip_gain(self, value: float) -> None:
        selected = self.selected_audio()
        if selected is None:
            return
        _track_index, _clip_index, clip = selected
        if abs(fx.gain(clip) - float(value)) > 1e-6:
            self.set_audio_effect("volume", "volumen del clip de audio", gain=float(value))

    def set_music_ducking(self, value: float) -> None:
        index = self._selected_music_index()
        if index is None:
            return
        value = max(0.0, min(1.0, float(value)))
        tracks = list(self.project.audio)
        tracks[index] = replace(tracks[index], ducking=value)
        self._apply("ducking de música", replace(self.project, audio=tuple(tracks)))

    def set_music_crossfade(self, seconds: float) -> None:
        index = self._selected_music_index()
        if index is None:
            return
        track = self.project.audio[index]
        if track.mode != "sequence":
            state.toast("info", "El crossfade solo aplica a pistas secuenciales (música)")
            return
        if len(track.clips) < 2:
            return
        limit = min(tl.transition_limit(track, i) for i in range(len(track.clips) - 1))
        duration = min(max(0.0, float(seconds)), limit)
        transition = Transition("fade", duration) if duration >= 0.2 else None
        tracks = list(self.project.audio)
        tracks[index] = replace(track, transitions=tuple(transition for _ in range(len(track.clips) - 1)))
        self._apply("crossfade de música", replace(self.project, audio=tuple(tracks)))

    def remove_music(self) -> None:
        index = self._selected_music_index()
        if index is not None:
            self.remove_audio_track(index)

    def _selected_music_index(self) -> int | None:
        selection = state.selection()
        if not selection or selection[0] != "audio":
            return None
        index = int(selection[1])
        return index if 0 <= index < len(self.project.audio) else None

    # -- textos (B2) ---------------------------------------------------------------

    def add_text(self) -> None:
        if not self.project.video.clips:
            state.toast("info", "Importa un video antes de añadir texto")
            return
        try:
            project, overlay = tl.add_text(
                self.project, "Título", state.playhead(), 3.0,
                x=0.5, y=0.82, size=42, color="#FFFFFF",
            )
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply("añadir texto", project):
            self.select_text(overlay.id)

    def delete_text(self) -> None:
        overlay = self.selected_text()
        if overlay is None:
            return
        if self._apply("eliminar texto", tl.remove_text(self.project, overlay.id)):
            state.selection.set(None)

    def set_text_text(self, value: str) -> None:
        self._update_text(text=value)

    def set_text_start(self, value: str) -> None:
        try:
            start = tl.parse_time(value)
        except ValueError:
            state.toast("warning", "Tiempo inválido: usa mm:ss.mmm")
            self._sync_inspector()
            return
        overlay = self.selected_text()
        if overlay is None:
            return
        total = tl.total_duration(self.project)
        start = max(0.0, min(start, total - 0.1))
        end = max(start + 0.1, overlay.end)
        if end > total:
            end = total
        self._update_text(start=round(start, 4), end=round(end, 4))

    def set_text_end(self, value: str) -> None:
        try:
            end = tl.parse_time(value)
        except ValueError:
            state.toast("warning", "Tiempo inválido: usa mm:ss.mmm")
            self._sync_inspector()
            return
        overlay = self.selected_text()
        if overlay is None:
            return
        end = max(overlay.start + 0.1, min(end, tl.total_duration(self.project)))
        self._update_text(end=round(end, 4))

    def _update_text(self, **changes) -> None:  # noqa: ANN003
        overlay = self.selected_text()
        if overlay is None:
            return
        try:
            project = tl.update_text(self.project, overlay.id, **changes)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            self._sync_inspector()
            return
        self._apply("editar texto", project)
        self._sync_inspector()

    # -- elementos superpuestos (v6, docs/25) ---------------------------------------

    def add_element(self, kind: str) -> None:
        """Añade una figura (rect/ellipse/triangle) en el playhead, encima de las demás."""
        if not self.project.video.clips:
            state.toast("info", "Importa un video antes de añadir elementos")
            return
        width, height, color, label = ELEMENT_DEFAULTS.get(kind, ELEMENT_DEFAULTS["rect"])
        try:
            project, element = element_mod.add_element(
                self.project, kind, state.playhead(), element_mod.ELEMENT_DURATION,
                width=width, height=height, color=color,
            )
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply(f"añadir {label}", project):
            self.select_element(element.id)

    def set_video_track_target(self, track: int) -> None:
        """Cambia la capa donde se insertan los próximos videos (0 = Elementos)."""
        state.video_track_target.set(max(0, min(VIDEO_TRACKS_MAX, int(track))))

    def add_video_element(
        self, asset_id: str, video_track: int | None = None, *, start: float | None = None,
    ) -> None:
        """Añade un video del panel al playhead, en V2/V3 si el destino no es «Elementos»."""
        if not self._guard_editable():
            return
        try:
            asset = self.project.asset(asset_id)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if asset.kind != "video":
            state.toast("warning", "Ese medio no es un video")
            return
        track = state.video_track_target() if video_track is None else int(video_track)
        track = max(0, min(VIDEO_TRACKS_MAX, track))
        kwargs = (
            {"width": 1.0, "height": 1.0, "fit": "fill"} if track > 0 else {}
        )
        at = state.playhead() if start is None else max(0.0, float(start))
        try:
            project, element = element_mod.add_element(
                self.project, "video", at, element_mod.ELEMENT_DURATION,
                asset_id=asset.id, video_track=track, **kwargs,
            )
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        label = element_mod.video_track_label(track)
        if self._apply(f"añadir video a {label}", project):
            self.select_element(element.id)
            self._queue_proxy(asset)
            self._queue_posters([asset])

    def browse_images(self) -> None:
        """Diálogo de imágenes (modal, hilo de UI); el probe y la cache van a un hilo."""
        from . import file_dialogs

        try:
            chosen = file_dialogs.open_images(state.recordings_dir(), title="Importar imagen")
        except Exception as exc:  # diálogo nativo: cualquier fallo debe acabar en un toast
            state.toast("danger", f"No se pudo abrir el diálogo: {exc}")
            return
        if chosen:
            self.import_images(chosen)

    def browse_pip(self, video_track: int | None = None) -> None:
        """Diálogo de video para añadirlo como superposición (Elementos, V2 o V3)."""
        from . import file_dialogs

        track = state.video_track_target() if video_track is None else int(video_track)
        label = element_mod.video_track_label(track)
        try:
            chosen = file_dialogs.open_videos(
                state.recordings_dir(), title=f"Añadir video a {label}",
            )
        except Exception as exc:
            state.toast("danger", f"No se pudo abrir el diálogo: {exc}")
            return
        if chosen:
            self.import_pip(chosen, video_track=track)

    def import_pip(self, paths: list[Path], video_track: int | None = None) -> None:
        """Añade uno o más videos en el playhead, en la pista indicada (0 = Elementos)."""
        if not paths or not self._guard_editable():
            return
        track = state.video_track_target() if video_track is None else int(video_track)
        track = max(0, min(VIDEO_TRACKS_MAX, track))
        label = element_mod.video_track_label(track)
        kwargs = {"width": 1.0, "height": 1.0, "fit": "fill"} if track > 0 else {}

        def worker() -> None:
            with self._io_lock:
                project = self.project
                start = state.playhead()
                added: list[Element] = []
                for path in paths:
                    path = Path(path)
                    try:
                        asset = media.probe(path)
                    except RecStudioError as exc:
                        state.toast("danger", exc.message)
                        continue
                    if asset.kind != "video":
                        state.toast("warning", f"«{path.name}» no es un video")
                        continue
                    project, asset = tl.add_asset(project, asset)
                    try:
                        project, element = element_mod.add_element(
                            project, "video", start, element_mod.ELEMENT_DURATION,
                            asset_id=asset.id, video_track=track, **kwargs,
                        )
                    except ProjectError as exc:
                        state.toast("warning", exc.message)
                        continue
                    added.append(element)
                    self._queue_proxy(asset)
                    self._queue_posters([asset])
                if not added:
                    return
                if self._apply(f"añadir {len(added)} video(s) a {label}", project):
                    self.select_element(added[-1].id)
                    state.toast("success", f"{len(added)} video(s) añadido(s) a {label}")

        threading.Thread(target=worker, daemon=True, name="import-pip").start()

    def browse_pip_track(self, video_track: int) -> None:
        """Botón «+» de la cabecera V2/V3: abre el diálogo para esa pista."""
        self.browse_pip(video_track)

    # -- subtítulos (v9, docs/30) ---------------------------------------------------

    def browse_subtitles(self) -> None:
        """Diálogo de subtítulos (modal, hilo de UI); el parseo va a un hilo."""
        from . import file_dialogs

        try:
            chosen = file_dialogs.open_subtitles(
                state.recordings_dir(), title="Importar subtítulos (SRT/VTT)",
            )
        except Exception as exc:
            state.toast("danger", f"No se pudo abrir el diálogo: {exc}")
            return
        if chosen:
            self.import_subtitles([chosen])

    def import_subtitles(self, paths: list[Path]) -> None:
        """Importa uno o varios SRT/VTT como textos marcados `subtitle=True`."""
        if not paths or not self._guard_editable():
            return

        def worker() -> None:
            with self._io_lock:
                project = self.project
                total = tl.total_duration(project)
                if total <= 0:
                    state.toast("info", "Importa un video antes de los subtítulos")
                    return
                added = []
                for path in paths:
                    path = Path(path)
                    try:
                        raw = path.read_bytes()
                    except OSError as exc:
                        state.toast("danger", f"No se pudo leer «{path.name}»: {exc}")
                        continue
                    data = None
                    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
                        try:
                            data = raw.decode(encoding)
                            break
                        except UnicodeDecodeError:
                            continue
                    if data is None:
                        state.toast("danger", f"«{path.name}» no es un texto legible")
                        continue
                    cues = subtitles_mod.parse_subtitles(data)
                    texts = subtitles_mod.cues_to_texts(cues, total=total)
                    if not texts:
                        state.toast("warning", f"«{path.name}» no tiene subtítulos válidos")
                        continue
                    project = replace(project, texts=(*project.texts, *texts))
                    added.extend(texts)
                if not added:
                    return
                if self._apply(f"importar {len(added)} subtítulo(s)", project):
                    first = added[0]
                    self.select_text(first.id)
                    self.seek(first.start)
                    state.toast("success", f"{len(added)} subtítulo(s) importado(s)")

        threading.Thread(target=worker, daemon=True, name="import-subtitles").start()

    def export_subtitles(self) -> None:
        """Guarda los textos marcados como subtítulo en un SRT."""
        if not self._guard_editable():
            return
        project = self.project
        if not any(text.subtitle for text in project.texts):
            state.toast("info", "No hay subtítulos importados; usa «Subtítulos…» primero")
            return
        from . import file_dialogs

        default_name = f"{project.name or 'subtitulos'}.srt"
        try:
            chosen = file_dialogs.save_subtitles(
                state.recordings_dir(), default_name=default_name, title="Exportar subtítulos",
            )
        except Exception as exc:
            state.toast("danger", f"No se pudo abrir el diálogo: {exc}")
            return
        if chosen is None:
            return
        payload = subtitles_mod.format_srt(project.texts)

        def worker() -> None:
            target = Path(chosen)
            tmp = target.with_name(target.name + ".tmp")
            try:
                tmp.write_text(payload, encoding="utf-8")
                os.replace(tmp, target)
            except OSError as exc:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                state.toast("danger", f"No se pudo guardar el SRT: {exc}")
                return
            state.toast("success", f"Subtítulos exportados a «{target.name}»")

        threading.Thread(target=worker, daemon=True, name="export-subtitles").start()

    def apply_subtitle_style(self) -> None:
        """Copia y/tamaño/color/negrita/opacidad del subtítulo seleccionado a todos los cues."""
        source = self.selected_text()
        if source is None or not source.subtitle:
            state.toast("info", "Selecciona un subtítulo para copiar su estilo")
            return
        texts = tuple(
            replace(
                text, x=0.5, y=source.y, size=source.size, color=source.color,
                bold=source.bold, opacity=source.opacity,
            ) if text.subtitle else text
            for text in self.project.texts
        )
        if texts == self.project.texts:
            return
        if self._apply("aplicar estilo a los subtítulos", replace(self.project, texts=texts)):
            state.toast("success", "Estilo aplicado a todos los subtítulos")

    # -- keyframes de texto/elemento (v9, docs/31) ----------------------------------

    @staticmethod
    def _key_label(channel: str) -> str:
        return {
            "x": "X", "y": "Y", "opacity": "opacidad",
            "scale": "escala", "rotation": "rotación", "size": "tamaño",
        }.get(channel, channel)

    def _key_time(self, item) -> float | None:  # noqa: ANN001
        duration = float(getattr(item, "duration", item.end - item.start))
        local = state.playhead() - float(item.start)
        if local < -1e-6 or local > duration + 1e-6:
            state.toast("warning", "El playhead está fuera del elemento; colócalo dentro")
            return None
        return round(max(0.0, min(duration, local)), 4)

    def add_element_keyframe(self, channel: str) -> None:
        element = self.selected_element()
        if element is None or channel not in anim.ELEMENT_CHANNELS:
            return
        local = self._key_time(element)
        if local is None:
            return
        if channel == "scale":
            value = float(state.insp_el_scale())
        elif channel == "rotation":
            value = float(state.insp_el_rotation())
        else:
            value = float(getattr(element, channel))
        animations = anim.set_keyframe(
            element.animations, channel, local, value, easing=state.insp_easing(),
        )
        try:
            project = element_mod.update_element(self.project, element.id, animations=animations)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        label = self._key_label(channel)
        if self._apply(f"añadir clave {label}", project):
            self._sync_inspector()
            state.toast("success", f"Clave {label} en {tl.format_time(state.playhead())}")

    def clear_element_animation(self, channel: str) -> None:
        element = self.selected_element()
        if element is None:
            return
        animations = anim.clear_channel(element.animations, channel)
        if animations == element.animations:
            return
        try:
            project = element_mod.update_element(self.project, element.id, animations=animations)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply(f"quitar animación {self._key_label(channel)}", project):
            self._sync_inspector()

    def add_text_keyframe(self, channel: str) -> None:
        overlay = self.selected_text()
        if overlay is None or channel not in anim.TEXT_CHANNELS:
            return
        local = self._key_time(overlay)
        if local is None:
            return
        if channel == "scale":
            value = float(state.insp_text_scale())
        elif channel == "rotation":
            value = float(state.insp_text_rotation())
        elif channel == "size":
            value = float(state.insp_text_size())
        else:
            value = float(getattr(overlay, channel))
        animations = anim.set_keyframe(
            overlay.animations, channel, local, value, easing=state.insp_easing(),
        )
        try:
            project = tl.update_text(self.project, overlay.id, animations=animations)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        label = self._key_label(channel)
        if self._apply(f"añadir clave {label}", project):
            self._sync_inspector()
            state.toast("success", f"Clave {label} en {tl.format_time(state.playhead())}")

    def clear_text_animation(self, channel: str) -> None:
        overlay = self.selected_text()
        if overlay is None:
            return
        animations = anim.clear_channel(overlay.animations, channel)
        if animations == overlay.animations:
            return
        try:
            project = tl.update_text(self.project, overlay.id, animations=animations)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply(f"quitar animación {self._key_label(channel)}", project):
            self._sync_inspector()

    def add_text_color_keyframe(self) -> None:
        """Graba una clave de color en el playhead local con el color actual del inspector."""
        overlay = self.selected_text()
        if overlay is None:
            return
        local = self._key_time(overlay)
        if local is None:
            return
        color = str(state.insp_text_color())
        try:
            keys = text_color_mod.set_keyframe(overlay.color_keys, local, color)
            project = tl.update_text(self.project, overlay.id, color_keys=keys)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply("añadir clave color", project):
            self._sync_inspector()
            state.toast("success", f"Clave color en {tl.format_time(state.playhead())}")

    def clear_text_color_animation(self) -> None:
        overlay = self.selected_text()
        if overlay is None or not overlay.color_keys:
            return
        try:
            project = tl.update_text(self.project, overlay.id, color_keys=())
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply("quitar animación color", project):
            self._sync_inspector()

    # -- envolventes de volumen (v10, docs/32) -------------------------------------

    def _envelope_target(self):
        """`(clip, inicio, pista, índice de pista, índice de clip)` del clip seleccionado.

        `índice de pista` es `None` para la pista de video y el índice real para una pista de
        audio. El inicio es el del clip en el timeline (no el `in_` de la fuente).
        """
        audio = self.selected_audio()
        if audio is not None:
            track_index, clip_index, clip = audio
            track = self.project.audio[track_index]
            return clip, tl.clip_starts(track)[clip_index], track, track_index, clip_index
        clip = self.selected_clip()
        if clip is not None:
            index = self.project.video.index_of(clip.id)
            return clip, tl.clip_starts(self.project.video)[index], self.project.video, None, index
        return None

    def _replace_envelope(self, target, points, label: str) -> bool:  # noqa: ANN001
        clip, _start, track, track_index, clip_index = target
        clips = list(track.clips)
        clips[clip_index] = replace(clip, envelope=points)
        if track_index is None:
            project = replace(self.project, video=replace(self.project.video, clips=tuple(clips)))
        else:
            tracks = list(self.project.audio)
            tracks[track_index] = replace(track, clips=tuple(clips))
            project = replace(self.project, audio=tuple(tracks))
        if not self._apply(label, project):
            return False
        self._sync_inspector()
        return True

    def add_audio_keyframe(self) -> None:
        """Graba el volumen estático del clip en el playhead (tiempo local al clip)."""
        target = self._envelope_target()
        if target is None:
            return
        clip, start, _track, _track_index, _clip_index = target
        local = state.playhead() - start
        if local < -1e-6 or local > clip.duration + 1e-6:
            state.toast("warning", "El playhead está fuera del clip; colócalo dentro")
            return
        local = round(max(0.0, min(clip.duration, local)), 4)
        points = env.set_point(clip.envelope, local, fx.gain(clip), state.insp_env_easing())
        if points == clip.envelope:
            return
        if self._replace_envelope(target, points, "añadir clave de volumen"):
            state.toast("success", f"Clave de volumen en {tl.format_time(state.playhead())}")

    def clear_audio_envelope(self) -> None:
        target = self._envelope_target()
        if target is None or not target[0].envelope:
            return
        if self._replace_envelope(target, (), "quitar envolvente de volumen"):
            state.toast("info", "Envolvente de volumen eliminada")

    def add_element_audio_keyframe(self) -> None:
        """Graba el volumen actual del elemento en el playhead (tiempo local al elemento)."""
        element = self.selected_element()
        if element is None or element.kind != "video":
            return
        local = self._key_time(element)
        if local is None:
            return
        gain = max(0.0, min(element_mod.AUDIO_GAIN_MAX, float(state.insp_el_gain())))
        points = env.set_point(element.envelope, local, gain, state.insp_env_easing())
        if points == element.envelope:
            return
        try:
            project = element_mod.update_element(self.project, element.id, envelope=points)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply("añadir clave de volumen del elemento", project):
            self._sync_inspector()
            state.toast("success", f"Clave de volumen en {tl.format_time(state.playhead())}")

    def clear_element_audio_envelope(self) -> None:
        element = self.selected_element()
        if element is None or not element.envelope:
            return
        try:
            project = element_mod.update_element(self.project, element.id, envelope=())
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply("quitar envolvente de volumen del elemento", project):
            self._sync_inspector()
            state.toast("info", "Envolvente de volumen eliminada")

    # -- asas de la envolvente en el timeline (docs/37) ----------------------------

    def _envelope_ref(self, scope: str, item_id: str, track_index: int = -1):
        """`(item, inicio, pista, índice de pista, índice de clip)` de una envolvente.

        `scope` es `"video"` (V1), `"audio"` (pista extra) o `"element"` (video superpuesto).
        """
        project = self.project
        if scope == "video":
            if not project.has_clip(item_id):
                return None
            index = project.video.index_of(item_id)
            return (
                project.video.clips[index],
                tl.clip_starts(project.video)[index],
                project.video,
                None,
                index,
            )
        if scope == "audio":
            track_index = int(track_index)
            if not 0 <= track_index < len(project.audio):
                return None
            track = project.audio[track_index]
            try:
                index = track.index_of(item_id)
            except ProjectError:
                return None
            return track.clips[index], tl.clip_starts(track)[index], track, track_index, index
        if scope == "element":
            if not project.has_element(item_id):
                return None
            item = project.element(item_id)
            if item.kind != "video":
                return None
            return item, float(item.start), None, None, -1
        return None

    def _project_with_envelope(
        self, project: Project, scope: str, item_id: str, track_index: int,
        points,  # noqa: ANN001
    ) -> Project | None:
        """Copia `project` con la envolvente de `item_id` reemplazada (sin tocar el store)."""
        try:
            if scope == "element":
                return element_mod.update_element(project, item_id, envelope=points)
            if scope == "video":
                index = project.video.index_of(item_id)
                clips = list(project.video.clips)
                clips[index] = replace(clips[index], envelope=points)
                return replace(project, video=replace(project.video, clips=tuple(clips)))
            track_index = int(track_index)
            track = project.audio[track_index]
            index = track.index_of(item_id)
            clips = list(track.clips)
            clips[index] = replace(clips[index], envelope=points)
            tracks = list(project.audio)
            tracks[track_index] = replace(track, clips=tuple(clips))
            return replace(project, audio=tuple(tracks))
        except (ProjectError, IndexError):
            return None

    def _envelope_locked(self, scope: str, ref) -> bool:  # noqa: ANN001
        if scope == "video" and self.project.video.locked:
            state.toast("warning", "La pista de video está bloqueada")
            return True
        if scope == "audio":
            track_index = ref[3]
            if track_index is not None and self.project.audio[int(track_index)].locked:
                state.toast("warning", "La pista de audio está bloqueada")
                return True
        return False

    def begin_envelope_key_drag(
        self, scope: str, item_id: str, track_index: int = -1, index: int = 0,
        row_height: float = 0.0,
    ) -> None:
        """Empieza a arrastrar una clave existente; la transacción se abre al primer movimiento."""
        if not self._guard_editable() or self.player.playing:
            return
        self.cancel_drag()
        ref = self._envelope_ref(scope, item_id, track_index)
        if ref is None or self._envelope_locked(scope, ref):
            return
        state.keyframe_key.set(None)
        item, start, _track, _ti, _ci = ref
        points = item.envelope
        if not 0 <= int(index) < len(points):
            return
        duration = float(item.duration) if scope != "element" else float(item.end - item.start)
        point = points[int(index)]
        self._envelope_drag = {
            "scope": scope, "id": item_id, "track": int(track_index), "index": int(index),
            "project": self.project, "points": points, "start": start, "duration": duration,
            "t": point.t, "gain": point.gain, "current_t": point.t,
            "inner_h": max(1.0, float(row_height) - 4.0),
            "dx": 0.0, "dy": 0.0, "begun": False, "add": False,
        }

    def begin_envelope_add(
        self, scope: str, item_id: str, track_index: int, window_x: float, row_height: float,
    ) -> None:
        """Crea una clave en el punto pulsado (modo «Env») y la deja lista para arrastrar."""
        if not self._guard_editable() or self.player.playing or not state.env_edit_on():
            return
        self.cancel_drag()
        ref = self._envelope_ref(scope, item_id, track_index)
        if ref is None or self._envelope_locked(scope, ref):
            return
        item, start, _track, _ti, _ci = ref
        duration = float(item.duration) if scope != "element" else float(item.end - item.start)
        if duration <= 0:
            return
        local = max(
            0.0,
            min(duration, self.content_x(window_x) / max(1.0, state.tl_zoom()) - start),
        )
        base = self.project
        points = item.envelope
        default = (
            element_mod.audio_gain(item) if scope == "element" else fx.gain(item)
        )
        gain = env.value_at(points, local, default=default) if points else default
        updated = env.set_point(points, local, gain, state.insp_env_easing())
        if updated == points:  # ya había una clave en ese instante: se selecciona y se arrastra
            found = min(range(len(points)), key=lambda i: abs(points[i].t - round(local, 4)))
            self.begin_envelope_key_drag(scope, item_id, track_index, found, row_height)
            return
        project = self._project_with_envelope(base, scope, item_id, track_index, updated)
        if project is None:
            return
        index = min(range(len(updated)), key=lambda i: abs(updated[i].t - round(local, 4)))
        self.store.begin("añadir clave de volumen")
        self.store.update(project)
        point = updated[index]
        self._envelope_drag = {
            "scope": scope, "id": item_id, "track": int(track_index), "index": index,
            "project": base, "points": updated, "start": start, "duration": duration,
            "t": point.t, "gain": point.gain, "current_t": point.t,
            "inner_h": max(1.0, float(row_height) - 4.0),
            "dx": 0.0, "dy": 0.0, "begun": True, "add": True,
        }

    def drag_envelope_key(self, dx: float, dy: float) -> None:
        drag = self._envelope_drag
        if drag is None:
            return
        drag["dx"], drag["dy"] = float(dx), float(dy)
        if (
            not drag.get("add")
            and abs(dx) < _CLICK_SLOP_PX and abs(dy) < _CLICK_SLOP_PX
        ):
            return
        if not drag.get("begun"):
            self.store.begin("mover clave de volumen")
            drag["begun"] = True
        points = drag["points"]
        index = int(drag["index"])
        duration = float(drag["duration"])
        zoom = max(1.0, state.tl_zoom())
        t = max(0.0, min(duration, float(drag["t"]) + float(dx) / zoom))
        # No cruzar la clave vecina: el modelo exige tiempos estrictamente crecientes.
        low = points[index - 1].t + 2e-4 if index > 0 else 0.0
        high = points[index + 1].t - 2e-4 if index + 1 < len(points) else duration
        t = max(low, min(high, t)) if low <= high else float(drag["t"])
        gain = max(
            0.0, min(env.GAIN_MAX, float(drag["gain"]) - float(dy) / drag["inner_h"] * env.GAIN_MAX)
        )
        point = env.VolumePoint(round(t, 4), round(gain, 4), points[index].easing)
        updated = (*points[:index], point, *points[index + 1:])
        project = self._project_with_envelope(
            drag["project"], drag["scope"], drag["id"], drag["track"], updated
        )
        if project is None:
            return
        drag["current_t"] = point.t
        drag["current_gain"] = point.gain
        self.store.update(project)

    def end_envelope_key_drag(self) -> None:
        drag, self._envelope_drag = self._envelope_drag, None
        if drag is None:
            return
        scope, item_id, track_index = drag["scope"], drag["id"], drag["track"]
        if not drag.get("begun"):
            state.envelope_key.set(state.EnvelopeKey(scope, item_id, drag["t"], track_index))
            return
        self.store.commit()
        state.envelope_key.set(state.EnvelopeKey(
            scope, item_id, float(drag.get("current_t", drag["t"])), track_index
        ))
        self._sync_inspector()
        self._sync_meta()

    def _cancel_envelope_drag(self) -> bool:
        if self._envelope_drag is None:
            return False
        drag, self._envelope_drag = self._envelope_drag, None
        if drag.get("begun"):
            self.store.cancel()
            self._sync_meta()
        return True

    def select_envelope_key(
        self, scope: str, item_id: str, track_index: int, t: float,
    ) -> None:
        """Selecciona una clave (clic sin arrastre en su asa) para poder borrarla con Supr."""
        ref = self._envelope_ref(scope, item_id, track_index)
        if ref is None:
            return
        points = ref[0].envelope
        if not points:
            return
        point = min(points, key=lambda p: abs(p.t - float(t)))
        if abs(point.t - float(t)) > 1e-3:
            return
        state.keyframe_key.set(None)
        state.envelope_key.set(state.EnvelopeKey(scope, item_id, point.t, int(track_index)))

    def delete_envelope_key_at(
        self, scope: str, item_id: str, track_index: int, t: float,
    ) -> bool:
        """Borra la clave más cercana a `t` (menú contextual del asa y Supr)."""
        if not self._guard_editable():
            return False
        ref = self._envelope_ref(scope, item_id, track_index)
        if ref is None or self._envelope_locked(scope, ref):
            return False
        item = ref[0]
        points = item.envelope
        if not points:
            return False
        index = min(range(len(points)), key=lambda i: abs(points[i].t - float(t)))
        if abs(points[index].t - float(t)) > 1e-3:
            return False
        updated = (*points[:index], *points[index + 1:])
        project = self._project_with_envelope(
            self.project, scope, item_id, track_index, updated
        )
        if project is None:
            return False
        if not self._apply("eliminar clave de volumen", project):
            return False
        state.envelope_key.set(None)
        self._sync_inspector()
        return True

    def delete_envelope_key(self) -> bool:
        """Supr con una clave de envolvente seleccionada en el timeline."""
        key = state.envelope_key()
        if key is None:
            return False
        return self.delete_envelope_key_at(key.scope, key.item, key.track, key.t)

    # -- asas de animaciones visuales en el timeline (docs/38) ----------------------

    def _keyframe_ref(self, scope: str, item_id: str, channel: str):
        """`(item, inicio, duración, animación)` de una clave de `Animation`."""
        project = self.project
        if scope == "element":
            if not project.has_element(item_id):
                return None
            item = project.element(item_id)
            if channel not in anim.ELEMENT_CHANNELS:
                return None
        elif scope == "text":
            if not project.has_text(item_id):
                return None
            item = project.text(item_id)
            if channel not in anim.TEXT_CHANNELS:
                return None
        else:
            return None
        return (
            item, float(item.start), float(item.end - item.start),
            anim.find(item.animations, channel),
        )

    @staticmethod
    def _animations_with_points(animations, channel: str, points):  # noqa: ANN001, ANN205
        """Sustituye (o quita, si queda vacío) las claves de `channel` en una tupla."""
        if not points:
            return anim.clear_channel(animations, channel)
        updated = anim.Animation(channel, tuple(points))
        return tuple(updated if animation.channel == channel else animation
                     for animation in animations)

    def _project_with_animations(
        self, project: Project, scope: str, item_id: str, animations,
    ) -> Project | None:  # noqa: ANN001
        """Copia `project` con las animaciones de `item_id` reemplazadas (sin tocar el store)."""
        try:
            if scope == "element":
                return element_mod.update_element(project, item_id, animations=animations)
            if scope == "text":
                return tl.update_text(project, item_id, animations=animations)
        except (ProjectError, IndexError):
            return None
        return None

    def begin_keyframe_key_drag(
        self, scope: str, item_id: str, channel: str, index: int = 0,
        row_height: float = 0.0,
    ) -> None:
        """Empieza a arrastrar una clave de animación; la transacción se abre al moverse."""
        if not self._guard_editable() or self.player.playing:
            return
        self.cancel_drag()
        ref = self._keyframe_ref(scope, item_id, channel)
        if ref is None:
            return
        state.envelope_key.set(None)
        item, start, duration, animation = ref
        if animation is None or not 0 <= int(index) < len(animation.points):
            return
        point = animation.points[int(index)]
        low, high = anim.channel_range(channel)
        self._keyframe_drag = {
            "scope": scope, "id": item_id, "channel": channel, "index": int(index),
            "project": self.project, "points": animation.points, "start": start,
            "duration": duration, "t": point.t, "value": point.value,
            "easing": point.easing,
            "current_t": point.t, "current_value": point.value,
            "animations": item.animations,
            "inner_h": max(1.0, float(row_height) - 4.0),
            "low": low, "high": high,
            "dx": 0.0, "dy": 0.0, "begun": False,
        }

    def begin_keyframe_add(
        self, scope: str, item_id: str, window_x: float, row_height: float = 0.0,
    ) -> None:
        """Modo «añadir clave» (N): crea o actualiza una clave en el instante pulsado (docs/42).

        Solo actúa sobre la capa seleccionada y el canal activo. El valor es el interpolado en
        ese instante (o la base de la capa si aún no hay animación) y el easing sale de
        `state.insp_easing`. Si ya había una clave en ese instante se reemplaza. La clave queda
        seleccionada y el mismo gesto puede arrastrarla; un clic sin movimiento deja **una**
        entrada de undo (`añadir clave X`) y no mueve la capa.
        """
        if not self._guard_editable() or self.player.playing or not state.key_add_on():
            return
        selection = state.selection()
        if selection is None or selection[0] != scope or selection[1] != item_id:
            state.toast("warning", "Selecciona un elemento o texto para añadir claves")
            return
        self.cancel_drag()
        channel = state.keyframe_channel()
        ref = self._keyframe_ref(scope, item_id, channel)
        if ref is None:
            state.toast("warning", "Ese canal no se puede animar en esta capa")
            return
        item, start, duration, _animation = ref
        if duration <= 0:
            return
        from .timeline import keyframe_time, keyframe_value_at

        local = round(
            keyframe_time(start, duration, self.content_x(window_x), max(1.0, state.tl_zoom())),
            4,
        )
        value = keyframe_value_at(item, channel, local)
        try:
            animations = anim.set_keyframe(
                item.animations, channel, local, value, easing=state.insp_easing(),
            )
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        updated = anim.find(animations, channel)
        if updated is None:  # no debería ocurrir: `set_keyframe` siempre deja una animación
            return
        index = min(range(len(updated.points)), key=lambda i: abs(updated.points[i].t - local))
        point = updated.points[index]
        label = self._key_label(channel)
        if animations == item.animations:
            # Mismo instante, valor y easing: solo selecciona (sin undo), como las asas.
            self.begin_keyframe_key_drag(scope, item_id, channel, index, row_height)
            state.keyframe_key.set(state.KeyframeKey(scope, item_id, channel, point.t))
            return
        project = self._project_with_animations(self.project, scope, item_id, animations)
        if project is None:
            return
        low, high = anim.channel_range(channel)
        base = self.project
        self.store.begin(f"añadir clave {label}")
        self.store.update(project)
        self._keyframe_drag = {
            "scope": scope, "id": item_id, "channel": channel, "index": index,
            "project": base, "points": updated.points, "start": start, "duration": duration,
            "t": point.t, "value": point.value, "easing": point.easing,
            "current_t": point.t, "current_value": point.value,
            "animations": item.animations,
            "inner_h": max(1.0, float(row_height) - 4.0),
            "low": low, "high": high,
            "dx": 0.0, "dy": 0.0, "begun": True, "add": True,
        }
        state.keyframe_key.set(state.KeyframeKey(scope, item_id, channel, point.t))

    def drag_keyframe_key(self, dx: float, dy: float) -> None:
        drag = self._keyframe_drag
        if drag is None:
            return
        drag["dx"], drag["dy"] = float(dx), float(dy)
        if abs(dx) < _CLICK_SLOP_PX and abs(dy) < _CLICK_SLOP_PX:
            return
        if not drag.get("begun"):
            self.store.begin(f"mover clave {self._key_label(drag['channel'])}")
            drag["begun"] = True
        points = drag["points"]
        index = int(drag["index"])
        duration = float(drag["duration"])
        zoom = max(1.0, state.tl_zoom())
        t = max(0.0, min(duration, float(drag["t"]) + float(dx) / zoom))
        # No cruzar la clave vecina: el modelo exige tiempos estrictamente crecientes.
        low = points[index - 1].t + 2e-4 if index > 0 else 0.0
        high = points[index + 1].t - 2e-4 if index + 1 < len(points) else duration
        t = max(low, min(high, t)) if low <= high else float(drag["t"])
        span = float(drag["high"]) - float(drag["low"])
        value = float(drag["value"]) - float(dy) / drag["inner_h"] * span
        value = max(float(drag["low"]), min(float(drag["high"]), value))
        point = anim.Keyframe(
            round(t, 4), round(value, 4), drag.get("easing", "linear"),
        )
        updated = (*points[:index], point, *points[index + 1:])
        animations = self._animations_with_points(
            drag["animations"], drag["channel"], updated,
        )
        project = self._project_with_animations(
            drag["project"], drag["scope"], drag["id"], animations,
        )
        if project is None:
            return
        drag["current_t"] = point.t
        drag["current_value"] = point.value
        self.store.update(project)

    def end_keyframe_key_drag(self) -> None:
        drag, self._keyframe_drag = self._keyframe_drag, None
        if drag is None:
            return
        scope, item_id, channel = drag["scope"], drag["id"], drag["channel"]
        if not drag.get("begun"):
            state.keyframe_key.set(state.KeyframeKey(scope, item_id, channel, drag["t"]))
            return
        self.store.commit()
        state.keyframe_key.set(state.KeyframeKey(
            scope, item_id, channel, float(drag.get("current_t", drag["t"])),
        ))
        self._sync_inspector()
        self._sync_meta()

    def _cancel_keyframe_drag(self) -> bool:
        if self._keyframe_drag is None:
            return False
        drag, self._keyframe_drag = self._keyframe_drag, None
        if drag.get("begun"):
            self.store.cancel()
            self._sync_meta()
        if drag.get("add"):
            # La clave recién creada se ha revertido: su selección ya no existe.
            state.keyframe_key.set(None)
        return True

    def select_keyframe_key(
        self, scope: str, item_id: str, channel: str, t: float,
    ) -> None:
        """Selecciona una clave (clic sin arrastre en su asa) para poder borrarla con Supr."""
        ref = self._keyframe_ref(scope, item_id, channel)
        if ref is None or ref[3] is None:
            return
        points = ref[3].points
        point = min(points, key=lambda p: abs(p.t - float(t)))
        if abs(point.t - float(t)) > 1e-3:
            return
        state.envelope_key.set(None)
        state.keyframe_key.set(state.KeyframeKey(scope, item_id, channel, point.t))

    def delete_keyframe_key_at(
        self, scope: str, item_id: str, channel: str, t: float,
    ) -> bool:
        """Borra la clave más cercana a `t` (menú contextual del asa y Supr)."""
        if not self._guard_editable():
            return False
        ref = self._keyframe_ref(scope, item_id, channel)
        if ref is None or ref[3] is None:
            return False
        item = ref[0]
        points = ref[3].points
        index = min(range(len(points)), key=lambda i: abs(points[i].t - float(t)))
        if abs(points[index].t - float(t)) > 1e-3:
            return False
        updated = (*points[:index], *points[index + 1:])
        animations = self._animations_with_points(item.animations, channel, updated)
        project = self._project_with_animations(self.project, scope, item_id, animations)
        if project is None:
            return False
        if not self._apply(f"eliminar clave {self._key_label(channel)}", project):
            return False
        state.keyframe_key.set(None)
        self._sync_inspector()
        return True

    def delete_keyframe_key(self) -> bool:
        """Supr con una clave de animación seleccionada en el timeline."""
        key = state.keyframe_key()
        if key is None:
            return False
        return self.delete_keyframe_key_at(key.scope, key.item, key.channel, key.t)

    # -- portapapeles de claves (docs/41) -------------------------------------------

    def _set_key_clipboard(self, clipboard: state.KeyClipboard) -> None:
        """El portapapeles de claves y el de clips son excluyentes: gana el último copiado."""
        self._key_clipboard = clipboard
        self._clipboard = None
        state.clipboard_count.set(0)

    def _copy_visual_key_at(
        self, scope: str, item_id: str, channel: str, t: float,
    ) -> bool:
        ref = self._keyframe_ref(scope, item_id, channel)
        if ref is None or ref[3] is None:
            return False
        point = min(ref[3].points, key=lambda item: abs(item.t - float(t)))
        if abs(point.t - float(t)) > 1e-3:
            return False
        label = self._key_label(channel)
        self._set_key_clipboard(state.KeyClipboard(
            kind="visual", channel=channel, value=point.value, easing=point.easing,
            source_label=f"{label} · {point.t:.2f} s",
        ))
        state.envelope_key.set(None)
        state.keyframe_key.set(state.KeyframeKey(scope, item_id, channel, point.t))
        state.toast("info", f"Clave {label} copiada")
        return True

    def _copy_envelope_key_at(
        self, scope: str, item_id: str, track_index: int, t: float,
    ) -> bool:
        ref = self._envelope_ref(scope, item_id, track_index)
        if ref is None:
            return False
        points = ref[0].envelope
        if not points:
            return False
        point = min(points, key=lambda item: abs(item.t - float(t)))
        if abs(point.t - float(t)) > 1e-3:
            return False
        self._set_key_clipboard(state.KeyClipboard(
            kind="audio", channel="", value=point.gain, easing="linear",
            source_label=f"volumen · {point.t:.2f} s",
        ))
        state.keyframe_key.set(None)
        state.envelope_key.set(state.EnvelopeKey(scope, item_id, point.t, int(track_index)))
        state.toast("info", "Punto de volumen copiado")
        return True

    def copy_selected_key(self) -> bool:
        """Ctrl+C con una clave visual o un punto de envolvente seleccionado."""
        key = state.keyframe_key()
        if key is not None:
            if self._copy_visual_key_at(key.scope, key.item, key.channel, key.t):
                return True
            state.keyframe_key.set(None)
        point = state.envelope_key()
        if point is not None:
            if self._copy_envelope_key_at(point.scope, point.item, point.track, point.t):
                return True
            state.envelope_key.set(None)
        return False

    def copy_keyframe_key_at(
        self, scope: str, item_id: str, channel: str, t: float,
    ) -> bool:
        """«Copiar clave» del menú contextual de un asa de animación."""
        return self._copy_visual_key_at(scope, item_id, channel, t)

    def copy_envelope_key_at(
        self, scope: str, item_id: str, track_index: int, t: float,
    ) -> bool:
        """«Copiar clave» del menú contextual de un asa de volumen."""
        return self._copy_envelope_key_at(scope, item_id, track_index, t)

    def paste_key(self) -> bool:
        """Ctrl+V con una clave copiada: pega en la capa/canal activo, en el playhead."""
        clipboard = self._key_clipboard
        if clipboard is None:
            state.toast("info", "No hay ninguna clave copiada (Ctrl+C sobre una clave)")
            return False
        if not self._guard_editable():
            return False
        if clipboard.kind == "visual":
            return self._paste_visual_key(clipboard)
        if clipboard.kind == "audio":
            return self._paste_audio_key(clipboard)
        return False

    def _paste_visual_key(self, clipboard: state.KeyClipboard) -> bool:
        selection = state.selection()
        if selection is None or selection[0] not in ("element", "text"):
            state.toast("warning", "Selecciona el elemento o texto donde pegar la clave")
            return False
        scope, item_id = selection[0], selection[1]
        channel = state.keyframe_channel()
        ref = self._keyframe_ref(scope, item_id, channel)
        label = self._key_label(channel)
        if ref is None:
            state.toast("warning", f"El canal {label} no es válido para esta capa")
            return False
        item, start, duration, _animation = ref
        local = state.playhead() - float(start)
        if local < -1e-6 or local > duration + 1e-6:
            state.toast("warning", "El playhead está fuera del elemento; colócalo dentro")
            return False
        local = round(max(0.0, min(duration, local)), 4)
        try:
            anim.validate_value(channel, clipboard.value)
        except ProjectError as exc:
            state.toast("warning", f"No se puede pegar la clave {label}: {exc.message}")
            return False
        animations = anim.set_keyframe(
            item.animations, channel, local, clipboard.value, easing=clipboard.easing,
        )
        project = self._project_with_animations(self.project, scope, item_id, animations)
        if project is None:
            return False
        applied = self._apply(f"pegar clave {label}", project)
        state.keyframe_key.set(state.KeyframeKey(scope, item_id, channel, local))
        state.envelope_key.set(None)
        self._sync_inspector()
        if applied:
            state.toast("success", f"Clave {label} pegada en {tl.format_time(state.playhead())}")
        else:
            state.toast("info", f"La clave {label} ya tenía ese valor en el playhead")
        return applied

    def _paste_audio_key(self, clipboard: state.KeyClipboard) -> bool:
        selection = state.selection()
        if selection is None:
            state.toast("warning", "Selecciona un clip o un video superpuesto para pegar el punto")
            return False
        if selection[0] == "element":
            element = self.selected_element()
            if element is None or element.kind != "video":
                state.toast("warning", "El elemento seleccionado no tiene audio")
                return False
            scope, item_id, track_index = "element", element.id, -1
        elif selection[0] == "audio":
            audio = self.selected_audio()
            if audio is None:
                state.toast("warning", "Selecciona un clip con audio para pegar el punto")
                return False
            track_index, _clip_index, clip = audio
            scope, item_id = "audio", clip.id
        elif selection[0] == "clip":
            scope, item_id, track_index = "video", selection[1], -1
        else:
            state.toast("warning", "Selecciona un clip o un video superpuesto para pegar el punto")
            return False
        ref = self._envelope_ref(scope, item_id, track_index)
        if ref is None:
            state.toast("warning", "Selecciona un clip o un video superpuesto para pegar el punto")
            return False
        if self._envelope_locked(scope, ref):
            return False
        item, start, _track, _track_index, _clip_index = ref
        duration = float(item.duration) if scope != "element" else float(item.end - item.start)
        local = state.playhead() - float(start)
        if local < -1e-6 or local > duration + 1e-6:
            state.toast("warning", "El playhead está fuera del clip; colócalo dentro")
            return False
        local = round(max(0.0, min(duration, local)), 4)
        points = env.set_point(item.envelope, local, clipboard.value)
        project = self._project_with_envelope(
            self.project, scope, item_id, track_index, points,
        )
        if project is None:
            return False
        applied = self._apply("pegar punto de volumen", project)
        state.envelope_key.set(state.EnvelopeKey(scope, item_id, local, int(track_index)))
        state.keyframe_key.set(None)
        self._sync_inspector()
        if applied:
            state.toast("success", f"Punto de volumen pegado en {tl.format_time(state.playhead())}")
        else:
            state.toast("info", "El punto de volumen ya tenía ese valor en el playhead")
        return applied

    def import_images(self, paths: list[Path]) -> None:
        """Añade imágenes al proyecto como assets y las coloca como elementos (sin clip)."""
        if not paths:
            return

        def worker() -> None:
            with self._io_lock:
                project = self.project
                if not project.video.clips:
                    state.toast("info", "Importa un video antes de añadir imágenes")
                    return
                start = state.playhead()
                added: list[str] = []
                for path in paths:
                    try:
                        asset = media.probe_image(path)
                    except RecStudioError as exc:
                        state.toast("danger", exc.message)
                        continue
                    project, asset = tl.add_asset(project, asset)
                    width, height = element_mod.default_image_box(project, asset)
                    try:
                        project, element = element_mod.add_element(
                            project, "image", start, element_mod.ELEMENT_DURATION,
                            width=width, height=height, asset_id=asset.id,
                        )
                    except ProjectError as exc:
                        state.toast("warning", exc.message)
                        continue
                    added.append(element.id)
                if not added:
                    return
                if self._apply(
                    f"añadir {len(added)} imagen{'es' if len(added) != 1 else ''}", project
                ):
                    self.select_element(added[-1])
                    state.toast("success", f"{len(added)} imagen(es) añadida(s) como elemento")

        threading.Thread(target=worker, daemon=True, name="import-images").start()

    def delete_element(self) -> None:
        element = self.selected_element()
        if element is None:
            return
        if self._apply("eliminar elemento", element_mod.remove_element(self.project, element.id)):
            state.selection.set(None)

    def _update_element(self, label: str, **changes) -> None:  # noqa: ANN003
        element = self.selected_element()
        if element is None:
            return
        try:
            project = element_mod.update_element(self.project, element.id, **changes)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            self._sync_inspector()
            return
        self._apply(label, project)
        self._sync_inspector()

    def submit_element_start(self, value: str) -> None:
        element = self.selected_element()
        if element is None:
            return
        try:
            start = tl.parse_time(value)
        except ValueError:
            state.toast("warning", "Tiempo inválido: usa mm:ss.mmm")
            self._sync_inspector()
            return
        total = tl.total_duration(self.project)
        start = max(0.0, min(start, total - 0.1))
        end = max(start + 0.1, min(element.end, total))
        if end <= start:
            end = min(total, start + 0.2)
        self._update_element("inicio del elemento", start=round(start, 4), end=round(end, 4))

    def submit_element_end(self, value: str) -> None:
        element = self.selected_element()
        if element is None:
            return
        try:
            end = tl.parse_time(value)
        except ValueError:
            state.toast("warning", "Tiempo inválido: usa mm:ss.mmm")
            self._sync_inspector()
            return
        total = tl.total_duration(self.project)
        end = max(element.start + 0.1, min(end, total))
        self._update_element("fin del elemento", end=round(end, 4))

    def move_element_z(self, action: str) -> None:
        """Cambia el z-order del elemento seleccionado: `front`, `back`, `up` o `down`."""
        element = self.selected_element()
        if element is None:
            return
        actions = {
            "front": ("traer al frente", element_mod.bring_to_front),
            "back": ("enviar al fondo", element_mod.send_to_back),
            "up": ("subir elemento", element_mod.raise_element),
            "down": ("bajar elemento", element_mod.lower_element),
        }
        item = actions.get(action)
        if item is None:
            return
        label, function = item
        self._apply(label, function(self.project, element.id))

    # -- caja de manipulación del preview (C, opcional) -----------------------------

    def _element_display(self) -> tuple[float, float] | None:
        """Tamaño (px) con el que se ve el lienzo dentro del panel del preview.

        Se deriva del hueco medido (`preview_area`) y del aspecto actual del proyecto, no del
        último `preview_box`: cambiar el lienzo en el inspector no siempre dispara un
        `on_resize`, y la caja del elemento debe seguir al escenario nuevo (docs/23/docs/25).
        """
        area = state.preview_area()
        stage = ce.fit_stage(self.project.width, self.project.height, area.width, area.height)
        if stage[0] <= 0 or stage[1] <= 0:
            return None
        return float(stage[0]), float(stage[1])

    def begin_element_drag(self, element_id: str, mode: str) -> None:
        if not self._guard_editable() or not self.project.has_element(element_id):
            return
        display = self._element_display()
        if display is None:
            return
        self.cancel_drag()
        self._drag = {
            "kind": "element",
            "mode": mode,
            "id": element_id,
            "project": self.project,
            "element": self.project.element(element_id),
            "display": display,
            "dx": 0.0,
            "dy": 0.0,
            "begun": False,
        }
        state.selection.set(("element", element_id))

    def drag_element(self, dx: float, dy: float) -> None:
        drag = self._drag
        if drag is None or drag["kind"] != "element":
            return
        drag["dx"], drag["dy"] = dx, dy
        if abs(dx) < _CLICK_SLOP_PX and abs(dy) < _CLICK_SLOP_PX:
            return
        if not drag.get("begun"):
            self.store.begin("mover elemento" if drag["mode"] == "move" else "escalar elemento")
            drag["begun"] = True
        base: Project = drag["project"]
        element = drag["element"]
        display_w, display_h = drag["display"]
        if display_w <= 0 or display_h <= 0:
            return
        fraction_x, fraction_y = dx / display_w, dy / display_h
        changes: dict[str, float] = {}
        if drag["mode"] == "move":
            changes = {
                "x": round(max(0.0, min(1.0, element.x + fraction_x)), 4),
                "y": round(max(0.0, min(1.0, element.y + fraction_y)), 4),
            }
        else:
            sign_x = 1.0 if drag["mode"] in ("se", "ne") else -1.0
            sign_y = 1.0 if drag["mode"] in ("se", "sw") else -1.0
            width = max(
                element_mod.ELEMENT_SIZE_MIN,
                min(element_mod.ELEMENT_SIZE_MAX, element.width + sign_x * fraction_x),
            )
            height = max(
                element_mod.ELEMENT_SIZE_MIN,
                min(element_mod.ELEMENT_SIZE_MAX, element.height + sign_y * fraction_y),
            )
            changes = {
                "width": round(width, 4),
                "height": round(height, 4),
                "x": round(max(0.0, min(1.0, element.x + sign_x * (width - element.width) / 2)), 4),
                "y": round(max(0.0, min(1.0, element.y + sign_y * (height - element.height) / 2)), 4),
            }
        try:
            project = element_mod.update_element(base, element.id, **changes)
        except ProjectError:
            return
        self.store.update(project)

    # -- manipulación directa de textos sobre el preview ----------------------------

    def begin_text_drag(self, text_id: str, mode: str = "move") -> None:
        if not self._guard_editable() or self.player.playing:
            return
        if not self.project.has_text(text_id):
            return
        display = self._element_display()
        if display is None:
            return
        self.cancel_drag()
        self._text_drag = {
            "id": text_id,
            "mode": mode,
            "project": self.project,
            "text": self.project.text(text_id),
            "display": display,
            "dx": 0.0,
            "dy": 0.0,
            "begun": False,
        }
        state.selection.set(("text", text_id))

    def drag_text(self, dx: float, dy: float) -> None:
        drag = self._text_drag
        if drag is None:
            return
        drag["dx"], drag["dy"] = float(dx), float(dy)
        if abs(dx) < _CLICK_SLOP_PX and abs(dy) < _CLICK_SLOP_PX:
            return
        if not drag.get("begun"):
            self.store.begin("mover texto" if drag["mode"] == "move" else "escalar texto")
            drag["begun"] = True
        base: Project = drag["project"]
        overlay = drag["text"]
        display_w, display_h = drag["display"]
        if display_w <= 0 or display_h <= 0:
            return
        if drag["mode"] == "move":
            changes = {
                "x": round(max(0.0, min(1.0, overlay.x + dx / display_w)), 4),
                "y": round(max(0.0, min(1.0, overlay.y + dy / display_h)), 4),
            }
        else:
            from . import element_overlay

            frame = element_overlay.text_box(base, overlay, drag["display"])
            if frame is None:
                return
            sign_x = 1.0 if drag["mode"] in ("se", "ne") else -1.0
            sign_y = 1.0 if drag["mode"] in ("se", "sw") else -1.0
            diagonal = max(1.0, frame.width**2 + frame.height**2)
            factor = 1.0 + (sign_x * dx * frame.width + sign_y * dy * frame.height) / diagonal
            size = max(8, min(200, int(round(overlay.size * factor))))
            changes = {"size": size}
        try:
            project = tl.update_text(base, overlay.id, **changes)
        except ProjectError:
            return
        self.store.update(project)

    def end_text_drag(self) -> None:
        drag, self._text_drag = self._text_drag, None
        if drag is None:
            return
        if not drag.get("begun"):
            return  # un clic solo selecciona
        if abs(drag.get("dx", 0.0)) >= _CLICK_SLOP_PX or abs(drag.get("dy", 0.0)) >= _CLICK_SLOP_PX:
            self.store.commit()
            self._sync_inspector()
        else:
            self.store.cancel()  # un clic sin movimiento no crea undo
        self._sync_meta()

    def _cancel_text_drag(self) -> bool:
        if self._text_drag is None:
            return False
        drag, self._text_drag = self._text_drag, None
        if drag.get("begun"):
            self.store.cancel()
            self._sync_meta()
        return True

    # -- capas de textos/elementos en el timeline -----------------------------------

    def begin_overlay_drag(self, kind: str, item_id: str, mode: str = "move") -> None:
        if not self._guard_editable() or self.player.playing:
            return
        if kind not in ("text", "element") or mode not in ("move", "trim-l", "trim-r"):
            return
        if kind == "text" and not self.project.has_text(item_id):
            return
        if kind == "element" and not self.project.has_element(item_id):
            return
        self.cancel_drag()
        item = self.project.text(item_id) if kind == "text" else self.project.element(item_id)
        self._overlay_drag = {
            "kind": kind,
            "id": item_id,
            "mode": mode,
            "project": self.project,
            "item": item,
            "dx": 0.0,
            "begun": False,
        }
        state.selection.set(("text" if kind == "text" else "element", item_id))

    def drag_overlay(self, dx: float) -> None:
        drag = self._overlay_drag
        if drag is None:
            return
        drag["dx"] = float(dx)
        if abs(dx) < _CLICK_SLOP_PX:
            return
        if not drag.get("begun"):
            self.store.begin(
                ("mover " if drag["mode"] == "move" else "recortar ")
                + ("texto" if drag["kind"] == "text" else "elemento")
            )
            drag["begun"] = True
        base: Project = drag["project"]
        item = drag["item"]
        total = tl.total_duration(base)
        if total <= 0:
            return
        zoom = max(1.0, state.tl_zoom())
        delta = float(dx) / zoom
        min_len = max(0.2, 1.0 / max(1, base.fps))
        start, end = float(item.start), float(item.end)
        if drag["mode"] == "move":
            duration = end - start
            start = max(0.0, min(start + delta, max(0.0, total - duration)))
            end = start + duration
        elif drag["mode"] == "trim-l":
            start = max(0.0, min(start + delta, end - min_len))
        else:
            end = min(total, max(start + min_len, end + delta))
        if state.snap_on():
            candidates = tl.snap_candidates(base, playhead=state.playhead())
            for other in (*base.texts, *base.elements):
                if other.id != drag["id"]:
                    candidates.extend((other.start, other.end))
            if drag["mode"] in ("move", "trim-l"):
                start = tl.snap(start, candidates, zoom, threshold_px=6)
                if drag["mode"] == "move":
                    end = min(total, start + (float(item.end) - float(item.start)))
            if drag["mode"] == "trim-r":
                end = tl.snap(end, candidates, zoom, threshold_px=6)
        changes: dict[str, float] = {"start": round(start, 4), "end": round(end, 4)}
        if drag["kind"] == "element" and item.kind == "video":
            asset = base.asset(item.asset_id or "")
            if drag["mode"] == "trim-l":
                source_in = item.in_ + (start - item.start)
                if source_in < 0:
                    source_in = 0.0
                    start = max(0.0, item.start - item.in_)
                    changes["start"] = round(start, 4)
                changes["in_"] = round(source_in, 4)
            if drag["mode"] in ("move", "trim-r"):
                max_end = item.start + max(0.2, asset.duration - item.in_)
                if end > max_end:
                    end = max_end
                    changes["end"] = round(end, 4)
        try:
            if drag["kind"] == "text":
                project = tl.update_text(
                    base, drag["id"], start=round(start, 4), end=round(end, 4),
                )
            else:
                project = element_mod.update_element(base, drag["id"], **changes)
        except ProjectError:
            return
        self.store.update(project)

    def end_overlay_drag(self) -> None:
        drag, self._overlay_drag = self._overlay_drag, None
        if drag is None:
            return
        if not drag.get("begun"):
            return
        if abs(drag.get("dx", 0.0)) >= _CLICK_SLOP_PX:
            self.store.commit()
            self._sync_inspector()
        else:
            self.store.cancel()
        self._sync_meta()

    def _cancel_overlay_drag(self) -> bool:
        if self._overlay_drag is None:
            return False
        drag, self._overlay_drag = self._overlay_drag, None
        if drag.get("begun"):
            self.store.cancel()
            self._sync_meta()
        return True

    # -- selección múltiple (B3) ---------------------------------------------------

    def begin_marquee(self, window_x: float) -> None:
        if not self._guard_editable():
            return
        self._marquee_start = self.content_x(window_x)
        state.marquee.set(state.Marquee(self._marquee_start, 0.0, self._marquee_start, 0.0))

    def drag_marquee(self, window_x: float) -> None:
        start = self._marquee_start
        if start is None:
            return
        current = self.content_x(window_x)
        x0, x1 = sorted((start, current))
        t0 = tl.time_at_x(x0, state.tl_zoom())
        t1 = tl.time_at_x(x1, state.tl_zoom())
        # Por rango de tiempo y en todas las pistas: el video y el audio en posición absoluta de
        # las pistas desbloqueadas (la música secuencial se selecciona con clic).
        project = self.project
        chosen: set[str] = set()
        for track in (project.video, *project.audio):
            if track is not project.video and (track.mode != "absolute" or track.locked):
                continue
            for clip, start_t in zip(track.clips, tl.clip_starts(track)):
                if start_t < t1 and start_t + clip.duration > t0:
                    chosen.add(clip.id)
        state.multi_selection.set(frozenset(chosen))
        state.marquee.set(state.Marquee(x0, 0.0, x1, 0.0))

    def end_marquee(self) -> None:
        self._marquee_start = None
        state.marquee.set(None)
        self._select_primary(self.project, state.multi_selection())

    # -- reubicar carpeta (B6) ------------------------------------------------------

    def relocate_folder(self) -> None:
        if not self._guard_editable():
            return
        from . import file_dialogs

        try:
            folder = file_dialogs.pick_folder(state.recordings_dir(), title="Reubicar archivos del proyecto")
        except Exception as exc:
            state.toast("danger", f"No se pudo abrir el diálogo: {exc}")
            return
        if folder is not None:
            threading.Thread(
                target=self.relocate_folder_to, args=(folder,), daemon=True, name="relocate-folder"
            ).start()

    def relocate_folder_to(self, folder: Path) -> bool:
        """Busca los assets offline por nombre dentro de `folder` y los reemplaza de una vez."""
        project = self.project
        missing = [a for a in project.assets if media.is_offline(a)]
        if not missing:
            state.toast("info", "No hay archivos offline que reubicar")
            return False
        found: dict[str, Path] = {}
        try:
            files = [p for p in Path(folder).rglob("*") if p.is_file()]
        except OSError as exc:
            state.toast("danger", f"No se pudo recorrer «{folder}»: {exc}")
            return False
        for asset in missing:
            wanted = Path(asset.path).name.lower()
            match = next((p for p in files if p.name.lower() == wanted), None)
            if match is not None:
                found[asset.id] = match
        if not found:
            state.toast("warning", f"No encontré ninguno de los {len(missing)} archivo(s) en «{folder.name}»")
            return False
        replaced: dict[str, MediaAsset] = {}
        for asset in missing:
            path = found.get(asset.id)
            if path is None:
                continue
            try:
                probed = (
                    media.probe_image(path, asset_id=asset.id)
                    if asset.kind == "image"
                    else media.probe(path, asset_id=asset.id)
                )
                if (asset.width > 0 or asset.height > 0) and (probed.width <= 0 or probed.height <= 0):
                    state.toast("warning", f"«{path.name}» no tiene pista de video")
                    continue
                replaced[asset.id] = probed
            except RecStudioError as exc:
                state.toast("warning", exc.message)
        if not replaced:
            return False
        assets = tuple(replaced.get(a.id, a) for a in project.assets)
        if not self._apply(f"reubicar {len(replaced)} archivo(s)", replace(project, assets=assets)):
            return False
        self._refresh_offline(self.project)
        for asset in replaced.values():
            self._queue_waveform(asset)
            self._queue_thumbs(asset)
            self._queue_proxy(asset)
            self._queue_proxy(asset)
        state.toast(
            "success",
            f"{len(replaced)} de {len(missing)} archivo(s) reubicado(s) desde «{folder.name}»",
        )
        return True

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
        before = self.project
        try:
            project, clamped = tl.set_transition(before, index, kind, duration)
            project = tl.sync_linked_audio(project, before)
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
        before = self.project
        try:
            project = tl.trim(
                before, clip.id,
                clip.in_ if new_in is None else new_in,
                clip.out_ if new_out is None else new_out,
            )
            project = tl.sync_linked_audio(project, before)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            self._sync_inspector()  # el texto vuelve al valor real
            return
        if not self._apply(label, project):
            self._sync_inspector()  # el texto vuelve al valor real

    def submit_audio_start(self, text: str) -> None:
        self._on_insp_audio_start(text)

    def submit_audio_end(self, text: str) -> None:
        self._on_insp_audio_end(text)

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
            self.player.play(state.playhead(), rate=1.0)
        state.play_rate.set(1.0)

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

    # -- J/K/L, loop e In/Out del timeline (fase 3) ---------------------------------------

    SHUTTLE_MAX = 8.0

    def shuttle(self, direction: int) -> None:
        """L (adelante) / J (atrás): 1× y, si ya va en ese sentido, el doble (hasta 8×).

        Hacia atrás no hay audio y cada frame es un seek de PyAV: el preview va a menos fps,
        pero el reloj es exacto (ver docs/21).
        """
        if not self.project.video.clips:
            return
        player = self.player
        if not player.playing:
            player.play(state.playhead(), rate=1.0 if direction > 0 else -1.0)
        elif (player.rate > 0) == (direction > 0):
            player.set_rate(max(-self.SHUTTLE_MAX, min(self.SHUTTLE_MAX, player.rate * 2)))
        else:
            player.set_rate(1.0 if direction > 0 else -1.0)
        state.play_rate.set(player.rate)

    def stop_shuttle(self) -> None:
        """K: pausa (y la próxima reproducción vuelve a 1×)."""
        if self.player.playing:
            self.player.pause()
            self.seek(self.player.time)
        state.play_rate.set(1.0)

    def toggle_loop(self) -> None:
        state.loop_on.set(not state.loop_on())
        self._sync_loop()
        start, end = tl.play_range(self.project)
        if state.loop_on():
            where = "el rango In/Out" if tl.has_range(self.project) else "todo el timeline"
            state.toast("info", f"Loop activado: {where} ({tl.format_time(start)}–{tl.format_time(end)})")
        else:
            state.toast("info", "Loop desactivado")

    def _sync_loop(self) -> None:
        self.player.set_loop(tl.play_range(self.project) if state.loop_on() else None)

    def set_range_in(self) -> None:
        if not self._guard_editable():
            return
        self._apply("punto de entrada", tl.set_range_in(self.project, state.playhead()))

    def set_range_out(self) -> None:
        if not self._guard_editable():
            return
        try:
            project = tl.set_range_out(self.project, state.playhead())
        except ProjectError as exc:
            state.toast("info", exc.message)
            return
        self._apply("punto de salida", project)

    def clear_range(self) -> None:
        if tl.has_range(self.project) and self._apply("quitar In/Out", tl.clear_range(self.project)):
            state.toast("info", "In/Out borrados")

    def go_range_in(self) -> None:
        self.seek(tl.play_range(self.project)[0])

    def go_range_out(self) -> None:
        self.seek(tl.play_range(self.project)[1])

    # -- marcadores (fase 3) -----------------------------------------------------------

    def add_marker(self) -> None:
        if not self._guard_editable():
            return
        try:
            project, marker = tl.add_marker(self.project, state.playhead())
        except ProjectError as exc:
            existing = tl.marker_at(self.project, state.playhead(), 0.5 / self.project.fps)
            if existing is not None:
                self.select_marker(existing.id, seek=False)
            state.toast("info", exc.message)
            return
        if self._apply("añadir marcador", project):
            self.select_marker(marker.id, seek=False)

    def select_marker(self, marker_id: str, *, seek: bool = True) -> None:
        if not self.project.has_marker(marker_id):
            return
        state.multi_selection.set(frozenset())
        state.selection.set(("marker", marker_id))
        if seek:
            self.seek(self.project.marker(marker_id).t)

    def rename_marker(self, name: str) -> None:
        marker = self.selected_marker()
        if marker is None:
            return
        try:
            project = tl.rename_marker(self.project, marker.id, name)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            self._sync_inspector()
            return
        self._apply("renombrar marcador", project)

    def delete_marker(self) -> None:
        marker = self.selected_marker()
        if marker is not None and self._apply("eliminar marcador", tl.remove_marker(self.project, marker.id)):
            state.selection.set(None)

    def jump_marker(self, direction: int) -> None:
        marker = tl.adjacent_marker(self.project, state.playhead(), direction)
        if marker is None:
            state.toast("info", "No hay más marcadores en esa dirección")
            return
        if self.player.playing:
            self.player.pause()
        self.select_marker(marker.id)

    # -- portapapeles (fase 3) ---------------------------------------------------------

    def _selected_ids(self) -> frozenset[str]:
        multi = state.multi_selection()
        if multi:
            return frozenset(multi)
        selection = state.selection()
        if not selection:
            return frozenset()
        if selection[0] == "clip" and self.project.has_clip(selection[1]):
            return frozenset({selection[1]})
        if selection[0] == "text" and self.project.has_text(selection[1]):
            return frozenset({selection[1]})
        if selection[0] == "element" and self.project.has_element(selection[1]):
            return frozenset({selection[1]})
        audio = self.selected_audio()
        if audio is not None:
            return frozenset({audio[2].id})
        return frozenset()

    def copy(self) -> bool:
        if self.copy_selected_key():  # Ctrl+C con una clave/punto seleccionado (docs/41)
            return True
        ids = self._selected_ids()
        if not ids:
            state.toast("info", "Selecciona un clip, un texto o un elemento para copiar")
            return False
        try:
            self._clipboard = tl.copy_items(self.project, ids)
        except ProjectError as exc:
            state.toast("info", exc.message)
            return False
        self._key_clipboard = None
        state.clipboard_count.set(len(self._clipboard.items))
        state.toast("info", f"{len(self._clipboard.items)} elemento(s) copiado(s)")
        return True

    def cut(self) -> None:
        if not self._guard_editable():
            return
        ids = self._selected_ids()
        if not ids:
            state.toast("info", "Selecciona un clip, un texto o un elemento para cortar")
            return
        try:
            clipboard = tl.copy_items(self.project, ids)
            project = tl.remove_items(self.project, ids)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply("cortar", project):
            self._clipboard = clipboard
            self._key_clipboard = None
            state.clipboard_count.set(len(clipboard.items))
            self.clear_selection()

    def paste(self) -> None:
        if self._key_clipboard is not None:  # gana el último copiado (docs/41)
            self.paste_key()
            return
        if not self._guard_editable():
            return
        if self._clipboard is None or self._clipboard.empty:
            state.toast("info", "El portapapeles está vacío (Ctrl+C para copiar)")
            return
        try:
            project, new_ids = tl.paste(self.project, self._clipboard, state.playhead())
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply("pegar", project):
            self._select_pasted(project, new_ids)

    def duplicate(self) -> None:
        if not self._guard_editable():
            return
        ids = self._selected_ids()
        if not ids:
            state.toast("info", "Selecciona un clip, un texto o un elemento para duplicar")
            return
        try:
            project, new_ids = tl.duplicate(self.project, ids)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        if self._apply("duplicar", project):
            self._select_pasted(project, new_ids)

    def _select_pasted(self, project: Project, new_ids: tuple[str, ...]) -> None:
        """Lo pegado queda seleccionado (varios clips ⇒ selección múltiple, para moverlos)."""
        if len(new_ids) == 1 and project.has_text(new_ids[0]):
            self.select_text(new_ids[0])
            return
        if len(new_ids) == 1 and project.has_element(new_ids[0]):
            self.select_element(new_ids[0])
            return
        clips = frozenset(
            i for i in new_ids if not project.has_text(i) and not project.has_element(i)
        )
        if not clips:
            state.selection.set(None)
            return
        state.multi_selection.set(clips if len(clips) > 1 else frozenset())
        self._select_primary(project, clips)

    def _select_primary(self, project: Project, ids) -> None:  # noqa: ANN001
        """Selección primaria (inspector) = el primer clip de video de `ids` o, si no, de audio."""
        first = next((c.id for c in project.video.clips if c.id in ids), None)
        if first is not None:
            state.selection.set(("clip", first))
            return
        for track_index, track in enumerate(project.audio):
            for clip_index, clip in enumerate(track.clips):
                if clip.id in ids:
                    state.selection.set(("audio", track_index, clip_index))
                    return
        state.selection.set(None)

    # -- razor y Q/W (fase 3) -------------------------------------------------------------

    def _time_at_window_x(self, window_x: float) -> float:
        t = tl.time_at_x(self.content_x(window_x), state.tl_zoom())
        if state.snap_on():
            t = tl.snap(t, tl.snap_candidates(self.project, playhead=state.playhead()), state.tl_zoom(),
                        threshold_px=5)
        return max(0.0, t)

    def razor_at(self, window_x: float, track: int | None = None, *, everything: bool = False) -> None:
        """Clic con la razor: corta la pista pulsada (`None` = video + su audio enlazado) o, en
        la regla o una zona vacía (`everything`), todas las pistas desbloqueadas."""
        if not self._guard_editable():
            return
        t = self._time_at_window_x(window_x)
        if not everything and track is None and self.project.video.locked:
            state.toast("warning", "La pista de video está bloqueada")
            return
        if not everything and track is not None and self.project.audio[track].locked:
            state.toast("warning", f"La pista «{self.project.audio[track].name or 'audio'}» está bloqueada")
            return
        try:
            project = tl.split_everything(self.project, t) if everything else tl.razor(self.project, t, track)
        except ProjectError as exc:
            state.toast("info", exc.message)
            return
        if self._apply("cortar con razor", project):
            self.seek(t)

    def trim_to_playhead(self, side: str) -> None:
        """Q (`start`) / W (`end`): recorta el clip bajo el playhead hasta el playhead.

        Si hay un clip de audio seleccionado bajo el playhead se recorta ese; si no, el de video
        (con ripple: el timeline de video es magnético).
        """
        if not self._guard_editable():
            return
        t = state.playhead()
        audio = self.selected_audio()
        track = None
        if audio is not None:
            track_index, clip_index, clip = audio
            start = tl.clip_starts(self.project.audio[track_index])[clip_index]
            if start <= t < start + clip.duration:
                if clip.link_id:
                    state.toast(
                        "warning",
                        "El audio está enlazado al video: desvincúlalo para recortarlo",
                    )
                    return
                track = track_index
        before = self.project
        try:
            project = tl.trim_to(before, t, side, track)
            if track is None:  # Q/W sobre el video: su audio enlazado lo sigue
                project = tl.sync_linked_audio(project, before)
        except ProjectError as exc:
            state.toast("info", exc.message)
            return
        if not self._apply("recortar inicio" if side == "start" else "recortar final", project):
            return
        if track is None:
            # Q: el clip conserva su inicio y lo de después se desplaza (ripple), así que el
            # playhead salta a ese inicio, que ahora muestra el frame que estaba bajo el playhead.
            pos = tl.locate(before, t)
            if pos is not None:
                if side == "start":
                    self.seek(tl.clip_starts(project.video)[pos.index])
                state.selection.set(("clip", pos.clip.id))

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

    def _source_peaks(self, project: Project) -> dict[str, list[float]]:
        """Picos de audio por asset para el snap (B4); solo los que ya tienen waveform cacheada."""
        peaks: dict[str, list[float]] = {}
        for asset in project.assets:
            if not asset.has_audio:
                continue
            waveform = media.cached_waveform(asset.path)
            if waveform is not None:
                peaks[asset.id] = media.waveform_peaks(waveform)
        return peaks

    def begin_move(self, clip_id: str) -> None:
        if not self._guard_editable() or not self.project.has_clip(clip_id):
            return
        if self._begin_block(clip_id):
            return
        if self.project.video.locked:
            state.toast("warning", "La pista de video está bloqueada")
            return
        self.cancel_drag()
        project = self.project
        index = project.video.index_of(clip_id)
        start = tl.clip_starts(project.video)[index]
        self.store.begin("mover clip")
        self._drag = {
            "kind": "move", "id": clip_id, "project": project, "start": start, "dx": 0.0,
            "audio_peaks": self._source_peaks(project),
        }
        state.selection.set(("clip", clip_id))

    def drag_move(self, dx: float) -> None:
        drag = self._drag
        if drag is not None and drag["kind"] == "block":
            self.drag_block(dx)
            return
        if drag is None or drag["kind"] != "move":
            return
        drag["dx"] = dx
        if abs(dx) < _CLICK_SLOP_PX:
            return
        base: Project = drag["project"]
        clip = base.clip(drag["id"])
        ghost = drag["start"] + dx / state.tl_zoom()
        cands = tl.snap_candidates(base, playhead=state.playhead(), exclude=clip.id)
        cands += tl.audio_snap_candidates(base, clip.id, drag.get("audio_peaks", {}))
        snapped_start = self._snap(ghost, cands)
        snapped_end = self._snap(ghost + clip.duration, cands) - clip.duration
        ghost = snapped_start if snapped_start != ghost else snapped_end
        ghost = max(0.0, ghost)
        index = tl.drop_index(base, clip.id, ghost)
        synced = self._sync_audio(base, tl.move(base, clip.id, index),
                                  notify=not drag.get("sync_warned", False))
        if synced is None:
            drag["sync_warned"] = True
            return  # el audio enlazado no cabe: se queda en la última posición válida
        self.store.update(synced)
        state.tl_drag.set(state.TimelineDrag("move", clip.id, ghost=round(ghost, 4)))

    def begin_trim(self, clip_id: str, side: str) -> None:
        if not self._guard_editable() or not self.project.has_clip(clip_id):
            return
        if self.project.video.locked:
            state.toast("warning", "La pista de video está bloqueada")
            return
        self.cancel_drag()
        project = self.project
        index = project.video.index_of(clip_id)
        start = tl.clip_starts(project.video)[index]
        self.store.begin("recortar")
        self._drag = {
            "kind": f"trim_{side}", "id": clip_id, "project": project, "index": index, "start": start,
            "audio_peaks": self._source_peaks(project),
        }
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
        cands += tl.audio_snap_candidates(base, clip.id, drag.get("audio_peaks", {}))
        if drag["kind"] == "trim_l":
            edge = self._snap(start + dx / zoom, cands)
            new_in = clip.in_ + (edge - start) * clip.speed
            synced = self._sync_audio(
                base, tl.trim(base, clip.id, new_in, clip.out_),
                notify=not drag.get("sync_warned", False),
            )
            if synced is None:
                drag["sync_warned"] = True
                return
            project = synced
            shift = (project.clip(clip.id).in_ - clip.in_) / clip.speed
            self.store.update(project)
            state.tl_drag.set(state.TimelineDrag("trim_l", clip.id, shift_index=index, shift=round(shift, 5)))
            self.player.seek(tl.clip_starts(project.video)[index])  # se ve el nuevo primer frame
        else:
            edge = self._snap(start + clip.duration + dx / zoom, cands)
            new_out = clip.in_ + (edge - start) * clip.speed
            synced = self._sync_audio(
                base, tl.trim(base, clip.id, clip.in_, new_out),
                notify=not drag.get("sync_warned", False),
            )
            if synced is None:
                drag["sync_warned"] = True
                return
            project = synced
            self.store.update(project)
            state.tl_drag.set(state.TimelineDrag("trim_r", clip.id))
            new_clip = project.clip(clip.id)
            self.player.seek(start + new_clip.duration - 1.0 / project.fps)  # último frame

    def begin_move_audio(self, track_index: int, clip_id: str) -> None:
        if not self._guard_editable():
            return
        if self._begin_block(clip_id):
            return
        track = self.project.audio[track_index]
        if track.locked:
            state.toast("warning", f"La pista «{track.name or 'audio'}» está bloqueada")
            return
        index = track.index_of(clip_id)
        if track.clips[index].link_id:
            # Política segura (docs/24): un audio enlazado no se mueve por su cuenta para no
            # desincronizarlo del video en silencio; se pide desvincularlo primero.
            state.toast(
                "warning",
                "El audio está enlazado al video: pulsa «Desvincular audio» para moverlo",
            )
            return
        self.cancel_drag()
        project = self.project
        start = tl.clip_starts(track)[index]
        self.store.begin("mover audio")
        self._drag = {
            "kind": "audio_move", "track": track_index, "id": clip_id, "project": project,
            "start": start, "dx": 0.0, "audio_peaks": self._source_peaks(project),
        }
        self.select_audio(track_index, index)

    def drag_move_audio(self, dx: float) -> None:
        drag = self._drag
        if drag is not None and drag["kind"] == "block":
            self.drag_block(dx)
            return
        if drag is None or drag["kind"] != "audio_move":
            return
        drag["dx"] = dx
        if abs(dx) < _CLICK_SLOP_PX:
            return
        base: Project = drag["project"]
        track_index = int(drag["track"])
        track = base.audio[track_index]
        index = track.index_of(drag["id"])
        clip = track.clips[index]
        zoom = state.tl_zoom()
        ghost = drag["start"] + dx / zoom
        cands = tl.snap_candidates(base, playhead=state.playhead(), exclude=clip.id)
        cands += tl.audio_snap_candidates(base, clip.id, drag.get("audio_peaks", {}))
        snapped_start = self._snap(ghost, cands)
        snapped_end = self._snap(ghost + clip.duration, cands) - clip.duration
        ghost = snapped_start if snapped_start != ghost else snapped_end
        ghost = max(0.0, ghost)
        if track.mode == "absolute":
            try:
                project = tl.move_audio(base, track_index, clip.id, start=round(ghost, 5))
            except ProjectError:
                return  # solape: el clip se queda donde estaba hasta que haya hueco
            self.store.update(project)
            state.tl_drag.set(state.TimelineDrag(
                "audio_move", clip.id, ghost=round(ghost, 4), track_index=track_index
            ))
        else:
            to_index = tl.audio_drop_index(base, track_index, clip.id, ghost)
            project = tl.move_audio(base, track_index, clip.id, to_index=to_index)
            self.store.update(project)
            state.tl_drag.set(state.TimelineDrag(
                "audio_move", clip.id, ghost=round(ghost, 4), track_index=track_index
            ))

    def begin_trim_audio(self, track_index: int, clip_id: str, side: str) -> None:
        if not self._guard_editable():
            return
        track = self.project.audio[track_index]
        if track.locked:
            state.toast("warning", f"La pista «{track.name or 'audio'}» está bloqueada")
            return
        index = track.index_of(clip_id)
        if track.clips[index].link_id:
            state.toast(
                "warning",
                "El audio está enlazado al video: pulsa «Desvincular audio» para recortarlo",
            )
            return
        self.cancel_drag()
        project = self.project
        start = tl.clip_starts(track)[index]
        self.store.begin("recortar audio")
        self._drag = {
            "kind": f"audio_trim_{side}", "track": track_index, "id": clip_id, "project": project,
            "index": index, "start": start, "audio_peaks": self._source_peaks(project),
        }
        self.select_audio(track_index, index)

    def drag_trim_audio(self, dx: float) -> None:
        drag = self._drag
        if drag is None or not drag["kind"].startswith("audio_trim"):
            return
        base: Project = drag["project"]
        track_index = int(drag["track"])
        track = base.audio[track_index]
        clip = track.clips[track.index_of(drag["id"])]
        index, start = drag["index"], drag["start"]
        zoom = state.tl_zoom()
        starts = tl.clip_starts(track)
        cands = [0.0, state.playhead()]
        for j in range(index):
            cands += [starts[j], starts[j] + track.clips[j].duration]
        cands += tl.audio_snap_candidates(base, clip.id, drag.get("audio_peaks", {}))
        if drag["kind"] == "audio_trim_l":
            edge = self._snap(start + dx / zoom, cands)
            new_in = clip.in_ + (edge - start) * clip.speed
            try:
                project = tl.trim_audio(base, track_index, clip.id, new_in, clip.out_)
            except ProjectError:
                return  # no cabe contra el vecino o el inicio del timeline
            self.store.update(project)
            state.tl_drag.set(state.TimelineDrag(
                "audio_trim_l", clip.id, track_index=track_index
            ))
            moved_track = project.audio[track_index]
            moved_index = moved_track.index_of(clip.id)
            self.player.seek(tl.clip_starts(moved_track)[moved_index])
        else:
            edge = self._snap(start + clip.duration + dx / zoom, cands)
            new_out = clip.in_ + (edge - start) * clip.speed
            project = tl.trim_audio(base, track_index, clip.id, clip.in_, new_out)
            self.store.update(project)
            state.tl_drag.set(state.TimelineDrag(
                "audio_trim_r", clip.id, track_index=track_index
            ))
            new_track = project.audio[track_index]
            new_clip = new_track.clips[new_track.index_of(clip.id)]
            self.player.seek(start + new_clip.duration - 1.0 / project.fps)

    # -- mover la selección múltiple en bloque (A8, fase 3) -------------------------------

    def _begin_block(self, clip_id: str) -> bool:
        """Si `clip_id` es parte de una selección múltiple, empieza a moverla en bloque."""
        multi = state.multi_selection()
        if clip_id not in multi or len(multi) < 2:
            return False
        project = self.project
        if project.video.locked and any(c.id in multi for c in project.video.clips):
            state.toast("warning", "La pista de video está bloqueada")
            return True
        for track in project.audio:
            if track.locked and any(c.id in multi for c in track.clips):
                state.toast("warning", f"La pista «{track.name or 'audio'}» está bloqueada")
                return True
        bounds = tl.selection_bounds(project, multi)
        if bounds is None:
            return False
        self.cancel_drag()
        self.store.begin("mover selección")
        self._drag = {
            "kind": "block", "id": clip_id, "ids": frozenset(multi), "project": project,
            "start": bounds[0], "length": bounds[1] - bounds[0], "dx": 0.0,
        }
        return True

    def drag_block(self, dx: float) -> None:
        drag = self._drag
        if drag is None or drag["kind"] != "block":
            return
        drag["dx"] = dx
        if abs(dx) < _CLICK_SLOP_PX:
            return
        base: Project = drag["project"]
        ids, length = drag["ids"], drag["length"]
        ghost = drag["start"] + dx / state.tl_zoom()
        cands = tl.snap_candidates(base, playhead=state.playhead(), exclude=ids)
        snapped_start = self._snap(ghost, cands)
        snapped_end = self._snap(ghost + length, cands) - length
        ghost = max(0.0, snapped_start if snapped_start != ghost else snapped_end)
        # El audio enlazado no viaja como audio libre: lo recoloca `sync_linked_audio` con el
        # video, no `shift_audio_many` (podía chocar con el audio de un vecino antes de tiempo).
        free_ids = {
            clip_id for clip_id in ids
            if not any(c.id == clip_id and c.link_id for track in base.audio for c in track.clips)
        }
        try:
            project = tl.move_block(base, free_ids, ghost)
        except ProjectError:
            return  # solape de audio: el bloque se queda en la última posición válida
        synced = self._sync_audio(base, project, notify=not drag.get("sync_warned", False))
        if synced is None:
            drag["sync_warned"] = True
            return
        self.store.update(synced)
        state.tl_drag.set(state.TimelineDrag("block", drag["id"], ghost=round(ghost, 4), length=round(length, 4)))

    def end_drag(self) -> None:
        drag, self._drag = self._drag, None
        if drag is None:
            return
        state.tl_drag.set(None)
        if not drag.get("begun", True):
            return  # clic sin transacción (selección de una capa en el preview)
        if drag["kind"] in ("move", "audio_move", "block") and abs(drag.get("dx", 0.0)) < _CLICK_SLOP_PX:
            self.store.cancel()  # fue un clic: solo selecciona
        else:
            self.store.commit()
        self._sync_meta()
        if drag["kind"].startswith("trim") or drag["kind"].startswith("audio_trim"):
            self.player.seek(state.playhead())

    def cancel_drag(self) -> bool:
        """Esc: deshace el drag en curso (vuelve al snapshot del `begin`)."""
        if self._cancel_canvas_drag():
            return True
        if self._cancel_text_drag():
            return True
        if self._cancel_overlay_drag():
            return True
        if self._cancel_envelope_drag():
            return True
        if self._cancel_keyframe_drag():
            return True
        drag, self._drag = self._drag, None
        if drag is None:
            return False
        if drag.get("begun", True):
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
            if state.key_add_on():
                state.key_add_on.set(False)  # Esc también sale del modo «añadir clave» (docs/42)
                return True
            if state.envelope_key() is not None:
                state.envelope_key.set(None)
                return True
            if state.keyframe_key() is not None:
                state.keyframe_key.set(None)
                return True
            if state.import_open():
                state.import_open.set(False)
                return True
            if state.export_open():
                self.close_export()
                return True
            if state.multi_selection() or state.selection() is not None:
                self.clear_selection()
                return True
            if state.razor_on() or state.marquee_on() or state.env_edit_on():
                self.select_tool()
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
        if e.matches("mod+a"):
            if from_input:  # seleccionar el texto del campo, no todos los clips
                return None
            if state.project_open():
                self.select_all()
                return True
            return None
        if e.matches("mod+i"):
            self.open_import()
            return True
        if e.matches("mod+b"):
            if from_input:
                return None
            self.toggle_project_panel()
            return True
        # Portapapeles (fase 3): en un campo enfocado copian/pegan texto, no clips.
        for combo, action in (("mod+c", self.copy), ("mod+x", self.cut), ("mod+v", self.paste),
                              ("mod+d", self.duplicate), ("mod+l", self.toggle_loop),
                              ("alt+x", self.clear_range)):
            if e.matches(combo):
                if from_input or not state.project_open():
                    return None
                action()
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
        if e.matches("shift+s"):
            self.split_audio_only()
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
        if e.matches("p"):
            self.toggle_inspector()
            return True
        # Fase 3 (docs/21): In/Out, marcadores, J/K/L, Q/W y herramientas.
        for combo, action in (
            ("i", self.set_range_in), ("o", self.set_range_out),
            ("shift+i", self.go_range_in), ("shift+o", self.go_range_out),
            ("b", self.add_marker), ("up", lambda: self.jump_marker(-1)),
            ("down", lambda: self.jump_marker(1)),
            ("j", lambda: self.shuttle(-1)), ("k", self.stop_shuttle), ("l", lambda: self.shuttle(1)),
            ("q", lambda: self.trim_to_playhead("start")), ("w", lambda: self.trim_to_playhead("end")),
            ("c", self.toggle_razor), ("v", self.select_tool), ("e", self.toggle_envelope_edit),
            ("n", self.toggle_key_add),
        ):
            if e.matches(combo):
                action()
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
        state.export_range.set("range" if tl.has_range(self.project) else "all")
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
        preset_id = state.export_preset()
        if preset_id == graph_mod.CUSTOM_PRESET_ID:
            error = state.export_custom_error()
            if error:
                state.toast("warning", f"Export personalizado no válido: {error}")
                return
            try:
                preset: str | graph_mod.ExportPreset = state.export_custom_preset()
            except RecStudioError as exc:
                state.toast("warning", exc.message)
                return
        else:
            preset = preset_id
        if preset_id == "copia" and state.export_blockers():
            state.toast("warning", "«Sin recodificar» no es posible: " + "; ".join(state.export_blockers()))
            return
        name = state.export_name().strip() or project.name
        span = self.export_span()
        out = export_mod.unique_output(state.exports_dir(), name)
        self._remember_export()
        try:
            self.exporter.start(project, preset, out, span=span)
        except RecStudioError as exc:
            state.toast("danger", exc.message)

    def export_span(self) -> tuple[float, float] | None:
        """Rango a exportar: `None` = todo el timeline; con «Rango In/Out», el de `play_range`."""
        if state.export_range() != "range" or not tl.has_range(self.project):
            return None
        return tl.play_range(self.project)

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
        """«Reubicar…»: elige el archivo movido. El diálogo es modal (hilo de UI); el `probe`
        del archivo elegido, que sí es lento, va a un hilo."""
        if not self._guard_editable():
            return

        from . import file_dialogs

        try:
            old = self.project.asset(asset_id)
            folder = Path(old.path).parent
            chosen = file_dialogs.open_videos(folder if folder.exists() else state.recordings_dir(),
                                              title=f"Reubicar «{old.name}»")
        except ProjectError as exc:
            state.toast("danger", exc.message)
            return
        except Exception as exc:  # diálogo nativo: cualquier fallo debe acabar en un toast
            state.toast("danger", f"No se pudo abrir el diálogo: {exc}")
            return
        if not chosen:
            return
        threading.Thread(
            target=self.relocate_to, args=(asset_id, chosen[0]), daemon=True, name="relocate"
        ).start()

    def relocate_to(self, asset_id: str, path: Path) -> bool:
        """Sustituye el archivo del asset (bloqueante: llamar desde un hilo). El nuevo debe
        durar lo suficiente para sus clips (lo comprueba `Project.validate`)."""
        old = self.project.asset(asset_id)
        try:
            probed = (
                media.probe_image(path, asset_id=asset_id)
                if old.kind == "image"
                else media.probe(path, asset_id=asset_id)
            )
        except RecStudioError as exc:
            state.toast("danger", exc.message)
            return False
        if (old.width > 0 or old.height > 0) and (probed.width <= 0 or probed.height <= 0):
            state.toast("danger", f"«{path.name}» no tiene pista de video")
            return False
        project = self.project
        assets = tuple(probed if a.id == asset_id else a for a in project.assets)
        if not self._apply("reubicar archivo", replace(project, assets=assets)):
            return False
        self._refresh_offline(self.project)
        self._queue_waveform(probed)
        self._queue_thumbs(probed)
        self._queue_proxy(probed)
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
        state.posters.set({})
        self._poster_failed.clear()
        self.store.reset(project, path, saved=saved)
        self.store.read_only = read_only
        state.selection.set(None)
        state.multi_selection.set(frozenset())
        state.marquee.set(None)
        state.env_edit_on.set(False)
        state.key_add_on.set(False)
        state.envelope_key.set(None)
        state.keyframe_key.set(None)
        state.project_path.set(str(path) if path else "")
        state.project_open.set(True)
        self._sync_meta()
        self._refresh_offline(project)
        self.seek(0.0)
        state.tl_zoom.set(tl.fit_zoom(tl.total_duration(project), state.tl_view_w()))
        for asset in project.assets:
            self._queue_waveform(asset)
            self._queue_thumbs(asset)
            self._queue_proxy(asset)
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

    # -- panel de medios / capas ---------------------------------------------------

    def toggle_project_panel(self) -> None:
        state.project_panel_open.set(not state.project_panel_open())

    def set_project_panel_tab(self, tab: str) -> None:
        if tab in ("media", "layers"):
            state.project_panel_tab.set(tab)

    def browse_media(self) -> None:
        """Diálogo de medios del proyecto (videos, audio e imágenes) para el panel."""
        from . import file_dialogs

        try:
            chosen = file_dialogs.open_media(state.recordings_dir(), title="Importar medios")
        except Exception as exc:  # diálogo nativo: cualquier fallo debe acabar en un toast
            state.toast("danger", f"No se pudo abrir el diálogo: {exc}")
            return
        if chosen:
            self.add_media(chosen)

    def add_media(self, paths: list[Path]) -> None:
        """Añade medios al proyecto sin crear clips (quedan disponibles en el panel)."""
        if not paths:
            return

        def worker() -> None:
            with self._io_lock:
                if not state.project_open():
                    state.toast("info", "Abre un proyecto antes de importar medios")
                    return
                project = self.project
                added: list[MediaAsset] = []
                known = {asset.id for asset in project.assets}
                images = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
                for path in paths:
                    path = Path(path)
                    state.importing.set((*state.importing(), path.name))
                    try:
                        asset = (
                            media.probe_image(path)
                            if path.suffix.lower() in images
                            else media.probe(path)
                        )
                    except RecStudioError as exc:
                        state.toast("danger", exc.message)
                        continue
                    finally:
                        state.importing.set(tuple(n for n in state.importing() if n != path.name))
                    project, effective = tl.add_asset(project, asset)
                    if effective.id not in known:
                        known.add(effective.id)
                        added.append(effective)
                if not added:
                    state.toast("info", "No hay medios nuevos que añadir")
                    return
                if not self._apply(f"importar {len(added)} medio(s)", project):
                    return
                for asset in added:
                    self._poster_failed.discard(asset.path)
                    self._queue_posters([asset])
                    self._queue_waveform(asset)
                    self._queue_thumbs(asset)
                    self._queue_proxy(asset)
                state.toast("success", f"{len(added)} medio(s) añadidos al panel")

        threading.Thread(target=worker, daemon=True, name="add-media").start()

    def remove_asset(self, asset_id: str) -> None:
        """Quita del proyecto un medio que ya no usa ningún clip ni elemento."""
        if not self._guard_editable():
            return
        try:
            project = tl.remove_asset(self.project, asset_id)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        self._apply("quitar medio", project)

    def insert_asset(self, asset_id: str) -> None:
        """Inserta un medio del panel en el playhead."""
        self.insert_asset_at(asset_id, state.playhead())

    def drop_asset(self, asset_id: str, window_x: float, target: str) -> None:
        """Suelta un medio del panel en el timeline (posición del puntero) o en el preview."""
        if target == "timeline":
            t = tl.time_at_x(self.content_x(window_x), state.tl_zoom())
        else:
            t = state.playhead()
        self.insert_asset_at(asset_id, t)

    def select_first_clip(self) -> None:
        clips = self.project.video.clips
        if not clips:
            return
        pos = tl.locate(self.project, state.playhead())
        self.select_clip(pos.clip.id if pos is not None else clips[0].id)

    def insert_asset_at(self, asset_id: str, t: float) -> None:
        """Inserta un medio del panel en el instante `t` (video/audio) o como elemento (imagen)."""
        if not self._guard_editable():
            return
        try:
            asset = self.project.asset(asset_id)
        except ProjectError as exc:
            state.toast("warning", exc.message)
            return
        t = max(0.0, float(t))
        try:
            if asset.kind == "image":
                project, element = element_mod.add_element(
                    self.project, "image", t, element_mod.ELEMENT_DURATION, asset_id=asset.id,
                )
                if self._apply("insertar imagen", project):
                    self.select_element(element.id)
                return
            if asset.kind == "audio":
                project = self.project
                track_index = next(
                    (i for i, track in enumerate(project.audio) if track.mode == "sequence"), None
                )
                if track_index is None:
                    project, track_index = tl.add_audio_track(project, "Música", mode="sequence")
                project, clip = tl.insert_audio_clip(project, track_index, asset, t)
                if self._apply("insertar audio", project):
                    self.select_audio(track_index, project.audio[track_index].index_of(clip.id))
                    self._queue_waveform(asset)
                return
            if state.video_track_target() > 0:
                # El destino del panel manda: un video se superpone en V2/V3 en vez de ir a V1.
                self.add_video_element(asset.id, start=t)
                return
            index = tl.video_insert_index(self.project, t)
            project, clip = tl.insert_clip(self.project, asset, index)
            if asset.has_audio:
                # Mismo contrato que la importación: audio separado y enlazado por defecto.
                try:
                    project, _audio_id = tl.detach_audio(project, clip.id)
                except ProjectError as exc:
                    state.toast("warning", f"No se pudo separar el audio: {exc.message}")
            if self._apply("insertar video", project):
                self.select_clip(clip.id)
                effective = project.asset(clip.asset_id)
                self._queue_posters([effective])
                self._queue_thumbs(effective)
                self._queue_proxy(effective)
                self._queue_waveform(effective)
        except ProjectError as exc:
            state.toast("warning", exc.message)

    def _probe_all(self, paths: list[Path]) -> list[MediaAsset]:
        assets: list[MediaAsset] = []
        for path in paths:
            state.importing.set((*state.importing(), path.name))
            try:
                asset = media.probe(path)
                if asset.width <= 0 or asset.height <= 0:
                    state.toast("warning", f"«{path.name}» no tiene pista de video; usa ♪ para música")
                else:
                    assets.append(asset)
            except RecStudioError as exc:
                state.toast("danger", exc.message)
            finally:
                state.importing.set(tuple(n for n in state.importing() if n != path.name))
        return assets

    def _separate_imported_audio(self, project: Project) -> Project:
        """Deja el audio de los videos importados en «Audio del video» enlazado (docs/24).

        Es una sola operación sobre el proyecto importado (un solo undo con el `apply` que
        sigue). Si un tramo no cabe (p. ej. choca con audio absoluto ya existente o la pista
        está bloqueada), se conserva el audio propio del video y se avisa.
        """
        try:
            return tl.separate_audio_all(project)
        except ProjectError as exc:
            state.toast("warning", f"No se pudo separar el audio automáticamente: {exc.message}")
            return project

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
                    project = self._separate_imported_audio(project)
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
                    project = self._separate_imported_audio(project)
                    self.store.apply("importar", project)
                    self._refresh_offline(project)
                    self.zoom_fit()
                    for asset in assets:
                        effective = project.asset_for_path(asset.path) or asset
                        self._queue_waveform(effective)
                        self._queue_thumbs(effective)
                        self._queue_proxy(effective)
                    state.toast("success", f"{len(assets)} video(s) añadidos al final del timeline")
            self.refresh_recent()

        threading.Thread(target=worker, daemon=True, name="import").start()

    def open_import(self) -> None:
        state.import_open.set(True)

    def browse_import(self) -> None:
        """Diálogo nativo de archivos (modal: el sistema corre su bucle mientras dura)."""
        from . import file_dialogs

        try:
            chosen = file_dialogs.open_videos(state.recordings_dir())
        except Exception as exc:  # diálogo nativo: cualquier fallo debe acabar en un toast
            state.toast("danger", f"No se pudo abrir el diálogo: {exc}")
            return
        if chosen:
            self.import_paths(chosen)

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
                state.multi_selection.set(frozenset())
                state.marquee.set(None)
                state.env_edit_on.set(False)
                state.key_add_on.set(False)
                state.envelope_key.set(None)
                state.keyframe_key.set(None)
                state.posters.set({})
                state.preview_frame.set(b"")
                state.playhead.set(0.0)
                self.settings.last_project = ""
                self._sync_meta()
            self.refresh_recent()

        threading.Thread(target=worker, daemon=True, name="close-project").start()

    # -- panel de medios: pósters de los assets ------------------------------------

    def _set_poster(self, asset_id: str, data: bytes) -> None:
        if self._closing.is_set():
            return
        with self._poster_lock:
            posters = dict(state.posters())
            posters[asset_id] = bytes(data)
            state.posters.set(posters)

    def _queue_posters(self, assets) -> None:  # noqa: ANN001
        """Rellena en segundo plano los pósters de video del panel de medios."""
        for asset in assets:
            if asset.kind != "video" or media.is_offline(asset):
                continue
            if asset.id in state.posters():
                continue
            if asset.path in self._poster_failed:
                continue
            cached = media.cached_poster(asset.path, height=96)
            if cached is not None:
                self._set_poster(asset.id, cached)
                continue
            with self._poster_lock:
                if asset.path in self._poster_pending:
                    continue
                self._poster_pending.add(asset.path)

            def worker(a=asset) -> None:
                try:
                    data = media.poster(a.path, at_s=0.5, height=96)
                except (RecStudioError, OSError):
                    with self._poster_lock:
                        self._poster_failed.add(a.path)
                    return
                finally:
                    with self._poster_lock:
                        self._poster_pending.discard(a.path)
                self._set_poster(a.id, data)

            threading.Thread(target=worker, daemon=True, name="media-poster").start()

    # -- miniaturas del filmstrip -------------------------------------------------

    def _queue_thumbs(self, asset: MediaAsset) -> None:
        """Calcula las miniaturas del asset (una tira por asset, cacheada en disco)."""
        if asset.kind != "video" or media.is_offline(asset):
            return
        if media.cached_thumbstrip(asset.path) is not None:
            state.thumbs.set(state.thumbs() + 1)
            return
        with self._thumb_lock:
            if asset.path in self._thumb_pending:
                return
            self._thumb_pending.add(asset.path)

        def worker() -> None:
            try:
                media.thumbstrip(asset.path)
                state.thumbs.set(state.thumbs() + 1)
            except RecStudioError:
                pass
            finally:
                with self._thumb_lock:
                    self._thumb_pending.discard(asset.path)

        threading.Thread(target=worker, daemon=True, name="thumbstrip").start()

    # -- proxy de preview (B5) ----------------------------------------------------

    def _queue_proxy(self, asset: MediaAsset) -> None:
        """Genera un proxy 540p en un hilo para videos pesados (4K/60+); el export usa el original."""
        if asset.kind != "video" or not media.is_heavy(asset) or media.is_offline(asset):
            return
        if media.cached_proxy(asset.path, rotation=asset.rotation) is not None:
            state.proxies.set(state.proxies() + 1)
            return
        with self._proxy_lock:
            if asset.path in self._proxy_pending:
                return
            self._proxy_pending.add(asset.path)

        def worker() -> None:
            try:
                media.proxy(asset.path, rotation=asset.rotation)
                state.proxies.set(state.proxies() + 1)
            except RecStudioError:
                pass
            finally:
                with self._proxy_lock:
                    self._proxy_pending.discard(asset.path)

        threading.Thread(target=worker, daemon=True, name="proxy").start()

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
