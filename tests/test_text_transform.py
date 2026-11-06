"""Escala y rotación animadas de textos (docs/35): modelo, preview y export real."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from recstudio.core import animation as anim
from recstudio.core import graph as G
from recstudio.core import timeline as tl
from recstudio.core.errors import ProjectError
from recstudio.core.project import Project, TextOverlay
from recstudio.platform import ffmpeg as F
from recstudio.services import export as export_mod
from recstudio.services import media, playback, text_animation

WIDTH, HEIGHT = 320, 180


def _solid(path: Path, color: str, *, duration: float = 3.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c={color}:size={WIDTH}x{HEIGHT}:rate=30:duration={duration}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def _base(tmp_path: Path, duration: float = 3.0) -> Project:
    source = _solid(tmp_path / "base.mp4", "blue", duration=duration)
    project = Project(width=WIDTH, height=HEIGHT, fps=30)
    project, _ = tl.insert_clip(project, media.probe(source))
    return project


def _animated_text(tmp_path: Path) -> tuple[Project, str]:
    project = _base(tmp_path)
    project, text = tl.add_text(
        project, "H", 0.0, 2.5, x=0.5, y=0.5, size=40, color="#FFFFFF", bold=True,
        animations=(
            anim.Animation("scale", (anim.Keyframe(0.0, 1.0), anim.Keyframe(2.0, 1.9))),
            anim.Animation("rotation", (anim.Keyframe(0.0, 0.0), anim.Keyframe(2.0, 45.0))),
        ),
    )
    return project.validate(), text.id


def _white_box(rgb: np.ndarray) -> tuple[int, int, int, int, int]:
    white = (rgb[:, :, 0] > 220) & (rgb[:, :, 1] > 220) & (rgb[:, :, 2] > 220)
    ys, xs = np.nonzero(white)
    assert len(xs) > 30, f"texto blanco insuficiente: {len(xs)} px"
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
    clips = text_animation.materialize(
        project, size, fps=project.fps, folder=tmp_path / "bake",
    )
    out = tmp_path / name
    spec = G.build(
        project, preset, out, encoder="libx264", exe=F.ffmpeg_exe(), text_clips=clips,
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


def test_texto_admite_escala_y_rotacion_y_text_values() -> None:
    assert "scale" in anim.TEXT_CHANNELS and "rotation" in anim.TEXT_CHANNELS
    curve = (
        anim.Animation("scale", (anim.Keyframe(0.0, 1.0), anim.Keyframe(2.0, 2.0))),
        anim.Animation("rotation", (anim.Keyframe(0.0, 0.0), anim.Keyframe(2.0, 90.0))),
    )
    overlay = TextOverlay("t", "Hola", 1.0, 4.0, animations=curve)
    x, y, opacity, scale, rotation = tl.text_values(overlay, 2.0)
    assert (x, y, opacity) == (0.5, 0.82, 1.0)
    assert scale == pytest.approx(1.5)
    assert rotation == pytest.approx(45.0)

    for channel, value in (("scale", 0.01), ("scale", 4.1), ("rotation", 361.0)):
        with pytest.raises(ProjectError):
            TextOverlay(
                "t", "Hola", 0.0, 1.0,
                animations=(anim.Animation(channel, (anim.Keyframe(0.0, value),)),),
            )


# -- preview --------------------------------------------------------------------------


def test_preview_real_de_un_texto_que_escala_y_rota(tmp_path: Path) -> None:
    project, _ = _animated_text(tmp_path)
    small = _white_box(_preview(project, 0.05))
    big = _white_box(_preview(project, 1.9))
    assert big[4] > small[4] * 2.0
    for box in (small, big):
        center_x = (box[0] + box[2]) / 2
        center_y = (box[1] + box[3]) / 2
        assert center_x == pytest.approx(WIDTH / 2, abs=3)
        assert center_y == pytest.approx(HEIGHT / 2, abs=3)


def test_preview_real_gira_el_texto_alrededor_del_centro(tmp_path: Path) -> None:
    project = _base(tmp_path)
    project, _ = tl.add_text(
        project, "TEXTO", 0.0, 2.5, x=0.5, y=0.5, size=36, color="#FFFFFF", bold=True,
        animations=(anim.Animation(
            "rotation", (anim.Keyframe(0.0, 0.0), anim.Keyframe(2.0, 90.0)),
        ),),
    )
    project = project.validate()
    horizontal = _white_box(_preview(project, 0.05))
    vertical = _white_box(_preview(project, 1.9))
    assert horizontal[2] - horizontal[0] > horizontal[3] - horizontal[1]
    assert vertical[3] - vertical[1] > vertical[2] - vertical[0]


# -- export real ----------------------------------------------------------------------


def test_export_real_paridad_escala_y_rotacion(tmp_path: Path) -> None:
    project, text_id = _animated_text(tmp_path)
    spec, clips, out = _export(project, tmp_path)
    assert text_id in clips
    clip = clips[text_id]
    assert Path(clip.path).is_file() and Path(clip.path).stat().st_size > 0
    assert clip.frames == 75  # 2.5 s a 30 fps
    assert str(clip.path) in spec.args
    assert "overlay=" in spec.filter_complex and "eof_action=pass" in spec.filter_complex
    assert "drawtext=" not in spec.filter_complex  # el único texto va horneado

    for t in (0.1, 1.9):
        preview = _white_box(_preview(project, t))
        exported = _white_box(_export_frame(out, t))
        assert exported[4] == pytest.approx(preview[4], rel=0.22)
        assert (exported[2] - exported[0]) == pytest.approx(preview[2] - preview[0], abs=5)
        assert (exported[3] - exported[1]) == pytest.approx(preview[3] - preview[1], abs=5)
        assert (exported[0] + exported[2]) / 2 == pytest.approx(
            (preview[0] + preview[2]) / 2, abs=3,
        )
        assert (exported[1] + exported[3]) / 2 == pytest.approx(
            (preview[1] + preview[3]) / 2, abs=3,
        )


def test_sin_escala_ni_rotacion_las_cadenas_siguen_siendo_drawtext(tmp_path: Path) -> None:
    project = _base(tmp_path)
    project, text = tl.add_text(
        project, "HOLA", 0.0, 2.5, x=0.4, y=0.3, size=40,
        animations=(anim.Animation("x", (anim.Keyframe(0.0, 0.3), anim.Keyframe(2.0, 0.7))),),
    )
    project = project.validate()
    assert text_animation.materialize(
        project, (WIDTH, HEIGHT), fps=30, folder=tmp_path / "bake-vacio",
    ) == {}
    preset = G.preset_by_id("calidad")
    spec = G.build(project, preset, tmp_path / "estatico.mp4", encoder="libx264",
                   exe=F.ffmpeg_exe(), text_clips={})
    assert "drawtext=" in spec.filter_complex
    assert "overlay=" not in spec.filter_complex
    assert spec.filter_complex.endswith("[vtext]")
    assert G.text_filters(project, HEIGHT)[0] in spec.filter_complex


def test_los_textos_estaticos_y_horneados_mantienen_el_orden(tmp_path: Path) -> None:
    project = _base(tmp_path)
    project, _ = tl.add_text(project, "ABAJO", 0.0, 2.5, y=0.2, size=30)
    project, baked = tl.add_text(
        project, "MEDIO", 0.0, 2.5, y=0.5, size=30,
        animations=(
            anim.Animation("scale", (anim.Keyframe(0.0, 1.0), anim.Keyframe(2.0, 2.0))),
            anim.Animation("x", (anim.Keyframe(0.0, 0.4), anim.Keyframe(2.0, 0.6))),
        ),
    )
    project, _ = tl.add_text(project, "ARRIBA", 0.0, 2.5, y=0.8, size=30)
    project = project.validate()
    clips = text_animation.materialize(
        project, (WIDTH, HEIGHT), fps=30, folder=tmp_path / "bake",
    )
    assert set(clips) == {baked.id}
    spec = G.build(project, G.preset_by_id("calidad"), tmp_path / "orden.mp4",
                   encoder="libx264", exe=F.ffmpeg_exe(), text_clips=clips)
    fc = spec.filter_complex
    assert fc.index("text='ABAJO'") < fc.index("overlay=") < fc.index("text='ARRIBA'")
    assert "overlay=x='" in fc  # X animada del texto horneado: expresión por frame


def test_el_grafo_falla_si_falta_el_video_animado(tmp_path: Path) -> None:
    project, _ = _animated_text(tmp_path)
    with pytest.raises(ProjectError):
        G.build(project, G.preset_by_id("calidad"), tmp_path / "sin-bake.mp4",
                encoder="libx264", exe=F.ffmpeg_exe(), text_clips={})


def test_el_bake_del_texto_conserva_el_alfa(tmp_path: Path) -> None:
    project = _base(tmp_path)
    project, text = tl.add_text(
        project, "I", 0.0, 1.0, x=0.5, y=0.5, size=80, color="#FFFFFF", bold=True,
        opacity=0.5,
        animations=(anim.Animation("scale", (anim.Keyframe(0.0, 1.0), anim.Keyframe(1.0, 1.5))),),
    )
    project = project.validate()
    clips = text_animation.materialize(
        project, (WIDTH, HEIGHT), fps=30, folder=tmp_path / "bake-alfa",
    )
    import av

    with av.open(clips[text.id].path) as container:
        frame = next(container.decode(container.streams.video[0])).to_ndarray(format="rgba")
    height, width = frame.shape[:2]
    centre = frame[height // 2 - 8:height // 2 + 8, width // 2 - 8:width // 2 + 8, 3]
    assert int(centre.max()) == pytest.approx(128, abs=3)


def test_el_exporter_borra_los_intermedios(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, _ = _animated_text(tmp_path)
    temp = tmp_path / "temp"
    temp.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(export_mod.paths, "temp_root", lambda: temp)
    exporter = export_mod.Exporter()
    exporter.start(project, "calidad", tmp_path / "export.mp4")
    result = exporter.wait(180)
    assert result.state == "done", result.message
    assert (tmp_path / "export.mp4").is_file()
    assert not temp.exists() or not any(temp.iterdir())
