"""Testes das capacidades de resiliência (perfis de família)."""

from dataclasses import replace
from decimal import Decimal

import pymupdf

from gmr_pdf.extractor import extract_document
from gmr_pdf.freight import (
    _collect_key_values,
    parse_tabela_documento,
)
from gmr_pdf.profile import detect_family, load_family_profile, norm_key
from gmr_pdf.spatial import extract_grids


def test_profile_loads_from_family_file() -> None:
    profile = load_family_profile("magalu_escalonada")
    assert profile.name == "magalu_escalonada"
    assert "MONEDA" not in profile.labels
    assert "CEP INICIAL" in profile.field_map
    assert norm_key("  cep  inicial: ") == "CEP INICIAL"
    assert profile.money_pattern.fullmatch("R$ 5,20")


def test_detect_family_score() -> None:
    texts = ["MODALIDADE DA OPERAÇÃO", "CEP INICIAL (OBRIGATÓRIO)", "ID TABELA"]
    profile = detect_family(texts)
    assert profile.name == "magalu_escalonada"


def test_inline_key_value_pattern() -> None:
    """CHAVE: valor inline na mesma célula (3º padrão de preâmbulo)."""
    mapping = {"CNPJ": "cnpj", "E-MAIL PARCEIRO": "email"}
    rows = [["CNPJ: 57804838000155", "E-MAIL PARCEIRO: parceiro@exemplo.com"]]
    collected = _collect_key_values(rows, mapping)
    assert collected["cnpj"] == "57804838000155"
    assert collected["email"] == "parceiro@exemplo.com"


def _tabela_sem_cep_pdf_bytes() -> bytes:
    """Tabela sem colunas de CEP — valores regionais puros."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for x, text in (
        (40.0, "TIPO DE VEÍCULO"),
        (130.0, "CIDADE"),
        (240.0, "SIGLA"),
        (340.0, "PRAZO"),
        (440.0, "De 0 até 2"),
    ):
        page.insert_text((x, 100), text, fontsize=8)
    for x, text in (
        (40.0, "TODOS"),
        (130.0, "PALHOÇA"),
        (240.0, "HPLH"),
        (340.0, "2"),
        (440.0, "R$ 8,90"),
    ):
        page.insert_text((x, 140), text, fontsize=8)
    data = doc.tobytes()
    doc.close()
    return data


def test_data_row_signature_auto_sem_cep() -> None:
    """Sem colunas cep_*: assinatura auto cai para células monetárias."""
    profile = load_family_profile()
    profile = replace(
        profile,
        field_map={
            "TIPO DE VEÍCULO": "tipo_veiculo",
            "CIDADE": "cidade",
            "SIGLA": "sigla",
            "PRAZO": "prazo",
        },
    )
    grids = extract_grids(extract_document(_tabela_sem_cep_pdf_bytes()))
    tabela = parse_tabela_documento(grids, profile)

    assert tabela is not None
    assert len(tabela.dados_tarifa) == 1
    rec = tabela.dados_tarifa[0]
    assert rec.cidade == "PALHOÇA"
    assert rec.prazo == 2
    assert rec.faixas[0].valor == Decimal("8.90")


def _tabela_valor_unico_pdf_bytes() -> bytes:
    """Tabela com valor único para múltiplas faixas."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for x, text in (
        (40.0, "TIPO DE VEÍCULO"),
        (130.0, "CIDADE"),
        (200.0, "SIGLA"),
        (240.0, "CEP INICIAL"),
        (330.0, "CEP FINAL"),
        (430.0, "De 0 até 2"),
        (500.0, "Acima de 2"),
    ):
        page.insert_text((x, 100), text, fontsize=8)
    for x, text in (
        (40.0, "TODOS"),
        (130.0, "PALHOÇA"),
        (200.0, "HPLH"),
        (240.0, "88130-000"),
        (330.0, "88139-999"),
        (430.0, "R$ 7,77"),
    ):
        page.insert_text((x, 140), text, fontsize=8)
    data = doc.tobytes()
    doc.close()
    return data


def test_tier_fanout_single_value() -> None:
    """1 valor × N faixas: fanout com warning auditável."""
    grids = extract_grids(extract_document(_tabela_valor_unico_pdf_bytes()))
    tabela = parse_tabela_documento(grids)

    assert tabela is not None
    rec = tabela.dados_tarifa[0]
    assert len(rec.faixas) == 2
    assert all(t.valor == Decimal("7.77") for t in rec.faixas)
    assert any("valor único" in w for w in tabela.warnings)
