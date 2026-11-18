"""P0-2: Canonical semantic table model, span/header links, RTL span safety, and irregular preservation."""
from __future__ import annotations

from babeldoc.format.pdf.document_il.il_version_1 import (
    BaseOperations,
    Box,
    Cropbox,
    Mediabox,
    Page,
    PageLayout,
    PdfCharacter,
    PdfCurve,
    PdfFont,
    PdfLine,
    PdfParagraph,
    PdfParagraphComposition,
    PdfSameStyleUnicodeCharacters,
    PdfStyle,
    VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
from babeldoc.format.pdf.translation_config import (
    SharedContextCrossSplitPart,
    TranslationConfig,
    WatermarkOutputMode,
)


def _make_config() -> TranslationConfig:
    cfg = TranslationConfig(
        translator=None,
        input_file="",
        lang_in="en",
        lang_out="ar",
        doc_layout_model=None,
        skip_clean=True,
        watermark_output_mode=WatermarkOutputMode.NoWatermark,
    )
    shared = SharedContextCrossSplitPart()
    shared.content_ledger = ContentLedger()
    cfg.shared_context_cross_split_part = shared
    return cfg


def _make_source_line(text: str, x: float, y: float, w_per_char: float = 4.0) -> PdfParagraph:
    style = PdfStyle(font_id="F1", font_size=9.0)
    chars: list[PdfCharacter] = []
    for idx, ch in enumerate(text):
        cbox = Box(x + idx * w_per_char, y, x + (idx + 1) * w_per_char, y + 9.0)
        chars.append(
            PdfCharacter(
                char_unicode=ch,
                box=cbox,
                visual_bbox=VisualBbox(box=cbox),
                pdf_style=style,
            )
        )
    lbox = Box(x, y, x + max(4.0, len(text) * w_per_char), y + 9.0)
    return PdfParagraph(
        box=lbox,
        unicode=text,
        layout_label="fallback_line",
        pdf_style=style,
        pdf_paragraph_composition=[
            PdfParagraphComposition(pdf_line=PdfLine(box=lbox, pdf_character=chars))
        ],
    )


def _make_cell_para(
    text: str,
    box: Box,
    *,
    debug_id: int,
    layout_label: str = "table_cell",
) -> PdfParagraph:
    style = PdfStyle(font_id="F1", font_size=9.5)
    return PdfParagraph(
        debug_id=debug_id,
        box=box,
        xobj_id=-1,
        unicode=text,
        layout_label=layout_label,
        pdf_style=style,
        pdf_paragraph_composition=[
            PdfParagraphComposition(
                pdf_same_style_unicode_characters=PdfSameStyleUnicodeCharacters(
                    unicode=text,
                    pdf_style=style,
                )
            )
        ],
    )


def _wrap_page(paragraphs: list[PdfParagraph], *, curves=None, layouts=None) -> Page:
    return Page(
        mediabox=Mediabox(box=Box(0.0, 0.0, 595.0, 842.0)),
        cropbox=Cropbox(box=Box(0.0, 0.0, 595.0, 842.0)),
        pdf_font=[PdfFont(font_id="F1", name="Helvetica", xref_id=1)],
        pdf_paragraph=paragraphs,
        pdf_curve=list(curves or []),
        page_layout=list(layouts or []),
        base_operations=BaseOperations(value=""),
        page_number=0,
        unit="pt",
    )


def test_wired_table_reconstruction_attaches_semantic_ids_spans_and_headers():
    """Wired table reconstruction must attach canonical IDs, spans, header links, and coherence."""
    cfg = _make_config()
    finder = ParagraphFinder(cfg)

    # 3-column x 3-row ruled table [50..350] x [100..220]:
    # - Top row [180..220]: col 0 merged "Category" [50..150], cols 1..2 merged "Budget" [150..350]
    # - Middle row [140..180]: col 0 "Staff" [50..150], col 1 "Q1" [150..250], col 2 "Q2" [250..350]
    # - Bottom row [100..140]: col 0 "Salaries" [50..150], col 1 "$1,250.00" [150..250], col 2 "94.5%" [250..350]
    table_box = Box(50.0, 100.0, 350.0, 220.0)
    curves = [
        # Outer left & right vertical rules, plus col 1 separator at 150
        PdfCurve(box=Box(49.8, 100.0, 50.2, 220.0)),
        PdfCurve(box=Box(149.8, 100.0, 150.2, 220.0)),
        PdfCurve(box=Box(349.8, 100.0, 350.2, 220.0)),
        # Inner col 2 separator at 250 starts below top merged header (only up to y=180)
        PdfCurve(box=Box(249.8, 100.0, 250.2, 180.0)),
        # Horizontal row rules
        PdfCurve(box=Box(50.0, 99.8, 350.0, 100.2)),
        PdfCurve(box=Box(50.0, 139.8, 350.0, 140.2)),
        PdfCurve(box=Box(50.0, 179.8, 350.0, 180.2)),
        PdfCurve(box=Box(50.0, 219.8, 350.0, 220.2)),
    ]
    paragraphs = [
        _make_source_line("Category", 75.0, 195.0),
        _make_source_line("Budget", 225.0, 195.0),
        _make_source_line("Staff", 75.0, 155.0),
        _make_source_line("Q1", 190.0, 155.0),
        _make_source_line("Q2", 290.0, 155.0),
        _make_source_line("Salaries", 75.0, 115.0),
        _make_source_line("$1,250.00", 170.0, 115.0),
        _make_source_line("94.5%", 280.0, 115.0),
    ]
    page = _wrap_page(
        paragraphs,
        curves=curves,
        layouts=[PageLayout(id=1, class_name="table", box=table_box, conf=0.98)],
    )

    finder.reconstruct_wired_table_cells(page, paragraphs)

    by_text = {p.unicode.strip(): p for p in paragraphs}
    budget_trace = by_text["Budget"].pipeline_trace["semantic_table"]
    assert budget_trace["table_coherent"] is True
    assert budget_trace["row_index"] == 0
    assert budget_trace["col_index"] == 1
    assert budget_trace["col_span"] == 2
    assert budget_trace["is_header"] is True

    q1_trace = by_text["Q1"].pipeline_trace["semantic_table"]
    assert q1_trace["row_index"] == 1
    assert q1_trace["col_index"] == 1
    assert q1_trace["col_span"] == 1
    assert q1_trace["is_header"] is True
    assert budget_trace["cell_id"] in q1_trace["header_cell_ids"]

    q2_trace = by_text["Q2"].pipeline_trace["semantic_table"]
    assert budget_trace["cell_id"] in q2_trace["header_cell_ids"]

    # Check preserved numeric cell or cell paragraph inside ledger & metadata
    report = cfg.shared_context_cross_split_part.content_ledger.build_report()
    assert report["status"] == "clean"
    assert report["summary"]["table_count"] == 1
    assert report["summary"]["irregular_table_count"] == 0
    assert report["tables"][0]["protected_numeric_count"] >= 2


def test_rtl_mirroring_and_row_height_sync_preserve_spans_and_header_links():
    """RTL mirroring and row synchronization must preserve row_span heights and semantic header IDs."""
    cfg = _make_config()
    ts = Typesetting(cfg)

    # 2 cols x 2 data rows + 1 header row:
    # Header row: "B" [100..250, 260..280], "V" [250..400, 260..280]
    # Left label spans BOTH data rows vertically (`row_span == 2`): [100..250, 180..255]
    # Right cells occupy two separate rows: [250..400, 220..255] and [250..400, 180..215]
    hdr_col0 = _make_cell_para("بند", Box(100.0, 260.0, 250.0, 280.0), debug_id=1, layout_label="table_cell_header")
    hdr_col1 = _make_cell_para("القيمة", Box(250.0, 260.0, 400.0, 280.0), debug_id=2, layout_label="table_cell_header")
    spanned_lbl = _make_cell_para("الإجمالي", Box(100.0, 180.0, 250.0, 255.0), debug_id=3, layout_label="table_cell")
    val_row1 = _make_cell_para("125.00", Box(250.0, 220.0, 400.0, 255.0), debug_id=4, layout_label="table_cell")
    val_row2 = _make_cell_para("250.00", Box(250.0, 180.0, 400.0, 215.0), debug_id=5, layout_label="table_cell")

    table_box = Box(100.0, 180.0, 400.0, 280.0)
    page = _wrap_page(
        [hdr_col0, hdr_col1, spanned_lbl, val_row1, val_row2],
        layouts=[PageLayout(id=1, class_name="table", box=table_box, conf=0.95)],
    )

    ts._mirror_table_grids_for_rtl(page)
    ts.render_page(page)

    spanned_trace = spanned_lbl.pipeline_trace["semantic_table"]
    assert spanned_trace["row_span"] == 2
    # Spanned cell must NOT be squashed to one sub-row's height (35pt); it spans [180..255]
    assert spanned_lbl.box.y <= 180.1
    assert spanned_lbl.box.y2 >= 254.9

    # RTL mirroring swapped X visually, while preserving header association to logical column headers
    assert hdr_col0.box.x > hdr_col1.box.x
    assert val_row1.pipeline_trace["semantic_table"]["header_cell_ids"] == [hdr_col1.pipeline_trace["cell_id"]]
    assert val_row2.pipeline_trace["semantic_table"]["header_cell_ids"] == [hdr_col1.pipeline_trace["cell_id"]]


def test_irregular_unreconstructable_table_is_preserved_and_reported():
    """Broken/overlapping table fragments must be preserved safely and flagged in ContentLedger."""
    cfg = _make_config()
    ts = Typesetting(cfg)

    # Deliberately colliding/overlapping fragments inside one table region (>50% box overlaps)
    f1 = _make_cell_para("100.00", Box(100.0, 300.0, 250.0, 340.0), debug_id=11)
    f2 = _make_cell_para("200.00", Box(110.0, 305.0, 260.0, 345.0), debug_id=12)
    f3 = _make_cell_para("Item A", Box(120.0, 310.0, 270.0, 350.0), debug_id=13)
    table_box = Box(95.0, 295.0, 280.0, 355.0)
    page = _wrap_page(
        [f1, f2, f3],
        layouts=[PageLayout(id=2, class_name="table", box=table_box, conf=0.60)],
    )
    orig_xs = [(p.box.x, p.box.x2) for p in (f1, f2, f3)]

    ts._mirror_table_grids_for_rtl(page)
    ts.render_page(page)

    # Original geometry preserved (NOT mirrored/reordered destructively!)
    assert [(p.box.x, p.box.x2) for p in (f1, f2, f3)] == orig_xs
    assert all((p.pipeline_trace or {}).get("preserve_irregular_table") is True for p in (f1, f2, f3))

    report = cfg.shared_context_cross_split_part.content_ledger.build_report()
    assert report["status"] == "needs_review"
    assert report["summary"]["irregular_table_count"] == 1
    assert any(issue["kind"] == "irregular_table_preserved" for issue in report["layout_issues"])


def test_fixed_table_cell_floor_and_overflow_collision_audit():
    """Fixed ruled cells must not expand downward across empty rows and collisions are audited."""
    cfg = _make_config()
    ts = Typesetting(cfg)

    fixed_cell = _make_cell_para(
        "نص طويل داخل خلية ضيقة",
        Box(50.0, 400.0, 110.0, 420.0),
        debug_id=21,
        layout_label="wired_table_cell_fixed_rtl",
    )
    # Empty space below at y=[300..400] — fixed cell floor must stay >= 400.0!
    page = _wrap_page([fixed_cell])
    assert ts.get_max_bottom_space(fixed_cell.box, page, paragraph=fixed_cell) >= fixed_cell.box.y

    # Simulate rendered glyphs that overflow outside the fixed cell box and collide with sibling
    sibling_cell = _make_cell_para(
        "أ",
        Box(115.0, 400.0, 180.0, 420.0),
        debug_id=22,
        layout_label="wired_table_cell_fixed_rtl",
    )
    fixed_cell.pdf_paragraph_composition = [
        PdfParagraphComposition(
            pdf_character=PdfCharacter(
                char_unicode="ن",
                box=Box(105.0, 392.0, 130.0, 415.0),  # escapes right edge (110) and overlaps sibling (115)
            )
        )
    ]
    sibling_cell.pdf_paragraph_composition = [
        PdfParagraphComposition(
            pdf_character=PdfCharacter(
                char_unicode="أ",
                box=Box(120.0, 402.0, 140.0, 418.0),
            )
        )
    ]
    page2 = _wrap_page([fixed_cell, sibling_cell])
    ts._ensure_semantic_table_models(page2)
    ts._audit_table_cells_after_render(page2)

    trace = fixed_cell.pipeline_trace or {}
    assert trace.get("table_cell_overflow_detected") is True
    assert trace.get("table_cell_overflow_resolved") is True
    assert not trace.get("table_cell_overflow")
    assert not trace.get("table_cell_collision")
    report = cfg.shared_context_cross_split_part.content_ledger.build_report()
    assert report["status"] == "clean"
    assert report["summary"]["table_cell_overflow_count"] == 0
