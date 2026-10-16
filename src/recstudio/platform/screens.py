"""Monitores y capturas de referencia."""

from __future__ import annotations

import io
from dataclasses import dataclass

from ..core.errors import CaptureError


@dataclass(frozen=True)
class MonitorInfo:
    index: int  # 1-based (coincide con windows-capture y mss)
    left: int
    top: int
    width: int
    height: int
    primary: bool

    @property
    def label(self) -> str:
        star = " (principal)" if self.primary else ""
        return f"Monitor {self.index} · {self.width}×{self.height}{star}"


def list_monitors() -> list[MonitorInfo]:
    """Monitores físicos (mss: el índice 0 es el escritorio virtual, se omite)."""
    import mss

    sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
    try:
        infos: list[MonitorInfo] = []
        for i, mon in enumerate(sct.monitors[1:], start=1):
            infos.append(
                MonitorInfo(
                    index=i,
                    left=int(mon["left"]),
                    top=int(mon["top"]),
                    width=int(mon["width"]),
                    height=int(mon["height"]),
                    primary=(i == 1),
                )
            )
        return infos
    finally:
        sct.close()


def monitor_size(monitor_index: int = 1) -> tuple[int, int]:
    for mon in list_monitors():
        if mon.index == monitor_index:
            return mon.width, mon.height
    raise CaptureError(f"no existe el monitor {monitor_index}")


def capture_reference_png(monitor_index: int = 1) -> bytes:
    """Captura el monitor como PNG en memoria (para elegir la región en la UI)."""
    import mss

    sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
    try:
        monitors = sct.monitors
        if not 1 <= monitor_index < len(monitors):
            raise CaptureError(f"no existe el monitor {monitor_index}")
        shot = sct.grab(monitors[monitor_index])
        png = shot.png
        return bytes(png)
    finally:
        sct.close()
