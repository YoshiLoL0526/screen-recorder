"""Comprobaciones estáticas del instalador (A4); la compilación real la hace check_installer.py."""

from __future__ import annotations

from pathlib import Path

ISS = Path(__file__).resolve().parents[1] / "installer" / "rec-studio.iss"


def test_el_iss_empaqueta_el_bundle_y_es_por_usuario() -> None:
    text = ISS.read_text(encoding="utf-8")

    assert 'Source: "..\\dist\\Rec Studio\\*"' in text
    assert "PrivilegesRequired=lowest" in text, "debe instalarse sin UAC"
    assert "OutputBaseFilename=RecStudio-{#MyAppVersion}-setup" in text
    assert "UninstallDisplayIcon={app}\\{#MyAppExeName}" in text
    assert 'Name: "spanish"' in text


def test_el_iss_recibe_la_version_desde_fuera() -> None:
    text = ISS.read_text(encoding="utf-8")

    assert "#ifndef MyAppVersion" in text
    assert "#ifndef MyAppVersion4" in text
    assert "VersionInfoVersion={#MyAppVersion4}" in text
