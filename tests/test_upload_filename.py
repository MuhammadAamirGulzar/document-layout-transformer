"""Uploads must never choose their own path on disk."""
import asyncio
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402

PDF_BYTES = b"%PDF-1.4\n%fake\n"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    temp_dir = tmp_path / "sessions"
    temp_dir.mkdir()
    monkeypatch.setattr(server, "TEMP_DIR", temp_dir)
    monkeypatch.setattr(server, "translation_sessions", {})
    return temp_dir


def upload(filename):
    async def run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/translate/start",
                files={"file": (filename, PDF_BYTES, "application/pdf")},
                data={"source_lang": "en", "target_lang": "ar"},
            )

    return asyncio.run(run())


def session_of(response):
    assert response.status_code == 200, response.text
    sid = response.json()["session_id"]
    return sid, server.translation_sessions[sid]


@pytest.mark.parametrize("name", ["../evil.pdf", "../../evil.pdf", "sub/dir/evil.pdf"])
def test_hostile_name_stays_inside_session_folder(sandbox, name):
    sid, session = session_of(upload(name))
    saved = Path(session["input_path"]).resolve()
    assert saved.parent == (sandbox / sid).resolve()
    assert saved.exists()
    assert list(sandbox.rglob("evil.pdf")) == []
    assert not (sandbox.parent / "evil.pdf").exists()


def test_absolute_path_name_cannot_escape(sandbox, tmp_path):
    target = tmp_path / "abs_evil.pdf"
    sid, session = session_of(upload(target.as_posix()))
    assert not target.exists()
    assert Path(session["input_path"]).resolve().parent == (sandbox / sid).resolve()


def test_saved_under_fixed_name_and_display_name_kept(sandbox):
    sid, session = session_of(upload("report.pdf"))
    saved = Path(session["input_path"])
    assert saved.name == "input.pdf"
    assert saved.read_bytes() == PDF_BYTES
    assert session["filename"] == "report.pdf"


def test_display_name_has_no_folders(sandbox):
    sid, session = session_of(upload("../../evil.pdf"))
    assert "/" not in session["filename"]
    assert "\\" not in session["filename"]
    assert session["filename"].endswith(".pdf")
