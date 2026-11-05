"""Rasterización y cache de los elementos superpuestos (docs/25).

El core no genera imágenes: esta capa toma un `Element`, calcula su geometría con
`core.elements.layout` y deja un PNG RGBA en la cache con **escritura atómica** (`tmp` +
`os.replace`), igual que las miniaturas. `core/overlays.py` recibe después esas rutas ya
generadas.

Decisiones:
- Se materializa **todo** elemento (figuras e imágenes) al tamaño de dibujo exacto del lienzo
  pedido (preview o export), con el encaje `fit`/`fill`/`stretch` y el blur ya aplicados. Así el
  PNG que consume el preview es literalmente el que consume FFmpeg y no hay dos implementaciones
  del encaje ni del desenfoque.
- El blur se hornea con `PIL.ImageFilter.GaussianBlur` (opción (a) de docs/22): FFmpeg solo tiene
  que rotar y aplicar la opacidad. Se comprobó un export real; ver docs/25.
- La clave de cache incluye tipo/color/tamaño/blur para figuras y ruta+mtime+tamaño+encaje para
  imágenes. Nada de `write_bytes` directo: la UI puede leer mientras se genera.
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ..core import elements as el
from ..core.elements import ElementLayout
from ..core.errors import MediaError
from ..core.project import Element, Project
from ..platform import paths

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_lock = threading.Lock()
_ready: dict[str, Path] = {}


def _cache_dir() -> Path:
    folder = paths.cache_dir() / "elements"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _stat_key(path: Path) -> str:
    stat = path.stat()
    return f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"


def _key(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _looks_like_png(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(len(PNG_SIGNATURE)) == PNG_SIGNATURE
    except OSError:
        return False


def _write_atomic(path: Path, image: Image.Image) -> None:
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    try:
        image.save(tmp, "PNG")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _cached(key: str, build) -> Path:  # noqa: ANN001, ANN202
    """Devuelve el PNG de `key`, generándolo con `build()` si falta o está corrupto."""
    folder = _cache_dir()
    ready_key = f"{folder}|{key}"
    with _lock:
        ready = _ready.get(ready_key)
    if ready is not None and ready.is_file() and _looks_like_png(ready):
        return ready
    target = folder / f"{key}.png"
    if target.is_file() and target.stat().st_size > len(PNG_SIGNATURE) and _looks_like_png(target):
        with _lock:
            _ready[ready_key] = target
        return target
    image = build()
    try:
        _write_atomic(target, image)
    except OSError as exc:
        raise MediaError(f"no se pudo escribir la cache de elementos: {exc}") from exc
    with _lock:
        _ready[ready_key] = target
    return target


def _supersample(width: int, height: int) -> int:
    """Factor de dibujo para antialias sin disparar la memoria en 4K."""
    largest = max(width, height)
    if largest * 4 <= 4096:
        return 4
    if largest * 2 <= 8192:
        return 2
    return 1


def _shape_image(kind: str, color: str, width: int, height: int, blur: float) -> Image.Image:
    if kind not in el.SHAPE_KINDS:
        raise MediaError(f"figura desconocida: {kind!r}")
    from ..core.transform import hex_to_rgb

    red, green, blue = hex_to_rgb(color)
    width, height = max(1, int(width)), max(1, int(height))
    pad = el.image_padding(blur)
    factor = _supersample(width + 2 * pad, height + 2 * pad)
    big = Image.new("RGBA", ((width + 2 * pad) * factor, (height + 2 * pad) * factor), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    left, top = pad * factor, pad * factor
    box = (left, top, left + width * factor - 1, top + height * factor - 1)
    fill = (red, green, blue, 255)
    if kind == "rect":
        draw.rectangle(box, fill=fill)
    elif kind == "ellipse":
        draw.ellipse(box, fill=fill)
    else:  # triangle
        draw.polygon(
            [
                (left + width * factor / 2.0, top),
                (left + width * factor - 1, top + height * factor - 1),
                (left, top + height * factor - 1),
            ],
            fill=fill,
        )
    if blur > 0:
        big = big.filter(ImageFilter.GaussianBlur(blur * factor))
    prepared_w, prepared_h = width + 2 * pad, height + 2 * pad
    if factor > 1:
        big = big.resize((prepared_w, prepared_h), Image.Resampling.LANCZOS)
    return big


def _image_image(path: Path, layout: ElementLayout, fit: str, blur: float) -> Image.Image:
    try:
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGBA")
    except (OSError, ValueError) as exc:
        raise MediaError(f"no se pudo leer la imagen «{path.name}»: {exc}") from exc
    draw_w, draw_h = layout.draw_width, layout.draw_height
    if fit == "fill":
        scale_w, scale_h = layout.scale_width, layout.scale_height
        image = image.resize((max(1, scale_w), max(1, scale_h)), Image.Resampling.LANCZOS)
        left = max(0, (image.width - draw_w) // 2)
        top = max(0, (image.height - draw_h) // 2)
        image = image.crop((left, top, left + draw_w, top + draw_h))
    else:
        image = image.resize((max(1, draw_w), max(1, draw_h)), Image.Resampling.LANCZOS)
    pad = el.image_padding(blur)
    if blur > 0:
        padded = Image.new("RGBA", (draw_w + 2 * pad, draw_h + 2 * pad), (0, 0, 0, 0))
        padded.paste(image, (pad, pad), image)
        image = padded.filter(ImageFilter.GaussianBlur(blur))
    return image


def shape_png(element: Element, layout: ElementLayout) -> Path:
    """PNG RGBA de una figura al tamaño de dibujo, con blur horneado si lo tiene."""
    prepared_w, prepared_h = el.prepared_size(layout)
    key = _key(
        "v2-shape", element.kind, element.color.upper(),
        f"{prepared_w}x{prepared_h}", f"{layout.blur:.3f}",
    )
    return _cached(
        key,
        lambda: _shape_image(element.kind, element.color, layout.draw_width, layout.draw_height, layout.blur),
    )


def image_png(path: str | Path, element: Element, layout: ElementLayout) -> Path:
    """PNG RGBA de una imagen encajada al tamaño de dibujo, con blur horneado."""
    source = Path(path)
    try:
        source_key = _stat_key(source)
    except OSError as exc:
        raise MediaError(f"no se pudo leer la imagen «{source.name}»: {exc}") from exc
    prepared_w, prepared_h = el.prepared_size(layout)
    key = _key(
        "v2-image", source_key, element.fit,
        f"{layout.draw_width}x{layout.draw_height}",
        f"{layout.scale_width}x{layout.scale_height}",
        f"{prepared_w}x{prepared_h}", f"{layout.blur:.3f}",
    )
    return _cached(
        key,
        lambda: _image_image(source, layout, element.fit, layout.blur),
    )


def materialize(project: Project, size: tuple[int, int]) -> dict[str, str]:
    """Rutas PNG listas para componer, por id de elemento (orden de `Project.elements`).

    Solo se materializan los elementos que llegan a verse en el timeline. Si un asset de imagen
    falta, la excepción sube: el export debe fallar en vez de perder el elemento en silencio.
    """
    sources: dict[str, str] = {}
    for element in project.elements:
        if not el.visible_in_timeline(project, element):
            continue
        layout = el.layout(project, element, size)
        if element.kind == "image":
            asset = project.asset(element.asset_id or "")
            path = image_png(asset.path, element, layout)
        else:
            path = shape_png(element, layout)
        sources[element.id] = str(path.resolve())
    return sources
