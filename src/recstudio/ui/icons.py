"""Iconos SVG como bytes (nítidos y tema-agnósticos: gris neutro sobre cualquier fondo)."""

from __future__ import annotations

_GRAY = "#8A91A8"


def _svg(body: str, *, fill: str | None = None, stroke: str | None = _GRAY, width: float = 1.8) -> bytes:
    stroke_attr = f' stroke="{stroke}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"' if stroke else ""
    fill_attr = f' fill="{fill}"' if fill else ' fill="none"'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"{fill_attr}{stroke_attr}>{body}</svg>'
    ).encode()


RECORD = _svg('<circle cx="12" cy="12" r="7"/>', fill="#FF5470", stroke=None)
LIBRARY = _svg('<rect x="3.5" y="4.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="4.5" width="7" height="7" rx="1.5"/>'
               '<rect x="3.5" y="13.5" width="7" height="6" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="6" rx="1.5"/>')
SETTINGS = _svg('<path d="M4 7h16M4 12h16M4 17h16"/><circle cx="9" cy="7" r="2.2" fill="#0B0D14"/>'
                '<circle cx="15" cy="12" r="2.2" fill="#0B0D14"/><circle cx="8" cy="17" r="2.2" fill="#0B0D14"/>')
SUN = _svg('<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.4M12 19.1v2.4M2.5 12h2.4M19.1 12h2.4'
           'M5 5l1.7 1.7M17.3 17.3L19 19M19 5l-1.7 1.7M6.7 17.3L5 19"/>')
MOON = _svg('<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z"/>')
PLAY = _svg('<path d="M8 5.5l10 6.5-10 6.5z"/>', fill=_GRAY, stroke=None)
FOLDER = _svg('<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>')
TRASH = _svg('<path d="M4.5 7h15M9.5 7V5.2A1.2 1.2 0 0 1 10.7 4h2.6a1.2 1.2 0 0 1 1.2 1.2V7'
             'M6.5 7l.9 11.2A1.8 1.8 0 0 0 9.2 20h5.6a1.8 1.8 0 0 0 1.8-1.8L17.5 7"/>')
CLOSE = _svg('<path d="M6 6l12 12M18 6L6 18"/>')
CHECK = _svg('<path d="M4.5 12.5l5 5 10-11"/>')
ALERT = _svg('<path d="M12 4l9 16H3z"/><path d="M12 10v4.5M12 17.2v.1"/>')
INFO = _svg('<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 7.8v.1"/>')
CAMERA = _svg('<rect x="3" y="6.5" width="18" height="13" rx="2.5"/><circle cx="12" cy="13" r="3.6"/>'
              '<path d="M8.5 6.5L10 4h4l1.5 2.5"/>')

# -- editor (F3) ----------------------------------------------------------------
CUT = _svg('<circle cx="6.5" cy="17" r="2.6"/><circle cx="17.5" cy="17" r="2.6"/>'
           '<path d="M8.4 15.2L17 4.5M15.6 15.2L7 4.5"/>')
PAUSE = _svg('<rect x="6.5" y="5" width="3.6" height="14" rx="1"/><rect x="13.9" y="5" width="3.6" height="14" rx="1"/>',
             fill=_GRAY, stroke=None)
STEP_BACK = _svg('<path d="M15.5 6.5L9 12l6.5 5.5z"/><path d="M7 6v12"/>')
STEP_FWD = _svg('<path d="M8.5 6.5L15 12l-6.5 5.5z"/><path d="M17 6v12"/>')
TO_START = _svg('<path d="M18 6.5L11.5 12 18 17.5z"/><path d="M11 6.5L4.5 12 11 17.5z"/>')
TO_END = _svg('<path d="M6 6.5l6.5 5.5L6 17.5z"/><path d="M13 6.5l6.5 5.5-6.5 5.5z"/>')
UNDO = _svg('<path d="M9 14L4.5 9.5 9 5"/><path d="M5 9.5h9a5 5 0 0 1 0 10h-3"/>')
REDO = _svg('<path d="M15 14l4.5-4.5L15 5"/><path d="M19 9.5h-9a5 5 0 0 0 0 10h3"/>')
SAVE = _svg('<path d="M5 4.5h11l3.5 3.5v11a1.5 1.5 0 0 1-1.5 1.5H6a1.5 1.5 0 0 1-1.5-1.5V6A1.5 1.5 0 0 1 6 4.5z"/>'
            '<path d="M8 4.5v5h7v-5M8 20v-6h8v6"/>')
IMPORT = _svg('<path d="M12 4v11M7.5 10.5L12 15l4.5-4.5"/><path d="M4.5 16v2.5A1.5 1.5 0 0 0 6 20h12a1.5 1.5 0 0 0 1.5-1.5V16"/>')
EXPORT = _svg('<path d="M12 15V4M7.5 8.5L12 4l4.5 4.5"/><path d="M4.5 14v4.5A1.5 1.5 0 0 0 6 20h12a1.5 1.5 0 0 0 1.5-1.5V14"/>')
ZOOM_IN = _svg('<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5M8 10.5h5M10.5 8v5"/>')
ZOOM_OUT = _svg('<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5M8 10.5h5"/>')
FIT = _svg('<path d="M4 9V5.5A1.5 1.5 0 0 1 5.5 4H9M15 4h3.5A1.5 1.5 0 0 1 20 5.5V9M20 15v3.5a1.5 1.5 0 0 1-1.5 1.5H15'
           'M9 20H5.5A1.5 1.5 0 0 1 4 18.5V15"/>')
MAGNET = _svg('<path d="M6 4.5v7a6 6 0 0 0 12 0v-7h-3.5v7a2.5 2.5 0 0 1-5 0v-7z"/><path d="M6 8h3.5M14.5 8H18"/>')
SPEAKER = _svg('<path d="M4.5 9.5h3l4.5-4v13l-4.5-4h-3z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>')
MUTE = _svg('<path d="M4.5 9.5h3l4.5-4v13l-4.5-4h-3z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>')
