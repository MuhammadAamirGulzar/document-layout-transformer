from babeldoc.format.pdf.document_il.il_version_1 import (
    Box, Cropbox, Page, PageLayout, PdfCharacter, PdfForm, PdfParagraph, PdfParagraphComposition,
)
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
from babeldoc.format.pdf.translation_config import TranslationConfig


def typesetter():
    return Typesetting(TranslationConfig(
        translator=None, input_file="", lang_in="en", lang_out="ar", doc_layout_model=None,
    ))


def test_fallback_labels_in_a_figure_keep_their_original_slots():
    page = Page(cropbox=Cropbox(box=Box(0, 0, 600, 800)))
    page.page_layout = [PageLayout(class_name="figure", box=Box(0, 100, 500, 600))]
    start = PdfParagraph(layout_label="fallback_line", unicode="<START>", box=Box(20, 300, 80, 314))
    child = PdfParagraph(layout_label="fallback_line", unicode="child", box=Box(230, 300, 280, 314))
    page.pdf_paragraph = [start, child]

    ts = typesetter()
    ts._mirror_table_grids_for_rtl(page)

    assert (start.box.x, child.box.x) == (20, 230)
    assert ts._should_skip_margin_mirroring(start, page)
    assert ts._should_skip_margin_mirroring(child, page)


def test_fallback_labels_in_a_detected_table_still_follow_its_grid():
    page = Page(cropbox=Cropbox(box=Box(0, 0, 600, 800)))
    page.page_layout = [
        PageLayout(class_name="figure", box=Box(0, 100, 500, 600)),
        PageLayout(class_name="table", box=Box(10, 250, 310, 340)),
    ]
    first = PdfParagraph(layout_label="fallback_line", box=Box(20, 300, 80, 314))
    second = PdfParagraph(layout_label="fallback_line", box=Box(230, 300, 280, 314))
    page.pdf_paragraph = [first, second]

    typesetter()._mirror_table_grids_for_rtl(page)

    assert (first.box.x, second.box.x) == (240, 40)


def test_distinct_pdf_layers_do_not_push_cover_text_out_of_its_lane():
    page = Page(cropbox=Cropbox(box=Box(0, 0, 600, 800)))
    visible = PdfParagraph(layout_label="title", xobj_id=0, box=Box(80, 660, 520, 730))
    overlay = PdfParagraph(layout_label="title", xobj_id=1, box=Box(85, 655, 515, 735))
    sibling = PdfParagraph(layout_label="plain text", xobj_id=0, box=Box(100, 645, 400, 675))
    for paragraph in (visible, overlay):
        box = paragraph.box
        paragraph.pdf_paragraph_composition = [PdfParagraphComposition(
            pdf_character=PdfCharacter(char_unicode="ا", box=Box(box.x, box.y, box.x2, box.y2)),
        )]
    page.pdf_paragraph = [visible, overlay, sibling]

    ts = typesetter()
    ts._resolve_vertical_paragraph_collisions(page)
    ts._resolve_rendered_text_collisions(page)

    assert (visible.box.y, visible.box.y2) == (660, 730)
    assert (overlay.box.y, overlay.box.y2) == (655, 735)
    assert sibling.box.y2 == 655


def test_mirroring_keeps_text_clear_of_an_image_painted_later():
    page = Page(cropbox=Cropbox(box=Box(0, 0, 720, 540)))
    paragraph = PdfParagraph(box=Box(68, 50, 458, 78), xobj_id=0, render_order=10)
    paragraph.pdf_paragraph_composition = [PdfParagraphComposition(
        pdf_character=PdfCharacter(char_unicode="ا", box=Box(200, 56, 450, 70)),
    )]
    image = PdfForm(box=Box(475, 34, 661, 159), form_type="image", xobj_id=0, render_order=20)
    page.pdf_form = [image]

    ts = typesetter()
    assert ts._calculate_rtl_margin_shift(paragraph, page) == 0
    image.render_order = 5
    assert ts._calculate_rtl_margin_shift(paragraph, page) == 194
    image.render_order = 20
    image.xobj_id = 1
    assert ts._calculate_rtl_margin_shift(paragraph, page) == 194
