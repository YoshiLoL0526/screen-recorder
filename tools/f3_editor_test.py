"""Prueba end-to-end del editor (F3) con la app real y `App.pilot()` (sin ventana).

Recorrido de aceptación del roadmap:
1. Biblioteca → «✂ Editar» crea el proyecto con la grabación.
2. Monta 3 clips con cortes exactos (S en el playhead) + transición + velocidad.
3. Drags de trim y de mover con undo/redo correctos; Esc cancela.
4. Reproduce con Espacio y mide los fps reales del preview (frames que llegan al `Signal`).
5. Guarda, cierra y reabre: el proyecto es idéntico.
Guarda capturas en `docs/assets/f3/` (`--no-shots` para no tocarlas).

Uso:  uv run python tools/f3_editor_test.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from recstudio import state  # noqa: E402
from recstudio.core import timeline as tl  # noqa: E402
from recstudio.platform import ffmpeg as ffmpeg_mod  # noqa: E402
from recstudio.platform import paths  # noqa: E402

DEST = Path("docs/assets/f3")


def make_recording(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    exe = ffmpeg_mod.ffmpeg_exe()
    mp4 = folder / "2026-10-16_18-05-12.mp4"
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=60:duration=12",
         "-f", "lavfi", "-i", "sine=frequency=220:beep_factor=4:duration=12",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-g", "120", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(mp4)],
        check=True, capture_output=True,
    )
    mp4.with_suffix(".json").write_text(json.dumps({
        "schema_version": 1, "duration_s": 12.0, "width": 1920, "height": 1080, "fps": 60,
        "backend": "wgc", "encoder": "libx264",
    }), encoding="utf-8")
    ffmpeg_mod.make_thumbnail(exe, mp4, mp4.with_suffix(".jpg"), at_s=3.0)
    return mp4


def wait(predicate, timeout: float = 15.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def check(ok: bool, label: str) -> None:
    print(f"   {'✓' if ok else '✗'} {label}")
    if not ok:
        raise SystemExit(f"❌ falló: {label}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-shots", action="store_true", help="no escribir capturas en docs/assets/f3")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="f3-"))
    paths.settings_path = lambda: work / "settings.json"  # type: ignore[assignment]
    from recstudio.app import build_app

    app, controller = build_app(size=(1280, 800))
    editor = controller.editor
    state.out_dir.set(str(work / "videos"))
    mp4 = make_recording(work / "videos")
    pilot = app.pilot(size=(1280, 800))
    shots: dict[str, Path] = {}

    def shot(name: str) -> None:
        if args.no_shots:
            return
        DEST.mkdir(parents=True, exist_ok=True)
        path = DEST / f"{name}.png"
        pilot.png(str(path))
        shots[name] = path

    def settle() -> None:
        for _ in range(3):
            pilot.frame()
        pilot.settle()

    try:
        print("1) Biblioteca → ✂ Editar")
        state.screen.set("editor")
        settle()
        shot("editor-vacio")
        controller.refresh_library()
        state.screen.set("library")
        check(wait(lambda: len(state.recordings()) == 1), "la grabación aparece en la biblioteca")
        settle()
        pilot.click("edit-0")
        check(wait(lambda: state.project_open()), "se crea y abre el proyecto")
        check(state.screen() == "editor", "pantalla del editor")
        settle()

        print("2) Tres clips con cortes exactos + transición + velocidad")
        editor.seek(3.0)
        pilot.press("s")
        editor.seek(7.5)
        pilot.press("s")
        project = state.editor_project()
        clips = project.video.clips
        check(len(clips) == 3, "3 clips")
        check([(c.in_, c.out_) for c in clips] == [(0.0, 3.0), (3.0, 7.5), (7.5, clips[2].out_)],
              f"cortes exactos {[(c.in_, round(c.out_, 3)) for c in clips]}")
        editor.select_clip(clips[0].id)
        editor.set_transition("fade", 0.5)
        editor.select_clip(clips[2].id)
        editor.set_speed(1.5)
        project = state.editor_project()
        expected = 3.0 + 4.5 + (clips[2].out_ - 7.5) / 1.5 - 0.5
        check(abs(tl.total_duration(project) - expected) < 1e-6,
              f"duración total {tl.total_duration(project):.3f} s (Σd − Σt)")
        settle()

        print("3) Drags con undo")
        c1, c2, c3 = project.video.clips
        zoom = state.tl_zoom()
        pilot.drag(f"trim-r-{c2.id}", dx=-1.0 * zoom, steps=8)
        settle()
        check(abs(state.editor_project().clip(c2.id).out_ - 6.5) <= 1 / 60 + 1e-6, "trim derecho −1 s")
        editor.undo()
        check(state.editor_project() == project, "undo del trim restaura exacto")
        editor.redo()
        editor.undo()
        settle()
        pilot.drag(f"clip-{c1.id}", dx=9.0 * zoom, steps=10)
        settle()
        check([c.id for c in state.editor_project().video.clips] == [c2.id, c3.id, c1.id], "mover al final")
        editor.undo()
        check(state.editor_project() == project, "undo del mover restaura exacto")
        settle()
        rect = pilot.rect(f"clip-{c3.id}")
        cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
        pilot.move_to(cx, cy)
        pilot.mouse_down()
        pilot.move_to(cx - 120, cy)
        pilot.move_to(cx - 260, cy)
        pilot.frame()
        shot("editor-arrastrando")
        pilot.press("escape")
        pilot.mouse_up()
        settle()
        check(state.editor_project() == project, "Esc cancela el drag sin dejar rastro")

        print("4) Reproducción")
        editor.select_clip(c2.id)
        editor.seek(0.0)
        settle()
        frames: list[float] = []
        unsubscribe = state.preview_frame.subscribe(lambda _b: frames.append(time.perf_counter()))
        pilot.press("space")
        time.sleep(4.0)
        pilot.press("space")
        unsubscribe()
        span = frames[-1] - frames[0] if len(frames) > 1 else 0.0
        fps = (len(frames) - 1) / span if span else 0.0
        ms = sorted(editor.player.render_ms)
        p95 = ms[int(len(ms) * 0.95)] if ms else 0.0
        check(fps >= 24, f"preview a {fps:.1f} fps (render p50 {ms[len(ms) // 2]:.1f} ms · p95 {p95:.1f} ms)")
        check(not state.playing(), "pausa con Espacio")
        editor.seek(3.0 - 0.25)  # en mitad del fundido
        check(wait(lambda: state.preview_frame() != b""), "frame del preview")
        time.sleep(0.3)
        settle()
        shot("editor-proyecto")

        print("5) Guardar, cerrar y reabrir")
        path = Path(state.project_path())
        pilot.press("ctrl+s")
        check(wait(lambda: not state.project_dirty()), "guardado (Ctrl+S)")
        editor.close_project()
        check(wait(lambda: not state.project_open()), "cerrado")
        editor.open_project(path)
        check(wait(lambda: state.project_open()), "reabierto")
        check(state.editor_project() == project, "el proyecto reabierto es idéntico")
        state.import_open.set(True)
        settle()
        shot("editor-importar")
        state.import_open.set(False)

        print("✅ F3 editor OK")
        for name, file in shots.items():
            print(f"   captura: {file}")
        return 0
    finally:
        controller.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
