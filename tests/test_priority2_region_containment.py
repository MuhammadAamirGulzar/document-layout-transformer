"""Priority 2 acceptance tests: structured-region grouping, containment, and QA reporting."""
from __future__ import annotations

from types import SimpleNamespace

from babeldoc.format.pdf.document_il import (
    Box,
    Cropbox,
    Document,
    Page,
    PageLayout,
    PdfCharacter,
    PdfCurve,
    PdfLine,
    PdfParagraph,
    PdfParagraphComposition,
    PdfRectangle,
    PdfStyle,
    VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
from babeldoc.format.pdf.document_il.utils.layout_semantics import assign_page_region_groups
from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger


def _glyph(ch: str, x: float, y: float, w: float = 6.0, h: float = 10.0) -> PdfCharacter:
    box = Box(x=x, y=y, x2=x + w, y2=y + h)
    return PdfCharacter(
        char_unicode=ch,
        box=box,
        visual_bbox=VisualBbox(box=box),
        pdf_style=PdfStyle(font_id="base", font_size=10.0),
    )


def _para(
    text: str,
    box: Box,
    *,
    label: str = "plain text",
    debug_id: int = 1,
    trace: dict | None = None,
    render_x: float | None = None,
    render_y: float | None = None,
    w_char: float = 6.0,
) -> PdfParagraph:
    rx = box.x if render_x is None else render_x
    ry = box.y if render_y is None else render_y
    chars = [_glyph(ch, rx + i * w_char, ry, w=w_char, h=min(10.0, max(6.0, box.y2 - box.y))) for i, ch in enumerate(text)]
    return PdfParagraph(
        debug_id=debug_id,
        box=Box(box.x, box.y, box.x2, box.y2),
        unicode=text,
        layout_label=label,
        pipeline_trace=dict(trace or {}),
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=PdfLine(pdf_character=chars))],
    )


def _typesetter(ledger: ContentLedger | None = None) -> Typesetting:
    ts = object.__new__(Typesetting)
    ts.is_arabic = True
    ts.translation_config = SimpleNamespace(
        lang_in="en",
        lang_out="ar",
        shared_context_cross_split_part=SimpleNamespace(content_ledger=ledger),
    )
    ts._rendered_collision_resolved_pages = set()
    ts._rtl_mirrored_table_pages = set()
    ts._reported_layout_issue_keys = set()
    ts._layout_issues = []
    return ts


def test_two_column_structured_card_keeps_group_identity_and_column_containment():
    # Outer drawn frame around a two-column header/terms/totals card
    outer_card = PdfRectangle(box=Box(40, 500, 550, 740))
    left_box = PdfRectangle(box=Box(50, 580, 270, 720))
    right_box = PdfRectangle(box=Box(310, 580, 540, 720))
    footer_strip = PdfRectangle(box=Box(50, 510, 540, 565))

    hdr = _para("Meridian Supply Co.", Box(56, 695, 220, 715), label="heading", debug_id=1)
    addr = _para("14 Harbor St, Dock 4", Box(56, 650, 220, 680), debug_id=2)
    meta = _para("Ref: MCA-TS-0417", Box(320, 695, 480, 715), label="heading", debug_id=3)
    terms = _para("Net 30 days", Box(320, 650, 460, 680), debug_id=4)
    total = _para("Grand Total: 1,250.00", Box(320, 520, 530, 545), label="heading", debug_id=5)

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_rectangle=[outer_card, left_box, right_box, footer_strip],
        pdf_paragraph=[hdr, addr, meta, terms, total],
    )
    groups = assign_page_region_groups(page)
    assert groups
    # All items inside outer card belong to a structured group with preserved containment bounds
    for p in (hdr, addr, meta, terms, total):
        assert (p.pipeline_trace or {}).get("region_group_id") is not None

    # Left and right sub-cards keep their own inner permitted bounds and do not cross each other
    ts = _typesetter()
    ts._expand_structured_text_boxes(page)
    assert hdr.box.x >= 50 and hdr.box.x2 <= 270
    assert meta.box.x >= 310 and meta.box.x2 <= 540
    assert ts._calculate_rtl_margin_shift(hdr, page) == 0.0
    assert ts._calculate_rtl_margin_shift(total, page) == 0.0


def test_form_checkboxes_and_radio_buttons_stay_paired_with_labels():
    form_card = PdfRectangle(box=Box(50, 400, 500, 600))
    chk_box = _para("☐", Box(65, 540, 80, 556), debug_id=11)
    chk_lbl = _para("Standard Delivery", Box(86, 540, 210, 556), debug_id=12)
    rad_box = _para("●", Box(65, 505, 80, 521), debug_id=13)
    rad_lbl = _para("Priority Express", Box(86, 505, 200, 521), debug_id=14)

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_rectangle=[form_card],
        pdf_paragraph=[chk_box, chk_lbl, rad_box, rad_lbl],
    )
    assign_page_region_groups(page)
    assert chk_box.pipeline_trace["region_group_id"] == chk_lbl.pipeline_trace["region_group_id"]
    assert rad_box.pipeline_trace["region_group_id"] == rad_lbl.pipeline_trace["region_group_id"]
    assert chk_box.pipeline_trace["region_pair_id"] == chk_lbl.pipeline_trace["region_pair_id"]
    assert rad_box.pipeline_trace["region_pair_id"] == rad_lbl.pipeline_trace["region_pair_id"]

    ts = _typesetter()
    assert ts._calculate_rtl_margin_shift(chk_box, page) == 0.0
    assert ts._calculate_rtl_margin_shift(chk_lbl, page) == 0.0


def test_figure_with_labels_and_caption_stays_grouped_and_reports_escapes():
    ledger = ContentLedger()
    fig_box = Box(80, 350, 380, 560)
    node_box = Box(100, 480, 190, 510)
    # Deliberately overflow node label horizontally out of node_box (100..230 > 190) -> must compact!
    label_a = _para("Long translated diagram node", node_box, label="figure_text", debug_id=21, render_x=100, w_char=5.0)
    # Huge non-fittable label spilling >2.5x outside figure -> cannot compact safely below floor -> records region_escape!
    label_bad = _para("UnfitLabel " * 12, Box(100, 380, 140, 396), label="figure_text", debug_id=22, render_x=100, w_char=6.0)
    caption = _para("Figure 1: Architecture overview", Box(80, 325, 380, 344), label="figure_caption", debug_id=23)

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        page_layout=[PageLayout(class_name="figure", box=fig_box)],
        pdf_rectangle=[PdfRectangle(box=node_box)],
        pdf_paragraph=[label_a, label_bad, caption],
    )
    assign_page_region_groups(page)
    assert label_a.pipeline_trace["region_group_id"] == caption.pipeline_trace["region_group_id"]

    ts = _typesetter(ledger)
    ts._validate_rendered_regions(page)

    # Compactable label_a must fit inside its node box after adaptive compaction
    ra = ts._rendered_text_box(label_a)
    assert ra is not None and ra.x2 <= node_box.x2 + 2.0

    # Unfittable extreme escape must be recorded in quality_report.json!
    rep = ledger.build_report()
    assert any(issue["kind"] == "region_escape" for issue in rep["layout_issues"])


def test_wide_table_with_merged_headers_preserves_cell_bounds():
    ledger = ContentLedger()
    ts = _typesetter(ledger)
    # Row 0: col_span=2 merged header [40, 240] and single col [240, 340]
    h_span = _para("Quarterly Financial Totals", Box(40, 500, 240, 522), label="wired_table_cell_fixed_rtl", debug_id=31, trace={"table_id": "wt1", "col_span": 2})
    h_right = _para("Status Note", Box(240, 500, 310, 522), label="wired_table_cell_fixed_rtl", debug_id=32, render_x=230, w_char=8.5, trace={"table_id": "wt1", "col_span": 1})
    r1_c1 = _para("Item Alpha", Box(40, 475, 140, 497), label="wired_table_cell_fixed_rtl", debug_id=33, trace={"table_id": "wt1"})
    r1_c2 = _para("12,400", Box(140, 475, 240, 497), label="wired_table_cell_fixed_rtl", debug_id=34, trace={"table_id": "wt1"})
    r1_c3 = _para("OK", Box(240, 475, 310, 497), label="wired_table_cell_fixed_rtl", debug_id=35, trace={"table_id": "wt1"})

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[h_span, h_right, r1_c1, r1_c2, r1_c3],
    )
    assign_page_region_groups(page)
    assert len({p.pipeline_trace["region_group_id"] for p in page.pdf_paragraph}) == 1

    ts._audit_table_cells_after_render(page)
    ts._validate_rendered_regions(page)
    rh = ts._rendered_text_box(h_right)
    assert rh is not None
    assert rh.x >= 238.5 and rh.x2 <= 311.5


def test_independent_overflowing_region_does_not_invade_or_move_adjacent_region():
    ts = _typesetter()
    # Left column card overflows; Right column card sits slightly lower in unused vertical space
    left_card = PdfRectangle(box=Box(40, 80, 260, 220))
    right_card = PdfRectangle(box=Box(310, 180, 540, 320))
    left_p = _para("Left overflowing region", Box(50, 45, 250, 200), debug_id=41, trace={"page_overflow": True})
    right_p = _para("Right independent block", Box(320, 210, 530, 290), debug_id=42)

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_rectangle=[left_card, right_card],
        pdf_paragraph=[left_p, right_p],
    )
    assign_page_region_groups(page)
    assert left_p.pipeline_trace["region_group_id"] != right_p.pipeline_trace["region_group_id"]

    # Right block expansion cannot jump across column gutter into Left block space
    ts._expand_structured_text_boxes(page)
    assert right_p.box.x >= 310 and right_p.box.x2 <= 540
    assert left_p.box.x >= 40 and left_p.box.x2 <= 260

    doc = Document(page=[page], total_pages=1)
    left_p.box = Box(50, 45, 250, 200)
    # Remove enclosing left_card for pagination test so left_p moves alone while right_p stays
    page.pdf_rectangle = [right_card]
    created = ts._paginate_overflowing_pages(doc)
    assert created == 1
    assert right_p in doc.page[0].pdf_paragraph
    assert left_p in doc.page[1].pdf_paragraph


def test_negative_control_normal_two_column_prose_stays_clean():
    ledger = ContentLedger()
    ts = _typesetter(ledger)
    # Two-column editorial page without local card borders
    col1_a = _para("First column paragraph one.", Box(50, 620, 270, 680), debug_id=51)
    col1_b = _para("First column paragraph two.", Box(50, 540, 270, 600), debug_id=52)
    col2_a = _para("Second column paragraph one.", Box(315, 620, 540, 680), debug_id=53)
    col2_b = _para("Second column paragraph two.", Box(315, 540, 540, 600), debug_id=54)

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[col1_a, col1_b, col2_a, col2_b],
    )
    assign_page_region_groups(page)
    ts._validate_rendered_regions(page)
    rep = ledger.build_report()
    assert not any(i["kind"] in {"region_escape", "region_collision"} for i in rep["layout_issues"])
    # Unboxed prose columns remain free to stay in independent horizontal lanes (no false card lock)
    assert ts._find_enclosing_region_box(col1_a, page) is None
