"""Prueba de recuperación: simula una sesión interrumpida y verifica `--recover`.

Crea un directorio temporal como el que deja un crash (video.mkv + WAV + session.json en
estado `recording`), comprueba que `scan_recoverable()` lo detecta, lo recupera y valida el MP4.

Uso:  uv run python tools/recovery_test.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from recstudio.platform import ffmpeg as ffmpeg_mod  # noqa: E402
from recstudio.platform import paths  # noqa: E402
from recstudio.services import recording as rec  # noqa: E402


def main() -> int:
    exe = ffmpeg_mod.ffmpeg_exe()
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    session_dir = paths.temp_root() / f"rec_{stamp}"
    session_dir.mkdir(parents=True, exist_ok=True)
    out_dir = Path("tools/spikes/out/f1-recovery")

    print(f"1) creando sesión interrumpida sintética en {session_dir}")
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=60:duration=3",
         "-c:v", "libx264", "-preset", "ultrafast", session_dir / "video.mkv"],
        check=True, capture_output=True,
    )
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-ar", "44100", "-ac", "2", session_dir / "audio_system.wav"],
        check=True, capture_output=True,
    )
    (session_dir / "session.json").write_text(json.dumps({
        "status": "recording",  # como si la app hubiera muerto a mitad
        "started_at": stamp,
        "config": {"fps": 60, "preset": "media"},
        "backend": "wgc", "encoder": "h264_nvenc", "size": [640, 360],
        "offsets": {"audio_system.wav": 0.15},
        "warnings": [],
    }, indent=2), encoding="utf-8")

    print("2) scan_recoverable()")
    found = rec.scan_recoverable()
    ok_found = session_dir in found
    print(f"   detectadas: {[f.name for f in found]} · ¿incluye la nueva? {'sí' if ok_found else 'NO'}")
    if not ok_found:
        return 1

    print("3) recover_session()")
    result = rec.recover_session(session_dir, out_dir=out_dir)
    info = ffmpeg_mod.probe_media(result.mp4, exe)
    print(f"   {result.mp4.name} · {info.duration_s:.2f}s · {info.width}×{info.height}"
          f" · audio={'sí' if info.has_audio else 'NO'} · {info.size_bytes / 1e6:.2f} MB")

    print("4) la sesión ya no vuelve a aparecer como recuperable")
    still = session_dir in rec.scan_recoverable()
    print(f"   ¿sigue pendiente? {'sí (mal)' if still else 'no (bien)'}")

    ok = ok_found and info.duration_s > 2.5 and info.has_audio and not still
    print(f"\n{'✅ recuperación OK' if ok else '✖ fallo en la recuperación'}")
    if ok:
        shutil.rmtree(session_dir, ignore_errors=True)
        print(f"(limpieza: {session_dir.name} eliminada)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
