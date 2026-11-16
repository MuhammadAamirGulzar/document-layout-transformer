"""Cross-module, model-free acceptance gates for the presentation candidate.

These tests deliberately combine translation integrity, semantic layout, saved-PDF
inspection, pagination, and contextual terminology.  They complement the focused
milestone tests and must stay independent of any benchmark filename or page number.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pymupdf

from babeldoc.format.pdf.document_il import (
    Box,
    Cropbox,
    Document,
    Page,
    PdfCharacter,
    PdfLine,
    PdfParagraph,
    PdfParagraphComposition,
    PdfStyle,
    VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.domain_glossary import apply_post_glossary
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
from babeldoc.format.pdf.document_il.utils.layout_semantics import is_standalone_code_text
from babeldoc.format.pdf.document_il.utils.preflight import audit_output_pdf
from babeldoc.translator import translator as tm


def _glyph(
    text: str,
    x: float,
    y: float,
    *,
    width: float = 6.0,
    height: float = 10.0,
) -> PdfCharacter:
    box = Box(x=x, y=y, x2=x + width, y2=y + height)
    return PdfCharacter(
        char_unicode=text,
        box=box,
        visual_bbox=VisualBbox(box=box),
        pdf_style=PdfStyle(font_id="base", font_size=10.0),
    )


def _paragraph(
    text: str,
    box: Box,
    *,
    label: str = "plain text",
    trace: dict | None = None,
    render_x: float | None = None,
) -> PdfParagraph:
    x = box.x if render_x is None else render_x
    chars = [_glyph(ch, x + idx * 6.0, box.y) for idx, ch in enumerate(text)]
    return PdfParagraph(
        box=box,
        unicode=text,
        layout_label=label,
        pipeline_trace=dict(trace or {}),
        pdf_paragraph_composition=[
            PdfParagraphComposition(pdf_line=PdfLine(pdf_character=chars))
        ],
    )


def _typesetter() -> Typesetting:
    typesetter = object.__new__(Typesetting)
    typesetter.is_arabic = True
    typesetter.translation_config = SimpleNamespace(
        lang_in="en",
        lang_out="ar",
        shared_context_cross_split_part=SimpleNamespace(),
    )
    typesetter._rendered_collision_resolved_pages = set()
    typesetter._rtl_mirrored_table_pages = set()
    return typesetter


def _write_pdf(path: Path, lines: list[tuple[float, float, str]]) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=400)
    for x, y, text in lines:
        page.insert_text((x, y), text)
    doc.save(path)
    doc.close()


def test_gate_preserves_mixed_immutable_content_without_overprotecting_prose():
    source = (
        "Track MCA-TS-0417 and invoice report-v2.json at /srv/reports/report-v2.json; "
        "email ops@example.org; total 12,450.00 USD; p < 0.001; confidence 95%; "
        "equation 98-34=64. The well-known AB-12-CD prose example stays translatable."
    )

    protected, mapping = tm._protect_numerals(source)
    values = set(mapping.values())

    for required in (
        "MCA-TS-0417",
        "report-v2.json",
        "/srv/reports/report-v2.json",
        "ops@example.org",
        "12,450.00",
        "p < 0.001",
        "95%",
        "98-34=64",
    ):
        assert required in values

    assert "well-known" in protected
    # The numeric fragment may still be protected, but the whole prose-like token
    # must not become an opaque passthrough atom.
    assert "AB-12-CD" not in values
    assert tm._restore_numerals(protected, mapping) == source

    assert tm._is_pure_passthrough("MCA-TS-0417") is True
    assert tm._is_pure_passthrough("ops@example.org") is True
    assert tm._is_pure_passthrough("AB-12-CD") is False


def test_gate_recognizes_structured_code_but_not_forms_or_prose():
    structured = (
        "SELECT id, total FROM invoices WHERE paid = 1;",
        '{\n  "invoice": "INV-2026-00417",\n  "paid": true\n}',
        "service:\n  host: 127.0.0.1\n  enabled: true",
    )
    for value in structured:
        assert is_standalone_code_text(value) is True

    ordinary = (
        "Invoice number: INV-2026-00417\nAmount due: USD 125.50",
        "Email: ops@example.org\nPhone: +974 44456371",
        "Select from the options below and sign the form.",
    )
    for value in ordinary:
        assert is_standalone_code_text(value) is False


def test_gate_degenerate_translation_fails_closed_and_is_not_cached(monkeypatch):
    hallucinated = "إِنَّهُ لَا يُمْكِنُ أَنْ يُمْكِنَ " * 4
    assert tm._is_degenerate_translation("Sample rate is 4.2%.", hallucinated) is True

    cache = Mock()
    cache.get.return_value = None
    monkeypatch.setattr(tm, "TranslationCache", lambda *args, **kwargs: cache)
    monkeypatch.setattr(tm._translate_rate_limiter, "wait", lambda: None)
    engine = tm.NLLBTranslator("en", "ar", model_name="model-free-test")
    monkeypatch.setattr(engine, "do_translate", Mock(return_value=hallucinated))
    monkeypatch.setattr(engine, "_generate_translation", Mock(return_value=hallucinated))

    output = engine.translate("Sample rate is 4.2%.")

    assert output == "Sample rate is 4.2%."
    assert engine.incomplete_count == 1
    cache.set.assert_not_called()


def test_gate_saved_pdf_and_il_audit_rejects_every_critical_defect(tmp_path: Path):
    source_pdf = tmp_path / "source.pdf"
    bad_pdf = tmp_path / "bad.pdf"
    clean_pdf = tmp_path / "clean.pdf"

    _write_pdf(source_pdf, [(40, 60, "MCA-TS-0417 total 125.50 p < 0.001")])
    _write_pdf(
        bad_pdf,
        [
            (350, 60, "OFFPAGEOVERFLOWTOKENTEXTOVER"),
            (50, 120, "Block Alpha line one"),
            (55, 122, "Block Beta overlapping line"),
            (50, 200, "Leaked <b1> value 99"),
        ],
    )
    _write_pdf(clean_pdf, [(50, 80, "MCA-TS-0417 total 125.50 p < 0.001")])

    escaping_code = _paragraph(
        "SQL",
        Box(40, 200, 90, 220),
        trace={"preserved_code": True},
        render_x=100,
    )
    bad_cell = _paragraph(
        "125.50",
        Box(40, 150, 90, 170),
        label="table_cell",
        trace={"table_id": "t1", "table_cell_overflow": True},
    )
    bad_il = Document(
        page=[
            Page(
                page_number=0,
                cropbox=Cropbox(box=Box(0, 0, 400, 400)),
                pdf_paragraph=[escaping_code, bad_cell],
            )
        ]
    )

    bad = audit_output_pdf(
        bad_pdf,
        expected_page_count=1,
        source_pdf_path=source_pdf,
        il_document=bad_il,
    )
    assert bad["checked"] is True
    assert bad["off_page_spans"]
    assert bad["overlap_collisions"]
    assert bad["leaked_placeholder_pages"] == [0]
    assert bad["region_escapes"]
    assert bad["table_cell_crossings"]
    assert {item["token"] for item in bad["missing_immutable_tokens"]} >= {
        "MCA-TS-0417",
        "125.50",
        "0.001",
    }

    safe_code = _paragraph(
        "SQL",
        Box(40, 200, 90, 220),
        trace={"preserved_code": True},
        render_x=45,
    )
    safe_cell = _paragraph(
        "125.50",
        Box(40, 150, 90, 170),
        label="table_cell",
        trace={"table_id": "t1"},
        render_x=45,
    )
    clean_il = Document(
        page=[
            Page(
                page_number=0,
                cropbox=Cropbox(box=Box(0, 0, 400, 400)),
                pdf_paragraph=[safe_code, safe_cell],
            )
        ]
    )
    clean = audit_output_pdf(
        clean_pdf,
        expected_page_count=1,
        source_pdf_path=source_pdf,
        il_document=clean_il,
    )
    assert clean["checked"] is True
    assert clean["total_qa_issues"] == 0


def test_gate_pagination_keeps_minor_overflow_and_moves_real_collision():
    typesetter = _typesetter()

    footnote = _paragraph("1 Source note", Box(50, 35, 130, 55), label="footnote")
    top = _paragraph("Main body", Box(50, 700, 130, 720))
    minor = _paragraph("Final line", Box(50, 60, 130, 72))
    minor_page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[top, minor, footnote],
    )
    minor_doc = Document(page=[minor_page], total_pages=1)

    assert typesetter._paginate_overflowing_pages(minor_doc) == 0
    assert len(minor_doc.page) == 1

    fixed_cell = _paragraph(
        "CELL",
        Box(50, 52, 100, 70),
        label="wired_table_cell_fixed_rtl",
    )
    heavy = _paragraph(
        "Line one\nLine two\nLine three\nLine four",
        Box(50, 42, 250, 92),
        trace={"page_overflow": True},
    )
    heavy_page = Page(
        page_number=0,
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=[fixed_cell, heavy, footnote],
    )
    heavy_doc = Document(page=[heavy_page], total_pages=1)

    assert typesetter._paginate_overflowing_pages(heavy_doc) == 1
    assert len(heavy_doc.page) == 2
    assert footnote in heavy_doc.page[0].pdf_paragraph
    assert fixed_cell in heavy_doc.page[0].pdf_paragraph
    assert heavy in heavy_doc.page[1].pdf_paragraph


def test_gate_contextual_glossary_repairs_terms_without_false_positives():
    positive_cases = (
        (
            "البرنامج الدولي وأولويات البرنامج والشركات",
            "The country programme and programme priorities and partnerships",
            "policy_un",
            ("البرنامج القطري", "الشراكات"),
        ),
        (
            "إطار المراقبة والتقييم للفئات الضعيفة",
            "Monitoring and evaluation framework for vulnerable groups",
            "policy_un",
            ("الرصد والتقييم", "للفئات الأكثر هشاشة"),
        ),
        (
            "معدلات الإهدار والتأخير بين الأطفال دون سن 0",
            "Wasting and stunting among children under 5",
            "policy_un",
            ("الهزال", "التقزّم", "دون سن الخامسة"),
        ),
        (
            "تفاصيل الإشعار وحساب المدفوع",
            "Invoice details and payer account",
            "invoice_form",
            ("الفاتورة", "الدافع"),
        ),
    )
    for output, source, domain, expected_terms in positive_cases:
        repaired = apply_post_glossary(output, source_text=source, domain=domain)
        for term in expected_terms:
            assert term in repaired

    negative_cases = (
        ("إهدار الوقت", "wasting time in the meeting", None),
        ("مراقبة معدل ضربات القلب", "heart rate monitoring", None),
        ("تنظيف ذاكرة التخزين المؤقت", "clearing the cache", None),
        ("أوتار الغيتار", "guitar strings", None),
    )
    for output, source, domain in negative_cases:
        assert apply_post_glossary(output, source_text=source, domain=domain) == output
