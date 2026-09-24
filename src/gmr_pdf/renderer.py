"""Reconstrução geométrica determinística de PDFs.

Nível mais baixo de escrita do PyMuPDF (``TextWriter``): emissão direta
de operadores de texto no content stream da página, com ancoragem na
baseline (``origin``) de cada span — sem qualquer engine de layout,
paginação ou inferência. Posição é mapeamento identidade:
``origin_origem → origin_destino`` (ver ``docs/arquitetura.md``).

Garantias do núcleo determinístico (Estágio 0):

- Cada span é escrito na coordenada exata de sua baseline original;
- ``fontsize`` idêntico ao original;
- Cor RGB idêntica à original;
- Peso/estilo preservados via variantes base-14 (helv/hebo/heit/hebi) —
  sem embedding de fontes proprietárias;
- Zero vetores/drawings e zero imagens na saída (filtro já ocorre na
  extração; a reconstrução apenas emite texto).

Aceita somente rotações múltiplas de 90° (``line.direction`` do MuPDF);
textos com rotação arbitrária caem para ``rotate=0`` (limitação
documentada — Estágio 0).
"""

import pymupdf

from gmr_pdf.extractor import RGB, DocumentData, PageData, Point, SpanData
from gmr_pdf.logger import get_logger

logger = get_logger(__name__)

# Variantes Helvetica base-14: (bold, italic) -> alias PyMuPDF
_FONT_ALIASES: dict[tuple[bool, bool], str] = {
    (False, False): "helv",
    (True, False): "hebo",
    (False, True): "heit",
    (True, True): "hebi",
}

# Direção de escrita do rawdict -> rotação em graus aplicada via morph
_ROTATION_DEGREES: dict[tuple[int, int], int] = {
    (1, 0): 0,  # esquerda → direita
    (0, -1): 90,  # baixo → cima
    (-1, 0): 180,  # direita → esquerda
    (0, 1): 270,  # cima → baixo
}

FontRegistry = dict[tuple[bool, bool], pymupdf.Font]
RGBf = tuple[float, float, float]


def _build_font_registry() -> FontRegistry:
    """Instancia (uma única vez) as 4 variantes base-14 de Helvetica."""
    return {
        variant: pymupdf.Font(alias) for variant, alias in _FONT_ALIASES.items()
    }


def _rotation_from_direction(direction: Point) -> int:
    """Converte o vetor de direção do rawdict em graus (múltiplo de 90°)."""
    key = (round(direction[0]), round(direction[1]))
    return _ROTATION_DEGREES.get(key, 0)


def _normalize_color(color: RGB) -> RGBf:
    return (color[0] / 255, color[1] / 255, color[2] / 255)


def _font_for_span(span: SpanData, fonts: FontRegistry) -> pymupdf.Font:
    return fonts[(span.is_bold, span.is_italic)]


def _append_span(
    writer: pymupdf.TextWriter, span: SpanData, fonts: FontRegistry
) -> None:
    writer.append(
        pymupdf.Point(span.origin),
        span.text,
        font=_font_for_span(span, fonts),
        fontsize=span.size,
    )


def _render_page(
    page: pymupdf.Page,
    page_data: PageData,
    fonts: FontRegistry,
) -> None:
    """Emite todos os spans de uma página em nível de content stream.

    Spans horizontais são agrupados por cor (cor é aplicada por chamada
    de ``write_text``). Spans rotacionados exigem writer dedicado:
    a rotação é aplicada via ``morph`` com fixpoint na baseline do span.
    """
    writers: dict[tuple[int, int, int], pymupdf.TextWriter] = {}
    for block in page_data.blocks:
        for line in block.lines:
            degrees = _rotation_from_direction(line.direction)
            for span in line.spans:
                if not span.text:
                    continue
                if degrees == 0:
                    writer = writers.get(span.color)
                    if writer is None:
                        writer = pymupdf.TextWriter(page.rect)
                        writers[span.color] = writer
                    _append_span(writer, span, fonts)
                else:
                    rotated = pymupdf.TextWriter(page.rect)
                    _append_span(rotated, span, fonts)
                    rotated.write_text(
                        page,
                        color=_normalize_color(span.color),
                        morph=(
                            pymupdf.Point(span.origin),
                            pymupdf.Matrix(degrees),
                        ),
                    )
    for color, writer in writers.items():
        writer.write_text(page, color=_normalize_color(color))


def reconstruct_document(document: DocumentData) -> bytes:
    """Reconstrói o PDF higienizado a partir do :class:`DocumentData`.

    Retorna ``bytes`` de um PDF novo, com as mesmas dimensões de página,
    contendo exclusivamente a camada de texto nas posições originais.
    """
    fonts = _build_font_registry()
    out = pymupdf.open()
    for page_data in document.pages:
        page = out.new_page(width=page_data.width, height=page_data.height)
        _render_page(page, page_data, fonts)
    pdf_bytes = out.tobytes(garbage=4, deflate=True)
    out.close()
    logger.info(
        "🏗️  Reconstrução concluída: %d página(s), %d span(s) reescritos",
        document.page_count,
        document.span_count,
    )
    return pdf_bytes
