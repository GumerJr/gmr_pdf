"""Camada semântica: separação de rótulos fundidos por vocabulário do domínio.

Exports de Excel colam rótulos de cabeçalho em um único span com gaps
geométricos zero/negativos (evidência medida: ``docs/arquitetura.md`` R5) —
impossível de separar por geometria. A fronteira existe apenas no
**vocabulário do domínio** (configurável em ``config/settings.yaml``).

Regras de negócio:
- células cujo texto contenha rótulos conhecidos em sequência são divididas
  nas posições de ocorrência de cada rótulo (ex.: ``CEP FINAL`` dentro de
  ``"CEP INICIAL (OBRIGATA CEP FINAL (OBRIGATÓINTERIORIZAÇÃO"``);
- rótulos de faixa escalonada (``De 2,01 até 4,00``, ``Acima de 10,00``) são
  reconhecidos por padrão regex (valores variam entre tabelas);
- cada célula resultante herda uma fatia proporcional do bbox original
  (aproximação determinística documentada — para textos de cabeçalho).
"""

import re
from dataclasses import dataclass
from itertools import pairwise

from gmr_pdf.extractor import BBox
from gmr_pdf.logger import get_logger
from gmr_pdf.profile import FamilyProfile, load_family_profile
from gmr_pdf.spatial import Cell, TableGrid

logger = get_logger(__name__)


@dataclass(frozen=True)
class SemanticConfig:
    """Vocabulário do domínio para separação de rótulos fundidos."""

    labels: tuple[str, ...]
    tier_pattern: re.Pattern[str]


def load_semantic_config(profile: FamilyProfile | None = None) -> SemanticConfig:
    """Vocabulário do domínio a partir do perfil de família ativo."""
    profile = profile or load_family_profile()
    return SemanticConfig(labels=profile.labels, tier_pattern=profile.tier_pattern)


def _match_positions(text: str, config: SemanticConfig) -> list[int]:
    """Posições de início de cada rótulo conhecido dentro do texto."""
    folded = text.casefold()
    positions: set[int] = set()
    for label in config.labels:
        needle = label.casefold()
        start = 0
        while (found := folded.find(needle, start)) != -1:
            positions.add(found)
            start = found + len(needle)
    for match in config.tier_pattern.finditer(text):
        positions.add(match.start())
    positions.discard(0)
    return sorted(positions)


def split_fused_text(text: str, config: SemanticConfig) -> list[str]:
    """Divide texto fundido nos pontos de ocorrência dos rótulos.

    Retorna lista unitária quando nada é encontrado (célula intacta).
    """
    cuts = _match_positions(text, config)
    if not cuts:
        return [text]
    parts: list[str] = []
    boundaries = [0, *cuts, len(text)]
    for start, end in pairwise(boundaries):
        part = text[start:end].strip()
        if part:
            parts.append(part)
    return parts


def _split_bbox(bbox: BBox, parts_count: int) -> list[BBox]:
    """Fatia horizontal aproximada do bbox (por contagem de partes)."""
    x0, y0, x1, y1 = bbox
    width = x1 - x0
    return [
        (x0 + width * i / parts_count, y0, x0 + width * (i + 1) / parts_count, y1)
        for i in range(parts_count)
    ]


def expand_grid_labels(
    grid: TableGrid, config: SemanticConfig | None = None
) -> TableGrid:
    """Expande células fundidas do grid pelos rótulos do domínio."""
    cfg = config or load_semantic_config()
    by_row: dict[int, list[Cell]] = {}
    for cell in grid.cells:
        by_row.setdefault(cell.row_index, []).append(cell)

    new_cells: list[Cell] = []
    n_cols = 0
    splits = 0
    for row_index in sorted(by_row):
        row_items = sorted(by_row[row_index], key=lambda c: c.col_index)
        col = 0
        for cell in row_items:
            parts = split_fused_text(cell.text, cfg)
            if len(parts) > 1:
                splits += 1
                boxes = _split_bbox(cell.bbox, len(parts))
                for part, box in zip(parts, boxes, strict=True):
                    new_cells.append(
                        Cell(
                            row_index=row_index,
                            col_index=col,
                            text=part,
                            bbox=box,
                            span_indices=cell.span_indices,
                        )
                    )
                    col += 1
            else:
                new_cells.append(
                    Cell(
                        row_index=row_index,
                        col_index=col,
                        text=cell.text,
                        bbox=cell.bbox,
                        span_indices=cell.span_indices,
                    )
                )
                col += 1
        n_cols = max(n_cols, col)

    if splits:
        logger.info(
            "🏷️  Página %d: %d célula(s) dividida(s) pelo vocabulário do domínio",
            grid.page_number,
            splits,
        )
    return TableGrid(
        page_number=grid.page_number,
        n_rows=grid.n_rows,
        n_cols=n_cols,
        cells=tuple(new_cells),
        orientation=grid.orientation,
    )
