"""Tests de los diálogos nativos (`aurora.dialogs`) y de su uso desde los controladores.

Un test sin pantalla no puede abrir un diálogo real, así que se sustituye el adaptador
(`recstudio.ui.file_dialogs`) por un doble, como recomienda la propia Aurora.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recstudio import state
from recstudio.platform import paths
from recstudio.ui import file_dialogs


# -- adaptador ---------------------------------------------------------------------


def test_open_videos_devuelve_paths_y_pasa_los_filtros(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict = {}

    def fake_open_files(*, title=None, directory=None, filters=None):  # noqa: ANN001, ANN202
        calls.update(title=title, directory=directory, filters=filters)
        return ["C:/videos/a.mp4", "C:/videos/b.mkv"]

    monkeypatch.setattr(file_dialogs.dialogs, "open_files", fake_open_files)
    chosen = file_dialogs.open_videos(Path("C:/videos"), title="Importar")

    assert chosen == [Path("C:/videos/a.mp4"), Path("C:/videos/b.mkv")]
    assert all(isinstance(p, Path) for p in chosen)
    assert calls["title"] == "Importar"
    assert calls["directory"] == Path("C:/videos")
    assert ("Videos", ("mp4", "mkv", "mov", "webm", "avi")) in calls["filters"]
    assert ("Todos los archivos", ("*",)) in calls["filters"]


def test_open_videos_cancelado_es_lista_vacia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(file_dialogs.dialogs, "open_files", lambda **_kw: [])
    assert file_dialogs.open_videos() == []


def test_pick_folder_devuelve_path_o_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(file_dialogs.dialogs, "pick_folder", lambda **_kw: None)
    assert file_dialogs.pick_folder() is None

    monkeypatch.setattr(file_dialogs.dialogs, "pick_folder", lambda **_kw: "C:/Users/x/Videos")
    assert file_dialogs.pick_folder() == Path("C:/Users/x/Videos")


# -- uso desde los controladores ---------------------------------------------------


@pytest.fixture()
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    from recstudio.app import build_app

    app, controller = build_app(size=(1280, 800))
    state.out_dir.set(str(tmp_path / "videos"))
    pilot = app.pilot(size=(1280, 800))
    pilot.settle()
    try:
        yield controller
    finally:
        controller.shutdown()
        state.toasts.set(())
        state.screen.set("record")


def _menciona(texto: str) -> bool:
    return any(texto in t.text for t in state.toasts())


def test_browse_out_dir_aplica_la_carpeta_elegida(ui, tmp_path: Path, monkeypatch) -> None:
    chosen = tmp_path / "Grabaciones"
    monkeypatch.setattr(file_dialogs, "pick_folder", lambda *a, **k: chosen)
    state.toasts.set(())

    ui.browse_out_dir()

    assert state.out_dir() == str(chosen)
    assert _menciona(str(chosen))


def test_browse_out_dir_cancelado_no_cambia_nada(ui, monkeypatch) -> None:
    before = state.out_dir()
    monkeypatch.setattr(file_dialogs, "pick_folder", lambda *a, **k: None)
    state.toasts.set(())

    ui.browse_out_dir()

    assert state.out_dir() == before
    assert state.toasts() == ()


def test_un_fallo_del_dialogo_avisa_con_toast(ui, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("sin ventana")

    monkeypatch.setattr(file_dialogs, "pick_folder", boom)
    state.toasts.set(())

    ui.browse_out_dir()  # no debe propagar la excepción

    assert _menciona("No se pudo abrir el diálogo")


def test_browse_import_pasa_lo_elegido_al_importador(ui, tmp_path: Path, monkeypatch) -> None:
    editor = ui.editor
    chosen = [tmp_path / "uno.mp4", tmp_path / "dos.mkv"]
    monkeypatch.setattr(file_dialogs, "open_videos", lambda *a, **k: chosen)
    got: list[Path] = []
    monkeypatch.setattr(editor, "import_paths", lambda paths, **kw: got.extend(paths))

    editor.browse_import()

    assert got == chosen


def test_browse_import_cancelado_no_importa(ui, monkeypatch) -> None:
    editor = ui.editor
    monkeypatch.setattr(file_dialogs, "open_videos", lambda *a, **k: [])
    got: list[Path] = []
    monkeypatch.setattr(editor, "import_paths", lambda paths, **kw: got.extend(paths))

    editor.browse_import()

    assert got == []
