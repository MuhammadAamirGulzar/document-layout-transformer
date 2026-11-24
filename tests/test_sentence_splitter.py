"""M2a: abbreviation-aware sentence splitting never cuts mid-sentence or loses text."""
import re

import pytest

from babeldoc.format.pdf.document_il.utils.sentence_splitter import split_sentences

_LEGACY = re.compile(r"(?<=[.!?])\s+")


def _legacy(text):
    return [part.strip() for part in _LEGACY.split(text) if part.strip()]


def _norm(text):
    return re.sub(r"\s+", " ", text).strip()


def _is_coarsening(new, old):
    """Every new segment must be a run of consecutive legacy segments."""
    index = 0
    for segment in new:
        parts = []
        while index < len(old) and _norm(" ".join(parts)) != _norm(segment):
            parts.append(old[index])
            index += 1
        if _norm(" ".join(parts)) != _norm(segment):
            return False
    return index == len(old)


ONE_SEGMENT = [
    "As shown in Fig. 3, the method improves recall.",
    "Smith et al. reported similar gains.",
    "Smith et al. 2020 reported similar gains.",
    "Use a threshold, e.g. 0.5, for all runs.",
    "Some tools, i.e. parsers, are slow.",
    "See No. 5 and approx. 3.5 % growth.",
    "Dr. Smith met Mr. Jones at 5 p.m. yesterday.",
    "The U.S. economy grew faster than expected.",
    "Results by J. Smith and A. Lee were reported.",
    "Eq. (3) and Sec. 4.2 are used throughout.",
    "See Ref. [12] and pp. 12-15 for details.",
    "Wait... what happened here?",
    "Compare Fig. 2 with Fig. 4 and Tab. 1.",
    "The ratio was vs. the baseline in vol. 3 of the series.",
]

SPLITS = [
    ("The method works. The results improve.",
     ["The method works.", "The results improve."]),
    ("Is it true? Yes it is! Done.",
     ["Is it true?", "Yes it is!", "Done."]),
    ("The total was $1,250.75 in 2024. It rose 4.2% afterwards.",
     ["The total was $1,250.75 in 2024.", "It rose 4.2% afterwards."]),
    ("He left at 5 p.m. The meeting ended.",
     ["He left at 5 p.m.", "The meeting ended."]),
    ("See Fig. 3. The next sentence follows.",
     ["See Fig. 3.", "The next sentence follows."]),
    ("We tested approx. 30 samples. The rest were skipped.",
     ["We tested approx. 30 samples.", "The rest were skipped."]),
    ("The answer is no. Next we continue.",
     ["The answer is no.", "Next we continue."]),
    ("He said so... Then he left.",
     ["He said so...", "Then he left."]),
    ("Option I. Then we continue.",
     ["Option I.", "Then we continue."]),
    ("Brown et al. Further work followed.",
     ["Brown et al.", "Further work followed."]),
    ("First line.\nSecond line.",
     ["First line.", "Second line."]),
    ("Done.    Next one.",
     ["Done.", "Next one."]),
]

PLAIN = [text for text, _ in SPLITS[:3]] + ["Alpha beta. Gamma delta! Epsilon?"]


@pytest.mark.parametrize("text", ONE_SEGMENT)
def test_abbreviations_do_not_split_a_sentence(text):
    assert split_sentences(text) == [text]


@pytest.mark.parametrize("text,expected", SPLITS)
def test_real_sentence_boundaries_still_split(text, expected):
    assert split_sentences(text) == expected


@pytest.mark.parametrize("text", PLAIN)
def test_plain_prose_matches_legacy_behaviour(text):
    assert split_sentences(text) == _legacy(text)


CORPUS = ONE_SEGMENT + [text for text, _ in SPLITS] + PLAIN


@pytest.mark.parametrize("text", CORPUS)
def test_no_text_is_lost_or_invented(text):
    assert _norm(" ".join(split_sentences(text))) == _norm(text)


@pytest.mark.parametrize("text", CORPUS)
def test_cuts_are_a_subset_of_legacy_cuts(text):
    assert _is_coarsening(split_sentences(text), _legacy(text))


@pytest.mark.parametrize("text", CORPUS)
def test_segments_are_stable_when_split_again(text):
    for segment in split_sentences(text):
        assert split_sentences(segment) == [segment]


def test_empty_and_whitespace_inputs():
    assert split_sentences("") == []
    assert split_sentences("   \n ") == []


def test_trailing_whitespace_leaves_no_empty_segment():
    assert split_sentences("Hello world. ") == ["Hello world."]


def test_text_without_terminator_is_one_segment():
    assert split_sentences("no terminator here") == ["no terminator here"]


def test_long_unpunctuated_text_is_preserved_whole():
    text = " ".join(["word"] * 2000)
    assert split_sentences(text) == [text]


def test_citation_span_suppresses_a_cut_inside_it():
    text = "Alpha. Beta gamma."
    assert split_sentences(text, [(0, len(text))]) == [text]


def test_citation_span_elsewhere_does_not_suppress():
    assert split_sentences("Alpha. Beta gamma.", [(0, 5)]) == ["Alpha.", "Beta gamma."]