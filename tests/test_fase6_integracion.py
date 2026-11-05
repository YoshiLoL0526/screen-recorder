"""Integración de las features v6: globales + elementos + textos (docs/22–26).

Comprueba que el orden compartido por preview y export se mantiene al combinarlas y que un
export real con las tres capas termina bien. Es el caso que no cubren por separado los tests de
cada feature.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from recstudio.core import global_effects as ge
from recstudio.core import graph as G
from recstudio.core import overlays
from recstudio.core.project import Clip, Effect, Element, Project, TextOverlay, Track
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import element_assets, export as export_mod, media


@pytest.fixture()
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "cache"
    folder.mkdir()
    monkeypatch.setattr(paths, "cache_dir", lambda: folder)
    return folder


def _synthetic(path: Path) -> Path:
    subprocess.run(
        [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "color=c=white:size=320x240:rate=30:duration=1",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def _project(source: Path) -> Project:
    asset = media.probe(source)
    return Project(
        assets=(asset,),
        video=Track("video", (Clip("c", asset.id, 0.0, 1.0),)),
        width=320, height=240, fps=30,
        elements=(Element("e", "rect", 0.0, 1.0, x=0.5, y=0.5, width=0.4, height=0.3,
                          color="#FF0000", effects=(Effect("blur", (("radius", 6.0),)),)),),
        effects=(Effect("eq", (("brightness", 0.1),)), Effect("blur", (("radius", 4.0),))),
        texts=(TextOverlay("t", "Integración v6", 0.0, 1.0),),
    )


def test_orden_global_elementos_textos_y_preview(cache: Path, tmp_path: Path) -> None:
    project = _project(_synthetic(tmp_path / "base.mp4")).validate()
    sources = element_assets.materialize(project, (320, 240))
    spec = G.build(project, G.preset_by_id("calidad"), tmp_path / "out.mp4",
                   element_sources=sources)
    fc = spec.filter_complex
    assert "gblur=sigma=4" in fc
    assert fc.index("gblur") < fc.index("overlay=") < fc.index("drawtext")
    assert "enable='between(t,0,1)" in fc

    # El elemento va encima del color global: un rectángulo rojo sigue siendo rojo aunque el
    # fondo pase por eq/blur/viñeta (no al revés).
    base = np.full((240, 320, 3), 100, np.uint8)
    global_frame = ge.apply_frame(project, base)
    composed = overlays.apply_frame(project, 0.5, global_frame, sources=sources)
    assert tuple(composed[120, 160]) == (255, 0, 0)


def test_export_real_combina_global_elementos_y_texto(cache: Path, tmp_path: Path) -> None:
    project = _project(_synthetic(tmp_path / "base.mp4")).validate()
    out = tmp_path / "integracion.mp4"
    exporter = export_mod.Exporter()
    exporter.start(project, "calidad", out)
    result = exporter.wait(120)
    assert result.state == "done", result.message
    assert out.is_file() and out.stat().st_size > 0
    probed = media.probe(out)
    assert 0.8 <= probed.duration <= 1.2
