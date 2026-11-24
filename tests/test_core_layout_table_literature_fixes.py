"""Focused tests for core PDF quality fixes: watermark typesetting hook, bibliography/column merge guards, table row & body clearance, and UN/literature glossary."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from babeldoc.format.pdf import high_level
from babeldoc.format.pdf.document_il import il_version_1
from babeldoc.format.pdf.document_il.midend.domain_glossary import (
    apply_post_glossary,
    apply_pre_glossary,
)
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
from babeldoc.format.pdf.document_il.utils.layout_semantics import (
    can_merge_paragraphs,
    paragraph_semantic_zone,
)


def _para(text: str, x: float, y: float, x2: float, y2: float, *, label: str = "body_text", trace: dict | None = None) -> il_version_1.PdfParagraph:
    style = il_version_1.PdfStyle(font_id="F0", font_size=10.0)
    ch = il_version_1.PdfCharacter(
        char_unicode="X",
        box=il_version_1.Box(x=x, y=y, x2=x2, y2=y2),
        pdf_style=style,
    )
    comp = il_version_1.PdfParagraphComposition(pdf_character=ch)
    p = il_version_1.PdfParagraph(
        unicode=text,
        box=il_version_1.Box(x=x, y=y, x2=x2, y2=y2),
        layout_label=label,
        pdf_style=style,
        pdf_paragraph_composition=[comp],
        first_line_indent=False,
        vertical=False,
        xobj_id=-1,
    )
    p.pipeline_trace = trace
    return p


def test_watermark_path_uses_typesetting_document():
    src = inspect.getsource(high_level.generate_first_page_with_watermark)
    assert "typesetting_document" in src
    assert "typsetting_document" not in src


def test_paragraph_finder_second_same_line_merge_passes_page_context():
    src = inspect.getsource(ParagraphFinder.process_page)
    assert "self.merge_same_line_sentence_fragments(paragraphs, page=page)" in src
    assert "self.merge_same_line_sentence_fragments(paragraphs)" not in src


def test_bibliography_entries_are_isolated_and_never_merged():
    page = il_version_1.Page(
        page_number=0,
        cropbox=il_version_1.Cropbox(box=il_version_1.Box(x=0, y=0, x2=595, y2=842)),
        page_layout=[
            il_version_1.PageLayout(
                id=1,
                class_name="reference",
                box=il_version_1.Box(x=40, y=100, x2=540, y2=500),
            )
        ],
    )
    ref1 = _para("[1] Vaswani, A. et al. (2017) Attention is all you need", 50, 420, 500, 450, label="reference")
    ref2 = _para("[2] Devlin, J. et al. (2019) BERT pre-training", 50, 380, 500, 410, label="reference")
    body = _para("As discussed above,", 50, 520, 500, 550, label="body_text")

    assert paragraph_semantic_zone(ref1, page) == "bibliography"
    assert can_merge_paragraphs(body, ref1, page=page) is False
    assert can_merge_paragraphs(ref1, ref2, page=page) is False

    # Even without explicit layout label, numeric bracket entries never merge.
    plain1 = _para("End of first entry", 50, 420, 500, 450, label="body_text")
    plain2 = _para("[2] Second bibliography item", 50, 380, 500, 410, label="body_text")
    assert can_merge_paragraphs(plain1, plain2, page=None) is False


def test_table_row_expansion_shifts_sibling_row_and_body_cleanly():
    ts = object.__new__(Typesetting)
    ts.translation_config = SimpleNamespace(lang_out="ar")
    ts._rendered_collision_resolved_pages = set()

    # Row 0 expanded downward so rendered characters reach y=585 (overlapping source row 1 at 580..600)
    r0_c0 = _para(
        "Expanded col 0", 50, 585, 220, 630,
        label="wireless_table_cell",
        trace={"table_id": "t1", "semantic_table": {"row_index": 0, "col_index": 0}},
    )
    r0_c1 = _para(
        "Row 0 col 1", 240, 610, 400, 630,
        label="wireless_table_cell",
        trace={"table_id": "t1", "semantic_table": {"row_index": 0, "col_index": 1}},
    )
    # Row 1 cells initially sit at y=575..598 (colliding with r0_c0 bottom=585)
    r1_c0 = _para(
        "Row 1 col 0", 50, 575, 220, 598,
        label="wireless_table_cell",
        trace={"table_id": "t1", "semantic_table": {"row_index": 1, "col_index": 0}},
    )
    r1_c1 = _para(
        "Row 1 col 1", 240, 575, 400, 598,
        label="wireless_table_cell",
        trace={"table_id": "t1", "semantic_table": {"row_index": 1, "col_index": 1}},
    )
    # Body paragraph below the table (source box was below row 1)
    body_below = _para("Following body section", 50, 540, 400, 568, label="body_text")

    page = il_version_1.Page(
        page_number=0,
        cropbox=il_version_1.Cropbox(box=il_version_1.Box(x=0, y=0, x2=595, y2=842)),
        pdf_paragraph=[r0_c0, r0_c1, r1_c0, r1_c1, body_below],
    )

    ts._resolve_table_row_and_body_clearance(page)

    r1_c0_box = ts._rendered_text_box(r1_c0)
    r1_c1_box = ts._rendered_text_box(r1_c1)
    body_box = ts._rendered_text_box(body_below)

    # Row 1 shifted coherently together below r0_c0's bottom (<= 582)
    assert r1_c0_box.y2 <= 582.5
    assert abs(r1_c0_box.y2 - r1_c1_box.y2) < 0.01
    # Body paragraph pushed below expanded table bottom
    assert body_box.y2 <= r1_c0_box.y - 3.5


def test_un_policy_and_source_gated_literature_terminology():
    assert apply_pre_glossary("Country programme document") == "وثيقة البرنامج القطري"
    assert apply_pre_glossary("Distr.: Limited") == "توزيع: محدود"
    assert apply_pre_glossary("Early Childhood Development") == "تنمية الطفولة المبكرة"

    # Source-gated false-friend fixes trigger ONLY when matching source exists.
    assert apply_post_glossary("تحلل التقاطعات", source_text="Probing interjections in BERT") == "تحلل صيغ التعجب"
    assert apply_post_glossary("تقاطعات الطرق", source_text="Road intersections in traffic") == "تقاطعات الطرق"
    assert apply_post_glossary("تدرس الترجمة", source_text="Models encode syntax trees") == "تدرس البنية النحوية"
    assert apply_post_glossary("جودة الترجمة", source_text="Machine translation quality") == "جودة الترجمة"
