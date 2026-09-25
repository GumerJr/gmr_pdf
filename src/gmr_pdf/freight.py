"""Camada de domínio: registros tipados de tabelas de frete escalonadas.

Transforma o grid (``TableGrid``) em registros ``FreightRecord`` prontos
para comparação com o cadastro — o objetivo final do pipeline.

Estratégia (white-box, auditável):

1. **Detecção do cabeçalho**: linha com o maior número de rótulos do
   vocabulário do domínio; ausência de cabeçalho → fallback resiliente
   (o grid genérico continua disponível no JSON nível 2);
2. **Mapeamento** label → campo canônico via ``freight.field_map``
   (prefixo, case-insensitive — tolera truncamentos como ``"(OBRIGATA"``);
3. **Faixas escalonadas**: valores monetários casados por ordem com os
   rótulos de faixa (mismatch gera alerta, nunca silêncio — auditoria);
4. **Ambiguidades posicionais** (ex.: coluna fixa sem valor na linha)
   são resolvidas pelo centro geométrico da coluna no cabeçalho;
5. **Proveniência**: todo registro carrega ``page`` e ``row``.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from gmr_pdf.logger import get_logger
from gmr_pdf.semantic import load_semantic_config
from gmr_pdf.settings import SETTINGS_FILE
from gmr_pdf.spatial import Cell, TableGrid

logger = get_logger(__name__)

_CEP_RE = re.compile(r"^\d{5}-\d{3}$")
_MONEY_RE = re.compile(r"^(?:R\$\s*)?[\d.]+,\d{2}$")
_INT_RE = re.compile(r"^\d{1,4}$")

_DEFAULT_FIELD_MAP = {
    "TIPO DE VEÍCULO": "tipo_veiculo",
    "CIDADE": "cidade",
    "SIGLA": "sigla",
    "CEP INICIAL": "cep_inicial",
    "CEP FINAL": "cep_final",
    "INTERIORIZAÇÃO": "interiorizacao",
    "PRAZO": "prazo",
    "DIÁRIA": "diaria",
}


class FreightTier(BaseModel):
    """Uma faixa escalonada (rótulo + valor monetário)."""

    rotulo: str
    valor: Decimal | None

    @field_validator("valor", mode="before")
    @classmethod
    def _parse_br_decimal(cls, value: object) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        text = re.sub(r"[^\d,]", "", str(value))
        if not text:
            return None
        try:
            return Decimal(text.replace(".", "").replace(",", "."))
        except InvalidOperation:
            return None


class FreightRecord(BaseModel):
    """Registro tipado de uma linha de tabela de frete (com proveniência).

    Colunas desconhecidas do cabeçalho são preservadas como campos extras
    (auditoria: nenhuma informação é silenciosamente descartada).
    """

    model_config = {"extra": "allow"}

    page: int = Field(ge=0)
    row: int = Field(ge=0)
    tipo_veiculo: str | None = None
    cidade: str | None = None
    sigla: str | None = None
    cep_inicial: str | None = None
    cep_final: str | None = None
    interiorizacao: str | None = None
    prazo: int | None = None
    diaria: Decimal | None = None
    faixas: list[FreightTier]

    @field_validator("diaria", mode="before")
    @classmethod
    def _parse_diaria(cls, value: object) -> Decimal | None:
        return FreightTier._parse_br_decimal(value)


class FreightTable(BaseModel):
    """Tabela de frete estruturada de uma página."""

    page: int
    template: str
    header: list[str]
    records: list[FreightRecord]
    warnings: list[str] = Field(default_factory=list)


class DadosTransportador(BaseModel):
    """Dados cadastrais do transportador (preâmbulo da tabela)."""

    modalidade_operacao: str | None = None
    operacao: str | None = None
    sigla: str | None = None
    razao_social: str | None = None
    cnpj: str | None = None
    telefone: str | None = None
    email: str | None = None
    inicio_vigencia: str | None = None
    responsavel_confeccao: str | None = None
    id_tabela: str | None = None


class AlteracoesTabela(BaseModel):
    """Seção 'ALTERAÇÕES DA TABELA' (trilha de alterações do documento)."""

    cd: str | None = None
    nome_analista: str | None = None
    data_alteracao: str | None = None
    tipo_alteracao: str | None = None
    tipo_operacao: str | None = None


class Generalidades(BaseModel):
    """Cláusulas gerais do documento, por seção (listas de textos)."""

    pagamento: list[str] = Field(default_factory=list)
    comprovante_entrega: list[str] = Field(default_factory=list)
    perda_idenizacao_restricao: list[str] = Field(default_factory=list)
    acareacoes: list[str] = Field(default_factory=list)


class TabelaFrete(BaseModel):
    """Documento completo: transportador + tarifa + alterações + cláusulas."""

    dados_transportador: DadosTransportador
    dados_tarifa: list[FreightRecord]
    alteracoes_tabela: AlteracoesTabela = Field(default_factory=AlteracoesTabela)
    generalidades: Generalidades = Field(default_factory=Generalidades)
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ColumnSpec:
    """Coluna do cabeçalho: campo canônico ou faixa."""

    kind: str  # "field" | "tier"
    name: str
    center_x: float


def _load_field_map() -> dict[str, str]:
    try:
        with SETTINGS_FILE.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        field_map = data.get("freight", {}).get("field_map")
        if field_map:
            return {str(k): str(v) for k, v in field_map.items()}
    except FileNotFoundError:
        logger.warning("⚠️  settings.yaml ausente; usando field_map default")
    return dict(_DEFAULT_FIELD_MAP)


def _parse_br_decimal(text: str) -> Decimal | None:
    cleaned = re.sub(r"[^\d,]", "", text)
    if not cleaned:
        return None
    try:
        return Decimal(cleaned.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def _find_header_row(
    grid: TableGrid, label_prefixes: tuple[str, ...]
) -> list[Cell] | None:
    """Linha do cabeçalho = a com mais células iniciadas por rótulo conhecido."""

    def label_hits(cells: list[Cell]) -> int:
        return sum(
            any(c.text.upper().startswith(prefix) for prefix in label_prefixes)
            for c in cells
        )

    by_row: dict[int, list[Cell]] = {}
    for cell in grid.cells:
        by_row.setdefault(cell.row_index, []).append(cell)
    best_row, best_score = None, 0
    for row_index, row_cells in by_row.items():
        score = label_hits(row_cells)
        if score > best_score:
            best_row, best_score = row_index, score
    if best_row is None or best_score < 3:
        return None
    return sorted(by_row[best_row], key=lambda c: c.col_index)


def _column_specs(
    header_cells: list[Cell], field_map: dict[str, str], tier_re: re.Pattern[str]
) -> list[ColumnSpec]:
    """Converte células do cabeçalho em especificações de coluna."""
    specs: list[ColumnSpec] = []
    for cell in header_cells:
        text = cell.text.strip()
        upper = text.upper()
        center = (cell.bbox[0] + cell.bbox[2]) / 2
        field = next(
            (field_map[label] for label in field_map if upper.startswith(label)),
            None,
        )
        if field is not None:
            specs.append(ColumnSpec(kind="field", name=field, center_x=center))
        elif tier_re.search(text):
            specs.append(ColumnSpec(kind="tier", name=text, center_x=center))
        else:
            specs.append(ColumnSpec(kind="field", name=text, center_x=center))
    return specs


def _nearest_fixed_spec(cell: Cell, fixed: list[ColumnSpec]) -> str | None:
    """Resolve campo por proximidade do centro-x (coluna vazia na linha)."""
    center = (cell.bbox[0] + cell.bbox[2]) / 2
    if not fixed:
        return None
    return min(fixed, key=lambda s: abs(s.center_x - center)).name


def _detect_header(
    grid: TableGrid, field_map: dict[str, str]
) -> tuple[list[Cell], list[ColumnSpec], list[ColumnSpec], list[ColumnSpec]] | None:
    """Detecta cabeçalho e retorna (células, specs, tiers, fixos) ou None."""
    header_cells = _find_header_row(grid, tuple(field_map))
    if header_cells is None:
        logger.warning(
            "⚠️  Página %d: cabeçalho de frete não detectado — página ignorada",
            grid.page_number,
        )
        return None
    specs = _column_specs(header_cells, field_map, load_semantic_config().tier_pattern)
    tier_specs = [s for s in specs if s.kind == "tier"]
    fixed_specs = [s for s in specs if s.kind == "field"]
    logger.info(
        "🚚 Cabeçalho detectado na página %d: %d campos fixos, %d faixas",
        grid.page_number,
        len(fixed_specs),
        len(tier_specs),
    )
    return header_cells, specs, tier_specs, fixed_specs


def _extract_records(
    grid: TableGrid,
    header_row: int,
    tier_specs: list[ColumnSpec],
    fixed_specs: list[ColumnSpec],
) -> tuple[list[FreightRecord], list[str]]:
    """Extrai os registros (linhas após o cabeçalho) do grid."""
    by_row: dict[int, list[Cell]] = {}
    for cell in grid.cells:
        if cell.row_index > header_row:
            by_row.setdefault(cell.row_index, []).append(cell)

    records: list[FreightRecord] = []
    warnings: list[str] = []
    for row_index in sorted(by_row):
        row_cells = sorted(by_row[row_index], key=lambda c: c.col_index)
        texts = [c.text for c in row_cells]
        cities = [t for t in texts if _CEP_RE.fullmatch(t)]
        if len(cities) < 2:
            continue  # preâmbulo/cláusulas: não é linha de dados

        record: dict[str, Any] = {
            "page": grid.page_number,
            "row": row_index,
            "cidade": None,
            "sigla": None,
            "cep_inicial": cities[0],
            "cep_final": cities[1],
        }

        money_cells = [c for c in row_cells if _MONEY_RE.fullmatch(c.text)]
        non_money = [
            c
            for c in row_cells
            if not _MONEY_RE.fullmatch(c.text) and not _CEP_RE.fullmatch(c.text)
        ]

        faixas: list[FreightTier] = []
        for spec, cell in zip(tier_specs, money_cells, strict=False):
            faixas.append(FreightTier(rotulo=spec.name, valor=cell.text))
        if len(money_cells) != len(tier_specs):
            msg = (
                f"página {grid.page_number} linha {row_index}: "
                f"{len(money_cells)} valores ≠ {len(tier_specs)} faixas"
            )
            logger.warning("⚠️  %s", msg)
            warnings.append(msg)

        assigned_fields: set[str] = {"cep_inicial", "cep_final"}
        for cell in non_money:
            name = _nearest_fixed_spec(cell, fixed_specs)
            if name is None or name in assigned_fields:
                name = f"_extra_{cell.col_index}"
            assigned_fields.add(name)
            record[name] = _coerce_field_value(name, cell.text)

        record["faixas"] = faixas
        records.append(FreightRecord(**record))

    logger.info(
        "✅ Página %d: %d registro(s) de frete extraído(s)",
        grid.page_number,
        len(records),
    )
    return records, warnings


def _coerce_field_value(name: str, text: str) -> str | int | Decimal | None:
    """Tipa o valor conforme o campo de destino (auditoria: sem coerção cega)."""
    if name == "prazo" and _INT_RE.fullmatch(text):
        return int(text)
    if name in {"prazo", "diaria"}:
        return _parse_br_decimal(text)
    return text


def parse_freight_grid(
    grid: TableGrid, template: str = "tabela_escalonada"
) -> FreightTable | None:
    """Converte um grid em uma ``FreightTable`` tipada.

    Retorna ``None`` quando nenhum cabeçalho é detectado (fallback: o
    grid permanece disponível no JSON nível 2 — nunca descarte silencioso).
    """
    field_map = _load_field_map()
    detected = _detect_header(grid, field_map)
    if detected is None:
        return None
    header_cells, specs, tier_specs, fixed_specs = detected
    records, warnings = _extract_records(
        grid, header_cells[0].row_index, tier_specs, fixed_specs
    )
    return FreightTable(
        page=grid.page_number,
        template=template,
        header=[s.name for s in specs],
        records=records,
        warnings=warnings,
    )


def _norm_key(text: str) -> str:
    """Normaliza chave visual do preâmbulo (upper, sem espaços extras/':')."""
    return re.sub(r"\s+", " ", text).strip().rstrip(":").strip().upper()


_DEFAULT_TRANSPORTADOR_MAP = {
    "MODALIDADE DA OPERAÇÃO": "modalidade_operacao",
    "OPERAÇÃO": "operacao",
    "SIGLA": "sigla",
    "RAZÃO SOCIAL": "razao_social",
    "CNPJ": "cnpj",
    "TELEFONE DO PARCEIRO": "telefone",
    "E-MAIL DO PARCEIRO": "email",
    "INICIO DA VIGÊNCIA": "inicio_vigencia",
    "EMAIL DE QUEM CONFECCIONOU": "responsavel_confeccao",
    "ID TABELA": "id_tabela",
}

_DEFAULT_ALTERACOES_MARK = "ALTERAÇÕES DA TABELA"
_DEFAULT_ALTERACOES_MAP = {
    "CD": "cd",
    "NOME DO ANALISTA": "nome_analista",
    "DATA ALTERAÇÃO": "data_alteracao",
    "TIPO ALTERAÇÃO": "tipo_alteracao",
    "TIPO OPERÇÃO": "tipo_operacao",
}

_DEFAULT_GENERALIDADES_SECTIONS = {
    "pagamento": {"startswith": ["QUINZENAL", "PAGAMENTO VÁLIDO"]},
    "comprovante_entrega": {"marker": "COMPROVANTE DE ENTREGA"},
    "perda_idenizacao_restricao": {"marker": "PERDAS, INDENIZAÇÕES e RESTRIÇÕES"},
    "acareacoes": {"marker": "ACAREAÇÕES"},
}
_DEFAULT_STOP_PREFIXES = ("CONTRATADA",)
_DEFAULT_JUNK_PREFIXES = ("DOCUSIGN",)


def _load_freight_section() -> dict[str, Any]:
    try:
        with SETTINGS_FILE.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        section = data.get("freight", {})
        return section if isinstance(section, dict) else {}
    except FileNotFoundError:
        logger.warning("⚠️  settings.yaml ausente; usando defaults de freight")
        return {}


def _load_key_map(section_key: str, default: dict[str, str]) -> dict[str, str]:
    """Mapeamento chave visual normalizada -> campo (de uma seção do YAML)."""
    raw = _load_freight_section().get(section_key)
    source = raw if isinstance(raw, dict) else default
    return {_norm_key(str(k)): str(v) for k, v in source.items()}


def _collect_key_values(
    rows: list[list[str]], mapping: dict[str, str]
) -> dict[str, str]:
    """Extrai pares chave→valor de linhas de texto (2 padrões).

    1. ``CHAVE | VALOR`` na mesma linha;
    2. Chave sozinha com o valor na linha seguinte (se essa próxima linha
       for outra chave, o valor permanece ausente — nunca inventado).
    """
    sorted_keys = sorted(mapping, key=len, reverse=True)

    def field_of(text: str) -> str | None:
        norm = _norm_key(text)
        if norm in mapping:
            return mapping[norm]
        for key in sorted_keys:
            if norm.startswith(key):
                return mapping[key]
        return None

    collected: dict[str, str] = {}
    index = 0
    while index < len(rows):
        cells = rows[index]
        consume_next = False
        pos = 0
        while pos < len(cells):
            field = field_of(cells[pos])
            if field is not None and field not in collected:
                if pos + 1 < len(cells):
                    collected[field] = " ".join(cells[pos + 1 :])
                    pos = len(cells)
                elif index + 1 < len(rows):
                    next_row = rows[index + 1]
                    if next_row and field_of(next_row[0]) is None:
                        collected[field] = " ".join(next_row)
                        consume_next = True
            pos += 1
        index += 2 if consume_next else 1
    return collected


def _rows_as_text(grid: TableGrid) -> dict[int, list[str]]:
    """Linhas do grid como listas de textos não vazios, na ordem."""
    return {
        row: [c.text.strip() for c in cells]
        for row, cells in _rows_cells(grid).items()
    }


def _rows_cells(grid: TableGrid) -> dict[int, list[Cell]]:
    """Linhas do grid como listas de células não vazias (texto + bbox)."""
    by_row: dict[int, list[Cell]] = {}
    for cell in grid.cells:
        by_row.setdefault(cell.row_index, []).append(cell)
    return {
        row: [c for c in sorted(cells, key=lambda c: c.col_index) if c.text.strip()]
        for row, cells in sorted(by_row.items())
    }


def parse_transportador(
    grid: TableGrid,
    header_row: int,
    key_to_field: dict[str, str] | None = None,
) -> DadosTransportador:
    """Dados do transportador: preâmbulo (linhas antes do cabeçalho)."""
    mapping = key_to_field or _load_key_map(
        "transportador_map", _DEFAULT_TRANSPORTADOR_MAP
    )
    rows_text = _rows_as_text(grid)
    rows = [texts for row, texts in rows_text.items() if row < header_row]
    return DadosTransportador(**_collect_key_values(rows, mapping))


def parse_alteracoes(grids: list[TableGrid]) -> AlteracoesTabela:
    """Seção 'ALTERAÇÕES DA TABELA' — pode estar em qualquer página.

    Localizada pelo marcador configurado (``freight.alteracoes.marker``);
    as linhas seguintes são varridas como pares chave→valor. Ausência da
    seção ou de valores individuais resulta em campos ``None`` explícitos.
    """
    section = _load_freight_section().get("alteracoes", {})
    marker = _norm_key(str(section.get("marker") or _DEFAULT_ALTERACOES_MARK))
    raw_map = section.get("map")
    source = raw_map if isinstance(raw_map, dict) else _DEFAULT_ALTERACOES_MAP
    alter_map = {_norm_key(str(k)): str(v) for k, v in source.items()}

    for grid in grids:
        rows_text = _rows_as_text(grid)
        row_ids = list(rows_text)
        marker_idx = next(
            (
                i
                for i, row in enumerate(row_ids)
                if any(_norm_key(t).startswith(marker) for t in rows_text[row])
            ),
            None,
        )
        if marker_idx is None:
            continue
        rows = [rows_text[row] for row in row_ids[marker_idx + 1 :]]
        return AlteracoesTabela(**_collect_key_values(rows, alter_map))

    logger.debug("🔍 Seção de alterações não encontrada no documento")
    return AlteracoesTabela()


def parse_tabela_frete(grid: TableGrid) -> TabelaFrete | None:
    """Documento completo: ``dados_transportador`` + ``dados_tarifa``.

    Retorna ``None`` quando a página não contém tabela de frete
    (fallback resiliente — o grid permanece no JSON nível 2).
    """
    field_map = _load_field_map()
    detected = _detect_header(grid, field_map)
    if detected is None:
        return None
    header_cells, _, tier_specs, fixed_specs = detected
    header_row = header_cells[0].row_index
    records, warnings = _extract_records(grid, header_row, tier_specs, fixed_specs)
    transportador = parse_transportador(grid, header_row)
    logger.info(
        "📑 Tabela estruturada: id_tabela=%s, transportador=%s, %d registro(s)",
        transportador.id_tabela,
        transportador.sigla,
        len(records),
    )
    return TabelaFrete(
        dados_transportador=transportador,
        dados_tarifa=records,
        warnings=warnings,
    )


@dataclass(frozen=True)
class _SectionSpec:
    """Especificação de seção de cláusulas (marcador ou startswith)."""

    field: str
    marker: str | None
    startswiths: tuple[str, ...]


def _load_generalidades_config() -> tuple[
    list[_SectionSpec], tuple[str, ...], tuple[str, ...]
]:
    """Seções, stop-prefixes e junk-prefixes (settings.yaml freight.generalidades)."""
    section = _load_freight_section().get("generalidades", {})
    raw_sections = section.get("secoes")
    source = (
        raw_sections if isinstance(raw_sections, dict)
        else _DEFAULT_GENERALIDADES_SECTIONS
    )
    specs: list[_SectionSpec] = []
    for field, cfg in source.items():
        marker = cfg.get("marker")
        startswiths = tuple(_norm_key(s) for s in cfg.get("startswith", []))
        specs.append(
            _SectionSpec(
                field=str(field),
                marker=_norm_key(str(marker)) if marker else None,
                startswiths=startswiths,
            )
        )
    stop = tuple(
        _norm_key(s) for s in section.get("stop_prefixes", _DEFAULT_STOP_PREFIXES)
    )
    junk = tuple(
        _norm_key(s) for s in section.get("junk_prefixes", _DEFAULT_JUNK_PREFIXES)
    )
    return specs, stop, junk


def _is_data_row(cells: list[Cell]) -> bool:
    """Linha de dados da tabela (contém CEPs ou valores) — excluída das cláusulas."""
    texts = [c.text for c in cells]
    ceps = sum(1 for t in texts if _CEP_RE.fullmatch(t))
    money = sum(1 for t in texts if _MONEY_RE.fullmatch(t))
    return ceps >= 2 or money >= 1


def parse_generalidades(grids: list[TableGrid]) -> Generalidades:
    """Extrai as cláusulas gerais por seção, cruzando páginas.

    Regras determinísticas (configuráveis em ``freight.generalidades``):

    - ``marker``: abre a seção na coluna x do marcador; textos seguintes
      **na mesma coluna** (sobreposição horizontal) entram nela — inclusive
      na página seguinte (continuação natural de cláusulas quebradas);
    - ``startswith``: coleta qualquer célula que comece com o padrão
      (seções sem título, ex.: textos de pagamento);
    - ``stop_prefixes`` encerram todas as seções abertas (ex.: início do
      bloco de assinaturas) e ``junk_prefixes`` são sempre descartados
      (ex.: cabeçalho DocuSign);
    - linhas de dados da tabela de tarifas nunca entram nas cláusulas.
    """
    specs, stop_prefixes, junk_prefixes = _load_generalidades_config()
    marker_specs = [s for s in specs if s.marker]
    plain_specs = [s for s in specs if not s.marker]

    collected: dict[str, list[str]] = {s.field: [] for s in specs}
    active: dict[str, float] = {}  # campo -> x0 da coluna do marcador

    for grid in grids:
        for cells in _rows_cells(grid).values():
            norms = [_norm_key(c.text) for c in cells]

            if any(n.startswith(stop) for n in norms for stop in stop_prefixes):
                active.clear()
                continue

            marker_cells: set[int] = set()
            opened_now: dict[str, float] = {}
            for pos, norm in enumerate(norms):
                for spec in marker_specs:
                    if norm.startswith(spec.marker or "\x00"):
                        opened_now[spec.field] = cells[pos].bbox[0]
                        marker_cells.add(pos)
            if opened_now:
                # novo marcador = novo bloco: seções do bloco anterior fecham;
                # marcadores da MESMA linha coexistem (colunas lado a lado)
                active = opened_now

            if _is_data_row(cells):
                continue

            for pos, cell in enumerate(cells):
                norm = norms[pos]
                if pos in marker_cells or any(
                    norm.startswith(j) for j in junk_prefixes
                ):
                    continue
                for spec in plain_specs:
                    if norm.startswith(spec.startswiths):
                        collected[spec.field].append(cell.text)
                        break
                if not active:
                    continue
                # âncora pela borda ESQUERDA (x0): cláusulas são alinhadas à
                # esquerda — o centro varia com o comprimento da linha
                target = min(
                    active.items(),
                    key=lambda item: abs(cell.bbox[0] - item[1]),
                )[0]
                collected[target].append(cell.text)

    result = Generalidades(**collected)
    encontradas = sum(1 for v in collected.values() if v)
    logger.info(
        "📜 Generalidades extraídas: %d/%d seções com conteúdo",
        encontradas,
        len(collected),
    )
    return result


def parse_tabela_documento(grids: list[TableGrid]) -> TabelaFrete | None:
    """Agrega o documento inteiro numa única ``TabelaFrete``.

    - ``dados_transportador``: preâmbulo do primeiro grid com tabela;
    - ``dados_tarifa``: registros de **todos** os grids com cabeçalho
      (tabelas que cruzam páginas são unidas — mitigação R5);
    - ``alteracoes_tabela``: seção localizada em qualquer página.

    Retorna ``None`` quando o documento não contém tabela de frete.
    """
    field_map = _load_field_map()
    transportador: DadosTransportador | None = None
    records: list[FreightRecord] = []
    warnings: list[str] = []

    for grid in grids:
        detected = _detect_header(grid, field_map)
        if detected is None:
            continue
        header_cells, _, tier_specs, fixed_specs = detected
        header_row = header_cells[0].row_index
        page_records, page_warnings = _extract_records(
            grid, header_row, tier_specs, fixed_specs
        )
        records.extend(page_records)
        warnings.extend(page_warnings)
        if transportador is None:
            transportador = parse_transportador(grid, header_row)

    if transportador is None:
        logger.warning("⚠️  Documento sem tabela de frete — agregação ignorada")
        return None

    alteracoes = parse_alteracoes(grids)
    generalidades = parse_generalidades(grids)
    tem_alteracoes = alteracoes.model_dump(exclude_none=True)
    logger.info(
        "📑 Documento agregado: id_tabela=%s, %d registro(s), alterações: %s",
        transportador.id_tabela,
        len(records),
        "encontradas" if tem_alteracoes else "não encontradas",
    )
    return TabelaFrete(
        dados_transportador=transportador,
        dados_tarifa=records,
        alteracoes_tabela=alteracoes,
        generalidades=generalidades,
        warnings=warnings,
    )
