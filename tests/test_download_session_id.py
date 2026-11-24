# Download route: only ids this server makes are accepted (backlog B-03).
import asyncio
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402

PDF = b"%PDF-1.4\nfake\n%%EOF\n"
GOOD_UUID = "123e4567-e89b-12d3-a456-426614174000"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setattr(server, "TEMP_DIR", root)
    monkeypatch.setattr(server, "translation_sessions", {})
    return root


def plant(folder, name="doc.en-ar.mono.pdf"):
    out = Path(folder) / "output"
    out.mkdir(parents=True)
    (out / name).write_bytes(PDF)


def download(session_id, kind="mono"):
    url = "/translate/download/" + quote(session_id, safe="") + "?type=" + kind

    async def run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(url)

    return asyncio.run(run())


def test_uuid_folder_on_disk_is_served(sandbox):
    sid = str(uuid.uuid4())
    plant(sandbox / sid)
    reply = download(sid)
    assert reply.status_code == 200
    assert reply.content == PDF


def test_older_route_id_on_disk_is_served(sandbox):
    sid = "session_" + "ab12cd34ef56ab78"
    plant(sandbox / sid)
    reply = download(sid)
    assert reply.status_code == 200
    assert reply.content == PDF


def test_folder_with_any_other_name_is_not_served(sandbox):
    plant(sandbox / "notasession")
    reply = download("notasession")
    assert reply.status_code == 404


def test_path_tricks_are_refused(sandbox):
    secret = sandbox.parent / "secret"
    plant(secret)
    tricks = ["..\\secret", "../secret", str(secret), str(secret).replace("\\", "/")]
    for trick in tricks:
        reply = download(trick)
        assert reply.status_code == 404, trick
        assert reply.content != PDF, trick


def test_validator_accepts_the_ids_this_server_makes():
    assert server._valid_session_id(GOOD_UUID)
    assert server._valid_session_id("session_" + "0123456789abcdef")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "..",
        "../x",
        "..\\x",
        "a/b",
        "C:\\x",
        "session_",
        "session_xyz",
        "session_" + "g" * 16,
        GOOD_UUID + "x",
        GOOD_UUID.upper(),
    ],
)
def test_validator_rejects_everything_else(bad):
    assert not server._valid_session_id(bad)
