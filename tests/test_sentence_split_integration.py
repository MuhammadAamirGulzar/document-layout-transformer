"""M2a integration: ILTranslator._translate_with_line_preservation uses abbreviation-aware sentence splitting."""
from types import SimpleNamespace
from unittest.mock import Mock

from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator


def _make_translator():
    translator = object.__new__(ILTranslator)
    translator._pre_translate_terminology = lambda text: text
    translator.translate_engine = Mock()
    translator.translate_engine.translate.side_effect = lambda text, **kwargs: f"TR[{text}]"
    return translator


def test_long_paragraph_does_not_split_on_figure_latin_or_et_al_abbreviations():
    translator = _make_translator()
    first = (
        "As shown in Fig. 3 and reported by Smith et al. (2020), using a small "
        "threshold, e.g. 0.5, consistently improves overall recall across datasets."
    )
    second = "Further ablation runs in Sec. 4 confirm the same trend under noise."
    assert len(f"{first} {second}") > 150

    result = translator._translate_with_line_preservation(f"{first} {second}", {})

    calls = [call.args[0] for call in translator.translate_engine.translate.call_args_list]
    assert calls == [first, second]
    assert result == f"TR[{first}] TR[{second}]"


def test_long_paragraph_with_only_abbreviation_periods_stays_single_call():
    translator = _make_translator()
    paragraph = (
        "Dr. Smith and Prof. Lee evaluated Eq. (3) and Ref. [12] using "
        "approx. 30 samples, i.e. balanced batches from Fig. 2 and Tab. 1, "
        "throughout the U.S. benchmark evaluation."
    )
    assert len(paragraph) > 150

    result = translator._translate_with_line_preservation(paragraph, {})

    calls = [call.args[0] for call in translator.translate_engine.translate.call_args_list]
    assert calls == [paragraph]
    assert result == f"TR[{paragraph}]"


def test_long_paragraph_preserves_author_year_citation_and_trailing_sentence():
    translator = _make_translator()
    first = (
        "Empirical findings align with prior multilingual studies "
        "(García et al., 2022a; O’Neill and Lee, 2019) across benchmarks."
    )
    second = "See Appendix A.1 for hyperparameter settings and Fig. 4 for latency."
    assert len(f"{first} {second}") > 150

    translator._translate_with_line_preservation(f"{first} {second}", {})

    calls = [call.args[0] for call in translator.translate_engine.translate.call_args_list]
    assert calls == [first, second]


def test_dummy_il_method_binding_compatibility():
    class DummyIL:
        translation_config = SimpleNamespace(lang_in="en", lang_out="ar", debug=False)
        _pre_translate_terminology = staticmethod(lambda text: text)
        _inline_numbered_list = staticmethod(ILTranslator._inline_numbered_list)
        _translate_with_line_preservation = ILTranslator._translate_with_line_preservation

    dummy = DummyIL()
    dummy.translate_engine = Mock()
    dummy.translate_engine.translate.side_effect = lambda text, **kwargs: text

    first = (
        "In Fig. 1, Dr. Kim used e.g. 0.25 dropout and compared vs. the baseline "
        "model across 120 controlled evaluation runs on multi-domain benchmarks."
    )
    second = "Results improved steadily across all metrics."
    assert len(f"{first} {second}") > 150

    dummy._translate_with_line_preservation(f"{first} {second}", {})

    calls = [call.args[0] for call in dummy.translate_engine.translate.call_args_list]
    assert calls == [first, second]
