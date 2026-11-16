"""P0-B tests: region-boundary containment, line grouping, column split, and negative controls."""
from __future__ import annotations

from types import SimpleNamespace

from babeldoc.format.pdf.document_il import (
    Box,
    Cropbox,
    Page,
    PdfCharacter,
    PdfCurve,
    PdfForm,
    PdfParagraph,
    PdfParagraphComposition,
    PdfRectangle,
    PdfStyle,
    VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting


def _char(ch: str, x: float, y: float, w: float = 6.0, h: float = 10.0) -> PdfCharacter:
    box = Box(x=x, y=y, x2=x + w, y2=y + h)
    return PdfCharacter(
        char_unicode=ch,
        box=box,
        visual_bbox=VisualBbox(box=box),
        pdf_style=PdfStyle(font_id="base", font_size=10.0),
    )


def _typesetter() -> Typesetting:
    ts = object.__new__(Typesetting)
    ts.is_arabic = True
    ts.translation_config = SimpleNamespace(lang_in="en", lang_out="ar")
    ts.font_mapper = SimpleNamespace(
        base_font=SimpleNamespace(char_lengths=lambda t, s: [6.0] * len(t))
    )
    return ts


def test_wired_line_group_keeps_descenders_and_midheight_hyphens_on_same_line():
    # Baseline letters sit at y=100..110; descender 'g','p' dip to y=97..107 (diff=3pt > 2.0);
    # hyphen '-' sits at y=104..105.5 (diff=4pt > 2.0). All belong on line 1.
    glyphs = [
        _char("p", 10, 97, h=10.0),
        _char("g", 16, 97, h=10.0),
        _char("-", 22, 104, h=1.5),
        _char("1", 28, 100, h=10.0),
        _char(" ", 34, 100, h=10.0),
        _char("a", 40, 100, h=10.0),
        # Second real line below (y=82..92)
        _char("b", 10, 82, h=10.0),
    ]
    groups: list[list[PdfCharacter]] = []
    for ch in sorted(
        glyphs,
        key=lambda c: (-c.visual_bbox.box.y2, c.visual_bbox.box.x),
    ):
        box = ch.visual_bbox.box
        g = ParagraphFinder._find_matching_line_group(groups, box)
        if g is None:
            groups.append([ch])
        else:
            g.append(ch)

    assert len(groups) == 2
    line1 = "".join(
        c.char_unicode
        for c in sorted(groups[0], key=lambda c: c.visual_bbox.box.x)
    )
    assert line1 == "pg-1 a"


def test_multicolumn_paragraph_split_and_line_sort_prevent_interleaving():
    pf = object.__new__(ParagraphFinder)
    pf.translation_config = SimpleNamespace(lang_in="en", lang_out="ar")

    # Deliberately interleave character order in the PDF stream across two columns
    # Column A (x=50..90), gutter=160pt, Column B (x=250..290) across 2 rows.
    chars = [
        _char("A", 50, 200), _char("X", 250, 200),
        _char("1", 57, 200), _char("1", 257, 200),
        _char(" ", 64, 200), _char(" ", 264, 200),
        _char("C", 70, 200), _char("Y", 270, 200),
        _char("A", 50, 182), _char("X", 250, 182),
        _char("2", 57, 182), _char("2", 257, 182),
        _char(" ", 64, 182), _char(" ", 264, 182),
        _char("D", 70, 182), _char("Z", 270, 182),
    ]
    para = PdfParagraph(
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=c) for c in chars],
        debug_id="merged_cols",
    )
    pf.update_paragraph_data(para)

    split_paras = pf._split_multicolumn_paragraphs([para])
    assert len(split_paras) == 2

    for sp in split_paras:
        pf._split_paragraph_into_lines(sp, set())
        pf.update_paragraph_data(sp, update_unicode=True)

    line_lists = sorted(
        [
            "".join(c.char_unicode for c in comp.pdf_line.pdf_character)
            for comp in sp.pdf_paragraph_composition
            if comp.pdf_line
        ]
        for sp in split_paras
    )
    assert line_lists == [["A1 C", "A2 D"], ["X1 Y", "X2 Z"]]

    # Negative control: justified single column without clean multi-row gutter stays 1 paragraph.
    justified = PdfParagraph(
        pdf_paragraph_composition=[
            PdfParagraphComposition(pdf_character=_char(ch, x, y))
            for y, row in [(200, [(50, "W"), (85, "Z")]), (182, [(50, "A"), (65, "M"), (85, "Q")])]
            for x, ch in row
        ],
        debug_id="single_col",
    )
    pf.update_paragraph_data(justified)
    assert len(pf._split_multicolumn_paragraphs([justified])) == 1


def test_drawn_enclosures_prevent_mirror_escape_while_free_prose_still_mirrors():
    ts = _typesetter()
    page = Page(
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_rectangle=[
            # Left SQL code box
            PdfRectangle(box=Box(40, 500, 260, 620)),
            # Right YAML config box
            PdfRectangle(box=Box(310, 500, 550, 620)),
            # Narrow badge & form field box
            PdfRectangle(box=Box(50, 420, 140, 445)),
            # Receipt frame sitting right above a barcode form image
            PdfRectangle(box=Box(50, 260, 240, 390)),
            # Full-page decorative border frame (must NOT lock free body prose)
            PdfRectangle(box=Box(10, 10, 585, 832)),
        ],
        pdf_curve=[
            # Flowchart diamond/box frame
            PdfCurve(box=Box(320, 320, 460, 370)),
        ],
        pdf_form=[
            # Barcode directly under receipt frame
            PdfForm(box=Box(60, 190, 230, 245)),
        ],
    )

    sql_para = PdfParagraph(
        box=Box(50, 520, 240, 600),
        unicode="SELECT id FROM orders WHERE paid = 1;",
        layout_label="plain text",
        pipeline_trace={"preserved_code": True},
    )
    yaml_para = PdfParagraph(
        box=Box(320, 520, 530, 600),
        unicode="service:\n  port: 8080\n  enabled: true",
        layout_label="plain text",
        pipeline_trace={"preserved_code": True},
    )
    form_badge = PdfParagraph(
        box=Box(55, 424, 130, 441),
        unicode="DD/MM/YYYY",
        layout_label="heading",
    )
    receipt_hdr = PdfParagraph(
        box=Box(60, 360, 160, 380),
        unicode="MERIDIAN CAFE",
        layout_label="heading",
    )
    diagram_node = PdfParagraph(
        box=Box(330, 330, 450, 360),
        unicode="Ask for one-time code",
        layout_label="plain text",
    )
    # Negative control: ordinary unboxed left-aligned body text outside local boxes.
    free_prose = PdfParagraph(
        box=Box(50, 700, 290, 740),
        unicode="Ordinary narrative prose continues cleanly.",
        layout_label="plain text",
    )

    page.pdf_paragraph = [sql_para, yaml_para, form_badge, receipt_hdr, diagram_node, free_prose]

    for boxed in (sql_para, yaml_para, form_badge, receipt_hdr, diagram_node):
        assert ts._should_skip_margin_mirroring(boxed, page) is True
        assert ts._calculate_rtl_margin_shift(boxed, page) == 0.0

    # Ordinary free prose must still mirror cleanly to RTL margin!
    assert ts._should_skip_margin_mirroring(free_prose, page) is False
    assert ts._calculate_rtl_margin_shift(free_prose, page) > 100.0

    # _expand_structured_text_boxes must NOT stretch boxed headings out of their frame.
    ts._expand_structured_text_boxes(page)
    assert form_badge.box.x >= 50.0 and form_badge.box.x2 <= 140.0
    assert receipt_hdr.box.x >= 50.0 and receipt_hdr.box.x2 <= 240.0

    # Receipt card must not expand vertically down into its enclosing bottom or barcode image.
    floor_y = ts.get_max_bottom_space(receipt_hdr.box, page, paragraph=receipt_hdr)
    assert floor_y >= 261.5
