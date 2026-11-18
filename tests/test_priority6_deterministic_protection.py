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


def test_clause_lexed_span_recovery_preserves_dense_tokens_and_rejects_hallucinations(monkeypatch):
    """Verify lexed span recovery translates prose spans around dense protected tokens,
    preserves exact multiplicity, rejects hallucinated/degenerate spans, and never caches."""
    from babeldoc.format.pdf.document_il.utils.structured_tokens import (
        lex_translatable_and_protected_spans,
    )

    def retry_fn(s: str) -> str:
        # Whole clause fails by dropping the second token
        if "{0}" in s and "{1}" in s:
            return "تحويل {0} إلى الحساب"
        if "Transfer funds" in s:
            return "تحويل الأموال"
        if "with" in s:
            # Simulate a degenerate/hallucinated span that must be rejected
            return "All rights reserved"
        if "to account" in s:
            return "إلى الحساب"
        return "نص مترجم"

    t, cache = _nllb_with_cache(monkeypatch, "تحويل {0} إلى الحساب", retry_fn)
    src = "Transfer funds 2,500 USD with 2,500 USD to account IBAN-9900."
    spans = lex_translatable_and_protected_spans(src)
    assert "".join(val for _, val in spans) == src

    out = t.translate(src)
    assert "تحويل الأموال" in out
    assert "إلى الحساب" in out
    # Unsafe hallucinated span ("with" -> "All rights reserved") kept source English, not hallucination
    assert "All rights reserved" not in out
    # Protected tokens preserved with exact multiplicity
    assert out.count("2,500") == 2
    assert "IBAN-9900" in out
    assert t.incomplete_count >= 1
    cache.set.assert_not_called()


def test_regex_quantifiers_not_reported_as_leaked_placeholders(tmp_path):
    """Verify legitimate regex quantifiers (\\d{4}, [A-Z]{2}) are not flagged as leaked placeholders,
    while real unresolved placeholders ({0}, <b1>) are still caught."""
    from babeldoc.format.pdf.document_il.utils.preflight import audit_output_pdf
    from babeldoc.format.pdf.document_il.utils.quality_report import detect_leaked_placeholders

    regex_samples = [
        r"Pattern: ^[A-Z]{2}\d{2}[A-Z0-9]{1,30}$ and date \d{4}-\d{2}-\d{2}",
        r"Use \w{3} or [0-9]{4} for validation",
    ]
    assert len(detect_leaked_placeholders(regex_samples)) == 0

    leaked_samples = [
        r"Pattern: ^[A-Z]{2}\d{2}$ with leaked {0} and <b1>",
    ]
    assert len(detect_leaked_placeholders(leaked_samples)) >= 2

    import pymupdf

    pdf_path = tmp_path / "regex_sample.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((36, 72), r"Regex: ^[A-Z]{2}\d{4}$", fontsize=11)
    doc.save(str(pdf_path))
    doc.close()

    report = audit_output_pdf(str(pdf_path))
    assert report.get("leaked_placeholders_count", 0) == 0


def test_dedicated_toc_row_layout_shortens_leaders_preserves_page_numbers_and_avoids_collisions():
    """Verify TOC rows with long Arabic titles, contiguous or spaced leaders, and nested indents
    shorten/regenerate leaders, keep page numbers in a stable lane, and never collide across rows."""
    from types import SimpleNamespace
    from babeldoc.format.pdf.document_il import Box, Cropbox, Page, PdfParagraphComposition
    from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
    from babeldoc.format.pdf.document_il.utils.layout_semantics import parse_toc_row
    from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
    from tests.test_priority3_pagination_cohesion import _para, _typesetter

    r1 = parse_toc_row("1. Executive Summary ......................................................... 12")
    assert r1 is not None and r1.page_number == "12" and r1.leader_style == "." and r1.indent_level == 0
    r2 = parse_toc_row("  1.2. Detailed Climate Adaptation Framework . . . . . . . . . . . . 104")
    assert r2 is not None and r2.page_number == "104" and r2.leader_style == ". " and r2.indent_level == 1

    ledger = ContentLedger()
    ts = _typesetter(ledger)
    ts.is_cjk = False
    ts.translation_config.watermark_output_mode = None
    ts.font_mapper = SimpleNamespace(
        fontid2font={},
        base_font=SimpleNamespace(char_lengths=lambda s, sz: [sz * 0.5]),
    )
    # Two consecutive TOC rows where the first has a very long Arabic title + 60 dots originally
    p1 = _para(
        "1. الملخص التنفيذي الشامل للبرنامج الإنمائي والإنساني في المنطقة " + ("." * 60) + " 12",
        50, 700, 540, 718, debug_id=301,
    )
    p2 = _para(
        "1.2. إطار التكيف المناخي التفصيلي . . . . . . . . . . . . . . . . 104",
        50, 680, 540, 696, debug_id=302,
    )
    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[p1, p2],
    )
    for p in (p1, p2):
        chars = list(p.pdf_paragraph_composition[0].pdf_line.pdf_character)
        p.pdf_paragraph_composition = []
        units = [TypesettingUnit(char=c) for c in chars]
        ts.retypeset_with_precomputed_scale(p, page, units, 1.0)
    ts._resolve_rendered_text_collisions(page)

    assert (p1.pipeline_trace or {}).get("is_toc_row") is True
    assert (p2.pipeline_trace or {}).get("is_toc_row") is True
    assert (p1.pipeline_trace or {}).get("toc_page_number") == "12"
    assert (p2.pipeline_trace or {}).get("toc_page_number") == "104"

    b1 = ts._rendered_text_box(p1)
    b2 = ts._rendered_text_box(p2)
    assert b1 is not None and b2 is not None
    # Neither row extends off page horizontally
    assert b1.x >= 0 and b1.x2 <= 595
    assert b2.x >= 0 and b2.x2 <= 595
    # Zero cross-row vertical collision between row 1 and row 2
    assert b2.y2 <= b1.y + 0.5
    # Page numbers are preserved verbatim in rendered characters
    p1_chars = "".join(
        c.char_unicode or ""
        for comp in (p1.pdf_paragraph_composition or [])
        for c in (
            ([comp.pdf_character] if comp.pdf_character else [])
            + (comp.pdf_line.pdf_character if comp.pdf_line and comp.pdf_line.pdf_character else [])
        )
    )
    p2_chars = "".join(
        c.char_unicode or ""
        for comp in (p2.pdf_paragraph_composition or [])
        for c in (
            ([comp.pdf_character] if comp.pdf_character else [])
            + (comp.pdf_line.pdf_character if comp.pdf_line and comp.pdf_line.pdf_character else [])
        )
    )
    assert "12" in p1_chars
    assert "104" in p2_chars
    # The 60 dots in p1 were shortened to fit cleanly
    assert p1_chars.count(".") < 60


def test_atomic_region_grouping_keeps_columns_and_unrelated_cards_separate():
    """Verify assign_page_region_groups groups related form/card/figure elements while never
    merging independent two-column body paragraphs or separate cards."""
    from babeldoc.format.pdf.document_il import Box, Cropbox, Page, PdfRectangle
    from babeldoc.format.pdf.document_il.utils.layout_semantics import assign_page_region_groups
    from tests.test_priority3_pagination_cohesion import _para

    # Two separate drawn cards side-by-side
    card1_rect = PdfRectangle(box=Box(40, 600, 260, 720))
    card2_rect = PdfRectangle(box=Box(300, 600, 520, 720))
    c1_lbl = _para("Card A Title", 50, 680, 240, 700, debug_id=401)
    c2_lbl = _para("Card B Title", 310, 680, 500, 700, debug_id=402)

    # Two independent body columns below the cards
    col_left = _para("Left column prose paragraph with normal sentences.", 40, 400, 250, 460, debug_id=403)
    col_right = _para("Right column prose paragraph with normal sentences.", 310, 400, 520, 460, debug_id=404)

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_rectangle=[card1_rect, card2_rect],
        pdf_paragraph=[c1_lbl, c2_lbl, col_left, col_right],
    )
    assign_page_region_groups(page)

    assert c1_lbl.pipeline_trace.get("region_group_id") != c2_lbl.pipeline_trace.get("region_group_id")
    assert col_left.pipeline_trace.get("column_lane") != col_right.pipeline_trace.get("column_lane")
    assert not col_left.pipeline_trace.get("region_group_id")
    assert not col_right.pipeline_trace.get("region_group_id")


def test_card_label_expands_into_verified_container_free_space():
    """Verify a card/figure label that slightly overflows its inner sub-box expands into
    verified free space inside the outer card container without reporting an escape."""
    from babeldoc.format.pdf.document_il import Box, Cropbox, Page, PdfRectangle
    from babeldoc.format.pdf.document_il.utils.layout_semantics import assign_page_region_groups
    from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
    from tests.test_priority3_pagination_cohesion import _para, _typesetter

    ledger = ContentLedger()
    ts = _typesetter(ledger)
    outer_card = PdfRectangle(box=Box(50, 500, 350, 650))
    inner_subbox = PdfRectangle(box=Box(60, 580, 120, 630))
    # Label starts in inner_subbox (width 60pt) and has 280pt rendered width -> exceeds min_ratio=0.48
    # inside inner_subbox, so it expands into outer_card free space (width 300pt) and fits cleanly!
    lbl = _para("عنوان البطاقة التفصيلي الشامل للبرنامج الإنمائي والإنساني", 65, 595, 340, 615, debug_id=410)
    lbl.box = Box(65, 595, 118, 615)

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_rectangle=[outer_card, inner_subbox],
        pdf_paragraph=[lbl],
    )
    assign_page_region_groups(page)
    issues = ts._validate_rendered_regions(page)
    assert not issues
    assert lbl.pipeline_trace.get("expanded_into_container_free_space") is True


def test_sparse_continuation_page_merges_back_through_pagination_lifecycle():
    """Verify _paginate_overflowing_pages creates real paginated_from_page metadata, merges back
    a sparse continuation onto its source page when collision-free above footnotes, and renumbers
    subsequent pages and continuation_pages cleanly."""
    from babeldoc.format.pdf.document_il import Box, Cropbox, Document, Page
    from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
    from tests.test_priority3_pagination_cohesion import _para, _typesetter

    ledger = ContentLedger()
    ts = _typesetter(ledger)
    ts.translation_config.shared_context_cross_split_part.continuation_pages = {}

    # Page 0: prose above at y=160..320, fixed footnote at y=35..55 (so body_floor = 65),
    # and a short 1-line body paragraph at y=57..73 that dipped into the footnote clearance band
    # (57 < body_floor 65, but >= footnote top 55). Because has_footnotes is True, pre-compaction
    # skips it, overflow_queue moves it to a continuation page with paginated_from_page=0, and
    # _merge_sparse_continuation_pages_back places it into the collision-free space below p_main
    # (at y=140..156 >= body_floor) and removes the sparse continuation page!
    p_main = _para("الفقرة الرئيسية في أعلى الصفحة الأولى.", 50, 160, 480, 320, debug_id=420)
    p_sparse_ov = _para(
        "سطر ختامي قصير قابل للدمج بأمان.",
        50, 57, 360, 73,
        debug_id=421,
        trace={"page_overflow": True},
    )
    p_fn = _para("1 حاشية مرجعية ثابتة في أسفل الصفحة.", 50, 35, 480, 55, label="footnote", debug_id=422)
    p_next_page = _para("محتوى الصفحة الثانية المستقلة.", 50, 680, 480, 720, debug_id=423)

    page0 = Page(page_number=0, cropbox=Cropbox(box=Box(0, 0, 595, 842)), pdf_paragraph=[p_main, p_sparse_ov, p_fn])
    page1 = Page(page_number=1, cropbox=Cropbox(box=Box(0, 0, 595, 842)), pdf_paragraph=[p_next_page])
    doc = Document(page=[page0, page1], total_pages=2)

    created = ts._paginate_overflowing_pages(doc)
    assert created == 0
    assert len(doc.page) == 2
    assert [p.page_number for p in doc.page] == [0, 1]
    assert ts.translation_config.shared_context_cross_split_part.continuation_pages == {}
    # Verify real pagination metadata was set and then merged back onto page 0 above the footnote
    assert p_sparse_ov in doc.page[0].pdf_paragraph
    assert p_sparse_ov.pipeline_trace.get("paginated_from_page") == 0
    assert p_sparse_ov.pipeline_trace.get("merged_from_sparse_continuation") is True
    r_sparse = ts._rendered_text_box(p_sparse_ov)
    r_fn = ts._rendered_text_box(p_fn)
    assert r_sparse is not None and r_fn is not None
    assert r_sparse.y >= r_fn.y2 + 3.0


def test_sparse_continuation_merge_blocked_by_collision_footnotes_artwork_or_atomic_group():
    """Verify sparse continuation merge-back refuses to merge when blocked by vertical collision/footnotes,
    relocated artwork, or an atomic region group, and preserves later page numbering when another page merges."""
    from babeldoc.format.pdf.document_il import Box, Cropbox, Document, Page, PdfRectangle
    from babeldoc.format.pdf.document_il.utils.layout_semantics import assign_page_region_groups
    from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
    from tests.test_priority3_pagination_cohesion import _para, _typesetter

    ledger = ContentLedger()
    ts = _typesetter(ledger)
    ts.translation_config.shared_context_cross_split_part.continuation_pages = {}

    # Case 1: Page 0 is full above the footnote zone (y=90..750, footnote at y=35..75 -> body_floor=85),
    # so a 24pt overflowing paragraph cannot fit in the 5pt gap and must stay on its continuation page!
    p0_full = _para("نص يملأ الصفحة بالكامل فوق الحاشية " * 10, 50, 90, 480, 750, debug_id=430)
    p0_fn = _para("1 حاشية سفلية ثابتة.", 50, 35, 480, 75, label="footnote", debug_id=431)
    p0_ov = _para("فقرة فائضة لا تتسع فوق الحاشية.", 50, 50, 420, 74, debug_id=432, trace={"page_overflow": True})
    page0 = Page(page_number=0, cropbox=Cropbox(box=Box(0, 0, 595, 842)), pdf_paragraph=[p0_full, p0_ov, p0_fn])

    # Case 2: Page 1 has an overflowing atomic card group with a drawn rectangle -> must NOT merge back!
    card_rect = PdfRectangle(box=Box(50, -15, 380, 40))
    card_lbl = _para("Total Due: 500 USD", 60, -10, 300, 15, debug_id=433, trace={"page_overflow": True})
    p1_top = _para("مقدمة الصفحة الثانية.", 50, 650, 480, 710, debug_id=434)
    page1 = Page(
        page_number=1,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_rectangle=[card_rect],
        pdf_paragraph=[p1_top, card_lbl],
    )
    assign_page_region_groups(page1)

    doc = Document(page=[page0, page1], total_pages=2)
    created = ts._paginate_overflowing_pages(doc)
    assert created == 2
    assert len(doc.page) == 4
    assert [p.page_number for p in doc.page] == [0, 1, 2, 3]
    assert p0_ov in doc.page[1].pdf_paragraph
    assert not p0_ov.pipeline_trace.get("merged_from_sparse_continuation")
    assert card_lbl in doc.page[3].pdf_paragraph
    assert card_rect in doc.page[3].pdf_rectangle
    assert not card_lbl.pipeline_trace.get("merged_from_sparse_continuation")


def test_duplicated_protected_token_in_span_or_segment_retry_is_rejected_and_uncached(monkeypatch):
    """Verify that if a translated span or segment duplicates a protected number/identifier,
    recovery is rejected, legitimate repeated source values still use Counter multiplicity,
    the result is marked incomplete, and it is never cached."""

    def dup_retry(s: str) -> str:
        # First sentence has 2 legitimate "50" tokens in source ({0} and {1}): valid!
        if "First batch" in s or ("{0}" in s and "{1}" in s):
            return "الدفعة الأولى دفعت {0} ثم {1} دولار."
        # Second sentence has only ONEprotected token ({0} = "INV-77"), but model duplicates it!
        return "تم تسديد الفاتورة {0} والفاتورة {0} بالكامل."

    # Whole-block decode drops INV-77 so it triggers retry/span recovery
    first_bad = "الدفعة الأولى دفعت {0} ثم {1} دولار. تم تسديد الفاتورة بالكامل."
    t, cache = _nllb_with_cache(monkeypatch, first_bad, dup_retry)
    src = "First batch paid 50 then 50 dollars. The team settled invoice INV-77 completely."
    out = t.translate(src)

    # First sentence with legitimate repeated "50" (Counter=2) is accepted in Arabic
    assert "الدفعة الأولى" in out
    assert out.count("50") == 2
    # Second sentence where retry duplicated INV-77 (actual=2 != expected=1) is rejected and keeps source English
    assert out.count("INV-77") == 1
    assert "The team settled invoice INV-77 completely." in out
    assert t.incomplete_count == 1
    cache.set.assert_not_called()


def test_table_header_expansion_shifts_subsequent_rows_and_following_body_cleanly():
    """Verify multi-line Arabic table header expansion shifts subsequent rows and following
    body content downward together while preserving row/column numeric associations."""
    from babeldoc.format.pdf.document_il import Box, Cropbox, Page
    from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
    from tests.test_priority3_pagination_cohesion import _para, _typesetter

    ledger = ContentLedger()
    ts = _typesetter(ledger)
    # Row 0 (header): col 0 wrapped to y=672..710 (dipping into Row 1 which sits at y=665..685)
    h0 = _para(
        "المكونات الرئيسية والنتائج المتوقعة للبرنامج",
        50, 672, 280, 710,
        label="wired_table_cell_fixed_rtl",
        debug_id=501,
        trace={"table_id": "t_budget", "semantic_table": {"row_index": 0, "col_index": 0, "is_header": True}},
    )
    h0.box = Box(50, 688, 280, 710)
    h1 = _para(
        "الميزانية (USD)",
        290, 690, 480, 708,
        label="wired_table_cell_fixed_rtl",
        debug_id=502,
        trace={"table_id": "t_budget", "semantic_table": {"row_index": 0, "col_index": 1, "is_header": True}},
    )
    # Row 1 (data row): originally at y=665..685 (overlaps h0's rendered bottom 672!)
    r1_c0 = _para(
        "الصحة والتغذية",
        50, 665, 280, 685,
        label="wired_table_cell_fixed_rtl",
        debug_id=503,
        trace={"table_id": "t_budget", "semantic_table": {"row_index": 1, "col_index": 0, "is_header": False}},
    )
    r1_c1 = _para(
        "12,500",
        290, 665, 480, 685,
        label="wired_table_cell_fixed_rtl",
        debug_id=504,
        trace={"table_id": "t_budget", "semantic_table": {"row_index": 1, "col_index": 1, "is_header": False}},
    )
    # Following body paragraph just below the table
    body_below = _para("فقرة نصية تلي الجدول مباشرة.", 50, 650, 480, 664, debug_id=505)

    page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[h0, h1, r1_c0, r1_c1, body_below],
    )
    ts._resolve_table_row_and_body_clearance(page)
    ts._audit_table_cells_after_render(page)

    b_h0 = ts._rendered_text_box(h0)
    b_r1_c0 = ts._rendered_text_box(r1_c0)
    b_r1_c1 = ts._rendered_text_box(r1_c1)
    b_body = ts._rendered_text_box(body_below)

    # Row 1 shifted below Row 0's expanded bottom without overlap
    assert b_r1_c0.y2 <= b_h0.y - 2.0
    # Numeric cell r1_c1 shifted by the exact same amount as its row label r1_c0
    assert abs(b_r1_c0.y2 - b_r1_c1.y2) <= 1.0
    # Following body paragraph shifted below Row 1
    assert b_body.y2 <= b_r1_c0.y - 2.0
    # No false table_cell_overflow reported for h0 after row expansion
    assert not h0.pipeline_trace.get("table_cell_overflow")
