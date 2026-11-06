"""Animación real (docs/31): preview y export de keyframes de posición y opacidad."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from recstudio.core import animation as anim
from recstudio.core import elements as el
from recstudio.core import graph as G
from recstudio.core import timeline as tl
from recstudio.core.project import Project
from recstudio.platform import ffmpeg as F
from recstudio.services import element_assets, media, playback


def _solid(path: Path, color: str, *, size: str = "320x180", duration: float = 3.0) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c={color}:size={size}:rate=30:duration={duration}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def _project(tmp_path: Path) -> Project:
    base = _solid(tmp_path / "base.mp4", "blue")
    pip = _solid(tmp_path / "pip.mp4", "red", size="100x100")
    project = Project(width=320, height=180, fps=30)
    project, _ = tl.insert_clip(project, media.probe(base))
    project, pip_asset = tl.add_asset(project, media.probe(pip))
    x_curve = anim.Animation("x", (
        anim.Keyframe(0.0, 0.25),
        anim.Keyframe(1.0, 0.75),
    ))
    project, _ = el.add_element(
        project, "video", 0.0, 3.0, asset_id=pip_asset.id,
        x=0.25, y=0.5, width=0.2, height=0.4, animations=(x_curve,),
    )
    project, _ = tl.add_text(
        project, "HOLA", 0.0, 3.0, x=0.25, y=0.18, size=40,
        animations=(
            anim.Animation("x", (anim.Keyframe(0.0, 0.25), anim.Keyframe(1.0, 0.75))),
            anim.Animation("opacity", (anim.Keyframe(0.0, 1.0), anim.Keyframe(1.0, 0.0))),
        ),
    )
    return project.validate()


def _red_center(rgb: np.ndarray) -> float:
    red = (rgb[:, :, 0] > 150) & (rgb[:, :, 1] < 100) & (rgb[:, :, 2] < 100)
    _ys, xs = np.nonzero(red)
    assert len(xs) > 100
    return float(xs.mean())


def _white_center(rgb: np.ndarray) -> float | None:
    white = (rgb[:, :, 0] > 220) & (rgb[:, :, 1] > 220) & (rgb[:, :, 2] > 220)
    _ys, xs = np.nonzero(white)
    return None if len(xs) < 20 else float(xs.mean())


def _export_frame(path: Path, t: float) -> np.ndarray:
    raw = subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(180, 320, 3)


def _preview_frame(project: Project, t: float) -> np.ndarray:
    renderer = playback.FrameRenderer(max_height=180)
    try:
        frame = renderer.render(project, t)
    finally:
        renderer.close()
    assert frame is not None
    return frame


@pytest.mark.skipif(
    not Path("C:/Windows/Fonts/arial.ttf").exists(), reason="Arial no disponible",
)
def test_preview_y_export_animan_x_y_opacidad(tmp_path: Path) -> None:
    project = _project(tmp_path)
    # Preview: el PiP se mueve y el texto se desplaza y se apaga.
    assert _red_center(_preview_frame(project, 0.1)) == pytest.approx(96, abs=6)
    assert _red_center(_preview_frame(project, 1.2)) == pytest.approx(240, abs=6)
    assert _white_center(_preview_frame(project, 0.05)) is not None
    assert _white_center(_preview_frame(project, 1.2)) is None

    out = tmp_path / "out.mp4"
    sources = element_assets.materialize(project, G.output_size(project, G.PRESETS[0]))
    spec = G.build(
        project, G.PRESETS[0], out, encoder="libx264", exe=F.ffmpeg_exe(),
        element_sources=sources,
    )
    assert "overlay=x='" in spec.filter_complex
    assert "alpha='if(" in spec.filter_complex
    result = subprocess.run(spec.args, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-800:]
    assert _red_center(_export_frame(out, 0.1)) == pytest.approx(96, abs=6)
    assert _red_center(_export_frame(out, 1.2)) == pytest.approx(240, abs=6)
    assert _white_center(_export_frame(out, 0.05)) is not None
    assert _white_center(_export_frame(out, 1.2)) is None


def test_texto_animado_solo_en_x_conserva_la_opacidad(tmp_path: Path) -> None:
    base = _solid(tmp_path / "base.mp4", "blue")
    project = Project(width=320, height=180, fps=30)
    project, _ = tl.insert_clip(project, media.probe(base))
    curve = anim.Animation("x", (anim.Keyframe(0.0, 0.25), anim.Keyframe(1.0, 0.75)))
    project, _ = tl.add_text(
        project, "MUNDO", 0.0, 3.0, x=0.25, y=0.2, size=36, animations=(curve,),
    )
    project = project.validate()
    left = _white_center(_preview_frame(project, 0.05))
    right = _white_center(_preview_frame(project, 1.2))
    assert left is not None and right is not None and right > left + 100
