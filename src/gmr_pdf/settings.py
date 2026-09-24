"""Configurações centralizadas do projeto.

- Não sensíveis: ``config/settings.yaml``
- Sensíveis/identificadores: ``.env`` (prefixo ``GMR_``)

Uso::

    from gmr_pdf.settings import get_settings

    settings = get_settings()
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = BASE_DIR / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.yaml"
ENV_FILE = BASE_DIR / ".env"


class AppConfig(BaseModel):
    """Configurações gerais da aplicação."""

    name: str = "gmr_pdf"
    version: str = "0.1.0"
    environment: str = "development"


class GoogleDriveConfig(BaseModel):
    """Parâmetros da API do Google Drive (não sensíveis)."""

    api_service: str = "drive"
    api_version: str = "v3"
    scopes: list[str] = Field(
        default_factory=lambda: ["https://www.googleapis.com/auth/drive.readonly"]
    )
    download_chunk_size_bytes: int = 10 * 1024 * 1024
    list_page_size: int = 100


class _EnvSettings(BaseSettings):
    """Variáveis sensíveis lidas exclusivamente do arquivo .env."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_prefix="GMR_",
        extra="ignore",
    )

    google_service_account_file: Path
    google_drive_folder_id: str


class Settings(BaseModel):
    """Objeto único com todas as configurações da aplicação."""

    app: AppConfig
    google_drive: GoogleDriveConfig
    service_account_file: Path
    drive_folder_id: str


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de configuração não encontrado: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache
def get_settings() -> Settings:
    """Carrega as configurações (cacheadas) a partir do YAML + .env."""
    raw = _load_yaml(SETTINGS_FILE)
    env = _EnvSettings()

    service_account_file = env.google_service_account_file
    if not service_account_file.is_absolute():
        service_account_file = BASE_DIR / service_account_file

    return Settings(
        app=AppConfig(**raw.get("app", {})),
        google_drive=GoogleDriveConfig(**raw.get("google_drive", {})),
        service_account_file=service_account_file,
        drive_folder_id=env.google_drive_folder_id,
    )
