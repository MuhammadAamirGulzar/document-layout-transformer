from unittest.mock import Mock

import pytest

from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("The number has two check digits.", "The number has two verification digits."),
        ("It has two check-digits (64).", "It has two verification-digits (64)."),
        ("Two Check Digits follow.", "Two Verification Digits follow."),
        ("5. Check Digit Generation and Validation", "5. Check Digit Generation and Validation"),
        ("5.2 Check Digit Validation", "5.2 Check Digit Validation"),
        ("Calculate the check digit using MOD 97-10.", "Calculate the check digit using MOD 97-10."),
        ("Please check each digit.", "Please check each digit."),
    ],
)
def test_rewrite_only_the_verified_dual_phrase(source, expected):
    il = object.__new__(ILTranslator)
    assert il._pre_translate_terminology(source) == expected


def test_local_model_receives_the_clarified_phrase_and_keeps_the_code():
    il = object.__new__(ILTranslator)
    il.translate_engine = Mock()
    il._translate_with_line_preservation("The IBAN has two check digits, for example 64.", {})
    il.translate_engine.translate.assert_called_once_with(
        "The IBAN has two verification digits, for example 64.", rate_limit_params={}
    )
