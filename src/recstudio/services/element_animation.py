"""Bake de elementos con escala/rotación animadas (docs/33).

El export no puede cambiar el tamaño de salida de un filtro `scale`/`rotate` por frame y
mantener el `overlay` centrado sin recortes. La solución robusta es hornear el elemento a un
vídeo RGBA intermedio:

1. Se calcula el lienzo máximo que necesita el elemento en todo su intervalo (evaluando
   `core.elements.effective`/`layout` frame a frame).
2. Cada frame se compone con PIL exactamente igual que el preview: PNG materializado (o frame
   decodificado del PiP), escala, opacidad y rotación, centrado en ese lienzo fijo.
3. Los frames RGBA se envían por stdin a FFmpeg y se codifican con QuickTime Animation
   (`qtrle`), que conserva el alfa sin pérdidas.
4. `core.overlays.apply_graph` superpone el vídeo con las mismas expresiones de X/Y que antes y
   `eof_action=pass`.

Ventaja: preview y export comparten la geometría y el rasterizado (PIL). Coste: un decode PIL por
frame del elemento, aceptable porque solo se hace para elementos con claves de escala/rotación y
el resultado vive en el directorio temporal del export (se borra con él).
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import Image

from ..core import animation as anim
from ..core import elements as el
from ..core import overlays
from ..core.errors import MediaError
from ..core.project import Element, Project
from ..core.timeline import total_duration
from ..platform import ffmpeg as F
from . import element_assets, playback

GEOMETRY_CHANNELS = ("scale", "rotation")


def needs_bake(element: Element) -> bool:
    """¿El elemento tiene claves de escala o rotación? (x/y solos no necesitan bake)."""
    return any(anim.is_animated(element.animations, channel) for channel in GEOMETRY_CHANNELS)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value) or "element"


def _encoder_args(exe: str, width: int, height: int, fps: int, target: Path) -> list[str]:
    return [
        exe, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}",
        "-r", str(fps), "-i", "-",
        "-an", "-c:v", "qtrle", "-pix_fmt", "argb", str(target),
    ]


def _frame_count(element: Element, project: Project, fps: int) -> int:
    total = total_duration(project)
    end = min(float(element.end), total)
    duration = max(0.0, end - float(element.start))
    # `ceil` cubre el último frame del timeline cuando la duración no es múltiplo de 1/fps
    # (el `enable` ya excluye el frame exacto del final).
    return max(1, int(math.ceil(duration * max(1, fps) - 1e-9)))


def _extent(project: Project, element: Element, size: tuple[int, int], t: float) -> tuple[int, int]:
    """Tamaño de la imagen ya rotada que produce el preview en `t`."""
    effective = el.effective(element, t)
    layout = el.layout(project, effective, size)
    if element.kind == "video":
        width, height = layout.draw_width, layout.draw_height
    else:
        width, height = el.prepared_size(layout)
    return el.rotated_size(width, height, layout.rotation)


def _static_frame(
    base: Image.Image, project: Project, element: Element, size: tuple[int, int], t: float,
) -> Image.Image:
    """Reproduce el paso `scale → alfa → rotate` de `core.overlays._prepare`."""
    effective = el.effective(element, t)
    layout = el.layout(project, effective, size)
    target = el.prepared_size(layout)
    image = base
    if image.size != target:
        image = image.resize(target, Image.Resampling.LANCZOS)
    if layout.opacity < 0.999999:
        alpha = image.getchannel("A").point(lambda value: int(round(value * layout.opacity)))
        image = image.copy()
        image.putalpha(alpha)
    return overlays.rotate_rgba(image, layout.rotation)


def _bake(
    project: Project,
    element: Element,
    size: tuple[int, int],
    *,
    fps: int,
    folder: Path,
    sources: dict[str, str],
) -> overlays.ElementClip:
    frames = _frame_count(element, project, fps)
    target = folder / f"element-{_safe_name(element.id)}.mov"
    base: Image.Image | None = None
    pool: playback.DecoderPool | None = None
    if element.kind == "video":
        pool = playback.DecoderPool(capacity=2)
    else:
        path = sources.get(element.id)
        if not path or not Path(path).is_file():
            raise MediaError(f"falta la imagen del elemento {element.id} para animarlo")
        try:
            with Image.open(path) as opened:
                base = opened.convert("RGBA")
        except (OSError, ValueError) as exc:
            raise MediaError(f"no se pudo leer la imagen del elemento {element.id}: {exc}") from exc

    try:
        # 1) Lienzo máximo del intervalo: el overlay necesita un tamaño constante.
        width = height = 1
        for index in range(frames):
            t = element.start + index / fps
            extent_w, extent_h = _extent(project, element, size, t)
            width, height = max(width, extent_w), max(height, extent_h)

        # 2) Frames RGBA por stdin → qtrle con alfa.
        proc = F.run_capture_process(
            _encoder_args(F.ffmpeg_exe(), width, height, fps, target),
            folder / f"element-{_safe_name(element.id)}.log",
        )
        stdin = proc.stdin
        assert stdin is not None
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        try:
            for index in range(frames):
                t = element.start + index / fps
                if base is not None:
                    image = _static_frame(base, project, element, size, t)
                else:
                    assert pool is not None
                    image = playback.video_element_image(
                        project, element, t, size, pool, 0,
                    )
                    if image is None:
                        raise MediaError(f"no se pudo decodificar el vídeo del elemento {element.id}")
                canvas.paste((0, 0, 0, 0), (0, 0, width, height))
                # Sin máscara: el lienzo ya está vacío y `paste` con la propia imagen como
                # máscara multiplicaría el alfa dos veces (128 → 64).
                canvas.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
                stdin.write(canvas.tobytes())
            stdin.close()
            code = proc.wait(timeout=600)
        except Exception:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            raise
        if code != 0 or not target.is_file() or target.stat().st_size == 0:
            detail = F.stderr_tail(folder / f"element-{_safe_name(element.id)}.log", lines=8)
            raise MediaError(
                "no se pudo generar el vídeo animado del elemento"
                + (f": {detail.splitlines()[-1]}" if detail else "")
            )
        return overlays.ElementClip(
            path=str(target.resolve()),
            width=width,
            height=height,
            frames=frames,
            duration=frames / fps,
        )
    finally:
        if base is not None:
            base.close()
        if pool is not None:
            pool.close()


def materialize(
    project: Project,
    size: tuple[int, int],
    *,
    fps: int,
    folder: str | Path,
    sources: dict[str, str] | None = None,
) -> dict[str, overlays.ElementClip]:
    """Hornea los elementos con escala/rotación animadas al tamaño de salida `size`.

    `folder` debe ser el directorio temporal del export: los `.mov` se borran con él. Si no se
    pasan `sources`, se materializan los PNG base con `services.element_assets`.
    """
    animated = [
        element for element in project.elements
        if needs_bake(element) and el.visible_in_timeline(project, element)
    ]
    if not animated:
        return {}
    directory = Path(folder)
    directory.mkdir(parents=True, exist_ok=True)
    if sources is None:
        sources = element_assets.materialize(project, size)
    return {
        element.id: _bake(
            project, element, size, fps=max(1, int(fps)), folder=directory, sources=sources,
        )
        for element in animated
    }


__all__ = ["GEOMETRY_CHANNELS", "materialize", "needs_bake"]
