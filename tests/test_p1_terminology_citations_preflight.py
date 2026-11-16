"""Focused tests for P1: domain glossary, abbreviation disambiguation, doc TM, citations/emails/dates, preflight, and output QA."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pymupdf
import pytest

from babeldoc.format.pdf.document_il import il_version_1
from babeldoc.format.pdf.document_il.midend.domain_glossary import (
    DocumentTranslationMemory,
    apply_post_glossary,
    apply_pre_glossary,
    classify_document_domain,
    extract_document_abbreviations,
)
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
from babeldoc.format.pdf.document_il.utils.preflight import (
    audit_output_pdf,
    inspect_document_preflight,
)
from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
from babeldoc.format.pdf.translation_config import SharedContextCrossSplitPart
from babeldoc.translator.translator import _protect_numerals, _restore_numerals


def _make_il_para(text: str, *, debug_id: int = 1, label: str = "body_text") -> il_version_1.PdfParagraph:
    style = il_version_1.PdfStyle(font_id="F0", font_size=10.0)
    box = il_version_1.Box(x=50, y=700, x2=400, y2=720)
    chars = [
        il_version_1.PdfCharacter(
            char_unicode=ch,
            box=il_version_1.Box(x=50 + i * 4, y=700, x2=54 + i * 4, y2=720),
            pdf_style=style,
            visual_bbox=None,
        )
        for i, ch in enumerate(text)
    ]
    comp = il_version_1.PdfParagraphComposition(
        pdf_same_style_characters=il_version_1.PdfSameStyleUnicodeCharacters
        if hasattr(il_version_1, "PdfSameStyleUnicodeCharacters")
        else None,
    )
    same = il_version_1.PdfSameStyleCharacters(pdf_style=style, pdf_character=chars)
    comp = il_version_1.PdfParagraphComposition(pdf_same_style_characters=same)
    return il_version_1.PdfParagraph(
        unicode=text,
        debug_id=debug_id,
        layout_label=label,
        box=box,
        pdf_style=style,
        pdf_paragraph_composition=[comp],
        first_line_indent=False,
        vertical=False,
        xobj_id=-1,
    )


def _make_il_page(page_number: int, paragraphs: list, chars: list | None = None) -> il_version_1.Page:
    crop = il_version_1.Cropbox(box=il_version_1.Box(x=0, y=0, x2=595, y2=842))
    return il_version_1.Page(
        page_number=page_number,
        cropbox=crop,
        pdf_paragraph=paragraphs,
        pdf_character=chars or [],
        pdf_font=[il_version_1.PdfFont(font_id="F0", name="Times", encoding_length=1, bold=False, italic=False, monospace=False, serif=False, ascent=800, descent=-200)],
        pdf_xobject=[],
    )


# ---------------------------------------------------------------------------
# P1-09: Domain detection, first-occurrence abbreviation disambiguation & TM
# ---------------------------------------------------------------------------


def test_classify_document_domain_and_extract_abbreviations():
    nlp_texts = [
        "We analyze Part-of-Speech (POS) neurons in deep transformer models.",
        "Ablation studies on BERT and RoBERTa evaluate selectivity across layers.",
    ]
    assert classify_document_domain(nlp_texts) == "scientific_nlp"
    abbrevs = extract_document_abbreviations(nlp_texts)
    assert abbrevs == {"POS": "Part-of-Speech"}

    bank_texts = [
        "International Bank Account Number (IBAN) generation uses MOD 97 check digits.",
        "Qatar Central Bank settlement via SWIFT BIC routing codes.",
    ]
    assert classify_document_domain(bank_texts) == "financial_banking"
    bank_abbrevs = extract_document_abbreviations(bank_texts)
    assert bank_abbrevs == {"IBAN": "International Bank Account Number"}

    invoice_texts = [
        "Invoice No: INV-2026-0041; Value Added Tax (VAT) Registration ID.",
        "Purchase Order PO #99412 Subtotal and Due Date.",
    ]
    assert classify_document_domain(invoice_texts) == "invoice_form"
    assert extract_document_abbreviations(invoice_texts) == {"VAT": "Value Added Tax"}

    # Citations and numbers are never mistaken for acronym definitions.
    assert extract_document_abbreviations(["Prior analysis (Smith, 2020) and Table (2024)."]) == {}


def test_abbreviation_disambiguates_pos_in_post_glossary():
    # Scientific NLP paper activates POS -> POS repair even on short sentences without 'NLP' keyword.
    assert (
        apply_post_glossary(
            "تحلل نقطة التسجيل",
            source_text="Analyze tags",
            domain="scientific_nlp",
            abbreviations={"POS": "Part-of-Speech"},
        )
        == "تحلل POS"
    )
    # Retail Point-of-Sale explicitly avoids turning POS context into linguistic tag forcing.
    assert (
        apply_post_glossary(
            "نقطة التسجيل",
            source_text="Retail Point of Sale terminal",
            domain="scientific_nlp",
            abbreviations={"POS": "Point of Sale"},
        )
        == "نقطة التسجيل"
    )
    assert apply_pre_glossary("SLA", domain="general", abbreviations={"SLA": "Service Level Agreement"}) == "SLA"


def test_document_translation_memory_caches_repeated_blocks_and_reports_to_ledger(tmp_path):
    shared = SharedContextCrossSplitPart()
    ledger = shared.content_ledger
    cfg = SimpleNamespace(
        lang_in="en",
        lang_out="ar",
        debug=False,
        min_text_length=1,
        disable_rich_text_translate=True,
        auto_extract_glossary=False,
        pool_max_workers=1,
        shared_context_cross_split_part=shared,
        progress_monitor=SimpleNamespace(
            stage_start=lambda *a, **k: __import__("contextlib").nullcontext(
                SimpleNamespace(advance=lambda *x: None)
            )
        ),
        raise_if_cancelled=lambda: None,
        get_working_file_path=lambda name: tmp_path / name,
    )
    engine = Mock()
    engine.name = "mock-mt"
    engine.untranslated_count = 0
    engine.incomplete_count = 0
    engine.translate = Mock(side_effect=lambda txt, **kw: f"ترجمة {txt}")

    il = object.__new__(ILTranslator)
    il.translation_config = cfg
    il.font_mapper = None
    il.shared_context_cross_split_part = shared
    il.support_llm_translate = False
    il.use_as_fallback = False
    il.add_content_filter_hint_lock = __import__("threading").Lock()
    il.docs = None
    il.translate_engine = engine
    il.stage_name = "Translate Paragraphs"
    il.calc_token_count = lambda t: len(t or "")
    il.shape_arabic_text = lambda t: t

    doc = il_version_1.Document(
        page=[
            _make_il_page(0, [_make_il_para("Natural Language Processing (NLP) overview", debug_id=1), _make_il_para("Quarterly Summary", debug_id=2, label="title")]),
            _make_il_page(1, [_make_il_para("Quarterly Summary", debug_id=3, label="title")]),
        ]
    )
    il.translate(doc)

    # "Quarterly Summary" appears on both pages -> engine translates it once, TM serves the 2nd!
    translated_inputs = [call.args[0] for call in engine.translate.call_args_list]
    assert translated_inputs.count("Quarterly Summary") == 1
    assert doc.page[0].pdf_paragraph[1].unicode == doc.page[1].pdf_paragraph[0].unicode

    report_path = ledger.write_json(tmp_path / "quality_report.json", input_file="sample.pdf", lang_in="en", lang_out="ar", engine="mock-mt")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["terminology"]["abbreviations"] == {"NLP": "Natural Language Processing"}
    assert report["summary"]["translation_memory_hits"] == 1


def test_document_tm_never_stores_empty_or_leaked_placeholders():
    tm = DocumentTranslationMemory()
    tm.store("Header", "{0} leaked", lang_in="en", lang_out="ar", engine_name="e")
    tm.store("Header", "Header", lang_in="en", lang_out="ar", engine_name="e")
    tm.store("Header", "", lang_in="en", lang_out="ar", engine_name="e")
    assert tm.lookup("Header", lang_in="en", lang_out="ar", engine_name="e") is None


# ---------------------------------------------------------------------------
# P1-11: Emails, ISO dates, invoice IDs, citations & BiDi isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        "billing.team+tax@acme-corp.org",
        "email@example.com",
        "2026-11-14",
        "14/11/2026",
        "PO #12345",
        "INV-2026-0041",
    ],
)
def test_numeral_shield_protects_email_date_and_invoice_ids(token):
    source = f"Send invoice {token} immediately."
    protected, mapping = _protect_numerals(source)
    assert protected == "Send invoice {0} immediately."
    assert mapping == {"{0}": token}
    assert _restore_numerals("أرسل الفاتورة {0} فوراً.", mapping) == f"أرسل الفاتورة {token} فوراً."


def test_pre_translate_terminology_keeps_email_local_part_intact():
    il = object.__new__(ILTranslator)
    il.translation_config = SimpleNamespace(lang_out="ar")
    out = il._pre_translate_terminology("E-Mail: email@bank.qa")
    assert "email@bank.qa" in out
    assert out.startswith("البريد الإلكتروني:")


def test_arabic_output_isolates_urls_dois_emails_and_numeric_citations():
    il = object.__new__(ILTranslator)
    il.translation_config = SimpleNamespace(lang_out="ar")
    out = il._normalize_arabic_output(
        "راجع [1, 3-5] والموقع https://example.org/p و10.1145/3292500.3330701 أو contact@org.io هنا"
    )
    assert "\u200e[1, 3-5]\u200e" in out
    assert "\u200ehttps://example.org/p\u200e" in out
    assert "\u200e10.1145/3292500.3330701\u200e" in out
    assert "\u200econtact@org.io\u200e" in out
    # No double LRM wrapping
    assert "\u200e\u200e" not in out


# ---------------------------------------------------------------------------
# P1-13 / P1-14 / P1-15: Preflight classification, AcroForms, links & QA
# ---------------------------------------------------------------------------


def test_preflight_classifies_mixed_broken_tounicode_scanned_acroform_and_signature(tmp_path):
    pdf_path = tmp_path / "forms_and_links.pdf"
    pdf = pymupdf.open()
    p0 = pdf.new_page(width=595, height=842)
    p0.insert_text((72, 72), "Born digital page with link.")
    p0.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 60, 200, 80), "uri": "https://example.org"})
    w_text = pymupdf.Widget()
    w_text.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w_text.field_name = "InvoiceNo"
    w_text.rect = pymupdf.Rect(72, 100, 220, 120)
    p0.add_widget(w_text)
    w_sig = pymupdf.Widget()
    w_sig.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w_sig.field_name = "AuthorizedSignature"
    w_sig.rect = pymupdf.Rect(72, 140, 220, 170)
    p0.add_widget(w_sig)

    pdf.new_page(width=595, height=842)  # simulated scanned/image-only
    pdf.new_page(width=595, height=842)  # simulated partial broken ToUnicode
    pdf.save(pdf_path)

    style = il_version_1.PdfStyle(font_id="F0", font_size=10)
    box = il_version_1.Box(x=10, y=10, x2=20, y2=20)
    born_chars = [il_version_1.PdfCharacter(char_unicode=c, box=box, pdf_style=style) for c in "Born digital valid text on page zero."]
    cid_chars = (
        [il_version_1.PdfCharacter(char_unicode="(cid:12)", box=box, pdf_style=style) for _ in range(4)]
        + [il_version_1.PdfCharacter(char_unicode=c, box=box, pdf_style=style) for c in "Normal words here too"]
    )
    docs = il_version_1.Document(
        page=[
            _make_il_page(0, [], chars=born_chars),
            il_version_1.Page(
                page_number=1,
                cropbox=il_version_1.Cropbox(box=il_version_1.Box(x=0, y=0, x2=595, y2=842)),
                pdf_paragraph=[],
                pdf_character=[],
                pdf_figure=[il_version_1.PdfFigure(box=il_version_1.Box(x=0, y=0, x2=595, y2=842))],
                pdf_font=[],
                pdf_xobject=[],
            ),
            _make_il_page(2, [], chars=cid_chars),
        ]
    )

    ledger = ContentLedger()
    report = inspect_document_preflight(docs, pdf, ledger=ledger)
    pdf.close()

    assert report["born_digital_page_count"] == 1
    assert report["scanned_page_count"] == 1
    assert report["broken_tounicode_page_count"] == 1
    assert report["has_acroform"] is True
    assert report["acroform_field_count"] == 2
    assert report["has_digital_signature"] is True
    assert report["source_link_count"] == 1
    assert "broken_tounicode_pages_detected" in report["warnings"]
    assert "digital_signatures_present" in report["warnings"]

    qa = audit_output_pdf(pdf_path, expected_page_count=3, ledger=ledger)
    assert qa["checked"] is True
    assert qa["output_link_count"] == 1
    assert qa["leaked_placeholder_pages"] == []

    built = ledger.build_report(input_file=str(pdf_path), lang_in="en", lang_out="ar")
    assert built["status"] == "needs_review"  # broken ToUnicode page flagged
    assert built["summary"]["broken_tounicode_page_count"] == 1
    assert built["output_qa"]["output_link_count"] == 1


def test_output_qa_flags_leaked_placeholder_to_quality_report(tmp_path):
    bad_pdf = tmp_path / "leaked.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Bad placeholder {0} left behind")
    pdf.save(bad_pdf)
    pdf.close()

    ledger = ContentLedger()
    qa = audit_output_pdf(bad_pdf, expected_page_count=1, ledger=ledger)
    assert qa["leaked_placeholder_pages"] == [0]
    built = ledger.build_report()
    assert built["status"] == "needs_review"
