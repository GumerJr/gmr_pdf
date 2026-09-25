"""Inteligência espacial determinística: reconstrução do "grid invisível".

Estágio 1 (ver ``docs/arquitetura.md`` — heurísticas vetorizadas, zero ML):

- **Perfis de projeção (XY-cut)**: a cobertura de cada eixo pelos intervalos
  ``[início, fim]`` dos spans é calculada vetorizadamente com array de
  eventos (+1/-1) + ``cumsum``. Intervalos contíguos de cobertura se tornam
  **linhas** (eixo Y) e **colunas** (eixo X);
- **Células** = interseção linha × coluna; spans da mesma célula são
  mesclados em ordem horizontal, inserindo espaço quando o gap supera
  ``gap_merge_factor`` × largura mediana do caractere (normalização dos
  artefatos de Excel — risco R5);
- Spans vazios (apenas espaços) e páginas sem camada de texto (gate R1)
  são ignorados com logs apropriados.
"""

from dataclasses import dataclass, replace

import numpy as np
import yaml
from numpy.typing import NDArray

from gmr_pdf.extractor import BBox, DocumentData, PageData
from gmr_pdf.logger import get_logger
from gmr_pdf.settings import SETTINGS_FILE
from gmr_pdf.vectorize import SpansVector, vectorize_document

logger = get_logger(__name__)

_DEFAULTS = {
    "profile_resolution_pt": 1.0,
    "row_band_factor": 0.15,
}

Int32Vector = NDArray[np.int32]
BoolVector = NDArray[np.bool_]


@dataclass(frozen=True)
class Cell:
    """Célula do grid reconstruído (interseção linha × coluna)."""

    row_index: int
    col_index: int
    text: str
    bbox: BBox
    span_indices: tuple[int, ...]


@dataclass(frozen=True)
class TableGrid:
    """Grid bidimensional reconstruído de uma página (ou grupo de orientação)."""

    page_number: int
    n_rows: int
    n_cols: int
    cells: tuple[Cell, ...]
    orientation: str = "horizontal"

    def _matrix(self) -> list[list[str]]:
        matrix = [[""] * self.n_cols for _ in range(self.n_rows)]
        for cell in self.cells:
            matrix[cell.row_index][cell.col_index] = cell.text
        return matrix

    def row_texts(self, row_index: int) -> tuple[str, ...]:
        """Textos das células de uma linha ('' em células vazias)."""
        return tuple(self._matrix()[row_index])

    def to_matrix(self) -> tuple[tuple[str, ...], ...]:
        """Grid completo como matriz de textos."""
        return tuple(tuple(row) for row in self._matrix())


def _load_spatial_config() -> dict[str, float]:
    config = dict(_DEFAULTS)
    try:
        with SETTINGS_FILE.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        section = data.get("spatial", {})
        for key in _DEFAULTS:
            if key in section:
                config[key] = float(section[key])
    except FileNotFoundError:
        logger.warning("⚠️  settings.yaml ausente; usando defaults de spatial")
    return config


def projection_segments(
    starts: NDArray[np.float32], ends: NDArray[np.float32], resolution: float
) -> Int32Vector:
    """Id do segmento de cobertura contígua de cada intervalo (XY-cut).

    Vetorizado via array de eventos (+1 no início, -1 após o fim) seguido
    de ``cumsum``: bins com cobertura > 0 pertencem a um mesmo segmento.
    """
    n = starts.shape[0]
    if n == 0:
        return np.empty(0, dtype=np.int32)
    lo, hi = float(starts.min()), float(ends.max())
    bins = int(np.ceil((hi - lo) / resolution)) + 1
    diff = np.zeros(bins + 2, dtype=np.int32)
    first = np.floor((starts - lo) / resolution).astype(np.int32)
    last = np.floor((ends - lo) / resolution).astype(np.int32)
    np.add.at(diff, first, 1)
    np.add.at(diff, last + 1, -1)
    coverage = np.cumsum(diff)[:bins]
    covered = coverage > 0
    seg_start = covered & ~np.concatenate(([False], covered[:-1]))
    seg_id_of_bin = np.cumsum(seg_start) - 1
    return seg_id_of_bin[first].astype(np.int32)


_ORIENTATION_LABELS: dict[tuple[int, int], str] = {
    (1, 0): "horizontal",
    (0, -1): "vertical",
    (0, 1): "vertical",
    (-1, 0): "horizontal-invertida",
}


def _to_horizontal_frame(
    spans: SpansVector, page_width: float, page_height: float
) -> SpansVector:
    """Normaliza as coordenadas para o frame horizontal (vetorizado).

    Documentos oriundos de Excel frequentemente chegam rotacionados
    (página portrait, conteúdo landscape). A rotação das coordenadas é
    feita sobre os arrays NumPy — a geometria é preservada rigidamente.
    Assume spans de uma única orientação (agrupar antes, ver
    ``extract_grids``).
    """
    if len(spans) == 0:
        return spans
    d0 = round(float(spans.directions[0, 0]))
    d1 = round(float(spans.directions[0, 1]))
    if (d0, d1) == (1, 0):
        return spans
    o, b, c = spans.origins, spans.bboxes, spans.char_bboxes
    if (d0, d1) == (0, -1):  # baixo → cima: gira +90° (x'=H-y, y'=x)
        origins = np.column_stack([page_height - o[:, 1], o[:, 0]])
        bboxes = np.column_stack(
            [page_height - b[:, 3], b[:, 0], page_height - b[:, 1], b[:, 2]]
        )
        char_boxes = np.column_stack(
            [page_height - c[:, 3], c[:, 0], page_height - c[:, 1], c[:, 2]]
        )
    elif (d0, d1) == (0, 1):  # cima → baixo: gira -90° (x'=y, y'=W-x)
        origins = np.column_stack([o[:, 1], page_width - o[:, 0]])
        bboxes = np.column_stack(
            [b[:, 1], page_width - b[:, 2], b[:, 3], page_width - b[:, 0]]
        )
        char_boxes = np.column_stack(
            [c[:, 1], page_width - c[:, 2], c[:, 3], page_width - c[:, 0]]
        )
    else:  # (-1, 0): 180° (x'=W-x, y'=H-y)
        origins = np.column_stack([page_width - o[:, 0], page_height - o[:, 1]])
        bboxes = np.column_stack(
            [page_width - b[:, 2], page_height - b[:, 3],
             page_width - b[:, 0], page_height - b[:, 1]]
        )
        char_boxes = np.column_stack(
            [page_width - c[:, 2], page_height - c[:, 3],
             page_width - c[:, 0], page_height - c[:, 1]]
        )
    return replace(
        spans,
        origins=origins.astype(np.float32),
        bboxes=bboxes.astype(np.float32),
        char_bboxes=char_boxes.astype(np.float32),
    )


def orientation_groups(spans: SpansVector) -> list[tuple[str, SpansVector]]:
    """Agrupa os spans por orientação de escrita (arredondada)."""
    if len(spans) == 0:
        return []
    dirs = np.round(spans.directions).astype(np.int32)
    keys, inverse = np.unique(dirs, axis=0, return_inverse=True)
    groups: list[tuple[str, SpansVector]] = []
    for i, key in enumerate(keys):
        label = _ORIENTATION_LABELS.get((int(key[0]), int(key[1])), "diagonal")
        groups.append((label, spans.select(inverse == i)))
    return groups


def _median_char_width(spans: SpansVector) -> float:
    """Largura mediana de caractere dos spans (base das tolerâncias)."""
    mask = spans.char_counts > 0
    if not mask.any():
        return 1.0
    return float(np.median(spans.widths[mask] / spans.char_counts[mask]))


def cluster_positions(values: NDArray[np.float32], tolerance: float) -> Int32Vector:
    """Agrupa posições 1-D: valores com gap > ``tolerance`` separam clusters.

    Vetorizado: ordena, encontra fronteiras onde o salto supera a
    tolerância e devolve o id do cluster de cada valor.
    """
    n = values.shape[0]
    if n == 0:
        return np.empty(0, dtype=np.int32)
    order = np.argsort(values, kind="stable")
    sorted_values = values[order]
    boundaries = np.flatnonzero(np.diff(sorted_values) > tolerance) + 1
    cluster_sorted = np.zeros(n, dtype=np.int32)
    cluster_sorted[boundaries] = 1
    cluster_sorted = np.cumsum(cluster_sorted)
    result = np.empty(n, dtype=np.int32)
    result[order] = cluster_sorted
    return result


def _median_span_height(spans: SpansVector) -> float:
    if len(spans) == 0:
        return 1.0
    return float(np.median(spans.heights))


def _collapse_spaces(text: str) -> str:
    """Colapsa sequências de espaços internas (``R$     5,50`` → ``R$ 5,50``)."""
    return " ".join(text.split())


def _default_join_tokens() -> tuple[str, ...]:
    """Tokens colantes default (famílias podem sobrescrever via perfil)."""
    return ("R$",)


def _merge_join_tokens(
    items: list[tuple[str, BBox, tuple[int, ...]]],
    tokens: tuple[str, ...],
) -> list[tuple[str, BBox, tuple[int, ...]]]:
    """Funde célula-token (ex.: ``R$``) com a célula seguinte da linha.

    Excel exporta símbolo de moeda e valor como spans separados; a célula
    lógica é a junção. Token sozinho no fim da linha permanece intacto.
    """
    merged: list[tuple[str, BBox, tuple[int, ...]]] = []
    skip_next = False
    for i, (text, bbox, span_ids) in enumerate(items):
        if skip_next:
            skip_next = False
            continue
        if text in tokens and i + 1 < len(items):
            next_text, next_bbox, next_ids = items[i + 1]
            union: BBox = (
                min(bbox[0], next_bbox[0]),
                min(bbox[1], next_bbox[1]),
                max(bbox[2], next_bbox[2]),
                max(bbox[3], next_bbox[3]),
            )
            merged.append((f"{text} {next_text}", union, span_ids + next_ids))
            skip_next = True
        else:
            merged.append((text, bbox, span_ids))
    return merged


def build_grid(
    page_spans: SpansVector,
    page_number: int,
    *,
    row_band_factor: float,
    cell_join_tokens: tuple[str, ...] = ("R$",),
) -> TableGrid:
    """Reconstrói a estrutura da página como **lista de listas** (XY-cut).

    Estratégia deliberadamente resiliente (bordas e alinhamento global
    NÃO são usados — não existem ou são incompletos em tabelas de Excel):

    1. **Linhas**: clustering das baselines (``origin.y``) com tolerância
       ``row_band_factor`` × altura mediana do span — robusto a linhas
       densamente empacotadas sem gaps de cobertura;
    2. **Células**: spans por ordem horizontal dentro da linha. O span é
       a fronteira de célula natural do export Excel — gaps **internos**
       ao span (ex.: ``R$       5,50``) NÃO separam células;
    3. Cada linha é uma lista independente de células (``n_cols`` = máximo
       observado) — cabeçalhos mesclados e linhas de dados convivem;
    4. Espaços internos são colapsados (normalização, sem perda: o nível
       1 do JSON preserva o texto bruto).
    """
    nonblank: BoolVector = np.char.str_len(np.char.strip(page_spans.texts)) > 0
    spans = page_spans.select(nonblank)
    if len(spans) == 0:
        return TableGrid(page_number=page_number, n_rows=0, n_cols=0, cells=())

    tolerance = max(0.5, _median_span_height(spans) * row_band_factor)
    rows = cluster_positions(spans.origins[:, 1].astype(np.float32), tolerance)
    n_rows = int(rows.max()) + 1

    cells: list[Cell] = []
    n_cols = 0
    for row in range(n_rows):
        idx = np.flatnonzero(rows == row)
        order = np.argsort(spans.x_left[idx], kind="stable")
        items: list[tuple[str, BBox, tuple[int, ...]]] = []
        for span_idx in idx[order]:
            bb = spans.bboxes[span_idx]
            bbox: BBox = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
            items.append(
                (_collapse_spaces(str(spans.texts[span_idx])), bbox, (int(span_idx),))
            )
        merged = _merge_join_tokens(items, cell_join_tokens)
        n_cols = max(n_cols, len(merged))
        for col, (text, bbox, span_ids) in enumerate(merged):
            cells.append(
                Cell(row_index=row, col_index=col, text=text, bbox=bbox,
                     span_indices=span_ids)
            )
    return TableGrid(
        page_number=page_number, n_rows=n_rows, n_cols=n_cols, cells=tuple(cells)
    )


def extract_grids(
    document: DocumentData,
    join_tokens: tuple[str, ...] | None = None,
) -> list[TableGrid]:
    """Extrai os grids de cada página (um por grupo de orientação).

    Spans rotacionados são normalizados para o frame horizontal antes do
    XY-cut (conteúdo landscape em página portrait é comum em exports de
    Excel — risco R5). Páginas sem camada de texto (R1) são puladas.
    """
    config = _load_spatial_config()
    all_spans = vectorize_document(document)
    tokens = join_tokens if join_tokens is not None else _default_join_tokens()

    def grids_for_page(page: PageData) -> list[TableGrid]:
        if page.needs_ocr:
            logger.warning("⚠️  Página %d sem texto — grid ignorado (R1)", page.number)
            return []
        page_spans = all_spans.select(all_spans.page_mask(page.number))
        grids: list[TableGrid] = []
        for orientation, group in orientation_groups(page_spans):
            canonical = _to_horizontal_frame(group, page.width, page.height)
            grid = replace(
                build_grid(
                    canonical,
                    page.number,
                    row_band_factor=config["row_band_factor"],
                    cell_join_tokens=tokens,
                ),
                orientation=orientation,
            )
            grids.append(grid)
            logger.info(
                "🧩 Grid página %d [%s]: %d linha(s) × %d coluna(s), %d célula(s)",
                page.number,
                orientation,
                grid.n_rows,
                grid.n_cols,
                len(grid.cells),
            )
        return grids

    return [grid for page in document.pages for grid in grids_for_page(page)]
