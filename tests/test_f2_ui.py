"""Tests de F2: ajustes persistentes, biblioteca y estado de la UI (sin ventana)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from recstudio import state
from recstudio.platform import paths
from recstudio.services import library
from recstudio.services.settings import Settings


# -- settings -----------------------------------------------------------------

def test_settings_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "settings_path", lambda: tmp_path / "settings.json")
    settings = Settings(theme="light", fps=30, preset="alta", region=(10, 20, 640, 480))
    settings.save()

    loaded = Settings.load()
    assert loaded.theme == "light"
    assert loaded.fps == 30
    assert loaded.preset == "alta"
    assert loaded.region == (10, 20, 640, 480)
    assert loaded.out_dir  # relleno con la carpeta por defecto


def test_settings_corrupt_file_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{esto no es json", encoding="utf-8")
    monkeypatch.setattr(paths, "settings_path", lambda: path)
    loaded = Settings.load()
    assert loaded.theme == "dark"
    assert loaded.fps == 60


def test_settings_ignores_unknown_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"fps": 24, "clave_vieja": 1}), encoding="utf-8")
    monkeypatch.setattr(paths, "settings_path", lambda: path)
    assert Settings.load().fps == 24


# -- biblioteca ---------------------------------------------------------------

def _fake_recording(folder: Path, name: str, *, duration: float = 12.5, with_thumb: bool = True) -> Path:
    mp4 = folder / f"{name}.mp4"
    mp4.write_bytes(b"\x00" * 2048)
    mp4.with_suffix(".json").write_text(
        json.dumps({"duration_s": duration, "width": 1920, "height": 1080, "fps": 60,
                    "encoder": "h264_nvenc", "backend": "wgc"}), encoding="utf-8")
    if with_thumb:
        mp4.with_suffix(".jpg").write_bytes(b"\xff\xd8\xff\xd9")
    return mp4


def test_list_recordings_parsea_sidecar(tmp_path: Path) -> None:
    _fake_recording(tmp_path, "sesion-a", duration=90.0)
    _fake_recording(tmp_path, "sesion-b", duration=5.0, with_thumb=False)

    items = library.list_recordings(tmp_path)
    assert len(items) == 2
    by_name = {item.name: item for item in items}
    first = by_name["sesion-a"]
    assert first.thumb is not None and first.sidecar is not None
    assert first.encoder == "h264_nvenc"
    assert first.duration_label == "01:30"
    assert by_name["sesion-b"].thumb is None
    assert by_name["sesion-b"].duration_label == "00:05"
    assert all(item.size_mb > 0 for item in items)


def test_list_recordings_sin_sidecar_no_revienta(tmp_path: Path) -> None:
    (tmp_path / "suelto.mp4").write_bytes(b"\x00" * 100)
    items = library.list_recordings(tmp_path)
    assert len(items) == 1
    assert items[0].duration_s == 0.0
    assert items[0].thumb is None


def test_list_recordings_incluye_recuperadas_marcadas(tmp_path: Path) -> None:
    _fake_recording(tmp_path, "normal")
    _fake_recording(tmp_path, "2026-01-01_10-00-00-recuperada")
    items = {item.name: item for item in library.list_recordings(tmp_path)}
    assert set(items) == {"normal", "2026-01-01_10-00-00-recuperada"}
    assert items["2026-01-01_10-00-00-recuperada"].recovered
    assert not items["normal"].recovered


def test_duration_label_con_horas() -> None:
    rec = library.Recording(
        mp4=Path("x.mp4"), thumb=None, sidecar=None, duration_s=3725.0, width=0, height=0,
        fps=0.0, size_bytes=0, created_at=0.0,
    )
    assert rec.duration_label == "1:02:05"


# -- estado de la UI ----------------------------------------------------------

def test_toasts_se_encolan_y_auto_cierran() -> None:
    state.toasts.set(())
    state.toast("info", "uno")
    state.toast("success", "dos")
    items = state.toasts()
    assert [t.text for t in items] == ["uno", "dos"]
    assert items[0].id != items[1].id

    state.drop_toast(items[0].id)
    assert [t.text for t in state.toasts()] == ["dos"]

    deadline = time.perf_counter() + 6.0
    while state.toasts() and time.perf_counter() < deadline:
        time.sleep(0.2)
    assert state.toasts() == ()  # el auto-cierre de 4 s funciona


def test_theme_por_nombre() -> None:
    state.set_theme("light")
    assert state.theme_name() == "light"
    assert state.theme().colors["background"].startswith("#F")
    state.set_theme("dark")
    assert state.theme().colors["rec"] == "#FF5470"


def test_confirm_es_comparable() -> None:
    a = state.Confirm(action="delete", title="t", message="m", target="x.mp4")
    b = state.Confirm(action="delete", title="t", message="m", target="x.mp4")
    assert a == b  # Signal.set no dispararía un rebuild con el mismo modal
