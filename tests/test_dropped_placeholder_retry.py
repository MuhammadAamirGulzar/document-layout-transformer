import asyncio
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest

import server
from babeldoc.translator import translator as tm


@pytest.fixture
def make_engine(monkeypatch):
    cache = Mock()
    cache.get.return_value = None
    monkeypatch.setattr(tm, "TranslationCache", lambda *args, **kwargs: cache)
    monkeypatch.setattr(tm._translate_rate_limiter, "wait", lambda: None)

    def make(first, retry, model="nllb"):
        engine = tm.NLLBTranslator("en", "ar", model_name=model)
        monkeypatch.setattr(engine, "do_translate", Mock(return_value=first))
        monkeypatch.setattr(engine, "_generate_translation", retry, raising=False)
        return engine, cache

    return make


@pytest.mark.parametrize(
    ("source", "first", "repaired"),
    [
        ("Under the QCB directive, publish 3 sets.", "انشر مجموعات.", "بناء على توجيه QCB انشر 3 مجموعات."),
        ("Head of IBAN Committee", "رئيس اللجنة", "رئيس لجنة IBAN"),
        ("Keep QA64SCBL00000000000137502560 and 3 copies.", "احتفظ بـ{0}.", "احتفظ بـ{0} و 3 نسخ."),
    ],
)
def test_one_retry_restores_protected_content(make_engine, source, first, repaired):
    retry = Mock(return_value=repaired)
    engine, cache = make_engine(first, retry)
    result = engine.translate(source)
    retry.assert_called_once()
    if "QA64" in source:
        assert retry.call_args.args == ("Keep {0} and 3 copies.",)
    else:
        assert retry.call_args.args == (source,)
    assert result == repaired.replace("{0}", "QA64SCBL00000000000137502560")
    assert engine.incomplete_count == 0
    cache.set.assert_called_once_with(source, result)


@pytest.mark.parametrize("candidate", [
    "احتفظ بـ 13 نسخة",
    "احتفظ بـ 3.5 نسخة",
    "احتفظ بـ -3 نسخ",
    "احتفظ بـ {3} نسخ",
    "احتفظ بـ 3 و 3 نسخ",
    "Keep 3 copies.",
    RuntimeError("generation failed"),
])
def test_bad_retry_keeps_the_first_output_and_flags_it(make_engine, candidate):
    retry = Mock(side_effect=candidate) if isinstance(candidate, Exception) else Mock(return_value=candidate)
    engine, cache = make_engine("احتفظ بنسخ.", retry)
    result = engine.translate("Keep 3 copies.")
    assert "احتفظ بنسخ" in result
    assert engine._protected_value_count(result, "3") == 1
    retry.assert_called_once()
    assert engine.incomplete_count == 1
    assert engine.untranslated_count == 0
    cache.set.assert_not_called()


def test_retry_cannot_drop_a_value_that_was_kept(make_engine):
    retry = Mock(return_value="انشر 3 مجموعات.")
    engine, cache = make_engine("توجيه {0}: انشر مجموعات.", retry)
    result = engine.translate("QCB directive: publish 3 sets.")
    assert "توجيه QCB: انشر مجموعات" in result
    assert engine._protected_value_count(result, "3") == 1
    retry.assert_called_once_with("{0} directive: publish 3 sets.")
    assert engine.incomplete_count == 1
    cache.set.assert_not_called()


def test_failed_retry_retains_currency_and_exact_grouped_number(make_engine):
    retry = Mock(return_value="تفكر الجامعة في تثبيت نظام.")
    engine, cache = make_engine("تفكر الجامعة في تثبيت نظام.", retry)

    result = engine.translate(
        "The university is considering installing a $ 250,000 system."
    )

    assert "$ 250,000" in result
    assert engine._protected_value_count(result, "250,000") == 1
    assert engine.incomplete_count == 1
    cache.set.assert_not_called()


def test_dropped_full_iban_keeps_the_existing_strict_guard(make_engine):
    retry = Mock()
    engine, cache = make_engine("احفظ الحساب.", retry)
    with pytest.raises(AssertionError, match="Byte-identity"):
        engine.translate("Keep QA64SCBL00000000000137502560.")
    retry.assert_not_called()
    cache.set.assert_not_called()


def test_dropped_grouped_iban_is_flagged_without_a_literal_retry(make_engine):
    retry = Mock()
    engine, cache = make_engine("احفظ الحساب.", retry)
    source = "Keep QA64 SCBL 0000 0000 0001 3750 2560 on file."
    assert engine.translate(source) == "احفظ الحساب."
    retry.assert_not_called()
    assert engine.incomplete_count == 1
    cache.set.assert_not_called()


def test_3_3b_keeps_existing_behavior(make_engine):
    retry = Mock()
    engine, _ = make_engine("رئيس اللجنة", retry, model="nllb-3.3b")
    assert engine.translate("Head of IBAN Committee") == "رئيس اللجنة"
    retry.assert_not_called()


def test_status_and_page_can_report_incomplete_translation(monkeypatch):
    sid = str(uuid4())
    monkeypatch.setattr(server, "translation_sessions", {
        sid: {"status": "completed", "untranslated_count": 0, "incomplete_count": 2},
    })

    async def read_status():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(f"/translate/status/{sid}")

    response = asyncio.run(read_status())
    assert response.json()["incomplete_count"] == 2
    assert response.json()["untranslated_count"] == 0
    page = (Path(__file__).resolve().parents[1] / "frontend/index.html").read_text(encoding="utf-8")
    assert "state.incomplete_count" in page
    assert "missing protected numbers or codes" in page
