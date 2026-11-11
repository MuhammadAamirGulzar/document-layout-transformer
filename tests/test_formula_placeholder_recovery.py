import re

import pytest

from babeldoc.format.pdf.document_il.il_version_1 import PdfCharacter, PdfFormula
from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder, ILTranslator


def prepared_text(source="Annotations: <b15> followed by <b16>."):
    formulas = [
        FormulaPlaceholder(
            tag_id,
            PdfFormula(pdf_character=[PdfCharacter(char_unicode=math)]),
            f"<b{tag_id}>",
            rf"<\s*b\s*{tag_id}\s*>",
        )
        for tag_id, math in [(15, "twN"), (16, "wi")]
    ]
    return ILTranslator.TranslateInput(source, formulas)


def parse(source, output):
    parts = ILTranslator.parse_translate_output(None, source, output)
    return [
        part.pdf_formula.pdf_character[0].char_unicode
        if part.pdf_formula else part.pdf_same_style_unicode_characters.unicode
        for part in parts
    ]


@pytest.mark.parametrize("marker", ["\u0628", "\ufe8f"], ids=["model-output", "shaped-output"])
def test_recovers_formula_tag_returned_with_arabic_beh(marker, caplog):
    # The page 4 model response returned this Arabic letter for the b in <b15>.
    assert parse(prepared_text(), f"قبل < {marker} 15> وبعد <B16>.") == [
        "قبل ", "twN", " وبعد ", "wi", ".",
    ]
    assert "Missing PDF formula placeholders" not in caplog.text


def test_reports_missing_formula_without_guessing_its_position(caplog):
    assert parse(prepared_text(), "النص <b16>.") == ["النص ", "wi", "."]
    warning = next(record.message for record in caplog.records if "Missing PDF formula placeholders" in record.message)
    assert re.search(r"<b15>$", warning)


def test_does_not_treat_a_literal_source_alias_as_a_formula(caplog):
    source = prepared_text("Literal < ب 15> followed by formulas <b15> and <b16>.")
    assert parse(source, "نص < ب 15> <b16>") == ["نص < ب 15> ", "wi"]
    assert "<b15>" in caplog.text


def test_does_not_invent_an_extra_formula_for_an_alias_when_original_exists():
    assert parse(prepared_text(), "<b15> < ب 15> < ب 99> <b16>") == [
        "twN", " < ب 15> < ب 99> ", "wi",
    ]
