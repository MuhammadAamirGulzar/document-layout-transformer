"""B-35: the job reports how many text blocks the engine could not translate."""
import asyncio
import inspect
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import server
from babeldoc.format.pdf.document_il.midend import il_translator
from babeldoc.translator import translator as translator_module

ROOT = Path(__file__).resolve().parent.parent


def _text_after(source, marker):
    assert marker in source, "text not found: " + marker
    return source.split(marker, 1)[1]


# --- the engine counts the blocks it could not translate ----------------------


def test_translator_starts_with_zero_count():
    class Dummy(translator_module.BaseTranslator):
        name = "dummy"

        def do_translate(self, text, rate_limit_params=None):
            return text

        def do_llm_translate(self, text, rate_limit_params=None):
            return text

    Dummy.__abstractmethods__ = frozenset()
    assert Dummy("en", "ar", True).untranslated_count == 0


def test_nllb_error_path_counts_the_block():
    source = inspect.getsource(translator_module.NLLBTranslator)
    tail = _text_after(source, "Error during NLLB translation")
    assert "untranslated_count" in tail.split("return cleaned_text", 1)[0]


def test_marian_error_path_counts_the_block():
    source = inspect.getsource(translator_module.MarianMTTranslator)
    tail = _text_after(source, "Error during MarianMT translation")
    assert "untranslated_count" in tail.split("return cleaned_text", 1)[0]


def test_content_filter_exit_counts_the_paragraph():
    source = inspect.getsource(il_translator.ILTranslator)
    tail = _text_after(source, "self.add_content_filter_hint(page, paragraph)")
    assert "untranslated_count" in tail.split("return", 1)[0]


def test_paragraph_error_exit_counts_the_paragraph():
    source = inspect.getsource(il_translator.ILTranslator)
    before = source.split("# ignore error and continue", 1)[0]
    assert "# ignore error and continue" in source
    assert "untranslated_count" in before[-200:]


# --- the server reads the count and reports it --------------------------------


def test_helper_reads_count_from_config():
    config = SimpleNamespace(translator=SimpleNamespace(untranslated_count=4))
    assert server._untranslated_count(config) == 4


def test_helper_defaults_to_zero():
    bad_configs = [
        SimpleNamespace(),
        SimpleNamespace(translator=None),
        SimpleNamespace(translator=SimpleNamespace()),
        SimpleNamespace(translator=SimpleNamespace(untranslated_count="x")),
        SimpleNamespace(translator=SimpleNamespace(untranslated_count=-3)),
    ]
    for config in bad_configs:
        assert server._untranslated_count(config) == 0, config


def test_finish_copies_the_count_into_the_session():
    source = inspect.getsource(server._translation_events)
    assert 'session["untranslated_count"] = _untranslated_count(config)' in source


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    (tmp_path / "sandbox").mkdir()
    monkeypatch.setattr(server, "TEMP_DIR", tmp_path / "sandbox")
    store = {}
    monkeypatch.setattr(server, "translation_sessions", store)
    return store


def _get(path):
    async def go():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(go())


def test_status_reports_the_count(sessions):
    sid = str(uuid.uuid4())
    sessions[sid] = {"status": "completed", "progress": 100, "stage": "Done", "untranslated_count": 3}
    response = _get(f"/translate/status/{sid}")
    assert response.status_code == 200
    assert response.json()["untranslated_count"] == 3


def test_status_count_defaults_to_zero(sessions):
    sid = str(uuid.uuid4())
    sessions[sid] = {"status": "processing", "progress": 10, "stage": "Parse Paragraphs"}
    response = _get(f"/translate/status/{sid}")
    assert response.status_code == 200
    assert response.json()["untranslated_count"] == 0


def test_status_keeps_the_old_fields(sessions):
    sid = str(uuid.uuid4())
    sessions[sid] = {"status": "completed", "progress": 100, "stage": "Done"}
    body = _get(f"/translate/status/{sid}").json()
    for key in ("session_id", "status", "progress", "stage", "error",
                "has_mono", "has_dual", "events", "next_event_id"):
        assert key in body, key


# --- the page shows it --------------------------------------------------------


def _page():
    return (ROOT / "frontend" / "index.html").read_text(encoding="utf-8", errors="replace")


def test_page_has_a_note_for_untranslated_blocks():
    html = _page()
    assert 'id="untranslatedNote"' in html
    assert "function updateUntranslatedNote" in html


def test_page_updates_the_note_on_every_poll():
    html = _page()
    assert "updateUntranslatedNote(state.untranslated_count" in html
    assert "could not be translated" in html
