"""Layout de «Grabar» y «Biblioteca»: ningún control se recorta y la lista se desplaza.

Dos bugs reales de la UI (docs/50), reproducidos aquí sin ventana:

1. El panel de configuración de «Grabar» cortaba controles contra el borde derecho de la
   ventana: la fila de la región (cuatro campos de ancho fijo más sus etiquetas) y el
   desplegable del micrófono, cuyos nombres de dispositivo no caben al lado de la etiqueta.
2. La lista de «Biblioteca» no hacía scroll: la columna de la pantalla crecía con el
   contenido (`min-content`), el `virtual_list` recibía un hueco tan alto como su contenido y
   con muchas grabaciones no había nada que desplazar: el final quedaba fuera de la ventana.

Como los de F3/F5, construyen la app real sin ventana (`build_app()` + `app.pilot(size=…)`).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from recstudio import state
from recstudio.platform import paths
from recstudio.platform.audio import AudioDevice
from recstudio.platform.camera import CameraDevice
from recstudio.platform.screens import MonitorInfo
from recstudio.services import library
from recstudio.services.library import Recording
from recstudio.ui.screens.record import PANEL_PAD, PANEL_W

SLACK = 0.5

# Nombres largos a propósito: son los que hacían crecer el panel (un dispositivo real llega a
# 30+ caracteres y el desplegable no trunca sus etiquetas).
MIC = AudioDevice(0, "Microphone (Realtek(R) Audio)", 48000, 2, "input")
CAMERA = CameraDevice("Logitech BRIO Ultra HD Pro Webcam")
MONITORS = (MonitorInfo(1, 0, 0, 3840, 2160, True), MonitorInfo(2, 0, 0, 1920, 1080, False))


def _fake_recordings(count: int) -> tuple[Recording, ...]:
    now = time.time()
    return tuple(
        Recording(
            mp4=Path(f"C:/videos/grabacion-{i:02d}.mp4"),
            thumb=None,
            sidecar=None,
            duration_s=60 + i,
            width=1920,
            height=1080,
            fps=60.0,
            size_bytes=int(12.5e6),
            created_at=now - i * 3600,
            encoder="h264_nvenc",
        )
        for i in range(count)
    )


@pytest.fixture()
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    # La biblioteca se recarga en un hilo al arrancar: con estas dos, el hilo publica lo mismo
    # que el test y el listado es determinista (no depende de las grabaciones del equipo).
    monkeypatch.setattr(library, "list_recordings", lambda out_dir: list(_fake_recordings(24)))
    monkeypatch.setattr(library, "list_recoverable", lambda: [])
    from recstudio.app import build_app

    app, controller = build_app(size=(1280, 800))
    state.out_dir.set(str(tmp_path / "videos"))
    state.monitors.set(MONITORS)
    state.audio_devices.set((MIC,))
    state.camera_devices.set((CAMERA,))
    try:
        yield app, controller
    finally:
        controller.shutdown()
        state.screen.set("record")
        state.source_kind.set("monitor")
        state.mic_index.set(-1)
        state.audio_mode.set("mix")
        state.camera_enabled.set(False)
        state.rec_state.set("idle")
        state.recordings.set(())
        state.recoverable.set(())
        state.monitors.set(())
        state.audio_devices.set(())
        state.camera_devices.set(())
        state.confirm.set(None)
        state.region_editing.set(False)


def _recortes(pilot, size: tuple[float, float]) -> list[str]:
    """Nodos que se salen del panel de configuración o de la ventana, y textos que no caben.

    El panel está pegado al borde derecho de la ventana: su borde de contenido (ancho menos el
    `padding`) es el límite real de los controles.
    """
    panel = pilot.rect("config-scroll")
    assert panel is not None, "no se pintó el panel de configuración"
    left, right = panel[0] + PANEL_PAD, panel[0] + PANEL_W - PANEL_PAD
    problems: list[str] = []
    for node in pilot.layout():
        key = node.get("key") or ""
        x, _y, w, _h = node["rect"]
        text_size, content_size = node.get("text_size"), node.get("content_size")
        if text_size and content_size and (
            text_size[0] > content_size[0] + 1 or text_size[1] > content_size[1] + 1
        ):
            problems.append(f"el texto #{key} no cabe: {text_size} en {content_size}")
        if x >= left - SLACK and x + w > right + SLACK:
            problems.append(f"#{key} se sale del panel ({node['rect']} > {right})")
        if x + w > size[0] + SLACK:
            problems.append(f"#{key} se sale de la ventana ({node['rect']} > {size[0]})")
    return problems


def _nombres(pilot) -> dict[str, str]:
    return {n["key"]: n.get("label") for n in pilot.a11y_tree() if n.get("key") and n.get("label")}


@pytest.mark.parametrize("kind", ["monitor", "window", "region"])
@pytest.mark.parametrize("size", [(1280, 800), (1100, 700), (900, 600)])
def test_panel_de_grabar_no_recorta_controles(ui, kind: str, size) -> None:
    app, _ = ui
    state.screen.set("record")
    state.source_kind.set(kind)
    pilot = app.pilot(size=size)
    pilot.settle()
    assert _recortes(pilot, size) == []


@pytest.mark.parametrize("kind", ["monitor", "window", "region"])
def test_panel_de_grabar_con_camara_y_audio_separado_cabe(ui, kind: str) -> None:
    """Las ramas que más crecen: cámara activa (preview + resolución + fps) y pistas separadas."""
    app, _ = ui
    state.screen.set("record")
    state.source_kind.set(kind)
    state.camera_enabled.set(True)
    state.mic_index.set(MIC.index)
    state.audio_mode.set("separate")
    size = (900, 600)
    pilot = app.pilot(size=size)
    pilot.settle()
    assert _recortes(pilot, size) == []
    # Los controles apilados (nombre de dispositivo) conservan su nombre accesible.
    names = _nombres(pilot)
    assert names["sel-mic"] == "Micrófono"
    assert names["sel-camera"] == "Dispositivo"


def test_panel_de_grabar_ensena_cada_rama_de_fuente(ui) -> None:
    """Cada `source_kind` pinta sus campos, y siguen ahí al cambiar de rama."""
    app, _ = ui
    state.screen.set("record")
    pilot = app.pilot(size=(1280, 800))
    pilot.settle()
    state.source_kind.set("window")
    pilot.settle()
    assert pilot.rect("win-title") is not None
    state.source_kind.set("region")
    pilot.settle()
    for key in ("rx", "ry", "rw", "rh"):
        assert pilot.rect(key) is not None
    assert _recortes(pilot, (1280, 800)) == []
    state.source_kind.set("monitor")
    pilot.settle()
    assert pilot.rect("sel-monitor") is not None


def test_biblioteca_con_muchas_grabaciones_hace_scroll(ui) -> None:
    app, _ = ui
    state.recordings.set(_fake_recordings(24))
    state.screen.set("library")
    size = (1280, 800)
    pilot = app.pilot(size=size)
    pilot.settle()
    assert len(state.recordings()) == 24

    lista = pilot.rect("library-list")
    assert lista is not None
    _x, y, _w, h = lista
    assert y + h <= size[1] + SLACK, "la lista se sale de la ventana: no cabe el scroll"
    # La cabecera y la sección «Interrumpida» quedan fijas: la lista empieza más abajo.
    cabecera = pilot.rect("rec-0")
    assert cabecera is not None and cabecera[1] >= y - SLACK
    assert pilot.scroll_offset("library-list") == (0.0, 0.0)

    for _ in range(8):  # 24 filas de 108 px + gap: el final está a ~2172 px
        pilot.scroll("library-list", dy=400)
    pilot.settle()

    offset = pilot.scroll_offset("library-list")
    assert offset is not None and offset[1] > h, "la lista no se desplazó"
    ultima = pilot.rect("rec-23")
    assert ultima is not None, "la última grabación no llega a construirse: no se puede recorrer"
    assert ultima[1] >= y - SLACK and ultima[1] + ultima[3] <= y + h + SLACK
    # La lista no se ha movido (la cabecera no scrollea con el contenido).
    assert pilot.rect("library-list") == lista


def test_biblioteca_mantiene_la_seccion_interrumpida_fija(ui) -> None:
    app, _ = ui
    state.recordings.set(_fake_recordings(24))
    state.recoverable.set((Path("C:/videos/Proyectos/2026-11-24_10-37-09"),))
    state.screen.set("library")
    pilot = app.pilot(size=(1280, 800))
    pilot.settle()
    recuperar = pilot.rect("recov-2026-11-24_10-37-09")
    assert recuperar is not None
    for _ in range(8):
        pilot.scroll("library-list", dy=400)
    pilot.settle()
    assert pilot.rect("recov-2026-11-24_10-37-09") == recuperar


def test_biblioteca_vacia_ocupa_el_hueco(ui) -> None:
    app, _ = ui
    state.recordings.set(())
    state.screen.set("library")
    pilot = app.pilot(size=(1280, 800))
    pilot.settle()
    assert pilot.rect("library-list") is None
