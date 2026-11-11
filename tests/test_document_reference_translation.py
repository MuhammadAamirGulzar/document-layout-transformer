from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from babeldoc.format.pdf.document_il.il_version_1 import Page, PdfCharacter, PdfFormula, PdfParagraph
from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder, ILTranslator


def formula(tag_id, value):
    tag = f"<b{tag_id}>"
    return FormulaPlaceholder(tag_id, PdfFormula(pdf_character=[PdfCharacter(char_unicode=value)]), tag, tag)


def test_split_section_reference_keeps_number_and_brackets_out_of_model():
    engine = object.__new__(ILTranslator)
    engine.translation_config = SimpleNamespace(lang_out="en-ar", raise_if_cancelled=lambda: None)
    engine.translate_engine = Mock()
    engine.use_as_fallback = False
    engine.support_llm_translate = False
    prepared = "Check the result.<b2>Section 12.<b3>"
    engine.pre_translate_paragraph = Mock(return_value=(
        prepared, engine.TranslateInput(prepared, [formula(2, "["), formula(3, "4]")]),
    ))
    engine._translate_with_line_preservation = Mock(return_value="راجع النتيجة.")
    engine.post_translate_paragraph = Mock()

    engine.translate_paragraph(
        PdfParagraph(unicode="Check the result. [Section 12.4]", layout_label="plain text"),
        Page(), pbar=Mock(), tracker=Mock(),
    )

    engine._translate_with_line_preservation.assert_called_once()
    assert engine._translate_with_line_preservation.call_args.args[0] == "Check the result."
    *_, parsed, output = engine.post_translate_paragraph.call_args.args
    assert parsed.placeholders == []
    assert output.endswith(" \u200e[Section\u00a012.4]\u200e")


def test_trailing_table_reference_keeps_unrelated_math_tag():
    engine = object.__new__(ILTranslator)
    prepared = "Use <b1> when comparing (Table 7b)"
    math = formula(1, "=")
    body, reference, parsed = engine._split_trailing_document_reference(
        prepared, "Use = when comparing (Table 7b)", engine.TranslateInput(prepared, [math]),
    )
    assert body == "Use <b1> when comparing"
    assert reference == "(Table\u00a07b)"
    assert parsed.placeholders == [math]


@pytest.mark.parametrize("source, prepared", [
    ("Read (the next section 7.2)", "Read (the next section 7.2)"),
    ("Read [Section 7.2)" , "Read [Section 7.2)"),
    ("Read [Section 7.2]", "Read [Section 7.3]"),
    ("Read (the first 4 characters)", "Read (the first 4 characters)"),
])
def test_trailing_document_reference_skips_ordinary_or_mismatched_content(source, prepared):
    engine = object.__new__(ILTranslator)
    input_text = engine.TranslateInput(prepared, [])
    assert engine._split_trailing_document_reference(prepared, source, input_text) == (prepared, "", input_text)
