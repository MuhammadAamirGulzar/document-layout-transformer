"""B-28: mixed glyph metrics must not merge separate source rows."""

import pytest

from babeldoc.format.pdf.document_il.il_version_1 import (
    Box,
    PdfCharacter,
    PdfLine,
    PdfParagraph,
    PdfParagraphComposition,
    Page,
    Cropbox,
    VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder


def glyph(char, x, y, visual_shift=0):
    return PdfCharacter(
        char_unicode=char,
        box=Box(x, y, x + 5, y + 12),
        visual_bbox=VisualBbox(box=Box(x, y + visual_shift, x + 5, y + 12 + visual_shift)),
    )


@pytest.mark.parametrize("shift", [-2.52, 0, 2.52], ids=["upper-shifted-down", "aligned", "lower-shifted-up"])
def test_line_split_keeps_distinct_pdf_rows(shift):
    upper = [
        glyph(char, index * 5, 14, shift if shift < 0 and index % 2 else 0)
        for index, char in enumerate("First row")
    ]
    lower = [
        glyph(char, index * 5, 0, shift if shift > 0 and index % 2 else 0)
        for index, char in enumerate("Second row")
    ]
    paragraph = PdfParagraph(
        pdf_paragraph_composition=[
            PdfParagraphComposition(pdf_character=char) for char in upper + lower
        ]
    )
    finder = object.__new__(ParagraphFinder)
    finder._split_paragraph_into_lines(paragraph, set())

    lines = [
        "".join(char.char_unicode for char in composition.pdf_line.pdf_character)
        for composition in paragraph.pdf_paragraph_composition
    ]
    assert lines == ["First row", "Second row"]


def test_vertical_character_keeps_visual_y_bounds():
    char = glyph("X", 0, 0, visual_shift=2)
    char.vertical = True
    finder = object.__new__(ParagraphFinder)
    assert finder._get_effective_y_bounds(char) == (2, 14)


def test_row_split_keeps_subscript_and_superscript_with_their_own_rows():
    def bounded_glyph(text, x, source_y, visual_y):
        return PdfCharacter(
            char_unicode=text,
            box=Box(x, source_y[0], x + 5, source_y[1]),
            visual_bbox=VisualBbox(box=Box(x, visual_y[0], x + 5, visual_y[1])),
        )

    # The font boxes of the two script glyphs overlap across the row gap.
    # The ink of each glyph overlaps the ordinary text on its own row.
    chars = [
        bounded_glyph("A", 0, (15, 26), (15, 22)),
        bounded_glyph("1", 5, (12, 18), (12, 17)),
        bounded_glyph("B", 0, (0, 11), (0, 8)),
        bounded_glyph("2", 5, (7, 13), (7, 10)),
    ]
    paragraph = PdfParagraph(
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=char) for char in chars]
    )
    finder = object.__new__(ParagraphFinder)
    finder._split_paragraph_into_lines(paragraph, set())

    assert [
        "".join(char.char_unicode for char in composition.pdf_line.pdf_character)
        for composition in paragraph.pdf_paragraph_composition
    ] == ["A1", "B2"]


def test_disjoint_visual_bounds_fall_back_to_source_bounds():
    char = glyph("A", 0, 0, visual_shift=20)
    finder = object.__new__(ParagraphFinder)
    assert finder._get_effective_y_bounds(char) == (0, 12)


def test_doubled_fallback_stream_collapses_letters_numbers_and_punctuation():
    text = "TThhee ssyysstteemm ccoossttss $$ 225500,,000000.."
    chars = [glyph(value, index * 5, 0) for index, value in enumerate(text)]
    line = PdfLine(pdf_character=chars, box=Box(0, 0, len(chars) * 5, 12))
    paragraph = PdfParagraph(
        layout_label="fallback_line",
        box=Box(0, 0, len(chars) * 5, 12),
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=line)],
    )
    page = Page(cropbox=Cropbox(box=Box(0, 0, 600, 800)))
    finder = object.__new__(ParagraphFinder)

    finder._deduplicate_artificial_header_characters(paragraph, page)

    assert "".join(char.char_unicode for char in line.pdf_character) == (
        "The system costs $ 250,000."
    )
