"""B-28: mixed glyph metrics must not merge separate source rows."""

import pytest

from babeldoc.format.pdf.document_il.il_version_1 import (
    Box,
    PdfCharacter,
    PdfParagraph,
    PdfParagraphComposition,
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
