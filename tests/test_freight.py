"""Testes da camada de domínio (registros de frete)."""

from decimal import Decimal

import pymupdf

from gmr_pdf.extractor import extract_document
from gmr_pdf.freight import (
    FreightTier,
    parse_freight_grid,
    parse_generalidades,
    parse_tabela_frete,
    parse_transportador,
)
from gmr_pdf.spatial import extract_grids


def _doc_clausulas_pdf_bytes() -> bytes:
    """Documento 2 páginas com cláusulas de 2 colunas e continuação."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for x, text in (
        (40.0, "TIPO DE VEÍCULO"),
        (130.0, "CIDADE"),
        (200.0, "SIGLA"),
        (240.0, "CEP INICIAL"),
        (330.0, "CEP FINAL"),
    ):
        page.insert_text((x, 100), text, fontsize=8)
    for x, text in (
        (40.0, "TODOS"),
        (130.0, "PALHOÇA"),
        (200.0, "HPLH"),
        (240.0, "88130-000"),
        (330.0, "88139-999"),
    ):
        page.insert_text((x, 140), text, fontsize=8)
    # pagamento (sem marcador, por startswith)
    page.insert_text((40, 200), "Quinzenal, 30 dias para pagamento", fontsize=8)
    pagamento2 = "Pagamento válido somente com baixas mobile"
    page.insert_text((40, 225), pagamento2, fontsize=8)
    # cláusulas em duas colunas
    page.insert_text((40, 260), "COMPROVANTE DE ENTREGA:", fontsize=8)
    page.insert_text((320, 260), "PERDAS, INDENIZAÇÕES e RESTRIÇÕES:", fontsize=8)
    comprovante1 = "Comprovante capturado no sistema eletrônico"
    page.insert_text((40, 285), comprovante1, fontsize=8)
    page.insert_text((320, 285), "A MAGALOG incluirá nos fechamentos", fontsize=8)
    page.insert_text((40, 310), "Caso a CONTRATADA localize uma encomenda", fontsize=8)

    page2 = doc.new_page(width=595, height=842)
    page2.insert_text((40, 60), "Docusign Envelope ID: 1234", fontsize=8)
    page2.insert_text((40, 90), "de posse da mercadoria, não devolver", fontsize=8)
    page2.insert_text((40, 130), "ACAREAÇÕES:", fontsize=8)
    page2.insert_text((40, 160), "Em caso de reclamação do cliente", fontsize=8)
    page2.insert_text((40, 190), "CONTRATADA", fontsize=8)
    page2.insert_text((40, 220), "texto pós-assinatura não entra", fontsize=8)
    data = doc.tobytes()
    doc.close()
    return data


def _doc_completo_pdf_bytes() -> bytes:
    """Documento com preâmbulo (2 padrões) + cabeçalho + linha de dados."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    # padrão 1: CHAVE | VALOR na mesma linha
    page.insert_text((40, 60), "MODALIDADE DA OPERAÇÃO", fontsize=8)
    page.insert_text((300, 60), "LAST MILE", fontsize=8)
    page.insert_text((40, 85), "ID TABELA", fontsize=8)
    page.insert_text((300, 85), "ID00999", fontsize=8)
    # padrão 2: chave sozinha, valor na linha seguinte
    page.insert_text((40, 110), "TELEFONE DO PARCEIRO", fontsize=8)
    page.insert_text((40, 135), "47 99999-0000", fontsize=8)
    # cabeçalho da tabela de tarifas
    for x, text in (
        (40.0, "TIPO DE VEÍCULO"),
        (130.0, "CIDADE"),
        (200.0, "SIGLA"),
        (240.0, "CEP INICIAL"),
        (330.0, "CEP FINAL"),
    ):
        page.insert_text((x, 200), text, fontsize=8)
    for (x, _), text in zip(
        ((40.0, ""), (130.0, ""), (200.0, ""), (240.0, ""), (330.0, "")),
        ("TODOS", "PALHOÇA", "HPLH", "88130-000", "88139-999"),
        strict=True,
    ):
        page.insert_text((x, 240), text, fontsize=8)
    data = doc.tobytes()
    doc.close()
    return data


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


def test_parse_transportador_both_patterns() -> None:
    """Preâmbulo: chave|valor na linha E chave sozinha com valor abaixo."""
    grids = extract_grids(extract_document(_doc_completo_pdf_bytes()))
    grid = grids[0]
    # cabeçalho está na última linha antes dos dados (row com labels)
    from gmr_pdf.freight import _find_header_row, _load_field_map

    header_row = _find_header_row(grid, tuple(_load_field_map()))
    dados = parse_transportador(grid, header_row[0].row_index)

    assert dados.modalidade_operacao == "LAST MILE"
    assert dados.id_tabela == "ID00999"
    assert dados.telefone == "47 99999-0000"
    # campos ausentes ficam None explícito (nunca inventados)
    assert dados.cnpj is None


def test_parse_tabela_frete_completa() -> None:
    grids = extract_grids(extract_document(_doc_completo_pdf_bytes()))
    tabela = parse_tabela_frete(grids[0])

    assert tabela is not None
    assert tabela.dados_transportador.id_tabela == "ID00999"
    assert len(tabela.dados_tarifa) == 1
    assert tabela.dados_tarifa[0].cidade == "PALHOÇA"
    assert tabela.dados_tarifa[0].cep_inicial == "88130-000"


def test_parse_generalidades_secoes() -> None:
    """Cláusulas por marcador de coluna, startswith e continuação de página."""
    grids = extract_grids(extract_document(_doc_clausulas_pdf_bytes()))
    g = parse_generalidades(grids)

    assert g.pagamento == [
        "Quinzenal, 30 dias para pagamento",
        "Pagamento válido somente com baixas mobile",
    ]
    assert g.comprovante_entrega == [
        "Comprovante capturado no sistema eletrônico",
        "Caso a CONTRATADA localize uma encomenda",
        "de posse da mercadoria, não devolver",  # continuação da quebra de página
    ]
    assert g.perda_idenizacao_restricao == ["A MAGALOG incluirá nos fechamentos"]
    assert g.acareacoes == ["Em caso de reclamação do cliente"]
    # junk (Docusign) e texto pós-stop (bloco de assinaturas) nunca entram
    todos = (
        g.pagamento
        + g.comprovante_entrega
        + g.perda_idenizacao_restricao
        + g.acareacoes
    )
    assert all("Docusign" not in t for t in todos)
    assert "texto pós-assinatura não entra" not in todos


def test_parse_alteracoes_secao() -> None:
    """Seção ALTERAÇÕES DA TABELA localizada pelo marcador entre páginas."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((40, 100), "ALTERAÇÕES DA TABELA:", fontsize=8)
    page.insert_text((40, 130), "CD", fontsize=8)
    page.insert_text((200, 130), "HLDB", fontsize=8)
    page.insert_text((40, 160), "Nome do analista", fontsize=8)
    page.insert_text((200, 160), "DOUGLAS GABRIEL", fontsize=8)
    page.insert_text((40, 190), "Data alteração", fontsize=8)
    page.insert_text((40, 220), "Tipo alteração", fontsize=8)
    page.insert_text((200, 220), "Atualização de abrangencia", fontsize=8)
    data = doc.tobytes()
    doc.close()

    from gmr_pdf.freight import parse_alteracoes

    grids = extract_grids(extract_document(data))
    alt = parse_alteracoes(grids)
    assert alt.cd == "HLDB"
    assert alt.nome_analista == "DOUGLAS GABRIEL"
    assert alt.data_alteracao is None  # chave sem valor — explicitamente vazia
    assert alt.tipo_alteracao == "Atualização de abrangencia"


def test_br_money_parsing_variants() -> None:
    assert FreightTier(rotulo="x", valor="R$ 1.234,56").valor == Decimal("1234.56")
    assert FreightTier(rotulo="x", valor="5,20").valor == Decimal("5.20")
    assert FreightTier(rotulo="x", valor="R$          6,00").valor == Decimal("6.00")
    assert FreightTier(rotulo="x", valor="").valor is None
