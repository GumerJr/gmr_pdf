"""Fixtures compartilhadas entre os testes."""

import pymupdf
import pytest


@pytest.fixture
def synthetic_table_pdf_bytes() -> bytes:
    """PDF com uma mini tabela 3×3 em posições conhecidas (viés Excel)."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    grid_rows = (
        (100.0, ("SIGLA", "CIDADE", "VALOR")),
        (140.0, ("SP", "São Paulo", "1.234,56")),
        (180.0, ("RJ", "Rio de Janeiro", "987,65")),
    )
    cols_x = (100.0, 260.0, 420.0)
    for y, texts in grid_rows:
        for x, text in zip(cols_x, texts, strict=True):
            page.insert_text((x, y), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def synthetic_pdf_bytes() -> bytes:
    """PDF sintético com posições, cores e pesos tipográficos conhecidos."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # A4 em pontos
    page.insert_text((72, 72), "Hola Mundo", fontsize=12, color=(1, 0, 0))
    page.insert_text((72, 100), "segunda linha", fontsize=8)
    page.insert_text((72, 140), "Negrito", fontsize=10, fontname="hebo")
    data = doc.tobytes()
    doc.close()
    return data
