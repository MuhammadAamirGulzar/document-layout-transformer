"""Static cancellation UI contract; real browser cancellation still needs review."""
from pathlib import Path
PAGE = Path(__file__).resolve().parents[1] / "frontend/index.html"


def test_cancel_button_posts_to_server():
    text = PAGE.read_text(encoding="utf-8")
    assert "/translate/cancel/${currentSessionId}" in text
    assert "cancelBtn.addEventListener('click', async" in text


def test_cancel_button_is_enabled_after_job_start():
    text = PAGE.read_text(encoding="utf-8")
    start = text.index("async function startJobAndPoll")
    assert "cancelBtn.disabled = false;" in text[start:]
    assert "Cancellation will be enabled separately" not in text


def test_ui_handles_cancel_timeout_and_stopping():
    text = PAGE.read_text(encoding="utf-8")
    for name in ("cancelled", "timed_out", "stopping"):
        assert "case '" + name + "':" in text
