"""Pistas de video V2/V3 (docs/29): agrupación por `Element.video_track`.

Comprueba el modelo, la composición en preview/export por capas, el audio de un clip de V2 y la
UI de inserción/cambio de pista. Usa FFmpeg real como el resto de tests del editor.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np

from recstudio.core import elements as el
from recstudio.core import graph as G
from recstudio.core import timeline as tl
from recstudio.core.project import Clip, Effect, MediaAsset, Project, Track
from recstudio.platform import ffmpeg as F
from recstudio.services import element_assets, media, playback


def _solid(path: Path, color: str, *, size: str = "320x180", duration: float = 2.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c={color}:size={size}:rate=30:duration={duration}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def _project(tmp_path: Path) -> Project:
    base = _solid(tmp_path / "base.mp4", "blue")
    red = _solid(tmp_path / "red.mp4", "red")
    green = _solid(tmp_path / "green.mp4", "green")
    project = Project(width=320, height=180, fps=30)
    project, _ = tl.insert_clip(project, media.probe(base))
    project, red_asset = tl.add_asset(project, media.probe(red))
    project, green_asset = tl.add_asset(project, media.probe(green))
    project, _ = el.add_element(
        project, "video", 0.0, 2.0, asset_id=red_asset.id, video_track=1,
    )
    project, _ = el.add_element(
        project, "video", 0.5, 1.0, asset_id=green_asset.id, video_track=2,
    )
    return project.validate()


def _rgb_frame(path: Path, t: float) -> np.ndarray:
    out = subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(out, np.uint8).reshape(180, 320, 3)


def _is_red(pixel) -> bool:  # noqa: ANN001
    return int(pixel[0]) > 150 and int(pixel[1]) < 100 and int(pixel[2]) < 100


def _is_green(pixel) -> bool:  # noqa: ANN001
    return int(pixel[1]) > 110 and int(pixel[0]) < 100 and int(pixel[2]) < 100


def test_preview_v2_v3_compone_por_capas(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert [e.video_track for e in project.elements] == [1, 2]
    renderer = playback.FrameRenderer(max_height=180)
    try:
        under_v3 = renderer.render(project, 0.2)
        on_v3 = renderer.render(project, 0.7)
        after_v3 = renderer.render(project, 1.7)
    finally:
        renderer.close()
    assert under_v3 is not None and on_v3 is not None and after_v3 is not None
    assert _is_red(under_v3[90, 160])  # V2 tapa el video base
    assert _is_green(on_v3[90, 160])  # V3 tapa V2
    assert _is_red(after_v3[90, 160])  # V3 terminó; vuelve a verse V2


def test_efectos_globales_afectan_a_v2_pero_no_a_la_capa_elementos(tmp_path: Path) -> None:
    base = _solid(tmp_path / "base.mp4", "blue")
    red = _solid(tmp_path / "red.mp4", "red")
    project = Project(width=320, height=180, fps=30)
    project, _ = tl.insert_clip(project, media.probe(base))
    project, red_asset = tl.add_asset(project, media.probe(red))
    project, _ = el.add_element(
        project, "video", 0.0, 2.0, asset_id=red_asset.id, video_track=1,
    )
    project, _ = el.add_element(
        project, "rect", 0.0, 2.0, x=0.1, y=0.1, width=0.2, height=0.2,
        color="#FF0000",
    )
    project = replace(project, effects=(Effect("grayscale"),)).validate()
    renderer = playback.FrameRenderer(max_height=180)
    try:
        frame = renderer.render(project, 1.0)
    finally:
        renderer.close()
    assert frame is not None
    center = frame[90, 160]
    assert abs(int(center[0]) - int(center[1])) < 12  # V2 rojo pasa a gris
    assert abs(int(center[1]) - int(center[2])) < 12
    corner = frame[18, 32]  # rectángulo genérico: encima de los efectos globales
    assert _is_red(corner)


def test_export_v2_v3_compone_por_capas(tmp_path: Path) -> None:
    project = _project(tmp_path)
    out = tmp_path / "out.mp4"
    spec = G.build(project, G.PRESETS[0], out, encoder="libx264", exe=F.ffmpeg_exe())
    result = subprocess.run(spec.args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-800:]
    assert _is_red(_rgb_frame(out, 0.2)[90, 160])
    assert _is_green(_rgb_frame(out, 0.7)[90, 160])
    assert _is_red(_rgb_frame(out, 1.7)[90, 160])


def test_export_efectos_globales_sobre_v2(tmp_path: Path) -> None:
    base = _solid(tmp_path / "base.mp4", "blue")
    red = _solid(tmp_path / "red.mp4", "red")
    project = Project(width=320, height=180, fps=30)
    project, _ = tl.insert_clip(project, media.probe(base))
    project, red_asset = tl.add_asset(project, media.probe(red))
    project, _ = el.add_element(
        project, "video", 0.0, 2.0, asset_id=red_asset.id, video_track=1,
    )
    project, _ = el.add_element(
        project, "rect", 0.0, 2.0, x=0.1, y=0.1, width=0.2, height=0.2,
        color="#FF0000",
    )
    project = replace(project, effects=(Effect("grayscale"),)).validate()
    out = tmp_path / "out.mp4"
    sources = element_assets.materialize(project, G.output_size(project, G.PRESETS[0]))
    spec = G.build(
        project, G.PRESETS[0], out, encoder="libx264", exe=F.ffmpeg_exe(),
        element_sources=sources,
    )
    result = subprocess.run(spec.args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-800:]
    center = _rgb_frame(out, 1.0)[90, 160]
    assert abs(int(center[0]) - int(center[1])) < 12
    assert abs(int(center[1]) - int(center[2])) < 12
    assert _is_red(_rgb_frame(out, 1.0)[18, 32])


def test_audio_de_un_clip_v2_se_mezcla(tmp_path: Path) -> None:
    base = _solid(tmp_path / "base.mp4", "blue")
    tone = tmp_path / "tone.mp4"
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:size=160x90:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
         "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(tone)],
        check=True, capture_output=True,
    )
    project = Project(width=320, height=180, fps=30)
    project, _ = tl.insert_clip(project, media.probe(base))
    project, tone_asset = tl.add_asset(project, media.probe(tone))
    project, _ = el.add_element(
        project, "video", 0.5, 1.0, asset_id=tone_asset.id, video_track=1,
        audio_gain=1.0,
    )
    project = project.validate()
    assert G.has_element_audio(project)
    out = tmp_path / "out.mp4"
    spec = G.build(project, G.PRESETS[0], out, encoder="libx264", exe=F.ffmpeg_exe())
    result = subprocess.run(spec.args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-800:]
    # El audio del clip V2 suena en su intervalo (mismo mezclador que el PiP).
    import io
    import wave

    def rms(a: float, b: float) -> float:
        raw = subprocess.run(
            [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", str(a),
             "-t", str(b - a), "-i", str(out), "-vn", "-ac", "2", "-ar", "48000", "-f", "wav", "-"],
            check=True, capture_output=True,
        ).stdout
        with wave.open(io.BytesIO(raw), "rb") as wav:
            data = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
        return float(np.sqrt((data.astype(np.float32) / 32768.0) ** 2).mean())

    assert rms(0.0, 0.4) < 0.005
    assert rms(0.7, 1.3) > 0.02
    assert rms(1.7, 2.0) < 0.005


def test_pegar_video_de_v2_conserva_el_orden_de_capas() -> None:
    base_asset = MediaAsset("a", "a.mp4", 10.0, 320, 180, 30.0, False)
    red_asset = MediaAsset("r", "r.mp4", 10.0, 320, 180, 30.0, False)
    green_asset = MediaAsset("g", "g.mp4", 10.0, 320, 180, 30.0, False)
    project = Project(
        assets=(base_asset, red_asset, green_asset),
        video=Track("video", (Clip("c", "a", 0.0, 10.0),)),
    )
    project, v2 = el.add_element(project, "video", 0.0, 2.0, asset_id="r", video_track=1)
    project, v3 = el.add_element(project, "video", 0.0, 2.0, asset_id="g", video_track=2)
    project, generic = el.add_element(project, "rect", 0.0, 2.0)
    clipboard = tl.copy_items(project, {v2.id})
    pasted, new_ids = tl.paste(project, clipboard, 0.0)
    order = [item.id for item in pasted.elements]
    assert order[:2] == [v2.id, new_ids[0]]  # el pegado queda en V2, antes de V3
    assert order.index(v3.id) > order.index(new_ids[0])
    assert order.index(generic.id) > order.index(new_ids[0])


def test_filas_y_cabeceras_v2_v3() -> None:
    """La geometría del timeline reserva una fila por pista usada, de V3 hacia abajo."""
    from recstudio.ui import timeline as tlui

    base_asset = MediaAsset("a", "a.mp4", 10.0, 320, 180, 30.0, False)
    red_asset = MediaAsset("r", "r.mp4", 10.0, 320, 180, 30.0, False)
    green_asset = MediaAsset("g", "g.mp4", 10.0, 320, 180, 30.0, False)
    project = Project(
        assets=(base_asset, red_asset, green_asset),
        video=Track("video", (Clip("c", "a", 0.0, 10.0),)),
    )
    project, _ = el.add_element(project, "video", 0.0, 2.0, asset_id="r", video_track=1)
    project, _ = el.add_element(project, "video", 0.0, 2.0, asset_id="g", video_track=2)
    layout = tlui.tracks_for(0.0, project)
    assert [row.track for row in layout.video_rows] == [2, 1]  # V3 encima de V2
    assert layout.video_top > layout.video_rows[0].top
    assert layout.video_rows[0].top < layout.video_rows[1].top
