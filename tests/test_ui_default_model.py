import re
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


def _html():
    return INDEX.read_text(encoding="utf-8", errors="replace")


def test_default_model_is_the_600m_card():
    active = re.findall(r'<div class="model-card active" data-model="([^"]+)"', _html())
    assert active == ["nllb"]


def test_hidden_default_matches_the_active_card():
    m = re.search(r'id="selectedModel" value="([^"]+)"', _html())
    assert m is not None and m.group(1) == "nllb"


def test_all_four_model_cards_still_exist():
    found = set(re.findall(r'data-model="([^"]+)"', _html()))
    assert {"nllb-3.3b", "nllb", "marianmt", "gpt-4o-mini"} <= found
