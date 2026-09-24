"""Logging centralizado do projeto gmr_pdf.

- Fuso horário de Brasília (``America/Sao_Paulo``, UTC-3) em todos os registros;
- Saída colorida no console via ``colorama``;
- Emojis profissionais por nível de severidade;
- Configuração externa em ``config/settings.yaml`` (seção ``logging``) —
  nenhum valor hardcoded.

Uso::

    from gmr_pdf.logger import get_logger

    logger = get_logger(__name__)
    logger.info("✅ Processo concluído")
"""

import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import colorama
import yaml
from colorama import Fore, Style

from gmr_pdf.settings import SETTINGS_FILE

_LOGGER_ROOT = "gmr_pdf"
_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

_DEFAULTS = {
    "level": "INFO",
    "timezone": "America/Sao_Paulo",
    "date_format": "%Y-%m-%d %H:%M:%S %z",
}

_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG: Fore.CYAN,
    logging.INFO: Fore.BLUE,
    logging.WARNING: Fore.YELLOW,
    logging.ERROR: Fore.RED,
    logging.CRITICAL: Fore.MAGENTA,
}

_LEVEL_EMOJIS: dict[int, str] = {
    logging.DEBUG: "🔍",
    logging.INFO: "ℹ️",
    logging.WARNING: "⚠️",
    logging.ERROR: "❌",
    logging.CRITICAL: "🚨",
}


def _load_logging_config() -> dict[str, str]:
    """Lê a seção ``logging`` do settings.yaml (com defaults seguros)."""
    config = dict(_DEFAULTS)
    try:
        with SETTINGS_FILE.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        section = data.get("logging", {})
        for key in _DEFAULTS:
            if key in section:
                config[key] = str(section[key])
    except FileNotFoundError:
        pass
    return config


class BrasiliaFormatter(logging.Formatter):
    """Formatter com horário de Brasília, cores e emojis por severidade."""

    def __init__(self, *, tz: ZoneInfo, datefmt: str) -> None:
        super().__init__(fmt=_LOG_FORMAT, datefmt=datefmt)
        self._tz = tz

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=self._tz)
        return dt.strftime(datefmt or self.datefmt or _DEFAULTS["date_format"])

    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelno, "")
        emoji = _LEVEL_EMOJIS.get(record.levelno, "")
        record.levelname = f"{color}{emoji} {record.levelname:<8}{Style.RESET_ALL}"
        return super().format(record)


def _setup_root_logger() -> None:
    """Configura uma única vez o logger raiz ``gmr_pdf``."""
    root = logging.getLogger(_LOGGER_ROOT)
    if root.handlers:
        return
    config = _load_logging_config()
    colorama.init(autoreset=True)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        BrasiliaFormatter(
            tz=ZoneInfo(config["timezone"]),
            datefmt=config["date_format"],
        )
    )
    root.addHandler(handler)
    root.setLevel(config["level"])
    root.propagate = False


def get_app_timezone() -> ZoneInfo:
    """Fuso horário da aplicação conforme ``settings.yaml`` (padrão UTC-3)."""
    return ZoneInfo(_load_logging_config()["timezone"])


def get_logger(name: str) -> logging.Logger:
    """Retorna um logger configurado da hierarquia ``gmr_pdf``.

    Passar ``__name__`` do módulo mantém a hierarquia automática
    (ex.: ``gmr_pdf.drive`` herda o handler/coloração do raiz).
    """
    _setup_root_logger()
    return logging.getLogger(name)
