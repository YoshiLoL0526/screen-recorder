"""Preferencias persistentes (`%APPDATA%/RecStudio/settings.json`)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from typing import Any

from ..platform import paths


@dataclass
class Settings:
    theme: str = "dark"
    source_kind: str = "monitor"
    monitor_index: int = 1
    window_title: str = ""
    region: tuple[int, int, int, int] = (0, 0, 1280, 720)
    fps: int = 60
    preset: str = "media"
    encoder: str = "auto"
    audio_system: bool = True
    keepalive: bool = True
    mic_index: int = -1
    out_dir: str = ""
    countdown_s: int = 0

    def normalized(self) -> "Settings":
        if not self.out_dir:
            self.out_dir = str(paths.videos_dir())
        if not isinstance(self.region, (list, tuple)) or len(self.region) != 4:
            self.region = (0, 0, 1280, 720)
        else:
            self.region = tuple(int(v) for v in self.region)  # type: ignore[assignment]
        known = {f.name for f in fields(Settings)}
        return self

    @classmethod
    def load(cls) -> "Settings":
        path = paths.settings_path()
        if not path.exists():
            return cls().normalized()
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            allowed = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in data.items() if k in allowed}).normalized()
        except (json.JSONDecodeError, OSError, TypeError):
            return cls().normalized()

    def save(self) -> None:
        try:
            paths.settings_path().write_text(
                json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass
