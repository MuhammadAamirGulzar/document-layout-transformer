import pytest

from babeldoc.format.pdf.document_il.midend import domain_glossary as glossary


def test_pre_glossary_locks_domain_phrases():
    assert glossary.apply_pre_glossary("Check Digit Generation and Validation") == "توليد رقم التحقق والتحقق من صحته"
    assert glossary.apply_pre_glossary("Validation") == "التحقق من الصحة"
    assert glossary.apply_pre_glossary("3.1. Validation") == "3.1. التحقق من الصحة"


def test_post_glossary_repairs_known_mistranslations():
    assert glossary.apply_post_glossary("استخدام رقمي الشيك في جيل IBAN") == "استخدام رقمي التحقق في إنشاء رقم IBAN"
    assert glossary.apply_post_glossary("استبعاد الباقي من 98") == "اطرح الباقي من 98"


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_glossary_can_be_disabled(monkeypatch, value):
    monkeypatch.setenv("TRANSDOC_DOMAIN_GLOSSARY", value)
    assert glossary.apply_pre_glossary("Validation") == "Validation"
    assert glossary.apply_post_glossary("جيل IBAN") == "جيل IBAN"


def test_scientific_glossary_and_percentage_protection():
    from babeldoc.translator.translator import NLLBTranslator, _restore_numerals

    assert glossary.apply_pre_glossary("Salient Neurons") == "الخلايا العصبية البارزة"
    assert glossary.apply_pre_glossary("Past tense verb") == "فعل ماضٍ"
    assert glossary.apply_pre_glossary("(Beginning)") == "(البداية)"
    source = "Neural probes measure neuron selectivity across three runs."
    assert glossary.apply_post_glossary(
        "الخلايا النفسية والانتخابية في ثلاث ركضات", source_text=source
    ) == (
        "الخلايا العصبية والانتقائية في 3 جولات تشغيل"
    )
    assert glossary.apply_post_glossary(
        "دقة القرآن", source_text="Oracle accuracy for the neuron probe"
    ) == "دقة المرجع المثالي (Oracle)"

    # Ambiguous Arabic words are valid outside the NLP domain and must not be
    # rewritten globally for general-purpose documents.
    unrelated = "تغطية الانتخابات ونتائج الاختبار الأعمى"
    assert glossary.apply_post_glossary(
        unrelated, source_text="Election coverage and blind-test results"
    ) == unrelated

    restored = _restore_numerals("أفضل {0} من الخلايا", {"{0}": "20%"})
    assert "20%" in restored and "20 %" not in restored

    translator = object.__new__(NLLBTranslator)
    canonical = translator._canonicalize_literal_values("أفضل 20٪ من الخلايا", ["20%"])
    assert "20%" in canonical
    assert translator._protected_value_count(canonical, "20%") >= 1
