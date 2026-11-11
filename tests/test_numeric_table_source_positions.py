from types import SimpleNamespace

from babeldoc.format.pdf.document_il.il_version_1 import (
    Box, Page, PageLayout, PdfCharacter, PdfCurve, PdfLine, PdfParagraph,
    PdfParagraphComposition, VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder


def source_line(text, x, y):
    chars = []
    for index, value in enumerate(text):
        left = x + 3 * index
        box = Box(left, y, left + 3, y + 6)
        chars.append(PdfCharacter(char_unicode=value, box=box, visual_bbox=VisualBbox(box=box)))
    box = Box(x, y, x + 3 * len(text), y + 6)
    paragraph = PdfParagraph(
        box=box, layout_label="fallback_line",
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=PdfLine(box=box, pdf_character=chars))],
    )
    return paragraph, chars


def test_ruled_group_keeps_numeric_cells_at_source_positions():
    # Only the group boundaries are drawn, with two unruled columns per group.
    page = Page(page_layout=[PageLayout(box=Box(0, 30, 120, 60), class_name="table")])
    page.pdf_curve = [PdfCurve(box=Box(x, 30, x, 60)) for x in (0, 60, 120)]
    page.pdf_curve.extend(PdfCurve(box=Box(0, y, 120, y)) for y in (30, 60))
    rows = [
        source_line(value, x, y)
        for y, values in [(49, ["95.92", "96.49"]), (37, ["-3.20", "91.20"])]
        for x, value in zip((12, 36), values)
    ]
    label, _ = source_line("Sample", 72, 49)
    outside, _ = source_line("42.5", 12, 20)
    body, _ = source_line("The report continues below.", 12, 10)
    paragraphs = [p for p, _ in rows] + [label, outside, body]
    expected_glyphs = [char for _, chars in rows for char in chars]
    positions = [(char.box.x, char.box.y) for char in expected_glyphs]
    finder = object.__new__(ParagraphFinder)
    finder.translation_config = SimpleNamespace(lang_out="ar", ocr_workaround=False)

    finder.reconstruct_wired_table_cells(page, paragraphs)

    assert [id(char) for char in page.pdf_character] == [id(char) for char in expected_glyphs]
    assert [(char.box.x, char.box.y) for char in page.pdf_character] == positions
    assert any(p is outside for p in paragraphs)
    assert any(p is body for p in paragraphs)
    assert any(p.unicode == "Sample" for p in paragraphs)
    assert all(p is not source for p in paragraphs for source, _ in rows)
