"""Deterministic protected-content recovery: exact multiplicity, boundary tags, span recovery."""
from __future__ import annotations

from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder, ILTranslator
from babeldoc.format.pdf.document_il.utils.structured_tokens import (
    is_immutable_technical_text,
    protect_structured_tokens,
    restore_structured_tokens,
)
from tests.test_priority1_completeness import _nllb_with_cache


def test_repeated_values_keep_exact_multiplicity():
    src = "Paid 5 on 5 and again 5 with (2024) (2024) noted."
    prot, mapping = protect_structured_tokens(src)
    assert sum(1 for v in mapping.values() if v == "5") == 3
    assert restore_structured_tokens(prot, mapping) == src


def test_literal_aliases_are_not_consumed_as_tags():
    # "(1)" and "[2]" are genuine text here; only the canonical tags are restored.
    out = restore_structured_tokens("{0} (1) {1} [2]", {"{0}": "7", "{1}": "9", "{2}": "X"}, strict=False)
    assert out.startswith("7 (1) 9")
    # Restored values are never re-scanned by later alias patterns.
    out = restore_structured_tokens("{0} <1>", {"{0}": "(1)", "{1}": "Q"}, strict=False)
    assert out == "(1) Q"


def _ti(unicode_text, count):
    return ILTranslator.TranslateInput(
        unicode=unicode_text,
        placeholders=[
            FormulaPlaceholder(
                placeholder_id=i,
                formula=None,
                placeholder=f"<b{i}>",
                regex_pattern=rf"<\s*b\s*{i}\s*>",
            )
            for i in range(1, count + 1)
        ],
        base_style=None,
    )


def test_leading_adjacent_and_trailing_boundary_placeholders_survive():
    ti = _ti("<b1><b2>Annual report summary for readers<b3>", 3)
    out = ILTranslator._recover_and_clean_formula_placeholders(ti, "ملخص التقرير السنوي للقراء")
    assert out.index("<b1>") < out.index("<b2>") < out.index("ملخص")
    assert out.rstrip().endswith("<b3>")
    assert out.count("<b1>") == out.count("<b2>") == out.count("<b3>") == 1


def test_interior_placeholders_are_never_guessed():
    ti = _ti("The value <b1> grows with demand every year.", 1)
    out = ILTranslator._recover_and_clean_formula_placeholders(ti, "تنمو القيمة مع الطلب كل عام.")
    assert "<b1>" not in out


def test_immutable_technical_text_is_not_a_failed_translation():
    for value in ("MCA-TS-0417", "12.5 mg/L", "https://example.org/a?b=1", "x \u2264 y", "ZZ30 MRCA 0001 2345"):
        assert is_immutable_technical_text(value), value
    for value in ("Total: 420.00", "No", "Total amount due"):
        assert not is_immutable_technical_text(value), value


def test_immutable_cell_bypasses_model_and_cache(monkeypatch):
    engine, cache = _nllb_with_cache(monkeypatch, "unused", lambda s: "unused")
    assert engine.translate("INV-2026-00417 / 12.5 mg/L") == "INV-2026-00417 / 12.5 mg/L"
    engine.do_translate.assert_not_called()
    assert engine.incomplete_count == 0
    cache.set.assert_not_called()


_S1 = "Clinics expanded maternal nutrition screening for vulnerable households in 2024."
_S2 = "District teams restored water supply lines serving 300 schools."
_S3 = "Regional inspectors verified every facility before the annual audit closed."
_AR1 = "وسّعت العيادات فحوصات تغذية الأمهات للأسر الأكثر هشاشة في {0}."
_AR2_BAD = "أعادت فرق المقاطعات خطوط إمداد المياه إلى المدارس."
_AR3 = "وتحقق المفتشون الإقليميون من كل منشأة قبل إغلاق التدقيق السنوي."


def _retry(seg: str) -> str:
    if "Clinics" in seg:
        return _AR1
    if "District" in seg:
        return _AR2_BAD  # drops the protected number: cannot be proven safe
    if "Regional" in seg:
        return _AR3
    return _AR2_BAD


def test_mixed_block_keeps_valid_arabic_spans_and_flags_the_failed_one(monkeypatch):
    first = _AR1 + " " + _AR2_BAD + " " + _AR3  # first decode lost the value 300
    engine, cache = _nllb_with_cache(monkeypatch, first, _retry)
    source = f"{_S1} {_S2} {_S3}"
    out = engine.translate(source)
    assert "2024" in out and "المفتشون" in out  # translated spans survive
    assert _S2 in out  # unsafe span keeps its exact source text
    assert out.count("300") == 1
    assert engine.incomplete_count == 1  # remains visible in the quality report
    cache.set.assert_not_called()  # never cached as a success


def test_identical_input_gives_identical_decision(monkeypatch):
    first = _AR1 + " " + _AR2_BAD + " " + _AR3
    engine, cache = _nllb_with_cache(monkeypatch, first, _retry)
    source = f"{_S1} {_S2} {_S3}"
    runs = []
    for _ in range(2):
        before = engine.incomplete_count
        runs.append((engine.translate(source), engine.incomplete_count - before))
    assert runs[0] == runs[1]


def test_unframed_invoice_and_form_siblings_move_together_without_detachment():
    from babeldoc.format.pdf.document_il import Box, Cropbox, Document, Page
    from babeldoc.format.pdf.document_il.utils.layout_semantics import assign_page_region_groups
    from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
    from tests.test_priority3_pagination_cohesion import _para, _typesetter

    ledger = ContentLedger()
    ts = _typesetter(ledger)
    # Fixed table obstacle above prevents upward compaction onto source page
    tbl = _para("Invoice Line 1", 50, 75, 540, 260, label="wired_table_cell_fixed_rtl", debug_id=70, trace={"table_id": "t_inv"})
    sec_hdr = _para("Payment Terms", 55, 48, 220, 64, label="heading", debug_id=71)
    left_kv = _para("Net 30 days. VAT ID: ZZ 123 456 789", 55, 24, 270, 42, debug_id=72)
    right_kv = _para("Bank: Northgate Savings\nAttn: Accounts Payable", 310, 24, 535, 42, debug_id=73)
    iban_kv = _para("Pay to: Meridian · IBAN ZZ30 MRCA 0001 · Ref: INV-2026-00417", 55, -8, 535, 12, debug_id=74, trace={"page_overflow": True})

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[tbl, sec_hdr, left_kv, right_kv, iban_kv],
    )
    assign_page_region_groups(page)
    doc = Document(page=[page], total_pages=1)

    created = ts._paginate_overflowing_pages(doc)
    assert created == 1
    assert tbl in doc.page[0].pdf_paragraph
    assert all(m in doc.page[1].pdf_paragraph for m in (sec_hdr, left_kv, right_kv, iban_kv))
    rep = ledger.build_report()
    assert rep["summary"]["detached_group_count"] == 0


def test_same_column_chain_compaction_uses_free_space_above_intermediate_block():
    from babeldoc.format.pdf.document_il import Box, Cropbox, Document, Page
    from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
    from tests.test_priority3_pagination_cohesion import _para, _typesetter

    ledger = ContentLedger()
    ts = _typesetter(ledger)
    footer = _para("Page 4", 480, 20, 545, 34, label="page_number", debug_id=80)
    col_top = _para("Top paragraph in column 1", 50, 620, 270, 680, debug_id=81)
    # Huge unused space 100..620 in column 1, with an intermediate paragraph at 56..68 right above heading+body
    col_mid = _para("Intermediate paragraph in column 1", 50, 56, 270, 68, debug_id=82)
    heading = _para("Section Heading", 50, 38, 240, 50, label="heading", debug_id=83)
    body = _para("Overflowing tail in column 1", 50, 22, 270, 34, debug_id=84, trace={"page_overflow": True})
    col2 = _para("Independent column 2 block", 320, 80, 540, 400, debug_id=85)

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[col_top, col_mid, heading, body, col2, footer],
    )
    doc = Document(page=[page], total_pages=1)

    created = ts._paginate_overflowing_pages(doc)
    assert created == 0
    assert len(doc.page) == 1
    assert (body.pipeline_trace or {}).get("compacted_on_source_page") is True
    assert (col_mid.pipeline_trace or {}).get("compacted_on_source_page") is True


def test_impossible_table_and_form_regions_report_readable_overflow():
    from babeldoc.format.pdf.document_il import Box, Cropbox, Page
    from babeldoc.format.pdf.document_il.utils.layout_semantics import assign_page_region_groups
    from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
    from tests.test_priority3_pagination_cohesion import _para, _typesetter

    ledger = ContentLedger()
    ts = _typesetter(ledger)
    # Extreme overflow in a fixed table cell and a form_row field
    cell = _para("Very long translated table cell content " * 4, 50, 300, 130, 316, label="wired_table_cell_fixed_rtl", debug_id=91, trace={"table_id": "t1"})
    cell.box = Box(50, 300, 130, 316)
    form_f = _para("Name: [          ] Date: DD/MM/YYYY " * 3, 50, 200, 180, 216, label="form_row", debug_id=92)
    form_f.box = Box(50, 200, 180, 216)

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[cell, form_f],
    )
    assign_page_region_groups(page)
    ts._audit_table_cells_after_render(page)
    issues = ts._validate_rendered_regions(page)

    assert issues
    rep = ledger.build_report()
    assert rep["summary"]["table_cell_overflow_count"] >= 1
    assert rep["summary"]["region_containment_issue_count"] >= 1
    assert (cell.pipeline_trace or {}).get("region_fallback") == "unresolved_readable_overflow"
    assert (form_f.pipeline_trace or {}).get("region_fallback") == "unresolved_readable_overflow"
