"""M4 tests: numeric & protected-value integrity across all translator engines."""
from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from babeldoc.translator import translator as tm


def _patch_cache_and_limiter(monkeypatch):
    cache = Mock()
    cache.get.return_value = None
    monkeypatch.setattr(tm, "TranslationCache", lambda *args, **kwargs: cache)
    monkeypatch.setattr(tm._translate_rate_limiter, "wait", lambda: None)
    return cache


class _DummyTranslator(tm.BaseTranslator):
    name = "dummy"

    def __init__(self, lang_in="en", lang_out="ar", ignore_cache=False):
        super().__init__(lang_in, lang_out, ignore_cache)

    def do_translate(self, text, rate_limit_params=None):
        return text

    def do_llm_translate(self, text, rate_limit_params=None):
        return text


def test_scientific_notation_and_decimal_percentages_protected_atomically():
    source = (
        "Learning rate 2e-5, scale -1.5E+10, threshold 2e-5%, "
        "rates 4.2%, -0.25%, and 1,250.5%, plus section 2.1.2."
    )
    protected, mapping = tm._protect_numerals(source)
    values = list(mapping.values())

    assert values == ["2e-5", "-1.5E+10", "2e-5%", "4.2%", "-0.25%", "1,250.5%", "2.1.2"]
    assert "%" not in protected
    assert tm._restore_numerals(protected, mapping) == source


def test_is_pure_passthrough_accepts_scientific_notation():
    assert tm._is_pure_passthrough("2e-5") is True
    assert tm._is_pure_passthrough("-1.5E+10%") is True
    assert tm._is_pure_passthrough("Learning rate 2e-5") is False


def test_protected_value_count_is_boundary_aware():
    count = tm.BaseTranslator._protected_value_count

    assert count("عام 2025 فقط", "25") == 0
    assert count("العدد 1,025 والباقي 1.25", "25") == 0
    assert count("القيمة 12e-5 وعام 2020-5", "-5") == 0
    assert count("القيمة -5 هنا", "-5") == 1

    assert count("القيمة 14.2 أو 4.2.1 أو 4.2%", "4.2") == 0
    assert count("القيمة 4.2 فقط", "4.2") == 1

    assert count("النسبة 4.2٪ أو -2٪", "2%") == 0
    assert count("النسبة 4.2٪ فقط", "4.2%") == 1
    assert count("النسبة 4.2 في المائة", "4.2%") == 1
    assert count("التغير -4.2 ٪", "-4.2%") == 1
    assert count("الزيادة 1,250.5 ٪", "1,250.5%") == 1

    assert count("القيمة 12e-5 أو 1.2e-5", "2e-5") == 0
    assert count("المعامل 2e-5.", "2e-5") == 1


def test_restore_numerals_warns_when_25_is_dropped_despite_2025(caplog):
    mapping = {"{0}": "25", "{1}": "2025"}
    with caplog.at_level(logging.WARNING):
        restored = tm._restore_numerals("ارتفع العدد في {1}.", mapping)
    assert restored == "ارتفع العدد في 2025."
    assert any("Shielded token '25'" in rec.message for rec in caplog.records)


def test_strict_bic_and_iban_identity_still_enforced():
    # Substring QNBA must not satisfy strict full BIC byte-identity check.
    with pytest.raises(AssertionError):
        tm._restore_numerals("رمز البنك QNBA هنا.", {"{0}": "QNBAQAQAXXX"})
    with pytest.raises(AssertionError):
        tm._restore_numerals("احفظ الحساب الآن.", {"{0}": "QA64SCBL00000000000137502560"})


@pytest.mark.parametrize(
    "factory",
    [
        lambda: tm.NLLBTranslator("en", "ar", model_name="nllb"),
        lambda: tm.NLLBTranslator("en", "ar", model_name="nllb-1.3b"),
        lambda: tm.MarianMTTranslator("en", "ar"),
    ],
    ids=["nllb-600m", "nllb-1.3b", "marianmt"],
)
def test_seq2seq_models_retry_when_protected_value_is_dropped_or_masked_by_year(
    monkeypatch, factory
):
    cache = _patch_cache_and_limiter(monkeypatch)
    engine = factory()
    monkeypatch.setattr(engine, "do_translate", Mock(return_value="ارتفع المعدل إلى عام 2025."))
    retry = Mock(return_value="ارتفع المعدل بمقدار 4.2% حتى 2025.")
    monkeypatch.setattr(engine, "_generate_translation", retry)

    result = engine.translate("Rate rose by 4.2% to 2025.")

    assert engine._protected_value_count(result, "4.2%") == 1
    assert engine._protected_value_count(result, "2025") == 1
    assert engine.incomplete_count == 0
    retry.assert_called_once_with("Rate rose by 4.2% to {1}.")
    cache.set.assert_called_once()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: tm.NLLBTranslator("en", "ar", model_name="nllb-1.3b"),
        lambda: tm.MarianMTTranslator("en", "ar"),
    ],
    ids=["nllb-1.3b", "marianmt"],
)
def test_failed_retry_retains_scientific_value_and_skips_cache(monkeypatch, factory):
    cache = _patch_cache_and_limiter(monkeypatch)
    engine = factory()
    monkeypatch.setattr(engine, "do_translate", Mock(return_value="معدل التعلم صغير جداً."))
    retry = Mock(return_value="معدل التعلم صغير جداً.")
    monkeypatch.setattr(engine, "_generate_translation", retry)

    result = engine.translate("Learning rate is 2e-5.")

    assert "2e-5" in result
    assert engine.incomplete_count == 1
    cache.set.assert_not_called()


def test_nllb_3_3b_retries_and_restores_number_when_dropped(monkeypatch):
    cache = _patch_cache_and_limiter(monkeypatch)
    engine = tm.NLLBTranslator("en", "ar", model_name="nllb-3.3b")
    monkeypatch.setattr(engine, "do_translate", Mock(return_value="انخفضت النسبة في 2025."))
    retry = Mock(return_value="انخفضت النسبة 25 في 2025.")
    monkeypatch.setattr(engine, "_generate_translation", retry)

    result = engine.translate("Rate fell 25 in 2025.")

    assert result == "انخفضت النسبة 25 في 2025."
    retry.assert_called_once()
    assert engine.incomplete_count == 0
    cache.set.assert_called_once()


@pytest.mark.parametrize(
    "bad_output",
    [
        "النسبة {1} في المائة.",
        "النسبة { 0 } ناقصة {1}.",
        "النسبة 4.2% <b9>",
    ],
    ids=["wrong-brace-index", "extra-brace-tag", "unallowed-b-tag"],
)
def test_unresolved_placeholders_flag_incomplete_and_skip_cache(monkeypatch, bad_output):
    cache = _patch_cache_and_limiter(monkeypatch)
    engine = tm.NLLBTranslator("en", "ar", model_name="nllb-3.3b")
    monkeypatch.setattr(engine, "do_translate", Mock(return_value=bad_output))

    engine.translate("Rate is 4.2%.")

    assert engine.incomplete_count == 1
    cache.set.assert_not_called()


def test_source_rich_text_b_tag_is_allowed_while_mismatched_tag_is_flagged(monkeypatch):
    cache = _patch_cache_and_limiter(monkeypatch)
    engine = _DummyTranslator()
    monkeypatch.setattr(engine, "do_translate", Mock(return_value="القيمة <b1>4.2%</b1> ثابتة."))

    out = engine.translate("The <b1>4.2%</b1> value is stable.")
    assert out == "القيمة <b1>4.2%</b1> ثابتة."
    assert engine.incomplete_count == 0
    cache.set.assert_called_once()
