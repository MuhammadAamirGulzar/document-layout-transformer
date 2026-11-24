from types import SimpleNamespace

import pymupdf
import pytest

from babeldoc.format.pdf.document_il import Page
from babeldoc.format.pdf.document_il.backend.pdf_creater import PDFCreater
from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
from babeldoc.format.pdf.document_il.utils.structured_tokens import (
    isolate_ltr_structured_spans,
    protect_structured_tokens,
    restore_structured_tokens,
)
from tests.test_b27_formula_layout import source_character, render_left_to_right
from tests.test_priority1_completeness import _nllb_with_cache
from tests.test_p8_dense_tables import _make_rendered_cell, _make_typesetter
from babeldoc.format.pdf.document_il import Box, Cropbox, PdfCurve, PdfPath, GraphicState, PageLayout
from babeldoc.format.pdf.document_il.utils.semantic_table import annotate_page_semantic_tables
from babeldoc.format.pdf.document_il.backend.pdf_creater import CurveRenderUnit
from bitstring import BitStream
from babeldoc.format.pdf.document_il.utils.preflight import audit_output_pdf


@pytest.mark.parametrize("quantity", ["$7.8M", "−€45.00", "€1.234,56", "PKR 1,25,000", "19 bps", "19 نقطة أساس", "3.6 bn"])
def test_quantity_protection_keeps_value_and_unit_together(quantity):
    source = f"The estimate is {quantity}."
    protected, mapping = protect_structured_tokens(source)
    assert quantity in mapping.values()
    assert restore_structured_tokens(protected, mapping) == source


def test_swapped_currency_cannot_pass_numeric_validation(monkeypatch):
    source = "The reserve is $7.8M while the adjustment is 19 bps."
    bad = "الاحتياطي 7.8M بينما التعديل $19 bps."
    engine, cache = _nllb_with_cache(monkeypatch, bad, lambda _: bad)
    out = engine.translate(source)
    assert "$7.8M" in out
    assert "$19" not in out
    assert engine.incomplete_count == 1
    cache.set.assert_not_called()


def test_currency_atom_survives_rtl_line_layout():
    text = isolate_ltr_structured_spans("بلغ الاحتياطي $7.8M ثم تغير بنسبة 0.5%.")
    units = [TypesettingUnit(char=source_character(char, 0)) for char in text]
    assert "$7.8M" in render_left_to_right(units)


@pytest.mark.parametrize("alternating", [False, True])
def test_sparse_selected_dual_keeps_all_translations_and_continuation(tmp_path, alternating):
    source_path = tmp_path / "source.pdf"
    with pymupdf.open() as source:
        for i in range(12):
            source.new_page().insert_text((30, 30), f"Original {i + 1}")
        source.save(source_path)
    creator = object.__new__(PDFCreater)
    creator.original_pdf_path = source_path
    creator.docs = SimpleNamespace(page=[
        Page(page_number=1), Page(page_number=6),
        Page(page_number=7, continuation_of_page=6), Page(page_number=11),
    ])
    cfg = SimpleNamespace(use_alternating_pages_dual=alternating, dual_translate_first=False)
    with pymupdf.open() as translated:
        for text in ("Translated 2", "Translated 7", "Continuation 7", "Translated 11"):
            translated.new_page().insert_text((30, 30), text)
        with creator._create_dual_pdf(translated, tmp_path / "dual.pdf", cfg, [1, 6, 10]) as dual:
            texts = [p.get_text() for p in dual]
            # Alternating mode reserves a blank original-side page for a continuation.
            assert len(dual) == (8 if alternating else 4)
            combined = "\n".join(texts)
            for text in ("Original 2", "Original 7", "Original 11", "Translated 2", "Translated 7", "Translated 11", "Continuation 7"):
                assert combined.count(text) == 1


def test_expanding_table_moves_the_drawn_bottom_rule_and_clears_note(tmp_path):
    header = _make_rendered_cell("Heading", Box(50, 200, 180, 216), 50, 200, 180, 216, debug_id=1)
    label = _make_rendered_cell("A wrapped label", Box(50, 175, 180, 190), 50, 153, 180, 190, debug_id=2)
    note = _make_rendered_cell("Source note", Box(50, 140, 180, 151), 50, 140, 180, 151, debug_id=3, label="plain text")
    for row, p in enumerate([header, label]):
        p.pipeline_trace = {"table_id": "t0", "semantic_table": {"row_index": row}, "table_source_box": [p.box.x, p.box.y, p.box.x2, p.box.y2]}
    rule = PdfCurve(
        box=Box(50, 172, 180, 172),
        graphic_state=GraphicState(passthrough_per_char_instruction="0 G 1 w"),
        pdf_original_path=None,
        pdf_path=[PdfPath(x=50, y=172, op="m", has_xy=True), PdfPath(x=180, y=172, op="l", has_xy=True)],
        stroke_path=True, debug_info="a",
    )
    page = Page(page_number=0, cropbox=Cropbox(box=Box(0, 0, 250, 300)), pdf_paragraph=[header, label, note], pdf_curve=[rule])
    ts = _make_typesetter()
    ts._resolve_table_row_and_body_clearance(page)
    label_bottom = ts._rendered_text_box(label).y
    assert rule.box.y < label_bottom
    assert ts._rendered_text_box(note).y2 < rule.box.y
    stream = BitStream()
    CurveRenderUnit(rule, 0).render(stream, None)
    with pymupdf.open() as out:
        out.new_page(width=250, height=300)
        xref = out.get_new_xref()
        out.update_object(xref, "<<>>")
        out.update_stream(xref, stream.tobytes())
        out[0].set_contents(xref)
        drawn_y = 300 - out[0].get_drawings()[0]["rect"].y0
        assert drawn_y == pytest.approx(rule.box.y, abs=0.01)


def test_selected_page_audit_counts_only_exported_source_pages(tmp_path):
    src = tmp_path / "source.pdf"
    dst = tmp_path / "translated.pdf"
    with pymupdf.open() as doc:
        for i in range(3):
            doc.new_page().insert_text((30, 30), f"Amount {100 + i} REF-99")
        doc.save(src)
        doc.select([1])
        doc.save(dst)
    qa = audit_output_pdf(dst, source_pdf_path=src, source_page_ranges=[(2, 2)])
    assert qa["missing_immutable_tokens"] == []


def test_overlapping_table_notes_are_reflowed_instead_of_treated_as_fixed_cells():
    first = _make_rendered_cell("(a) A longer note", Box(50, 150, 190, 162), 50, 145, 190, 162, debug_id=1, label="table_footnote")
    second = _make_rendered_cell("(b) A second note", Box(50, 135, 190, 147), 50, 135, 190, 151, debug_id=2, label="table_footnote")
    cell = _make_rendered_cell("A table cell", Box(50, 195, 190, 210), 50, 195, 190, 210, debug_id=3)
    page = Page(page_number=0, cropbox=Cropbox(box=Box(0, 0, 250, 300)), pdf_paragraph=[cell, first, second], page_layout=[PageLayout(class_name="table", box=Box(45, 130, 195, 220))])
    annotate_page_semantic_tables(page)
    ts = _make_typesetter()
    assert not ts._is_fixed_table_paragraph(first)
    assert not ts._is_table_cell(first, page)
    ts._resolve_rendered_text_collisions(page)
    assert ts._rendered_text_box(second).y2 <= ts._rendered_text_box(first).y - 2.0


def test_selected_gap_dual_pdf_pairs_true_source_pages_with_continuations(tmp_path):
    """Selection 3,14-16 with continuations after 15 and 16 must pair English sides
    [3, 14, 15, blank, 16, blank] in the saved dual PDF — never pulling source page 17."""
    source_path = tmp_path / "source.pdf"
    dual_path = tmp_path / "dual.pdf"
    with pymupdf.open() as src:
        for pno in range(1, 19):
            src.new_page(width=400, height=500).insert_text((30, 50), f"SRC_PAGE_{pno}")
        src.save(source_path)

    # Simulate sparse selected pages [3, 14, 15, 16] (0-based 2, 13, 14, 15) AFTER
    # _paginate_overflowing_pages inserted continuations after 15 and 16:
    # page 16 (source_page_number=15) shifted to page_number=16!
    docs_pages = [
        Page(page_number=2, source_page_number=2),
        Page(page_number=13, source_page_number=13),
        Page(page_number=14, source_page_number=14),
        Page(page_number=15, continuation_of_page=14),
        Page(page_number=16, source_page_number=15),
        Page(page_number=17, continuation_of_page=16),
    ]
    creator = object.__new__(PDFCreater)
    creator.original_pdf_path = source_path
    creator.docs = SimpleNamespace(page=docs_pages)
    cfg = SimpleNamespace(use_alternating_pages_dual=False, dual_translate_first=False)

    # Mirror write_pdf's selection logic using _resolve_original_source_pages
    orig_resolved = creator._resolve_original_source_pages(docs_pages)
    selected_pages = sorted({o for o in orig_resolved if o is not None})
    assert selected_pages == [2, 13, 14, 15]

    trans_labels = [
        "AR_TRANS_3",
        "AR_TRANS_14",
        "AR_TRANS_15",
        "AR_CONT_15",
        "AR_TRANS_16",
        "AR_CONT_16",
    ]
    with pymupdf.open() as translated:
        for lbl in trans_labels:
            translated.new_page(width=400, height=500).insert_text((30, 50), lbl)
        with creator._create_dual_pdf(translated, dual_path, cfg, selected_pages) as dual:
            dual.save(dual_path)

    with pymupdf.open(dual_path) as saved:
        assert saved.page_count == 6
        expected = [
            ("SRC_PAGE_3", "AR_TRANS_3"),
            ("SRC_PAGE_14", "AR_TRANS_14"),
            ("SRC_PAGE_15", "AR_TRANS_15"),
            ("", "AR_CONT_15"),
            ("SRC_PAGE_16", "AR_TRANS_16"),
            ("", "AR_CONT_16"),
        ]
        for idx, (exp_en, exp_ar) in enumerate(expected):
            pg = saved[idx]
            mid_x = pg.rect.width / 2.0
            left_txt = pg.get_text("text", clip=pymupdf.Rect(0, 0, mid_x, pg.rect.height)).strip()
            right_txt = pg.get_text("text", clip=pymupdf.Rect(mid_x, 0, pg.rect.width, pg.rect.height)).strip()
            assert left_txt == exp_en, f"Dual page {idx + 1} left={left_txt!r} expected={exp_en!r}"
            assert right_txt == exp_ar, f"Dual page {idx + 1} right={right_txt!r} expected={exp_ar!r}"
        # Confirm source page 17 never leaked into the dual PDF
        full_text = "\n".join(p.get_text() for p in saved)
        assert "SRC_PAGE_17" not in full_text


def test_mixed_arabic_quantities_keep_unit_sign_and_parentheses_across_wrap():
    """Verify visible per-line glyph order and proximity in mixed Arabic prose
    with currencies, percentages, Arabic basis points, minus sign, and accounting
    parentheses — both unwrapped and across line wraps."""
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting

    def _layout_visual_lines(text: str, width: float) -> list[str]:
        ts = object.__new__(Typesetting)
        ts.translation_config = SimpleNamespace(lang_out="ar")
        ts.font_mapper = SimpleNamespace(base_font=SimpleNamespace(char_lengths=lambda t, s: [6]))
        units = [TypesettingUnit(char=source_character(ch, 0)) for ch in text]
        box = Box(x=0, y=0, x2=width, y2=240)
        placed, _ = ts._layout_typesetting_units(
            units, box, scale=1.0, line_skip=1.2, paragraph=_make_rendered_cell("x", box, 0, 0, label="plain text")
        )
        by_y: dict[float, list[tuple[float, str]]] = {}
        for u in placed:
            for ch in u.render()[0]:
                by_y.setdefault(round(ch.box.y, 1), []).append((ch.box.x, ch.char_unicode))
        return [
            "".join(c for _, c in sorted(chars, key=lambda item: item[0]))
            for _y, chars in sorted(by_y.items(), reverse=True)
        ]

    mixed = (
        "قيم المراجعة: EUR 4,560.20 · $8.4M · 14 bn · 0.75٪ · "
        "32 نقطة أساس · −€82.50 · (€82.50)"
    )
    isolated = isolate_ltr_structured_spans(mixed)
    # Idempotency check
    assert isolate_ltr_structured_spans(isolated) == isolated

    # 1) Single wide line
    wide_lines = _layout_visual_lines(isolated, width=1000.0)
    assert len(wide_lines) == 1
    wide = wide_lines[0]
    # In visual LTR order for an RTL Arabic line, "32 نقطة أساس" displays with
    # Arabic "ساسأ ةطقن" immediately to the left of "32" (no other quantity between them!).
    assert "ساسأ ةطقن 32" in wide
    # Minus sign stays attached to the left of €82.50, distinct from balanced (€82.50)
    assert "−€82.50" in wide
    assert "(€82.50)" in wide
    assert "(€82.50 · (€82.50" not in wide
    assert "$8.4M" in wide
    assert "EUR 4,560.20" in wide
    assert "14 bn" in wide
    assert "0.75٪" in wide

    # 2) Wrapped narrow line: 32 must stay on the SAME line immediately next to نقطة أساس,
    # and −€82.50 vs (€82.50) must each remain intact without breaking or mirroring parens.
    wrapped_lines = _layout_visual_lines(isolated, width=220.0)
    assert len(wrapped_lines) >= 3
    assert any("ساسأ ةطقن 32" in ln for ln in wrapped_lines), wrapped_lines
    assert any("−€82.50" in ln for ln in wrapped_lines), wrapped_lines
    assert any("(€82.50)" in ln for ln in wrapped_lines), wrapped_lines
    assert any("$8.4M" in ln for ln in wrapped_lines), wrapped_lines

    # 3) Signed Arabic basis points ('+25 نقطة أساس') stay bound with '+' on the left of 25
    signed_bps = "تغيير سعر السياسة: +25 نقطة أساس"
    iso_bps = isolate_ltr_structured_spans(signed_bps)
    assert isolate_ltr_structured_spans(iso_bps) == iso_bps
    bps_lines = _layout_visual_lines(iso_bps, width=500.0)
    assert len(bps_lines) == 1
    assert "ساسأ ةطقن +25" in bps_lines[0], bps_lines


def test_styles_and_formulas_preserves_signed_currency_and_underscore_paths():
    """Phase-1 parser regression:
    1) Leading '−' in '−€45.00' (category Sm) must not become a lone PdfFormula (<b1>).
    2) Underscores in 'transdoc_report_final-v2.pdf' and hard-wrapped Unix paths
       (even when visual_bbox sits below baseline) must not become PdfFormula (<bN>).
    3) Hard-wrapped path lines rejoin without injecting a breaking space.
    4) Control: genuine formula-font expressions still classify as PdfFormula."""
    from babeldoc.format.pdf.document_il.il_version_1 import (
        Page,
        PdfCharacter,
        PdfFont,
        PdfLine,
        PdfParagraph,
        PdfParagraphComposition,
        PdfStyle,
        VisualBbox,
    )
    from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
    from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas
    from babeldoc.format.pdf.document_il.utils.structured_tokens import (
        is_immutable_technical_text,
        protect_structured_tokens,
    )

    def _make_line(text: str, *, font_id: str = "F1", lower_underscore: bool = True) -> PdfLine:
        chars = []
        x = 10.0
        for ch in text:
            box = Box(x=x, y=100.0, x2=x + 5.0, y2=110.0)
            # Mimic LiberationSans underscore ink sitting strictly below baseline
            vbox = (
                Box(x=x, y=97.0, x2=x + 5.0, y2=98.5)
                if (lower_underscore and ch == "_")
                else Box(x=x, y=100.0, x2=x + 5.0, y2=110.0)
            )
            chars.append(
                PdfCharacter(
                    pdf_character_id=ord(ch),
                    char_unicode=ch,
                    box=box,
                    visual_bbox=VisualBbox(box=vbox),
                    pdf_style=PdfStyle(font_id=font_id, font_size=9.5),
                    vertical=False,
                )
            )
            x += 5.0
        return PdfLine(box=Box(10.0, 100.0, x, 110.0), pdf_character=chars)

    def _make_para(lines: list[PdfLine]) -> PdfParagraph:
        return PdfParagraph(
            box=Box(10.0, 90.0, 400.0, 115.0),
            pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=ln) for ln in lines],
        )

    p_curr = _make_para([_make_line("0.25% · 25 bps · −€45.00 · (€45.00)")])
    p_file = _make_para([_make_line("transdoc_report_final-v2.pdf")])
    p_path = _make_para([
        _make_line("Q3 file not received: /srv/shared/finance/2026/q3/bra"),
        _make_line("nch_reports/millbrook_final_v3_REVIEWED.xlsx"),
    ])
    p_math = _make_para([_make_line("x_1 + y_2", font_id="F_MATH")])

    page = Page(
        page_number=0,
        pdf_font=[
            PdfFont(font_id="F1", name="LiberationSans"),
            PdfFont(font_id="F_MATH", name="CMMI10"),
        ],
        pdf_paragraph=[p_curr, p_file, p_path, p_math],
    )

    sf = object.__new__(StylesAndFormulas)
    sf.translation_config = SimpleNamespace(
        formular_font_pattern=r"CMMI.*",
        formular_char_pattern=None,
    )
    sf.font_mapper = SimpleNamespace(has_char=lambda _c: True)
    sf.process_page_formulas(page)
    sf.process_translatable_formulas(page)

    # 1) No PdfFormula for '−' in '−€45.00'
    assert not any(c.pdf_formula for c in p_curr.pdf_paragraph_composition)
    curr_text = "".join(
        ch.char_unicode
        for c in p_curr.pdf_paragraph_composition
        if c.pdf_line
        for ch in c.pdf_line.pdf_character
    )
    assert "−€45.00" in curr_text
    _, curr_map = protect_structured_tokens(curr_text)
    assert "−€45.00" in curr_map.values()

    # 2) No PdfFormula for '_' in filename or wrapped path
    assert not any(c.pdf_formula for c in p_file.pdf_paragraph_composition)
    file_text = "".join(
        ch.char_unicode
        for c in p_file.pdf_paragraph_composition
        if c.pdf_line
        for ch in c.pdf_line.pdf_character
    )
    assert file_text == "transdoc_report_final-v2.pdf"
    assert is_immutable_technical_text(file_text)
    assert not any(c.pdf_formula for c in p_path.pdf_paragraph_composition)

    # 3) Hard-wrapped Unix path rejoins without breaking space (both newline and soft-wrap space)
    captured: list[str] = []
    il_tr = object.__new__(ILTranslator)
    il_tr.translation_config = SimpleNamespace(
        lang_in="en", lang_out="ar", raise_if_cancelled=lambda: None, progress_monitor=None
    )
    il_tr.translate_engine = SimpleNamespace(
        translate=lambda seg, rate_limit_params=None: (captured.append(seg), seg)[1]
    )
    il_tr.stage_name = "Translate Paragraphs"
    il_tr.support_llm_translate = False
    expected_path_line = (
        "Q3 file not received: /srv/shared/finance/2026/q3/branch_reports/millbrook_final_v3_REVIEWED.xlsx"
    )
    il_tr._translate_with_line_preservation(
        "Q3 file not received: /srv/shared/finance/2026/q3/bra\n"
        "nch_reports/millbrook_final_v3_REVIEWED.xlsx",
        {},
    )
    il_tr._translate_with_line_preservation(
        "Q3 file not received: /srv/shared/finance/2026/q3/bra "
        "nch_reports/millbrook_final_v3_REVIEWED.xlsx",
        {},
    )
    assert captured == [expected_path_line, expected_path_line]
    _, path_map = protect_structured_tokens(captured[0])
    assert "/srv/shared/finance/2026/q3/branch_reports/millbrook_final_v3_REVIEWED.xlsx" in path_map.values()

    # 4) Control: real formula-font math still produces PdfFormula
    assert any(c.pdf_formula for c in p_math.pdf_paragraph_composition)
