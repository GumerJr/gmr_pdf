"""Exportação JSON validada (níveis 1 e 2 do pipeline).

- ``extraction_payload``: espelho bruto e fiel da extração (spans);
- ``tables_payload``: grids reconstruídos pela inteligência espacial.

Ambos passam por validação pydantic e carimbam data/hora em UTC-3
(America/Sao_Paulo) — essencial para trilha de auditoria.
"""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from gmr_pdf.extractor import DocumentData
from gmr_pdf.logger import get_app_timezone, get_logger
from gmr_pdf.models import (
    CellPayload,
    ExtractionPayload,
    GridPayload,
    PagePayload,
    SpanPayload,
    TablesPayload,
)
from gmr_pdf.settings import get_settings
from gmr_pdf.spatial import TableGrid

logger = get_logger(__name__)


def _base_fields() -> dict[str, str | datetime]:
    settings = get_settings()
    return {
        "generated_at": datetime.now(get_app_timezone()),
        "app": settings.app.name,
        "app_version": settings.app.version,
        "folder_id": settings.drive_folder_id,
    }


def extraction_payload(document: DocumentData) -> ExtractionPayload:
    """Monta o payload bruto (nível 1) a partir do documento extraído."""
    pages: list[PagePayload] = []
    for page in document.pages:
        spans = [
            SpanPayload(
                text=span.text,
                origin=span.origin,
                bbox=span.bbox,
                size=span.size,
                color=span.color,
                font=span.font,
                bold=span.is_bold,
                italic=span.is_italic,
                monospaced=span.is_monospaced,
            )
            for block in page.blocks
            for line in block.lines
            for span in line.spans
        ]
        pages.append(
            PagePayload(
                number=page.number,
                width=page.width,
                height=page.height,
                needs_ocr=page.needs_ocr,
                spans=spans,
            )
        )
    return ExtractionPayload(
        **_base_fields(),  # type: ignore[arg-type]
        page_count=document.page_count,
        span_count=document.span_count,
        pages=pages,
    )


def tables_payload(grids: list[TableGrid]) -> TablesPayload:
    """Monta o payload estruturado (nível 2) a partir dos grids."""
    return TablesPayload(
        **_base_fields(),  # type: ignore[arg-type]
        grids=[
            GridPayload(
                page=grid.page_number,
                orientation=grid.orientation,
                n_rows=grid.n_rows,
                n_cols=grid.n_cols,
                cells=[
                    CellPayload(
                        row=cell.row_index,
                        col=cell.col_index,
                        text=cell.text,
                        bbox=cell.bbox,
                    )
                    for cell in grid.cells
                ],
            )
            for grid in grids
        ],
    )


def to_json(payload: BaseModel) -> str:
    """Serializa o payload validado em JSON (UTF-8, indentado)."""
    return payload.model_dump_json(indent=2)


def save_json(payload: BaseModel, path: Path) -> Path:
    """Persiste o payload JSON e registra o log de auditoria."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json(payload), encoding="utf-8")
    logger.info("💾 JSON salvo: %s (%d bytes)", path, path.stat().st_size)
    return path
