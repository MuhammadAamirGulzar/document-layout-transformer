"""Priority 3 acceptance tests: pagination cohesion, bounded compaction, and metrics."""
from __future__ import annotations

from types import SimpleNamespace

from babeldoc.format.pdf.document_il import (
    Box,
    Cropbox,
    Document,
    Page,
    PdfCharacter,
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
    x: float,
    y: float,
    x2: float,
    y2: float,
    *,
    label: str = "plain text",
    debug_id: int = 1,
    trace: dict | None = None,
) -> PdfParagraph:
    chars = [_glyph(ch, x + i * 6.0, y, h=max(8.0, min(12.0, y2 - y))) for i, ch in enumerate(text)]
    return PdfParagraph(
        debug_id=debug_id,
        box=Box(x, y, x2, y2),
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


def test_minor_overflow_fits_after_bounded_compaction_without_sparse_page():
    ledger = ContentLedger()
    ts = _typesetter(ledger)
    # Footnote occupies 25..45 -> body_floor = 55.
    # Two-line last paragraph sits at y=49..67 (18pt tall, only 6pt below body_floor, and plenty of vertical gap above at 100..650).
    fn = _para("1 Footnote reference", 50, 25, 400, 45, label="footnote", debug_id=1)
    top_p = _para("Top paragraph", 50, 680, 400, 720, debug_id=2)
    mid_p = _para("Middle paragraph", 50, 120, 400, 160, debug_id=3)
    tail_p = _para("Short concluding lines", 50, 49, 300, 67, debug_id=4)
    page = Page(page_number=0, cropbox=Cropbox(box=Box(0, 0, 595, 842)), pdf_paragraph=[top_p, mid_p, tail_p, fn])
    doc = Document(page=[page], total_pages=1)

    created = ts._paginate_overflowing_pages(doc)
    assert created == 0
    assert len(doc.page) == 1
    assert ts._rendered_text_box(tail_p).y >= 54.0
    rep = ledger.build_report()
    assert rep["summary"]["sparse_continuation_pages"] == 0
    assert rep["summary"]["clipped_fragment_count"] == 0


def test_genuine_multiline_overflow_creates_continuation_and_keeps_heading_with_body():
    ledger = ContentLedger()
    ts = _typesetter(ledger)
    fn = _para("1 Source footnote", 50, 28, 350, 46, label="footnote", debug_id=10)
    upper = _para("Earlier body text.", 50, 650, 500, 720, debug_id=11)
    blocker = _para("Existing content in the same lane", 50, 105, 450, 160, debug_id=14)
    heading = _para("2.1 Key Programme Findings", 50, 72, 320, 90, label="heading", debug_id=12)
    # Multi-line body overflowing straight into footnote area (y=15..68)
    lines = [f"Translated sentence row {i}" for i in range(6)]
    body_chars = [
        _glyph(c, 50 + idx * 6.0, 58 - r * 10.0)
        for r, ln in enumerate(lines)
        for idx, c in enumerate(ln)
    ]
    following = PdfParagraph(
        debug_id=13,
        box=Box(50, 8, 450, 68),
        unicode="\n".join(lines),
        layout_label="plain text",
        pipeline_trace={"page_overflow": True},
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=c) for c in body_chars],
    )
    page = Page(page_number=0, cropbox=Cropbox(box=Box(0, 0, 595, 842)), pdf_paragraph=[upper, blocker, heading, following, fn])
    doc = Document(page=[page], total_pages=1)

    created = ts._paginate_overflowing_pages(doc)
    assert created == 1
    assert fn in doc.page[0].pdf_paragraph
    assert upper in doc.page[0].pdf_paragraph
    assert blocker in doc.page[0].pdf_paragraph
    # Heading MUST stay glued with following content on continuation page!
    assert heading in doc.page[1].pdf_paragraph
    assert following in doc.page[1].pdf_paragraph
    assert doc.page[1].pdf_paragraph.index(heading) < doc.page[1].pdf_paragraph.index(following)

    rep = ledger.build_report()
    assert rep["summary"]["detached_group_count"] == 0
    assert rep["summary"]["clipped_fragment_count"] == 0


def test_heading_and_body_use_free_space_in_their_column_before_creating_continuation():
    ledger = ContentLedger()
    ts = _typesetter(ledger)
    footer = _para("Executive summary | 3", 430, 28, 545, 46, label="abandon", debug_id=60)
    upper = _para("Earlier same-column text", 50, 180, 450, 220, debug_id=61)
    heading = _para("Water and sanitation", 50, 72, 300, 90, label="heading", debug_id=62)
    body = _para(
        "Translated body that needs the available lane above",
        50,
        22,
        450,
        68,
        debug_id=63,
        trace={"page_overflow": True},
    )
    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[upper, heading, body, footer],
    )
    doc = Document(page=[page], total_pages=1)

    created = ts._paginate_overflowing_pages(doc)

    assert created == 0
    assert len(doc.page) == 1
    assert heading in doc.page[0].pdf_paragraph and body in doc.page[0].pdf_paragraph
    assert ts._rendered_text_box(body).y >= footer.box.y2 + 6.0
    assert ts._rendered_text_box(heading).y2 < ts._rendered_text_box(upper).y - 3.0
    assert (body.pipeline_trace or {}).get("compacted_on_source_page") is True


def test_independent_two_column_and_fixed_table_plus_body_pagination():
    ledger = ContentLedger()
    ts = _typesetter(ledger)
    col1_ok = _para("Col 1 upper", 45, 500, 260, 540, debug_id=21)
    col1_ov = _para("Col 1 overflowing tail " * 3, 45, -10, 260, 35, debug_id=22, trace={"page_overflow": True})
    col2_ok = _para("Col 2 independent block", 320, 120, 540, 220, debug_id=23)
    fixed_tbl = _para("500.00", 320, 420, 480, 460, label="wired_table_cell_fixed_rtl", debug_id=24, trace={"table_id": "tbl1"})

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[col1_ok, col1_ov, fixed_tbl, col2_ok],
    )
    doc = Document(page=[page], total_pages=1)
    created = ts._paginate_overflowing_pages(doc)
    assert created == 1
    assert fixed_tbl in doc.page[0].pdf_paragraph
    assert col2_ok in doc.page[0].pdf_paragraph
    assert col1_ov in doc.page[1].pdf_paragraph
    # Validate reading order on continuation preserves original page order cleanly
    assert ledger.build_report()["summary"]["detached_group_count"] == 0


def test_cohesive_card_group_moves_completely_when_overflowing():
    ledger = ContentLedger()
    ts = _typesetter(ledger)
    # Entire card (header + line item + total + outer border) drops off page bottom
    card_rect = PdfRectangle(box=Box(45, -25, 420, 65))
    card_hdr = _para("Summary Header", 55, 38, 220, 56, label="heading", debug_id=31)
    card_row = _para("Item A: 420.00", 55, 14, 240, 32, debug_id=32)
    card_tot = _para("Total: 420.00", 55, -12, 220, 6, label="heading", debug_id=33, trace={"page_overflow": True})
    top_body = _para("Main page prose", 50, 600, 500, 720, debug_id=30)

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_rectangle=[card_rect],
        pdf_paragraph=[top_body, card_hdr, card_row, card_tot],
    )
    assign_page_region_groups(page)
    doc = Document(page=[page], total_pages=1)

    created = ts._paginate_overflowing_pages(doc)
    assert created == 1
    # Complete logical group + its border rect move together!
    assert top_body in doc.page[0].pdf_paragraph
    assert all(m in doc.page[1].pdf_paragraph for m in (card_hdr, card_row, card_tot))
    assert card_rect in doc.page[1].pdf_rectangle
    assert ledger.build_report()["summary"]["detached_group_count"] == 0


def test_quality_report_records_sparse_pages_clipped_fragments_and_detached_groups():
    ledger = ContentLedger()
    ts = _typesetter(ledger)
    # Tiny 1-line continuation (sparse!) + a clipped glyph extending beyond crop.x2 = 595
    clipped_p = _para("OFFPAGE_TEXT", 580, 700, 660, 712, debug_id=41)
    tiny_cont = _para("tiny", 50, 780, 80, 792, debug_id=42)
    p0 = Page(page_number=0, cropbox=Cropbox(box=Box(0, 0, 595, 842)), pdf_paragraph=[clipped_p])
    # Even a retained decoration and stale logical paragraph text must not
    # hide a continuation that paints only one short fragment.
    tiny_cont.unicode = "Original unsplit text " * 10
    p1 = Page(page_number=1, continuation_of_page=0, cropbox=Cropbox(box=Box(0, 0, 595, 842)),
              pdf_rectangle=[PdfRectangle(box=Box(120, 780, 135, 795))], pdf_paragraph=[tiny_cont])
    doc = Document(page=[p0, p1], total_pages=2)

    for pg in doc.page:
        ts._report_clipped_content(pg, record_to_ledger=True)
    ts._record_pagination_metrics(doc)
    rep = ledger.build_report()
    assert rep["summary"]["sparse_continuation_pages"] >= 1
    assert rep["summary"]["clipped_fragment_count"] >= 1


def test_long_but_narrow_fragment_on_mostly_blank_continuation_is_sparse():
    ledger = ContentLedger()
    ts = _typesetter(ledger)
    narrow = _para(
        "A long logical paragraph " * 20,
        45,
        610,
        230,
        790,
        debug_id=43,
    )
    page = Page(
        page_number=1,
        continuation_of_page=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[narrow],
    )
    ts._record_pagination_metrics(Document(page=[page], total_pages=1))

    assert ledger.build_report()["summary"]["sparse_continuation_pages"] == 1


def test_filtered_pages_subset_preserves_page_numbers_across_continuations():
    ledger = ContentLedger()
    ts = _typesetter(ledger)
    p13 = Page(page_number=13, cropbox=Cropbox(box=Box(0, 0, 595, 842)), pdf_paragraph=[_para("Page 14 text", 50, 700, 300, 720, debug_id=50)])
    # Page 17 (0-indexed 16) spawns a continuation page while page 18 (0-indexed 17) follows
    ov = _para("Long overflow " * 6, 50, -15, 400, 30, debug_id=51, trace={"page_overflow": True})
    p16 = Page(page_number=16, cropbox=Cropbox(box=Box(0, 0, 595, 842)), pdf_paragraph=[ov])
    p17 = Page(page_number=17, cropbox=Cropbox(box=Box(0, 0, 595, 842)), pdf_paragraph=[_para("Page 18 text", 50, 700, 300, 720, debug_id=52)])
    doc = Document(page=[p13, p16, p17], total_pages=3)

    created = ts._paginate_overflowing_pages(doc)
    assert created == 1
    assert [p.page_number for p in doc.page] == [13, 16, 17, 18]
    assert doc.page[2].continuation_of_page == 16
