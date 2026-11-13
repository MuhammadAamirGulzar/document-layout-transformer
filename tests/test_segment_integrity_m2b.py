"""M2b: token-count guard, safe segment splitting, no-EOS detection, and cache-fallback safety."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from babeldoc.format.pdf.document_il.utils.sentence_splitter import (
    SEGMENTATION_VERSION,
    split_text_for_token_budget,
)
from babeldoc.translator import translator as tm


class FakeTensor(list):
    @property
    def shape(self):
        rows = len(self)
        cols = len(self[0]) if rows else 0
        return (rows, cols)

    def to(self, _device):
        return self


class FakeBatch(dict):
    def to(self, _device):
        return self


class FakeSeq2SeqTokenizer:
    """Deterministic whitespace tokenizer mimicking HF __call__ & batch_decode."""

    def __init__(self, eos_token_id=2, pad_token_id=1, model_max_length=1024):
        self.eos_token_id = eos_token_id
        self.pad_token_id = pad_token_id
        self.model_max_length = model_max_length
        self.src_lang = None
        self.call_kwargs = []
        self._id_to_word = {}
        self._word_to_id = {}
        self._next_id = 10

    def _encode_words(self, text: str) -> list[int]:
        ids = []
        for word in text.split():
            if word not in self._word_to_id:
                tok_id = self._next_id
                self._next_id += 1
                self._word_to_id[word] = tok_id
                self._id_to_word[tok_id] = word
            ids.append(self._word_to_id[word])
        # Count BOS + EOS just like real SentencePiece/Marian tokenizers.
        return [0, *ids, self.eos_token_id]

    def __call__(self, texts, return_tensors=None, padding=True, truncation=False, max_length=None):
        self.call_kwargs.append({"truncation": truncation, "max_length": max_length})
        encoded = [self._encode_words(t) for t in texts]
        if truncation and max_length is not None:
            encoded = [seq[:max_length] for seq in encoded]
        return FakeBatch({"input_ids": FakeTensor(encoded)})

    def batch_decode(self, sequences, skip_special_tokens=True):
        decoded = []
        for seq in sequences:
            words = [
                self._id_to_word.get(int(tok), f"tok{int(tok)}")
                for tok in seq
                if not skip_special_tokens or int(tok) not in (0, self.eos_token_id, self.pad_token_id)
            ]
            decoded.append("TR:" + " ".join(words))
        return decoded


class FakeSeq2SeqModel:
    """Deterministic stub generating either complete (EOS) or capped (no-EOS) sequences."""

    def __init__(self, eos_token_id=2, pad_token_id=1, truncate_when_gt_words=None, fail=False):
        self.config = SimpleNamespace(eos_token_id=eos_token_id, pad_token_id=pad_token_id)
        self.truncate_when_gt_words = truncate_when_gt_words
        self.fail = fail
        self.seen_input_lengths = []

    def generate(self, input_ids, max_length=512, **_kwargs):
        if self.fail:
            raise RuntimeError("synthetic CUDA OOM")
        seq = list(input_ids[0])
        self.seen_input_lengths.append(len(seq))
        content = [tok for tok in seq if tok not in (0, self.config.eos_token_id, self.config.pad_token_id)]
        if self.truncate_when_gt_words is not None and len(content) > self.truncate_when_gt_words:
            # Fill up to max_length without emitting EOS.
            out = (content * ((max_length // max(1, len(content))) + 2))[:max_length]
            return FakeTensor([out])
        out = [0, *content, self.config.eos_token_id][:max_length]
        return FakeTensor([out])


@pytest.fixture
def make_fake_cache(monkeypatch):
    cache = Mock()
    cache.get.return_value = None
    monkeypatch.setattr(tm, "TranslationCache", lambda *args, **kwargs: cache)
    monkeypatch.setattr(tm._translate_rate_limiter, "wait", lambda: None)
    return cache


def test_split_text_for_token_budget_respects_sentences_clauses_and_paired_tags():
    text = (
        "First <b1>very important clause</b1>, with Fig. 3 details; "
        "second clause follows cleanly. Third sentence ends here."
    )
    count_fn = lambda s: len(s.split()) + 2

    chunks, oversized = split_text_for_token_budget(text, count_fn, max_tokens=9)

    assert not oversized
    assert len(chunks) >= 2
    assert all(count_fn(c) <= 9 for c in chunks)
    # Paired rich-text tag must not be broken across chunks.
    assert any("<b1>very important clause</b1>" in c for c in chunks)
    assert " ".join(chunks) == text


def test_split_text_for_token_budget_flags_indivisible_oversized_unit():
    text = "alpha supercalifragilisticexpialidocious beta"
    # Charge per-word budget high only for the long token.
    count_fn = lambda s: sum(12 if len(w) > 15 else 1 for w in s.split())

    chunks, oversized = split_text_for_token_budget(text, count_fn, max_tokens=6)

    assert oversized is True
    assert chunks == ["alpha", "supercalifragilisticexpialidocious", "beta"]


def test_cache_impact_parameters_include_segmentation_version_and_max_length():
    nllb = tm.NLLBTranslator("en", "ar", model_name="nllb-3.3b", max_length=256, ignore_cache=True)
    marian = tm.MarianMTTranslator("en", "ar", max_length=128, ignore_cache=True)

    assert nllb.cache.params["segmentation_version"] == SEGMENTATION_VERSION
    assert nllb.cache.params["max_length"] == 256
    assert marian.cache.params["segmentation_version"] == SEGMENTATION_VERSION
    assert marian.cache.params["max_length"] == 128


@pytest.mark.parametrize(
    ("cls", "kwargs"),
    [
        (tm.NLLBTranslator, {"model_name": "nllb-3.3b", "max_length": 16}),
        (tm.NLLBTranslator, {"model_name": "nllb", "max_length": 16}),
        (tm.MarianMTTranslator, {"max_length": 16}),
    ],
)
def test_error_fallback_returns_source_but_never_caches(make_fake_cache, cls, kwargs):
    engine = cls("en", "ar", **kwargs)
    engine._tokenizer = FakeSeq2SeqTokenizer()
    engine._model = FakeSeq2SeqModel(fail=True)
    engine._forced_bos_token_id = 3
    engine._load_model = lambda: None

    result = engine.translate("Pure prose block without numbers.")

    assert result == "Pure prose block without numbers."
    assert engine.untranslated_count == 1
    make_fake_cache.set.assert_not_called()


@pytest.mark.parametrize(
    ("cls", "kwargs"),
    [
        (tm.NLLBTranslator, {"model_name": "nllb-3.3b", "max_length": 10}),
        (tm.MarianMTTranslator, {"max_length": 10}),
    ],
)
def test_oversized_input_is_split_without_truncating_tail(make_fake_cache, cls, kwargs):
    engine = cls("en", "ar", **kwargs)
    engine._tokenizer = FakeSeq2SeqTokenizer()
    engine._model = FakeSeq2SeqModel()
    engine._forced_bos_token_id = 3
    engine._load_model = lambda: None

    source = (
        "one two three four five six; "
        "seven eight nine ten eleven twelve, "
        "thirteen fourteen 2026"
    )
    result = engine.translate(source)

    assert len(engine._model.seen_input_lengths) >= 2
    assert all(length <= 10 for length in engine._model.seen_input_lengths)
    assert "fourteen" in result
    assert "2026" in result
    assert engine.incomplete_count == 0
    make_fake_cache.set.assert_called_once_with(source, result)


def test_output_max_length_no_eos_on_single_clause_flags_incomplete_and_skips_cache(make_fake_cache):
    engine = tm.NLLBTranslator("en", "ar", model_name="nllb-3.3b", max_length=10)
    engine._tokenizer = FakeSeq2SeqTokenizer()
    engine._model = FakeSeq2SeqModel(truncate_when_gt_words=4)
    engine._forced_bos_token_id = 3
    engine._load_model = lambda: None

    result = engine.translate("alpha beta gamma delta epsilon")

    assert result.startswith("TR:")
    assert engine.incomplete_count == 1
    make_fake_cache.set.assert_not_called()


def test_output_ending_at_exact_max_length_with_eos_is_complete(make_fake_cache):
    engine = tm.MarianMTTranslator("en", "ar", max_length=7)
    engine._tokenizer = FakeSeq2SeqTokenizer()
    engine._model = FakeSeq2SeqModel()
    engine._load_model = lambda: None

    # 5 words + BOS + EOS = 7 tokens (exact max_length, ends cleanly on EOS).
    source = "one two three four five"
    result = engine.translate(source)

    assert result == "TR:one two three four five"
    assert engine.incomplete_count == 0
    make_fake_cache.set.assert_called_once_with(source, result)


def test_multi_clause_output_truncation_recovers_by_splitting_on_clause(make_fake_cache):
    engine = tm.NLLBTranslator("en", "ar", model_name="nllb-1.3b", max_length=20)
    engine._tokenizer = FakeSeq2SeqTokenizer()
    # Input fits within encoder max_length=20 (16 words -> 18 tokens),
    # but decoder truncates > 10 words unless split cleanly at the semicolon.
    engine._model = FakeSeq2SeqModel(truncate_when_gt_words=10)
    engine._forced_bos_token_id = 3
    engine._load_model = lambda: None

    source = (
        "alpha beta gamma delta epsilon zeta eta theta; "
        "iota kappa lambda mu nu xi omicron pi"
    )
    result = engine.translate(source)

    assert "alpha" in result and "pi" in result
    assert engine.incomplete_count == 0
    make_fake_cache.set.assert_called_once_with(source, result)


def test_indivisible_oversized_token_logs_incomplete_and_skips_cache(make_fake_cache):
    class HugeWordTokenizer(FakeSeq2SeqTokenizer):
        def _encode_words(self, text: str) -> list[int]:
            if "monolith" in text:
                return [0] + [99] * 15 + [self.eos_token_id]
            return super()._encode_words(text)

    engine = tm.NLLBTranslator("en", "ar", model_name="nllb", max_length=10)
    engine._tokenizer = HugeWordTokenizer()
    engine._model = FakeSeq2SeqModel()
    engine._forced_bos_token_id = 3
    engine._load_model = lambda: None

    result = engine.translate("Keep monolith intact.")

    assert engine.incomplete_count == 1
    make_fake_cache.set.assert_not_called()
