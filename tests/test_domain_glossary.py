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
