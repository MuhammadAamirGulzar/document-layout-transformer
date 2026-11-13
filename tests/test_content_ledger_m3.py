"""M3: per-block content ledger, disposition coverage, leak guards, live counts, and quality_report.json."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest

import server
from babeldoc.babeldoc_exception.BabelDOCException import ContentFilterError
from babeldoc.format.pdf.document_il.il_version_1 import (
    Box,
    Page,
    PdfCharacter,
    PdfFormula,
    PdfParagraph,
    PdfParagraphComposition,
    PdfSameStyleCharacters,
)
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
from babeldoc.format.pdf.document_il.utils.quality_report import (
    DISPOSITIONS,
    ContentLedger,
    detect_leaked_placeholders,
)
from babeldoc.format.pdf.translation_config import SharedContextCrossSplitPart
from babeldoc.translator import translator as tm


def _make_paragraph(text="Sample paragraph text.", debug_id=1, layout_label="body_text", vertical=False):
    comp = PdfParagraphComposition()
    same = PdfSameStyleCharacters()
    same.pdf_character = [
        PdfCharacter(char_unicode=ch, box=None, pdf_style=None, visual_bbox=None)
        for ch in text
    ]
    comp.pdf_same_style_characters = same
    para = PdfParagraph(
        unicode=text,
        debug_id=debug_id,
        layout_label=layout_label,
        vertical=vertical,
        pdf_paragraph_composition=[comp],
    )
    return para


def _make_il_translator(side_effect=None):
    shared = SharedContextCrossSplitPart()
    config = SimpleNamespace(
        lang_in="en",
        lang_out="ar",
        debug=False,
        min_text_length=5,
        disable_rich_text_translate=True,
        auto_extract_glossary=False,
        shared_context_cross_split_part=shared,
        raise_if_cancelled=lambda: None,
    )
    il = object.__new__(ILTranslator)
    il.translation_config = config
    il.font_mapper = None
    il.shared_context_cross_split_part = shared
    il.support_llm_translate = False
    il.use_as_fallback = False
    il.add_content_filter_hint = Mock()
    il._pre_translate_terminology = lambda t: t
    il._normalize_arabic_output = lambda t: t
    il.shape_arabic_text = lambda t: t
    il.translate_engine = Mock()
    il.translate_engine.name = "mock-engine"
    il.translate_engine.untranslated_count = 0
    il.translate_engine.incomplete_count = 0
    il.translate_engine.get_formular_placeholder.side_effect = (
        lambda pid: (f"<b{pid}>", rf"<\s*b\s*{pid}\s*>")
    )
    if side_effect is not None:
        il.translate_engine.translate.side_effect = side_effect
    return il, shared.content_ledger


def _page(num=1):
    p = Page()
    p.page_number = num
    return p


def _request(method, path):
    async def run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path)

    return asyncio.run(run())


def test_detect_leaked_placeholders_ignores_literal_source_tokens():
    assert detect_leaked_placeholders(["Text <b1> and {0} and </style>"], "Text literal") == [
        "</style>",
        "<b1>",
        "{0}",
    ]
    assert detect_leaked_placeholders(["Code prints {0}."], "Code prints {0}.") == []


def test_il_translator_records_all_seven_dispositions(tmp_path):
    state = {"mode": "normal"}

    def translate_fn(text, **kwargs):
        mode = state["mode"]
        if mode == "filter":
            raise ContentFilterError("blocked")
        if mode == "exception":
            raise RuntimeError("boom")
        if mode == "unchanged":
            return text
        if mode == "empty":
            return "   "
        if mode == "leaked":
            return "النص المترجم <b2> {0}"
        return "نص مترجم جيد"

    il, ledger = _make_il_translator(side_effect=translate_fn)
    page = _page(3)

    # 1. translated
    p1 = _make_paragraph("Normal English sentence.", debug_id=1)
    il.translate_paragraph(p1, page, pbar=Mock(), tracker=Mock())
    assert p1.pipeline_trace["disposition"] == "translated"

    # 2. passthrough (vertical + pure numeric)
    p2 = _make_paragraph("Side label", debug_id=2, vertical=True)
    il.translate_paragraph(p2, page, pbar=Mock(), tracker=Mock())
    assert p2.pipeline_trace["disposition"] == "passthrough"

    # 3. skipped-text-none (empty composition)
    p3 = PdfParagraph(unicode="", debug_id=3, layout_label="body_text", pdf_paragraph_composition=[])
    il.translate_paragraph(p3, page, pbar=Mock(), tracker=Mock())
    assert p3.pipeline_trace["disposition"] == "skipped-text-none"

    # 4. unchanged (must also preserve original pdf_same_style_characters via fixed post_translate_paragraph)
    state["mode"] = "unchanged"
    p4 = _make_paragraph("Keep identical heading", debug_id=4)
    orig_comp = p4.pdf_paragraph_composition
    il.translate_paragraph(p4, page, pbar=Mock(), tracker=Mock())
    assert p4.pipeline_trace["disposition"] == "unchanged"
    assert p4.pdf_paragraph_composition is orig_comp

    # 5. content-filter
    state["mode"] = "filter"
    p5 = _make_paragraph("Sensitive clause.", debug_id=5)
    il.translate_paragraph(p5, page, pbar=Mock(), tracker=Mock())
    assert p5.pipeline_trace["disposition"] == "content-filter"

    # 6. untranslated-exception
    state["mode"] = "exception"
    p6 = _make_paragraph("Broken clause.", debug_id=6)
    il.translate_paragraph(p6, page, pbar=Mock(), tracker=Mock())
    assert p6.pipeline_trace["disposition"] == "untranslated-exception"

    # 7a. incomplete via empty translation (preserves original unicode + composition)
    state["mode"] = "empty"
    p7 = _make_paragraph("Must not disappear.", debug_id=7)
    il.translate_paragraph(p7, page, pbar=Mock(), tracker=Mock())
    assert p7.pipeline_trace["disposition"] == "incomplete"
    assert p7.pipeline_trace["reason"] == "empty_translation"
    assert p7.unicode == "Must not disappear."

    # 7b. incomplete via leaked placeholder
    state["mode"] = "leaked"
    p8 = _make_paragraph("Sentence without placeholders.", debug_id=8)
    il.translate_paragraph(p8, page, pbar=Mock(), tracker=Mock())
    assert p8.pipeline_trace["disposition"] == "incomplete"
    assert p8.pipeline_trace["leaked_placeholders"] == ["<b2>", "{0}"]

    snap = ledger.snapshot()
    assert snap["total_blocks"] == 8
    assert set(snap["counts"].keys()) == set(DISPOSITIONS)
    assert snap["counts"] == {
        "translated": 1,
        "passthrough": 1,
        "unchanged": 1,
        "untranslated-exception": 1,
        "content-filter": 1,
        "skipped-text-none": 1,
        "incomplete": 2,
    }
    assert snap["untranslated_count"] == 2
    assert snap["incomplete_count"] == 2
    assert snap["leaked_placeholder_blocks"] == 1
    assert snap["missing_content_blocks"] == 1

    report_path = ledger.write_json(
        tmp_path / "quality_report.json",
        input_file="sample.pdf",
        lang_in="en",
        lang_out="ar",
        engine="mock-engine",
    )
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["status"] == "needs_review"
    assert data["summary"]["total_blocks"] == 8
    assert len(data["flagged_blocks"]) == 5


def test_real_engine_internal_error_records_untranslated_exception_once(monkeypatch):
    monkeypatch.setattr(tm, "TranslationCache", lambda *args, **kwargs: Mock(get=Mock(return_value=None)))
    monkeypatch.setattr(tm._translate_rate_limiter, "wait", lambda: None)

    il, ledger = _make_il_translator()
    engine = tm.NLLBTranslator("en", "ar", model_name="nllb-3.3b", ignore_cache=True)
    engine._load_model = lambda: None
    engine._generate_translation = Mock(side_effect=RuntimeError("OOM"))
    il.translate_engine = engine

    # Two sentences in one paragraph (> 150 chars); both fail internally in engine.
    long_text = (
        "First sentence exceeds one hundred characters easily when combined with more analytical prose. "
        "Second sentence also fails inside the Seq2Seq backend."
    )
    para = _make_paragraph(long_text, debug_id=10)
    il.translate_paragraph(para, _page(1), pbar=Mock(), tracker=Mock())

    assert para.pipeline_trace["disposition"] == "untranslated-exception"
    snap = ledger.snapshot()
    assert snap["untranslated_count"] == 1


def test_missing_formula_placeholder_flags_incomplete():
    il, ledger = _make_il_translator(side_effect=lambda t, **k: "معادلة بدون عنصر نائب")
    para = PdfParagraph(unicode="Equation x", debug_id=11, layout_label="body_text")
    comp1 = PdfParagraphComposition()
    same = PdfSameStyleCharacters()
    same.pdf_character = [
        PdfCharacter(
            char_unicode=c,
            box=Box(x=10 + i * 5, y=10, x2=15 + i * 5, y2=20),
            pdf_style=None,
            visual_bbox=None,
        )
        for i, c in enumerate("Equation ")
    ]
    comp1.pdf_same_style_characters = same
    comp2 = PdfParagraphComposition()
    comp2.pdf_formula = PdfFormula(
        pdf_character=[
            PdfCharacter(
                char_unicode="x",
                box=Box(x=60, y=10, x2=65, y2=20),
                pdf_style=None,
                visual_bbox=None,
            )
        ]
    )
    comp2.pdf_formula.box = Box(x=60, y=10, x2=65, y2=20)
    para.pdf_paragraph_composition = [comp1, comp2]

    il.translate_paragraph(para, _page(2), pbar=Mock(), tracker=Mock())

    assert para.pipeline_trace["disposition"] == "incomplete"
    assert para.pipeline_trace["missing_placeholders"] == ["<b1>"]
    assert ledger.snapshot()["leaked_placeholder_blocks"] == 1


def test_server_exposes_live_counts_and_quality_report(tmp_path, monkeypatch):
    (tmp_path / "sandbox").mkdir()
    monkeypatch.setattr(server, "TEMP_DIR", tmp_path / "sandbox")
    sessions = {}
    monkeypatch.setattr(server, "translation_sessions", sessions)

    ledger = ContentLedger()
    ledger.record(block_key=1, page=1, debug_id=1, disposition="translated", source_text="Hello", translated_text="مرحبا")
    ledger.record(block_key=2, page=1, debug_id=2, disposition="incomplete", reason="truncated", source_text="Long")

    sid = str(uuid4())
    sessions[sid] = {
        "status": "processing",
        "progress": 42,
        "stage": "Translating Content",
        "_config": SimpleNamespace(
            input_file="doc.pdf",
            lang_in="en",
            lang_out="ar",
            translator=SimpleNamespace(untranslated_count=0, incomplete_count=1),
            shared_context_cross_split_part=SimpleNamespace(content_ledger=ledger),
        ),
    }

    status_resp = _request("GET", f"/translate/status/{sid}")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["untranslated_count"] == 0
    assert body["incomplete_count"] == 1
    assert body["dispositions"]["translated"] == 1
    assert body["dispositions"]["incomplete"] == 1

    report_resp = _request("GET", f"/translate/report/{sid}")
    assert report_resp.status_code == 200
    report = report_resp.json()
    assert report["status"] == "needs_review"
    assert report["summary"]["total_blocks"] == 2
