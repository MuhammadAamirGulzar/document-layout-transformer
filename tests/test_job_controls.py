"""Cooperative cancellation, timeout and bounded cleanup; no model loading."""
import asyncio
import sys
import time
import types
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(server, "translation_sessions", {})
    monkeypatch.setattr(server, "_job_slots", asyncio.Semaphore(1))
    monkeypatch.setenv("JOB_TIMEOUT_SECONDS", "3600")
    monkeypatch.setenv("SESSION_TTL_SECONDS", "60")
    monkeypatch.setattr(server, "TranslationConfig", lambda **kw: types.SimpleNamespace(**kw))
    for name in ("NLLBTranslator", "MarianMTTranslator", "OpenAITranslator"):
        monkeypatch.setattr(server, name, lambda *a, **kw: None)
    async def fake(config):
        yield {"type": "stage_summary", "stages": []}
        signal = getattr(config, "_job_cancel_event", None)
        while signal is None or not signal.is_set():
            await asyncio.sleep(0.005)
        yield {"type": "error", "error": asyncio.CancelledError}
    monkeypatch.setattr(server, "async_translate", fake)
    def add(status="pending", expired=False):
        sid = str(uuid.uuid4())
        folder = tmp_path / sid
        out = folder / "output"
        out.mkdir(parents=True)
        server.translation_sessions[sid] = {
            "status": status, "input_path": str(folder / "input.pdf"),
            "output_directory": str(out), "source_lang": "en", "target_lang": "ar",
            "model": "nllb", "filename": "doc.pdf", "progress": 0, "error": None,
            "_created_at": time.time() - (120 if expired else 0),
        }
        if status in {"completed", "error", "cancelled", "timed_out"}:
            server.translation_sessions[sid]["_finished_at"] = time.time() - (120 if expired else 0)
        return sid
    return add


def scenario(fn):
    async def go():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            try:
                async with asyncio.timeout(5):
                    await fn(c)
            finally:
                tasks = []
                for s in server.translation_sessions.values():
                    task = s.get("_task")
                    if task is not None and not task.done():
                        signal = s.get("_cancel_signal")
                        if signal is not None:
                            signal.set()
                        task.cancel()
                        tasks.append(task)
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(go())


async def wait_processing(sid):
    for _ in range(600):
        if server.translation_sessions[sid]["status"] == "processing":
            return
        await asyncio.sleep(0.005)
    raise AssertionError("job did not start")


async def wait_done(sid):
    await asyncio.wait_for(asyncio.shield(server.translation_sessions[sid]["_task"]), 3)


def test_cancel_pending_job_without_loading_model(jobs):
    sid = jobs()
    async def go(c):
        r = await c.post(f"/translate/cancel/{sid}")
        assert r.status_code == 200
        assert server.translation_sessions[sid]["status"] == "cancelled"
        assert "_task" not in server.translation_sessions[sid]
    scenario(go)


def test_cancel_running_job_requests_stop_then_becomes_cancelled(jobs):
    sid = jobs()
    async def go(c):
        await c.post(f"/translate/run/{sid}")
        await wait_processing(sid)
        r = await c.post(f"/translate/cancel/{sid}")
        assert r.status_code == 200
        await wait_done(sid)
        s = (await c.get(f"/translate/status/{sid}")).json()
        assert s["status"] == "cancelled"
        assert s["events"][-1]["type"] == "cancelled"
    scenario(go)


def test_cancelling_waits_for_cleanup_and_keeps_slot(jobs, monkeypatch):
    a, b = jobs(), jobs()
    entered, allow_cleanup = None, None
    starts = []
    async def fake(config):
        starts.append(config.output_dir)
        entered.set()
        signal = config._job_cancel_event
        while not signal.is_set():
            await asyncio.sleep(0.005)
        await allow_cleanup.wait()
        yield {"type": "error", "error": asyncio.CancelledError}
    monkeypatch.setattr(server, "async_translate", fake)
    async def go(c):
        nonlocal entered, allow_cleanup
        entered, allow_cleanup = asyncio.Event(), asyncio.Event()
        await c.post(f"/translate/run/{a}")
        await asyncio.wait_for(entered.wait(), 3)
        await c.post(f"/translate/run/{b}")
        assert (await c.post(f"/translate/cancel/{a}")).status_code == 200
        await asyncio.sleep(0.03)
        assert server.translation_sessions[a]["status"] == "cancelling"
        assert not server.translation_sessions[a]["_task"].done()
        assert len(starts) == 1
        assert server.translation_sessions[b]["status"] == "queued"
        await c.post(f"/translate/cancel/{b}")
        await wait_done(b)
        assert server.translation_sessions[b]["status"] == "cancelled"
        allow_cleanup.set()
        await wait_done(a)
    scenario(go)


def test_timeout_uses_cooperative_stop(jobs, monkeypatch):
    sid = jobs()
    monkeypatch.setenv("JOB_TIMEOUT_SECONDS", "0.08")
    async def go(c):
        await c.post(f"/translate/run/{sid}")
        await wait_done(sid)
        s = (await c.get(f"/translate/status/{sid}")).json()
        assert s["status"] == "timed_out"
        assert s["events"][-1]["type"] == "timed_out"
    scenario(go)


def test_timeout_allows_jobs_that_keep_reporting_progress(jobs, monkeypatch):
    monkeypatch.setenv("JOB_TIMEOUT_SECONDS", "0.08")

    async def go():
        session = {"status": "processing"}
        server._job_event(session, {"type": "progress_update"})
        watcher = asyncio.create_task(server._watch_job_deadline(session))
        for current in range(3):
            await asyncio.sleep(0.05)
            server._job_event(
                session,
                {"type": "progress_update", "stage_current": current + 1},
            )
        assert session["status"] == "processing"
        session["status"] = "completed"
        await asyncio.wait_for(watcher, 0.2)

    asyncio.run(go())


def test_repeated_cancel_is_idempotent(jobs):
    sid = jobs()
    async def go(c):
        for _ in range(2):
            assert (await c.post(f"/translate/cancel/{sid}")).status_code == 200
        assert server.translation_sessions[sid]["status"] == "cancelled"
    scenario(go)


def test_completed_job_cannot_be_cancelled(jobs):
    sid = jobs("completed")
    async def go(c):
        assert (await c.post(f"/translate/cancel/{sid}")).status_code == 409
        assert server.translation_sessions[sid]["status"] == "completed"
    scenario(go)


def test_cancel_rejects_unknown_and_invalid_ids(jobs):
    async def go(c):
        for sid in ("bad", str(uuid.uuid4())):
            assert (await c.post(f"/translate/cancel/{sid}")).status_code == 404
    scenario(go)


def test_no_partial_download_after_cancellation(jobs):
    sid = jobs("cancelled")
    out = Path(server.translation_sessions[sid]["output_directory"])
    (out / "x.mono.pdf").write_bytes(b"%PDF partial")
    async def go(c):
        assert (await c.get(f"/translate/download/{sid}")).status_code == 409
    scenario(go)


def test_expired_terminal_session_is_removed(jobs):
    sid = jobs("completed", expired=True)
    folder = server.TEMP_DIR / sid
    assert sid in server._purge_expired_sessions()
    assert sid not in server.translation_sessions
    assert not folder.exists()


def test_unexpired_session_is_preserved(jobs):
    sid = jobs("completed")
    assert server._purge_expired_sessions() == []
    assert (server.TEMP_DIR / sid).exists()


def test_active_task_is_never_purged_even_if_status_is_error(jobs):
    sid = jobs("error", expired=True)
    async def go(c):
        task = asyncio.create_task(asyncio.Event().wait())
        server.translation_sessions[sid]["_task"] = task
        assert server._purge_expired_sessions() == []
        assert (server.TEMP_DIR / sid).exists()
    scenario(go)


def test_expired_abandoned_upload_is_removed(jobs):
    sid = jobs(expired=True)
    assert sid in server._purge_expired_sessions()
    assert not (server.TEMP_DIR / sid).exists()


def test_unknown_folder_is_not_purged(jobs):
    folder = server.TEMP_DIR / str(uuid.uuid4())
    folder.mkdir()
    assert server._purge_expired_sessions() == []
    assert folder.exists()


def test_cleanup_refuses_path_traversal_registry_entry(jobs):
    jobs()
    outside = server.TEMP_DIR.parent / ("outside-" + uuid.uuid4().hex)
    outside.mkdir()
    try:
        server.translation_sessions["../" + outside.name] = {
            "status": "completed", "_finished_at": time.time() - 120}
        assert server._purge_expired_sessions() == []
        assert outside.exists()
    finally:
        outside.rmdir()


@pytest.mark.parametrize("value", ["abc", "nan", "inf", "0", "-1"])
def test_invalid_time_settings_fall_back(jobs, monkeypatch, value):
    monkeypatch.setenv("JOB_TIMEOUT_SECONDS", value)
    assert server._setting_seconds("JOB_TIMEOUT_SECONDS", 3600) == 3600


def test_cleanup_leaves_sessions_without_timestamps_alone(jobs):
    sid = jobs("completed", expired=True)
    server.translation_sessions[sid].pop("_finished_at")
    assert server._purge_expired_sessions() == []


def test_sse_cancellation_has_a_terminal_event(jobs):
    sid = jobs()
    async def go(c):
        cancelled = await c.post(f"/translate/cancel/{sid}")
        assert cancelled.status_code == 200
        r = await c.get(f"/translate/progress/{sid}")
        assert r.status_code == 200
        assert '"type": "cancelled"' in r.text
    scenario(go)
