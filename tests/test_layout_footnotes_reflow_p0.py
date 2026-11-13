"""P0-1: margin containment, footnote zone preservation, continuation pagination, and clip reporting."""
from __future__ import annotations

import pymupdf

from babeldoc.format.pdf.document_il.backend.pdf_creater import PDFCreater
from babeldoc.format.pdf.document_il.il_version_1 import (
    BaseOperations,
    Box,
    Cropbox,
    Document,
    Mediabox,
    Page,
    PdfCharacter,
    PdfCurve,
    PdfFont,
    PdfParagraph,
    PdfParagraphComposition,
    PdfSameStyleUnicodeCharacters,
    PdfStyle,
)
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
from babeldoc.format.pdf.high_level import TRANSLATE_STAGES
from babeldoc.format.pdf.translation_config import (
    SharedContextCrossSplitPart,
    TranslationConfig,
    WatermarkOutputMode,
)
from babeldoc.progress_monitor import ProgressMonitor


def _make_config(
    *,
    input_file: str = "",
    output_dir: str = "",
    use_alternating_pages_dual: bool = False,
    with_progress: bool = False,
) -> TranslationConfig:
    cfg = TranslationConfig(
        translator=None,
        input_file=input_file,
        output_dir=output_dir or None,
        working_dir=output_dir or None,
        lang_in="en",
        lang_out="ar",
        doc_layout_model=None,
        skip_clean=True,
        watermark_output_mode=WatermarkOutputMode.NoWatermark,
        use_alternating_pages_dual=use_alternating_pages_dual,
        progress_monitor=ProgressMonitor(stages=TRANSLATE_STAGES) if with_progress else None,
    )
    shared = SharedContextCrossSplitPart()
    shared.content_ledger = ContentLedger()
    cfg.shared_context_cross_split_part = shared
    return cfg


def _make_paragraph(
    text: str,
    box: Box,
    *,
    debug_id: int,
    layout_label: str = "plain text",
    font_id: str = "F1",
    font_size: float = 11.0,
) -> PdfParagraph:
    return PdfParagraph(
        debug_id=debug_id,
        box=box,
        xobj_id=-1,
        unicode=text,
        layout_label=layout_label,
        pdf_style=PdfStyle(font_id=font_id, font_size=font_size),
        pdf_paragraph_composition=[
            PdfParagraphComposition(
                pdf_same_style_unicode_characters=PdfSameStyleUnicodeCharacters(
                    unicode=text,
                    pdf_style=PdfStyle(font_id=font_id, font_size=font_size),
                )
            )
        ],
    )


def _make_page(
    paragraphs: list[PdfParagraph],
    *,
    page_number: int = 0,
    width: float = 595.0,
    height: float = 842.0,
    curves: list[PdfCurve] | None = None,
) -> Page:
    return Page(
        mediabox=Mediabox(box=Box(0.0, 0.0, width, height)),
        cropbox=Cropbox(box=Box(0.0, 0.0, width, height)),
        pdf_font=[PdfFont(font_id="F1", name="Helvetica", xref_id=1)],
        pdf_paragraph=paragraphs,
        pdf_curve=list(curves or []),
        base_operations=BaseOperations(value=""),
        page_number=page_number,
        unit="pt",
    )


def test_get_max_bottom_space_enforces_margin_and_footnote_band_on_zero_crop_origin():
    """Expanding body text must stop above margins and footnotes even when horizontally disjoint."""
    ts = Typesetting(_make_config())
    body_left = _make_paragraph(
        "فقرة أساسية في العمود الأيسر",
        Box(50.0, 180.0, 250.0, 320.0),
        debug_id=1,
    )
    no_fn_page = _make_page([body_left])
    zones_no_fn = ts._get_page_vertical_zones(no_fn_page)
    assert zones_no_fn["page_floor"] >= 20.0
    assert ts.get_max_bottom_space(body_left.box, no_fn_page, paragraph=body_left) == zones_no_fn["body_floor"]

    # Footnote sits only on the right half at the bottom; left-column body box must
    # still respect the full-width footnote band ceiling plus rule clearance.
    footnote_right = _make_paragraph(
        "1 حاشية أسفل الصفحة",
        Box(320.0, 48.0, 545.0, 88.0),
        debug_id=2,
        layout_label="footnote",
    )
    rule = PdfCurve(box=Box(320.0, 96.0, 545.0, 96.8))
    fn_page = _make_page([body_left, footnote_right], curves=[rule])
    max_bottom = ts.get_max_bottom_space(body_left.box, fn_page, paragraph=body_left)
    assert max_bottom >= 106.0
    # The footnote itself can still expand down to content_floor, not above body_floor.
    assert ts.get_max_bottom_space(footnote_right.box, fn_page, paragraph=footnote_right) < max_bottom


def test_footnote_anchors_and_collision_firewall_preserve_footnotes():
    """Body collisions never push footnotes off-page and footnote anchors are linked."""
    ts = Typesetting(_make_config())
    body = _make_paragraph(
        "تشير النتائج السابقة إلى استقرار النظام [1] وتطابق المعايير [2].",
        Box(60.0, 90.0, 535.0, 210.0),
        debug_id=10,
    )
    # Intentionally place footnotes slightly overlapping each other and close to bottom footer.
    fn1 = _make_paragraph(
        "1 الحاشية الأولى لتوضيح الاختبار.",
        Box(60.0, 55.0, 535.0, 82.0),
        debug_id=11,
        layout_label="footnote",
    )
    fn2 = _make_paragraph(
        "2 الحاشية الثانية الملاصقة للهامش.",
        Box(60.0, 12.0, 535.0, 62.0),
        debug_id=12,
        layout_label="footnote",
    )
    footer = _make_paragraph(
        "صفحة 1",
        Box(240.0, 14.0, 355.0, 28.0),
        debug_id=13,
        layout_label="footer",
    )
    page = _make_page([body, fn1, fn2, footer])
    zones = ts._get_page_vertical_zones(page)

    ts.render_page(page)

    fn1_box = ts._rendered_text_box(fn1) or fn1.box
    fn2_box = ts._rendered_text_box(fn2) or fn2.box
    footer_box = ts._rendered_text_box(footer) or footer.box
    assert fn2_box.y >= zones["content_floor"] - 1.0
    assert fn2_box.y >= footer_box.y2 - 0.5
    assert fn1_box.y >= fn2_box.y2 - 0.5

    assert (fn1.pipeline_trace or {}).get("zone") == "footnote"
    assert (fn1.pipeline_trace or {}).get("footnote_marker") == "1"
    assert 10 in ((fn1.pipeline_trace or {}).get("anchor_paragraph_ids") or [])
    assert (fn2.pipeline_trace or {}).get("footnote_marker") == "2"
    assert (body.pipeline_trace or {}).get("referenced_footnotes") == ["1", "2"]


def test_continuation_pagination_writes_valid_mono_and_dual_pdfs(tmp_path):
    """Overflowing prose moves onto continuation pages without dropping content or breaking dual PDFs."""
    src_pdf_path = tmp_path / "source_two_page.pdf"
    src_doc = pymupdf.open()
    for idx in range(2):
        p = src_doc.new_page(width=595, height=842)
        p.insert_text((60, 100), f"Original Page {idx + 1}", fontsize=12)
    src_doc.save(src_pdf_path)
    src_doc.close()

    def _run_pipeline(*, use_alternating: bool) -> TranslationConfig:
        out_dir = tmp_path / ("alt" if use_alternating else "sbs")
        out_dir.mkdir(parents=True, exist_ok=True)
        cfg = _make_config(
            input_file=str(src_pdf_path),
            output_dir=str(out_dir),
            use_alternating_pages_dual=use_alternating,
            with_progress=True,
        )
        # Page 0: top prose + colliding second prose block pushed deep into bottom/footnote zone
        # + third prose block directly inside bottom band, alongside a fixed footnote and footer.
        p0_top = _make_paragraph(
            "المقدمة العامة للتقرير الفني " * 8,
            Box(50.0, 560.0, 545.0, 780.0),
            debug_id=101,
        )
        p0_mid = _make_paragraph(
            "الفقرة المتداخلة التي ستنتقل إلى صفحة التتمة لكي لا تغطي الحواشي " * 6,
            Box(50.0, 260.0, 545.0, 620.0),
            debug_id=102,
        )
        p0_low = _make_paragraph(
            "سطر أخير في ذيل الصفحة الأولى يجب نقله مع التتمة.",
            Box(50.0, 50.0, 545.0, 120.0),
            debug_id=103,
        )
        p0_fn = _make_paragraph(
            "1 مصدر بيانات الجدول الأول.",
            Box(50.0, 52.0, 545.0, 88.0),
            debug_id=104,
            layout_label="footnote",
        )
        p0_footer = _make_paragraph(
            "تذييل 1",
            Box(250.0, 14.0, 345.0, 30.0),
            debug_id=105,
            layout_label="footer",
        )
        p1_body = _make_paragraph(
            "محتوى الصفحة الثانية الأصلية.",
            Box(50.0, 680.0, 545.0, 740.0),
            debug_id=201,
        )
        il_doc = Document(
            page=[
                _make_page([p0_top, p0_mid, p0_low, p0_fn, p0_footer], page_number=0),
                _make_page([p1_body], page_number=1),
            ],
            total_pages=2,
        )
        Typesetting(cfg).typesetting_document(il_doc)
        assert len(il_doc.page) >= 3
        assert il_doc.page[1].continuation_of_page == 0
        assert cfg.shared_context_cross_split_part.continuation_pages == {1: 0}
        assert il_doc.page[0].pdf_paragraph and any(p.debug_id == 104 for p in il_doc.page[0].pdf_paragraph)
        # Original page 1 moved cleanly to index 2 with its paragraph intact.
        assert il_doc.page[2].continuation_of_page is None
        assert any(p.debug_id == 201 for p in il_doc.page[2].pdf_paragraph)

        # Ensure every rendered block across all pages stays cleanly inside cropbox without clipping.
        for final_page in il_doc.page:
            assert all(not (p.pipeline_trace or {}).get("clipped") for p in final_page.pdf_paragraph)

        result = PDFCreater(str(src_pdf_path), il_doc, cfg, {}).write(cfg)
        with pymupdf.open(result.mono_pdf_path) as mono_pdf:
            assert mono_pdf.page_count == len(il_doc.page)
            cont_text = mono_pdf[1].get_text()
            assert cont_text.strip()
        with pymupdf.open(result.dual_pdf_path) as dual_pdf:
            expected_dual_pages = len(il_doc.page) * (2 if use_alternating else 1)
            assert dual_pdf.page_count == expected_dual_pages
        return cfg

    _run_pipeline(use_alternating=False)
    _run_pipeline(use_alternating=True)


def test_unresolvable_clipping_is_reported_to_quality_report_ledger():
    """Unrendered or off-page paragraphs must be flagged in pipeline_trace and ContentLedger."""
    cfg = _make_config()
    ts = Typesetting(cfg)
    missing_glyph_para = PdfParagraph(
        debug_id=301,
        box=Box(50.0, 400.0, 300.0, 440.0),
        unicode="نص مفقود لم يتم رسمه",
        pdf_paragraph_composition=[],
    )
    offpage_para = PdfParagraph(
        debug_id=302,
        box=Box(-25.0, 200.0, 100.0, 240.0),
        unicode="ن",
        pdf_paragraph_composition=[
            PdfParagraphComposition(
                pdf_character=PdfCharacter(
                    char_unicode="ن",
                    box=Box(-20.0, 210.0, -5.0, 225.0),
                )
            )
        ],
    )
    page = _make_page([missing_glyph_para, offpage_para])

    problems = ts._report_clipped_content(page, record_to_ledger=True)
    assert len(problems) == 2
    assert (offpage_para.pipeline_trace or {}).get("clipped") is True
    assert "off_page" in ((offpage_para.pipeline_trace or {}).get("clip_reasons") or [])

    report = cfg.shared_context_cross_split_part.content_ledger.build_report()
    assert report["status"] == "needs_review"
    assert report["summary"]["clipped_content_count"] == 2
    assert len(report["layout_issues"]) == 2
