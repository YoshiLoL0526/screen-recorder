"""Smoke test de la app tal como se distribuye: `Rec Studio.exe --smoke informe.json`.

El ejecutable empaquetado no tiene consola (`console = false`), así que el resultado va a un
JSON que lee `tools/check_build.py`. Comprueba lo que suele romper PyInstaller:

1. que todo el stack se importa dentro del bundle (numpy, PyAV, Pillow, audio, captura…);
2. que el FFmpeg incluido arranca y **codifica** un MP4 de 2 s que PyAV vuelve a abrir;
3. que la UI se construye sin ventana (`App.pilot`) y cada pantalla se pinta.

No toca los ajustes ni las carpetas del usuario: todo va a un directorio temporal.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable

_MODULES = ("numpy", "av", "PIL.Image", "sounddevice", "mss", "imageio_ffmpeg", "aurora",
            "aurora.dialogs")
_WINDOWS_MODULES = ("windows_capture", "pyaudiowpatch")


def _check_imports() -> dict[str, Any]:
    import importlib

    names = _MODULES + (_WINDOWS_MODULES if sys.platform == "win32" else ())
    versions = {}
    for name in names:
        module = importlib.import_module(name)
        versions[name] = str(getattr(module, "__version__", "ok"))
    # El adaptador de diálogos se importa tarde (al pulsar «Examinar»); aquí se comprueba que el
    # bundle trae `aurora.dialogs` y el módulo del proyecto.
    from .ui import file_dialogs

    for fn in ("open_videos", "pick_folder"):
        if not callable(getattr(file_dialogs, fn, None)):
            raise RuntimeError(f"file_dialogs.{fn} no está disponible en el bundle")
    versions["recstudio.ui.file_dialogs"] = "ok"
    return versions


def _check_ffmpeg(work: Path) -> dict[str, Any]:
    import av

    from .platform import ffmpeg as ffmpeg_mod

    exe = ffmpeg_mod.ffmpeg_exe()
    mp4 = work / "smoke.mp4"
    proc = ffmpeg_mod.run([
        exe, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(mp4),
    ])
    if proc.returncode != 0 or not mp4.is_file():
        raise RuntimeError(f"FFmpeg no generó el MP4 (rc={proc.returncode}): {proc.stderr[-400:]}")
    with av.open(str(mp4)) as container:
        duration = float(container.duration or 0) / 1e6
        streams = sorted(s.type for s in container.streams)
    if abs(duration - 2.0) > 0.2 or streams != ["audio", "video"]:
        raise RuntimeError(f"MP4 inesperado: {duration:.2f} s, streams {streams}")
    return {"version": ffmpeg_mod.ffmpeg_version(exe), "mp4_bytes": mp4.stat().st_size, "duration_s": duration}


def _check_ui(work: Path) -> dict[str, Any]:
    from . import state
    from .platform import paths

    paths.settings_path = lambda: work / "settings.json"  # type: ignore[assignment]
    from .app import build_app

    app, controller = build_app(size=(1280, 800))
    state.out_dir.set(str(work / "videos"))
    try:
        pilot = app.pilot(size=(1280, 800))
        rendered = []
        for screen in ("record", "library", "editor", "settings"):
            state.screen.set(screen)
            for _ in range(3):
                pilot.frame()
            pilot.settle()
            if screen == "record" and pilot.rect("rec-button") is None:
                raise RuntimeError("no se encuentra el botón de grabar")
            rendered.append(screen)
        pilot.png(str(work / "smoke.png"))
        return {"screens": rendered, "png_bytes": (work / "smoke.png").stat().st_size}
    finally:
        controller.shutdown()


def run(report: Path) -> int:
    from . import __version__

    result: dict[str, Any] = {"app": __version__, "frozen": bool(getattr(sys, "frozen", False)), "checks": {}}
    work = Path(tempfile.mkdtemp(prefix="recstudio-smoke-"))
    steps: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
        ("imports", _check_imports),
        ("ffmpeg", lambda: _check_ffmpeg(work)),
        ("ui", lambda: _check_ui(work)),
    )
    ok = True
    for name, step in steps:
        t0 = time.perf_counter()
        try:
            detail = step()
            result["checks"][name] = {"ok": True, "s": round(time.perf_counter() - t0, 2), **detail}
        except Exception as exc:  # el informe debe salir siempre, falle lo que falle
            ok = False
            result["checks"][name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                                      "trace": traceback.format_exc()[-1500:]}
    result["ok"] = ok
    report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    index = argv.index("--smoke")
    target = Path(argv[index + 1]) if len(argv) > index + 1 else Path.cwd() / "smoke-report.json"
    return run(target)
