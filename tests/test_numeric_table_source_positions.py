from types import SimpleNamespace

import pytest

from babeldoc.format.pdf.document_il.il_version_1 import (
    Box, Page, PageLayout, PdfCharacter, PdfCurve, PdfLine, PdfParagraph,
    PdfParagraphComposition, PdfStyle, VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting, TypesettingUnit


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


@pytest.mark.parametrize("numeric_offset", [0, 5])
def test_grouped_table_rules_keep_headers_and_row_labels_in_their_own_slots(numeric_offset):
    page = Page(page_layout=[PageLayout(box=Box(0, 10, 150, 65), class_name="table")])
    page.pdf_curve = [PdfCurve(box=Box(x, 10, x, 65)) for x in (0, 30, 150)]
    page.pdf_curve.append(PdfCurve(box=Box(90, 10, 90, 52)))
    page.pdf_curve.extend(PdfCurve(box=Box(0, y, 150, y)) for y in (10, 40, 52, 65))
    top_headers = [
        source_line("AVG", 55.5, 55)[0],
        source_line("Annual total", 101, 55)[0],
    ]
    header_chars = [
        char
        for text, x in (("Min", 39), ("Max", 78), ("Low", 101), ("High", 129))
        for char in source_line(text, x, 43)[1]
    ]
    header_box = Box(39, 43, 141, 49)
    joined_header = PdfParagraph(
        box=header_box,
        layout_label="fallback_line",
        pdf_paragraph_composition=[
            PdfParagraphComposition(pdf_line=PdfLine(box=header_box, pdf_character=header_chars))
        ],
    )
    row_labels = [source_line("North", 5, 28)[0], source_line("South", 5, 14)[0]]
    values = [
        source_line(value, x, y + numeric_offset)
        for y, texts in ((28, ("1.2", "3.4", "5.6", "7.8")), (14, ("9.1", "2.3", "4.5", "6.7")))
        for x, value in zip((39, 81, 101, 129), texts)
    ]
    paragraphs = [*top_headers, joined_header, *row_labels, *(p for p, _ in values)]
    finder = object.__new__(ParagraphFinder)
    finder.translation_config = SimpleNamespace(lang_out="ar", ocr_workaround=False)

    finder.reconstruct_wired_table_cells(page, paragraphs)

    labels = {paragraph.unicode: paragraph.box for paragraph in paragraphs}
    assert set(labels) == {"AVG", "Annual total", "Min", "Max", "Low", "High", "North", "South"}
    # The inferred Min/Max divider falls just past AVG. The group header
    # must use the full drawn group instead of the width of the Min column.
    assert labels["AVG"].x == 32
    assert labels["AVG"].x2 == 88
    assert labels["Annual total"].x == 92
    assert next(p for p in paragraphs if p.unicode == "AVG").layout_label.endswith("_centered")
    assert labels["Min"].x2 < labels["Max"].x
    assert labels["Max"].x2 < labels["Low"].x
    assert labels["Low"].x2 < labels["High"].x
    assert labels["South"].y2 < labels["North"].y
    assert [id(char) for char in page.pdf_character] == [
        id(char) for _, chars in values for char in chars
    ]


def test_grouped_table_header_renders_centered_in_its_slot():
    paragraph, chars = source_line("AVG", 0, 0)
    paragraph.box = Box(32, 53, 88, 64)
    paragraph.layout_label = "wired_table_cell_fixed_rtl_centered"
    for char in chars:
        char.pdf_style = PdfStyle(font_id="base", font_size=6)
    typesetter = object.__new__(Typesetting)
    typesetter.translation_config = SimpleNamespace(lang_out="ar")
    typesetter.font_mapper = SimpleNamespace(
        base_font=SimpleNamespace(char_lengths=lambda text, size: [3 for _ in text])
    )

    units, fits = typesetter._layout_typesetting_units(
        [TypesettingUnit(char=char) for char in chars],
        paragraph.box,
        scale=1.0,
        line_skip=1.2,
        paragraph=paragraph,
    )

    assert fits
    assert (units[0].box.x + units[-1].box.x2) / 2 == 60


def test_three_line_table_without_vertical_rules_keeps_numbers_in_place():
    page = Page(page_layout=[PageLayout(box=Box(0, 30, 120, 60), class_name="table")])
    page.pdf_curve = [PdfCurve(box=Box(0, y, 120, y)) for y in (30, 52, 60)]
    rows = [
        source_line(value, x, y)
        for y, values in [(40, ["0.91", "0.88"]), (34, ["0.77", "0.69"])]
        for x, value in zip((30, 80), values)
    ]
    label, _ = source_line("Method", 4, 40)
    body, _ = source_line("The report continues below.", 12, 10)
    paragraphs = [p for p, _ in rows] + [label, body]
    expected = [char for _, chars in rows for char in chars]
    finder = object.__new__(ParagraphFinder)
    finder.translation_config = SimpleNamespace(lang_out="ar", ocr_workaround=False)

    finder.reconstruct_wired_table_cells(page, paragraphs)

    assert [id(char) for char in page.pdf_character] == [id(char) for char in expected]
    assert any(p is label for p in paragraphs)
    assert any(p is body for p in paragraphs)
    assert all(p is not source for p in paragraphs for source, _ in rows)
