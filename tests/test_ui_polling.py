"""Static UI contract checks; browser verification is still required."""
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "frontend/index.html"


def test_ui_uses_status_polling_instead_of_eventsource():
    text = PAGE.read_text(encoding="utf-8")
    assert "/translate/status/${sessionId}?after=${cursor}" in text
    assert "new EventSource(" not in text


def test_ui_explicitly_runs_the_uploaded_job():
    text = PAGE.read_text(encoding="utf-8")
    assert "/translate/run/${sessionId}" in text
    assert "startJobAndPoll(session_id)" in text


def test_ui_has_one_poll_in_flight_and_retries_without_restarting():
    text = PAGE.read_text(encoding="utf-8")
    assert "await fetch(`${API_BASE_URL}/translate/status/" in text
    assert "if (pollStopped) return;" in text
    assert "setTimeout(poll, 1000)" in text
