"""Schemas pydantic da fronteira de serialização (JSON auditável).

O pydantic atua aqui como contrato de saída: documentos extraídos e
grids reconstruídos são validados antes de virar JSON — o produto de
dados usado na comparação com o cadastro. O PDF reconstruído permanece
como artefato de QA visual (ver ``docs/arquitetura.md``).
"""

from datetime import datetime

from pydantic import BaseModel, Field


class SpanPayload(BaseModel):
    """Span serializado: texto + geometria + tipografia (lossless)."""

    text: str
    origin: tuple[float, float]
    bbox: tuple[float, float, float, float]
    size: float
    color: tuple[int, int, int]
    font: str
    bold: bool
    italic: bool
    monospaced: bool


class PagePayload(BaseModel):
    """Página serializada com seus spans."""

    number: int
    width: float
    height: float
    needs_ocr: bool
    spans: list[SpanPayload]


class ExtractionPayload(BaseModel):
    """JSON bruto (nível 1): espelho fiel da extração."""

    generated_at: datetime
    app: str
    app_version: str
    folder_id: str
    page_count: int
    span_count: int
    pages: list[PagePayload]


class CellPayload(BaseModel):
    """Célula do grid reconstruído (nível 2)."""

    row: int = Field(ge=0)
    col: int = Field(ge=0)
    text: str
    bbox: tuple[float, float, float, float]


class GridPayload(BaseModel):
    """Grid serializado de uma página (por grupo de orientação)."""

    page: int = Field(ge=0)
    orientation: str
    n_rows: int = Field(ge=0)
    n_cols: int = Field(ge=0)
    cells: list[CellPayload]


class TablesPayload(BaseModel):
    """JSON estruturado (nível 2): grids por página."""

    generated_at: datetime
    app: str
    app_version: str
    folder_id: str
    grids: list[GridPayload]
