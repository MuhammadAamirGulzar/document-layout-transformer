"""Priority 1 acceptance tests: block-level translation completeness & semantic retry."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from babeldoc.format.pdf.document_il import Box, Page, PdfParagraph
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
from babeldoc.translator import translator as tm


def _nllb_with_cache(monkeypatch, first: str, retry_fn):
    cache_store = {}
    cache = Mock()
    cache.get.side_effect = lambda k: cache_store.get(k)
    cache.set.side_effect = lambda k, v: cache_store.__setitem__(k, v)
    monkeypatch.setattr(tm, "TranslationCache", lambda *args, **kwargs: cache)
    monkeypatch.setattr(tm._translate_rate_limiter, "wait", lambda: None)

    engine = tm.NLLBTranslator("en", "ar", model_name="nllb")
    monkeypatch.setattr(engine, "do_translate", Mock(return_value=first))
    monkeypatch.setattr(engine, "_generate_translation", retry_fn)
    monkeypatch.setattr(
        engine,
        "translate_batch",
        lambda texts, ignore_cache=False, rate_limit_params=None: [
            engine.translate(t, ignore_cache=ignore_cache, rate_limit_params=rate_limit_params)
            for t in texts
        ],
    )
    return engine, cache


def test_untranslated_prose_triggers_semantic_retry_and_recovers(monkeypatch):
    source = (
        "The national programme expands primary education access across rural governorates. "
        "Local partner teams monitor enrollment every quarter."
    )
    seg_map = {
        "The national programme expands primary education access across rural governorates.": (
            "يوسع البرنامج الوطني فرص الوصول إلى التعليم الابتدائي في المحافظات الريفية."
        ),
        "Local partner teams monitor enrollment every quarter.": (
            "تتابع فرق الشركاء المحليين معدلات الالتحاق كل ربع سنة."
        ),
    }
    calls = []

    def _retry(seg: str) -> str:
        calls.append(seg)
        return seg_map.get(seg.strip(), "تمت الترجمة بنجاح للفقرة.")

    # Model initially leaves source untranslated in English
    engine, cache = _nllb_with_cache(monkeypatch, source, _retry)
    result = engine.translate(source)

    assert len(calls) >= 2
    assert "يوسع البرنامج الوطني" in result
    assert "تتابع فرق الشركاء" in result
    assert engine.incomplete_count == 0
    cache.set.assert_called_once()


def test_untranslated_prose_failure_skips_cache_and_records_quality_report(monkeypatch):
    source = (
        "Public health clinics delivered vaccines across twelve districts last winter. "
        "Mobile outreach teams reached displaced families."
    )
    # Even retry stays English -> must fail validation, skip cache, warn/count incomplete, record reason
    engine, cache = _nllb_with_cache(monkeypatch, source, lambda s: s)
    out = engine.translate(source)

    assert engine.incomplete_count == 1
    cache.set.assert_not_called()
    assert out == source

    # End-to-end ILTranslator + ContentLedger recording
    ledger = ContentLedger()
    cfg = SimpleNamespace(
        lang_in="en",
        lang_out="ar",
        min_text_length=2,
        disable_rich_text_translate=True,
        shared_context_cross_split_part=SimpleNamespace(content_ledger=ledger),
        raise_if_cancelled=lambda: None,
    )
    il = object.__new__(ILTranslator)
    il.translation_config = cfg
    il.translate_engine = engine
    il.support_llm_translate = False
    il.use_as_fallback = False

    para = PdfParagraph(debug_id=901, box=Box(50, 500, 500, 550), unicode=source, layout_label="plain text")
    il.pre_translate_paragraph = Mock(return_value=(source, ILTranslator.TranslateInput(source, [])))
    il.post_translate_paragraph = Mock(return_value=True)
    il.translate_paragraph(para, Page(page_number=0), pbar=Mock(), tracker=Mock())

    report = ledger.build_report()
    assert report["summary"]["incomplete_count"] == 1
    assert report["flagged_blocks"][0]["validation_reason"] == "excessive_untranslated_source"
    assert report["flagged_blocks"][0]["retry_outcome"] == "failed"


def test_arabic_with_valid_acronyms_urls_and_ids_passes_cleanly(monkeypatch):
    source = (
        "Report MCA-TS-0417 from UNICEF (CEE) and Dr. Okafor explains MICS progress at "
        "https://example.org/data?id=42 across governorates."
    )
    arabic_out = (
        "يشرح التقرير MCA-TS-0417 الصادر عن اليونيسف (CEE) والدكتور Okafor التقدم في MICS "
        "عبر https://example.org/data?id=42 في جميع المحافظات."
    )
    retry = Mock()
    engine, cache = _nllb_with_cache(monkeypatch, arabic_out, retry)

    res = engine.translate(source)
    assert "MCA-TS-0417" in res
    assert "(CEE)" in res
    assert "https://example.org/data?id=42" in res
    retry.assert_not_called()
    assert engine.incomplete_count == 0
    cache.set.assert_called_once()


def test_code_and_bibliography_pass_without_translation_or_failure():
    ledger = ContentLedger()
    cfg = SimpleNamespace(
        lang_in="en",
        lang_out="ar",
        min_text_length=2,
        disable_rich_text_translate=True,
        shared_context_cross_split_part=SimpleNamespace(content_ledger=ledger),
        raise_if_cancelled=lambda: None,
    )
    il = object.__new__(ILTranslator)
    il.translation_config = cfg
    il.translate_engine = Mock(name="stub", untranslated_count=0, incomplete_count=0)
    il.support_llm_translate = False
    il.use_as_fallback = False

    code_p = PdfParagraph(
        debug_id=902,
        box=Box(40, 400, 260, 480),
        unicode="SELECT id, total FROM invoices WHERE paid = 1;",
        layout_label="plain text",
    )
    bib_p = PdfParagraph(
        debug_id=903,
        box=Box(40, 300, 450, 340),
        unicode="Alvarez, M. & Patel, R. (2024). Journal of Policy, 12(3), 44-59. https://doi.org/10.1000/xyz",
        layout_label="reference",
    )
    il.translate_paragraph(code_p, Page(page_number=0), pbar=Mock(), tracker=Mock())
    il.pre_translate_paragraph = Mock(return_value=(None, None))
    bib_p.pipeline_trace = {"skip_disposition": "passthrough", "skip_reason": "bibliography"}
    il.translate_paragraph(bib_p, Page(page_number=0), pbar=Mock(), tracker=Mock())

    report = ledger.build_report()
    assert report["summary"]["incomplete_count"] == 0
    assert report["summary"]["counts"]["passthrough"] == 2


def test_long_paragraph_segmentation_and_missing_final_sentence_recovery(monkeypatch):
    s1 = "First, national clinics expanded maternal nutrition screenings for vulnerable households in 2024."
    s2 = "Second, district water teams restored municipal supply lines serving three hundred schools."
    s3 = "Finally, regional inspectors verified every facility before the annual audit closed."
    long_src = f"{s1} {s2} {s3}"

    # Initial decode omits the entire 3rd sentence
    truncated = (
        "أولاً، وسّعت العيادات الوطنية فحوصات تغذية الأمهات للأسر الهشّة في 2024. "
        "ثانياً، أعادت فرق المياه في الأقضية خطوط الإمداد البلدية التي تخدم ثلاثمائة مدرسة."
    )
    per_sentence = {
        s1: "أولاً، وسّعت العيادات الوطنية فحوصات تغذية الأمهات للأسر الهشّة في {0}.",
        s2: "ثانياً، أعادت فرق المياه في الأقضية خطوط الإمداد البلدية التي تخدم ثلاثمائة مدرسة.",
        s3: "وأخيراً، تحقق المفتشون الإقليميون من كل منشأة قبل إغلاق التدقيق السنوي.",
    }

    def _retry(seg: str) -> str:
        stripped = seg.strip()
        return per_sentence.get(stripped, per_sentence.get(stripped.replace("{0}", "2024"), "جملة مترجمة كاملة."))

    engine, cache = _nllb_with_cache(monkeypatch, truncated, _retry)
    out = engine.translate(long_src)

    assert "2024" in out
    assert "المفتشون الإقليميون" in out
    assert out.index("أولاً") < out.index("ثانياً") < out.index("وأخيراً")
    assert engine.incomplete_count == 0
    cache.set.assert_called_once()


def test_all_protected_values_present_but_truncated_or_unrelated_prose_fails(monkeypatch):
    source = (
        "Enrollment rose to 45% across 12 governorates in 2025 after emergency education grants "
        "funded teacher stipends and temporary classrooms."
    )
    # Keeps all protected tokens ("45%", "12", "2025") yet strips away the actual prose!
    stub_bad = "45% في 12 عام 2025."
    engine, cache = _nllb_with_cache(monkeypatch, stub_bad, lambda s: stub_bad)

    out = engine.translate(source)
    assert engine.incomplete_count == 1
    cache.set.assert_not_called()
    assert "2025" in out and "45%" in out


def test_shaped_arabic_presentation_forms_pass_completeness():
    from arabic_reshaper import reshape
    from bidi.algorithm import get_display

    from babeldoc.translator.translator import validate_translation_completeness

    src = (
        "41. This country programme document outlines UNICEF contributions to the National Development Plan "
        "and works across multiple governorates for vulnerable children."
    )
    ar = (
        "41. وتحدد هذه الوثيقة البرنامج القطري مساهمات اليونيسف في خطة التنمية الوطنية "
        "وتعمل في عدة محافظات لصالح الأطفال الأكثر هشاشة."
    )
    shaped = get_display(reshape(ar))
    ok, reason = validate_translation_completeness(src, shaped, target_lang="ar")
    assert ok, f"Shaped Arabic should validate cleanly, got reason={reason}"
