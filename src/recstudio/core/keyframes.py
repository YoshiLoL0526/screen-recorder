"""«Corte rápido» sin recodificar (puro; ver docs/04 → «Corte rápido» y docs/13).

Cuando el proyecto es solo cortes (sin transiciones, efectos, velocidad ni cambios de volumen) se
puede exportar copiando los paquetes: cada clip es un segmento `-c copy` y luego se unen con el
demuxer `concat`. Un segmento copiado solo puede **empezar en un keyframe**: el inicio de cada
clip se imanta al keyframe más cercano (dentro del clip) y se avisa de cuánto se movió. El final
no necesita keyframe (se copia hasta el último paquete antes del corte).

Los keyframes los indexa `services.media.keyframes` (PyAV, `packet.is_keyframe`).
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import effects as fx
from .errors import ProjectError
from .graph import num
from .project import Project

_SEEK_EPS = 0.001  # -ss un pelo después del keyframe: FFmpeg busca el keyframe ≤ t


@dataclass(frozen=True)
class CutSegment:
    clip_id: str
    path: str
    start: float  # inicio real (keyframe) en la fuente
    end: float  # final en la fuente (el `out_` del clip)
    requested: float  # `in_` del clip

    @property
    def shift(self) -> float:
        return self.start - self.requested

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class FastCutPlan:
    segments: tuple[CutSegment, ...]

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)

    @property
    def max_shift(self) -> float:
        return max((abs(s.shift) for s in self.segments), default=0.0)


def blockers(project: Project) -> list[str]:
    """Motivos por los que el proyecto no admite corte rápido (lista vacía ⇒ sí lo admite)."""
    reasons: list[str] = []
    track = project.video
    if not track.clips:
        return ["el timeline está vacío"]
    if any(t is not None for t in track.transitions):
        reasons.append("tiene transiciones")
    if any(abs(c.speed - 1.0) > 1e-9 for c in track.clips):
        reasons.append("hay clips con velocidad distinta de 1×")
    if any(fx.has_effects(c) for c in track.clips):
        reasons.append("hay clips con efectos")
    if track.muted or abs(track.gain - 1.0) > 1e-6:
        reasons.append("el volumen del audio original no es el 100 %")
    if any(t.clips and not t.muted for t in project.audio):
        reasons.append("tiene pistas de audio adicionales")
    by_id = {a.id: a for a in project.assets}
    used = {(by_id[c.asset_id].width, by_id[c.asset_id].height, round(by_id[c.asset_id].fps, 2),
             by_id[c.asset_id].has_audio)
            for c in track.clips if c.asset_id in by_id}
    if len(used) > 1:
        reasons.append("mezcla videos con distinta resolución, fps o audio")
    return reasons


def snap_start(t: float, end: float, keyframes: Sequence[float]) -> float:
    """Keyframe más cercano a `t` que deje el segmento con duración (< `end`)."""
    if not keyframes:
        return 0.0
    i = bisect.bisect_left(keyframes, t)
    candidates = [k for k in (keyframes[i - 1] if i > 0 else None, keyframes[i] if i < len(keyframes) else None)
                  if k is not None and k < end]
    if not candidates:
        before = [k for k in keyframes if k < end]
        return before[-1] if before else keyframes[0]
    return min(candidates, key=lambda k: (abs(k - t), k))


def plan(project: Project, keyframes: Mapping[str, Sequence[float]]) -> FastCutPlan:
    """Segmentos del corte rápido; `keyframes` va por `asset_id` (tiempos ordenados, s)."""
    reasons = blockers(project)
    if reasons:
        raise ProjectError("no se puede exportar sin recodificar: " + "; ".join(reasons))
    segments = []
    for clip in project.video.clips:
        asset = project.asset(clip.asset_id)
        start = snap_start(clip.in_, clip.out_, keyframes.get(asset.id, ()))
        segments.append(CutSegment(clip.id, asset.path, start, clip.out_, clip.in_))
    return FastCutPlan(tuple(segments))


def segment_cmd(exe: str, segment: CutSegment, out_path: str | Path) -> list[str]:
    return [
        exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-ss", num(segment.start + _SEEK_EPS if segment.start > 0 else 0), "-i", segment.path,
        "-t", num(segment.duration),
        "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
        "-avoid_negative_ts", "make_zero", "-f", "mp4", str(out_path),
    ]


def concat_list(paths: Sequence[str | Path]) -> str:
    """Contenido del `list.txt` del demuxer concat (rutas absolutas con comillas escapadas)."""
    lines = []
    for path in paths:
        escaped = str(Path(path).resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def concat_cmd(exe: str, list_path: str | Path, out_path: str | Path, *, progress: bool = True) -> list[str]:
    # `auto_convert=0`: por defecto el demuxer concat mete `h264_mp4toannexb` y los keyframes
    # salen con SPS/PPS en banda (36 bytes más): ya no serían idénticos a los de la fuente.
    args = [
        exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-auto_convert", "0", "-i", str(list_path),
        "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart",
    ]
    if progress:
        args += ["-progress", "pipe:1", "-nostats"]
    return [*args, str(out_path)]
