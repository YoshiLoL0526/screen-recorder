"""Bake de textos con escala/rotación animadas (docs/35).

`drawtext` no permite rotar el texto por frame ni cambiar `fontsize` con la fiabilidad del
rasterizado de PIL. Por eso el export sigue la misma estrategia que los elementos animados
(docs/33): el texto se compone frame a frame con la **misma** función que usa el preview
(`services.playback.text_rgba`), se codifica a un vídeo RGBA `qtrle` y se superpone con `overlay`
en `(x, y)`, con la opacidad ya horneada.

- Solo se hornean los textos con claves de `scale` o `rotation`. Los demás siguen usando
  `drawtext`, y un proyecto sin esos canales conserva las cadenas del graph byte a byte.
- El lienzo del `.mov` es el máximo que necesita el texto en todo su intervalo (escala y rotación
  incluidas), porque `overlay` exige un tamaño constante.
- Los intermedios viven en el directorio temporal del export y se borran con él.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from ..core import animation as anim
from ..core import overlays
from ..core.errors import MediaError
from ..core.project import Project, TextOverlay
from ..core.timeline import total_duration
from ..platform import ffmpeg as F
from . import playback
from .rgba_video import encoder_args, safe_name

GEOMETRY_CHANNELS = anim.TRANSFORM_CHANNELS


def needs_bake(overlay: TextOverlay) -> bool:
    """¿El texto tiene claves de escala o rotación? (x/y/opacidad solos no necesitan bake)."""
    return any(anim.is_animated(overlay.animations, channel) for channel in GEOMETRY_CHANNELS)


def _visible_in_timeline(project: Project, overlay: TextOverlay) -> bool:
    total = total_duration(project)
    return total > 0 and float(overlay.start) < total and float(overlay.end) > 0


def _frame_count(overlay: TextOverlay, project: Project, fps: int) -> int:
    total = total_duration(project)
    end = min(float(overlay.end), total)
    duration = max(0.0, end - float(overlay.start))
    # `ceil` cubre el último frame cuando la duración no es múltiplo de 1/fps; el `enable` del
    # overlay excluye el frame exacto del final, igual que el preview (`start <= t < end`).
    return max(1, int(math.ceil(duration * max(1, fps) - 1e-9)))


def _bake(
    project: Project,
    overlay: TextOverlay,
    size: tuple[int, int],
    *,
    fps: int,
    folder: Path,
) -> overlays.TextClip:
    frames = _frame_count(overlay, project, fps)
    target = folder / f"text-{safe_name(overlay.id)}.mov"
    log = folder / f"text-{safe_name(overlay.id)}.log"

    # 1) Lienzo máximo del intervalo: el overlay necesita un tamaño constante.
    width = height = 1
    for index in range(frames):
        t = overlay.start + index / fps
        extent_w, extent_h = playback.text_extent(
            overlay, t, size, project_height=project.height,
        )
        width, height = max(width, extent_w), max(height, extent_h)

    # 2) Frames RGBA por stdin → qtrle con alfa.
    proc = F.run_capture_process(
        encoder_args(F.ffmpeg_exe(), width, height, fps, target), log,
    )
    stdin = proc.stdin
    assert stdin is not None
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    try:
        for index in range(frames):
            t = overlay.start + index / fps
            image = playback.text_rgba(overlay, t, size, project_height=project.height)
            canvas.paste((0, 0, 0, 0), (0, 0, width, height))
            if image is not None:
                # Sin máscara: el lienzo ya está vacío y `paste` con la propia imagen como
                # máscara multiplicaría el alfa dos veces.
                canvas.paste(
                    image,
                    ((width - image.width) // 2, (height - image.height) // 2),
                )
            stdin.write(canvas.tobytes())
        stdin.close()
        code = proc.wait(timeout=600)
    except Exception:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        raise
    if code != 0 or not target.is_file() or target.stat().st_size == 0:
        detail = F.stderr_tail(log, lines=8)
        raise MediaError(
            "no se pudo generar el vídeo animado del texto"
            + (f": {detail.splitlines()[-1]}" if detail else "")
        )
    return overlays.TextClip(
        path=str(target.resolve()),
        width=width,
        height=height,
        frames=frames,
        duration=frames / fps,
    )


def materialize(
    project: Project,
    size: tuple[int, int],
    *,
    fps: int,
    folder: str | Path,
) -> dict[str, overlays.TextClip]:
    """Hornea los textos con escala/rotación animadas al tamaño de salida `size`.

    `folder` debe ser el directorio temporal del export: los `.mov` se borran con él.
    """
    animated = [
        overlay for overlay in project.texts
        if needs_bake(overlay) and _visible_in_timeline(project, overlay)
    ]
    if not animated:
        return {}
    directory = Path(folder)
    directory.mkdir(parents=True, exist_ok=True)
    return {
        overlay.id: _bake(
            project, overlay, size, fps=max(1, int(fps)), folder=directory,
        )
        for overlay in animated
    }


__all__ = ["GEOMETRY_CHANNELS", "materialize", "needs_bake"]
