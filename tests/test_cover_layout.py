import pytest

from babeldoc.format.pdf.document_il.il_version_1 import (
    Box, Cropbox, Page, PageLayout, PdfCharacter, PdfCurve, PdfParagraph,
    PdfParagraphComposition, PdfPath, PdfSameStyleUnicodeCharacters, PdfStyle,
)
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
from babeldoc.format.pdf.translation_config import TranslationConfig


@pytest.fixture
def typesetter():
    return Typesetting(TranslationConfig(
        translator=None, input_file="", lang_in="en", lang_out="ar", doc_layout_model=None,
    ))


@pytest.mark.parametrize(("label", "size"), [("title", 36), ("plain text", 22)])
def test_centered_display_text_keeps_size_and_alignment(typesetter, label, size):
    page = Page(cropbox=Cropbox(box=Box(20, 0, 620, 800)))
    style = PdfStyle(font_id="base", font_size=size)
    paragraph = PdfParagraph(
        box=Box(140, 400, 494, 400 + size), layout_label=label, pdf_style=style,
        pdf_paragraph_composition=[PdfParagraphComposition(
            pdf_same_style_unicode_characters=PdfSameStyleUnicodeCharacters(
                unicode="العنوان", pdf_style=style,
            ),
        )],
    )
    page.pdf_paragraph = [paragraph]

    typesetter.render_paragraph(paragraph, page, typesetter.font_mapper.fontid2font)

    rendered = typesetter._rendered_text_box(paragraph)
    assert (rendered.x + rendered.x2) / 2 == pytest.approx(317)
    assert max(c.pdf_character.pdf_style.font_size for c in paragraph.pdf_paragraph_composition) == size


def test_display_geometry_excludes_body_tables_and_off_center_text(typesetter):
    page = Page(cropbox=Cropbox(box=Box(0, 0, 600, 800)))
    paragraph = PdfParagraph(
        layout_label="plain text", pdf_style=PdfStyle(font_size=14), box=Box(100, 300, 500, 320),
    )
    assert not typesetter._is_centered_display_text(paragraph, page)
    paragraph.pdf_style.font_size = 25
    paragraph.box = Box(100, 300, 400, 320)
    assert not typesetter._is_centered_display_text(paragraph, page)
    paragraph.box.x2 = 500
    page.page_layout = [PageLayout(class_name="table", box=Box(85, 280, 510, 330))]
    assert not typesetter._is_centered_display_text(paragraph, page)


def make_rule(x1, y, x2):
    return PdfCurve(
        box=Box(x1, y, x2, y + 0.7), fill_background=True, xobj_id=0, debug_info="a",
        pdf_path=[PdfPath(op=op) for op in ("m", "l", "l", "l", "h")],
    )


def test_header_underlines_follow_rendered_lines_without_moving_other_rules(typesetter):
    page = Page(cropbox=Cropbox(box=Box(0, 0, 600, 800)))
    header = PdfParagraph(
        box=Box(390, 710, 535, 752), layout_label="abandon", xobj_id=0,
        pdf_style=PdfStyle(font_size=12),
    )
    rules = [make_rule(390, 742, 535), make_rule(430, 729, 535), make_rule(420, 716, 535)]
    divider = make_rule(50, 710, 545)
    page.pdf_paragraph = [header]
    debug_rule = make_rule(390, 742, 535)
    debug_rule.debug_info = True
    page.pdf_curve = [divider, debug_rule, *rules]

    assert typesetter._find_header_underlines(page) == [(header, rules)]
    header.pdf_paragraph_composition = [
        PdfParagraphComposition(pdf_character=PdfCharacter(char_unicode="ب", box=Box(198, y, 205, y + 11)))
        for y in (740, 723)
    ]
    typesetter._align_header_underlines(header, rules, page.cropbox.box)
    assert all(not rule.relocation_transform for rule in rules)

    header.pdf_paragraph_composition.append(
        PdfParagraphComposition(pdf_character=PdfCharacter(char_unicode="ب", box=Box(198, 706, 205, 717))),
    )
    typesetter._align_header_underlines(header, rules, page.cropbox.box)

    assert [rule.box.x2 for rule in rules] == [205] * 3
    assert [rule.box.y2 for rule in rules] == pytest.approx([738.5, 721.5, 704.5])
    assert all(rule.relocation_transform[:5] == [1, 0, 0, 1, -330] for rule in rules)
    assert not divider.relocation_transform
    assert not debug_rule.relocation_transform
