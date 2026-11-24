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


def _fn_placeholder(tag_id: int, text: str = "1", is_corner: bool = True) -> FormulaPlaceholder:
    return FormulaPlaceholder(
        tag_id,
        PdfFormula(
            pdf_character=[PdfCharacter(char_unicode=text)],
            is_corner_mark=is_corner,
        ),
        f"<b{tag_id}>",
        rf"<\s*b\s*{tag_id}\s*>",
    )


def test_recovers_single_footnote_marker_after_sentence_punctuation():
    src = ILTranslator.TranslateInput(
        "Approximately 92 per cent of children are enrolled.<b1>Results from 2018 MICS show progress.",
        [_fn_placeholder(1, "1")],
    )
    assert parse(src, "ويلتحق نحو 92 في المائة من الأطفال. وتظهر نتائج المسح تقدماً.") == [
        "ويلتحق نحو 92 في المائة من الأطفال.",
        "1",
        " وتظهر نتائج المسح تقدماً.",
    ]


def test_recovers_multiple_footnote_markers_in_sentence_order():
    src = ILTranslator.TranslateInput(
        "First sentence ends here.<b1> Second sentence ends here.<b2>",
        [_fn_placeholder(1, "1"), _fn_placeholder(2, "2")],
    )
    assert parse(src, "الجملة الأولى تنتهي هنا. الجملة الثانية تنتهي هنا.") == [
        "الجملة الأولى تنتهي هنا.",
        "1",
        " الجملة الثانية تنتهي هنا.",
        "2",
    ]


def test_does_not_guess_genuine_inline_formula_in_middle_of_sentence(caplog):
    inline_math = FormulaPlaceholder(
        1,
        PdfFormula(pdf_character=[PdfCharacter(char_unicode="x+y")], is_corner_mark=False),
        "<b1>",
        r"<\s*b\s*1\s*>",
    )
    src = ILTranslator.TranslateInput(
        "Compute the value of <b1> before proceeding to the next step.",
        [inline_math],
    )
    assert parse(src, "احسب القيمة قبل الانتقال إلى الخطوة التالية.") == [
        "احسب القيمة قبل الانتقال إلى الخطوة التالية.",
    ]
    assert "Missing PDF formula placeholders after translation: <b1>" in caplog.text


def test_missing_formula_with_multiple_punctuation_marks_is_never_guessed(caplog):
    inline_math = FormulaPlaceholder(
        1,
        PdfFormula(pdf_character=[PdfCharacter(char_unicode="k_i")], is_corner_mark=False),
        "<b1>",
        r"<\s*b\s*1\s*>",
    )
    src = ILTranslator.TranslateInput(
        "First, check the input. Second, compute <b1> in the model. Third, verify the output.",
        [inline_math],
    )
    assert parse(src, "أولاً، تحقق من المدخلات. ثانياً، احسب في النموذج. ثالثاً، تحقق من المخرجات.") == [
        "أولاً، تحقق من المدخلات. ثانياً، احسب في النموذج. ثالثاً، تحقق من المخرجات.",
    ]
    assert "Missing PDF formula placeholders after translation: <b1>" in caplog.text


def test_no_duplicate_placeholder_restoration():
    src = ILTranslator.TranslateInput(
        "First sentence ends here.<b1> Second sentence follows.",
        [_fn_placeholder(1, "1")],
    )
    repaired = ILTranslator._recover_and_clean_formula_placeholders(
        src,
        "الجملة الأولى تنتهي هنا.<b1> الجملة الثانية تتبع.",
    )
    assert len(re.findall(r"<b1>", repaired)) == 1
