"""Testes do extrator de baixo nível (rawdict)."""

import pymupdf
import pytest

from gmr_pdf.extractor import DocumentData, extract_document


def _blank_pdf_bytes() -> bytes:
    """PDF de uma página 100% vazia (simula escaneado falho)."""
    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def document(synthetic_pdf_bytes: bytes) -> DocumentData:
    return extract_document(synthetic_pdf_bytes)


def test_page_dimensions_preserved(document: DocumentData) -> None:
    page = document.pages[0]
    assert page.width == 595
    assert page.height == 842
    assert document.page_count == 1


def test_span_geometry_and_typography(document: DocumentData) -> None:
    spans = [
        span
        for block in document.pages[0].blocks
        for line in block.lines
        for span in line.spans
    ]
    assert len(spans) == 3

    primeiro = next(s for s in spans if s.text == "Hola Mundo")
    assert primeiro.origin == pytest.approx((72, 72), abs=0.5)
    assert primeiro.size == pytest.approx(12, abs=0.1)
    assert primeiro.color == (255, 0, 0)
    assert not primeiro.is_bold

    segunda = next(s for s in spans if s.text == "segunda linha")
    assert segunda.size == pytest.approx(8, abs=0.1)
    assert segunda.origin[1] == pytest.approx(100, abs=0.5)

    negrito = next(s for s in spans if s.text == "Negrito")
    assert negrito.is_bold


def test_chars_have_individual_geometry(document: DocumentData) -> None:
    span = document.pages[0].blocks[0].lines[0].spans[0]
    assert len(span.chars) == len(span.text)
    assert all(c.bbox[3] > c.bbox[1] for c in span.chars)  # altura positiva


def test_blank_page_flags_ocr_fallback() -> None:
    """Página sem spans deve acionar o gatilho de OCR (risco R1)."""
    document = extract_document(_blank_pdf_bytes())
    assert document.pages[0].needs_ocr is True
    assert document.span_count == 0


def test_extract_specific_pages(synthetic_pdf_bytes: bytes) -> None:
    document = extract_document(synthetic_pdf_bytes, page_numbers=iter([0]))
    assert document.page_count == 1
