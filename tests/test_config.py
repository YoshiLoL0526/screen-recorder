"""Validación de la configuración (core puro, sin hardware)."""

from __future__ import annotations

import pytest

from recstudio.core.config import AudioConfig, RecordingConfig, SourceConfig
from recstudio.core.errors import ConfigError


def test_defaults_are_valid() -> None:
    config = RecordingConfig().validate()
    assert config.fps == 60
    assert config.preset == "media"
    assert config.quality["cq"] > 0


def test_fps_choices() -> None:
    RecordingConfig(fps=30).validate()
    with pytest.raises(ConfigError):
        RecordingConfig(fps=59).validate()


def test_preset_unknown() -> None:
    with pytest.raises(ConfigError):
        RecordingConfig(preset="ultra").validate()


def test_monitor_index_is_one_based() -> None:
    with pytest.raises(ConfigError):
        SourceConfig(monitor_index=0).validate()
    SourceConfig(monitor_index=1).validate()


def test_window_requires_title() -> None:
    with pytest.raises(ConfigError):
        SourceConfig(kind="window").validate()
    SourceConfig(kind="window", window_title="Bloc de notas").validate()


def test_region_requires_size() -> None:
    with pytest.raises(ConfigError):
        SourceConfig(kind="region").validate()
    with pytest.raises(ConfigError):
        SourceConfig(kind="region", region=(0, 0, 4, 4)).validate()
    source = SourceConfig(kind="region", region=(10, 10, 640, 480)).validate()
    assert source.is_region


def test_backend_choices() -> None:
    SourceConfig(backend="dxcam").validate()
    with pytest.raises(ConfigError):
        SourceConfig(backend="opengl").validate()


def test_audio_config_any() -> None:
    assert AudioConfig(system=True).any
    assert AudioConfig(system=False, mic_device_index=3).any
    assert not AudioConfig(system=False).any


def test_with_source_keeps_frozen_contract() -> None:
    config = RecordingConfig()
    changed = config.with_source(kind="region", region=(0, 0, 320, 240))
    assert changed.source.kind == "region"
    assert config.source.kind == "monitor"  # el original no cambia
