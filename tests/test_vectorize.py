"""Testes da vetorização Struct-of-Arrays."""

import numpy as np

from gmr_pdf.extractor import extract_document
from gmr_pdf.vectorize import SpansVector, vectorize_document


def _spans(pdf_bytes: bytes) -> SpansVector:
    return vectorize_document(extract_document(pdf_bytes))


def test_shapes_and_dtypes(synthetic_pdf_bytes: bytes) -> None:
    spans = _spans(synthetic_pdf_bytes)
    assert len(spans) == 3
    assert spans.origins.shape == (3, 2)
    assert spans.bboxes.shape == (3, 4)
    assert spans.colors.shape == (3, 3)
    assert spans.origins.dtype == np.float32
    assert spans.colors.dtype == np.uint8
    assert spans.flags.dtype == np.uint8
    assert spans.texts.dtype.kind == "U"


def test_geometry_is_exact(synthetic_pdf_bytes: bytes) -> None:
    spans = _spans(synthetic_pdf_bytes)
    np.testing.assert_allclose(spans.origins[0], (72.0, 72.0), atol=0.5)
    assert spans.sizes[0] == np.float32(12.0)
    assert np.all(spans.widths > 0)
    assert np.all(spans.heights > 0)


def test_bold_mask_selects_vectorized(synthetic_pdf_bytes: bytes) -> None:
    spans = _spans(synthetic_pdf_bytes)
    bold = spans.select(spans.bold_mask)
    assert len(bold) == 1
    assert bold.texts[0] == "Negrito"
    # regular subset é o complemento da máscara
    regular = spans.select(~spans.bold_mask)
    assert len(regular) == 2
    assert set(regular.texts.tolist()) == {"Hola Mundo", "segunda linha"}


def test_font_vocabulary_deduplicates(synthetic_pdf_bytes: bytes) -> None:
    spans = _spans(synthetic_pdf_bytes)
    assert len(spans.font_vocabulary) == len(set(spans.font_ids.tolist()))
    assert spans.font_ids.dtype == np.int32


def test_select_preserves_structural_ids(synthetic_pdf_bytes: bytes) -> None:
    spans = _spans(synthetic_pdf_bytes)
    bold = spans.select(spans.bold_mask)
    # ids estruturais sobrevivem à filtragem (span continua mapeável à origem)
    assert bold.line_ids.shape == (1,)
    assert bold.page_ids.tolist() == [0]
    assert bold.char_counts.tolist() == [len("Negrito")]


def test_size_percentiles(synthetic_pdf_bytes: bytes) -> None:
    spans = _spans(synthetic_pdf_bytes)
    p = spans.size_percentiles((0.5,))
    assert p.shape == (1,)
    assert float(p[0]) == np.float32(10.0)
