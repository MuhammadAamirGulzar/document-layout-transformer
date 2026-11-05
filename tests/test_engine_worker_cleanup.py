"""Actual worker threads, with fake translation work and no model/PDF parsing."""
import asyncio
import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from babeldoc.format.pdf import high_level as engine


def test_external_cancel_signal_reaches_worker_and_waits_for_cleanup(monkeypatch):
    stop, cleanup, release = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(engine, "get_translation_stage", lambda config: [])
    def work(pm, config):
        try:
            assert pm.cancel_event is stop
            assert stop.wait(3)
        finally:
            pm.on_finish()
            cleanup.set()
            release.wait(3)
    monkeypatch.setattr(engine, "do_translate", work)
    async def go():
        config = types.SimpleNamespace(report_interval=0, _job_cancel_event=stop)
        async def consume():
            async for _ in engine.async_translate(config):
                pass
        task = asyncio.create_task(consume())
        await asyncio.sleep(0.03)
        stop.set()
        try:
            for _ in range(600):
                if cleanup.is_set():
                    break
                await asyncio.sleep(0.005)
            assert cleanup.is_set()
            await asyncio.sleep(0.03)
            assert not task.done(), "engine must wait for worker cleanup after on_finish"
        finally:
            release.set()
            await asyncio.wait_for(task, 3)
    asyncio.run(go())


def test_closing_engine_stream_waits_for_worker_cleanup(monkeypatch):
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(engine, "get_translation_stage", lambda config: [])
    def work(pm, config):
        started.set()
        try:
            pm.cancel_event.wait(3)
        finally:
            pm.on_finish()
            release.wait(3)
    monkeypatch.setattr(engine, "do_translate", work)
    async def go():
        stream = engine.async_translate(types.SimpleNamespace(report_interval=0))
        await stream.__anext__()
        close = asyncio.create_task(stream.aclose())
        try:
            for _ in range(600):
                if started.is_set():
                    break
                await asyncio.sleep(0.005)
            assert started.is_set()
            await asyncio.sleep(0.03)
            assert not close.done(), "closing an observer is not proof the thread stopped"
        finally:
            release.set()
            await asyncio.wait_for(close, 3)
    asyncio.run(go())
