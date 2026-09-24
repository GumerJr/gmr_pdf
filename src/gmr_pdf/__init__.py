"""gmr_pdf - Leitura e extração de dados de arquivos PDF."""

from gmr_pdf.drive import GoogleDriveClient, get_drive_client
from gmr_pdf.extractor import (
    BlockData,
    DocumentData,
    LineData,
    PageData,
    SpanData,
    extract_document,
    extract_page,
)
from gmr_pdf.freight import FreightRecord, FreightTable, parse_freight_grid
from gmr_pdf.json_export import (
    extraction_payload,
    save_json,
    tables_payload,
    to_json,
)
from gmr_pdf.renderer import reconstruct_document
from gmr_pdf.settings import Settings, get_settings
from gmr_pdf.spatial import TableGrid, extract_grids
from gmr_pdf.vectorize import SpansVector, vectorize_document

__all__ = [
    "BlockData",
    "DocumentData",
    "FreightRecord",
    "FreightTable",
    "GoogleDriveClient",
    "LineData",
    "PageData",
    "Settings",
    "SpanData",
    "SpansVector",
    "TableGrid",
    "extract_document",
    "extract_grids",
    "extract_page",
    "extraction_payload",
    "get_drive_client",
    "get_settings",
    "parse_freight_grid",
    "reconstruct_document",
    "save_json",
    "tables_payload",
    "to_json",
    "vectorize_document",
]
