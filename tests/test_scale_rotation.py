"""Escala y rotación animadas de elementos (docs/33): modelo, preview y export real."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from recstudio.core import animation as anim
from recstudio.core import elements as el
from recstudio.core import graph as G
from recstudio.core import timeline as tl
from recstudio.core.errors import ProjectError
from recstudio.core.project import Element, Project
from recstudio.platform import ffmpeg as F
from recstudio.services import element_animation, element_assets, media, playback
from recstudio.services import export as export_mod

WIDTH, HEIGHT = 320, 180


def _solid(path: Path, color: str, *, size: str = "320x180", duration: float = 3.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c={color}:size={size}:rate=30:duration={duration}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def _solid_with_tone(path: Path, color: str, *, size: str, duration: float) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c={color}:size={size}:rate=30:duration={duration}",
         "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
         "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


def _base(tmp_path: Path, duration: float = 3.0) -> Project:
    source = _solid(tmp_path / "base.mp4", "blue", duration=duration)
    project = Project(width=WIDTH, height=HEIGHT, fps=30)
    project, _ = tl.insert_clip(project, media.probe(source))
    return project


def _animated_shape(tmp_path: Path) -> tuple[Project, str]:
    project = _base(tmp_path)
    project, rect = el.add_element(
        project, "rect", 0.0, 2.5, x=0.5, y=0.5, width=0.10, height=0.28,
        color="#FF2020",
        animations=(
            anim.Animation("scale", (anim.Keyframe(0.0, 1.0), anim.Keyframe(2.0, 1.9))),
            anim.Animation("rotation", (anim.Keyframe(0.0, 0.0), anim.Keyframe(2.0, 40.0))),
        ),
    )
    return project.validate(), rect.id


def _animated_pip(tmp_path: Path) -> tuple[Project, str]:
    project = _base(tmp_path)
    pip = _solid_with_tone(tmp_path / "pip.mp4", "red", size="100x100", duration=2.0)
    project, asset = tl.add_asset(project, media.probe(pip))
    project, element = el.add_element(
        project, "video", 0.0, 1.5, asset_id=asset.id, x=0.5, y=0.5,
        width=0.12, height=0.20, video_track=1,
        animations=(
            anim.Animation("scale", (anim.Keyframe(0.0, 1.0), anim.Keyframe(1.2, 2.0))),
        ),
    )
    return project.validate(), element.id


def _red_box(rgb: np.ndarray) -> tuple[int, int, int, int, int]:
    red = (rgb[:, :, 0] > 150) & (rgb[:, :, 1] < 100) & (rgb[:, :, 2] < 100)
    ys, xs = np.nonzero(red)
    assert len(xs) > 50, f"rojo insuficiente: {len(xs)} px"
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()), int(len(xs))


def _preview(project: Project, t: float) -> np.ndarray:
    renderer = playback.FrameRenderer(max_height=HEIGHT)
    try:
        frame = renderer.render(project, t)
    finally:
        renderer.close()
    assert frame is not None
    return frame


def _export(project: Project, tmp_path: Path, *, name: str = "out.mp4"):
    preset = G.preset_by_id("calidad")
    size = G.output_size(project, preset)
    sources = element_assets.materialize(project, size)
    clips = element_animation.materialize(
        project, size, fps=project.fps, folder=tmp_path / "bake", sources=sources,
    )
    out = tmp_path / name
    spec = G.build(
        project, preset, out, encoder="libx264", exe=F.ffmpeg_exe(),
        element_sources=sources, element_clips=clips,
    )
    result = subprocess.run(spec.args, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-900:]
    return spec, clips, out


def _export_frame(path: Path, t: float) -> np.ndarray:
    raw = subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(HEIGHT, WIDTH, 3)


# -- modelo ---------------------------------------------------------------------------


def test_rangos_por_canal_y_expresion() -> None:
    scale = anim.Animation("scale", (anim.Keyframe(0.0, 0.05), anim.Keyframe(1.0, 4.0)))
    assert scale.value_at(0.5, 1.0) == pytest.approx(2.025)
    rotation = anim.Animation("rotation", (anim.Keyframe(0.0, -45.0), anim.Keyframe(1.0, 45.0)))
    assert rotation.value_at(0.5, 0.0) == pytest.approx(0.0)
    assert "if(lt(t,0)" in scale.expr(0.0, 1.0)
    assert anim.channel_range("rotation") == (-360.0, 360.0)

    for channel, value in (("scale", 0.049), ("scale", 4.01), ("rotation", 361.0),
                           ("rotation", -361.0), ("x", 1.5), ("opacity", -0.1)):
        with pytest.raises(ProjectError):
            anim.Animation(channel, (anim.Keyframe(0.0, value),))
    with pytest.raises(ProjectError):
        anim.Animation("escala", (anim.Keyframe(0.0, 1.0),))
    with pytest.raises(ProjectError):
        anim.Animation("scale", (anim.Keyframe(1.0, 1.0), anim.Keyframe(1.0, 2.0)))

    element = Element(
        "e", "rect", 2.0, 4.0, width=0.2, height=0.1, rotation=10.0,
        animations=(
            anim.Animation("scale", (anim.Keyframe(0.0, 1.0), anim.Keyframe(1.0, 2.0))),
            anim.Animation("rotation", (anim.Keyframe(0.0, 10.0), anim.Keyframe(1.0, 50.0))),
        ),
    )
    middle = el.effective(element, 2.5)
    assert middle.width == pytest.approx(0.3)
    assert middle.height == pytest.approx(0.15)
    assert middle.rotation == pytest.approx(30.0)
    end = el.effective(element, 3.0)
    assert end.width == pytest.approx(0.4)
    assert end.rotation == pytest.approx(50.0)


# -- preview --------------------------------------------------------------------------


def test_preview_escala_y_rotacion_de_una_figura(tmp_path: Path) -> None:
    project, _ = _animated_shape(tmp_path)
    small = _red_box(_preview(project, 0.05))
    big = _red_box(_preview(project, 1.95))
    assert big[4] > small[4] * 2.5  # el área crece con la escala
    assert big[4] < small[4] * 4.6
    for box in (small, big):
        center_x = (box[0] + box[2]) / 2
        center_y = (box[1] + box[3]) / 2
        assert center_x == pytest.approx(WIDTH / 2, abs=3)
        assert center_y == pytest.approx(HEIGHT / 2, abs=3)
    # La rotación de un rectángulo alto ensancha su caja envolvente.
    assert (big[2] - big[0]) > (small[2] - small[0]) * 2.5


def test_preview_de_un_pip_que_escala(tmp_path: Path) -> None:
    project, _ = _animated_pip(tmp_path)
    small = _red_box(_preview(project, 0.05))
    big = _red_box(_preview(project, 1.15))
    assert big[4] > small[4] * 2.0


# -- export real ----------------------------------------------------------------------


def test_export_real_paridad_escala_rotacion(tmp_path: Path) -> None:
    project, element_id = _animated_shape(tmp_path)
    spec, clips, out = _export(project, tmp_path)
    assert element_id in clips
    clip = clips[element_id]
    assert Path(clip.path).is_file() and Path(clip.path).stat().st_size > 0
    assert clip.frames == 75  # 2.5 s a 30 fps
    assert str(clip.path) in spec.args
    assert "rotate=" not in spec.filter_complex  # horneada; el graph no rota dos veces
    assert "eof_action=pass" in spec.filter_complex

    for t in (0.1, 1.9):
        preview = _red_box(_preview(project, t))
        exported = _red_box(_export_frame(out, t))
        assert exported[4] == pytest.approx(preview[4], rel=0.18)
        assert (exported[2] - exported[0]) == pytest.approx(preview[2] - preview[0], abs=4)
        assert (exported[3] - exported[1]) == pytest.approx(preview[3] - preview[1], abs=4)
        assert (exported[0] + exported[2]) / 2 == pytest.approx(
            (preview[0] + preview[2]) / 2, abs=3,
        )


def test_export_real_de_un_pip_animado(tmp_path: Path) -> None:
    project, element_id = _animated_pip(tmp_path)
    spec, clips, out = _export(project, tmp_path, name="animado.mp4")
    assert element_id in clips
    assert spec.has_audio
    assert F.probe_media(out).has_audio  # el bake no lleva audio: se abre el archivo original
    preview = _red_box(_preview(project, 1.15))
    exported = _red_box(_export_frame(out, 1.15))
    assert (exported[2] - exported[0]) == pytest.approx(preview[2] - preview[0], abs=5)
    assert exported[4] == pytest.approx(preview[4], rel=0.22)


def test_el_bake_conserva_el_alfa(tmp_path: Path) -> None:
    project = _base(tmp_path)
    project, element = el.add_element(
        project, "rect", 0.0, 1.0, x=0.5, y=0.5, width=0.2, height=0.2,
        color="#FF0000", opacity=0.5,
        animations=(anim.Animation(
            "scale", (anim.Keyframe(0.0, 1.0), anim.Keyframe(1.0, 1.5)),
        ),),
    )
    project = project.validate()
    preset = G.preset_by_id("calidad")
    size = G.output_size(project, preset)
    sources = element_assets.materialize(project, size)
    clips = element_animation.materialize(
        project, size, fps=30, folder=tmp_path / "bake", sources=sources,
    )
    import av

    with av.open(clips[element.id].path) as container:
        frame = next(container.decode(container.streams.video[0])).to_ndarray(format="rgba")
    height, width = frame.shape[:2]
    alpha = int(frame[height // 2, width // 2, 3])
    assert alpha == pytest.approx(128, abs=3)
    red, _green, blue, _alpha = (int(v) for v in frame[height // 2, width // 2])
    assert red > 200 and blue < 30


def test_el_exporter_borra_los_intermedios(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, _ = _animated_shape(tmp_path)
    temp = tmp_path / "temp"
    temp.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(export_mod.paths, "temp_root", lambda: temp)
    exporter = export_mod.Exporter()
    exporter.start(project, "calidad", tmp_path / "export.mp4")
    result = exporter.wait(180)
    assert result.state == "done", result.message
    assert (tmp_path / "export.mp4").is_file()
    assert not temp.exists() or not any(temp.iterdir())


def test_el_graph_falla_si_falta_el_video_animado(tmp_path: Path) -> None:
    project, _ = _animated_shape(tmp_path)
    preset = G.preset_by_id("calidad")
    sources = element_assets.materialize(project, G.output_size(project, preset))
    with pytest.raises(ProjectError):
        G.build(
            project, preset, tmp_path / "sin-bake.mp4", encoder="libx264", exe=F.ffmpeg_exe(),
            element_sources=sources,
        )
