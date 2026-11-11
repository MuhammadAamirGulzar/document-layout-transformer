from types import SimpleNamespace

import pytest

from babeldoc.format.pdf.document_il.il_version_1 import (
    Box,
    Page,
    PdfCharacter,
    PdfCurve,
    PdfLine,
    PdfParagraph,
    PdfParagraphComposition,
    PdfPath,
    PdfStyle,
    VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting, TypesettingUnit


def source_line(text, x, y):
    chars = []
    for index, value in enumerate(text):
        left = x + 4 * index
        box = Box(left, y, left + 4, y + 10)
        chars.append(PdfCharacter(
            box=box, visual_bbox=VisualBbox(box=box), char_unicode=value,
            pdf_style=PdfStyle(font_id="body", font_size=10), xobj_id=0,
        ))
    box = Box(x, y, x + 4 * len(text), y + 10)
    return PdfParagraphComposition(pdf_line=PdfLine(box=box, pdf_character=chars))


def dot(x, y):
    return PdfCurve(
        box=Box(x, y, x + 4, y + 4), fill_background=True, xobj_id=0,
        pdf_path=[PdfPath(op=operator) for operator in ("m", "c", "c", "c", "c", "h")],
    )


@pytest.mark.parametrize("body_text", ["\u0627\u0628", "AWS setup"])
def test_drawn_bullets_split_items_and_travel_with_their_text(body_text):
    first_dot, second_dot, unrelated = dot(10, 73), dot(10, 39), dot(80, 19)
    intro = source_line("Introduction", 20, 92)
    first = source_line("First item with", 20, 70)
    wrapped = source_line("a continuation", 20, 55)
    second = source_line("Second item", 20, 36)
    other = source_line("An unrelated shape", 90, 16)
    page = Page(
        pdf_curve=[first_dot, second_dot, unrelated],
        pdf_paragraph=[
            PdfParagraph(
                layout_label="plain text", pdf_paragraph_composition=[intro, first, wrapped, second],
            ),
            PdfParagraph(layout_label="plain text", pdf_paragraph_composition=[other]),
        ],
    )
    finder = object.__new__(ParagraphFinder)

    finder._attach_drawn_list_bullets(page)

    prelude, item_one, item_two, separate = page.pdf_paragraph
    assert [p.layout_label for p in page.pdf_paragraph] == [
        "plain text", "list_item", "list_item", "plain text",
    ]
    assert prelude.pdf_paragraph_composition == [intro]
    assert item_one.pdf_paragraph_composition[1:] == [first, wrapped]
    assert item_two.pdf_paragraph_composition[1:] == [second]
    assert separate.pdf_paragraph_composition == [other]
    assert page.pdf_curve == [unrelated]
    formula = item_one.pdf_paragraph_composition[0].pdf_formula
    assert formula.pdf_curve[0] is first_dot
    assert item_two.pdf_paragraph_composition[0].pdf_formula.pdf_curve[0] is second_dot

    typesetter = object.__new__(Typesetting)
    typesetter.translation_config = SimpleNamespace(lang_out="ar")
    typesetter.font_mapper = SimpleNamespace(
        base_font=SimpleNamespace(char_lengths=lambda text, size: [4 for _ in text])
    )
    body = source_line(body_text, 20, 70).pdf_line.pdf_character
    units, fits = typesetter._layout_typesetting_units(
        [TypesettingUnit(formular=formula), *(TypesettingUnit(char=char) for char in body)],
        Box(20, 20, 200, 100),
        scale=1.0,
        line_skip=1.2,
        paragraph=item_one,
    )

    assert fits
    rendered_dot = next(curve for unit in units for curve in unit.render()[1])
    text_right = max(unit.box.x2 for unit in units if unit.char)
    assert rendered_dot.box.x >= text_right
    assert rendered_dot.relocation_transform

    initial_transform = list(rendered_dot.relocation_transform)
    initial_x, initial_y = rendered_dot.box.x, rendered_dot.box.y
    typesetter._commit_layout_to_paragraph(item_one, page, units, 1.0)
    assert page.pdf_curve == [unrelated]
    assert any(
        comp.pdf_formula and comp.pdf_formula.pdf_curve[0] is rendered_dot
        for comp in item_one.pdf_paragraph_composition
    )

    typesetter._apply_rtl_margin_shift_to_composition(item_one.pdf_paragraph_composition, 35)
    typesetter._shift_rendered_paragraph_vertically(item_one, 12)

    assert rendered_dot.box.x == initial_x + 35
    assert rendered_dot.box.y == initial_y - 12
    assert rendered_dot.relocation_transform[:4] == initial_transform[:4]
    assert rendered_dot.relocation_transform[4:] == [
        initial_transform[4] + 35, initial_transform[5] - 12,
    ]
