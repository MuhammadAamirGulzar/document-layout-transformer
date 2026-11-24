"""Abbreviation-aware sentence segmentation for translation input.

The legacy splitter cut after every ".", "!" or "?" followed by whitespace, so
"Fig. 3", "et al. reported" or "e.g. 0.5" were translated as separate
fragments.  This module only ever removes cut points from that legacy rule:
every cut it makes is also a legacy cut, and joining its segments with single
spaces reproduces the input apart from whitespace.

Known tradeoff: "Option A. Then ..." stays one segment because "A." is treated
as a personal initial.  A false merge is cheaper than cutting a name in half.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

SEGMENTATION_VERSION = "m2b-v1"

_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_BOUNDARY = re.compile(r"(?<=[;:\u2014\u2013,])\s+")
_PAIRED_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)\b[^>]*>")
_OPENERS = "([{\"'\u201c\u2018"
_NUMBER_START = re.compile(r"[(\[]?[A-Za-z]?\d")
_INITIAL = re.compile(r"[A-Z]\.")

# Never ends a sentence, whatever follows it.
_ALWAYS_ABBREVIATION = frozenset(
    {"mr", "mrs", "ms", "dr", "prof", "messrs", "vs", "cf", "viz", "e.g", "i.e"}
)
# Abbreviations that introduce a number, bracket or label, e.g. "Fig. 3".
_NUMBERED_ABBREVIATION = frozenset(
    {
        "fig", "figs", "eq", "eqs", "sec", "secs", "sect", "tab", "no", "nos",
        "vol", "vols", "pp", "p", "ch", "chap", "ref", "refs", "approx", "ca",
        "art", "para", "app", "ex",
    }
)


def _chunk_before(text: str, end: int) -> tuple[str, int]:
    """Return the whitespace-delimited chunk that ends at *end* and its start."""
    start = end
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    return text[start:end], start


def _skip_space_back(text: str, index: int) -> int:
    while index > 0 and text[index - 1].isspace():
        index -= 1
    return index


def _word(chunk: str) -> str:
    return chunk.lstrip(_OPENERS).rstrip(".").lower()


def _is_false_boundary(text: str, match: re.Match) -> bool:
    """Whether the whitespace run in *match* does not really end a sentence."""
    end = match.start()
    if text[end - 1] != ".":
        return False
    rest = text[match.end():]
    head = rest[:1]
    if head.islower():
        return True
    chunk, chunk_start = _chunk_before(text, end)
    word = _word(chunk)
    if word in _ALWAYS_ABBREVIATION:
        return True
    if word in _NUMBERED_ABBREVIATION and _NUMBER_START.match(rest):
        return True
    if word == "al" and head and (head.isdigit() or head in "(["):
        previous, _ = _chunk_before(text, _skip_space_back(text, chunk_start))
        if _word(previous) == "et":
            return True
    initial = chunk.lstrip(_OPENERS)
    if _INITIAL.fullmatch(initial) and initial[0] != "I" and head.isupper():
        return True
    return False


def split_sentences(
    text: str, citation_spans: Iterable[tuple[int, int]] = ()
) -> list[str]:
    """Split *text* into sentences without cutting abbreviations or citations.

    *citation_spans* are (start, end) character ranges; a cut whose whitespace
    starts strictly inside one of them is suppressed.
    """
    spans = list(citation_spans)
    segments: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        if any(left < match.start() < right for left, right in spans):
            continue
        if _is_false_boundary(text, match):
            continue
        segments.append(text[start:match.start()].strip())
        start = match.end()
    segments.append(text[start:].strip())
    return [segment for segment in segments if segment]


def _paired_tag_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) ranges for matched <tag>...</tag> pairs.

    Standalone formula tags such as <b1> without a matching </b1> are ignored so
    normal sentence splitting around formulas is not blocked.
    """
    open_stacks: dict[str, list[int]] = {}
    spans: list[tuple[int, int]] = []
    for match in _PAIRED_TAG.finditer(text):
        is_close, name = match.group(1), match.group(2)
        if not is_close:
            open_stacks.setdefault(name, []).append(match.start())
        elif open_stacks.get(name):
            spans.append((open_stacks[name].pop(), match.end()))
    return spans


def _inside_any(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(left < pos < right for left, right in spans)


def _split_regex_outside_spans(
    text: str, pattern: re.Pattern[str], spans: list[tuple[int, int]]
) -> list[str]:
    parts: list[str] = []
    start = 0
    for match in pattern.finditer(text):
        if _inside_any(match.start(), spans):
            continue
        piece = text[start:match.start()].strip()
        if piece:
            parts.append(piece)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _split_atomic_units(text: str, spans: list[tuple[int, int]]) -> list[str]:
    """Split on whitespace while keeping matched <tag>...</tag> spans atomic."""
    units: list[str] = []
    start = 0
    for match in re.finditer(r"\s+", text):
        if _inside_any(match.start(), spans):
            continue
        piece = text[start:match.start()].strip()
        if piece:
            units.append(piece)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        units.append(tail)
    return units


def _pack_units(
    units: list[str],
    count_tokens: Callable[[str], int],
    max_tokens: int,
) -> tuple[list[str], bool]:
    chunks: list[str] = []
    had_oversized = False
    index = 0
    total = len(units)
    while index < total:
        if count_tokens(units[index]) > max_tokens:
            chunks.append(units[index])
            had_oversized = True
            index += 1
            continue
        lo, hi = index + 1, total
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if count_tokens(" ".join(units[index:mid])) <= max_tokens:
                lo = mid
            else:
                hi = mid - 1
        while lo > index + 1 and count_tokens(" ".join(units[index:lo])) > max_tokens:
            lo -= 1
        chunks.append(" ".join(units[index:lo]))
        index = lo
    return chunks, had_oversized


def split_text_for_token_budget(
    text: str,
    count_tokens: Callable[[str], int],
    max_tokens: int,
    citation_spans: Iterable[tuple[int, int]] = (),
    allow_word_split: bool = True,
) -> tuple[list[str], bool]:
    """Split *text* so each segment stays within *max_tokens* when possible.

    Boundaries are tried in decreasing order of safety: sentences, clauses
    (semicolon, colon, dash, comma + space), and optionally whitespace outside
    paired rich-text tags.  Returns ``(segments, had_oversized_unit)``.
    """
    if not text or not text.strip():
        return ([text] if text else []), False
    if max_tokens <= 0 or count_tokens(text) <= max_tokens:
        return [text], False

    paired = _paired_tag_spans(text)
    protected = list(citation_spans) + paired
    had_oversized = False
    out: list[str] = []

    sentence_parts = split_sentences(text, citation_spans=protected)
    packed_sentences, _ = _pack_units(sentence_parts, count_tokens, max_tokens)

    for sentence in packed_sentences:
        if count_tokens(sentence) <= max_tokens:
            out.append(sentence)
            continue
        sent_paired = _paired_tag_spans(sentence)
        clauses = _split_regex_outside_spans(sentence, _CLAUSE_BOUNDARY, sent_paired)
        packed_clauses, clause_oversized = _pack_units(clauses, count_tokens, max_tokens)
        if not allow_word_split:
            had_oversized = had_oversized or clause_oversized
            out.extend(packed_clauses)
            continue
        for clause in packed_clauses:
            if count_tokens(clause) <= max_tokens:
                out.append(clause)
                continue
            clause_paired = _paired_tag_spans(clause)
            words = _split_atomic_units(clause, clause_paired)
            if len(words) <= 1 and clause_paired:
                # Paired span itself exceeds budget; fall back to plain whitespace
                # words if possible rather than silently truncating the tail.
                words = _split_atomic_units(clause, [])
            packed_words, unit_oversized = _pack_units(words, count_tokens, max_tokens)
            had_oversized = had_oversized or unit_oversized
            out.extend(packed_words)

    return (out if out else [text]), had_oversized
