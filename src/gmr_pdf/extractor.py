"""Extração de texto em baixo nível via PyMuPDF (modo ``rawdict``).

``rawdict`` é o nível mais baixo exposto pela API Python do PyMuPDF:
cada caractere vem com ``bbox`` e ``origin`` próprios, agrupados em
spans com ``origin`` (baseline), ``size``, ``color`` e ``flags``.

Regras do pipeline (ver ``docs/arquitetura.md``):

- Apenas blocos do Tipo 0 (texto) são capturados;
- Blocos do Tipo 1 (imagens) e drawings (vetores/grids) são ignorados;
- Uma página sem nenhum span indica PDF escaneado → gatilho do
  fallback de OCR (risco R1).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

import pymupdf

from gmr_pdf.logger import get_logger

logger = get_logger(__name__)

# Tipos de bloco do rawdict do MuPDF
TEXT_BLOCK = 0
IMAGE_BLOCK = 1

# Bits do campo ``flags`` do span (tipografia)
FLAG_SUPERSCRIPT = 1 << 0
FLAG_ITALIC = 1 << 1
FLAG_SERIFED = 1 << 2
FLAG_MONOSPACED = 1 << 3
FLAG_BOLD = 1 << 4

BBox = tuple[float, float, float, float]
Point = tuple[float, float]
RGB = tuple[int, int, int]
RawDict = dict[str, Any]


def _as_bbox(value: Iterable[float]) -> BBox:
    return cast(BBox, tuple(value))


def _as_point(value: Iterable[float]) -> Point:
    return cast(Point, tuple(value))


@dataclass(frozen=True)
class CharData:
    """Caractere individual com geometria própria (nível mais baixo)."""

    char: str
    bbox: BBox
    origin: Point  # baseline do caractere


@dataclass(frozen=True)
class SpanData:
    """Sequência de caracteres com tipografia uniforme."""

    text: str
    font: str
    size: float
    color: RGB
    flags: int
    origin: Point  # baseline do span (âncora da reconstrução)
    bbox: BBox
    chars: tuple[CharData, ...]

    @property
    def is_bold(self) -> bool:
        return bool(self.flags & FLAG_BOLD)

    @property
    def is_italic(self) -> bool:
        return bool(self.flags & FLAG_ITALIC)

    @property
    def is_monospaced(self) -> bool:
        return bool(self.flags & FLAG_MONOSPACED)


@dataclass(frozen=True)
class LineData:
    """Linha de texto: sequência de spans na mesma baseline."""

    bbox: BBox
    direction: Point  # direção de escrita (geralmente (1.0, 0.0))
    spans: tuple[SpanData, ...]

    @property
    def text(self) -> str:
        return "".join(span.text for span in self.spans)


@dataclass(frozen=True)
class BlockData:
    """Bloco de texto (Tipo 0). Blocos de imagem nunca entram aqui."""

    bbox: BBox
    lines: tuple[LineData, ...]

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


@dataclass(frozen=True)
class PageData:
    """Página com dimensões originais e seus blocos de texto."""

    number: int  # 0-indexed
    width: float
    height: float
    blocks: tuple[BlockData, ...]

    @property
    def span_count(self) -> int:
        return sum(len(line.spans) for block in self.blocks for line in block.lines)

    @property
    def needs_ocr(self) -> bool:
        """True quando a página não tem camada de texto (PDF escaneado)."""
        return self.span_count == 0


@dataclass(frozen=True)
class DocumentData:
    """Documento extraído, pronto para a reconstrução geométrica."""

    pages: tuple[PageData, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def span_count(self) -> int:
        return sum(page.span_count for page in self.pages)


def _srgb_to_rgb(color: int) -> RGB:
    """Converte o inteiro sRGB do MuPDF (0xRRGGBB) para tupla RGB."""
    return ((color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF)


def _parse_span(raw_span: RawDict) -> SpanData:
    chars = tuple(
        CharData(
            char=str(c["c"]),
            bbox=_as_bbox(c["bbox"]),
            origin=_as_point(c["origin"]),
        )
        for c in cast(list[RawDict], raw_span["chars"])
    )
    return SpanData(
        text="".join(c.char for c in chars),
        font=str(raw_span["font"]),
        size=float(raw_span["size"]),
        color=_srgb_to_rgb(int(raw_span["color"])),
        flags=int(raw_span["flags"]),
        origin=_as_point(raw_span["origin"]),
        bbox=_as_bbox(raw_span["bbox"]),
        chars=chars,
    )


def _parse_line(raw_line: RawDict) -> LineData:
    return LineData(
        bbox=_as_bbox(raw_line["bbox"]),
        direction=_as_point(raw_line["dir"]),
        spans=tuple(
            _parse_span(s) for s in cast(list[RawDict], raw_line["spans"])
        ),
    )


def extract_page(page: pymupdf.Page, number: int | None = None) -> PageData:
    """Extrai os blocos de texto (Tipo 0) de uma página, ignorando imagens."""
    raw = cast(RawDict, page.get_text("rawdict"))
    blocks = tuple(
        BlockData(
            bbox=_as_bbox(raw_block["bbox"]),
            lines=tuple(
                _parse_line(line) for line in cast(list[RawDict], raw_block["lines"])
            ),
        )
        for raw_block in cast(list[RawDict], raw["blocks"])
        if raw_block["type"] == TEXT_BLOCK
    )
    return PageData(
        number=number if number is not None else page.number,
        width=float(raw["width"]),
        height=float(raw["height"]),
        blocks=blocks,
    )


def extract_document(
    pdf_bytes: bytes,
    page_numbers: Iterable[int] | None = None,
) -> DocumentData:
    """Extrai o texto de um PDF recebido como ``bytes``.

    Compatível com a saída de ``GoogleDriveClient.download_pdf()`` —
    nenhum arquivo precisa ser gravado em disco.
    """
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        indices = page_numbers if page_numbers is not None else range(doc.page_count)
        pages = tuple(extract_page(doc[i], number=i) for i in indices)
    document = DocumentData(pages=pages)
    for page in document.pages:
        if page.needs_ocr:
            logger.warning(
                "⚠️  Página %d sem camada de texto (possível escaneado) — gate R1",
                page.number,
            )
    logger.info(
        "🔎 Extração concluída: %d página(s), %d span(s)",
        document.page_count,
        document.span_count,
    )
    return document
