"""Tests del build ligero (A5): sustituto de cv2 y error claro del backend dxcam."""

from __future__ import annotations

import builtins
import sys
from pathlib import Path

import numpy as np
import pytest

from recstudio.core.config import SourceConfig
from recstudio.core.errors import CaptureError
from recstudio.platform import capture, cv2_shim


@pytest.fixture()
def sin_cv2(monkeypatch: pytest.MonkeyPatch):
    """Quita el cv2 real y hace que `import cv2` falle, como en el bundle sin OpenCV."""
    real = sys.modules.pop("cv2", None)
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if name == "cv2":
            module = sys.modules.get("cv2")
            if module is not None:  # el sustituto ya instalado
                return module
            raise ImportError("No module named 'cv2'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    try:
        yield
    finally:
        sys.modules.pop("cv2", None)
        if real is not None:
            sys.modules["cv2"] = real


def test_el_sustituto_se_instala_y_guarda_con_pillow(sin_cv2, tmp_path: Path) -> None:
    assert not cv2_shim.is_stub()
    cv2_shim.install()

    assert cv2_shim.is_stub()
    image = np.zeros((4, 6, 3), dtype=np.uint8)
    assert sys.modules["cv2"].imwrite(str(tmp_path / "frame.png"), image) is True
    assert (tmp_path / "frame.png").is_file()


def test_el_sustituto_es_idempotente(sin_cv2) -> None:
    cv2_shim.install()
    stub = sys.modules["cv2"]
    cv2_shim.install()
    assert sys.modules["cv2"] is stub


def test_dxcam_avisa_en_vez_de_fallar_dentro_del_hook(sin_cv2) -> None:
    cv2_shim.install()
    backend = capture.DxcamBackend(SourceConfig(kind="monitor", monitor_index=1))

    with pytest.raises(CaptureError, match="no incluye OpenCV"):
        backend.start(60)


@pytest.mark.skipif(sys.platform != "win32", reason="windows-capture solo existe en Windows")
def test_windows_capture_importa_con_el_sustituto(sin_cv2) -> None:
    cv2_shim.install()
    wc = capture._windows_capture()
    assert callable(wc.WindowsCapture)
    assert callable(wc.DxgiDuplicationSession)
