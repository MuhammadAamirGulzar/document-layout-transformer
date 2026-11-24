# The root JSON fallback must not advertise a nonexistent endpoint.
import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def get_root():
    async def run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/")
    return asyncio.run(run())


def test_json_does_not_advertise_a_fake_stream_route(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reply = get_root()
    assert reply.status_code == 200
    endpoints = reply.json()["endpoints"]
    assert "translate_stream" not in endpoints
    assert "/translate/stream" not in endpoints.values()


def test_json_keeps_the_real_endpoint_listings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reply = get_root()
    assert reply.status_code == 200
    body = reply.json()
    assert body["status"] == "running"
    for name, path in {
        "health": "/health",
        "languages": "/languages",
        "translate": "/translate",
    }.items():
        assert body["endpoints"][name] == path
        assert path in {route.path for route in server.app.routes}


def test_root_still_serves_the_frontend(monkeypatch):
    monkeypatch.chdir(ROOT)
    reply = get_root()
    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/html")
    assert "<html" in reply.text.lower()
