"""Release-gate regressions for failures found in rendered real-PDF probes."""
from __future__ import annotations

from types import SimpleNamespace

from babeldoc.format.pdf.document_il import Box
from babeldoc.format.pdf.document_il import Cropbox
from babeldoc.format.pdf.document_il import Page
from babeldoc.format.pdf.document_il import PageLayout
from babeldoc.format.pdf.document_il import PdfCharacter
from babeldoc.format.pdf.document_il import PdfCurve
from babeldoc.format.pdf.document_il import PdfLine
from babeldoc.format.pdf.document_il import PdfParagraph
from babeldoc.format.pdf.document_il import PdfParagraphComposition
from babeldoc.format.pdf.document_il import PdfStyle
from babeldoc.format.pdf.document_il import VisualBbox
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting


def _glyph(ch: str, x: float, y: float, *, w: float = 6.0, h: float = 10.0) -> PdfCharacter:
    box = Box(x, y, x + w, y + h)
    return PdfCharacter(
        char_unicode=ch,
        box=box,
        visual_bbox=VisualBbox(box=Box(box.x, box.y, box.x2, box.y2)),
        pdf_style=PdfStyle(font_id="base", font_size=10.0),
    )


def _para(
    text: str,
    box: Box,
    *,
    debug_id: int,
    label: str = "plain text",
    render_box: Box | None = None,
    trace: dict | None = None,
    xobj_id: int | None = None,
) -> PdfParagraph:
    rendered = render_box or box
    step = max(1.0, (rendered.x2 - rendered.x) / max(1, len(text)))
    chars = [
        _glyph(ch, rendered.x + idx * step, rendered.y, w=step, h=rendered.y2 - rendered.y)
        for idx, ch in enumerate(text)
    ]
    return PdfParagraph(
        debug_id=debug_id,
        box=Box(box.x, box.y, box.x2, box.y2),
        unicode=text,
        layout_label=label,
        xobj_id=xobj_id,
        pipeline_trace=dict(trace or {}),
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=PdfLine(pdf_character=chars))],
    )


def _typesetter() -> Typesetting:
    ts = object.__new__(Typesetting)
    ts.is_arabic = True
    ts.translation_config = SimpleNamespace(
        lang_in="en",
        lang_out="ar",
        shared_context_cross_split_part=SimpleNamespace(content_ledger=None),
    )
    ts._rendered_collision_resolved_pages = set()
    ts._rtl_mirrored_table_pages = set()
    ts._reported_layout_issue_keys = set()
    ts._layout_issues = []
    return ts


def _overlap(a: Box, b: Box) -> tuple[float, float]:
    return (
        max(0.0, min(a.x2, b.x2) - max(a.x, b.x)),
        max(0.0, min(a.y2, b.y2) - max(a.y, b.y)),
    )


def test_bottom_footer_items_are_restored_to_their_own_lanes_across_xobjects():
    ts = _typesetter()
    left = _para(
        "24-23109",
        Box(45, 18, 125, 32),
        render_box=Box(420, 18, 500, 32),
        label="abandon",
        debug_id=1,
        xobj_id=1,
        trace={"page_overflow": True},
    )
    right = _para(
        "2/19",
        Box(485, 18, 545, 32),
        render_box=Box(450, 18, 510, 32),
        label="page_number",
        debug_id=2,
        xobj_id=2,
    )
    page = Page(
        page_number=1,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[left, right],
    )

    ts._contain_bottom_fixed_lanes(page)

    left_rendered = ts._rendered_text_box(left)
    right_rendered = ts._rendered_text_box(right)
    assert left_rendered is not None and right_rendered is not None
    assert left_rendered.x >= left.box.x - 1.0 and left_rendered.x2 <= left.box.x2 + 1.0
    assert right_rendered.x >= right.box.x - 1.0 and right_rendered.x2 <= right.box.x2 + 1.0
    assert _overlap(left_rendered, right_rendered)[0] == 0.0
    assert ts._is_bottom_fixed_paragraph(left, page)


def test_rtl_heading_is_shifted_away_from_a_neighboring_vector_icon():
    ts = _typesetter()
    heading = _para(
        "عنوان عربي طويل",
        Box(155, 500, 350, 525),
        render_box=Box(112, 500, 340, 525),
        label="heading",
        debug_id=3,
        trace={"column_lane": [80.0, 360.0]},
    )
    icon = PdfCurve(box=Box(95, 497, 142, 530), fill_background=True)
    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[heading],
        pdf_curve=[icon],
        page_layout=[PageLayout(class_name="figure", box=Box(70, 475, 365, 550))],
    )

    ts._avoid_decorative_shape_collisions(page)

    rendered = ts._rendered_text_box(heading)
    assert rendered is not None
    assert _overlap(rendered, icon.box)[0] == 0.0
    assert (heading.pipeline_trace or {}).get("shape_collision_resolved") is True


def test_illustrated_heading_and_body_keep_their_source_column():
    ts = _typesetter()
    heading = _para("عنوان", Box(90, 688, 240, 704), label="heading", debug_id=9)
    body = _para("نص", Box(90, 620, 245, 687), debug_id=10)
    unrelated = _para("آخر", Box(90, 450, 245, 475), debug_id=11)
    icon = PdfCurve(box=Box(52, 672, 80, 705), fill_background=True)
    page = Page(cropbox=Cropbox(box=Box(0, 0, 595, 842)),
                pdf_paragraph=[heading, body, unrelated], pdf_curve=[icon])

    ts._expand_structured_text_boxes(page)

    assert heading.box == Box(90, 688, 240, 704)
    assert ts._calculate_rtl_margin_shift(heading, page) == 0.0
    assert ts._calculate_rtl_margin_shift(body, page) == 0.0
    assert ts._calculate_rtl_margin_shift(unrelated, page) > 0.0


def test_parallel_panel_without_an_icon_keeps_its_lane_beside_illustrated_panel():
    ts = _typesetter()
    label = _para("جهة", Box(90, 688, 240, 704), label="heading", debug_id=16)
    opposite = _para("فاتورة", Box(430, 685, 545, 705), label="title", debug_id=17)
    metadata = _para("مرجع", Box(390, 653, 545, 678), debug_id=18)
    icon = PdfCurve(box=Box(52, 672, 80, 705), fill_background=True)
    page = Page(cropbox=Cropbox(box=Box(0, 0, 595, 842)),
                pdf_paragraph=[label, opposite, metadata], pdf_curve=[icon])

    ts._expand_structured_text_boxes(page)

    assert all(ts._calculate_rtl_margin_shift(p, page) == 0.0 for p in page.pdf_paragraph)


def test_footnote_stabilization_does_not_move_table_rows_or_table_notes():
    ts = _typesetter()
    ts._footnote_zone_stabilized_pages = set()
    cells = [
        _para("42", Box(180, 114, 198, 125), label="footnote", debug_id=12,
              trace={"cell_id": "t0:r1c1"}),
        _para("39", Box(225, 114, 243, 125), label="footnote", debug_id=13,
              trace={"cell_id": "t0:r1c2"}),
    ]
    table_note = _para("جدول", Box(85, 99, 440, 108), label="table_footnote", debug_id=14)
    page_note = _para("هامش", Box(85, 12, 440, 23), label="footnote", debug_id=15)
    page = Page(cropbox=Cropbox(box=Box(0, 0, 595, 842)),
                pdf_paragraph=[*cells, table_note, page_note])

    ts._stabilize_footnote_zone(page)

    assert [cell.box.y for cell in cells] == [114, 114]
    assert table_note.box.y == 99
    assert page_note.box.y > 12


def test_checkbox_form_rows_are_separate_translation_and_layout_units():
    lines = ["Name:", "☑ Email ☐ Phone", "Comments:"]
    drawn_rows = [
        _para(text, Box(90, y, 215, y + 10), debug_id=index)
        for index, (text, y) in enumerate(zip(lines, (310, 290, 270), strict=True))
    ]
    paragraph = PdfParagraph(
        unicode=" ".join(lines), layout_label="paragraph",
        pdf_paragraph_composition=[row.pdf_paragraph_composition[0] for row in drawn_rows],
    )
    paragraphs = [paragraph]
    finder = object.__new__(ParagraphFinder)
    finder.split_embedded_structured_lines(paragraphs)

    assert [item.unicode for item in paragraphs] == lines
    assert [item.layout_label for item in paragraphs] == ["form_row"] * 3
    assert [item.box.y for item in paragraphs] == [310, 290, 270]


def test_fixed_table_cell_is_compacted_vertically_and_horizontally_to_its_slot():
    ts = _typesetter()
    cell = _para(
        "قيمة مترجمة طويلة",
        Box(100, 400, 205, 422),
        render_box=Box(88, 392, 225, 435),
        label="wired_table_cell_fixed_rtl",
        debug_id=4,
        trace={"table_id": "table-1", "cell_id": "r1c1"},
    )
    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[cell],
    )

    ts._audit_table_cells_after_render(page)

    rendered = ts._rendered_text_box(cell)
    assert rendered is not None
    assert rendered.x >= cell.box.x - 1.5 and rendered.x2 <= cell.box.x2 + 1.5
    assert rendered.y >= cell.box.y - 1.5 and rendered.y2 <= cell.box.y2 + 1.5
    trace = cell.pipeline_trace or {}
    assert trace.get("table_cell_overflow_detected") is True
    assert trace.get("table_cell_overflow_resolved") is True
    assert not trace.get("table_cell_overflow")


def test_cell_fit_scales_shared_font_styles_once_and_updates_line_bounds():
    ts = _typesetter()
    cell = _para("نص", Box(100, 400, 145, 422), debug_id=8,
                 render_box=Box(95, 395, 175, 420))
    line = cell.pdf_paragraph_composition[0].pdf_line
    line.box = Box(95, 395, 175, 420)
    shared_style = line.pdf_character[0].pdf_style
    line.pdf_character[1].pdf_style = shared_style

    assert ts._compact_paragraph_to_box(cell, cell.box, min_ratio=0.45)

    first, second = line.pdf_character
    assert first.pdf_style.font_size == second.pdf_style.font_size == 5.38
    assert shared_style.font_size == 10.0
    assert line.box.x >= cell.box.x and line.box.x2 <= cell.box.x2
    assert abs((second.box.x - first.box.x) - 21.5) < 0.001


def test_column_lane_containment_prevents_cross_column_vertical_displacement():
    ts = _typesetter()
    left = _para(
        "بيانات الفاتورة",
        Box(50, 650, 270, 690),
        render_box=Box(210, 650, 390, 690),
        debug_id=5,
        trace={"column_lane": [45.0, 275.0]},
    )
    right = _para(
        "شروط الدفع",
        Box(320, 650, 545, 690),
        render_box=Box(250, 650, 440, 690),
        debug_id=6,
        trace={"column_lane": [315.0, 550.0]},
    )
    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[left, right],
    )

    ts._contain_rendered_region_bounds(page)
    left_before = ts._rendered_text_box(left)
    right_before = ts._rendered_text_box(right)
    ts._resolve_rendered_text_collisions(page)
    left_after = ts._rendered_text_box(left)
    right_after = ts._rendered_text_box(right)

    assert left_before is not None and right_before is not None
    assert left_after is not None and right_after is not None
    assert left_after.x2 <= 275.0 + 1.5
    assert right_after.x >= 315.0 - 1.5
    assert (left_after.y, left_after.y2) == (left_before.y, left_before.y2)
    assert (right_after.y, right_after.y2) == (right_before.y, right_before.y2)
