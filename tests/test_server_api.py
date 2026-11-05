import asyncio
import json
import sys
import types
import uuid
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402

PDF_BYTES = b"%PDF-1.4 test"


class _DummyTranslator:
    def __init__(self, *args, **kwargs):
        pass


def _fake_config(**kwargs):
    return types.SimpleNamespace(**kwargs)


def _make_fake(write_mono=True, write_dual=True, fail_with=None):
    async def fake_async_translate(config):
        if fail_with:
            yield {"type": "error", "error": fail_with}
            return
        yield {
            "type": "stage_summary",
            "stages": [
                {"name": "Parse Paragraphs", "percent": 0.4},
                {"name": "Translate Paragraphs", "percent": 0.6},
            ],
        }
        yield {"type": "progress_start", "stage": "Translate Paragraphs",
               "stage_current": 0, "stage_total": 2, "overall_progress": 40}
        yield {"type": "progress_update", "stage": "Translate Paragraphs", "stage_progress": 50,
               "stage_current": 1, "stage_total": 2, "overall_progress": 70}
        yield {"type": "progress_end", "stage": "Translate Paragraphs",
               "stage_total": 2, "overall_progress": 100}
        out = Path(config.output_dir)
        if write_mono:
            (out / "doc.en-ar.mono.pdf").write_bytes(b"%PDF-1.4 mono")
        if write_dual:
            (out / "doc.en-ar.dual.pdf").write_bytes(b"%PDF-1.4 dual")
        yield {"type": "finish", "translate_result": None}
    return fake_async_translate


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(server, "translation_sessions", {})
    for name in ("NLLBTranslator", "MarianMTTranslator", "OpenAITranslator"):
        monkeypatch.setattr(server, name, _DummyTranslator)
    monkeypatch.setattr(server, "TranslationConfig", _fake_config)
    monkeypatch.setattr(server, "async_translate", _make_fake())
    return monkeypatch


def _run(fn):
    async def main():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await fn(client)
    return asyncio.run(main())


def _start(client, name="small.pdf", source="en", target="ar", model="nllb"):
    return client.post(
        "/translate/start",
        files={"file": (name, PDF_BYTES, "application/pdf")},
        data={"source_lang": source, "target_lang": target, "model": model},
    )


def _events(text):
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if block.startswith("data: "):
            events.append(json.loads(block[len("data: "):]))
    return events


def test_health(api):
    async def go(c):
        return await c.get("/health")
    r = _run(go)
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_languages(api):
    async def go(c):
        return await c.get("/languages")
    r = _run(go)
    body = r.json()
    assert r.status_code == 200
    assert body["supported_languages"]["ar"] == "Arabic"
    assert body["count"] == len(server.LANGUAGE_CODES)


def test_root_serves_the_page(api):
    api.chdir(ROOT)
    async def go(c):
        return await c.get("/")
    r = _run(go)
    assert r.status_code == 200
    assert "<html" in r.text.lower()


def test_start_rejects_non_pdf(api):
    async def go(c):
        return await _start(c, name="notes.txt")
    assert _run(go).status_code == 400


def test_start_rejects_same_language(api):
    async def go(c):
        return await _start(c, source="en", target="en")
    assert _run(go).status_code == 400


def test_start_rejects_unknown_language(api):
    async def go(c):
        return await _start(c, target="xx")
    assert _run(go).status_code == 400


def test_start_creates_a_pending_session(api, tmp_path):
    async def go(c):
        return await _start(c)
    r = _run(go)
    assert r.status_code == 200
    sid = r.json()["session_id"]
    uuid.UUID(sid)
    assert (tmp_path / sid).is_dir()
    assert server.translation_sessions[sid]["status"] == "pending"


def test_progress_unknown_session_is_404(api):
    async def go(c):
        return await c.get("/translate/progress/not-a-session")
    assert _run(go).status_code == 404


def test_progress_stream_full_flow(api):
    async def go(c):
        r = await _start(c)
        sid = r.json()["session_id"]
        return sid, await c.get(f"/translate/progress/{sid}")
    sid, p = _run(go)
    assert p.status_code == 200
    assert p.headers["content-type"].startswith("text/event-stream")
    events = _events(p.text)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "init"
    assert kinds[-1] == "finish"
    for kind in ("stage_summary", "progress_start", "progress_update", "progress_end"):
        assert kind in kinds, kind
    summary = next(e for e in events if e["type"] == "stage_summary")
    labels = {s["name"]: s["display_name"] for s in summary["stages"]}
    assert "Translating Content" in labels["Translate Paragraphs"]
    assert "Finding Paragraphs" in labels["Parse Paragraphs"]
    finish = events[-1]
    assert finish["has_mono"] is True
    assert finish["has_dual"] is True
    assert server.translation_sessions[sid]["status"] == "completed"


def test_download_mono_and_dual_after_finish(api):
    async def go(c):
        r = await _start(c)
        sid = r.json()["session_id"]
        await c.get(f"/translate/progress/{sid}")
        mono = await c.get(f"/translate/download/{sid}?type=mono")
        dual = await c.get(f"/translate/download/{sid}?type=dual")
        return mono, dual
    mono, dual = _run(go)
    assert mono.status_code == 200
    assert mono.content == b"%PDF-1.4 mono"
    assert dual.status_code == 200
    assert dual.content == b"%PDF-1.4 dual"
    assert mono.headers["content-type"] == "application/pdf"


def test_download_unknown_session_is_404(api):
    async def go(c):
        return await c.get("/translate/download/no-such-session")
    assert _run(go).status_code == 404


def test_error_event_marks_the_session_failed(api):
    api.setattr(server, "async_translate", _make_fake(fail_with="boom"))
    async def go(c):
        r = await _start(c)
        sid = r.json()["session_id"]
        return sid, await c.get(f"/translate/progress/{sid}")
    sid, p = _run(go)
    events = _events(p.text)
    assert events[-1]["type"] == "error"
    assert "boom" in events[-1]["error"]
    assert server.translation_sessions[sid]["status"] == "error"


def test_finish_without_any_output_file_is_reported_as_error(api):
    api.setattr(server, "async_translate", _make_fake(write_mono=False, write_dual=False))
    async def go(c):
        r = await _start(c)
        sid = r.json()["session_id"]
        return await c.get(f"/translate/progress/{sid}")
    p = _run(go)
    kinds = [e["type"] for e in _events(p.text)]
    assert "error" in kinds
    assert "finish" not in kinds
