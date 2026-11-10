from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from babeldoc.format.pdf.document_il.il_version_1 import Page, PdfCharacter, PdfFormula, PdfParagraph
from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder, ILTranslator


def formula(tag_id, glyph):
    tag = f"<b{tag_id}>"
    return FormulaPlaceholder(
        tag_id, PdfFormula(pdf_character=[PdfCharacter(char_unicode=glyph)]), tag, tag
    )


@pytest.mark.parametrize("tagged", [False, True])
def test_trailing_index_reference_stays_out_of_local_translation(tagged):
    engine = object.__new__(ILTranslator)
    engine.translation_config = SimpleNamespace(lang_out="en-ar", raise_if_cancelled=lambda: None)
    engine.translate_engine = Mock()
    engine.use_as_fallback = False
    engine.support_llm_translate = False
    source = "3. Empty Table Entry: No production in M[X, a]"
    prepared = source
    if tagged:
        prepared = prepared.replace("[", "<b1>").replace("]", "<b2>")
    placeholders = [formula(1, "["), formula(2, "]")] if tagged else []
    engine.pre_translate_paragraph = Mock(
        return_value=(prepared, engine.TranslateInput(prepared, placeholders))
    )
    engine._translate_with_line_preservation = Mock(return_value="3. لا إنتاج في")
    engine.post_translate_paragraph = Mock()

    engine.translate_paragraph(
        PdfParagraph(unicode=source, layout_label="plain text"), Page(), pbar=Mock(), tracker=Mock()
    )

    assert engine._translate_with_line_preservation.call_args.args[0] == (
        "3. Empty Table Entry: No production in"
    )
    *_, parse_input, output = engine.post_translate_paragraph.call_args.args
    assert parse_input.placeholders == []
    assert output.endswith(" \u200eM[X, a]\u200e")


def test_index_reference_only_consumes_its_own_formula_tags():
    engine = object.__new__(ILTranslator)
    unrelated = formula(1, "=")
    source = "Use = when reading M[X, a]"
    prepared = "Use <b1> when reading M<b2>X, a<b3>"
    input_text = engine.TranslateInput(
        prepared, [unrelated, formula(2, "["), formula(3, "]")]
    )

    body, reference, parse_input = engine._split_trailing_index_reference(
        prepared, source, input_text
    )

    assert body == "Use <b1> when reading"
    assert reference == "M[X, a]"
    assert parse_input.placeholders == [unrelated]


@pytest.mark.parametrize(
    ("source", "prepared", "placeholders"),
    [
        ("See [the next section]", "See [the next section]", []),
        ("Read M[X, a] before continuing", "Read M[X, a] before continuing", []),
        (
            "Read M[X, a]", "Read M<b1>X, a<b2>", [formula(1, "("), formula(2, ")")]
        ),
    ],
)
def test_trailing_reference_does_not_consume_prose_or_other_formulas(
    source, prepared, placeholders
):
    engine = object.__new__(ILTranslator)
    input_text = engine.TranslateInput(prepared, placeholders)

    assert engine._split_trailing_index_reference(prepared, source, input_text) == (
        prepared, "", input_text
    )
