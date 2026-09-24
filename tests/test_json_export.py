"""Testes da exportação JSON validada por pydantic."""

import json
from pathlib import Path

from gmr_pdf.extractor import extract_document
from gmr_pdf.json_export import (
    extraction_payload,
    save_json,
    tables_payload,
    to_json,
)
from gmr_pdf.spatial import extract_grids


def test_extraction_payload_validates_and_serializes(
    synthetic_pdf_bytes: bytes,
) -> None:
    payload = extraction_payload(extract_document(synthetic_pdf_bytes))
    assert payload.page_count == 1
    assert payload.span_count == 3
    assert payload.pages[0].spans[0].text == "Hola Mundo"
    assert payload.pages[0].spans[0].bold is False
    assert payload.pages[0].spans[2].bold is True

    data = json.loads(to_json(payload))
    assert data["app"] == "gmr_pdf"
    assert data["folder_id"] == "1qATGIABfc_wtXlSTyI8LjZgr0JXZWE0Q"
    # timestamp com fuso UTC-3 (Brasília) — trilha de auditoria
    assert data["generated_at"].endswith("-03:00")


def test_tables_payload_from_grid(synthetic_table_pdf_bytes: bytes) -> None:
    grids = extract_grids(extract_document(synthetic_table_pdf_bytes))
    payload = tables_payload(grids)
    assert payload.grids[0].n_rows == 3
    assert payload.grids[0].n_cols == 3
    texts = {(c.row, c.col): c.text for c in payload.grids[0].cells}
    assert texts[(1, 2)] == "1.234,56"

    data = json.loads(to_json(payload))
    assert data["grids"][0]["page"] == 0


def test_save_json_writes_file(
    synthetic_pdf_bytes: bytes, tmp_path: Path
) -> None:
    payload = extraction_payload(extract_document(synthetic_pdf_bytes))
    path = save_json(payload, tmp_path / "out" / "extracao.json")
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["span_count"] == 3
