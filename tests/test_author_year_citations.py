from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
from babeldoc.translator.translator import _protect_numerals, _restore_numerals


@pytest.mark.parametrize("citation", [
    "(García et al., 2022a; O’Neill and Lee, 2019)",
    "Martin et al. (2023)",
    "(Nguyen, 2017)",
])
def test_author_year_citation_is_protected_as_a_single_span(citation):
    protected, mapping = _protect_numerals(f"See the analysis {citation} for context.")
    assert protected == "See the analysis {0} for context."
    assert mapping == {"{0}": citation}
    assert _restore_numerals("راجع الدراسة {0}.", mapping) == f"راجع الدراسة {citation}."


def test_ordinary_parenthetical_with_year_remains_translatable():
    protected, mapping = _protect_numerals("Results (since 2020) improved.")
    assert "since" in protected
    assert mapping == {"{0}": "2020"}


def test_long_paragraph_keeps_the_abbreviation_and_year_in_the_same_call():
    translator = object.__new__(ILTranslator)
    translator._pre_translate_terminology = lambda text: text
    translator.translate_engine = Mock()
    translator.translate_engine.translate.side_effect = lambda text, **kwargs: text
    first = ("The report presents the measured changes in the observed results "
             "and connects them to related work by Martin et al. (2023) in a separate study.")
    second = "The remaining results are discussed separately."

    translator._translate_with_line_preservation(f"{first} {second}", {})

    assert [call.args[0] for call in translator.translate_engine.translate.call_args_list] == [first, second]


def test_arabic_layout_marks_the_whole_citation_as_one_ltr_span():
    translator = object.__new__(ILTranslator)
    translator.translation_config = SimpleNamespace(lang_out="ar")
    output = translator._normalize_arabic_output("تؤكد الدراسة (Nguyen et al., 2017) هذه النتيجة")
    assert "\u200e(Nguyen\u00a0et\u00a0al.,\u00a02017)\u200e" in output
