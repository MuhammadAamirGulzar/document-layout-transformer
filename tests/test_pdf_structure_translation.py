from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from babeldoc.format.pdf.document_il.il_version_1 import PdfParagraph
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator


@pytest.mark.parametrize("symbol", ["I", "X"])
def test_table_letter_is_preserved_before_translation(symbol):
    engine = object.__new__(ILTranslator)
    engine.support_llm_translate = False
    engine.translation_config = SimpleNamespace(
        disable_rich_text_translate=False, min_text_length=5
    )
    engine.get_translate_input = Mock(return_value=engine.TranslateInput(symbol, []))
    paragraph = PdfParagraph(unicode=symbol, layout_label="wired_table_cell_fixed_rtl")

    assert engine.pre_translate_paragraph(paragraph, Mock(), {}, {}) == (None, None)
    engine.get_translate_input.assert_not_called()


def test_letter_in_body_text_still_goes_to_translation():
    engine = object.__new__(ILTranslator)
    engine.support_llm_translate = False
    engine.translation_config = SimpleNamespace(
        disable_rich_text_translate=False, min_text_length=5
    )
    engine.get_translate_input = Mock(return_value=engine.TranslateInput("I", []))
    paragraph = PdfParagraph(unicode="I", layout_label="plain text")

    text, _ = engine.pre_translate_paragraph(paragraph, Mock(), {}, {})

    assert text == "I"
    engine.get_translate_input.assert_called_once()


@pytest.mark.parametrize(
    ("source", "expected", "model_input"),
    [
        ("1. Standards Document", "1. translated", "Standards Document"),
        ("5. Check Digit Generation", "5. translated", "Check Digit Generation"),
        ("5. Check Digit Generation .... 1312", "5. translated .... 1312", "Check Digit Generation"),
        ("3.1. Validation", "3.1. translated", "Validation"),
        ("1.5 billion", "translated", "1.5 billion"),
    ],
)
def test_list_and_toc_markers_survive_local_translation(source, expected, model_input):
    engine = object.__new__(ILTranslator)
    engine.translate_engine = Mock()
    engine.translate_engine.translate.return_value = "translated"

    assert engine._translate_with_line_preservation(source, {}) == expected
    engine.translate_engine.translate.assert_called_once_with(model_input, rate_limit_params={})
