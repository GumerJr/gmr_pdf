"""Camada de domínio: registros tipados de tabelas de frete.

Transforma o grid (``TableGrid``) em ``TabelaFrete`` prontos para
comparação com o cadastro. Todo o vocabulário/regras vivem em perfis de
família (``config/families/*.yaml``, ver ``gmr_pdf.profile``) — o código
é genérico, o domínio é configuração.

Estratégia (white-box, auditável):

1. **Cabeçalho**: linha com mais rótulos do vocabulário do perfil;
   ausência → fallback resiliente (grid genérico permanece no JSON nível 2);
2. **Mapeamento** label → campo via ``field_map`` do perfil (prefixo,
   case-insensitive — tolera truncamentos como ``"(OBRIGATA"``);
3. **Linha de dados**: assinatura derivada do cabeçalho (CEP se existir
   coluna cep_*; dinheiro caso contrário) ou explícita no perfil;
4. **Faixas escalonadas**: valores monetários casados por ordem com os
   rótulos; valor único → política configurável (``fanout``/``first``);
5. **Chave-valor** do preâmbulo: três padrões — ``CHAVE | VALOR``, chave
   sozinha com valor na linha seguinte e ``CHAVE: valor`` inline;
6. **Ambiguidades posicionais** (coluna sem valor na linha) resolvidas
   pelo centro geométrico do cabeçalho;
7. **Proveniência**: todo registro carrega ``page`` e ``row``; ausências
   são ``None`` explícitos, incompatibilidades viram ``warnings``.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field, field_validator

from gmr_pdf.logger import get_logger
from gmr_pdf.profile import (
    FamilyProfile,
    detect_family,
    load_family_profile,
    norm_key,
)
from gmr_pdf.spatial import Cell, TableGrid

logger = get_logger(__name__)

_CEP_RE = re.compile(r"^\d{5}-\d{3}$")
_INT_RE = re.compile(r"^\d{1,4}$")


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
        text = re.sub(r"[^\d,.]", "", str(value))
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
    """Coluna do cabeçalho: campo canônico ou faixa escalonada."""

    kind: str  # "field" | "tier"
    name: str
    center_x: float


def _parse_br_decimal(text: str) -> Decimal | None:
    cleaned = re.sub(r"[^\d,]", "", text)
    if not cleaned:
        return None
    try:
        return Decimal(cleaned.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


# --------------------------------------------------------------------------
# Cabeçalho
# --------------------------------------------------------------------------


def _find_header_row(
    grid: TableGrid, label_prefixes: tuple[str, ...]
) -> list[Cell] | None:
    """Linha do cabeçalho = a com mais células iniciadas por rótulo."""

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
    grid: TableGrid, profile: FamilyProfile
) -> tuple[list[Cell], list[ColumnSpec], list[ColumnSpec], list[ColumnSpec]] | None:
    """Detecta cabeçalho e retorna (células, specs, tiers, fixos) ou None."""
    header_cells = _find_header_row(grid, tuple(profile.field_map))
    if header_cells is None:
        logger.warning(
            "⚠️  Página %d: cabeçalho de frete não detectado — página ignorada",
            grid.page_number,
        )
        return None
    specs = _column_specs(header_cells, profile.field_map, profile.tier_pattern)
    tier_specs = [s for s in specs if s.kind == "tier"]
    fixed_specs = [s for s in specs if s.kind == "field"]
    logger.info(
        "🚚 Cabeçalho detectado na página %d: %d campos fixos, %d faixas",
        grid.page_number,
        len(fixed_specs),
        len(tier_specs),
    )
    return header_cells, specs, tier_specs, fixed_specs


# --------------------------------------------------------------------------
# Registros (dados_tarifa)
# --------------------------------------------------------------------------


def _coerce_field_value(name: str, text: str) -> str | int | Decimal | None:
    """Tipa o valor conforme o campo de destino (auditoria: sem coerção cega)."""
    if name == "prazo" and _INT_RE.fullmatch(text):
        return int(text)
    if name in {"prazo", "diaria"}:
        return _parse_br_decimal(text)
    return text


def _build_tiers(
    tier_specs: list[ColumnSpec],
    money_cells: list[Cell],
    profile: FamilyProfile,
    location: str,
) -> tuple[list[FreightTier], str | None]:
    """Casa valores monetários com faixas; aplica a política de valor único."""
    if len(money_cells) == len(tier_specs):
        faixas = [
            FreightTier(rotulo=s.name, valor=c.text)
            for s, c in zip(tier_specs, money_cells, strict=True)
        ]
        return faixas, None
    if (
        len(money_cells) == 1
        and len(tier_specs) > 1
        and profile.tier_single_value_policy == "fanout"
    ):
        faixas = [
            FreightTier(rotulo=s.name, valor=money_cells[0].text) for s in tier_specs
        ]
        msg = f"{location}: valor único replicado para {len(tier_specs)} faixas"
        return faixas, msg
    faixas = [
        FreightTier(rotulo=s.name, valor=c.text)
        for s, c in zip(tier_specs, money_cells, strict=False)
    ]
    msg = f"{location}: {len(money_cells)} valores ≠ {len(tier_specs)} faixas"
    return faixas, msg


def _extract_records(
    grid: TableGrid,
    header_row: int,
    tier_specs: list[ColumnSpec],
    fixed_specs: list[ColumnSpec],
    profile: FamilyProfile,
) -> tuple[list[FreightRecord], list[str]]:
    """Extrai os registros (linhas após o cabeçalho) do grid.

    A assinatura de linha de dados é derivada do cabeçalho no modo
    ``auto``: se existirem colunas ``cep_inicial``/``cep_final``, exige
    pares de CEP; caso contrário, exige células monetárias (padrão do
    perfil). Modos explícitos (``cep_pairs``/``money``) usam
    ``data_row.min_count``.
    """
    header_fields = {s.name for s in fixed_specs}
    has_cep = "cep_inicial" in header_fields and "cep_final" in header_fields
    money_re = profile.money_pattern

    def is_data_row(cells: list[Cell]) -> bool:
        texts = [c.text for c in cells]
        ceps = sum(1 for t in texts if _CEP_RE.fullmatch(t))
        money = sum(1 for t in texts if money_re.fullmatch(t))
        mode = profile.data_row_mode
        if mode == "cep_pairs":
            return ceps >= profile.data_row_min
        if mode == "money":
            return money >= profile.data_row_min
        return (ceps >= 2) if has_cep else (money >= 1)

    by_row: dict[int, list[Cell]] = {}
    for cell in grid.cells:
        if cell.row_index > header_row:
            by_row.setdefault(cell.row_index, []).append(cell)

    records: list[FreightRecord] = []
    warnings: list[str] = []
    for row_index in sorted(by_row):
        row_cells = sorted(by_row[row_index], key=lambda c: c.col_index)
        if not is_data_row(row_cells):
            continue  # preâmbulo/cláusulas: não é linha de dados

        record: dict[str, Any] = {"page": grid.page_number, "row": row_index}
        if has_cep:
            ceps = [t for t in (c.text for c in row_cells) if _CEP_RE.fullmatch(t)]
            record["cep_inicial"] = ceps[0]
            record["cep_final"] = ceps[1]

        money_cells = [c for c in row_cells if money_re.fullmatch(c.text)]
        non_money = [
            c
            for c in row_cells
            if not money_re.fullmatch(c.text)
            and not (has_cep and _CEP_RE.fullmatch(c.text))
        ]

        location = f"página {grid.page_number} linha {row_index}"
        faixas, tier_msg = _build_tiers(tier_specs, money_cells, profile, location)
        if tier_msg:
            logger.warning("⚠️  %s", tier_msg)
            warnings.append(tier_msg)

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


def parse_freight_grid(
    grid: TableGrid,
    profile: FamilyProfile | None = None,
    template: str = "tabela_escalonada",
) -> FreightTable | None:
    """Converte um grid em uma ``FreightTable`` tipada.

    Retorna ``None`` quando nenhum cabeçalho é detectado (fallback: o
    grid permanece disponível no JSON nível 2 — nunca descarte silencioso).
    """
    profile = profile or load_family_profile()
    detected = _detect_header(grid, profile)
    if detected is None:
        return None
    header_cells, specs, tier_specs, fixed_specs = detected
    records, warnings = _extract_records(
        grid, header_cells[0].row_index, tier_specs, fixed_specs, profile
    )
    return FreightTable(
        page=grid.page_number,
        template=template,
        header=[s.name for s in specs],
        records=records,
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Pares chave→valor (preâmbulo e seção de alterações)
# --------------------------------------------------------------------------


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


def _key_pattern(key: str) -> re.Pattern[str]:
    """Regex com espaços flexíveis + case-insensitive para a chave visual."""
    return re.compile(
        r"^\s*" + r"\s*".join(map(re.escape, key.split())), re.IGNORECASE
    )


def _collect_key_values(
    rows: list[list[str]], mapping: dict[str, str]
) -> dict[str, str]:
    """Extrai pares chave→valor de linhas de texto (3 padrões).

    1. ``CHAVE | VALOR`` na mesma linha (células distintas);
    2. Chave sozinha com o valor na linha seguinte;
    3. ``CHAVE: valor`` inline na mesma célula.

    Se a linha seguinte contiver outra chave, o valor fica ausente —
    ``None`` explícito, nunca um valor inventado.
    """
    sorted_keys = sorted(mapping, key=len, reverse=True)

    def match(text: str) -> tuple[str | None, str | None]:
        """Retorna (campo, valor inline) para uma célula de texto."""
        norm = norm_key(text)
        if norm in mapping:
            return mapping[norm], None
        for key in sorted_keys:
            if not norm.startswith(key):
                continue
            found = _key_pattern(key).match(text)
            rest = text[found.end() :].strip() if found else ""
            if rest.startswith(":"):
                inline = rest[1:].strip()
                return mapping[key], inline or None
            return mapping[key], None
        return None, None

    collected: dict[str, str] = {}
    index = 0
    while index < len(rows):
        cells = rows[index]
        consume_next = False
        pos = 0
        while pos < len(cells):
            field, inline = match(cells[pos])
            if field is not None and field not in collected:
                if inline:
                    collected[field] = inline
                elif pos + 1 < len(cells):
                    collected[field] = " ".join(cells[pos + 1 :])
                    pos = len(cells)
                elif index + 1 < len(rows):
                    next_row = rows[index + 1]
                    if next_row and match(next_row[0])[0] is None:
                        collected[field] = " ".join(next_row)
                        consume_next = True
            pos += 1
        index += 2 if consume_next else 1
    return collected


def parse_transportador(
    grid: TableGrid,
    header_row: int,
    profile: FamilyProfile | None = None,
) -> DadosTransportador:
    """Dados do transportador: preâmbulo (linhas antes do cabeçalho)."""
    profile = profile or load_family_profile()
    mapping = {
        norm_key(k): v for k, v in profile.transportador_map.items()
    }
    rows_text = _rows_as_text(grid)
    rows = [texts for row, texts in rows_text.items() if row < header_row]
    return DadosTransportador(**_collect_key_values(rows, mapping))


def parse_alteracoes(
    grids: list[TableGrid], profile: FamilyProfile | None = None
) -> AlteracoesTabela:
    """Seção 'ALTERAÇÕES DA TABELA' — pode estar em qualquer página.

    Localizada pelo marcador do perfil; as linhas seguintes são varridas
    como pares chave→valor. Ausência da seção ou de valores individuais
    resulta em campos ``None`` explícitos.
    """
    profile = profile or load_family_profile()
    marker = profile.alteracoes_marker
    alter_map = {norm_key(k): v for k, v in profile.alteracoes_map.items()}

    for grid in grids:
        rows_text = _rows_as_text(grid)
        row_ids = list(rows_text)
        marker_idx = next(
            (
                i
                for i, row in enumerate(row_ids)
                if any(norm_key(t).startswith(marker) for t in rows_text[row])
            ),
            None,
        )
        if marker_idx is None:
            continue
        rows = [rows_text[row] for row in row_ids[marker_idx + 1 :]]
        return AlteracoesTabela(**_collect_key_values(rows, alter_map))

    logger.debug("🔍 Seção de alterações não encontrada no documento")
    return AlteracoesTabela()


# --------------------------------------------------------------------------
# Cláusulas gerais (generalidades)
# --------------------------------------------------------------------------


def _is_clause_data_row(cells: list[Cell], money_re: re.Pattern[str]) -> bool:
    """Linha de dados da tabela (CEPs/valores) — excluída das cláusulas."""
    texts = [c.text for c in cells]
    ceps = sum(1 for t in texts if _CEP_RE.fullmatch(t))
    money = sum(1 for t in texts if money_re.fullmatch(t))
    return ceps >= 2 or money >= 1


def parse_generalidades(
    grids: list[TableGrid], profile: FamilyProfile | None = None
) -> Generalidades:
    """Extrai as cláusulas gerais por seção, cruzando páginas.

    Regras determinísticas (configuráveis no perfil da família):

    - ``marker``: abre a seção na coluna x₀ do marcador; textos seguintes
      **na mesma coluna** entram nela — inclusive na página seguinte
      (continuação de cláusulas quebradas);
    - ``startswith``: coleta qualquer célula que comece com o padrão
      (seções sem título, ex.: textos de pagamento);
    - um novo marcador fecha o bloco anterior (marcadores da MESMA linha
      coexistem — colunas lado a lado);
    - ``stop_prefixes`` encerram todas as seções abertas (assinaturas /
      cláusula de conformidade), ``junk_prefixes`` são descartados;
    - linhas de dados da tabela de tarifas nunca entram nas cláusulas.
    """
    profile = profile or load_family_profile()
    marker_specs = [s for s in profile.sections if s.marker]
    plain_specs = [s for s in profile.sections if not s.marker]

    model_fields = set(Generalidades.model_fields)
    collected: dict[str, list[str]] = {s.field: [] for s in profile.sections}
    unknown = [f for f in collected if f not in model_fields]
    if unknown:
        logger.warning("⚠️  Seções sem campo no schema (ignoradas): %s", unknown)

    active: dict[str, float] = {}  # campo -> x0 da coluna do marcador

    for grid in grids:
        for cells in _rows_cells(grid).values():
            norms = [norm_key(c.text) for c in cells]

            if any(n.startswith(p) for n in norms for p in profile.stop_prefixes):
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
                active = opened_now

            if _is_clause_data_row(cells, profile.money_pattern):
                continue

            for pos, cell in enumerate(cells):
                norm = norms[pos]
                if pos in marker_cells or any(
                    norm.startswith(j) for j in profile.junk_prefixes
                ):
                    continue
                for spec in plain_specs:
                    if norm.startswith(spec.startswiths):
                        collected[spec.field].append(cell.text)
                        break
                if not active:
                    continue
                # âncora pela borda ESQUERDA (x0): cláusulas são alinhadas
                # à esquerda — o centro varia com o comprimento da linha
                target = min(
                    active.items(), key=lambda item: abs(cell.bbox[0] - item[1])
                )[0]
                if target in model_fields:
                    collected[target].append(cell.text)

    result = Generalidades(**collected)
    encontradas = sum(1 for v in collected.values() if v)
    logger.info(
        "📜 Generalidades extraídas: %d/%d seções com conteúdo",
        encontradas,
        len(collected),
    )
    return result


def parse_tabela_frete(
    grid: TableGrid, profile: FamilyProfile | None = None
) -> TabelaFrete | None:
    """Documento de UM grid: ``dados_transportador`` + ``dados_tarifa``.

    Retorna ``None`` quando a página não contém tabela de frete
    (fallback resiliente — o grid permanece no JSON nível 2).
    """
    profile = profile or load_family_profile()
    detected = _detect_header(grid, profile)
    if detected is None:
        return None
    header_cells, _, tier_specs, fixed_specs = detected
    header_row = header_cells[0].row_index
    records, warnings = _extract_records(
        grid, header_row, tier_specs, fixed_specs, profile
    )
    transportador = parse_transportador(grid, header_row, profile)
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


# --------------------------------------------------------------------------
# Agregação em nível de documento
# --------------------------------------------------------------------------


def parse_tabela_documento(
    grids: list[TableGrid], profile: FamilyProfile | None = None
) -> TabelaFrete | None:
    """Agrega o documento inteiro numa única ``TabelaFrete``.

    - ``dados_transportador``: preâmbulo do primeiro grid com tabela;
    - ``dados_tarifa``: registros de **todos** os grids com cabeçalho
      (tabelas que cruzam páginas são unidas — mitigação R5);
    - ``alteracoes_tabela``/``generalidades``: localizadas em qualquer
      página.

    Sem perfil informado, a família é auto-detectada por marcadores.
    Retorna ``None`` quando o documento não contém tabela de frete.
    """
    if profile is None:
        all_texts = [cell.text for grid in grids for cell in grid.cells]
        profile = detect_family(all_texts)

    transportador: DadosTransportador | None = None
    records: list[FreightRecord] = []
    warnings: list[str] = []

    for grid in grids:
        detected = _detect_header(grid, profile)
        if detected is None:
            continue
        header_cells, _, tier_specs, fixed_specs = detected
        header_row = header_cells[0].row_index
        page_records, page_warnings = _extract_records(
            grid, header_row, tier_specs, fixed_specs, profile
        )
        records.extend(page_records)
        warnings.extend(page_warnings)
        if transportador is None:
            transportador = parse_transportador(grid, header_row, profile)

    if transportador is None:
        logger.warning("⚠️  Documento sem tabela de frete — agregação ignorada")
        return None

    alteracoes = parse_alteracoes(grids, profile)
    generalidades = parse_generalidades(grids, profile)
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
