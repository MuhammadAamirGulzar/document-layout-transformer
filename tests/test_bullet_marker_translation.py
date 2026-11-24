from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from babeldoc.format.pdf.document_il.il_version_1 import Page, PdfCharacter, PdfFormula, PdfParagraph
from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder, ILTranslator


@pytest.mark.parametrize(
    ("source", "label", "glyph", "model_input", "render_input"),
    [
        ("<b1>Text-based tree", "list_item", "(cid:136)", "Text-based tree", "<b1> translated"),
        ("<b1>First step", "plain text", "\u2022", "First step", "<b1> translated"),
        ("<b1>Ordinary text", "plain text", "(cid:136)", "<b1>Ordinary text", "translated"),
        ("Call <b1> next", "list_item", "(cid:136)", "Call <b1> next", "translated"),
        ("<b1>Result", "list_item", "=", "<b1>Result", "translated"),
    ],
)
def test_only_a_leading_bullet_is_kept_out_of_local_translation(
    source, label, glyph, model_input, render_input
):
    engine = object.__new__(ILTranslator)
    engine.translation_config = SimpleNamespace(lang_out="ar", raise_if_cancelled=lambda: None)
    engine.translate_engine = Mock()
    engine.translate_engine.untranslated_count = 0
    engine.use_as_fallback = False
    engine.support_llm_translate = False
    marker = FormulaPlaceholder(
        1, PdfFormula(pdf_character=[PdfCharacter(char_unicode=glyph)]),
        "<b1>", r"<b1>",
    )
    prepared = engine.TranslateInput(source, [marker])
    engine.pre_translate_paragraph = Mock(return_value=(source, prepared))
    engine._translate_with_line_preservation = Mock(return_value="translated")
    engine._normalize_arabic_output = lambda text: text
    engine.shape_arabic_text = lambda text: text
    engine.post_translate_paragraph = Mock()
    paragraph = PdfParagraph(unicode=source, layout_label=label)

    engine.translate_paragraph(paragraph, Page(), pbar=Mock(), tracker=Mock())

    assert engine._translate_with_line_preservation.call_args.args[0] == model_input
    assert engine.post_translate_paragraph.call_args.args[-1] == render_input


def test_inline_list_numbers_stay_outside_model_input():
    translator = object.__new__(ILTranslator)
    translator.translate_engine = Mock()
    translator.translate_engine.translate.side_effect = ["intro", "first", "second", "third", "fourth"]
    source = "Checklist: (1) inspect the logs, (2) check the config; (3) test the build, (4) review the result."

    result = translator._translate_with_line_preservation(source, {})

    assert result == "intro: (1) first, (2) second; (3) third, (4) fourth"
    assert translator.translate_engine.translate.call_args_list == [
        call(text, rate_limit_params={})
        for text in ["Checklist", "inspect the logs", "check the config", "test the build", "review the result."]
    ]


@pytest.mark.parametrize(
    "source",
    [
        "See (1) in the text, (2) in the notes, (3) in the appendix.",
        "Checklist: (1) inspect the logs, (3) check the config, (4) test the build.",
        "Coordinates: (1) x, (2) y, (3) z.",
    ],
)
def test_unrelated_parenthesized_numbers_stay_in_the_sentence(source):
    translator = object.__new__(ILTranslator)
    translator.translate_engine = Mock()
    translator.translate_engine.translate.return_value = "translated"

    assert translator._translate_with_line_preservation(source, {}) == "translated"
    translator.translate_engine.translate.assert_called_once_with(source, rate_limit_params={})
