"""Testes do carregamento centralizado de configurações."""

from pathlib import Path

import pytest

import gmr_pdf.settings as settings_module


def test_get_settings_loads_yaml_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """YAML fornece os defaults e o ambiente fornece os segredos."""
    monkeypatch.setenv(
        "GMR_GOOGLE_SERVICE_ACCOUNT_FILE", "config/credentials/fake.json"
    )
    monkeypatch.setenv("GMR_GOOGLE_DRIVE_FOLDER_ID", "folder-123")

    settings_module.get_settings.cache_clear()
    settings = settings_module.get_settings()
    settings_module.get_settings.cache_clear()

    assert settings.drive_folder_id == "folder-123"
    assert (
        settings.service_account_file
        == settings_module.BASE_DIR / "config" / "credentials" / "fake.json"
    )
    assert settings.google_drive.api_service == "drive"
    assert settings.google_drive.api_version == "v3"
    assert settings.google_drive.scopes == [
        "https://www.googleapis.com/auth/drive.readonly"
    ]
    assert settings.google_drive.download_chunk_size_bytes % (256 * 1024) == 0


def test_absolute_service_account_path_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caminhos absolutos do .env não são combinados com o BASE_DIR."""
    absolute = Path("/tmp/opencode/service_account.json").resolve()
    monkeypatch.setenv("GMR_GOOGLE_SERVICE_ACCOUNT_FILE", str(absolute))
    monkeypatch.setenv("GMR_GOOGLE_DRIVE_FOLDER_ID", "folder-123")

    settings_module.get_settings.cache_clear()
    settings = settings_module.get_settings()
    settings_module.get_settings.cache_clear()

    assert settings.service_account_file == absolute
