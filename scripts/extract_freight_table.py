#!/usr/bin/env python
"""Pipeline completo: Drive → extração → grid → domínio → JSON auditáveis.

Uso::

    uv run python scripts/extract_freight_table.py [<file_id>] [--familia NOME]

Sem ``file_id``, usa o primeiro PDF da pasta configurada no ``.env``.
Sem ``--familia``, a família é auto-detectada por marcadores
(``config/families/*.yaml``).

Gera em ``outputs/``:

- ``extracao_<file_id>.json`` — nível 1 (spans lossless, auditoria);
- ``tabelas_<file_id>.json`` — nível 2 (grids linha × coluna);
- ``fretes_<file_id>.json`` + ``fretes_<file_id>.csv`` — tarifas;
- ``tabela_<file_id>.json`` — documento completo (transportador, tarifa,
  alterações, generalidades).
"""

import argparse
import csv
import sys
from collections.abc import Sequence
from pathlib import Path

from gmr_pdf.drive import get_drive_client
from gmr_pdf.extractor import extract_document
from gmr_pdf.freight import (
    FreightTable,
    parse_freight_grid,
    parse_tabela_documento,
)
from gmr_pdf.json_export import extraction_payload, save_json, tables_payload
from gmr_pdf.logger import get_logger
from gmr_pdf.profile import detect_family, load_family_profile
from gmr_pdf.semantic import expand_grid_labels, load_semantic_config
from gmr_pdf.spatial import extract_grids

logger = get_logger("gmr_pdf.pipeline")

OUTPUT_DIR = Path("outputs")


def save_freights_csv(tables: Sequence[FreightTable], path: Path) -> Path:
    """Gera CSV dos registros (uma linha por registro, uma coluna por faixa)."""
    tier_labels = sorted(
        {
            tier.rotulo
            for table in tables
            for rec in table.records
            for tier in rec.faixas
        }
    )
    fixed = [
        "page", "row", "tipo_veiculo", "cidade", "sigla",
        "cep_inicial", "cep_final", "interiorizacao", "prazo", "diaria",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[*fixed, *tier_labels])
        writer.writeheader()
        for table in tables:
            for rec in table.records:
                row = {f: rec.model_dump().get(f) for f in fixed}
                row.update({t.rotulo: t.valor for t in rec.faixas})
                writer.writerow(row)
    logger.info("📊 CSV de análise salvo: %s", path)
    return path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file_id", nargs="?", default=None)
    parser.add_argument("--familia", default=None, help="perfil em config/families/")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logger.info("🚀 Iniciando pipeline: Drive → extração → grid → JSON")

    client = get_drive_client()
    file_id = args.file_id
    if file_id is None:
        pdfs = client.list_pdfs()
        if not pdfs:
            logger.error("❌ Nenhum PDF encontrado na pasta configurada")
            sys.exit(1)
        file_id = pdfs[0]["id"]
        logger.info("📄 Arquivo selecionado: %s", pdfs[0]["name"])

    pdf_bytes = client.download_pdf(file_id)
    document = extract_document(pdf_bytes)

    # perfil de família: explícito ou auto-detectado (antes de gerar grids,
    # pois os join_tokens do perfil afetam a montagem das células)
    all_texts = [
        span.text
        for page in document.pages
        for block in page.blocks
        for line in block.lines
        for span in line.spans
    ]
    profile = (
        load_family_profile(args.familia) if args.familia else detect_family(all_texts)
    )

    grids = extract_grids(document, join_tokens=profile.join_tokens)
    semantic = load_semantic_config(profile)
    grids = [expand_grid_labels(grid, semantic) for grid in grids]

    save_json(extraction_payload(document), OUTPUT_DIR / f"extracao_{file_id}.json")
    save_json(tables_payload(grids), OUTPUT_DIR / f"tabelas_{file_id}.json")

    freight_tables = [
        table
        for grid in grids
        if (table := parse_freight_grid(grid, profile)) is not None
    ]
    if freight_tables:
        from pydantic import BaseModel, Field

        class FreightReport(BaseModel):
            tables: list[FreightTable]
            total_records: int = Field(ge=0)

        report = FreightReport(
            tables=freight_tables,
            total_records=sum(len(t.records) for t in freight_tables),
        )
        save_json(report, OUTPUT_DIR / f"fretes_{file_id}.json")
        save_freights_csv(freight_tables, OUTPUT_DIR / f"fretes_{file_id}.csv")

    # documento completo: transportador + tarifa + alterações + cláusulas
    tabela = parse_tabela_documento(grids, profile)
    if tabela is not None:
        save_json(tabela, OUTPUT_DIR / f"tabela_{file_id}.json")

    logger.info("🏁 Pipeline concluído com sucesso")


if __name__ == "__main__":
    main()
