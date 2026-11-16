"""P0-C and P0-D tests: table geometry containment/clearance and output-level PDF QA audit."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pymupdf

from babeldoc.format.pdf.document_il import (
    Box,
    Cropbox,
    Page,
    PdfCharacter,
    PdfCurve,
    PdfLine,
    PdfParagraph,
    PdfParagraphComposition,
    PdfStyle,
    VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
from babeldoc.format.pdf.document_il.utils.preflight import audit_output_pdf
from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger


def _glyph(ch: str, x: float, y: float, w: float = 6.0, h: float = 10.0) -> PdfCharacter:
    box = Box(x=x, y=y, x2=x + w, y2=y + h)
    return PdfCharacter(
        char_unicode=ch,
        box=box,
        visual_bbox=VisualBbox(box=box),
        pdf_style=PdfStyle(font_id="base", font_size=10.0),
    )


def _rendered_para(
    text: str,
    src_box: Box,
    render_x: float,
    render_y: float,
    w_per_char: float = 6.0,
    label: str = "plain text",
    trace: dict | None = None,
) -> PdfParagraph:
    chars = [
        _glyph(ch, render_x + i * w_per_char, render_y, w=w_per_char, h=10.0)
        for i, ch in enumerate(text)
    ]
    return PdfParagraph(
        box=Box(src_box.x, src_box.y, src_box.x2, src_box.y2),
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
    return ts


def test_narrow_fixed_cell_compacts_horizontally_instead_of_colliding():
    ledger = ContentLedger()
    ts = _typesetter(ledger)
    # Narrow 30pt cell slot [100, 130], while uncompacted Arabic glyphs span [80, 130] (50pt wide).
    c1 = _glyph("ق", 80, 500, w=8.0)
    c2 = _glyph("م", 88, 500, w=8.0)
    c3 = _glyph("ر", 96, 500, w=8.0)
    c4 = _glyph("ل", 104, 500, w=8.0)
    c5 = _glyph("ا", 112, 500, w=8.0)
    cell_a = PdfParagraph(
        box=Box(100, 495, 130, 515),
        layout_label="wired_table_cell_fixed_rtl",
        pipeline_trace={"table_id": "t1"},
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=c) for c in (c1, c2, c3, c4, c5)],
    )
    cell_b = _rendered_para(
        "25 240",
        Box(40, 495, 95, 515),
        render_x=45,
        render_y=500,
        label="wired_table_cell_fixed_rtl",
        trace={"table_id": "t1"},
    )
    page = Page(cropbox=Cropbox(box=Box(0, 0, 595, 842)), pdf_paragraph=[cell_b, cell_a])

    ts._audit_table_cells_after_render(page)
    rbox_a = ts._rendered_text_box(cell_a)
    assert rbox_a is not None
    assert rbox_a.x >= cell_a.box.x - 1.5
    assert rbox_a.x2 <= cell_a.box.x2 + 1.5
    assert not ledger._layout_issues


def test_stacked_tables_and_fixed_table_barriers_prevent_overlaps():
    ts = _typesetter()
    t1_c1 = _rendered_para(
        "Portvale",
        Box(60, 480, 200, 520),
        render_x=62,
        render_y=455,  # Expanded below source bottom (480) down to 455
        label="wired_table_cell_fixed_rtl",
        trace={"table_id": "table_b10"},
    )
    t2_c1 = _rendered_para(
        "Metric A",
        Box(60, 430, 200, 465),  # Overlaps expanded Table B10 bottom (455)
        render_x=62,
        render_y=450,
        label="wired_table_cell_fixed_rtl",
        trace={"table_id": "table_b11"},
    )
    below_body = _rendered_para(
        "Footnote body",
        Box(60, 390, 260, 420),
        render_x=62,
        render_y=446,  # Overlaps Table B11
        label="plain text",
    )
    grid_line = PdfCurve(box=Box(60, 430, 200, 465))
    page = Page(
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[t1_c1, t2_c1, below_body],
        pdf_curve=[grid_line],
    )

    ts._resolve_table_row_and_body_clearance(page)
    ts._resolve_rendered_text_collisions(page)

    r_t1 = ts._rendered_text_box(t1_c1)
    r_t2 = ts._rendered_text_box(t2_c1)
    r_body = ts._rendered_text_box(below_body)
    assert r_t1 is not None and r_t2 is not None and r_body is not None
    assert r_t2.y2 <= r_t1.y - 4.0
    assert r_body.y2 <= r_t2.y - 3.0


def test_same_row_table_cells_do_not_stack_vertically_in_collision_pass():
    ts = _typesetter()
    left_cell = _rendered_para(
        "ABCDEFGHI",
        Box(60, 500, 110, 520),
        render_x=60,
        render_y=505,
        label="table_cell",
        trace={"table_id": "t_nonfixed"},
    )
    right_cell = _rendered_para(
        "99",
        Box(110, 500, 150, 520),
        render_x=112,
        render_y=505,
        label="table_cell",
        trace={"table_id": "t_nonfixed"},
    )
    page = Page(cropbox=Cropbox(box=Box(0, 0, 595, 842)), pdf_paragraph=[left_cell, right_cell])
    ts._resolve_rendered_text_collisions(page)
    # Neither cell was vertically dropped below its row neighbor.
    assert ts._rendered_text_box(left_cell).y == 505
    assert ts._rendered_text_box(right_cell).y == 505


def test_has_vertical_divider_blocks_asymmetric_column_slide():
    page = Page(pdf_curve=[PdfCurve(box=Box(200, 400, 200.5, 520))])
    c_left = PdfParagraph(box=Box(80, 480, 200, 500))
    c_right = PdfParagraph(box=Box(200, 480, 320, 500))
    assert Typesetting._has_vertical_divider_between(c_left, c_right, page) is True


def test_audit_output_pdf_detects_real_pdf_defects_and_passes_clean_pdf(tmp_path: Path):
    src_pdf = tmp_path / "src.pdf"
    bad_pdf = tmp_path / "bad.pdf"
    good_pdf = tmp_path / "good.pdf"

    d = pymupdf.open()
    p = d.new_page(width=400, height=400)
    p.insert_text((40, 60), "Track ID MCA-TS-0417 total 125.50")
    d.save(src_pdf)
    d.close()

    # Defective PDF: off-page span, overlapping distinct blocks, leaked <b1>, missing MCA-TS-0417
    d = pymupdf.open()
    p = d.new_page(width=400, height=400)
    p.insert_text((350, 60), "OFFPAGEOVERFLOWTOKENTEXTOVER")
    p.insert_text((50, 120), "Block Alpha line one")
    p.insert_text((55, 122), "Block Beta overlapping line")
    p.insert_text((50, 200), "Leaked tag <b1> value 99")
    d.save(bad_pdf)
    d.close()

    bad_qa = audit_output_pdf(bad_pdf, expected_page_count=1, source_pdf_path=src_pdf)
    assert bad_qa["checked"] is True
    assert bad_qa["off_page_spans"]
    assert bad_qa["overlap_collisions"]
    assert bad_qa["leaked_placeholder_pages"] == [0]
    assert any(tok["token"] == "MCA-TS-0417" for tok in bad_qa["missing_immutable_tokens"])
    assert bad_qa["total_qa_issues"] >= 4

    # Clean PDF preserving all immutable tokens cleanly inside page bounds with no overlaps
    d = pymupdf.open()
    p = d.new_page(width=400, height=400)
    p.insert_text((50, 80), "ID MCA-TS-0417")
    p.insert_text((50, 150), "Total 125.50")
    d.save(good_pdf)
    d.close()

    good_qa = audit_output_pdf(good_pdf, expected_page_count=1, source_pdf_path=src_pdf)
    assert good_qa["checked"] is True
    assert good_qa["total_qa_issues"] == 0
