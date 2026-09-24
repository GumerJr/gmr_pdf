"""Testes do logging centralizado."""

import logging

from gmr_pdf.logger import BrasiliaFormatter, get_logger


def _root() -> logging.Logger:
    return logging.getLogger("gmr_pdf")


def _own_handlers() -> list[logging.Handler]:
    """Handlers do projeto (ignora os injetados pelo pytest)."""
    return [
        h
        for h in _root().handlers
        if isinstance(getattr(h, "formatter", None), BrasiliaFormatter)
    ]


def test_root_logger_has_single_handler() -> None:
    get_logger("gmr_pdf.a")
    get_logger("gmr_pdf.b")
    assert len(_own_handlers()) == 1


def test_root_level_comes_from_settings_yaml() -> None:
    get_logger("gmr_pdf.settings_test")
    assert _root().level == logging.INFO  # config/settings.yaml: logging.level


def test_child_logger_belongs_to_gmr_pdf_hierarchy() -> None:
    logger = get_logger("gmr_pdf.drive")
    assert logger.parent is _root()


def test_formatter_uses_brasilia_offset_and_emoji() -> None:
    get_logger("gmr_pdf.format_test")
    handler = _own_handlers()[0]
    record = logging.LogRecord(
        name="gmr_pdf.format_test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="mensagem de teste",
        args=(),
        exc_info=None,
    )
    output = handler.format(record)
    assert "-0300" in output  # UTC-3 (Brasília)
    assert "ℹ️" in output
    assert "mensagem de teste" in output
