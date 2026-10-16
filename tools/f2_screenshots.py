"""Genera las capturas de la documentación de F2 en `docs/assets/f2/`.

- Las pantallas se renderizan con `render_png` (sin ventana).
- La biblioteca se muestra con una grabación de ejemplo generada con FFmpeg.
- Copia (redimensionadas a 1280) las capturas de interacción de `tools/spikes/out/f2/`
  si existen (las produce `tools/f2_ui_test.py`).

Uso:  uv run python tools/f2_screenshots.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from PIL import Image

from recstudio import state  # noqa: E402
from recstudio.app import build_app  # noqa: E402
from recstudio.platform import ffmpeg as ffmpeg_mod  # noqa: E402
from recstudio.platform import paths  # noqa: E402

DEST = Path("docs/assets/f2")
SOURCE = Path("tools/spikes/out/f2")


def resize_in_place(path: Path, width: int = 1280) -> None:
    img = Image.open(path)
    if img.width > width:
        height = round(img.height * width / img.width)
        img.resize((width, height), Image.Resampling.LANCZOS).save(path, "PNG", optimize=True)


def make_sample_recording(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    exe = ffmpeg_mod.ffmpeg_exe()
    mp4 = folder / "2026-10-16_17-42-06.mp4"
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=60:duration=12",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", str(mp4)],
        check=True, capture_output=True,
    )
    mp4.with_suffix(".json").write_text(json.dumps({
        "schema_version": 1, "duration_s": 12.0, "width": 1280, "height": 720, "fps": 60,
        "backend": "wgc", "encoder": "h264_nvenc", "frames": 720, "duplicated": 12, "skipped": 40,
        "audio": [{"device": "Speakers (loopback)", "seconds": 12.0, "offset_s": 0.03}],
    }, indent=2), encoding="utf-8")
    ffmpeg_mod.make_thumbnail(exe, mp4, mp4.with_suffix(".jpg"), at_s=3.0)


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    # Los renders cambian señales (out_dir/audio) que el controlador persiste: guardar y restaurar.
    settings_file = paths.settings_path()
    backup = settings_file.read_text(encoding="utf-8") if settings_file.exists() else None

    tmp = Path(tempfile.mkdtemp(prefix="f2_shots_"))
    make_sample_recording(tmp)

    try:
        app, controller = build_app(size=(1280, 800))
        state.out_dir.set(str(tmp))
        state.audio_system.set(False)  # el render no depende del audio
        time.sleep(2.5)
        controller.refresh_library()
        time.sleep(0.8)

        for screen in ("record", "library", "settings"):
            state.screen.set(screen)
            target = DEST / f"pantalla-{screen}.png"
            app.render_png(str(target), scale=1.0)
            print(f"· {target}")

        controller.shutdown()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if backup is not None:
            settings_file.write_text(backup, encoding="utf-8")

    for name, out in (
        ("ui-grabando", "interaccion-grabando"),
        ("ui-biblioteca", "interaccion-biblioteca"),
    ):
        source = SOURCE / f"{name}.png"
        if source.exists():
            target = DEST / f"{out}.png"
            shutil.copy(source, target)
            resize_in_place(target)
            print(f"· {target} (de {source.name})")

    extras = Path("tools/spikes/out/f21")
    for name, out in (("cuenta-atras", "cuenta-atras"), ("editor-region-movido", "editor-region")):
        source = extras / f"{name}.png"
        if source.exists():
            target = DEST / f"{out}.png"
            shutil.copy(source, target)
            print(f"· {target} (de {source.name})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
