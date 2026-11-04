"""Stage label tests for server.get_display_stage_name (backlog B-01)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402
from babeldoc.format.pdf.high_level import TRANSLATE_STAGES  # noqa: E402

STAGE_NAMES = [name for name, _weight in TRANSLATE_STAGES]


@pytest.mark.parametrize("name", STAGE_NAMES)
def test_every_engine_stage_has_an_exact_label(name):
    assert name in server.STAGE_DISPLAY_NAMES, f"no label key for engine stage {name!r}"


def test_labels_are_unique_across_engine_stages():
    labels = [server.get_display_stage_name(n) for n in STAGE_NAMES]
    assert len(set(labels)) == len(labels)


def test_translate_stage_is_not_labelled_as_paragraph_finding():
    translate = server.get_display_stage_name("Translate Paragraphs")
    finding = server.get_display_stage_name("Parse Paragraphs")
    assert translate != finding
    assert "Translat" in translate
    assert "Paragraph" in finding


def test_longest_matching_key_wins_in_fallback():
    assert "Translat" in server.get_display_stage_name("Translate Paragraphs (part 2)")


def test_unknown_stage_keeps_its_raw_name():
    assert "Something New" in server.get_display_stage_name("Something New")
