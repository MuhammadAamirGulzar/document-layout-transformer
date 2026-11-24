from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from babeldoc.format.pdf.document_il.il_version_1 import Page, PdfParagraph
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
from babeldoc.translator.translator import BaseTranslator


class EchoTranslator(BaseTranslator):
    def do_translate(self, text, rate_limit_params=None):
        return text

    def do_llm_translate(self, text, rate_limit_params=None):
        raise NotImplementedError


def translate_block(text, *, label="wired_table_cell_fixed_rtl", lang="en-ar"):
    translator = EchoTranslator("en", lang, ignore_cache=True)
    engine = object.__new__(ILTranslator)
    engine.translate_engine = translator
    engine.translation_config = SimpleNamespace(lang_out=lang, raise_if_cancelled=lambda: None)
    engine.support_llm_translate = False
    engine.use_as_fallback = False
    engine.pre_translate_paragraph = Mock(return_value=(text, object()))
    engine._normalize_arabic_output = lambda value: value
    engine.shape_arabic_text = lambda value: value
    engine.post_translate_paragraph = Mock()
    paragraph = PdfParagraph(unicode=text, layout_label=label)
    engine.translate_paragraph(paragraph, Page(), pbar=Mock(), tracker=Mock())
    return engine.post_translate_paragraph.call_args.args[-1], translator.translate_call_count


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("INCOMING", "\u0627\u0644\u0648\u0627\u0631\u062f\u0629"),
        ("OUTGOING", "\u0627\u0644\u0635\u0627\u062f\u0631\u0629"),
    ],
)
def test_arabic_table_payment_label_is_translated(source, expected):
    assert translate_block(source) == (expected, 0)


@pytest.mark.parametrize("source", ["SCBLQAQAXXX", "CBQAQAQA", "QA64SCBL00000000000137502560"])
def test_table_banking_identifiers_stay_verbatim(source):
    assert translate_block(source) == (source, 1)


def test_payment_override_is_scoped_to_arabic_table_cells():
    assert translate_block("OUTGOING", lang="fr") == ("OUTGOING", 1)
    assert translate_block("INCOMING", label="plain text") == ("INCOMING", 1)
