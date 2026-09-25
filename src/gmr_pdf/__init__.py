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
from gmr_pdf.freight import (
    AlteracoesTabela,
    DadosTransportador,
    FreightRecord,
    FreightTable,
    Generalidades,
    TabelaFrete,
    parse_alteracoes,
    parse_freight_grid,
    parse_generalidades,
    parse_tabela_documento,
    parse_tabela_frete,
)
from gmr_pdf.json_export import (
    extraction_payload,
    save_json,
    tables_payload,
    to_json,
)
from gmr_pdf.profile import (
    FamilyProfile,
    detect_family,
    load_family_profile,
)
from gmr_pdf.renderer import reconstruct_document
from gmr_pdf.settings import Settings, get_settings
from gmr_pdf.spatial import TableGrid, extract_grids
from gmr_pdf.vectorize import SpansVector, vectorize_document

__all__ = [
    "AlteracoesTabela",
    "BlockData",
    "DadosTransportador",
    "DocumentData",
    "FamilyProfile",
    "FreightRecord",
    "FreightTable",
    "Generalidades",
    "GoogleDriveClient",
    "LineData",
    "PageData",
    "Settings",
    "SpanData",
    "SpansVector",
    "TabelaFrete",
    "TableGrid",
    "detect_family",
    "extract_document",
    "extract_grids",
    "extract_page",
    "extraction_payload",
    "get_drive_client",
    "get_settings",
    "load_family_profile",
    "parse_alteracoes",
    "parse_freight_grid",
    "parse_generalidades",
    "parse_tabela_documento",
    "parse_tabela_frete",
    "reconstruct_document",
    "save_json",
    "tables_payload",
    "to_json",
    "vectorize_document",
]
