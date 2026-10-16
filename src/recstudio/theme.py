"""Tema de la app: tokens propios sobre los de Aurora (ver docs/05-ui-aurora.md)."""

from __future__ import annotations

from aurora import DAYLIGHT, MIDNIGHT, Theme

_EXTRA_DARK = {
    "rec": "#FF5470",
    "rec_hover": "#FF6B84",
    "rec_pressed": "#E63E5C",
    "track_bg": "#10131C",
    "clip": "#2A3350",
    "clip_audio": "#1F4B45",
    "wave": "#5EEAD4",
    "playhead": "#FF5470",
    "grid": "#1B2030",
}

_EXTRA_LIGHT = {
    "rec": "#E63E5C",
    "rec_hover": "#FF6B84",
    "rec_pressed": "#C9324C",
    "track_bg": "#EDEFF5",
    "clip": "#C9D2EA",
    "clip_audio": "#BFE6DC",
    "wave": "#0E9384",
    "playhead": "#E63E5C",
    "grid": "#DFE3EE",
}

STUDIO_DARK = Theme(
    colors={**MIDNIGHT.colors, **_EXTRA_DARK},
    radius=MIDNIGHT.radius,
    font_family="Inter",
)

STUDIO_LIGHT = Theme(
    colors={**DAYLIGHT.colors, **_EXTRA_LIGHT},
    radius=DAYLIGHT.radius,
    font_family="Inter",
)

THEMES = {"dark": STUDIO_DARK, "light": STUDIO_LIGHT}


def theme_by_name(name: str) -> Theme:
    return THEMES.get(name, STUDIO_DARK)
