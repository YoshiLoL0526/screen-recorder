"""Diálogos nativos de archivo y carpeta, sobre `aurora.dialogs`.

Esta capa adapta los diálogos de Aurora a lo que esperan los controladores (rutas `Path` en
vez de `str`) y centraliza los filtros de video. Vive en `ui/` y no en `platform/`: los
diálogos nativos son parte de la UI (Aurora es la librería de UI) y `platform/` encapsula el SO.

Aurora los ofrece **síncronos**: mientras el diálogo está abierto lo corre el sistema con su
propio bucle de mensajes y suelta el GIL, así que la captura, el export y los hilos de trabajo
siguen. La ventana desde la que se llama —su vista o uno de sus manejadores— es el padre del
diálogo (sale modal sobre ella), por eso se llaman **desde el hilo de UI**, en un `on_click`,
y no desde un hilo de trabajo.

En los tests no se puede abrir un diálogo de verdad: se sustituyen por un doble
(`monkeypatch.setattr(file_dialogs, "open_videos", ...)`).
"""

from __future__ import annotations

from pathlib import Path

from aurora import dialogs

#: Filtros de la ventana «Abrir» (pares nombre, extensiones).
VIDEO_FILTERS = (
    ("Videos", ("mp4", "mkv", "mov", "webm", "avi")),
    ("Todos los archivos", ("*",)),
)
IMAGE_FILTERS = (
    ("Imágenes", ("png", "jpg", "jpeg", "webp", "bmp")),
    ("Todos los archivos", ("*",)),
)
MEDIA_FILTERS = (
    ("Videos e imágenes", ("mp4", "mkv", "mov", "webm", "avi", "png", "jpg", "jpeg", "webp", "bmp")),
    ("Audio", ("mp3", "wav", "m4a", "aac", "flac", "ogg")),
    ("Todos los archivos", ("*",)),
)
SUBTITLE_FILTERS = (
    ("Subtítulos", ("srt", "vtt")),
    ("Todos los archivos", ("*",)),
)
SRT_FILTERS = (
    ("Subtítulos SRT", ("srt",)),
    ("Todos los archivos", ("*",)),
)


def open_media(initial_dir: str | Path | None = None, *, title: str = "Importar medios") -> list[Path]:
    """Diálogo «Abrir» con videos, imágenes y audio para el panel de medios."""
    chosen = dialogs.open_files(title=title, directory=initial_dir, filters=MEDIA_FILTERS)
    return [Path(p) for p in chosen]


def open_videos(initial_dir: str | Path | None = None, *, title: str = "Importar video") -> list[Path]:
    """Diálogo «Abrir» con multiselección de video; `[]` si se cancela.

    Devuelve los archivos elegidos, en el orden en que los da el sistema.
    """
    chosen = dialogs.open_files(title=title, directory=initial_dir, filters=VIDEO_FILTERS)
    return [Path(p) for p in chosen]


def open_images(initial_dir: str | Path | None = None, *, title: str = "Importar imagen") -> list[Path]:
    """Diálogo «Abrir» con multiselección de imágenes (PNG/JPG/WebP/BMP); `[]` si se cancela."""
    chosen = dialogs.open_files(title=title, directory=initial_dir, filters=IMAGE_FILTERS)
    return [Path(p) for p in chosen]


def pick_folder(initial_dir: str | Path | None = None, *, title: str = "Elegir carpeta") -> Path | None:
    """Diálogo «Buscar carpeta»; `None` si se cancela."""
    chosen = dialogs.pick_folder(title=title, directory=initial_dir)
    return Path(chosen) if chosen else None


def open_subtitles(
    initial_dir: str | Path | None = None, *, title: str = "Importar subtítulos",
) -> Path | None:
    """Diálogo «Abrir» con un SRT/VTT; `None` si se cancela."""
    chosen = dialogs.open_file(title=title, directory=initial_dir, filters=SUBTITLE_FILTERS)
    return Path(chosen) if chosen else None


def save_subtitles(
    initial_dir: str | Path | None = None, *, default_name: str = "subtitulos.srt",
    title: str = "Exportar subtítulos",
) -> Path | None:
    """Diálogo «Guardar como» para un SRT; `None` si se cancela."""
    chosen = dialogs.save_file(
        title=title, directory=initial_dir, default_name=default_name,
        filters=SRT_FILTERS,
    )
    return Path(chosen) if chosen else None
