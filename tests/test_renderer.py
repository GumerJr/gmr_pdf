"""Testes da reconstrução geométrica determinística (Estágio 0)."""

import pymupdf
import pytest

from gmr_pdf.extractor import DocumentData, extract_document
from gmr_pdf.renderer import reconstruct_document


@pytest.fixture
def rebuilt_document(synthetic_pdf_bytes: bytes) -> DocumentData:
    """Round-trip: extrai o sintético, reconstrói e re-extrai a saída."""
    original = extract_document(synthetic_pdf_bytes)
    rebuilt_bytes = reconstruct_document(original)
    return extract_document(rebuilt_bytes)


def _spans_of(document: DocumentData) -> dict[str, tuple]:
    """Mapa texto -> (origin, size, color, is_bold) por página 0."""
    return {
        span.text: (span.origin, span.size, span.color, span.is_bold)
        for block in document.pages[0].blocks
        for line in block.lines
        for span in line.spans
    }


def test_reconstruction_preserves_geometry(
    synthetic_pdf_bytes: bytes, rebuilt_document: DocumentData
) -> None:
    original = _spans_of(extract_document(synthetic_pdf_bytes))
    rebuilt = _spans_of(rebuilt_document)

    assert set(rebuilt) == {"Hola Mundo", "segunda linha", "Negrito"}

    for text, (origin, size, color, is_bold) in original.items():
        r_origin, r_size, r_color, r_bold = rebuilt[text]
        assert r_origin == pytest.approx(origin, abs=1.0)
        assert r_size == pytest.approx(size, abs=0.1)
        assert r_color == color
        assert r_bold is is_bold


def test_page_dimensions_preserved(synthetic_pdf_bytes: bytes) -> None:
    original = extract_document(synthetic_pdf_bytes)
    rebuilt = extract_document(reconstruct_document(original))
    assert rebuilt.pages[0].width == original.pages[0].width
    assert rebuilt.pages[0].height == original.pages[0].height
    assert rebuilt.page_count == original.page_count


def test_output_has_no_images_no_vectors(synthetic_pdf_bytes: bytes) -> None:
    """Saída higienizada: zero imagens e zero drawings (grids/linhas)."""
    original = extract_document(synthetic_pdf_bytes)
    rebuilt_bytes = reconstruct_document(original)
    with pymupdf.open(stream=rebuilt_bytes, filetype="pdf") as doc:
        for page in doc:
            assert page.get_images(full=True) == []
            assert page.get_drawings() == []


def _rotated_pdf_bytes() -> bytes:
    """PDF com texto nas 4 rotações suportadas pelo TextWriter (morph)."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((100, 100), "rot0", fontsize=12, rotate=0)
    page.insert_text((200, 300), "rot90", fontsize=12, rotate=90)
    page.insert_text((400, 500), "rot180", fontsize=12, rotate=180)
    page.insert_text((300, 600), "rot270", fontsize=12, rotate=270)
    data = doc.tobytes()
    doc.close()
    return data


def test_rotated_spans_round_trip() -> None:
    """Rotações 0/90/180/270 devem sobreviver ao round-trip."""
    original = extract_document(_rotated_pdf_bytes())
    rebuilt = extract_document(reconstruct_document(original))

    def by_text(document: DocumentData) -> dict[str, tuple[float, float]]:
        return {
            span.text: (span.origin, line.direction)
            for block in document.pages[0].blocks
            for line in block.lines
            for span in line.spans
        }

    orig = by_text(original)
    reb = by_text(rebuilt)
    assert set(reb) == {"rot0", "rot90", "rot180", "rot270"}
    for text, (origin, direction) in orig.items():
        r_origin, r_direction = reb[text]
        assert r_origin == pytest.approx(origin, abs=1.5), text
        assert r_direction == direction, text


def test_blank_document_reconstructs_empty_page() -> None:
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    data = doc.tobytes()
    doc.close()
    original = extract_document(data)
    rebuilt_bytes = reconstruct_document(original)
    rebuilt = extract_document(rebuilt_bytes)
    assert rebuilt.page_count == 1
    assert rebuilt.pages[0].needs_ocr is True  # sem texto, página vazia
