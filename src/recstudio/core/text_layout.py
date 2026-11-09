"""Ajuste de línea de textos (v15, docs/45): puro, determinista y sin PIL.

`wrap_text` convierte un texto en las líneas que se dibujan en el preview y se hornean en el
export. Respeta los `\\n` explícitos, colapsa los espacios dentro de cada párrafo y parte por
palabras; una palabra que no cabe en una línea se rompe por caracteres.

El ancho se estima con una tabla de anchos relativos de Arial (em) para no depender de PIL en
`core/`. La estimación no es una medida tipográfica exacta, pero preview y export usan **este
mismo** helper, así que el número y el contenido de las líneas coinciden siempre. El ancho real
puede diferir en unos píxeles del máximo (`wrap_width * ancho del lienzo`).
"""

from __future__ import annotations

LINE_HEIGHT = 1.15  # interlineado en múltiplos del tamaño de fuente
TEXT_WRAP_MIN = 0.1  # fracción mínima del lienzo si `wrap_width > 0`

_SPACE_WIDTH = 0.28
_NARROW = frozenset("ilI.,:;!'|`")
_WIDE = frozenset("MW@%&")
_DIGITS = frozenset("0123456789")


def _char_width(char: str) -> float:
    """Ancho relativo (em) aproximado de un carácter en Arial."""
    if char == " ":
        return _SPACE_WIDTH
    if char in _NARROW:
        return 0.25
    if char in _WIDE:
        return 0.85
    if char in _DIGITS:
        return 0.56
    if char.isupper():
        return 0.68
    return 0.52


def estimate_width(text: str, size: float) -> float:
    """Ancho estimado de `text` en píxeles del proyecto (sin contorno ni márgenes)."""
    try:
        size = max(0.0, float(size))
    except (TypeError, ValueError):
        return 0.0
    return sum(_char_width(char) for char in text) * size


def _paragraphs(text: str) -> list[str]:
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    return [" ".join(part.split()) for part in normalized.split("\n")]


def _split_long_word(word: str, max_width: float, size: float) -> list[str]:
    """Parte una palabra que no cabe en `max_width` en trozos que sí quepan."""
    chunks: list[str] = []
    current = ""
    for char in word:
        candidate = current + char
        if current and estimate_width(candidate, size) > max_width:
            chunks.append(current)
            current = char
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _wrap_paragraph(paragraph: str, max_width: float, size: float) -> list[str]:
    if not paragraph:
        return [""]
    lines: list[str] = []
    current = ""
    for word in paragraph.split(" "):
        if not current:
            pieces = (
                _split_long_word(word, max_width, size)
                if estimate_width(word, size) > max_width
                else [word]
            )
            lines.extend(pieces[:-1])
            current = pieces[-1]
            continue
        candidate = f"{current} {word}"
        if estimate_width(candidate, size) <= max_width:
            current = candidate
            continue
        lines.append(current)
        pieces = (
            _split_long_word(word, max_width, size)
            if estimate_width(word, size) > max_width
            else [word]
        )
        lines.extend(pieces[:-1])
        current = pieces[-1]
    if current:
        lines.append(current)
    return lines


def wrap_text(
    text: str, *, size: float, wrap_width: float, project_width: float,
) -> tuple[str, ...]:
    """Líneas visibles de `text` para un ancho máximo `wrap_width` del lienzo.

    - `wrap_width <= 0` (o un proyecto sin ancho) no ajusta: solo respeta los `\\n` y colapsa
      los espacios.
    - `wrap_width` es una fracción del ancho del lienzo; el máximo en píxeles es
      `wrap_width * project_width`.
    - Una línea vacía entre dos `\\n` se conserva como línea en blanco (ocupa su interlineado).
    """
    try:
        size = max(0.0, float(size))
        project_width = max(0.0, float(project_width))
        factor = float(wrap_width)
    except (TypeError, ValueError):
        return tuple(_paragraphs(text))
    paragraphs = _paragraphs(text)
    if factor <= 0.0 or size <= 0.0 or project_width <= 0.0:
        return tuple(paragraphs)
    max_width = min(1.0, factor) * project_width
    if max_width <= 0.0:
        return tuple(paragraphs)
    lines: list[str] = []
    for paragraph in paragraphs:
        lines.extend(_wrap_paragraph(paragraph, max_width, size))
    return tuple(lines)


def breaks_lines(overlay) -> bool:  # noqa: ANN001
    """¿El texto necesita el rasterizado multilínea (wrap o `\\n`/`\\r` explícito)?"""
    return (
        float(getattr(overlay, "wrap_width", 0.0)) > 0.0
        or "\n" in overlay.text
        or "\r" in overlay.text
    )


__all__ = [
    "LINE_HEIGHT",
    "TEXT_WRAP_MIN",
    "breaks_lines",
    "estimate_width",
    "wrap_text",
]
