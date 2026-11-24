"""Background/polling contract; every engine is replaced with a fake."""
import asyncio
import json
import sys
import types
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "translation_sessions", {})
    monkeypatch.setattr(server, "_job_slots", asyncio.Semaphore(1), raising=False)
    monkeypatch.setattr(server, "TranslationConfig", lambda **kw: types.SimpleNamespace(**kw))
    for name in ("NLLBTranslator", "MarianMTTranslator", "OpenAITranslator"):
        monkeypatch.setattr(server, name, lambda *a, **kw: None)

    async def fake(config):
        yield {"type": "stage_summary", "stages": [
            {"name": "Translate Paragraphs", "percent": 1.0}]}
        yield {"type": "progress_update", "stage": "Translate Paragraphs",
               "stage_current": 1, "stage_total": 2, "stage_progress": 50,
               "overall_progress": 50}
        (Path(config.output_dir) / "x.mono.pdf").write_bytes(b"%PDF-1.4 mono")
        yield {"type": "finish", "translate_result": None}

    monkeypatch.setattr(server, "async_translate", fake)

    def add():
        sid = str(uuid.uuid4())
        out = tmp_path / sid
        out.mkdir()
        server.translation_sessions[sid] = {
            "status": "pending", "input_path": str(out / "input.pdf"),
            "output_directory": str(out), "source_lang": "en", "target_lang": "ar",
            "model": "nllb", "filename": "doc.pdf", "progress": 0, "error": None,
        }
        return sid
    return add


def run(coro):
    async def scenario():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            try:
                await coro(c)
            finally:
                tasks = [s["_task"] for s in server.translation_sessions.values()
                         if s.get("_task") is not None and not s["_task"].done()]
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(scenario())


async def done(sid):
    task = server.translation_sessions[sid].get("_task")
    assert task is not None, "job must have a retained background task"
    await asyncio.wait_for(asyncio.shield(task), 3)


def test_status_does_not_start_a_pending_job(jobs):
    sid = jobs()
    async def go(c):
        r = await c.get(f"/translate/status/{sid}")
        assert r.status_code == 200
        assert r.json()["status"] == "pending"
        assert "_task" not in server.translation_sessions[sid]
    run(go)


def test_explicit_run_finishes_without_progress_connection(jobs):
    sid = jobs()
    async def go(c):
        assert (await c.post(f"/translate/run/{sid}")).status_code == 200
        await done(sid)
        r = await c.get(f"/translate/status/{sid}")
        assert r.json()["status"] == "completed"
        assert r.json()["has_mono"] is True
        assert r.json()["events"][-1]["type"] == "finish"
        assert "_task" not in r.json() and "input_path" not in r.json()
    run(go)


def test_duplicate_run_returns_409_and_keeps_one_task(jobs):
    sid = jobs()
    async def go(c):
        assert (await c.post(f"/translate/run/{sid}")).status_code == 200
        task = server.translation_sessions[sid]["_task"]
        assert (await c.post(f"/translate/run/{sid}")).status_code == 409
        assert server.translation_sessions[sid]["_task"] is task
        await done(sid)
    run(go)


def test_second_sse_connection_does_not_rerun_engine(jobs, monkeypatch):
    sid = jobs()
    original = server.async_translate
    calls = []
    async def fake(config):
        calls.append(1)
        async for event in original(config):
            yield event
    monkeypatch.setattr(server, "async_translate", fake)
    async def go(c):
        first, second = await asyncio.gather(
            c.get(f"/translate/progress/{sid}"), c.get(f"/translate/progress/{sid}"))
        assert first.status_code == second.status_code == 200
        assert '"type": "finish"' in first.text and '"type": "finish"' in second.text
        assert calls == [1]
    run(go)


def test_closed_observer_does_not_cancel_worker(jobs, monkeypatch):
    sid = jobs()
    started, release = None, None
    original = server.async_translate
    async def fake(config):
        started.set()
        await release.wait()
        async for event in original(config):
            yield event
    monkeypatch.setattr(server, "async_translate", fake)
    async def go(c):
        nonlocal started, release
        started, release = asyncio.Event(), asyncio.Event()
        response = await server.get_translation_progress(sid)
        observer = response.body_iterator
        assert await observer.__anext__()
        await asyncio.wait_for(started.wait(), 3)
        await observer.aclose()
        task = server.translation_sessions[sid]["_task"]
        assert not task.done()
        release.set()
        await done(sid)
        assert server.translation_sessions[sid]["status"] == "completed"
    run(go)


def test_jobs_use_one_engine_slot(jobs, monkeypatch):
    a, b = jobs(), jobs()
    entered = []
    original = server.async_translate
    release = None
    first_entered = None
    async def fake(config):
        entered.append(config.output_dir)
        if len(entered) == 1:
            first_entered.set()
            await release.wait()
        async for event in original(config):
            yield event
    monkeypatch.setattr(server, "async_translate", fake)
    async def go(c):
        nonlocal release, first_entered
        release, first_entered = asyncio.Event(), asyncio.Event()
        await c.post(f"/translate/run/{a}")
        await c.post(f"/translate/run/{b}")
        await asyncio.wait_for(first_entered.wait(), 3)
        assert len(entered) == 1
        assert server.translation_sessions[b]["status"] == "queued"
        release.set()
        await done(a)
        await done(b)
        assert len(entered) == 2
    run(go)


def test_finish_without_output_is_terminal_error(jobs, monkeypatch):
    sid = jobs()
    async def fake(config):
        yield {"type": "finish", "translate_result": None}
    monkeypatch.setattr(server, "async_translate", fake)
    async def go(c):
        await c.get(f"/translate/progress/{sid}")
        assert server.translation_sessions[sid]["status"] == "error"
        assert server.translation_sessions[sid]["error"]
    run(go)


def test_stream_without_finish_is_terminal_error(jobs, monkeypatch):
    sid = jobs()
    async def fake(config):
        yield {"type": "progress_update", "stage": "Translate Paragraphs"}
    monkeypatch.setattr(server, "async_translate", fake)
    async def go(c):
        await c.post(f"/translate/run/{sid}")
        await done(sid)
        assert server.translation_sessions[sid]["status"] == "error"
        assert "finish" in server.translation_sessions[sid]["error"]
    run(go)


def test_raised_engine_exception_is_terminal_error(jobs, monkeypatch):
    sid = jobs()
    async def fake(config):
        raise RuntimeError("fake engine failed")
        yield
    monkeypatch.setattr(server, "async_translate", fake)
    async def go(c):
        await c.post(f"/translate/run/{sid}")
        await done(sid)
        r = (await c.get(f"/translate/status/{sid}")).json()
        assert r["status"] == "error" and "fake engine failed" in r["error"]
    run(go)


def test_engine_error_event_is_terminal_error(jobs, monkeypatch):
    sid = jobs()
    async def fake(config):
        yield {"type": "error", "error": "fake error event"}
    monkeypatch.setattr(server, "async_translate", fake)
    async def go(c):
        await c.post(f"/translate/run/{sid}")
        await done(sid)
        assert server.translation_sessions[sid]["status"] == "error"
        assert server.translation_sessions[sid]["error"] == "fake error event"
    run(go)


def test_polling_cursor_does_not_repeat_events(jobs):
    sid = jobs()
    async def go(c):
        await c.post(f"/translate/run/{sid}")
        await done(sid)
        first = (await c.get(f"/translate/status/{sid}")).json()
        assert first["events"]
        again = (await c.get(f"/translate/status/{sid}?after={first['next_event_id']}")).json()
        assert again["events"] == []
        assert again["status"] == "completed"
    run(go)


def test_polling_keeps_stage_labels(jobs):
    sid = jobs()
    async def go(c):
        await c.post(f"/translate/run/{sid}")
        await done(sid)
        body = (await c.get(f"/translate/status/{sid}")).json()
        summary = next(e for e in body["events"] if e["type"] == "stage_summary")
        assert "Translating Content" in summary["stages"][0]["display_name"]
    run(go)


def test_worker_cancellation_is_terminal_error(jobs, monkeypatch):
    sid = jobs()
    entered = None
    async def fake(config):
        entered.set()
        await asyncio.Event().wait()
        yield
    monkeypatch.setattr(server, "async_translate", fake)
    async def go(c):
        nonlocal entered
        entered = asyncio.Event()
        await c.post(f"/translate/run/{sid}")
        await asyncio.wait_for(entered.wait(), 3)
        task = server.translation_sessions[sid]["_task"]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert server.translation_sessions[sid]["status"] == "error"
    run(go)


def test_completed_job_cannot_be_started_again(jobs):
    sid = jobs()
    async def go(c):
        await c.post(f"/translate/run/{sid}")
        await done(sid)
        assert (await c.post(f"/translate/run/{sid}")).status_code == 409
    run(go)


def test_engine_completion_callback_does_not_turn_success_into_error(jobs, monkeypatch):
    sid = jobs()
    original = server.async_translate
    async def fake(config):
        async for event in original(config):
            yield event
        yield {"type": "error", "error": asyncio.CancelledError}
    monkeypatch.setattr(server, "async_translate", fake)
    async def go(c):
        await c.post(f"/translate/run/{sid}")
        await done(sid)
        assert server.translation_sessions[sid]["status"] == "completed"
    run(go)


def test_real_error_after_finish_still_prevents_success(jobs, monkeypatch):
    sid = jobs()
    original = server.async_translate
    async def fake(config):
        async for event in original(config):
            yield event
        yield {"type": "error", "error": "cleanup failed"}
    monkeypatch.setattr(server, "async_translate", fake)
    async def go(c):
        await c.post(f"/translate/run/{sid}")
        await done(sid)
        assert server.translation_sessions[sid]["status"] == "error"
        assert server.translation_sessions[sid]["error"] == "cleanup failed"
    run(go)


def test_invalid_and_unknown_status_ids_are_404(jobs):
    async def go(c):
        for sid in ("not-an-id", str(uuid.uuid4())):
            assert (await c.get(f"/translate/status/{sid}")).status_code == 404
    run(go)


def test_invalid_and_unknown_run_ids_are_404(jobs):
    async def go(c):
        for sid in ("not-an-id", str(uuid.uuid4())):
            assert (await c.post(f"/translate/run/{sid}")).status_code == 404
    run(go)


def test_negative_cursor_is_rejected(jobs):
    sid = jobs()
    async def go(c):
        assert (await c.get(f"/translate/status/{sid}?after=-1")).status_code == 400
    run(go)
