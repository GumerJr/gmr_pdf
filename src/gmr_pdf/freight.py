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


class TabelaFrete(BaseModel):
    """Documento completo: dados do transportador + dados da tarifa."""

    dados_transportador: DadosTransportador
    dados_tarifa: list[FreightRecord]
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


def _load_transportador_map() -> dict[str, str]:
    """Mapeamento chave visual -> campo (chaves normalizadas)."""
    default = {
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
    try:
        with SETTINGS_FILE.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        raw = data.get("freight", {}).get("transportador_map")
        if raw:
            return {_norm_key(str(k)): str(v) for k, v in raw.items()}
    except FileNotFoundError:
        logger.warning("⚠️  settings.yaml ausente; usando transportador_map default")
    return {_norm_key(k): v for k, v in default.items()}


def parse_transportador(
    grid: TableGrid,
    header_row: int,
    key_to_field: dict[str, str] | None = None,
) -> DadosTransportador:
    """Extrai os dados do transportador do preâmbulo (linhas antes do
    cabeçalho da tabela).

    Dois padrões de formatação, ambos observados em tabelas reais:

    1. ``CHAVE | VALOR`` na mesma linha;
    2. Chave sozinha numa linha e o valor na linha seguinte.

    Se a linha seguinte contiver outra chave, o valor anterior fica
    ``None`` — ausência explícita, nunca um valor inventado.
    """
    mapping = key_to_field or _load_transportador_map()

    by_row: dict[int, list[Cell]] = {}
    for cell in grid.cells:
        if cell.row_index < header_row:
            by_row.setdefault(cell.row_index, []).append(cell)

    rows: list[list[str]] = [
        [c.text for c in sorted(cells, key=lambda c: c.col_index) if c.text.strip()]
        for _, cells in sorted(by_row.items())
    ]
    rows = [r for r in rows if r]

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

    return DadosTransportador(**collected)


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
