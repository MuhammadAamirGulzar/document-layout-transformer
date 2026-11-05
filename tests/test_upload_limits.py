"""Uploads must have a size limit and must really be PDFs."""
import asyncio
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402

MB = 1024 * 1024
PDF_HEAD = b"%PDF-1.4\n"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    temp_dir = tmp_path / "sessions"
    temp_dir.mkdir()
    monkeypatch.setattr(server, "TEMP_DIR", temp_dir)
    monkeypatch.setattr(server, "translation_sessions", {})
    return temp_dir


def upload(content, filename="doc.pdf"):
    async def run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/translate/start",
                files={"file": (filename, content, "application/pdf")},
                data={"source_lang": "en", "target_lang": "ar"},
            )

    return asyncio.run(run())


# ---- accepted ----

def test_normal_pdf_is_accepted(sandbox):
    body = PDF_HEAD + b"hello"
    response = upload(body)
    assert response.status_code == 200, response.text
    session = server.translation_sessions[response.json()["session_id"]]
    assert Path(session["input_path"]).read_bytes() == body


def test_pdf_just_under_the_limit_is_accepted(sandbox, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    body = PDF_HEAD + b"0" * (1 * MB - 100)
    response = upload(body)
    assert response.status_code == 200, response.text


def test_small_junk_before_the_header_is_accepted(sandbox):
    body = b"\n\n  junk\n" + PDF_HEAD + b"rest"
    response = upload(body)
    assert response.status_code == 200, response.text


# ---- rejected ----

def test_file_over_the_limit_gets_413_and_leaves_nothing(sandbox, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    body = PDF_HEAD + b"0" * (1 * MB + 10)
    response = upload(body)
    assert response.status_code == 413, response.text
    assert list(sandbox.iterdir()) == []
    assert server.translation_sessions == {}


def test_file_that_is_not_a_pdf_gets_400_and_leaves_nothing(sandbox):
    response = upload(b"MZ\x90\x00 this is not a pdf at all", filename="evil.pdf")
    assert response.status_code == 400, response.text
    assert list(sandbox.iterdir()) == []
    assert server.translation_sessions == {}


def test_empty_file_gets_400_and_leaves_nothing(sandbox):
    response = upload(b"")
    assert response.status_code == 400, response.text
    assert list(sandbox.iterdir()) == []


# ---- the limit setting ----

def test_default_limit_is_50_mb(monkeypatch):
    monkeypatch.delenv("MAX_UPLOAD_MB", raising=False)
    assert server._max_upload_bytes() == 50 * MB


def test_bad_limit_setting_falls_back_to_50_mb(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "abc")
    assert server._max_upload_bytes() == 50 * MB
