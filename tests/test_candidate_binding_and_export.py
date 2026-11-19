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
