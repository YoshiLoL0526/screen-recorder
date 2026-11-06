"""Elementos superpuestos al video (v6, docs/22 y docs/25).

Este módulo implementa la composición anunciada por la base v6. Las firmas originales se
mantienen y solo se añade un parámetro opcional `sources` (`element.id → PNG ya generado`):

- `apply_graph(project, video_out, chains, inputs, size, *, first_input, sources=None,
  video_inputs=None)` añade una entrada por elemento, la cadena `scale/rotate/colorchannelmixer`
  y el `overlay` con `enable` al filter graph. Devuelve `(etiqueta_de_video, próxima_entrada)`;
  si se pasa `video_inputs`, deja ahí `element.id → índice de su entrada` para que el audio del
  PiP reutilice la misma entrada en vez de abrir el archivo dos veces. `prefix` evita colisiones
  de etiquetas cuando se compone en dos pasadas (V2/V3 antes de los efectos globales y la capa
  «Elementos» después).
- `apply_frame(project, t, rgb, *, sources=None)` compone en PIL el mismo PNG, con la misma
  rotación, opacidad y posición. El preview pasa las rutas de `services.element_assets`.

`Project.elements` guarda el z-order (el último va encima). `graph`/`playback` hacen dos pasadas:
V2/V3 antes de los efectos globales y la capa «Elementos» (imágenes, figuras y PiP suelto)
después; los textos van al final. El blur se hornea en el PNG (opción (a), docs/25): FFmpeg solo
rota y mezcla alfa, de modo que preview y export usan los mismos píxeles.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Mapping

import numpy as np
from PIL import Image, ImageOps

from . import animation as anim
from . import elements as el
from .errors import ProjectError
from .project import Project
from .timeline import total_duration

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _num(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def apply_graph(
    project: Project,
    video_out: str,
    chains: list[str],
    inputs: list[str],
    size: tuple[int, int],
    *,
    first_input: int,
    sources: Mapping[str, str] | None = None,
    video_inputs: dict[str, int] | None = None,
    prefix: str = "",
) -> tuple[str, int]:
    """Añade los elementos activos al filter graph (no-op si no hay ninguno)."""
    visible = [e for e in project.elements if el.visible_in_timeline(project, e)]
    if not visible:
        return video_out, first_input
    if sources is None and any(e.kind != "video" for e in visible):
        raise ProjectError(
            "faltan las imágenes de los elementos: usa services.element_assets.materialize"
        )
    total = total_duration(project)
    if total <= 0:
        return video_out, first_input
    next_input = int(first_input)
    label = video_out
    for index, element in enumerate(visible):
        layout = el.layout(project, element, size)
        prepared_w, prepared_h = el.prepared_size(layout)
        fps = max(1, int(project.fps))
        steps: list[str]
        if element.kind == "video":
            asset = project.asset(element.asset_id or "")
            duration = element.duration
            inputs += [
                "-ss", _num(element.in_), "-t", _num(duration + 0.2), "-i", asset.path,
            ]
            steps = [
                "format=rgba",
                f"setpts=PTS-STARTPTS+{_num(element.start)}/TB",
                f"fps={fps}",
            ]
            if element.fit == "fill":
                steps += [
                    f"scale={layout.scale_width}:{layout.scale_height}",
                    f"crop={layout.draw_width}:{layout.draw_height}",
                ]
            else:
                steps += [f"scale={prepared_w}:{prepared_h}"]
        else:
            path = sources.get(element.id) if sources is not None else None
            if not path or not Path(path).is_file():
                raise ProjectError(f"falta la imagen del elemento {element.id} ({element.kind})")
            inputs += [
                "-loop", "1", "-framerate", str(fps), "-t", _num(total), "-i", str(path),
            ]
            steps = [
                "format=rgba",
                f"scale={prepared_w}:{prepared_h}",
            ]
        steps.append("setsar=1")
        source = next_input
        next_input += 1
        if element.kind == "video" and video_inputs is not None:
            video_inputs[element.id] = source
        rotation = float(element.rotation) % 360.0
        if abs(rotation) > 1e-6:
            angle = rotation * 3.141592653589793 / 180.0
            steps.append(
                f"rotate={_num(angle)}:ow=rotw({_num(angle)}):oh=roth({_num(angle)}):c=none"
            )
        if element.opacity < 0.999999:
            steps.append(f"colorchannelmixer=aa={_num(element.opacity)}")
        prepared = f"{prefix}el{index}"
        chains.append(f"[{source}:v]{','.join(steps)}[{prepared}]")
        # `between` documenta el intervalo; el `lt` extra excluye el frame exacto del final, que
        # el preview tampoco pinta (`start <= t < end`).
        enable = (
            f"between(t,{_num(element.start)},{_num(element.end)})"
            f"*lt(t,{_num(element.end)})"
        )
        out = f"{prefix}ov{index}"
        eof = ":eof_action=pass" if element.kind == "video" else ""
        x_animated = anim.is_animated(element.animations, "x")
        y_animated = anim.is_animated(element.animations, "y")
        x_value = anim.expr(element.animations, "x", element.start, element.x)
        y_value = anim.expr(element.animations, "y", element.start, element.y)
        x_part = f"main_w*{x_value}-overlay_w/2"
        y_part = f"main_h*{y_value}-overlay_h/2"
        if x_animated:
            x_part = f"'{x_part}'"
        if y_animated:
            y_part = f"'{y_part}'"
        chains.append(
            f"[{label}][{prepared}]overlay="
            f"x={x_part}:y={y_part}:"
            f"format=auto{eof}:enable='{enable}'[{out}]"
        )
        label = out
    return label, next_input


def _resize_fit(image: Image.Image, layout: el.ElementLayout, fit: str) -> Image.Image:
    """Encaja una imagen original en la caja del layout (fallback sin PNG materializado)."""
    if fit == "fill":
        image = image.resize(
            (max(1, layout.scale_width), max(1, layout.scale_height)),
            Image.Resampling.LANCZOS,
        )
        left = max(0, (image.width - layout.draw_width) // 2)
        top = max(0, (image.height - layout.draw_height) // 2)
        return image.crop((left, top, left + layout.draw_width, top + layout.draw_height))
    return image.resize((max(1, layout.draw_width), max(1, layout.draw_height)), Image.Resampling.LANCZOS)


def rotate_rgba(image: Image.Image, degrees: float) -> Image.Image:
    """Rota una imagen RGBA en grados horarios, con el tamaño de `rotated_size`."""
    degrees = float(degrees) % 360.0
    if degrees <= 1e-6:
        return image
    quarter = round(degrees / 90.0)
    if abs(degrees - quarter * 90.0) < 1e-6:
        transpose = {
            1: Image.Transpose.ROTATE_270,  # 90° horario
            2: Image.Transpose.ROTATE_180,
            3: Image.Transpose.ROTATE_90,  # 270° horario
        }
        return image.transpose(transpose[quarter % 4])
    # PIL rota en sentido antihorario; el modelo y FFmpeg usan grados horarios.
    rotated = image.rotate(-degrees, expand=True, resample=Image.Resampling.BICUBIC)
    # `expand=True` redondea como techo y FFmpeg (rotw/roth) redondea al más cercano:
    # se ajusta el lienzo al tamaño del export para que la caja y el overlay coincidan.
    target_w, target_h = el.rotated_size(image.width, image.height, degrees)
    if (rotated.width, rotated.height) != (target_w, target_h):
        canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
        canvas.paste(rotated, ((target_w - rotated.width) // 2, (target_h - rotated.height) // 2),
                     rotated)
        rotated = canvas
    return rotated


@lru_cache(maxsize=64)
def _prepare(
    path: str,
    mtime_ns: int,
    draw_width: int,
    draw_height: int,
    scale_width: int,
    scale_height: int,
    fit: str,
    prepared: bool,
    rotation: float,
    opacity: float,
) -> Image.Image | None:
    """Carga (una vez) y deja el elemento listo para pegar: encaje, alfa y rotación."""
    try:
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGBA")
    except (OSError, ValueError):
        return None
    if not prepared:
        # Layout auxiliar solo para reutilizar el encaje (el layout real se descarta).
        proxy = el.ElementLayout(
            canvas_width=0, canvas_height=0, center_x=0, center_y=0,
            box_width=draw_width, box_height=draw_height,
            draw_width=draw_width, draw_height=draw_height,
            scale_width=scale_width, scale_height=scale_height,
            source_width=image.width, source_height=image.height,
            rotation=rotation, opacity=opacity, blur=0.0, fit=fit,
        )
        image = _resize_fit(image, proxy, fit)
    # Un PNG materializado ya tiene el tamaño de dibujo **más el margen del blur**; no se toca.
    if opacity < 0.999999:
        alpha = image.getchannel("A").point(lambda value: int(round(value * opacity)))
        image.putalpha(alpha)
    return rotate_rgba(image, rotation)


def _source_for(
    project: Project, element, sources: Mapping[str, str] | None,
) -> tuple[str, bool] | None:  # noqa: ANN001
    if sources is not None and element.id in sources:
        return sources[element.id], True
    if element.kind == "image" and element.asset_id:
        try:
            return project.asset(element.asset_id).path, False
        except ProjectError:
            return None
    return None


def apply_frame(
    project: Project,
    t: float,
    rgb,
    *,
    sources: Mapping[str, str] | None = None,
    video_images: Mapping[str, Image.Image] | None = None,
):
    """Compone los elementos activos sobre un frame RGB del preview.

    `video_images` trae las imágenes RGBA ya decodificadas de los elementos `kind="video"` (las
    prepara `services.playback.FrameRenderer` en el tamaño del preview).
    """
    active = el.active(project, t)
    if not active:
        return rgb
    height, width = rgb.shape[:2]
    canvas = Image.fromarray(np.ascontiguousarray(rgb)).convert("RGBA")
    painted = False
    for element in active:
        element = el.effective(element, t)
        layout = el.layout(project, element, (width, height))
        if element.kind == "video":
            image = (video_images or {}).get(element.id)
            if image is None:
                continue
            x = int(round(layout.center_x - image.width / 2.0))
            y = int(round(layout.center_y - image.height / 2.0))
            canvas.paste(image, (x, y), image)
            painted = True
            continue
        source = _source_for(project, element, sources)
        if source is None:
            continue
        path, prepared = source
        try:
            stat = os.stat(path)
        except OSError:
            continue
        image = _prepare(
            path, stat.st_mtime_ns, layout.draw_width, layout.draw_height,
            layout.scale_width, layout.scale_height, layout.fit, prepared,
            layout.rotation, layout.opacity,
        )
        if image is None:
            continue
        x = int(round(layout.center_x - image.width / 2.0))
        y = int(round(layout.center_y - image.height / 2.0))
        canvas.paste(image, (x, y), image)
        painted = True
    if not painted:
        return rgb
    return np.asarray(canvas.convert("RGB"))


__all__ = ["PNG_SIGNATURE", "apply_frame", "apply_graph", "rotate_rgba"]
