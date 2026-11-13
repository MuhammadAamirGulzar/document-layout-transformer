"""M5 tests: batched Seq2Seq decoding parity, model-load & block heartbeats, ETA, and log filtering."""
from __future__ import annotations

import asyncio
import logging
import time
import types
import uuid
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

import server
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
from babeldoc.progress_monitor import ProgressMonitor
from babeldoc.translator import translator as tm


class _FakeTensorBatch:
    def __init__(self, rows):
        self.rows = [list(r) for r in rows]

    @property
    def shape(self):
        width = len(self.rows[0]) if self.rows else 0
        return (len(self.rows), width)

    def __getitem__(self, item):
        if isinstance(item, slice):
            return _FakeTensorBatch(self.rows[item])
        return self.rows[item]

    def __len__(self):
        return len(self.rows)


class _FakeSeq2SeqTokenizer:
    eos_token_id = 2
    pad_token_id = 1
    model_max_length = 512

    def __init__(self):
        self.src_lang = None

    def __call__(self, texts, return_tensors=None, padding=True, truncation=False, max_length=None):
        encoded = []
        for text in texts:
            toks = [100 + (i % 50) for i, _ in enumerate(text.split())] + [self.eos_token_id]
            if truncation and max_length is not None:
                toks = toks[:max_length]
            encoded.append(toks)
        if padding and encoded:
            width = max(len(r) for r in encoded)
            encoded = [r + [self.pad_token_id] * (width - len(r)) for r in encoded]
        return {
            "input_ids": _FakeTensorBatch(encoded),
            "_raw_texts": list(texts),
        }

    def batch_decode(self, token_rows, skip_special_tokens=True):
        out = []
        for row in token_rows:
            out.append(self._row_to_text.get(tuple(row), ""))
        return out


class _FakeSeq2SeqModel:
    def __init__(self, tokenizer, outputs_by_text, *, force_no_eos_for=None, max_gen_width=None):
        self.config = types.SimpleNamespace(eos_token_id=2, pad_token_id=1)
        self.tokenizer = tokenizer
        self.outputs_by_text = outputs_by_text
        self.force_no_eos_for = set(force_no_eos_for or ())
        self.max_gen_width = max_gen_width
        self.generate_calls: list[list[str]] = []

    def generate(self, **kwargs):
        texts = kwargs["_raw_texts"]
        self.generate_calls.append(list(texts))
        rows = []
        row_map = {}
        width = self.max_gen_width or 16
        for idx, text in enumerate(texts):
            if text in self.force_no_eos_for:
                seq = [500 + idx] * width
            else:
                seq = [300 + idx, 301 + idx, self.tokenizer.eos_token_id]
            padded = list(seq)
            rows.append(padded)
        if rows:
            max_w = max(len(r) for r in rows)
            for idx, r in enumerate(rows):
                if len(r) < max_w:
                    r.extend([self.tokenizer.pad_token_id] * (max_w - len(r)))
                row_map[tuple(r)] = self.outputs_by_text.get(text := texts[idx], f"AR[{text}]")
        self.tokenizer._row_to_text = row_map
        return _FakeTensorBatch(rows)


def _patch_cache_and_limiter(monkeypatch):
    cache_store = {}
    cache = Mock()
    cache.get.side_effect = lambda key: cache_store.get(key)
    cache.set.side_effect = lambda key, val: cache_store.__setitem__(key, val)
    monkeypatch.setattr(tm, "TranslationCache", lambda *a, **kw: cache)
    monkeypatch.setattr(tm._translate_rate_limiter, "wait", lambda: None)
    return cache, cache_store


@pytest.mark.parametrize(
    "factory",
    [
        lambda: tm.NLLBTranslator("en", "ar", model_name="nllb"),
        lambda: tm.MarianMTTranslator("en", "ar"),
    ],
    ids=["nllb-600m", "marianmt"],
)
def test_translate_batch_decodes_in_batch_with_single_sentence_parity(monkeypatch, factory):
    _patch_cache_and_limiter(monkeypatch)
    engine = factory()
    tok = _FakeSeq2SeqTokenizer()
    outputs = {
        "Short sentence one.": "جملة قصيرة أولى.",
        "This longer scientific sentence reports {0} gain at {1} learning rate.": (
            "تسجل هذه الجملة العلمية الأطول مكسباً قدره {0} عند معدل تعلم {1}."
        ),
        "Final concluding statement in English.": "العبارة الختامية الأخيرة بالإنجليزية.",
    }
    model = _FakeSeq2SeqModel(tok, outputs)
    engine._tokenizer = tok
    engine._model = model
    monkeypatch.setattr(engine, "_load_model", lambda: None)

    inputs = [
        "This longer scientific sentence reports 4.2% gain at 2e-5 learning rate.",
        "2025",
        "Short sentence one.",
        "Final concluding statement in English.",
    ]

    batched = engine.translate_batch(inputs)
    batch_gen_count = len(model.generate_calls)

    # Clear cache and compare against sequential single-sentence calls.
    engine.ignore_cache = True
    sequential = [engine.translate(item) for item in inputs]

    assert batched == sequential
    assert batched[1] == "2025"  # passthrough never sent to generate()
    assert batch_gen_count == 1  # all 3 non-passthrough sentences decoded together


def test_translate_batch_recovers_only_the_truncated_row(monkeypatch):
    _patch_cache_and_limiter(monkeypatch)
    engine = tm.NLLBTranslator("en", "ar", model_name="nllb", max_length=12)
    tok = _FakeSeq2SeqTokenizer()
    long_text = "alpha, beta, gamma, delta, epsilon, zeta"
    outputs = {
        "Brief line.": "سطر موجز.",
        long_text: "طويل جداً",
        "alpha, beta,": "ألفا بيتا",
        "gamma, delta,": "غاما دلتا",
        "epsilon, zeta": "إبسيلون زيتا",
    }
    model = _FakeSeq2SeqModel(tok, outputs, force_no_eos_for={long_text}, max_gen_width=12)
    engine._tokenizer = tok
    engine._model = model
    monkeypatch.setattr(engine, "_load_model", lambda: None)

    res = engine.translate_batch(["Brief line.", long_text])

    assert res[0] == "سطر موجز."
    # Sibling sentence kept normal result while truncated row split into clauses cleanly.
    assert "ألفا بيتا" in res[1] and "إبسيلون زيتا" in res[1]


def test_translate_batch_falls_back_cleanly_on_batch_error(monkeypatch):
    _patch_cache_and_limiter(monkeypatch)
    engine = tm.NLLBTranslator("en", "ar", model_name="nllb")
    tok = _FakeSeq2SeqTokenizer()
    engine._tokenizer = tok
    engine._model = object()
    monkeypatch.setattr(engine, "_load_model", lambda: None)
    monkeypatch.setattr(
        engine,
        "_run_seq2seq_batch",
        Mock(side_effect=RuntimeError("OOM during batch")),
    )
    monkeypatch.setattr(engine, "do_translate", lambda text, rl=None: f"AR[{text}]")

    out = engine.translate_batch(["First test sentence.", "Second test sentence."])
    assert out == ["AR[First test sentence.]", "AR[Second test sentence.]"]


def test_il_translator_batches_multi_sentence_paragraphs_for_seq2seq(monkeypatch):
    _patch_cache_and_limiter(monkeypatch)
    il = object.__new__(ILTranslator)
    il.stage_name = "Translate Paragraphs"
    il._pre_translate_terminology = lambda text: text
    il.translation_config = types.SimpleNamespace(
        lang_in="en",
        lang_out="ar",
        raise_if_cancelled=lambda: None,
        progress_monitor=None,
    )
    engine = tm.NLLBTranslator("en", "ar", model_name="nllb")
    il.translate_engine = engine
    batch_spy = Mock(return_value=["جملة أولى.", "جملة ثانية."])
    monkeypatch.setattr(engine, "translate_batch", batch_spy)

    p = (
        "1. First experimental finding across several long evaluation datasets "
        "shows consistent improvements. "
        "Second experimental finding holds across all evaluated models."
    )
    assert len(p) > 150

    out = il._translate_with_line_preservation(p, {})

    assert out == "1. جملة أولى. جملة ثانية."
    batch_spy.assert_called_once()


def test_model_load_status_callback_and_progress_monitor_heartbeat():
    events = []
    pm = ProgressMonitor(
        stages=[("Translate Paragraphs", 1.0)],
        progress_change_callback=lambda **kw: events.append(kw),
        report_interval=0.1,
    )
    engine = tm.NLLBTranslator("en", "ar", model_name="nllb", ignore_cache=True)
    engine.set_model_status_callback(
        lambda loading, message="", elapsed_seconds=None: pm.heartbeat(
            stage="Translate Paragraphs",
            message=message,
            model_loading=loading,
            model_load_seconds=elapsed_seconds,
            force=True,
        )
    )
    engine._emit_model_status(loading=True, message="Loading...")
    engine._emit_model_status(loading=False, message="Ready", elapsed_seconds=1.25)

    hb = [e for e in events if e["type"] == "heartbeat"]
    assert len(hb) == 2
    assert hb[0]["model_loading"] is True
    assert hb[1]["model_loading"] is False
    assert hb[1]["model_load_seconds"] == 1.25


def test_defensible_eta_excludes_model_load_and_requires_measured_blocks():
    session = {
        "status": "processing",
        "_started_mono_at": time.monotonic() - 10.0,
    }
    server._job_event(
        session,
        {
            "type": "progress_start",
            "stage": "Translate Paragraphs",
            "stage_current": 0,
            "stage_total": 10,
        },
    )
    server._job_event(
        session,
        {"type": "heartbeat", "stage": "Translate Paragraphs", "model_loading": True},
    )
    assert server._estimate_eta_seconds(session) is None

    server._job_event(
        session,
        {
            "type": "heartbeat",
            "stage": "Translate Paragraphs",
            "model_loading": False,
            "model_load_seconds": 8.0,
        },
    )
    # Anchor set just now; backdate anchor by 2.0s to simulate 2 blocks completing cleanly.
    anchor_cur, _ = session["_stage_rate_anchor"]
    session["_stage_rate_anchor"] = (anchor_cur, time.monotonic() - 2.0)
    server._job_event(
        session,
        {
            "type": "progress_update",
            "stage": "Translate Paragraphs",
            "stage_current": 1,
            "stage_total": 10,
        },
    )
    assert server._estimate_eta_seconds(session) is None  # only 1 block done

    server._job_event(
        session,
        {
            "type": "progress_update",
            "stage": "Translate Paragraphs",
            "stage_current": 2,
            "stage_total": 10,
        },
    )
    eta = server._estimate_eta_seconds(session)
    assert eta is not None
    # 2 blocks in ~2.0s => 8 remaining blocks ~8.0s (NOT polluted by the 8s model load).
    assert 6.0 <= eta <= 11.0


def test_status_poll_access_log_filter_suppresses_200_polls_only():
    flt = server._SuppressStatusPollAccessFilter()

    def make_rec(msg):
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, msg, (), None)

    assert flt.filter(make_rec('127.0.0.1 - "GET /translate/status/abc?after=3 HTTP/1.1" 200')) is False
    assert flt.filter(make_rec('127.0.0.1 - "GET /health HTTP/1.1" 200')) is False
    assert flt.filter(make_rec('127.0.0.1 - "GET /translate/status/abc?after=3 HTTP/1.1" 404')) is True
    assert flt.filter(make_rec('127.0.0.1 - "POST /translate/start HTTP/1.1" 200')) is True


def test_status_endpoint_reports_liveness_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "translation_sessions", {})
    sid = str(uuid.uuid4())
    out = tmp_path / sid
    out.mkdir()
    server.translation_sessions[sid] = {
        "status": "processing",
        "input_path": str(out / "input.pdf"),
        "output_directory": str(out),
        "source_lang": "en",
        "target_lang": "ar",
        "model": "nllb",
        "filename": "doc.pdf",
        "progress": 10,
        "error": None,
        "model_loading": True,
        "_started_mono_at": time.monotonic() - 1.5,
        "_last_activity_at": time.monotonic() - 0.2,
    }

    async def go():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            res = await c.get(f"/translate/status/{sid}")
            assert res.status_code == 200
            data = res.json()
            assert data["model_loading"] is True
            assert data["eta_seconds"] is None
            assert data["elapsed_seconds"] >= 1.4
            assert data["last_activity_seconds_ago"] >= 0.0

    asyncio.run(go())
