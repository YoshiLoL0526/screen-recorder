"""Subtítulos SRT/VTT (v9, docs/30): parser puro y conversión a `TextOverlay`.

- Acepta SRT (`HH:MM:SS,mmm`) y WebVTT (`HH:MM:SS.mmm`), con BOM, CRLF, índices y ajustes de
  cue (`align:start position:10%`). Los bloques `NOTE`/`STYLE`/`REGION` de VTT se ignoran.
- Junta las líneas de un cue con un espacio (el modelo de texto es de una línea) y limpia etiquetas
  HTML/VTT (`<i>`, `{\\an8}`) para que el `drawtext` no las muestre.
- `cues_to_texts` crea los `TextOverlay` con estilo de subtítulo; el controlador los recorta al
  timeline y los aplica en una sola entrada de undo.
- `format_srt` es el inverso para exportar los textos marcados como subtítulo.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from .project import TextOverlay, new_id

_TIME = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})"
_TIMESTAMP = re.compile(rf"^\s*{_TIME}\s*-->\s*{_TIME}")
_TAG = re.compile(r"<[^>]*>|\{[^}]*\}")
_SUBTITLE_DEFAULTS = {
    "x": 0.5,
    "y": 0.88,
    "size": 44,
    "color": "#FFFFFF",
    "bold": True,
}


@dataclass(frozen=True)
class SubtitleCue:
    start: float
    end: float
    text: str


def _seconds(hours: str | None, minutes: str, seconds: str, millis: str) -> float:
    scale = 10 ** (3 - len(millis))
    return (
        (int(hours or 0) * 3600)
        + int(minutes) * 60
        + int(seconds)
        + int(millis) * scale / 1000.0
    )


def _clean_text(lines: list[str]) -> str:
    text = " ".join(line.strip() for line in lines if line.strip())
    text = _TAG.sub("", text)
    text = html.unescape(text)
    return " ".join(text.split())


def parse_subtitles(data: str) -> list[SubtitleCue]:
    """Cues ordenadas de un SRT/VTT. Un archivo vacío o sin cues válidos devuelve `[]`."""
    text = data.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    lines = text.split("\n")
    if lines and lines[0].strip().upper().startswith("WEBVTT"):
        lines = lines[1:]
        # La cabecera VTT puede traer metadatos hasta la primera línea en blanco.
        while lines and lines[0].strip():
            lines.pop(0)
    blocks = re.split(r"\n{2,}", "\n".join(lines).strip())
    cues: list[SubtitleCue] = []
    for block in blocks:
        block_lines = block.split("\n")
        if not block_lines:
            continue
        first = block_lines[0].strip().upper()
        if first.startswith(("NOTE", "STYLE", "REGION")):
            continue
        timing_index = next(
            (i for i, line in enumerate(block_lines) if "-->" in line), None
        )
        if timing_index is None:
            continue
        match = _TIMESTAMP.match(block_lines[timing_index])
        if match is None:
            continue
        start = _seconds(*match.groups()[:4])
        end = _seconds(*match.groups()[4:8])
        body = _clean_text(block_lines[timing_index + 1:])
        if not body or end <= start + 1e-6:
            continue
        cues.append(SubtitleCue(round(start, 3), round(end, 3), body))
    cues.sort(key=lambda cue: (cue.start, cue.end))
    return cues


def cues_to_texts(
    cues: list[SubtitleCue], *, total: float = 0.0, x: float = _SUBTITLE_DEFAULTS["x"],
    y: float = _SUBTITLE_DEFAULTS["y"], size: int = _SUBTITLE_DEFAULTS["size"],
    color: str = _SUBTITLE_DEFAULTS["color"], bold: bool = _SUBTITLE_DEFAULTS["bold"],
) -> list[TextOverlay]:
    """Convierte cues en textos subtítulo, recortados al timeline si `total > 0`."""
    texts: list[TextOverlay] = []
    for cue in cues:
        start = max(0.0, cue.start)
        end = cue.end
        if total > 0:
            if start >= total - 1e-6:
                continue
            end = min(end, total)
        if end <= start + 0.05:
            continue
        texts.append(TextOverlay(
            id=new_id("t"), text=cue.text, start=round(start, 4), end=round(end, 4),
            x=x, y=y, size=int(size), color=color, bold=bool(bold), subtitle=True,
        ))
    return texts


def _stamp(seconds: float) -> str:
    millis = max(0, int(round(float(seconds) * 1000)))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_srt(texts: tuple[TextOverlay, ...] | list[TextOverlay]) -> str:
    """SRT de los textos marcados como subtítulo, ordenados por inicio."""
    cues = sorted((text for text in texts if text.subtitle), key=lambda t: (t.start, t.end))
    blocks = [
        f"{index}\n{_stamp(text.start)} --> {_stamp(text.end)}\n{text.text}"
        for index, text in enumerate(cues, start=1)
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


__all__ = [
    "SubtitleCue",
    "cues_to_texts",
    "format_srt",
    "parse_subtitles",
]
