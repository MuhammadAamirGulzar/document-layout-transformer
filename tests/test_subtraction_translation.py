from unittest.mock import Mock

import pytest

from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator


@pytest.fixture
def translator():
    il = object.__new__(ILTranslator)
    il.translate_engine = Mock(
        model_name="facebook/nllb-200-distilled-600M",
        src_nllb="eng_Latn",
        tgt_nllb="arb_Arab",
    )
    il.translate_engine.translate.return_value = "translated"
    return il


@pytest.mark.parametrize(
    ("source", "model_input", "expected"),
    [
        (
            "7. Subtract the remainder from 98 and, if necessary, pad with a leading 0 to make a two digit number.",
            "Calculate 98 minus the remainder. If necessary, add a leading 0 so that the result has two digits.",
            "7. translated",
        ),
        ("3. Subtract 8 from 5.", "Calculate 5 minus 8.", "3. translated"),
        (
            "Step 4: Subtract the remainder from 10 to find the checksum",
            "Step 4: Calculate 10 minus the remainder to find the checksum",
            "translated",
        ),
        (
            "Step 3: Subtract the remainder calculated in step 2 from 10.",
            "Step 3: Calculate 10 minus the remainder calculated in step 2.",
            "translated",
        ),
        (
            "Subtract the remainder from 75 and record the result.",
            "Calculate 75 minus the remainder and record the result.",
            "translated",
        ),
    ],
)
def test_preserves_subtraction_order_and_steps(translator, source, model_input, expected):
    assert translator._translate_with_line_preservation(source, {}) == expected
    translator.translate_engine.translate.assert_called_once_with(model_input, rate_limit_params={})


@pytest.mark.parametrize(
    "source",
    [
        "Exclude the remainder from the report.",
        "Subtract 7 from 12.5.",
        "Subtract -7 from 12.",
        "Subtract 7 dollars from 12 dollars.",
        "Subtract 7 from 12 million.",
        "The instructions say to subtract 7 from 12.",
    ],
)
def test_leaves_other_wording_and_unsupported_operands_alone(translator, source):
    translator._translate_with_line_preservation(source, {})
    translator.translate_engine.translate.assert_called_once_with(source, rate_limit_params={})


def test_does_not_mistake_a_wrapped_unit_for_the_end_of_the_instruction(translator):
    translator._translate_with_line_preservation("Subtract 7 from 12\nmillion.", {})
    inputs = [call.args[0] for call in translator.translate_engine.translate.call_args_list]
    assert inputs == ["Subtract 7 from 12", "million."]


@pytest.mark.parametrize(
    ("field", "value"),
    [("model_name", "marian"), ("src_nllb", "fra_Latn"), ("tgt_nllb", "fra_Latn")],
)
def test_rewrite_stays_on_verified_model_and_language_pair(translator, field, value):
    setattr(translator.translate_engine, field, value)
    source = "Subtract 8 from 5."
    translator._translate_with_line_preservation(source, {})
    translator.translate_engine.translate.assert_called_once_with(source, rate_limit_params={})
