"""Tests del filmstrip: la tira de miniaturas por asset y su colocación (pura) en el timeline."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from recstudio.core.project import Clip, MediaAsset
from recstudio.platform import ffmpeg as F
from recstudio.platform import paths
from recstudio.services import media
from recstudio.ui import timeline as tlui


def _asset(duration: float = 12.0) -> MediaAsset:
    return MediaAsset(id="a1", path="C:/v/a1.mp4", duration=duration, width=1920, height=1080,
                      fps=30.0, has_audio=True)


def _indices(slots) -> list[int]:  # noqa: ANN001
    return [index for _, _, index in slots]


# -- colocación (pura, sin medios) -------------------------------------------------


def test_con_el_clip_entero_salen_todas_las_miniaturas() -> None:
    clip = Clip("c1", "a1", in_=0.0, out_=12.0)
    slots = tlui.filmstrip_slots(clip, _asset(), media.THUMB_COUNT, zoom=240.0, x=0.0,
                                 w=12.0 * 240, x0=-1e9, x1=1e9)

    assert _indices(slots) == list(range(media.THUMB_COUNT))
    step = 12.0 / media.THUMB_COUNT
    for index, (left, width, _) in enumerate(slots):
        # el primer borde se pega al clip (+1 px), el resto cae en su tiempo de fuente
        assert left == pytest.approx(max(1.0, index * step * 240), abs=0.5)
        assert width == pytest.approx(step * 240, abs=1.5)  # el primer hueco pierde 1 px


def test_un_clip_recortado_usa_las_miniaturas_de_su_tramo() -> None:
    """El clip empieza en el segundo 6: sus miniaturas son las de la segunda mitad del asset."""
    clip = Clip("c1", "a1", in_=6.0, out_=9.0)
    slots = tlui.filmstrip_slots(clip, _asset(), media.THUMB_COUNT, zoom=240.0, x=0.0,
                                 w=3.0 * 240, x0=-1e9, x1=1e9)

    indices = _indices(slots)
    assert indices and min(indices) >= 12 and max(indices) <= 18
    assert slots[0][0] == pytest.approx(0.0, abs=1.0)  # la primera cae al inicio del clip


def test_la_velocidad_escala_los_huecos() -> None:
    lento = Clip("c1", "a1", in_=0.0, out_=4.0, speed=0.5)  # 8 s en el timeline
    rapido = Clip("c2", "a1", in_=0.0, out_=4.0, speed=2.0)  # 2 s en el timeline
    zoom = 300.0
    slots_lento = tlui.filmstrip_slots(lento, _asset(), 24, zoom, 0.0, lento.duration * zoom, -1e9, 1e9)
    slots_rapido = tlui.filmstrip_slots(rapido, _asset(), 24, zoom, 0.0, rapido.duration * zoom, -1e9, 1e9)

    assert len(slots_lento) == len(slots_rapido) == 8  # los dos cubren 4 s de fuente
    # el clip lento dura el doble en el timeline: cada miniatura ocupa el cuádruple de ancho
    assert slots_lento[1][1] == pytest.approx(slots_rapido[1][1] * 4, rel=0.02)


def test_el_culling_deja_fuera_lo_que_no_se_ve() -> None:
    clip = Clip("c1", "a1", in_=0.0, out_=12.0)
    slots = tlui.filmstrip_slots(clip, _asset(), 24, 240.0, 0.0, 12.0 * 240, x0=1000.0, x1=1500.0)

    assert slots, "la vista cae dentro del clip: debería haber alguna miniatura"
    for left, width, _ in slots:
        assert left < 1500.0 and left + width > 1000.0


def test_con_el_zoom_bajo_las_miniaturas_se_ensanchan_para_solaparse() -> None:
    clip = Clip("c1", "a1", in_=0.0, out_=12.0)
    zoom = 20.0  # huecos de 0.5 s * 20 = 10 px: por debajo del mínimo de dibujo
    slots = tlui.filmstrip_slots(clip, _asset(), 24, zoom, 0.0, 12.0 * zoom, -1e9, 1e9)

    assert slots
    for left, width, _ in slots:
        assert width > 10.0, "debería dibujarse más ancho que su hueco (solape)"
        assert left + width <= 12.0 * zoom, "nunca se sale del clip"


def test_un_clip_fuera_de_la_vista_no_aporta_nada() -> None:
    clip = Clip("c1", "a1", in_=0.0, out_=12.0)
    assert tlui.filmstrip_slots(clip, _asset(), 24, 60.0, 0.0, 720.0, x0=5000.0, x1=6000.0) == []
    assert tlui.filmstrip_slots(clip, _asset(), 0, 60.0, 0.0, 720.0, -1e9, 1e9) == []


# -- medios (FFmpeg real) ----------------------------------------------------------


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("filmstrip") / "demo.mp4"
    try:
        subprocess.run(
            [F.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=4",
             "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-pix_fmt", "yuv420p", str(path)],
            check=True, capture_output=True,
        )
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sin FFmpeg: {exc}")
    return path


def test_thumbstrip_genera_la_tira_y_la_cachea(video: Path, tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
    media._thumbstrips.clear()

    thumbs = media.thumbstrip(video)

    assert len(thumbs) == media.THUMB_COUNT
    assert all(t.startswith(b"\xff\xd8") for t in thumbs), "deben ser JPEG"
    assert len(set(thumbs)) > media.THUMB_COUNT // 2, "los frames deben ser distintos"

    # segunda vez: sale de la cache en memoria (sin decodificar)
    media._thumbstrips.clear()
    assert media.cached_thumbstrip(video) == thumbs


def test_sin_cache_no_hay_miniaturas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
    media._thumbstrips.clear()
    assert media.cached_thumbstrip(tmp_path / "no-existe.mp4") is None


def test_una_cache_a_medias_no_se_usa_y_se_regenera(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El CI destapó un JPEG de 0 bytes cacheado mientras el hilo escribía (A1)."""
    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
    media._thumbstrips.clear()
    folder = media._cache_dir("thumbs") / media._cache_key(video)
    folder.mkdir(parents=True)
    for index in range(media.THUMB_COUNT):
        (folder / f"{index:02d}.jpg").write_bytes(b"" if index == 7 else b"\xff\xd8\xff\xd9")

    assert media.cached_thumbstrip(video) is None, "una tira incompleta no debe cachearse"

    thumbs = media.thumbstrip(video)
    assert len(thumbs) == media.THUMB_COUNT
    assert all(data.startswith(b"\xff\xd8") and len(data) > 4 for data in thumbs)
    assert media.cached_thumbstrip(video) == thumbs
    assert not list(folder.glob("*.tmp")), "las escrituras atómicas no dejan temporales"
