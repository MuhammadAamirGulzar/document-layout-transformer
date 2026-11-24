"""Ordinary ALL-CAPS words must not be shielded as bank codes."""

import pytest

from babeldoc.translator.translator import _is_pure_passthrough
from babeldoc.translator.translator import _protect_numerals
from babeldoc.translator.translator import _restore_numerals

PLAIN_WORDS = [
    "DEVELOPMENT", "ENVELOPE", "NATIONAL", "POSITION", "STANDARD", "INCOMING", "OUTGOING",
]
REAL_BICS = [
    "ABQQQAQAXXX", "SCBLQAQXXXX", "CBQAQAQA", "DEUTDEFF", "CHASUS33", "BOFAUS3N",
]


@pytest.mark.parametrize("word", PLAIN_WORDS)
def test_plain_caps_word_is_left_for_the_model(word):
    assert _protect_numerals(word) == (word, {})
    sentence = f"The {word} column is shown."
    assert _protect_numerals(sentence) == (sentence, {})


@pytest.mark.parametrize("word", PLAIN_WORDS)
def test_plain_caps_word_is_not_a_passthrough_cell(word):
    assert not _is_pure_passthrough(word)


@pytest.mark.parametrize("bic", REAL_BICS)
def test_real_bic_stays_protected(bic):
    sentence = f"Use {bic} here."
    protected, mapping = _protect_numerals(sentence)
    assert mapping == {"{0}": bic}
    assert _restore_numerals(protected, mapping) == sentence
    assert _is_pure_passthrough(bic)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("STANDARDS DOCUMENT", "Standards document"),
        ("SCBLQAQAXXX CBQAQAQA", "SCBLQAQAXXX CBQAQAQA"),
        ("TERMS AND ABBREVIATIONS", "TERMS AND ABBREVIATIONS"),
        ("QCB STANDARDS", "QCB STANDARDS"),
        ("DOCUMENT", "DOCUMENT"),
        ("Standards document", "Standards document"),
        ("ARAB BANK", "ARAB BANK"),
        ("BANK CBQAQAQA", "BANK CBQAQAQA"),
        ("QATAR BANK ABQQQAQAXXX", "QATAR BANK ABQQQAQAXXX"),
        ("STANDARDS DOCUMENT\nQATAR CENTRAL", "Standards document\nQatar central"),
    ],
)
def test_caps_phrase_sentence_case(source, expected):
    from babeldoc.translator.translator import _caps_phrase_to_sentence_case

    assert _caps_phrase_to_sentence_case(source) == expected


def test_caps_title_reaches_model_as_words_cover_title():
    from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator

    engine = object.__new__(ILTranslator)
    assert engine._pre_translate_terminology("STANDARDS DOCUMENT") == "Standards document"
    # Not special to one title: any caps phrase of ordinary words is handled.
    assert engine._pre_translate_terminology("ANNUAL DOCUMENT") == "Annual document"
