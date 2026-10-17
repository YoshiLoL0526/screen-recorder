"""Capturas de F5 (`docs/assets/f5/`) con la app real sin ventana (`App.pilot`).

Todo el contenido es sintético (`testsrc2` de FFmpeg y un JPEG vertical generado): el preview en
vivo se detiene antes de capturar para no incluir el escritorio de quien lo ejecuta.

Uso:  uv run python tools/f5_screenshots.py
"""

from __future__ import annotations

import io
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image, ImageDraw  # noqa: E402

from f3_editor_test import make_recording, wait  # noqa: E402
from recstudio import state  # noqa: E402
from recstudio.platform import paths  # noqa: E402

DEST = Path("docs/assets/f5")


def tall_preview() -> bytes:
    """Una «región» de 540×1600 (p. ej. una ventana de móvil o un feed vertical)."""
    image = Image.new("RGB", (540, 1600), (24, 28, 44))
    draw = ImageDraw.Draw(image)
    for i in range(12):
        top = 40 + i * 128
        draw.rounded_rectangle((30, top, 510, top + 108), radius=18, fill=(46 + i * 6, 70, 140 - i * 4))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="f5-"))
    paths.settings_path = lambda: work / "settings.json"  # type: ignore[assignment]
    from recstudio.app import build_app

    app, controller = build_app(size=(1280, 800))
    state.out_dir.set(str(work / "videos"))
    make_recording(work / "videos")
    if controller._preview is not None:
        controller._preview.stop()  # nada del escritorio real en las capturas
    pilot = app.pilot(size=(1280, 800))
    DEST.mkdir(parents=True, exist_ok=True)

    def settle() -> None:
        for _ in range(3):
            pilot.frame()
        pilot.settle()

    def shot(name: str) -> None:
        settle()
        pilot.png(str(DEST / f"{name}.png"))
        print(f"   captura: {DEST / name}.png")

    try:
        state.preview.set(tall_preview())
        shot("grabar-region-vertical")

        controller.refresh_library()
        state.screen.set("library")
        wait(lambda: len(state.recordings()) == 1)
        settle()
        pilot.click("edit-0")
        wait(lambda: state.project_open())
        wait(lambda: bool(state.preview_frame()))
        time.sleep(0.5)  # el waveform llega en segundo plano
        state.toasts.set(())
        shot("editor-1280")
        pilot.drag("split-timeline", dy=-140, steps=6)
        shot("editor-timeline-alto")
        pilot.drag("split-timeline", dy=140, steps=6)
        pilot.resize(1920, 1080)
        shot("editor-1920")
        pilot.press("i")
        shot("editor-1920-sin-inspector")
        pilot.press("i")
        pilot.resize(1280, 800)

        state.screen.set("settings")
        shot("ajustes")
        state.set_theme("light")
        pilot.scroll("settings-scroll", dy=900)
        shot("ajustes-claro")
        state.set_theme("dark")
    finally:
        controller.shutdown()
    print("✅ capturas de F5 en", DEST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
