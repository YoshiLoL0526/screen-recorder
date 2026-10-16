"""Prueba de aceptación de F4 (export y efectos) con la app real y `App.pilot()` (sin ventana).

Criterios del roadmap (docs/06 → F4):
1. Export de 3 clips con 2 transiciones y velocidad (+ efectos) desde la UI: duración ±1 frame
   de la esperada y audio en sync (destellos blancos y pitidos simultáneos cada segundo en la
   fuente; en el resultado se emparejan y se mide el desfase).
2. Reproducible en móvil y Windows: H.264 High/yuv420p, AAC 48 kHz y `moov` al principio
   (faststart).
3. 1 min de 1080p60 con NVENC en < 40 s (RNF-05).
4. Corte rápido sin recodificar: los paquetes de video de los tramos conservados son idénticos
   byte a byte a los de la fuente.
5. Cancelar borra el parcial.
Guarda capturas en `docs/assets/f4/` (`--no-shots` para no tocarlas).

Uso:  uv run python tools/f4_export_test.py [--no-shots] [--skip-long]
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

import numpy as np  # noqa: E402

from recstudio import state  # noqa: E402
from recstudio.core import effects as fx  # noqa: E402
from recstudio.core import graph  # noqa: E402
from recstudio.core import timeline as tl  # noqa: E402
from recstudio.core.project import Project  # noqa: E402
from recstudio.platform import ffmpeg as ffmpeg_mod  # noqa: E402
from recstudio.platform import paths  # noqa: E402
from recstudio.services import export as export_mod  # noqa: E402
from recstudio.services import media  # noqa: E402

DEST = Path("docs/assets/f4")


def make_sync_source(path: Path, *, seconds: float, fps: int = 60, size: str = "1920x1080", gop: int = 120) -> Path:
    """testsrc2 con un cuadro blanco (arriba a la izquierda) y un pitido de 1 kHz en los primeros
    50 ms de cada segundo: marcas de sincronía A/V."""
    exe = ffmpeg_mod.ffmpeg_exe()
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=1000:sample_rate=48000:duration={seconds}",
         "-filter_complex",
         "[0:v]drawbox=x=0:y=0:w=240:h=240:color=white:t=fill:enable='lt(mod(t\\,1)\\,0.05)'[v];"
         "[1:a]volume=volume=0:enable='gte(mod(t\\,1)\\,0.05)'[a]",
         "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-g", str(gop), "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", str(path)],
        check=True, capture_output=True,
    )
    return path


def flash_onsets(path: Path) -> tuple[list[float], int, float]:
    """Instantes en que aparece el cuadro blanco, nº de frames y fps del video."""
    import av

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        onsets, frames, lit = [], 0, False
        for frame in container.decode(stream):
            frames += 1
            gray = frame.to_ndarray(format="gray")
            h = gray.shape[0]
            k = max(8, h * 240 // 1080)
            bright = float(gray[4 : k - 4, 4 : k - 4].mean()) > 200
            if bright and not lit:
                onsets.append(float(frame.time))
            lit = bright
        return onsets, frames, float(stream.average_rate or 0)


def beep_onsets(path: Path) -> tuple[list[float], float]:
    import av

    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=48000)
        chunks = [out.to_ndarray().reshape(-1) for frame in container.decode(stream) for out in resampler.resample(frame)]
    samples = np.concatenate(chunks) if chunks else np.zeros(0, np.float32)
    win = 240  # 5 ms
    n = len(samples) // win
    rms = np.sqrt((samples[: n * win].reshape(n, win) ** 2).mean(axis=1))
    loud = rms > 0.05
    onsets = [i * win / 48000 for i in range(1, n) if loud[i] and not loud[i - 1]]
    return onsets, len(samples) / 48000


def sync_error(flashes: list[float], beeps: list[float]) -> tuple[int, float]:
    pairs = []
    for f in flashes:
        near = min(beeps, key=lambda b: abs(b - f), default=None)
        if near is not None and abs(near - f) < 0.2:
            pairs.append(near - f)
    return len(pairs), max((abs(p) for p in pairs), default=float("inf"))


def video_packets(path: Path) -> list[tuple[float, str]]:
    import av

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        return [
            (float(p.pts * stream.time_base), hashlib.sha1(bytes(p)).hexdigest())
            for p in container.demux(stream) if p.size and p.pts is not None
        ]


def wait(predicate, timeout: float = 60.0) -> bool:
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
    parser.add_argument("--no-shots", action="store_true", help="no escribir capturas en docs/assets/f4")
    parser.add_argument("--skip-long", action="store_true", help="saltar el export de 1 min a 1080p60")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="f4-"))
    paths.settings_path = lambda: work / "settings.json"  # type: ignore[assignment]
    from recstudio.app import build_app

    app, controller = build_app(size=(1280, 800))
    editor = controller.editor
    state.out_dir.set(str(work / "videos"))
    (work / "videos").mkdir(parents=True, exist_ok=True)
    src = make_sync_source(work / "videos" / "demo-sync.mp4", seconds=12)
    pilot = app.pilot(size=(1280, 800))
    shots: dict[str, Path] = {}

    def shot(name: str) -> None:
        if args.no_shots:
            return
        DEST.mkdir(parents=True, exist_ok=True)
        path = DEST / f"{name}.png"
        pilot.png(str(path))
        shots[name] = path

    def settle(frames: int = 3) -> None:
        for _ in range(frames):
            pilot.frame()
        pilot.settle()

    try:
        print("1) 3 clips + 2 transiciones + velocidad + efectos → Exportar desde la UI")
        state.screen.set("editor")
        editor.import_paths([src], new_project=True)
        check(wait(lambda: state.project_open() and bool(state.editor_project().video.clips)), "proyecto creado")
        settle()
        editor.seek(4.0)
        pilot.press("s")
        editor.seek(8.0)
        pilot.press("s")
        c1, c2, c3 = state.editor_project().video.clips
        editor.select_clip(c1.id)
        editor.set_transition("fade", 0.5)
        editor.select_clip(c2.id)
        editor.set_transition("wipeleft", 0.4)
        editor.select_clip(c3.id)
        editor.set_speed(2.0)
        settle()
        state.insp_fade_out.set(0.5)  # por el inspector, como el usuario
        editor.select_clip(c2.id)
        settle()
        state.insp_saturation.set(0.5)
        state.insp_zoom.set(1.25)
        project = state.editor_project()
        check(fx.fade_out(project.clip(c3.id)) == 0.5 and fx.zoom(project.clip(c2.id))[0] == 1.25,
              "efectos aplicados desde el inspector (con undo)")
        editor.seek(6.0)
        check(wait(lambda: state.preview_frame() != b"", 10), "preview con efectos")
        time.sleep(0.4)
        settle()
        shot("editor-efectos")

        expected_frames = graph.timeline_frames(project)[2]
        pilot.press("ctrl+e")
        settle()
        check(state.export_open(), "Ctrl+E abre «Exportar»")
        check(state.export_blockers() != (), "«Sin recodificar» bloqueado (hay transiciones y efectos)")
        state.export_name.set("acept-f4")
        pilot.click("preset-calidad")
        settle()
        shot("export-modal")
        pilot.click("export-start")
        check(wait(lambda: state.export_progress() is not None and state.export_progress().state == "rendering", 20),
              "export en marcha")
        wait(lambda: state.export_progress().fraction > 0.3, 30)
        settle()
        shot("export-progreso")
        check(wait(lambda: not editor.exporter.busy, 120), "export terminado")
        result = state.export_progress()
        check(result.state == "done", f"estado final «{result.state}» {result.message}")
        settle()
        shot("export-hecho")
        out = Path(result.out_path)
        flashes, frames, fps = flash_onsets(out)
        check(abs(frames - expected_frames) <= 1,
              f"{frames} frames (esperados {expected_frames}: ±1) · {frames / project.fps:.3f} s "
              f"vs modelo {tl.total_duration(project):.3f} s")
        beeps, audio_len = beep_onsets(out)
        check(abs(audio_len - frames / project.fps) < 0.03, f"audio de {audio_len:.3f} s (igual que el video)")
        pairs, err = sync_error(flashes, beeps)
        check(pairs >= 6 and err <= 1.0 / project.fps + 0.006,
              f"A/V en sync: {pairs} marcas, desfase máx. {err * 1000:.1f} ms")
        info = ffmpeg_mod.run([ffmpeg_mod.ffmpeg_exe(), "-hide_banner", "-i", str(out)]).stderr
        head = out.read_bytes()[:2_000_000]
        check("h264 (High)" in info and "yuv420p" in info and "48000 Hz" in info,
              "H.264 High yuv420p + AAC 48 kHz (reproducible en móvil)")
        check(0 <= head.find(b"moov") < head.find(b"mdat"), "faststart: moov antes que mdat")
        state.export_open.set(False)

        print("2) Cancelar borra el parcial")
        editor.start_export()
        wait(lambda: state.export_progress() is not None and state.export_progress().state == "rendering", 20)
        editor.cancel_export()
        check(wait(lambda: not editor.exporter.busy, 20), "el export se detiene")
        folder = state.exports_dir()
        check(state.export_progress().state == "cancelled", "estado cancelado")
        check(not list(folder.glob("*.part.mp4")), "sin archivos parciales")

        print("3) Corte rápido sin recodificar (byte a byte)")
        editor.close_project()
        wait(lambda: not state.project_open(), 10)
        p = Project(fps=60, width=1920, height=1080)
        asset = media.probe(src)
        p, _ = tl.append_clip(p, asset)
        p = tl.split(p, 4.0)
        p = tl.split(p, 8.0)
        p = tl.ripple_delete(p, p.video.clips[1].id)  # queda [0, 4) + [8, 12)
        exporter = export_mod.Exporter()
        out_copy = work / "copia.mp4"
        exporter.start(p, "copia", out_copy)
        res = exporter.wait(60)
        check(res.state == "done", f"export sin recodificar: {res.state} en {res.elapsed:.2f} s {res.message}")
        source = video_packets(src)
        kept = [h for t, h in source if t < 4.0 - 1e-6] + [h for t, h in source if 8.0 - 1e-6 <= t < 12.0 - 1e-6]
        got = [h for _t, h in video_packets(out_copy)]
        check(got == kept, f"{len(got)} paquetes de video idénticos a los de la fuente ({len(kept)} esperados)")

        if not args.skip_long:
            print("4) 1 min de 1080p60 con el preset Rápido (NVENC)")
            t0 = time.perf_counter()
            long_src = make_sync_source(work / "largo.mp4", seconds=60)
            print(f"   (fuente generada en {time.perf_counter() - t0:.1f} s)")
            p = Project(fps=60, width=1920, height=1080)
            p, _ = tl.append_clip(p, media.probe(long_src))
            p = tl.split(p, 20.0)
            p = tl.split(p, 40.0)
            p, _ = tl.set_transition(p, 0, "fade", 1.0)
            available = ffmpeg_mod.probe_encoders()
            encoder = graph.resolve_encoder(graph.preset_by_id("rapido"), available)
            exporter.start(p, "rapido", work / "largo-export.mp4")
            res = exporter.wait(300)
            check(res.state == "done", f"export: {res.state} {res.message}")
            limit_ok = res.elapsed < 40 if encoder != "libx264" else True
            check(limit_ok, f"{res.duration:.1f} s de 1080p60 en {res.elapsed:.1f} s con {res.encoder} "
                            f"({res.duration / res.elapsed:.1f}× tiempo real; RNF-05 < 40 s)")

        print("✅ F4 export OK")
        for name, file in shots.items():
            print(f"   captura: {file}")
        return 0
    finally:
        controller.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
