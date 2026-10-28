"""Sustituto mínimo de `cv2` para el bundle sin OpenCV (A5).

`windows-capture` importa OpenCV en su `__init__` solo para `Frame.save_as_image()`; Rec Studio
nunca llama a ese método (copia `frame.frame_buffer` directamente). El build excluye `cv2` porque
son ~112 MB, así que antes de importar `windows_capture` se instala este módulo si el real no está.
`imwrite` se implementa con Pillow para que `save_as_image()` siga funcionando si alguien lo usa.

El backend `dxcam` sí necesita OpenCV de verdad: `is_stub()` permite detectarlo y dar un error
explicable en vez de un `AttributeError` a mitad de la captura.
"""

from __future__ import annotations

import sys
import types

_MARK = "__recstudio_cv2_stub__"


def install() -> None:
    """Deja un `cv2` importable: el real si existe, o el sustituto marcado."""
    if "cv2" in sys.modules:
        return
    try:
        import cv2  # noqa: F401  (el wheel completo, en desarrollo)
        return
    except ImportError:
        pass

    module = types.ModuleType("cv2")
    module.__doc__ = "Sustituto mínimo de OpenCV incluido con Rec Studio (solo Windows)."

    def imwrite(path: str, image, *args, **kwargs) -> bool:  # noqa: ANN001, ANN002, ANN003
        from PIL import Image

        Image.fromarray(image).save(path)
        return True

    module.imwrite = imwrite  # type: ignore[attr-defined]
    setattr(module, _MARK, True)
    sys.modules["cv2"] = module


def is_stub() -> bool:
    """`True` si el `cv2` cargado es el sustituto (bundle sin OpenCV)."""
    module = sys.modules.get("cv2")
    return bool(module is not None and getattr(module, _MARK, False))
