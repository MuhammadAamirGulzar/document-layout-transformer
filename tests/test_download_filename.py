# Download names must be encoded into the header, not pasted in (backlog B-03).
import asyncio
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402

PDF = b"%PDF-1.4\nfake\n%%EOF\n"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setattr(server, "TEMP_DIR", root)
    monkeypatch.setattr(server, "translation_sessions", {})
    return tmp_path


def download_with_name(tmp_path, display_name, kind="mono"):
    pdf = tmp_path / "out.pdf"
    pdf.write_bytes(PDF)
    sid = str(uuid.uuid4())
    server.translation_sessions[sid] = {
        "status": "completed",
        "filename": display_name,
        "mono_output_path": str(pdf),
        "dual_output_path": str(pdf),
    }

    async def run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/translate/download/" + sid + "?type=" + kind)

    return asyncio.run(run())


def test_plain_name_download_works(sandbox):
    reply = download_with_name(sandbox, "report.pdf")
    assert reply.status_code == 200
    assert reply.content == PDF
    assert reply.headers["content-type"] == "application/pdf"
    assert "report_translated_mono.pdf" in reply.headers["content-disposition"]


def test_dual_download_uses_the_dual_name(sandbox):
    reply = download_with_name(sandbox, "report.pdf", "dual")
    assert reply.status_code == 200
    assert "report_dual_bilingual.pdf" in reply.headers["content-disposition"]


def test_spaces_are_encoded(sandbox):
    reply = download_with_name(sandbox, "my report.pdf")
    assert reply.status_code == 200
    assert "my report" not in reply.headers["content-disposition"]


def test_quote_mark_cannot_break_the_header(sandbox):
    reply = download_with_name(sandbox, 'a"b.pdf')
    assert reply.status_code == 200
    assert 'a"b' not in reply.headers["content-disposition"]


def test_arabic_name_downloads(sandbox):
    reply = download_with_name(sandbox, "\u062a\u0642\u0631\u064a\u0631.pdf")
    assert reply.status_code == 200
    assert "filename*=" in reply.headers["content-disposition"]
