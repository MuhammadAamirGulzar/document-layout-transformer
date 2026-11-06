import pytest

from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
from babeldoc.translator.translator import _protect_numerals, _restore_numerals


def test_standards_document_cover_title_reaches_model_as_words():
    translator = object.__new__(ILTranslator)
    prepared = translator._pre_translate_terminology("STANDARDS DOCUMENT")
    assert _protect_numerals(prepared) == ("Standards document", {})


def test_character_count_note_shields_the_number_and_leaves_the_words_visible():
    source = "<b1>It has a fixed length (25 Characters for Qatar)."
    protected, mapping = _protect_numerals(source)
    assert protected == "<b1>It has a fixed length ({0} Characters for Qatar)."
    assert mapping == {"{0}": "25"}
    assert _restore_numerals(protected, mapping) == source


@pytest.mark.parametrize(
    ("source", "expected_values"),
    [
        ("Use SCBLQAQAXXX and QA64SCBL00000000000137502560.", {"SCBLQAQAXXX", "QA64SCBL00000000000137502560"}),
        ("Consumer Price Index (2018=100).", {"(2018=100)"}),
        ("The table covers (Q1 2022).", {"(Q1 2022)"}),
    ],
)
def test_banking_codes_and_numeric_references_stay_protected(source, expected_values):
    protected, mapping = _protect_numerals(source)
    assert set(mapping.values()) == expected_values
    assert _restore_numerals(protected, mapping) == source
