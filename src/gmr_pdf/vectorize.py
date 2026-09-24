"""Vetorização da geometria extraída (padrão Struct-of-Arrays).

Converte a hierarquia de objetos tipados do extrator em arrays NumPy
contíguos. Toda filtragem e análise espacial da camada 2 (inteligência
espacial / ML) opera sobre máscaras booleanas vetorizadas, sem loops
Python no hot path::

    doc = extract_document(pdf_bytes)
    spans = vectorize_document(doc)
    negritos = spans.select(spans.bold_mask)
    colunas = np.histogram(spans.x_left, bins=32)
"""

from dataclasses import dataclass, replace

import numpy as np
from numpy.typing import NDArray

from gmr_pdf.extractor import (
    FLAG_BOLD,
    FLAG_ITALIC,
    FLAG_MONOSPACED,
    DocumentData,
)

Float32Matrix = NDArray[np.float32]  # (N, K)
Float32Vector = NDArray[np.float32]  # (N,)
UInt8Matrix = NDArray[np.uint8]  # (N, 3)
UInt8Vector = NDArray[np.uint8]  # (N,)
Int32Vector = NDArray[np.int32]  # (N,)
BoolVector = NDArray[np.bool_]  # (N,)
StrVector = NDArray[np.str_]  # (N,)


@dataclass(frozen=True)
class SpansVector:
    """Struct-of-Arrays imutável com todos os spans do documento.

    Arrays alinhados por índice: o span ``i`` tem origin ``origins[i]``,
    texto ``texts[i]`` etc. ``line_ids``/``block_ids``/``page_ids``
    permitem reagrupar spans por estrutura sem perder a hierarquia.
    """

    origins: Float32Matrix  # (N, 2) baseline (x, y)
    bboxes: Float32Matrix  # (N, 4) x0, y0, x1, y1
    char_texts: StrVector  # (M,) caracteres de todos os spans
    char_bboxes: Float32Matrix  # (M, 4) bbox por caractere
    char_span_ids: Int32Vector  # (M,) índice do span dono do caractere
    sizes: Float32Vector  # (N,) fontsize em pontos
    colors: UInt8Matrix  # (N, 3) RGB
    flags: UInt8Vector  # (N,) bits tipográficos
    texts: StrVector  # (N,) texto do span
    directions: Float32Matrix  # (N, 2) vetor de escrita da linha (dir do rawdict)
    font_ids: Int32Vector  # (N,) índice em ``font_vocabulary``
    page_ids: Int32Vector  # (N,) página (0-indexed)
    block_ids: Int32Vector  # (N,) bloco, id global sequencial
    line_ids: Int32Vector  # (N,) linha, id global sequencial
    char_counts: Int32Vector  # (N,) nº de caracteres por span
    font_vocabulary: tuple[str, ...]

    def __len__(self) -> int:
        return int(self.sizes.shape[0])

    # --- acessores de geometria (views vetorizadas, sem cópia) ---

    @property
    def x_left(self) -> Float32Vector:
        return self.bboxes[:, 0]

    @property
    def x_right(self) -> Float32Vector:
        return self.bboxes[:, 2]

    @property
    def y_top(self) -> Float32Vector:
        return self.bboxes[:, 1]

    @property
    def y_bottom(self) -> Float32Vector:
        return self.bboxes[:, 3]

    @property
    def widths(self) -> Float32Vector:
        return self.bboxes[:, 2] - self.bboxes[:, 0]

    @property
    def heights(self) -> Float32Vector:
        return self.bboxes[:, 3] - self.bboxes[:, 1]

    # --- máscaras tipográficas vetorizadas ---

    @property
    def bold_mask(self) -> BoolVector:
        return (self.flags & FLAG_BOLD).astype(np.bool_)

    @property
    def italic_mask(self) -> BoolVector:
        return (self.flags & FLAG_ITALIC).astype(np.bool_)

    @property
    def monospaced_mask(self) -> BoolVector:
        return (self.flags & FLAG_MONOSPACED).astype(np.bool_)

    def page_mask(self, page_number: int) -> BoolVector:
        """Máscara booleana dos spans de uma página específica."""
        return self.page_ids == page_number

    # --- operações vetorizadas ---

    def select(self, mask: BoolVector) -> SpansVector:
        """Retorna um novo SpansVector com apenas os spans da máscara.

        Os arrays de caracteres são filtrados conjuntamente e seus índices
        de span remapeados para a nova numeração.
        """
        kept = np.flatnonzero(mask)
        char_mask = np.isin(self.char_span_ids, kept)
        remap = np.empty(len(self), dtype=np.int32)
        remap[kept] = np.arange(kept.shape[0], dtype=np.int32)
        return replace(
            self,
            origins=self.origins[mask],
            bboxes=self.bboxes[mask],
            char_texts=self.char_texts[char_mask],
            char_bboxes=self.char_bboxes[char_mask],
            char_span_ids=remap[self.char_span_ids[char_mask]],
            sizes=self.sizes[mask],
            colors=self.colors[mask],
            flags=self.flags[mask],
            texts=self.texts[mask],
            directions=self.directions[mask],
            font_ids=self.font_ids[mask],
            page_ids=self.page_ids[mask],
            block_ids=self.block_ids[mask],
            line_ids=self.line_ids[mask],
            char_counts=self.char_counts[mask],
        )

    def size_percentiles(self, quantiles: tuple[float, ...]) -> Float32Vector:
        """Percentis do tamanho da fonte (ex.: (0.5, 0.9, 0.99))."""
        return np.percentile(
            self.sizes, np.asarray(quantiles, dtype=np.float32) * 100
        ).astype(np.float32)


def vectorize_document(document: DocumentData) -> SpansVector:
    """Converte um :class:`DocumentData` em :class:`SpansVector`."""
    origins: list[tuple[float, float]] = []
    bboxes: list[tuple[float, float, float, float]] = []
    sizes: list[float] = []
    colors: list[tuple[int, int, int]] = []
    flags: list[int] = []
    texts: list[str] = []
    directions: list[tuple[float, float]] = []
    font_ids: list[int] = []
    page_ids: list[int] = []
    block_ids: list[int] = []
    line_ids: list[int] = []
    char_counts: list[int] = []
    char_texts: list[str] = []
    char_boxes: list[tuple[float, float, float, float]] = []
    char_span_ids: list[int] = []
    font_map: dict[str, int] = {}
    font_vocabulary: list[str] = []

    block_id = 0
    line_id = 0
    for page in document.pages:
        for block in page.blocks:
            for line in block.lines:
                for span in line.spans:
                    font_id = font_map.get(span.font)
                    if font_id is None:
                        font_id = len(font_vocabulary)
                        font_map[span.font] = font_id
                        font_vocabulary.append(span.font)
                    origins.append(span.origin)
                    bboxes.append(span.bbox)
                    sizes.append(span.size)
                    colors.append(span.color)
                    flags.append(span.flags)
                    texts.append(span.text)
                    directions.append(line.direction)
                    font_ids.append(font_id)
                    page_ids.append(page.number)
                    block_ids.append(block_id)
                    line_ids.append(line_id)
                    char_counts.append(len(span.chars))
                    span_index = len(origins) - 1
                    for char in span.chars:
                        char_texts.append(char.char)
                        char_boxes.append(char.bbox)
                        char_span_ids.append(span_index)
                line_id += 1
            block_id += 1

    return SpansVector(
        origins=np.asarray(origins, dtype=np.float32),
        bboxes=np.asarray(bboxes, dtype=np.float32),
        char_texts=np.asarray(char_texts, dtype=np.str_),
        char_bboxes=np.asarray(char_boxes, dtype=np.float32).reshape(-1, 4),
        char_span_ids=np.asarray(char_span_ids, dtype=np.int32),
        sizes=np.asarray(sizes, dtype=np.float32),
        colors=np.asarray(colors, dtype=np.uint8),
        flags=np.asarray(flags, dtype=np.uint8),
        texts=np.asarray(texts, dtype=np.str_),
        directions=np.asarray(directions, dtype=np.float32),
        font_ids=np.asarray(font_ids, dtype=np.int32),
        page_ids=np.asarray(page_ids, dtype=np.int32),
        block_ids=np.asarray(block_ids, dtype=np.int32),
        line_ids=np.asarray(line_ids, dtype=np.int32),
        char_counts=np.asarray(char_counts, dtype=np.int32),
        font_vocabulary=tuple(font_vocabulary),
    )
