# Other websites must not be able to call this server from a browser (backlog B-03).
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402


def call(method, path, headers=None):
    async def run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, headers=headers or {})

    return asyncio.run(run())


def test_page_on_its_own_origin_still_works():
    reply = call("GET", "/health", {"Origin": "http://127.0.0.1:7860"})
    assert reply.status_code == 200


def test_other_sites_get_no_cors_header():
    reply = call("GET", "/health", {"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in reply.headers


def test_preflight_from_another_site_is_not_approved():
    reply = call(
        "OPTIONS",
        "/translate/start",
        {
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in reply.headers
