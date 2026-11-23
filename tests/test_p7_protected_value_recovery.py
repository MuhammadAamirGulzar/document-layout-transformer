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
    assert "$1.2M" in mapping.values()
    assert "€1.234,56" in mapping.values()
    assert "PKR 1,25,000" in mapping.values()
    assert "(€45.00)" in mapping.values()
    assert "−€45.00" in mapping.values()

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
    assert "−€45.00" in BaseTranslator._mismatched_protected_values(dropped_standalone, expected)

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


def test_single_sentence_multi_clause_exhausted_recovery_never_appends_unanchored_numbers(
    monkeypatch,
):
    """A single sentence with multiple clauses whose numbers are dropped on all retry
    attempts must NEVER append detached values like 'العيادات (300، 45).'; it retains
    the source text so each value stays anchored to its own entity, flags incomplete,
    and skips cache."""
    src = (
        "District teams repaired water pipes serving 300 schools "
        "while inspectors audited 45 clinics."
    )
    dropped_ar = "أصلحت فرق المقاطعة أنابيب المياه التي تخدم المدارس بينما قام المفتشون بتدقيق العيادات."

    # Every retry attempt (whole sentence, clause placeholder, clause literal) drops numbers.
    def retry_fn(_seg: str) -> str:
        return dropped_ar

    engine, cache = _nllb_with_cache(monkeypatch, dropped_ar, retry_fn)

    out = engine.translate(src)

    assert out == src
    assert "(300" not in out
    assert "(\u200e300" not in out
    assert "العيادات (" not in out
    assert "300 schools" in out
    assert "45 clinics" in out
    assert engine.incomplete_count == 1
    assert engine.source_spans_retained_count >= 1
    assert engine._get_call_state().retry_outcome == "failed"
    cache.set.assert_not_called()


def test_single_sentence_multi_clause_partial_and_full_clause_recovery_preserve_entity_binding(
    monkeypatch,
):
    """Through translate(), single-sentence multi-clause prose splits on subordinating
    conjunctions ('while'):
    - Partial recovery preserves validated Arabic for the first clause and retains ONLY
      the failing source clause verbatim (never appending detached '(45)').
    - Full recovery caches clean Arabic when both clauses validate with anchored values."""
    src = (
        "District teams repaired water pipes serving 300 schools "
        "while inspectors audited 45 clinics."
    )
    dropped_ar = "أصلحت فرق المقاطعة أنابيب المياه التي تخدم المدارس بينما قام المفتشون بتدقيق العيادات."

    # 1) Partial recovery: first clause keeps 300 with schools; second clause still drops 45.
    def partial_retry(seg: str) -> str:
        if "schools" in seg and "clinics" in seg:
            return dropped_ar
        if "300 schools" in seg:
            return "أصلحت فرق المقاطعة أنابيب المياه التي تخدم 300 مدرسة"
        return "بينما قام المفتشون بتدقيق العيادات."

    engine_p, cache_p = _nllb_with_cache(monkeypatch, dropped_ar, partial_retry)
    out_p = engine_p.translate(src)

    assert "تخدم 300 مدرسة" in out_p
    assert "while inspectors audited 45 clinics." in out_p
    assert "(\u200e45" not in out_p and "(45" not in out_p
    assert engine_p.incomplete_count == 1
    assert engine_p.source_spans_retained_count == 1
    state_p = engine_p._get_call_state()
    assert state_p.retry_outcome == "partial"
    assert state_p.validation_reason == "partial_span_recovery"
    cache_p.set.assert_not_called()

    # 2) Full recovery: both clauses validate independently with anchored values.
    def full_retry(seg: str) -> str:
        if "schools" in seg and "clinics" in seg:
            return dropped_ar
        if "300 schools" in seg:
            return "أصلحت فرق المقاطعة أنابيب المياه التي تخدم 300 مدرسة"
        if "45 clinics" in seg:
            return "بينما قام المفتشون بتدقيق 45 عيادة."
        return dropped_ar

    engine_f, cache_f = _nllb_with_cache(monkeypatch, dropped_ar, full_retry)
    out_f = engine_f.translate(src)

    assert "تخدم 300 مدرسة" in out_f
    assert "بتدقيق 45 عيادة." in out_f
    assert "while inspectors" not in out_f
    assert engine_f.incomplete_count == 0
    assert engine_f.source_spans_retained_count == 0
    cache_f.set.assert_called_once_with(src, out_f)


def test_standard_dotted_code_and_written_percentage_recover_arabic_without_weakening_checks(
    monkeypatch,
):
    """1) Standard letter-dot-number codes (F.748.57, [ITU-T F.748.56], GC.18/INF/4)
       protect atomically so valid Arabic is accepted instead of failing left-dot lookup.
    2) Written percentages ('60 per cent') accept valid Arabic '60٪' / '60 في المائة',
       and clause recovery salvages comma-separated numeric clauses.
    3) Control: a plausible Arabic sentence that attaches a number to the wrong
       entity/unit (e.g. moving '%' from 60 onto 25 or swapping quantities) is still rejected."""
    from babeldoc.format.pdf.document_il.utils.structured_tokens import (
        protect_structured_tokens,
        protected_value_count,
    )

    # 1) Standard dotted code & slash-document-code protection & exact validation
    itu_src = (
        "Recommendation ITU-T F.748.57 establishes a framework of technical requirements "
        "referenced in [ITU-T F.748.56] and document GC.18/Res.9."
    )
    itu_prot, itu_map = protect_structured_tokens(itu_src)
    assert "F.748.57" in itu_map.values()
    assert "F.748.56" in itu_map.values()
    assert "GC.18/Res.9" in itu_map.values()
    assert "748.57" not in itu_map.values()

    itu_ar = (
        "تضع التوصية ITU-T F.748.57 إطاراً للمتطلبات التقنية "
        "المشار إليها في [ITU-T F.748.56] والوثيقة GC.18/Res.9."
    )
    engine_itu, cache_itu = _nllb_with_cache(monkeypatch, itu_ar, lambda _s: itu_ar)
    out_itu = engine_itu.translate(itu_src)
    assert "ITU-T F.748.57" in out_itu
    assert "[ITU-T F.748.56]" in out_itu
    assert "GC.18/Res.9" in out_itu
    assert "تضع التوصية" in out_itu
    assert engine_itu.incomplete_count == 0
    cache_itu.set.assert_called_once()

    # 2) Written percentage ('60 per cent') accepted as Arabic '60٪' / '60 في المائة',
    #    and multi-clause numeric sentence recovers cleanly via clause split when initial drops 25.
    un_src = (
        "49. Of the 25 United Nations entities that responded to the question about specific "
        "country strategies, 15 entities (60 per cent) indicated that they had a strategy."
    )
    _, un_map = protect_structured_tokens(un_src)
    assert "60 per cent" in un_map.values()
    assert protected_value_count("أشارت 15 كيانات (60٪) إلى وجود استراتيجية.", "60 per cent") == 1
    assert protected_value_count("أشارت 15 كيانات (60 في المائة) إلى وجود استراتيجية.", "60 per cent") == 1
    # Dropping the percent unit must NOT satisfy "60 per cent"
    assert protected_value_count("أشارت 15 كيانات (60) إلى وجود استراتيجية.", "60 per cent") == 0

    dropped_first = "49. من بين كيانات الأمم المتحدة التي أجابت، أشارت 15 كيانات (60٪) إلى وجود استراتيجية."

    def clause_retry(seg: str) -> str:
        if "United Nations entities" in seg and "indicated" in seg:
            return dropped_first
        if "United Nations entities" in seg:
            return (
                "من بين 25 كياناً من كيانات الأمم المتحدة التي أجابت عن السؤال حول استراتيجيات البلدان"
                if "25" in seg
                else "من بين {0} كياناً من كيانات الأمم المتحدة التي أجابت عن السؤال حول استراتيجيات البلدان"
            )
        if "indicated" in seg:
            return (
                "أشارت 15 كياناً (60٪) إلى أن لديها استراتيجية."
                if "15" in seg
                else "أشارت {0} كياناً (60٪) إلى أن لديها استراتيجية."
            )
        return dropped_first

    engine_un, cache_un = _nllb_with_cache(monkeypatch, dropped_first, clause_retry)
    out_un = engine_un.translate(un_src)
    assert "25 كياناً" in out_un
    assert "15 كياناً (60٪)" in out_un
    assert "Of the 25" not in out_un
    assert engine_un.incomplete_count == 0
    cache_un.set.assert_called_once()

    # 3) Control: plausible Arabic attaching '%' / quantity to the WRONG entity must be rejected
    #    (attaches '%' to 25 and leaves 60 bare as a plain count, mis-binding both values).
    wrong_entity_ar = (
        "49. من بين 25٪ من كيانات الأمم المتحدة التي أجابت عن الاستراتيجيات، "
        "أشارت 15 كياناً (60 كياناً) إلى وجود استراتيجية."
    )
    engine_bad, cache_bad = _nllb_with_cache(
        monkeypatch, wrong_entity_ar, lambda _s: wrong_entity_ar
    )
    out_bad = engine_bad.translate(un_src)
    assert out_bad == un_src
    assert "25٪" not in out_bad
    assert engine_bad.incomplete_count == 1
    cache_bad.set.assert_not_called()


def test_short_written_percent_clause_recovers_arabic_and_isolates_retained_fallback(
    monkeypatch,
):
    """Phase-1 Iraq p.13 regression:
    1) Arabic 'في المئة' (without alef) satisfies written English '52 per cent'.
    2) Short percentage-subject prose ('52 per cent lack sanitation services.')
       whose placeholder rung echoes English recovers cleanly via literal retry.
    3) Defense-in-depth: if an English sentence genuinely fails recovery inside
       a mixed Arabic paragraph, _normalize_arabic_output does not partially
       rewrite '52 per cent' into '52٪' inside the English sentence, and sentence
       joining isolates the retained English span so '52' cannot collide with '48٪'."""
    from types import SimpleNamespace
    from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
    from babeldoc.format.pdf.document_il.utils.structured_tokens import (
        protected_value_count,
    )

    # 1) Both MSA spellings ('في المئة' and 'في المائة') match '52 per cent'
    assert protected_value_count("52 في المئة تفتقر إلى خدمات الصرف الصحي.", "52 per cent") == 1
    assert protected_value_count("52 في المائة تفتقر إلى خدمات الصرف الصحي.", "52 per cent") == 1
    assert protected_value_count("52 تفتقر إلى خدمات الصرف الصحي.", "52 per cent") == 0

    # 2) Placeholder rung echoes English -> completeness check rejects 3-word lowercase
    #    prose -> bounded literal retry recovers Arabic '52 في المئة ...'
    short_src = "52 per cent lack sanitation services."

    def short_retry(seg: str) -> str:
        if "{0}" in seg:
            return "{0} lack sanitation services."
        return "52 في المئة تفتقر إلى خدمات الصرف الصحي."

    engine_short, cache_short = _nllb_with_cache(
        monkeypatch, "{0} lack sanitation services.", short_retry
    )
    out_short = engine_short.translate(short_src)
    assert "52 في المئة" in out_short
    assert "lack sanitation services" not in out_short
    assert engine_short.incomplete_count == 0
    cache_short.set.assert_called_once()

    # 3) Mixed paragraph fallback defense-in-depth via ILTranslator
    para_src = (
        "52 per cent lack sanitation services. "
        "Forty-eight per cent of primary and secondary schools lack basic water services "
        "and 35 per cent lack sanitation services."
    )

    class _RetainingStubEngine:
        supports_sentence_batching = False
        name = "stub"

        def translate(self, text: str, rate_limit_params=None) -> str:
            if text.startswith("52 per cent"):
                return text  # Simulate genuine unrecoverable fallback on sentence 1
            return "48٪ من المدارس الابتدائية والثانوية تفتقر إلى خدمات المياه الأساسية و 35٪ تفتقر إلى خدمات الصرف الصحي."

    il_tr = object.__new__(ILTranslator)
    il_tr.translation_config = SimpleNamespace(
        lang_in="en",
        lang_out="ar",
        raise_if_cancelled=lambda: None,
        progress_monitor=None,
    )
    il_tr.translate_engine = _RetainingStubEngine()
    il_tr.stage_name = "Translate Paragraphs"
    il_tr.support_llm_translate = False

    joined = il_tr._translate_with_line_preservation(para_src, {})
    assert "\u200f\u200e52 per cent lack sanitation services.\u200e\u200f" in joined
    normalized = il_tr._normalize_arabic_output(joined)
    # Must NOT rewrite '52 per cent' -> '52٪' inside the retained English clause
    assert "52 per cent lack sanitation services." in normalized
    assert "52٪ lack sanitation services" not in normalized
    # Arabic percentages in sentence 2 remain intact
    assert "48٪" in normalized
    assert "35٪" in normalized
