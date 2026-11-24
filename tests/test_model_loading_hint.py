"""Static checks for the model-loading hint on the page (page-only)."""
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "frontend/index.html"


def _text():
    return PAGE.read_text(encoding="utf-8")


def _slice(text, start_marker, end_marker):
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def _render_body():
    return _slice(_text(), "function renderEvent(data) {", "async function poll()")


def _hint_body():
    return _slice(_text(), "function updateModelLoadingHint(data) {", "function updateOverallProgress")


def test_page_defines_model_loading_hint_function():
    assert "function updateModelLoadingHint(data) {" in _text()


def test_hint_text_says_it_is_not_stuck():
    text = _text()
    assert "Loading the translation model." in text
    assert "This is not stuck." in text


def test_render_event_calls_hint_before_the_switch():
    body = _render_body()
    assert "updateModelLoadingHint(data);" in body
    assert body.index("updateModelLoadingHint(data);") < body.index("switch (data.type)")


def test_hint_only_for_translate_stage_at_zero():
    body = _hint_body()
    assert "Translate Paragraphs" in body
    assert "progress_start" in body
    assert "progress_update" in body
    assert "stage_current" in body
    assert "current === 0" in body


def test_hint_is_hidden_when_not_waiting():
    body = _hint_body()
    assert "'block'" in body
    assert "'none'" in body
