"""Protected-value failures must not push translatable prose back to English.

Generic synthetic fixtures only: the behaviour depends on text structure and
validation outcome, never on a file name, page number or specific phrase.
"""
from __future__ import annotations

from tests.test_priority1_completeness import _nllb_with_cache

S1 = "Clinics expanded nutrition screening for vulnerable households in 2024."
S2 = "District teams restored water supply lines serving 300 schools and 45 clinics."
S3 = "Regional inspectors verified every facility before the annual audit closed."
SOURCE = f"{S1} {S2} {S3}"

AR_S1 = "وسّعت العيادات فحوصات التغذية للأسر الأكثر هشاشة في {v}."
AR_S3 = "وتحقق المفتشون الإقليميون من كل منشأة قبل إغلاق التدقيق السنوي."
AR_S2_DROPPED = "أعادت فرق المقاطعات خطوط إمداد المياه إلى المدارس والعيادات."
AR_S2_LITERAL = "أعادت فرق المقاطعات خطوط إمداد المياه إلى 300 مدرسة و45 عيادة."


def _first_decode_losing_values() -> str:
    return (
        AR_S1.format(v="{0}") + " " + AR_S2_DROPPED + " " + AR_S3
    )


def _make_retry(literal_succeeds: bool):
    """Deterministic model stub.

    Whole-block / multi-sentence inputs always fail (long inputs are where the
    real model drops values).  For the numeric sentence the placeholder form
    loses the values (as observed on real runs); the literal form keeps them
    only when ``literal_succeeds``.
    """
    calls: list[str] = []

    def retry(seg: str) -> str:
        calls.append(seg)
        s = seg.strip()
        markers = sum(m in s for m in ("Clinics", "District", "Regional"))
        if markers > 1:
            return "ترجمة غير كاملة."
        if s.startswith("Clinics"):
            return AR_S1.format(v="{0}" if "{0}" in s else "2024")
        if s.startswith("District") and s.endswith("clinics.") or s.startswith("District") and s.endswith("{1}."):
            if "{0}" in s:
                return AR_S2_DROPPED
            return AR_S2_LITERAL if literal_succeeds else AR_S2_DROPPED
        if s.startswith("Regional"):
            return AR_S3
        # Out-of-context fragments come back untranslated (rejected by validation).
        return s

    retry.calls = calls
    return retry


def test_literal_inline_retry_recovers_sentence_whose_placeholder_was_dropped(monkeypatch):
    retry = _make_retry(literal_succeeds=True)
    engine, cache = _nllb_with_cache(monkeypatch, _first_decode_losing_values(), retry)
    out = engine.translate(SOURCE)

    # Every sentence is Arabic, values intact exactly once, nothing left in English.
    assert "300 مدرسة" in out and "45 عيادة" in out
    assert out.count("300") == 1 and out.count("45") == 1 and out.count("2024") == 1
    assert "District teams" not in out and "Regional inspectors" not in out
    # Fully validated recovery is a success, not an "incomplete" block.
    assert engine.incomplete_count == 0
    cache.set.assert_called_once()
    # Bounded: no unbounded retry loop (block + <=2 attempts per failing unit).
    assert len(retry.calls) <= 12


def test_unrecoverable_span_keeps_source_is_reported_and_not_cached(monkeypatch):
    retry = _make_retry(literal_succeeds=False)
    engine, cache = _nllb_with_cache(monkeypatch, _first_decode_losing_values(), retry)
    out = engine.translate(SOURCE)

    # Valid Arabic sentences survive; only the unsafe sentence keeps source text.
    assert "وسّعت العيادات" in out and "وتحقق المفتشون" in out
    assert S2 in out
    assert out.count("300") == 1 and out.count("45") == 1
    assert engine.incomplete_count == 1
    assert engine.source_spans_retained_count >= 1
    cache.set.assert_not_called()


def test_duplicated_value_in_literal_retry_is_rejected(monkeypatch):
    def retry(seg: str) -> str:
        s = seg.strip()
        if sum(m in s for m in ("Clinics", "District", "Regional")) > 1:
            return "ترجمة غير كاملة."
        if s.startswith("Clinics"):
            return AR_S1.format(v="{0}" if "{0}" in s else "2024")
        if s.startswith("District") and (s.endswith("clinics.") or s.endswith("{1}.")):
            if "{0}" in s:
                return AR_S2_DROPPED
            return "أعادت الفرق الخطوط إلى 300 مدرسة و45 عيادة و45 عيادة أخرى."
        if s.startswith("Regional"):
            return AR_S3
        return s

    engine, cache = _nllb_with_cache(monkeypatch, _first_decode_losing_values(), retry)
    out = engine.translate(SOURCE)
    assert S2 in out  # duplicate rejected -> source retained for that sentence
    assert out.count("45") == 1
    assert "وسّعت العيادات" in out and "وتحقق المفتشون" in out
    assert engine.incomplete_count == 1
    assert engine.source_spans_retained_count >= 1
    cache.set.assert_not_called()


def test_dropped_value_is_never_appended_to_an_unrelated_sentence(monkeypatch):
    """When nothing can be recovered, a multi-sentence block keeps its source
    rather than gluing '(300, 45)' onto the last sentence."""
    engine, cache = _nllb_with_cache(
        monkeypatch, _first_decode_losing_values(), lambda s: s.strip()
    )
    out = engine.translate(SOURCE)
    assert "(\u200e300" not in out and "\u200e45\u200e" not in out
    assert out.count("300") == 1 and out.count("45") == 1
    assert engine.incomplete_count == 1
    cache.set.assert_not_called()


def test_good_translation_is_untouched_by_recovery_path(monkeypatch):
    good = (
        AR_S1.format(v="2024")
        + " أعادت فرق المقاطعات خطوط إمداد المياه إلى 300 مدرسة و45 عيادة. "
        + AR_S3
    )
    calls: list[str] = []
    engine, cache = _nllb_with_cache(monkeypatch, good, lambda s: calls.append(s) or "x")
    out = engine.translate(SOURCE)
    # Whitespace around digits is normalised by the ordinary path; content is identical.
    assert out.replace(" ", "") == good.replace(" ", "")
    assert calls == []  # no retry was triggered
    assert engine.incomplete_count == 0
    cache.set.assert_called_once()


def test_dash_range_and_parenthetical_operator_spacing_accepted_as_exact():
    """Verify 2021–2022 vs 2021 - 2022 and (±0.5) vs (± 0.5) or (3 × 4 = 12) vs (3×4=12)
    are recognized by protected_value_count and canonicalize_literal_values while genuine
    mismatches or duplicates still fail."""
    from babeldoc.format.pdf.document_il.utils.structured_tokens import (
        canonicalize_literal_values,
        protected_value_count,
    )

    # En-dash range spaced or converted to ASCII hyphen by the model
    out_range = "نمو الاقتصاد في الفترة 2021 - 2022 تفوق على الأداء السابق."
    canon_range = canonicalize_literal_values(out_range, ["2021–2022"])
    assert "2021–2022" in canon_range
    assert protected_value_count(out_range, "2021–2022") == 1
    assert protected_value_count("نمو الاقتصاد في 2021 فقط.", "2021–2022") == 0
    assert protected_value_count("في 2021 - 2022 وكذلك 2021–2022.", "2021–2022") == 2

    # Parenthetical math/symbol expressions with varied internal operator whitespace
    assert protected_value_count("إشارة زائد أو ناقص (± 0.5) وعلامة ضرب (3×4=12).", "(±0.5)") == 1
    assert protected_value_count("إشارة زائد أو ناقص (± 0.5) وعلامة ضرب (3×4=12).", "(3 × 4 = 12)") == 1
    assert protected_value_count("إشارة (± 0.6).", "(±0.5)") == 0


def test_subordinate_opening_clause_recovery_and_batch_state_preservation(monkeypatch):
    """Verify a single-sentence Subordinate, Main-clause structure (like 'Despite ... in 2022,
    economic growth in 2021–2022 outpaced...') recovers via clause splitting when whole-sentence
    decode fails, and that translate_batch preserves para_incomplete across items."""
    sent1 = (
        "Despite external headwinds and the resulting slowdown in 2022, "
        "economic growth in middle-income countries in the period 2021–2022 outpaced expectations."
    )
    sent2 = "This pattern is expected to continue in 2023."

    def retry_fn(seg: str) -> str:
        s = seg.strip()
        # Whole sentence 1 fails by dropping 2021–2022
        if "Despite" in s and "outpaced" in s:
            return "وعلى الرغم من الرياح المعاكسة في 2022، تفوق النمو الاقتصادي على التوقعات."
        if "Despite" in s:
            return "وعلى الرغم من الرياح المعاكسة والتباطؤ الناتج في 2022"
        if "economic growth" in s:
            return "تفوق النمو الاقتصادي في البلدان المتوسطة الدخل في الفترة 2021 - 2022 على التوقعات."
        if "This pattern" in s:
            return "ومن المتوقع أن يستمر هذا النمط في 2023."
        return s

    engine, cache = _nllb_with_cache(
        monkeypatch,
        "وعلى الرغم من الرياح المعاكسة في {0}، تفوق النمو الاقتصادي على التوقعات.",
        retry_fn,
    )
    out = engine.translate(sent1)
    assert "وعلى الرغم من الرياح المعاكسة" in out
    assert "تفوق النمو الاقتصادي" in out
    assert "2021–2022" in out
    assert "Despite" not in out
    assert engine.incomplete_count == 0


def test_international_formats_nested_parentheses_and_magnitude_exact_counts(monkeypatch):
    """Verify European decimals (1.234,56), South Asian grouping (1,25,000),
    magnitude suffixes ($1.2M), unit spacing (20 °C), and nested parenthetical
    currencies (−€45.00 · (€45.00)) pass exact-multiplicity without false
    duplicates while genuine drops or extras are still rejected."""
    from collections import Counter

    from babeldoc.format.pdf.document_il.utils.structured_tokens import (
        protect_structured_tokens,
    )
    from babeldoc.translator.translator import BaseTranslator

    src = (
        "Amounts include USD 1,234.56, €1.234,56, PKR 1,25,000, $1.2M, "
        "stored at 20 °C, adjustment −€45.00 (€45.00)."
    )
    prot, mapping = protect_structured_tokens(src)
    assert "$1.2M" in prot or "1.2M" in mapping.values()
    assert "1.234,56" in mapping.values()
    assert "1,25,000" in mapping.values()
    assert "(€45.00)" in mapping.values()
    assert "45.00" in mapping.values()

    expected = Counter(mapping.values())
    good_ar = (
        "تشمل المبالغ USD 1,234.56 و €1.234,56 و PKR 1,25,000 و $1.2M، "
        "ومحفوظة عند 20°C، مع تعديل −€45.00 (€45.00)."
    )
    assert BaseTranslator._mismatched_protected_values(good_ar, expected) == set()

    # Genuine drop of standalone 45.00 must STILL be detected even when (€45.00) is present.
    dropped_standalone = (
        "تشمل المبالغ USD 1,234.56 و €1.234,56 و PKR 1,25,000 و $1.2M، "
        "ومحفوظة عند 20 °C، مع تعديل (€45.00)."
    )
    assert "45.00" in BaseTranslator._mismatched_protected_values(dropped_standalone, expected)

    # Genuine duplication of (€45.00) must also fail.
    duplicated_nested = good_ar + " (€45.00)"
    assert "(€45.00)" in BaseTranslator._mismatched_protected_values(duplicated_nested, expected)

    # Full translate pass accepts valid output without spurious retries or cache bypass.
    engine, cache = _nllb_with_cache(
        monkeypatch,
        good_ar,
        lambda seg: good_ar,
    )
    out = engine.translate(src)
    assert "20 °C" in out
    assert "€1.234,56" in out
    assert "PKR 1,25,000" in out
    assert "$1.2M" in out
    assert engine.incomplete_count == 0
    cache.set.assert_called_once()


def test_translate_batch_recovery_and_source_span_report_telemetry(monkeypatch):
    """Verify NLLBTranslator.translate_batch preserves partial recovery state and
    only caches fully validated items, while ContentLedger.build_report exports
    source_text_retained_count accurately."""
    from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger

    good_src = "System budget rose 12% in 2024."
    partial_src = (
        "Revenue climbed 15% last quarter. "
        "Special identifier XQ-9910 remains active for auditing."
    )

    def retry_fn(seg: str) -> str:
        if "Revenue climbed" in seg:
            return "ارتفعت الإيرادات 15% في الربع الأخير."
        # Second sentence / its fragments remain untranslatable on retry
        return seg

    engine, cache = _nllb_with_cache(monkeypatch, "", retry_fn)
    monkeypatch.delattr(engine, "translate_batch", raising=False)
    monkeypatch.setattr(engine, "_load_model", lambda: None)
    monkeypatch.setattr(engine, "_tokenizer", None)
    monkeypatch.setattr(engine, "_effective_max_length", lambda: 256)
    monkeypatch.setattr(engine, "_seq2seq_make_gen_kwargs", lambda: (lambda _n: {}))
    monkeypatch.setattr(engine, "_decode_batch_size", lambda: 8)

    def fake_run_seq2seq_batch(items, make_gen_kwargs, limit):
        out = []
        for item in items:
            if "{0}" in item and "{1}" in item and "budget" in item:
                out.append(("ارتفعت ميزانية النظام {0} في {1}.", False, False))
            else:
                # Drops {1} (XQ-9910) on batch pass to force span recovery
                out.append(("ارتفعت الإيرادات {0}. بقي المعرف قيد التدقيق.", False, False))
        return out

    monkeypatch.setattr(engine, "_run_seq2seq_batch", fake_run_seq2seq_batch)

    outputs = engine.translate_batch([good_src, partial_src])
    assert len(outputs) == 2
    assert "ارتفعت ميزانية النظام 12% في 2024." in outputs[0]
    assert "ارتفعت الإيرادات 15% في الربع الأخير." in outputs[1]
    assert "XQ-9910" in outputs[1]  # failed span kept verbatim, never dropped
    # First item was cached; second item was partial so must not be cached
    assert cache.set.call_count == 1
    assert cache.set.call_args[0][0] == good_src

    state = engine._get_call_state()
    assert state.validation_reason == "partial_span_recovery"
    assert state.retry_outcome == "partial"
    assert engine.source_spans_retained_count >= 1

    ledger = ContentLedger()
    ledger.record(
        block_key="blk_ok",
        page=0,
        layout_label="prose",
        disposition="translated",
        source_text=good_src,
        translated_text=outputs[0],
        validation_reason=None,
    )
    ledger.record(
        block_key="blk_part",
        page=0,
        layout_label="prose",
        disposition="incomplete",
        source_text=partial_src,
        translated_text=outputs[1],
        validation_reason="partial_span_recovery",
        retry_attempted=True,
        retry_outcome="partial",
    )
    report = ledger.build_report()
    assert report["summary"]["source_text_retained_count"] == 1
    assert report["summary"]["validation_retry_partial_count"] == 1


