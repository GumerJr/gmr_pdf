"""Testes da camada de domínio (registros de frete)."""

from decimal import Decimal

import pymupdf

from gmr_pdf.extractor import extract_document
from gmr_pdf.freight import FreightTier, parse_freight_grid
from gmr_pdf.spatial import extract_grids


def _freight_pdf_bytes() -> bytes:
    """Mini tabela de frete: cabeçalho + 2 linhas de dados."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    header = (
        (40.0, "TIPO DE VEÍCULO"),
        (130.0, "CIDADE"),
        (200.0, "SIGLA"),
        (240.0, "CEP INICIAL"),
        (330.0, "CEP FINAL"),
        (440.0, "PRAZO"),
        (500.0, "De 0 até 2"),
    )
    for x, text in header:
        page.insert_text((x, 100), text, fontsize=8)
    rows = (
        ("TODOS", "PALHOÇA", "HPLH", "88130-000", "88139-999", "3", "R$ 3,55"),
        ("TODOS", "SÃO JOSÉ", "HPLH", "88100-000", "88124-999", "3", "R$ 5,20"),
    )
    for i, row in enumerate(rows):
        y = 140.0 + i * 30
        for (x, _), text in zip(header, row, strict=True):
            page.insert_text((x, y), text, fontsize=8)
    data = doc.tobytes()
    doc.close()
    return data


def test_parse_freight_grid_records() -> None:
    grids = extract_grids(extract_document(_freight_pdf_bytes()))
    table = parse_freight_grid(grids[0])

    assert table is not None
    assert len(table.records) == 2

    first = table.records[0]
    assert first.page == 0
    assert first.cidade == "PALHOÇA"
    assert first.sigla == "HPLH"
    assert first.cep_inicial == "88130-000"
    assert first.cep_final == "88139-999"
    assert first.prazo == 3
    assert first.faixas == [
        FreightTier(rotulo="De 0 até 2", valor=Decimal("3.55"))
    ]

    second = table.records[1]
    assert second.cep_inicial == "88100-000"
    assert second.faixas[0].valor == Decimal("5.20")


def test_parse_freight_grid_without_header_returns_none() -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Texto livre sem tabela", fontsize=10)
    data = doc.tobytes()
    doc.close()
    grids = extract_grids(extract_document(data))
    assert parse_freight_grid(grids[0]) is None


def test_br_money_parsing_variants() -> None:
    assert FreightTier(rotulo="x", valor="R$ 1.234,56").valor == Decimal("1234.56")
    assert FreightTier(rotulo="x", valor="5,20").valor == Decimal("5.20")
    assert FreightTier(rotulo="x", valor="R$          6,00").valor == Decimal("6.00")
    assert FreightTier(rotulo="x", valor="").valor is None
