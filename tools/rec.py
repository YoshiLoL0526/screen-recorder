"""Grabadora por línea de comandos (prototipo de F1 para validar el motor sin UI).

Ejemplos:

    uv run python tools/rec.py --list
    uv run python tools/rec.py --duration 10 -o tools/spikes/out
    uv run python tools/rec.py --fps 60 --preset alta --audio both --duration 12 --pause-at 5 --resume-after 2
    uv run python tools/rec.py --recover
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from recstudio.core.config import AudioConfig, RecordingConfig, SourceConfig  # noqa: E402
from recstudio.core.errors import RecStudioError  # noqa: E402
from recstudio.platform import audio as audio_mod  # noqa: E402
from recstudio.platform import ffmpeg as ffmpeg_mod  # noqa: E402
from recstudio.platform import screens  # noqa: E402
from recstudio.services import recording as rec  # noqa: E402


def parse_region(value: str) -> tuple[int, int, int, int]:
    parts = [int(p) for p in value.replace(" ", "").split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("región con formato X,Y,W,H")
    return parts[0], parts[1], parts[2], parts[3]


def build_config(args: argparse.Namespace) -> RecordingConfig:
    if args.window:
        source = SourceConfig(kind="window", window_title=args.window, capture_cursor=not args.no_cursor)
    elif args.region:
        source = SourceConfig(kind="region", monitor_index=args.monitor, region=args.region,
                              capture_cursor=not args.no_cursor)
    else:
        source = SourceConfig(kind="monitor", monitor_index=args.monitor, capture_cursor=not args.no_cursor)
    if args.backend != "auto":
        source = replace(source, backend=args.backend)

    audio = AudioConfig(
        system=args.audio in ("system", "both"),
        mic_device_index=args.mic if args.audio in ("mic", "both") else None,
        keepalive=not args.no_keepalive,
    )
    return RecordingConfig(
        fps=args.fps,
        preset=args.preset,
        encoder=None if args.encoder == "auto" else args.encoder,
        source=source,
        audio=audio,
        max_size_gb=args.max_size,
    ).validate()


def print_devices() -> None:
    print("Monitores:")
    for mon in screens.list_monitors():
        print(f"  [{mon.index}] {mon.label}  origen=({mon.left},{mon.top})")
    print("\nDispositivos de audio:")
    for dev in audio_mod.list_devices():
        tag = "loopback" if dev.kind == "loopback" else "entrada "
        print(f"  [{dev.index:>3}] {tag} {dev.label}")
    print("\nEncoders (prueba real de encode):")
    available = ffmpeg_mod.probe_encoders()
    for name, ok in available.items():
        print(f"  {'✅' if ok else '❌'} {name}")
    print(f"\nFFmpeg: {ffmpeg_mod.ffmpeg_version()}")
    print(f"Binario: {ffmpeg_mod.ffmpeg_exe()}")


def run(args: argparse.Namespace) -> int:
    config = build_config(args)

    def on_event(name: str, data: dict) -> None:
        if name == "warning":
            print(f"⚠ {data.get('message')}")
        elif name == "recording":
            print(f"● grabando · backend={data.get('backend')} encoder={data.get('encoder')}")

    session = rec.RecordingSession(config, out_dir=args.out, on_event=on_event)
    print(f"Salida: {session.out_dir}")
    print(f"Temporal: {session.temp_dir}")
    session.start()

    pause_at = args.pause_at
    resume_after = args.resume_after
    paused_at: float | None = None
    t_start = time.perf_counter()
    exit_code = 0
    try:
        while True:
            state = session.state
            if state in (rec.RecState.DONE, rec.RecState.ERROR):
                break
            stats = session.stats()
            if stats.state == rec.RecState.ERROR.value:
                print(f"\n✖ {stats.error}")
                exit_code = 1
                break
            elapsed = time.perf_counter() - t_start
            print(
                f"\r  {stats.elapsed_s:7.2f}s · {stats.fps_real:5.1f} fps · dup {stats.duplicated} "
                f"· {stats.size_bytes / 1e6:6.1f} MB · disco {stats.disk_free_mb / 1000:5.1f} GB "
                f"· nivel {stats.level:0.2f}",
                end="",
                flush=True,
            )
            if pause_at is not None and paused_at is None and elapsed >= pause_at:
                session.pause()
                paused_at = time.perf_counter()
                print(f"\r  ⏸ pausado a los {stats.elapsed_s:.2f}s" + " " * 40)
            if paused_at is not None and resume_after is not None and time.perf_counter() - paused_at >= resume_after:
                session.resume()
                pause_at = None  # una sola pausa en este modo
                paused_at = None
                print("\r  ▶ reanudado" + " " * 50)
            if args.duration is not None and elapsed >= args.duration:
                break
            time.sleep(0.25)
        print()
    except KeyboardInterrupt:
        print("\n(cancelado por el usuario)")

    try:
        result = session.stop()
    except RecStudioError as exc:
        print(f"✖ {exc.message}")
        if getattr(exc, "details", None):
            print(str(exc.details)[-1200:])
        return 1

    print(f"✔ {result.mp4}")
    print(
        f"  {result.duration_s:.2f}s · {result.width}×{result.height} · {result.fps:.1f} fps"
        f" · {result.mp4.stat().st_size / 1e6:.1f} MB · backend={result.backend} encoder={result.encoder}"
    )
    print(f"  frames={result.frames} dup={result.duplicated} skipped={result.skipped} audio={len(result.audio)}")
    for track in result.audio:
        offset = track.first_callback_t  # solo informativo
        print(f"    · {track.device} · {track.seconds:.2f}s")
    if result.thumbnail:
        print(f"  miniatura: {result.thumbnail}")
    print(f"  sidecar: {result.sidecar}")
    for warning in result.warnings:
        print(f"  ⚠ {warning}")

    if args.json:
        payload = {
            "mp4": str(result.mp4),
            "duration_s": round(result.duration_s, 3),
            "width": result.width,
            "height": result.height,
            "fps": round(result.fps, 2),
            "backend": result.backend,
            "encoder": result.encoder,
            "frames": result.frames,
            "duplicated": result.duplicated,
            "skipped": result.skipped,
            "audio": [asdict(track) | {"path": str(track.path)} for track in result.audio],
        }
        print(json.dumps(payload, ensure_ascii=False))
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(prog="rec", description="RecStudio · grabadora CLI (F1)")
    parser.add_argument("--list", action="store_true", help="monitores, dispositivos y encoders")
    parser.add_argument("--probe-encoders", action="store_true", help="re-prueba los encoders (sin caché)")
    parser.add_argument("--list-recoverable", action="store_true", help="sesiones interrumpidas")
    parser.add_argument("--recover", nargs="?", const="", default=None, metavar="DIR",
                        help="recupera una sesión interrumpida (la más reciente si no se indica DIR)")

    parser.add_argument("--monitor", type=int, default=1, help="monitor 1-based (default 1)")
    parser.add_argument("--region", type=parse_region, default=None, help="región X,Y,W,H dentro del monitor")
    parser.add_argument("--window", default=None, help="capturar la ventana cuyo título contenga este texto")
    parser.add_argument("--backend", default="auto", choices=["auto", "wgc", "dxgi", "dxcam", "mss"])
    parser.add_argument("--no-cursor", action="store_true", help="no capturar el cursor")
    parser.add_argument("--fps", type=int, default=60, choices=[15, 24, 30, 48, 60, 120])
    parser.add_argument("--preset", default="media", choices=["baja", "media", "alta", "insane"])
    parser.add_argument("--encoder", default="auto",
                        choices=["auto", "h264_nvenc", "h264_amf", "h264_qsv", "libx264"])
    parser.add_argument("--audio", default="system", choices=["system", "mic", "both", "none"])
    parser.add_argument("--mic", type=int, default=None, help="índice del micrófono (--list)")
    parser.add_argument("--no-keepalive", action="store_true", help="sin silencio de keep-alive (loopback)")
    parser.add_argument("--max-size", type=float, default=None, help="tamaño máximo en GB")
    parser.add_argument("-o", "--out", default=None, help="carpeta de salida")
    parser.add_argument("--duration", type=float, default=None, help="parar tras N segundos")
    parser.add_argument("--pause-at", type=float, default=None, help="pausar a los N segundos (pruebas)")
    parser.add_argument("--resume-after", type=float, default=None, help="reanudar N segundos tras pausar")
    parser.add_argument("--json", action="store_true", help="resultado final en JSON")
    args = parser.parse_args()

    if args.list:
        print_devices()
        return 0
    if args.probe_encoders:
        available = ffmpeg_mod.probe_encoders(force=True)
        for name, ok in available.items():
            print(f"{'✅' if ok else '❌'} {name}")
        return 0
    if args.list_recoverable:
        found = rec.scan_recoverable()
        if not found:
            print("No hay sesiones recuperables.")
        for folder in found:
            print(f"  {folder}")
        return 0
    if args.recover is not None:
        if args.recover == "":
            found = rec.scan_recoverable()
            if not found:
                print("No hay sesiones recuperables.")
                return 1
            target = found[-1]
        else:
            target = Path(args.recover)
        print(f"Recuperando {target}…")
        try:
            result = rec.recover_session(target, out_dir=args.out)
        except RecStudioError as exc:
            print(f"✖ {exc.message}")
            if getattr(exc, "details", None):
                print(str(exc.details)[-1200:])
            return 1
        print(f"✔ {result.mp4} ({result.duration_s:.2f}s)")
        return 0

    if args.mic is None and args.audio == "mic":
        print("✖ --audio mic requiere --mic INDEX (usa --list)")
        return 2
    try:
        return run(args)
    except RecStudioError as exc:
        print(f"✖ {exc.message}")
        if exc.details:
            print(str(exc.details)[-1200:])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
