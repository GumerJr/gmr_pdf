"""Testes da inteligência espacial (linhas por baseline, células por span)."""

import numpy as np
import pymupdf
import pytest

from gmr_pdf.extractor import extract_document
from gmr_pdf.spatial import (
    build_grid,
    cluster_positions,
    extract_grids,
    projection_segments,
)
from gmr_pdf.vectorize import vectorize_document


def test_projection_segments_splits_on_gaps() -> None:
    starts = np.array([0.0, 5.0, 30.0], dtype=np.float32)
    ends = np.array([4.0, 9.0, 35.0], dtype=np.float32)
    segs = projection_segments(starts, ends, resolution=1.0)
    # [0,4] e [5,9] contíguos -> mesmo segmento; [30,35] isolado -> outro
    assert segs.tolist() == [0, 0, 1]


def test_projection_segments_empty() -> None:
    empty = np.empty(0, dtype=np.float32)
    assert projection_segments(empty, empty, 1.0).size == 0


def test_cluster_positions_groups_by_gap() -> None:
    values = np.array([10.0, 10.3, 10.1, 50.0, 50.2], dtype=np.float32)
    clusters = cluster_positions(values, tolerance=1.0)
    assert clusters.tolist() == [0, 0, 0, 1, 1]


def test_cluster_positions_empty() -> None:
    empty = np.empty(0, dtype=np.float32)
    assert cluster_positions(empty, 1.0).size == 0


def test_extract_grids_rebuilds_3x3_table(
    synthetic_table_pdf_bytes: bytes,
) -> None:
    document = extract_document(synthetic_table_pdf_bytes)
    grids = extract_grids(document)
    assert len(grids) == 1
    grid = grids[0]
    assert grid.n_rows == 3
    assert grid.n_cols == 3
    matrix = grid.to_matrix()
    assert matrix[0] == ("SIGLA", "CIDADE", "VALOR")
    assert matrix[1] == ("SP", "São Paulo", "1.234,56")
    assert matrix[2] == ("RJ", "Rio de Janeiro", "987,65")


def test_grid_row_texts_pads_empty_cells(synthetic_table_pdf_bytes: bytes) -> None:
    grid = extract_grids(extract_document(synthetic_table_pdf_bytes))[0]
    assert len(grid.row_texts(0)) == grid.n_cols


def test_blank_page_yields_no_grid() -> None:
    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    assert extract_grids(extract_document(data)) == []


def _build(texts: list[tuple[float, str]]) -> tuple:
    """Grid de spans horizontais na mesma linha, posições livres."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for x, text in texts:
        page.insert_text((x, 100), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    spans = vectorize_document(extract_document(data))
    return build_grid(spans, 0, row_band_factor=0.15)


def test_adjacent_spans_are_separate_cells() -> None:
    """Spans distintos na mesma linha são células distintas (borda Excel)."""
    grid = _build([(100.0, "AB"), (140.0, "CD")])
    assert grid.n_rows == 1
    assert grid.n_cols == 2
    assert grid.row_texts(0) == ("AB", "CD")


def test_internal_span_gap_stays_one_cell() -> None:
    """Gap interno do span não separa célula; espaços são colapsados."""
    grid = _build([(100.0, "R$          5,50"), (260.0, "88000-000")])
    assert grid.n_cols == 2
    assert grid.row_texts(0) == ("R$ 5,50", "88000-000")


def test_cell_keeps_geometry_for_audit() -> None:
    grid = _build([(100.0, "X")])
    cell = grid.cells[0]
    assert cell.bbox[0] == pytest.approx(100, abs=1.0)
    assert len(cell.span_indices) == 1


def test_build_grid_skips_whitespace_spans(
    synthetic_table_pdf_bytes: bytes,
) -> None:
    document = extract_document(synthetic_table_pdf_bytes)
    grid = extract_grids(document)[0]
    assert all(cell.text.strip() for cell in grid.cells)


def _rotated_table_pdf_bytes() -> bytes:
    """Tabela 2×2 inteiramente rotacionada 90° (viés Excel landscape)."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((150, 700), "A1", fontsize=10, rotate=90)
    page.insert_text((150, 400), "A2", fontsize=10, rotate=90)
    page.insert_text((350, 700), "B1", fontsize=10, rotate=90)
    page.insert_text((350, 400), "B2", fontsize=10, rotate=90)
    data = doc.tobytes()
    doc.close()
    return data


def test_rotated_table_is_canonicalized_to_grid() -> None:
    """Spans em direção (0,-1) devem virar um grid horizontal normal."""
    grids = extract_grids(extract_document(_rotated_table_pdf_bytes()))
    assert len(grids) == 1
    grid = grids[0]
    assert grid.orientation == "vertical"
    assert grid.n_rows == 2
    assert grid.n_cols == 2
    # Frame canônico paisagem: original-x vira linha, original-y vira coluna.
    # A1/A2 em x=150 → mesma linha; A1/B1 em y=700 → mesma coluna.
    assert grid.to_matrix() == (("A1", "A2"), ("B1", "B2"))
