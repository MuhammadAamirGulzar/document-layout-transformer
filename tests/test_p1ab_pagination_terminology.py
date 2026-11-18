"""P1-A and P1-B tests: pagination/footnotes/bookmarks and controlled terminology with counterexamples."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
    PdfRectangle,
    PdfStyle,
    VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.domain_glossary import (
    apply_post_glossary,
    apply_pre_glossary,
)
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
from babeldoc.format.pdf.high_level import migrate_toc
from babeldoc.translator import translator as tm


def _glyph(ch: str, x: float, y: float, w: float = 6.0, h: float = 10.0) -> PdfCharacter:
    box = Box(x=x, y=y, x2=x + w, y2=y + h)
    return PdfCharacter(
        char_unicode=ch,
        box=box,
        visual_bbox=VisualBbox(box=box),
        pdf_style=PdfStyle(font_id="base", font_size=10.0),
    )


def _para(text: str, x: float, y: float, y2: float, label: str = "plain text", trace: dict | None = None) -> PdfParagraph:
    chars = [_glyph(ch, x + i * 6.0, y, h=max(8.0, y2 - y)) for i, ch in enumerate(text)]
    return PdfParagraph(
        box=Box(x, y, x + max(10.0, len(text) * 6.0), y2),
        unicode=text,
        layout_label=label,
        pipeline_trace=dict(trace or {}),
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=PdfLine(pdf_character=chars))],
    )


def _typesetter() -> Typesetting:
    ts = object.__new__(Typesetting)
    ts.is_arabic = True
    ts.translation_config = SimpleNamespace(
        lang_in="en",
        lang_out="ar",
        shared_context_cross_split_part=SimpleNamespace(),
    )
    ts._rendered_collision_resolved_pages = set()
    ts._rtl_mirrored_table_pages = set()
    ts._reported_layout_issue_keys = set()
    ts._layout_issues = []
    return ts


def test_minor_footnote_buffer_touch_avoids_nearly_empty_continuation_page():
    ts = _typesetter()
    # Footnote occupies y=35..55 -> zones['body_floor'] = 65.0, footnote_ink_top = 55.0.
    # A short 1-line body paragraph ends at y=60.0 (5pt clear of footnote ink, only dipping into the 10pt cushion).
    fn = _para("1 Source note", 50, 35, 55, label="footnote")
    body_top = _para("Main text upper", 50, 700, 720)
    body_low = _para("Single last body line", 50, 60, 72)
    page = Page(
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        page_number=0,
        pdf_paragraph=[body_top, body_low, fn],
    )
    doc = Document(page=[page], total_pages=1)

    created = ts._paginate_overflowing_pages(doc)
    assert created == 0
    assert len(doc.page) == 1
    assert len(doc.page[0].pdf_paragraph) == 3


def test_real_multiline_overflow_still_creates_continuation_page_and_keeps_table_indivisible():
    ts = _typesetter()
    fn = _para("1 Footnote stays pinned", 50, 35, 55, label="footnote")
    fixed_cell = _para("CELL1", 50, 52, 70, label="wired_table_cell_fixed_rtl")
    # Real heavy overflow block spanning y=42..92 (50pt tall, crashing directly into footnote band!)
    overflow_lines = [
        PdfParagraphComposition(
            pdf_line=PdfLine(
                pdf_character=[_glyph("A", 50 + i * 6, 42 + row * 12) for i in range(10)]
            )
        )
        for row in range(4)
    ]
    heavy_body = PdfParagraph(
        box=Box(50, 42, 250, 92),
        unicode="Line1\nLine2\nLine3\nLine4",
        layout_label="plain text",
        pipeline_trace={"page_overflow": True},
        pdf_paragraph_composition=overflow_lines,
    )
    page = Page(
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        page_number=0,
        pdf_paragraph=[fixed_cell, heavy_body, fn],
    )
    doc = Document(page=[page], total_pages=1)

    created = ts._paginate_overflowing_pages(doc)
    assert created == 1
    assert len(doc.page) == 2
    assert fn in doc.page[0].pdf_paragraph
    assert fixed_cell in doc.page[0].pdf_paragraph
    assert heavy_body in doc.page[1].pdf_paragraph


def test_pagination_splits_tall_rendered_paragraph_without_losing_glyphs():
    ts = _typesetter()
    footnote = _para("1 Source note", 50, 35, 55, label="footnote")
    lines = [f"Line {i:02d}" for i in range(20)]
    compositions = [
        PdfParagraphComposition(pdf_character=_glyph(char, 50 + col * 6, 258 - row * 12))
        for row, line in enumerate(lines)
        for col, char in enumerate(line)
    ]
    body = PdfParagraph(
        box=Box(50, 30, 92, 268),
        unicode="\n".join(lines),
        layout_label="body",
        debug_id="long-body",
        pdf_paragraph_composition=compositions,
    )
    page = Page(
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        page_number=0,
        pdf_paragraph=[body, footnote],
    )
    doc = Document(page=[page], total_pages=1)

    assert ts._paginate_overflowing_pages(doc) == 1

    assert doc.total_pages == 2
    assert doc.page[0].pdf_paragraph == [body, footnote]
    assert [p.debug_id for p in doc.page[1].pdf_paragraph] == ["long-body_cont"]
    continuation = doc.page[1].pdf_paragraph[0]
    # The last three lines fall below the 65 pt footnote clearance boundary.
    kept_glyphs = sum(len(line) for line in lines[:-3])
    assert body.pdf_paragraph_composition == compositions[:kept_glyphs]
    assert continuation.pdf_paragraph_composition == compositions[kept_glyphs:]
    assert continuation.box.y > footnote.box.y2


def test_overflow_stays_in_its_column_and_does_not_detach_a_framed_field():
    ts = _typesetter()
    overflow = _para("Left overflow", 45, 175, 190, trace={"page_overflow": True})
    left_next = _para("Left continuation", 45, 150, 163)
    right_field = _para("Reference: 12345", 330, 145, 157, trace={"page_overflow": True})
    right_text = _para("Independent column", 330, 100, 113)
    page = Page(
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        page_number=0,
        pdf_rectangle=[PdfRectangle(box=Box(310, 136, 500, 170))],
        pdf_paragraph=[overflow, left_next, right_field, right_text],
    )
    doc = Document(page=[page], total_pages=1)

    assert ts._paginate_overflowing_pages(doc) == 1
    assert page.pdf_paragraph == [right_field, right_text]
    assert doc.page[1].pdf_paragraph == [overflow, left_next]
    assert [issue["kind"] for issue in ts._layout_issues] == ["contained_region_overflow"]


def test_migrate_toc_preserves_bookmarks_across_continuation_pages(tmp_path: Path):
    src_pdf = tmp_path / "src.pdf"
    mono_pdf = tmp_path / "mono.pdf"

    d = pymupdf.open()
    for i in range(2):
        p = d.new_page(width=300, height=300)
        p.insert_text((30, 50), f"Source page {i+1}")
    d.set_toc([[1, "Section One", 1], [1, "Section Two", 2]])
    d.save(src_pdf)
    d.close()

    # Simulated rendered mono PDF where a continuation page was inserted after source page 1 (index 1 -> page 2 of 3)
    d = pymupdf.open()
    for i in range(3):
        p = d.new_page(width=300, height=300)
        p.insert_text((30, 50), f"Out page {i+1}")
    d.save(mono_pdf)
    d.close()

    cfg = SimpleNamespace(
        input_file=str(src_pdf),
        only_include_translated_page=False,
        should_translate_page=lambda n: True,
        use_alternating_pages_dual=False,
        dual_translate_first=False,
        skip_clean=True,
        shared_context_cross_split_part=SimpleNamespace(continuation_pages={1: 0}),
        get_working_file_path=lambda name: str(tmp_path / name),
    )
    res = SimpleNamespace(
        mono_pdf_path=mono_pdf,
        no_watermark_mono_pdf_path=None,
        dual_pdf_path=None,
        no_watermark_dual_pdf_path=None,
    )
    migrate_toc(cfg, res)

    out_doc = pymupdf.open(mono_pdf)
    toc = out_doc.get_toc()
    out_doc.close()
    assert toc == [[1, "Section One", 1], [1, "Section Two", 3]]


def test_policy_un_and_technical_terminology_with_counterexamples():
    # Policy / UN fixes
    assert apply_post_glossary("البرنامج الدولي يحقق تقدم", source_text="The country programme achieves progress", domain="policy_un") == "البرنامج القطري يحقق تقدم"
    assert "أولويات البرنامج والشراكات" in apply_post_glossary("أولويات البرنامج والشركات", source_text="Programme priorities and partnerships", domain="policy_un")
    assert "حصائل اليونيسف" in apply_post_glossary("جزء من اليونيسف", source_text="UNICEF outcomes", domain="policy_un")
    assert "الأوضاع الهشّة" in apply_post_glossary("في الأوضاع الضعيفة", source_text="children in vulnerable situations", domain="policy_un")
    assert "الرصد والتقييم" in apply_post_glossary("إطار المراقبة والتقييم", source_text="Programme monitoring framework", domain="policy_un")
    assert "العائدين" in apply_post_glossary("دعم المعتادين والنازحين", source_text="support for returnees and IDPs", domain="policy_un")
    assert "تنمية الطفولة المبكرة" in apply_post_glossary("تطوير الطفولة المبكرة", source_text="Early childhood development (ECD)", domain="policy_un")
    assert "التعليم ما قبل الابتدائي" in apply_post_glossary("ما قبل الأولية", source_text="pre-primary education", domain="policy_un")
    assert "الهزال" in apply_post_glossary("معدلات الإهدار المرتفعة", source_text="high wasting among children under 5", domain="policy_un")
    assert "التقزّم" in apply_post_glossary("معدل التأخير الغذائي", source_text="nutrition stunting rates", domain="policy_un")
    assert "دون سن الخامسة" in apply_post_glossary("الأطفال دون سن 0", source_text="children under 5 years", domain="policy_un")
    assert "عدم إلحاق الضرر" in apply_post_glossary("مبدأ لن يفعل أي شيء الضرر", source_text="do no harm principle", domain="policy_un")
    assert "MICS" in apply_post_glossary("بيانات ميكس لعام 2018", source_text="2018 MICS survey", domain="policy_un")
    assert "هيئة رعاية الطفولة" in apply_post_glossary("توصيات مفوضية رعاية الطفل", source_text="Child Welfare Commission", domain="policy_un")
    assert apply_pre_glossary("ibid.", domain="policy_un") == "المرجع نفسه"
    assert "المرجع نفسه" in apply_post_glossary("انظر إيبيد ص 4", source_text="See ibid. p. 4", domain="policy_un")

    # Technical / financial terms & "#" passthrough
    assert tm._is_pure_passthrough("#") is True
    assert apply_pre_glossary("Qty", domain="invoice_form") == "الكمية"
    assert "قائمة المصطلحات" in apply_post_glossary("الملحق ب: الجلسة", source_text="Appendix B: Glossary")
    assert "الدافع" in apply_post_glossary("حساب المدفوع", source_text="payer account")
    assert "الفاتورة" in apply_post_glossary("تفاصيل الإشعار", source_text="invoice details", domain="invoice_form")
    assert "المقاصة" in apply_post_glossary("غرفة التنظيف البنكية", source_text="bank clearing house", domain="financial_banking")
    assert "رقم التحقق" in apply_post_glossary("خوارزمية تولد المال التحقق", source_text="check digit algorithm", domain="financial_banking")
    assert "الكمية" in apply_post_glossary("القيادة 3", source_text="Qty 3", domain="invoice_form")
    assert "المعادلة" in apply_post_glossary("حل التساوي", source_text="solve the equation")
    assert "وكيل برمجي" in apply_post_glossary("يقوم عامل الذكاء الاصطناعي بالتنفيذ", source_text="The AI autonomous agent executes")
    assert "سلاسل نصية" in apply_post_glossary("تحليل أوتار UTF-8", source_text="parse UTF-8 strings cleanly")

    # Ambiguous counterexamples MUST NOT falsely rewrite!
    assert apply_post_glossary("أوتار الغيتار", source_text="tighten the guitar strings") == "أوتار الغيتار"
    assert apply_post_glossary("إهدار الوقت في الاجتماع", source_text="wasting time in the meeting") == "إهدار الوقت في الاجتماع"
    assert apply_post_glossary("تنظيف ذاكرة التخزين المؤقت", source_text="clearing the cache before restart") == "تنظيف ذاكرة التخزين المؤقت"
    assert apply_post_glossary("عامل التغيير الاجتماعي", source_text="an agent of change in society") == "عامل التغيير الاجتماعي"
    assert apply_post_glossary("مراقبة معدل ضربات القلب", source_text="continuous heart rate monitoring") == "مراقبة معدل ضربات القلب"
