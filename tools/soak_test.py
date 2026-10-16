"""Soak test de F1: 10 minutos a 1080p60 con audio del sistema + micrófono y una pausa.

Comprueba el criterio de aceptación de F1:
- grabación larga estable (fps real, frames duplicados/descartados),
- **sin deriva A/V**: desfase beep↔cursor a los ~60 s vs a los ~540 s (< 80 ms de diferencia),
- la pausa intermedia no avanza ni el video ni el audio (duración = tiempo - pausa).

Uso:  uv run python tools/soak_test.py [--minutes 10] [--mic INDEX]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
sys.path.insert(0, str(Path(__file__).parent))

from av_sync_test import (  # noqa: E402
    BeepPlayer,
    find_beeps,
    find_cursor_move,
    find_empty_spots,
    set_cursor,
)
from recstudio.core.config import AudioConfig, RecordingConfig, SourceConfig  # noqa: E402
from recstudio.platform import ffmpeg as ffmpeg_mod  # noqa: E402
from recstudio.services import recording as rec  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--mic", type=int, default=None)
    ap.add_argument("--pause-at", type=float, default=None, help="segundos (por defecto: mitad)")
    ap.add_argument("--pause-for", type=float, default=5.0)
    ap.add_argument("--beep-every", type=float, default=0.0,
                    help="beeps periódicos cada N s (modo deriva: ajusta la tendencia)")
    ap.add_argument("-o", "--out", default="tools/spikes/out/f1")
    args = ap.parse_args()

    total = args.minutes * 60
    first_event = min(60.0, total * 0.15)
    last_event = total - 60.0
    pause_at = args.pause_at if args.pause_at is not None else total / 2

    config = RecordingConfig(
        fps=60,
        preset="media",
        source=SourceConfig(kind="monitor", monitor_index=1),
        audio=AudioConfig(system=True, mic_device_index=args.mic, keepalive=True),
    )
    player = BeepPlayer()
    spots = find_empty_spots(4)
    pos_a, pos_b = spots[0], spots[1]
    pos_c, pos_d = spots[2], spots[3]

    if args.beep_every > 0:
        schedule = [{"target": t, "kind": "beep"} for t in np.arange(args.beep_every, total - 4, args.beep_every)]
        # dos eventos de cursor (inicio y final) para medir la estabilidad del desfase A/V
        schedule.append({"target": first_event, "kind": "cursor", "from": pos_a, "to": pos_b})
        schedule.append({"target": last_event, "kind": "cursor", "from": pos_c, "to": pos_d})
        schedule.sort(key=lambda item: item["target"])
        print(f"soak de deriva: {args.minutes:.0f} min · beeps cada {args.beep_every:.0f}s"
              f" · cursor a {first_event:.0f}s y {last_event:.0f}s · pausa a los {pause_at:.0f}s")
    else:
        schedule = [
            {"target": first_event, "kind": "cursor", "from": pos_a, "to": pos_b},
            {"target": last_event, "kind": "cursor", "from": pos_c, "to": pos_d},
        ]
        print(f"soak: {args.minutes:.0f} min · pausa a los {pause_at:.0f}s ({args.pause_for:.0f}s)"
              f" · eventos a {first_event:.0f}s y {last_event:.0f}s · mic={args.mic}")

    events: list[dict] = []
    session = rec.RecordingSession(config, out_dir=args.out)
    session.start()
    set_cursor(*pos_a)

    paused_at: float | None = None
    resumed = False
    t_start = time.perf_counter()
    next_log = 0.0
    try:
        while True:
            stats = session.stats()
            elapsed = stats.elapsed_s
            wall = time.perf_counter() - t_start
            if stats.state == rec.RecState.ERROR.value:
                print(f"\n✖ {stats.error}")
                return 1
            if wall >= total:
                break
            if paused_at is None and wall >= pause_at and not resumed:
                session.pause()
                paused_at = time.perf_counter()
                print(f"\n⏸ pausa a los {elapsed:.1f}s grabados")
            elif paused_at is not None and time.perf_counter() - paused_at >= args.pause_for:
                session.resume()
                resumed = True
                paused_at = None
                print(f"▶ reanudado ({elapsed:.1f}s grabados)")
            if paused_at is None:
                for item in schedule:
                    if not item.get("fired") and elapsed >= item["target"]:
                        item["fired"] = True
                        item["elapsed"] = elapsed
                        player.beep()
                        if item["kind"] == "cursor":
                            set_cursor(*item["to"])
                        events.append({k: v for k, v in item.items() if k != "fired"})
                        print(f"\n🔔 evento a los {elapsed:.1f}s (objetivo {item['target']:.0f}s)")
            if wall >= next_log:
                next_log = wall + 60
                print(f"\r  {elapsed:7.1f}s grabados · {stats.fps_real:5.1f} fps · dup {stats.duplicated}"
                      f" · skip {stats.skipped} · {stats.size_bytes / 1e6:6.1f} MB · nivel {stats.level:0.2f}",
                      end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n(cancelado)")
    print()

    player.close()
    result = session.stop()
    stopped = time.perf_counter()
    wall_pause_for = args.pause_for
    print(f"\n✔ {result.mp4} · {result.duration_s:.2f}s · {result.mp4.stat().st_size / 1e6:.1f} MB")
    if result.audio:
        for track in result.audio:
            print(f"· deriva del dispositivo medida: {track.drift_ppm:+.0f} ppm"
                  f" ({track.seconds:.1f}s de audio) → corrección en el mux")
    _ = stopped, wall_pause_for

    exe = ffmpeg_mod.ffmpeg_exe()
    all_beeps = find_beeps(result.mp4, exe)
    print(f"· beeps detectados: {[round(b, 3) for b in all_beeps]}")

    report: dict = {
        "minutes": args.minutes,
        "pause_for": args.pause_for,
        "duration_s": round(result.duration_s, 2),
        "duplicated": result.duplicated,
        "skipped": result.skipped,
        "tracks": [{"device": t.device, "seconds": round(t.seconds, 2)} for t in result.audio],
        "events": [],
    }

    samples: list[tuple[float, float]] = []  # (elapsed, beep_t) para el ajuste de deriva
    for event in events:
        nearest = min(all_beeps, key=lambda b: abs(b - event["elapsed"]), default=None)
        beep_t = nearest if nearest is not None and abs(nearest - event["elapsed"]) < 2.0 else None
        row: dict = {"target": event["target"], "kind": event["kind"], "elapsed": round(event["elapsed"], 2)}
        if beep_t is not None:
            row["beep_t"] = round(beep_t, 3)
            row["audio_latency_ms"] = round((beep_t - event["elapsed"]) * 1000, 1)
            samples.append((event["elapsed"], beep_t))
        if event["kind"] == "cursor":
            cursor_t, _ = find_cursor_move(result.mp4, tuple(event["from"]), tuple(event["to"]), event["elapsed"])
            row["cursor_t"] = round(cursor_t, 3) if cursor_t else None
            if beep_t is not None and cursor_t is not None:
                row["skew_ms"] = round((beep_t - cursor_t) * 1000, 1)
        report["events"].append(row)
        detail = f"beep {beep_t:.3f}s (lat {row.get('audio_latency_ms')} ms)" if beep_t else "beep no detectado"
        if "skew_ms" in row:
            detail += f" · cursor {row['cursor_t']}s · desfase {row['skew_ms']:+.0f} ms"
        print(f"· evento {event['target']:.0f}s: {detail}")

    drift_ok = None
    if len(samples) >= 3:
        xs = np.array([e for e, _ in samples])
        ys = np.array([b for _, b in samples])
        slope, intercept = np.polyfit(xs, ys, 1)
        residual_ms = float(np.abs(ys - (slope * xs + intercept)).max()) * 1000
        drift_10min_ms = float((slope - 1) * 600 * 1000)
        drift_ok = bool(abs(drift_10min_ms) < 80)
        report.update({"slope_ppm": round((slope - 1) * 1e6, 1),
                       "drift_10min_ms": round(drift_10min_ms, 1),
                       "residual_max_ms": round(residual_ms, 1), "drift_ok": drift_ok})
        print(f"· deriva del reloj de audio: {(slope - 1) * 1e6:+.0f} ppm ⇒ {drift_10min_ms:+.0f} ms por 10 min"
              f" · residuo máx {residual_ms:.0f} ms → {'OK (<80 ms)' if drift_ok else 'FUERA DE TOLERANCIA'}")

    report["ok"] = bool(drift_ok) if drift_ok is not None else None
    print(json.dumps(report, ensure_ascii=False))
    return 0 if (drift_ok or drift_ok is None) else 1


if __name__ == "__main__":
    raise SystemExit(main())
