"""Milestone 2 acceptance tests: dense, multi-section staggered, and booktabs table layout."""
from __future__ import annotations

from types import SimpleNamespace

from babeldoc.format.pdf.document_il.il_version_1 import (
    Box,
    Cropbox,
    Page,
    PageLayout,
    PdfCharacter,
    PdfCurve,
    PdfLine,
    PdfParagraph,
    PdfParagraphComposition,
    PdfStyle,
    VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
from babeldoc.format.pdf.document_il.utils.semantic_table import annotate_page_semantic_tables
from babeldoc.format.pdf.translation_config import (
    SharedContextCrossSplitPart,
    TranslationConfig,
    WatermarkOutputMode,
)


def _glyph(ch: str, x: float, y: float, w: float = 5.5, h: float = 10.0) -> PdfCharacter:
    box = Box(x=x, y=y, x2=x + w, y2=y + h)
    return PdfCharacter(
        char_unicode=ch,
        box=box,
        visual_bbox=VisualBbox(box=box),
        pdf_style=PdfStyle(font_id="F1", font_size=9.5),
    )


def _make_rendered_cell(
    text: str,
    src_box: Box,
    rx0: float,
    ry0: float,
    rx2: float | None = None,
    ry2: float | None = None,
    *,
    debug_id: int = 1,
    label: str = "table_cell",
) -> PdfParagraph:
    chars = [_glyph(ch, rx0 + i * 5.0, ry0) for i, ch in enumerate(text[:6])]
    if rx2 is not None or ry2 is not None:
        bx2 = rx2 if rx2 is not None else rx0 + max(10.0, len(text[:6]) * 5.0)
        by2 = ry2 if ry2 is not None else ry0 + 10.0
        chars[0].box = Box(rx0, ry0, min(bx2, rx0 + 6.0), by2)
        chars[-1].box = Box(max(rx0, bx2 - 6.0), ry0, bx2, by2)
    return PdfParagraph(
        debug_id=debug_id,
        box=Box(src_box.x, src_box.y, src_box.x2, src_box.y2),
        xobj_id=-1,
        unicode=text,
        layout_label=label,
        pdf_style=PdfStyle(font_id="F1", font_size=9.5),
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=PdfLine(pdf_character=chars))],
    )


def _make_typesetter(ledger: ContentLedger | None = None) -> Typesetting:
    ts = object.__new__(Typesetting)
    ts.is_arabic = True
    ts.translation_config = SimpleNamespace(
        lang_in="en",
        lang_out="ar",
        shared_context_cross_split_part=SimpleNamespace(content_ledger=ledger),
    )
    ts._rendered_collision_resolved_pages = set()
    ts._vertical_collision_resolved_pages = set()
    ts._footnote_zone_stabilized_pages = set()
    ts._rtl_mirrored_table_pages = set()
    return ts


def test_multi_section_staggered_table_is_coherent_and_pushes_source_note():
    """Vertically stacked sub-tables with distinct staggered column grids (5 cols /
    6 cols / 4 cols) must partition into coherent sections without ambiguous_slot_assignment,
    link headers per section, and clear below-table body notes when rendered taller."""
    cells: list[PdfParagraph] = []
    did = 1

    # Section 0: 5 columns (y=500..540)
    sec0_cols = [(60, 130), (150, 220), (240, 310), (330, 410), (430, 540)]
    for c_idx, (x0, x2) in enumerate(sec0_cols):
        cells.append(
            _make_rendered_cell(
                f"S0_H{c_idx}", Box(x0, 525, x2, 540), x0, 522, x2, 540, debug_id=did
            )
        )
        did += 1
        val = "Panel A" if c_idx == 0 else str(10 + c_idx)
        cells.append(
            _make_rendered_cell(
                val, Box(x0, 505, x2, 518), x0, 502, x2, 518, debug_id=did
            )
        )
        did += 1

    # Section 1: 6 staggered columns (different boundaries from section 0!) (y=455..495)
    sec1_cols = [(60, 125), (140, 200), (210, 275), (285, 355), (365, 445), (460, 540)]
    for c_idx, (x0, x2) in enumerate(sec1_cols):
        # Expand header height downwards by 8pt to trigger row expansion
        cells.append(
            _make_rendered_cell(
                f"S1_H{c_idx}", Box(x0, 480, x2, 495), x0, 470, x2, 495, debug_id=did
            )
        )
        did += 1
        val = "Panel B" if c_idx == 0 else str(20 + c_idx)
        cells.append(
            _make_rendered_cell(
                val, Box(x0, 460, x2, 473), x0, 460, x2, 473, debug_id=did
            )
        )
        did += 1

    # Section 2: 4 wide columns (y=410..450)
    sec2_cols = [(60, 140), (165, 290), (315, 425), (450, 540)]
    for c_idx, (x0, x2) in enumerate(sec2_cols):
        cells.append(
            _make_rendered_cell(
                f"S2_H{c_idx}", Box(x0, 435, x2, 450), x0, 430, x2, 450, debug_id=did
            )
        )
        did += 1
        val = "Panel C" if c_idx == 0 else str(30 + c_idx)
        # Render wrapped bottom row overflowing downward into source note space (402..422)
        cells.append(
            _make_rendered_cell(
                val, Box(x0, 415, x2, 428), x0, 402, x2, 428, debug_id=did
            )
        )
        did += 1

    source_note = _make_rendered_cell(
        "Source: Statistical division survey.",
        Box(60, 396, 380, 408),
        60,
        396,
        380,
        408,
        debug_id=999,
        label="plain text",
    )

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        page_layout=[PageLayout(class_name="table", box=Box(55, 410, 545, 545))],
        pdf_paragraph=[*cells, source_note],
    )

    tables = annotate_page_semantic_tables(page)
    assert len(tables) == 1
    assert tables[0].is_coherent is True
    assert tables[0].validation_issues == []
    assert not any((c.pipeline_trace or {}).get("preserve_irregular_table") for c in cells)

    # Headers in section 1 link to their own section 1 rows, not section 0 headers
    sec1_data_cell = [c for c in cells if c.unicode == "21"][0]
    assert sec1_data_cell.pipeline_trace["semantic_table"]["section_index"] == 1
    assert any("r2c" in hid for hid in sec1_data_cell.pipeline_trace["header_cell_ids"])

    ts = _make_typesetter()
    ts._mirror_table_grids_for_rtl(page)
    ts._resolve_table_row_and_body_clearance(page)

    table_bottom = min(ts._rendered_text_box(c).y for c in cells)
    note_box = ts._rendered_text_box(source_note)
    assert note_box.y2 <= table_bottom - 3.0


def test_unruled_table_splits_wide_gutter_collapsed_cells_not_justified_prose():
    """Unruled/booktabs table rows with wide column gutters (> 12pt) split cleanly into
    independent cells before numeric preservation, leaving narrow-spaced source notes intact."""
    cfg = TranslationConfig(
        translator=None,
        input_file="",
        lang_in="en",
        lang_out="ar",
        doc_layout_model=None,
        skip_clean=True,
        watermark_output_mode=WatermarkOutputMode.NoWatermark,
    )
    finder = ParagraphFinder(cfg)
    style = PdfStyle(font_id="F1", font_size=9.5)

    def _make_gapped_line(segments: list[tuple[str, float]], y: float) -> PdfParagraph:
        chars: list[PdfCharacter] = []
        for text, start_x in segments:
            cur_x = start_x
            for ch in text:
                w = 2.6 if ch == " " else 4.8
                cbox = Box(cur_x, y, cur_x + w, y + 9.5)
                chars.append(
                    PdfCharacter(
                        char_unicode=ch,
                        box=cbox,
                        visual_bbox=VisualBbox(box=cbox),
                        pdf_style=style,
                    )
                )
                cur_x += w
        pbox = Box(min(c.box.x for c in chars), y, max(c.box.x2 for c in chars), y + 9.5)
        full_txt = " ".join(seg[0] for seg in segments)
        return PdfParagraph(
            box=pbox,
            unicode=full_txt,
            layout_label="plain text",
            pdf_style=style,
            pdf_paragraph_composition=[
                PdfParagraphComposition(pdf_line=PdfLine(box=pbox, pdf_character=chars))
            ],
        )

    # Collapsed total row (label + 3 distant numbers) and header row (3 short cells)
    row_totals = _make_gapped_line([("All host entities", 120.0), ("148", 285.0), ("100", 343.0), ("224", 420.0)], 400.0)
    row_headers = _make_gapped_line([("Frameworks", 60.0), ("Sendai Path", 150.0), ("Samoa Track", 240.0)], 440.0)
    # Normal justified prose note inside table box (2.8pt word gaps only)
    note_para = _make_gapped_line(
        [("Source: Office of Intergovernmental Support and Coordination for Development.", 60.0)],
        380.0,
    )

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        page_layout=[PageLayout(class_name="table", box=Box(55, 375, 550, 460))],
        pdf_curve=[
            PdfCurve(box=Box(55, 456, 550, 457)),
            PdfCurve(box=Box(55, 396, 550, 397)),
        ],
        pdf_paragraph=[row_headers, row_totals, note_para],
        pdf_character=[],
    )

    finder.reconstruct_wired_table_cells(page, page.pdf_paragraph)

    texts = [p.unicode for p in page.pdf_paragraph]
    assert "All host entities" in texts
    assert "Frameworks" in texts
    assert "Sendai Path" in texts
    assert "Samoa Track" in texts
    assert "Source: Office of Intergovernmental Support and Coordination for Development." in texts
    # 148, 100, 224 were cleanly separated out and preserved as table numeric glyphs
    preserved_nums = "".join(ch.char_unicode for ch in page.pdf_character)
    assert "148" in preserved_nums
    assert "100" in preserved_nums
    assert "224" in preserved_nums


def test_booktabs_anchored_numeric_rows_shift_with_expanded_labels_and_clear_footer():
    """When booktabs headers or row labels expand vertically downward, frozen numeric
    glyphs in page.pdf_character must move down in lockstep with their owning row,
    headers use gutter slack, and below-table notes clear the true table bottom."""
    ledger = ContentLedger()
    ts = _make_typesetter(ledger)

    # Row 0: Headers (y=540..555, rendered taller downward to y=522..555)
    hdr0 = _make_rendered_cell("Group", Box(120, 540, 200, 555), 120, 540, 200, 555, debug_id=1)
    hdr1 = _make_rendered_cell("Number", Box(288, 540, 304, 555), 278, 522, 312, 555, debug_id=2)
    hdr2 = _make_rendered_cell("Share", Box(342, 540, 360, 555), 342, 540, 360, 555, debug_id=3)

    # Row 1: Labels (y=520..532) + frozen glyphs ("46", "52.9") at y=520..530
    r1_lbl = _make_rendered_cell("Least developed", Box(120, 520, 220, 532), 120, 518, 220, 532, debug_id=4)
    # Row 2: Labels (y=502..514) + frozen glyphs ("38", "3.9") at y=502..512
    r2_lbl = _make_rendered_cell("Small island", Box(120, 502, 220, 514), 120, 502, 220, 514, debug_id=5)

    r1_nums = [_glyph(c, 290 + i * 5.0, 520) for i, c in enumerate("46")] + [
        _glyph(c, 344 + i * 5.0, 520) for i, c in enumerate("52.9")
    ]
    r2_nums = [_glyph(c, 290 + i * 5.0, 502) for i, c in enumerate("38")] + [
        _glyph(c, 344 + i * 5.0, 502) for i, c in enumerate("3.9")
    ]

    footer_note = _make_rendered_cell(
        "Source: UN DESA.",
        Box(120, 482, 250, 494),
        120,
        482,
        250,
        494,
        debug_id=99,
        label="plain text",
    )

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        page_layout=[PageLayout(class_name="table", box=Box(115, 498, 400, 560))],
        pdf_paragraph=[hdr0, hdr1, hdr2, r1_lbl, r2_lbl, footer_note],
        pdf_character=[*r1_nums, *r2_nums],
    )

    ts._ensure_semantic_table_models(page)
    # Pre-render collision pass must not drift row labels independently from frozen numbers
    ts._resolve_vertical_paragraph_collisions(page)
    ts._balance_table_row_widths(page, {})
    assert hdr1.box.x < 288.0  # claimed left gutter slack
    assert hdr1.box.x2 > 304.0  # claimed right gutter slack

    ts._resolve_rendered_text_collisions(page)
    ts._resolve_table_row_and_body_clearance(page)
    ts._audit_table_cells_after_render(page)

    # Row 1 and Row 2 frozen numbers shifted down cleanly together with their row labels
    r1_lbl_cy = (r1_lbl.box.y + r1_lbl.box.y2) * 0.5
    r1_num_cy = (r1_nums[0].box.y + r1_nums[0].box.y2) * 0.5
    r2_lbl_cy = (r2_lbl.box.y + r2_lbl.box.y2) * 0.5
    r2_num_cy = (r2_nums[0].box.y + r2_nums[0].box.y2) * 0.5
    assert abs(r1_lbl_cy - r1_num_cy) <= 6.0
    assert abs(r2_lbl_cy - r2_num_cy) <= 6.0
    # Footer note pushed safely below Row 2
    assert ts._rendered_text_box(footer_note).y2 <= r2_nums[0].box.y - 2.0


def test_intra_slot_stacked_subcells_separate_cleanly_without_collision():
    """Two sub-cells stacked into the same (row_index, col_index) slot in a dense table
    must separate vertically instead of colliding."""
    ledger = ContentLedger()
    ts = _make_typesetter(ledger)

    hdr_a = _make_rendered_cell("Category", Box(60, 520, 180, 532), 60, 520, 180, 532, debug_id=1)
    hdr_b = _make_rendered_cell("Amount", Box(220, 520, 260, 532), 220, 520, 260, 532, debug_id=2)
    r0_a = _make_rendered_cell("First group", Box(60, 504, 180, 514), 60, 504, 180, 514, debug_id=3)
    r0_b = _make_rendered_cell("10", Box(220, 504, 260, 514), 220, 504, 260, 514, debug_id=4)
    # Row 1 col 0 contains two stacked fragments in the same row slot that overlap after rendering
    sub_a = _make_rendered_cell("Upper item", Box(60, 492, 180, 500), 60, 484, 180, 500, debug_id=10)
    sub_b = _make_rendered_cell("Lower item", Box(60, 484, 180, 492), 60, 482, 180, 492, debug_id=11)
    sib = _make_rendered_cell("42", Box(220, 486, 260, 498), 220, 486, 260, 498, debug_id=12)
    next_row = _make_rendered_cell("Next row", Box(60, 466, 180, 476), 60, 466, 180, 476, debug_id=13)
    next_val = _make_rendered_cell("99", Box(220, 466, 260, 476), 220, 466, 260, 476, debug_id=14)

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        page_layout=[PageLayout(class_name="table", box=Box(55, 460, 280, 540))],
        pdf_paragraph=[hdr_a, hdr_b, r0_a, r0_b, sub_a, sub_b, sib, next_row, next_val],
    )

    ts._ensure_semantic_table_models(page)
    ts._resolve_table_row_and_body_clearance(page)
    ts._audit_table_cells_after_render(page)

    box_a = ts._rendered_text_box(sub_a)
    box_b = ts._rendered_text_box(sub_b)
    box_next = ts._rendered_text_box(next_row)
    assert box_b.y2 <= box_a.y + 0.5
    assert box_next.y2 <= box_b.y - 2.0
    issues = ledger.build_report()["layout_issues"]
    assert not any(it["kind"] == "table_cell_collision" for it in issues)


def test_genuinely_irregular_overlapping_table_remains_flagged_for_review():
    """Tables whose cells genuinely intersect in 2D must still be marked irregular."""
    c1 = _make_rendered_cell("Chaos A", Box(60, 480, 200, 520), 60, 480, 200, 520, debug_id=21)
    c2 = _make_rendered_cell("Chaos B", Box(80, 485, 210, 515), 80, 485, 210, 515, debug_id=22)
    c3 = _make_rendered_cell("Chaos C", Box(90, 482, 220, 518), 90, 482, 220, 518, debug_id=23)

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        page_layout=[PageLayout(class_name="table", box=Box(55, 470, 250, 530))],
        pdf_paragraph=[c1, c2, c3],
    )
    tables = annotate_page_semantic_tables(page)
    assert len(tables) == 1
    assert tables[0].is_coherent is False
    assert tables[0].layout_kind == "irregular"
    assert c1.pipeline_trace.get("preserve_irregular_table") is True
