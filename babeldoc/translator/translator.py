import contextlib
import logging
import os
import re
import threading
import time
import unicodedata
from collections import Counter
from abc import ABC
from abc import abstractmethod

import httpx
import openai
from tenacity import before_sleep_log
from tenacity import retry
from tenacity import retry_if_exception_type
from tenacity import stop_after_attempt
from tenacity import wait_exponential

from babeldoc.babeldoc_exception.BabelDOCException import ContentFilterError
from babeldoc.format.pdf.document_il.utils.sentence_splitter import (
    SEGMENTATION_VERSION,
    split_sentences,
    split_text_for_token_budget,
)
from babeldoc.format.pdf.document_il.utils.web_references import (
    iter_web_references,
    restore_web_references,
    stash_web_references,
)
from babeldoc.translator.cache import TranslationCache
from babeldoc.utils.atomic_integer import AtomicInteger

logger = logging.getLogger(__name__)


def remove_control_characters(s):
    return "".join(ch for ch in s if unicodedata.category(ch)[0] != "C")


# Unified numeric bypass pattern.
# ANY token that contains a Western digit (0-9) is protected before translation
# and restored byte-for-byte afterwards. This prevents the model from:
#   - Converting digits to Eastern Arabic-Indic (٧٥ instead of 75)
#   - Spelling numbers out in Arabic ("seventy-five")
#   - Swapping decimal/thousand separators
#   - Corrupting percentages, ratios, years, codes, equation labels
#
# Unified numeric and code protection pattern.
# Tokens matching these patterns are protected before translation into {0}, {1} placeholders
# and restored byte-for-byte afterwards. This prevents translation models from:
#   - Converting digits to Eastern Arabic-Indic (٧٥ instead of 75)
#   - Corrupting or tearing arithmetic division/equations across linebreaks (281211210/97 = 62)
#   - Translating SWIFT/BIC codes or financial codes phonetically (ABQQQAQAXXX)
#   - Inverting modulo algorithms (MOD 97-10) or dropping phone numbers (+974 44456371)
#
from babeldoc.format.pdf.document_il.utils.structured_tokens import (
    ATOMIC_EXPRESSION_RE as _ATOMIC_EXPRESSION,
    AUTHOR_YEAR_CITATION,
    CHEM_ION_RE as _CHEM_ION,
    CHEM_REACTION_RE as _CHEM_REACTION,
    IBAN_PATTERN as _IBAN_PATTERN,
    ISO_COUNTRY_CODES as _ISO_COUNTRY_CODES,
    KNOWN_CODES as _KNOWN_CODES,
    NUMERIC_VALUE_RE as _SHARED_NUMERIC_VALUE_RE,
    PASSTHROUGH_ATOM_RE as _PASSTHROUGH_ATOM_RE,
    PERCENT_VALUE_RE as _SHARED_PERCENT_VALUE_RE,
    PHONE_PATTERN as _PHONE_PATTERN,
    STRUCTURED_TOKEN_PATTERN as _NUMERAL_PATTERN,
    SWIFT_BIC_PATTERN as _SWIFT_BIC_PATTERN,
    canonicalize_literal_values as _canonicalize_literal_values_shared,
    caps_phrase_to_sentence_case as _caps_phrase_to_sentence_case,
    is_code_like as _is_code_like,
    is_immutable_technical_text,
    lex_translatable_and_protected_spans,
    is_plain_caps_word as _is_plain_caps_word,
    is_valid_bic as _is_valid_bic,
    protect_structured_tokens as _protect_numerals,
    protected_value_count as _protected_value_count_shared,
    restore_structured_tokens as _restore_numerals,
)

# Pattern: text that is entirely made of digits, numeric punctuation, symbols,
# and/or known short financial codes — no translatable natural-language words.
# These cells should bypass the translation model entirely.
_PURE_PASSTHROUGH_PATTERN = re.compile(
    r'^[\s\d,.\-+±×÷=()/%$€£¥٪#\u2013\u2014\u00B1\u2019\u2018:;\'\"]*$'
)


_ARABIC_LETTER_RANGE = r"\u0621-\u064A\u0671-\u06D3\uFB50-\uFDFF\uFE70-\uFEFC"


def _is_degenerate_translation(source_text: str, translated_text: str) -> bool:
    """Detect repetitive hallucinated Arabic output or heavy unrequested vocalization loops."""
    if not translated_text:
        return False
    arabic_chars = re.findall(rf"[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFC]", translated_text)
    if len(arabic_chars) < 16:
        return False
    diacritics = re.findall(r"[\u064B-\u065F\u0670]", translated_text)
    src_diacritics = re.findall(r"[\u064B-\u065F\u0670]", source_text or "")
    if not src_diacritics and len(diacritics) / max(1, len(arabic_chars)) > 0.22:
        return True
    words = [w for w in re.findall(rf"[{_ARABIC_LETTER_RANGE}A-Za-z0-9]{{2,}}", translated_text)]
    if len(words) >= 6:
        run_len = 1
        for i in range(1, len(words)):
            if words[i] == words[i - 1]:
                run_len += 1
                if run_len >= 4:
                    return True
            else:
                run_len = 1
        if len(words) >= 9:
            trigrams = Counter(tuple(words[i : i + 3]) for i in range(len(words) - 2))
            if trigrams and trigrams.most_common(1)[0][1] >= 3:
                return True
    return False


_HALLUCINATION_STOCK_RE = re.compile(
    r"(?:لا\s+توجد\s+أسباب\s+إضافية|As\s+an\s+AI\b|Translate\s+the\s+following)",
    re.IGNORECASE,
)


def _strip_immutable_for_completeness(text: str, extra_values=()) -> str:
    """Remove protected atoms, web refs, placeholders, and acronyms before prose checks."""
    cleaned = text or ""
    cleaned, _ = stash_web_references(cleaned)
    cleaned = re.sub(r"\uE002\d+\uE003", " ", cleaned)
    for val in extra_values or ():
        if val and len(val) >= 2:
            cleaned = cleaned.replace(val, " ")
    cleaned = _ATOMIC_EXPRESSION.sub(" ", cleaned)
    cleaned = _NUMERAL_PATTERN.sub(" ", cleaned)
    cleaned = re.sub(r"<\s*/?\s*b\s*\d+\s*>", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\{\s*\d+\s*\}", " ", cleaned)
    cleaned = re.sub(r"\([A-Z0-9\-/]{2,12}\)", " ", cleaned)
    cleaned = re.sub(r"\b[A-Z0-9]{2,10}\b", " ", cleaned)
    return cleaned


def validate_translation_completeness(
    source_text: str,
    translated_text: str,
    *,
    target_lang: str = "ar",
    extra_protected=(),
) -> tuple[bool, str | None]:
    """Validate that an Arabic translation of substantial prose is complete and on-language."""
    tgt_norm = str(target_lang or "").lower().rsplit("-", 1)[-1]
    if tgt_norm not in {"ar", "ara", "arabic", "arb_arab"}:
        return True, None
    src = (source_text or "").strip()
    out = (translated_text or "").strip()
    if not src or _is_pure_passthrough(src):
        return True, None
    if not out:
        return False, "empty_translation"

    clean_src = _strip_immutable_for_completeness(src, extra_protected)
    clean_out = _strip_immutable_for_completeness(out, extra_protected)

    src_prose_words = [
        w for w in re.findall(r"\b[A-Za-z][A-Za-z'\u2019\-]{1,}\b", clean_src)
        if not w.isupper()
    ]
    if len(src_prose_words) < 4:
        return True, None

    if _is_degenerate_translation(src, out):
        return False, "degenerate_output"
    if _HALLUCINATION_STOCK_RE.search(out):
        return False, "unrelated_hallucination"

    out_latin_words = [
        w for w in re.findall(r"\b[A-Za-z][A-Za-z'\u2019\-]{1,}\b", clean_out)
        if not w.isupper()
    ]
    out_lower_latin = [w for w in out_latin_words if w[0].islower()]
    ar_words = re.findall(rf"[{_ARABIC_LETTER_RANGE}]+", clean_out)
    ar_chars = re.findall(rf"[{_ARABIC_LETTER_RANGE}]", clean_out)
    src_prose_chars = sum(len(w) for w in src_prose_words)

    # 1. Excessive untranslated English prose
    if len(ar_chars) < max(6, len(src_prose_words)) and (
        len(out_latin_words) >= 3 or clean_out.strip().lower() == clean_src.strip().lower()
    ):
        return False, "excessive_untranslated_source"
    if len(out_lower_latin) >= 4 and len(out_lower_latin) / max(1, len(src_prose_words)) >= 0.38:
        return False, "excessive_untranslated_source"

    # 2. Suspicious truncation / dropped final sentence / missing prose despite protected tokens
    if len(src_prose_words) >= 8 and len(ar_chars) < max(10, int(src_prose_chars * 0.25)):
        return False, "insufficient_translated_prose"

    src_sents = [
        s for s in split_sentences(src)
        if len([w for w in re.findall(r"\b[A-Za-z][A-Za-z'\u2019\-]{1,}\b", _strip_immutable_for_completeness(s, extra_protected)) if not w.isupper()]) >= 4
    ]
    if len(src_sents) >= 2:
        out_sents = [s.strip() for s in re.split(r"[.!?؟\n]+", out) if len(re.findall(rf"[{_ARABIC_LETTER_RANGE}]{{2,}}", s)) >= 3]
        if len(out_sents) < len(src_sents) and (
            len(ar_chars) < src_prose_chars * 0.55 or len(ar_words) < len(src_prose_words) * 0.72
        ):
            return False, "suspicious_truncation"

    if (
        len(src_prose_words) >= 8
        and src[-1] in ".!?؟"
        and out[-1] in "(,،:؛-"
        and len(ar_chars) < src_prose_chars * 0.55
    ):
        return False, "suspicious_truncation"

    return True, None


def _is_pure_passthrough(text: str) -> bool:
    """Return True if text contains no translatable natural-language words.

    Covers:
    - Pure numerics: "75", "75.3", "1,200,000", "8.6%", "(2018=100)"
    - Table year headers: "2020  2021  2022"
    - Known financial codes: "QR", "QCBRR", "Q1", "bps", "BBAN", "IBAN"
    - SWIFT/BIC codes: "ABQQQAQAXXX", "BBMEQAQXXXX", "QNBAQAQAXXX"
    - Bank identifiers: "QNBA", "CBQA", "DOHB", "ABQQ", "KLJI", "QIDB"
    - 2-letter ISO country codes: "AL", "BA", "GB", "SA", "AE", "CZ"
    - Phone / Fax numbers: "+974 44456371", "+974 44430382"
    - Arithmetic equations: "281211210/97 = 62", "502560126/97 =22"
    - Mixed code+number: "Q1 2022", "8.6% QoQ"
    - Negative/positive values: "-3.2", "+4.5%"

    Does NOT match: "Revenue 75", "Oil and gas 8.6%", "Growth rate"
    """
    stripped = text.strip()
    if not stripped:
        return True

    # Fast path: entirely digits, punctuation, symbols (no letters at all)
    if _PURE_PASSTHROUGH_PATTERN.match(stripped):
        return True

    # Direct match for SWIFT/BIC, IBAN, Phone numbers, Modulo algorithm strings,
    # URLs, DOIs, emails, Unix file paths, filenames, UUIDs, hashes, or multi-hyphen IDs.
    if _PASSTHROUGH_ATOM_RE.match(stripped):
        return True
    if (
        re.search(r"[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF]", stripped)
        and not re.search(r"[A-Za-z]", stripped)
    ):
        return True
    if _is_valid_bic(stripped) and not _is_plain_caps_word(stripped):
        return True
    if _IBAN_PATTERN.match(stripped):
        return True
    if _PHONE_PATTERN.match(stripped):
        return True
    if re.fullmatch(r'^(?:MOD\s*\d+(?:-\d+)?|ISO(?:/IEC)?\s*\d+(?:-\d+)?)$', stripped, re.IGNORECASE):
        return True
    if re.fullmatch(r'^[A-Z]{1,6}(?:/[A-Z0-9.\-]+){2,}$', stripped):
        return True
    if re.fullmatch(r'^\d+\s*[/÷\-–—+*×]\s*\d+\s*(?:=\s*\d+)?$', stripped):
        return True
    if re.fullmatch(r'^[A-Z0-9]{15,35}$', stripped) and any(c.isdigit() for c in stripped) and any(c.isalpha() for c in stripped):
        return True

    # Tokenize by whitespace and check every token
    tokens = stripped.split()
    for token in tokens:
        # Strip surrounding punctuation from the token
        clean = token.strip("()[]{}.,;:%+-±$€£¥٪'\"-")
        if not clean:
            continue
        # Is it purely digits/numeric?
        if re.fullmatch(r'[\d,.%+\-±]+', clean):
            continue
        if _PASSTHROUGH_ATOM_RE.match(clean):
            continue
        # Scientific notation: "2e-5", "-1.5E+10", "2e-5%"
        if re.fullmatch(r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)[eE][+-]?\d+%?', clean):
            continue
        # Is it a known passthrough code?
        if clean in _KNOWN_CODES or clean.upper() in _KNOWN_CODES:
            continue
        # Is it a SWIFT BIC code or IBAN string?
        if (_is_valid_bic(clean) and not _is_plain_caps_word(clean, text)) or _IBAN_PATTERN.match(clean):
            continue
        # A standalone ISO alpha-2 code is structural data.  Longer uppercase
        # words are not assumed to be codes; genuine acronyms and bank IDs are
        # enumerated in _KNOWN_CODES above.
        if len(tokens) == 1 and re.fullmatch(r'[A-Z]{2}', clean):
            continue
        # Alphanumeric compact identifiers remain safe when they contain a
        # digit; pure alphabetic words must go through translation.
        if (
            len(tokens) == 1
            and re.fullmatch(r'[A-Z][A-Z0-9]{0,11}', clean)
            and any(char.isdigit() for char in clean)
        ):
            continue
        # Is it a compound reference like "2-1", "4-4", "2022–2023"?
        if re.fullmatch(r'\d+[-\u2013\u2014]\d+', clean):
            continue
        # This token is a real word that needs translation
        return False

    return True


class RateLimiter:
    """
    A rate limiter using the leaky bucket algorithm to ensure a smooth, constant rate of requests.
    This implementation is thread-safe and robust against system clock changes.
    """

    def __init__(self, max_qps: int):
        if max_qps <= 0:
            raise ValueError("max_qps must be a positive number")
        self.max_qps = max_qps
        self.min_interval = 1.0 / max_qps
        self.lock = threading.Lock()
        # Use monotonic time to prevent issues with system time changes
        self.next_request_time = time.monotonic()

    def wait(self, _rate_limit_params: dict = None):
        """
        Blocks until the next request can be processed, ensuring the rate limit is not exceeded.
        """
        with self.lock:
            now = time.monotonic()

            wait_duration = self.next_request_time - now
            if wait_duration > 0:
                time.sleep(wait_duration)

            # Update the next allowed request time.
            # If the limiter has been idle, the next request should start from 'now'.
            now = time.monotonic()
            self.next_request_time = (
                max(self.next_request_time, now) + self.min_interval
            )

    def set_max_qps(self, max_qps: int):
        """
        Updates the maximum queries per second. This operation is thread-safe.
        """
        if max_qps <= 0:
            raise ValueError("max_qps must be a positive number")
        with self.lock:
            self.max_qps = max_qps
            self.min_interval = 1.0 / max_qps


_translate_rate_limiter = RateLimiter(5)


def set_translate_rate_limiter(max_qps):
    _translate_rate_limiter.set_max_qps(max_qps)


class BaseTranslator(ABC):
    # Due to cache limitations, name should be within 20 characters.
    # cache.py: translate_engine = CharField(max_length=20)
    name = "base"
    lang_map = {}
    supports_sentence_batching = False
    _model_status_callback = None

    def __init__(self, lang_in, lang_out, ignore_cache):
        self.ignore_cache = ignore_cache
        lang_in = self.lang_map.get(lang_in.lower(), lang_in)
        lang_out = self.lang_map.get(lang_out.lower(), lang_out)
        self.lang_in = lang_in
        self.lang_out = lang_out

        self.cache = TranslationCache(
            self.name,
            {
                "lang_in": lang_in,
                "lang_out": lang_out,
            },
        )

        self.translate_call_count = 0
        self.translate_cache_call_count = 0
        self.untranslated_count = 0  # text blocks the engine could not translate
        self._incomplete_blocks = AtomicInteger()
        self._call_state = threading.local()
        self._model_lock = threading.Lock()
        self._model_status_callback = None
        self.add_cache_impact_parameters("segmentation_version", SEGMENTATION_VERSION)

    def _get_model_lock(self):
        lock = getattr(self, "_model_lock", None)
        return lock if lock is not None else contextlib.nullcontext()

    def set_model_status_callback(self, callback):
        self._model_status_callback = callback

    def _emit_model_status(
        self,
        *,
        loading: bool,
        message: str = "",
        elapsed_seconds: float | None = None,
    ):
        cb = getattr(self, "_model_status_callback", None)
        if cb is not None:
            with contextlib.suppress(Exception):
                cb(loading=loading, message=message, elapsed_seconds=elapsed_seconds)

    @property
    def incomplete_count(self):
        blocks = getattr(self, "_incomplete_blocks", None)
        return blocks.value if blocks is not None else 0

    @property
    def source_spans_retained_count(self):
        """Number of sentence/clause/prose spans that kept their source text after recovery."""
        spans = getattr(self, "_source_spans_retained", None)
        return spans.value if spans is not None else 0

    def _note_source_spans_retained(self, count: int) -> None:
        if count <= 0:
            return
        spans = getattr(self, "_source_spans_retained", None)
        if spans is None:
            spans = AtomicInteger()
            self._source_spans_retained = spans
        for _ in range(count):
            spans.inc()

    def _get_call_state(self):
        state = getattr(self, "_call_state", None)
        if state is None:
            state = threading.local()
            self._call_state = state
        return state

    def _inc_incomplete_once(self):
        state = self._get_call_state()
        state.para_incomplete = True
        if getattr(state, "incomplete_counted", False):
            return
        state.incomplete_counted = True
        blocks = getattr(self, "_incomplete_blocks", None)
        if blocks is None:
            blocks = AtomicInteger()
            self._incomplete_blocks = blocks
        blocks.inc()

    def _mark_call_failed(self):
        state = self._get_call_state()
        state.failed = True
        state.para_failed = True

    def _mark_call_incomplete(self):
        state = self._get_call_state()
        state.incomplete = True
        state.para_incomplete = True
        self._inc_incomplete_once()

    def _begin_paragraph_tracking(self):
        state = self._get_call_state()
        state.para_failed = False
        state.para_incomplete = False
        state.validation_reason = None
        state.retry_attempted = False
        state.retry_outcome = None

    def _consume_paragraph_status(self) -> tuple[bool, bool]:
        state = self._get_call_state()
        failed = bool(getattr(state, "para_failed", False))
        incomplete = bool(getattr(state, "para_incomplete", False))
        state.para_failed = False
        state.para_incomplete = False
        return failed, incomplete

    def _consume_paragraph_validation(self) -> tuple[str | None, bool, str | None]:
        state = self._get_call_state()
        reason = getattr(state, "validation_reason", None)
        attempted = bool(getattr(state, "retry_attempted", False))
        outcome = getattr(state, "retry_outcome", None)
        state.validation_reason = None
        state.retry_attempted = False
        state.retry_outcome = None
        return reason, attempted, outcome

    def __del__(self):
        with contextlib.suppress(Exception):
            logger.info(
                f"{self.name} translate call count: {self.translate_call_count}"
            )
            logger.info(
                f"{self.name} translate cache call count: {self.translate_cache_call_count}",
            )

    def add_cache_impact_parameters(self, k: str, v):
        """
        Add parameters that affect the translation quality to distinguish the translation effects under different parameters.
        :param k: key
        :param v: value
        """
        self.cache.add_params(k, v)

    def translate(self, text, ignore_cache=False, rate_limit_params: dict = None):
        """
        Translate the text, and the other part should call this method.
        :param text: text to translate
        :return: translated text
        """
        self.translate_call_count += 1
        if not (self.ignore_cache or ignore_cache):
            try:
                cache = self.cache.get(text)
                if cache is not None:
                    self.translate_cache_call_count += 1
                    return cache
            except Exception as e:
                logger.debug(f"try get cache failed, ignore it: {e}")
        _translate_rate_limiter.wait()

        cleaned_text = remove_control_characters(str(text)) if text else text
        if not cleaned_text or not str(cleaned_text).strip():
            return text

        if _is_pure_passthrough(cleaned_text) or is_immutable_technical_text(cleaned_text):
            return cleaned_text

        # Shielding placeholder mechanism applied uniformly across all translation engines
        protected_text, numeral_map = _protect_numerals(cleaned_text)
        translation, cacheable = self._translate_protected(
            protected_text, numeral_map, rate_limit_params
        )

        if cacheable and not (self.ignore_cache or ignore_cache):
            self.cache.set(text, translation)
        return translation

    _PERCENT_VALUE_RE = _SHARED_PERCENT_VALUE_RE
    _NUMERIC_VALUE_RE = _SHARED_NUMERIC_VALUE_RE
    _retry_missing_values = False

    def _generate_translation(self, text: str) -> str:
        """Raw model call used for single-pass protected-content retry."""
        return self.do_translate(text)

    def _retry_target_satisfied(self, text: str) -> bool:
        """Verify retry output is non-empty and in Arabic when targeting Arabic."""
        target_lang = str(getattr(self, "lang_out", "")).lower()
        tgt_nllb = str(getattr(self, "tgt_nllb", ""))
        if target_lang.startswith("ar") or tgt_nllb == "arb_Arab":
            return bool(re.search(r"[\u0621-\u064A]", text))
        return bool(text and text.strip())

    @classmethod
    def _canonicalize_literal_values(cls, text: str, values) -> str:
        """Canonicalize Arabic/spaced percentage forms back to source tokens."""
        return _canonicalize_literal_values_shared(text, values)

    @classmethod
    def _protected_value_count(cls, text: str, value: str) -> int:
        """Boundary-aware count of protected literal occurrences in *text*."""
        return _protected_value_count_shared(text, value)

    @classmethod
    def _effective_protected_counts(cls, text: str, expected: Counter[str]) -> dict[str, int]:
        """Count occurrences of each expected token without double-counting tokens nested inside longer expected tokens."""
        counts = {value: cls._protected_value_count(text, value) for value in expected}
        ordered = sorted(expected.keys(), key=len, reverse=True)
        for idx, outer in enumerate(ordered):
            outer_cnt = counts.get(outer, 0)
            if outer_cnt <= 0:
                continue
            for inner in ordered[idx + 1 :]:
                if len(inner) >= len(outer):
                    continue
                inner_in_outer = cls._protected_value_count(outer, inner)
                if inner_in_outer > 0:
                    counts[inner] = counts[inner] - (outer_cnt * inner_in_outer)
        return counts

    @classmethod
    def _mismatched_protected_values(cls, text: str, expected: Counter[str]) -> set[str]:
        """Return expected protected values whose effective occurrence count != expected count."""
        effective = cls._effective_protected_counts(text, expected)
        return {value for value, exp_cnt in expected.items() if effective.get(value, 0) != exp_cnt}

    @staticmethod
    def _find_unresolved_placeholders(
        protected_source: str,
        restored: str,
        mapping: dict[str, str],
    ) -> list[str]:
        """Return any un-restored ``{n}`` or unallowed ``<bN>`` placeholder tags."""
        if not restored:
            return []
        if mapping:
            tag_pattern = re.compile("|".join(re.escape(k) for k in mapping))
            literal_chunks = tag_pattern.split(protected_source or "")
        else:
            literal_chunks = [protected_source or ""]
        literal_sources = list(literal_chunks) + list(mapping.values())

        expected_braces = Counter()
        allowed_b_tags: set[str] = set()
        for chunk in literal_sources:
            for match in re.finditer(r"\{\s*\d+\s*\}", chunk):
                expected_braces[re.sub(r"\s+", "", match.group(0))] += 1
            for match in re.finditer(r"<\s*/?\s*b\s*\d+\s*>", chunk, re.IGNORECASE):
                allowed_b_tags.add(re.sub(r"\s+", "", match.group(0)).lower())

        unresolved: list[str] = []
        seen_braces = Counter()
        for match in re.finditer(r"\{\s*\d+\s*\}", restored):
            norm = re.sub(r"\s+", "", match.group(0))
            seen_braces[norm] += 1
            if seen_braces[norm] > expected_braces[norm]:
                unresolved.append(match.group(0))
        for match in re.finditer(r"<\s*/?\s*b\s*\d+\s*>", restored, re.IGNORECASE):
            norm = re.sub(r"\s+", "", match.group(0)).lower()
            if norm not in allowed_b_tags:
                unresolved.append(match.group(0))
        return unresolved

    def _protected_integrity_fallback(self, translated, protected_source, mapping, expected):
        """Return *translated* only when all protected values stay intact and anchored; else source.

        Never append dropped protected values in a detached parenthetical list
        (e.g. ``(300، 45)`` at the end of a sentence), because doing so severs
        which value belongs to which entity or clause.  When recovery cannot
        prove exact source-span association, preserve ``source_full`` verbatim.
        """
        source_full = (
            _restore_numerals(protected_source, mapping, strict=False)
            if mapping
            else protected_source
        )
        if not translated or not str(translated).strip():
            return source_full
        if expected and self._mismatched_protected_values(translated, expected):
            return source_full
        if self._find_unresolved_placeholders(protected_source, translated, mapping):
            return source_full
        target_lang = str(getattr(self, "tgt_nllb", "") or getattr(self, "lang_out", ""))
        semantically_valid, _ = validate_translation_completeness(
            source_full,
            translated,
            target_lang=target_lang,
            extra_protected=tuple(expected.keys()),
        )
        return translated if semantically_valid else source_full

    def translate_batch(
        self,
        texts: list[str],
        ignore_cache: bool = False,
        rate_limit_params: dict = None,
    ) -> list[str]:
        """Translate a list of texts while preserving per-item protection and caching."""
        return [
            self.translate(text, ignore_cache=ignore_cache, rate_limit_params=rate_limit_params)
            for text in texts
        ]

    def _finalize_protected_translation(self, text, output, mapping, before_untranslated):
        state = self._get_call_state()
        first = _restore_numerals(output, mapping, strict=False)
        if mapping:
            first = self._canonicalize_literal_values(first, mapping.values())
        if (
            getattr(state, "failed", False)
            or getattr(self, "untranslated_count", 0) > before_untranslated
        ):
            return first, False

        if _is_degenerate_translation(text, first):
            if getattr(self, "_retry_missing_values", False) and hasattr(self, "_generate_translation"):
                clean_retry_text = re.sub(r"\s+", " ", text).strip()
                try:
                    regenerated = _restore_numerals(
                        self._generate_translation(clean_retry_text),
                        mapping,
                        strict=False,
                    )
                    if mapping:
                        regenerated = self._canonicalize_literal_values(regenerated, mapping.values())
                    if (
                        not _is_degenerate_translation(clean_retry_text, regenerated)
                        and not self._find_unresolved_placeholders(clean_retry_text, regenerated, mapping)
                    ):
                        first = regenerated
                except Exception:
                    logger.warning("Degenerate-output retry failed")
            if _is_degenerate_translation(text, first):
                self._inc_incomplete_once()
                logger.warning(
                    "Detected degenerate hallucinated translation output; falling back safely to source content."
                )
                safe_source = (
                    _restore_numerals(text, mapping, strict=False) if mapping else text
                )
                return safe_source, False

        unresolved = self._find_unresolved_placeholders(text, first, mapping)
        if not mapping and unresolved:
            self._inc_incomplete_once()
            logger.warning(
                "Translated output contains unresolved placeholders. Check the translated block."
            )
            return first, False

        expected = Counter(mapping.values()) if mapping else Counter()
        src_full = (
            _restore_numerals(text, mapping, strict=False) if mapping else text
        )
        missing = self._mismatched_protected_values(first, expected)
        if not missing and not unresolved:
            target_lang = str(getattr(self, "tgt_nllb", "") or getattr(self, "lang_out", ""))
            valid, val_reason = validate_translation_completeness(
                src_full,
                first,
                target_lang=target_lang,
                extra_protected=tuple(mapping.values()) if mapping else (),
            )
            if valid:
                return first, not getattr(state, "incomplete", False)

            state.validation_reason = val_reason
            if not getattr(self, "_retry_missing_values", False):
                self._inc_incomplete_once()
                logger.warning(
                    "Translated output failed completeness validation (%s); skipping cache.",
                    val_reason,
                )
                return first, False

            state.retry_attempted = True
            retried, retried_ok = self._retry_semantic_segments(src_full, mapping, target_lang)
            if retried_ok:
                state.retry_outcome = "succeeded"
                return retried, not getattr(state, "incomplete", False)
            state.retry_outcome = "failed"
            partial = self._recover_by_spans(src_full, target_lang)
            if partial is not None:
                if partial[2] == 0:
                    state.retry_outcome = "succeeded"
                    return partial[0], not getattr(state, "incomplete", False)
                state.retry_outcome = "partial"
                state.validation_reason = "partial_span_recovery"
                self._note_source_spans_retained(partial[2])
                self._inc_incomplete_once()
                logger.warning(
                    "Span recovery kept %d translated and %d source segment(s) (%s); not caching.",
                    partial[1], partial[2], val_reason,
                )
                return partial[0], False
            self._inc_incomplete_once()
            logger.warning(
                "Semantic retry failed completeness validation (%s); emitting incomplete block without caching.",
                val_reason,
            )
            return first, False

        if not getattr(self, "_retry_missing_values", False):
            self._inc_incomplete_once()
            logger.warning(
                "Translated output is missing protected content or kept unresolved placeholders. "
                "Check the translated block."
            )
            return self._protected_integrity_fallback(
                first, text, mapping, expected
            ), False

        was_incomplete = getattr(state, "incomplete", False)
        state.validation_reason = "protected_content_mismatch"
        # Do not expose full account numbers (including grouped IBANs) or bank codes.
        literal = {
            tag: value for tag, value in mapping.items()
            if value in missing
            and not (_is_valid_bic(value) or _IBAN_PATTERN.fullmatch(re.sub(r"\s+", "", value)))
        }
        if literal:
            state.retry_attempted = True
            retry_text = re.sub(r"\{\d+\}", lambda match: literal.get(match[0], match[0]), text)
            remaining = {tag: value for tag, value in mapping.items() if tag not in literal}
            state.incomplete = False
            try:
                candidate = _restore_numerals(
                    self._generate_translation(retry_text),
                    remaining,
                    strict=False,
                )
                candidate = self._canonicalize_literal_values(candidate, mapping.values())
            except Exception:
                logger.warning("Protected-content retry failed")
            else:
                retry_incomplete = getattr(state, "incomplete", False)
                candidate_valid, _ = validate_translation_completeness(
                    src_full,
                    candidate,
                    target_lang=str(
                        getattr(self, "tgt_nllb", "")
                        or getattr(self, "lang_out", "")
                    ),
                    extra_protected=tuple(expected.keys()),
                )
                if (
                    not retry_incomplete
                    and not self._find_unresolved_placeholders(retry_text, candidate, remaining)
                    and self._retry_target_satisfied(candidate)
                    and candidate_valid
                    and not self._mismatched_protected_values(candidate, expected)
                ):
                    state.retry_outcome = "succeeded"
                    return candidate, (not was_incomplete)

        # Retry longer blocks in smaller units when a whole-block decode
        # cannot keep every URL, date or other protected value in context.
        state.incomplete = was_incomplete
        if len(src_full) > 160:
            target_lang = str(getattr(self, "tgt_nllb", "") or getattr(self, "lang_out", ""))
            state.retry_attempted = True
            segmented, valid = self._retry_semantic_segments(src_full, mapping, target_lang)
            if valid and not self._mismatched_protected_values(segmented, expected):
                state.retry_outcome = "succeeded"
                return segmented, not was_incomplete
        if getattr(state, "retry_attempted", False):
            state.retry_outcome = "failed"
        target_lang = str(getattr(self, "tgt_nllb", "") or getattr(self, "lang_out", ""))
        partial = self._recover_by_spans(src_full, target_lang)
        if partial is not None:
            state.retry_attempted = True
            if partial[2] == 0:
                # Every sentence/clause validated exactly (protected values,
                # Arabic completeness, no placeholders): a genuine recovery.
                state.retry_outcome = "succeeded"
                return partial[0], not was_incomplete
            state.retry_outcome = "partial"
            state.validation_reason = "partial_span_recovery"
            self._note_source_spans_retained(partial[2])
            self._inc_incomplete_once()
            logger.warning(
                "Span recovery kept %d translated and %d source segment(s) after protected-content mismatch; not caching.",
                partial[1], partial[2],
            )
            return partial[0], False
        self._inc_incomplete_once()
        self._note_source_spans_retained(1)
        state.validation_reason = state.validation_reason or "source_text_retained"
        logger.warning(
            "Translated output is missing protected content or kept unresolved placeholders. "
            "Check the translated block."
        )
        return self._protected_integrity_fallback(
            first, text, mapping, expected
        ), False

    def _split_retry_segments(self, source_full: str) -> list[str]:
        """Split a block into citation/URL-safe sentence (or clause) segments."""
        web_spans = [(s, e) for s, e, _ in iter_web_references(source_full)]
        cites = [m.span() for m in AUTHOR_YEAR_CITATION.finditer(source_full)] + web_spans
        segments = [s.strip() for s in split_sentences(source_full, citation_spans=cites) if s and s.strip()]
        if len(segments) <= 1 and len(source_full) > 70:
            clauses = [
                c.strip()
                for c in re.split(r"(?<=[;:\u2014])\s+|(?<=,)\s+(?=(?:however|while|whereas|and|but|which|after|before)\b)", source_full, flags=re.IGNORECASE)
                if c and c.strip()
            ]
            if len(clauses) >= 2:
                segments = clauses
        return segments or [source_full]

    _CLAUSE_SPLIT_RE = re.compile(
        r"("
        r"[;:\u2014\u2013]\s+"
        r"|,\s+(?=(?:however|while|whereas|and|or|but|which|where|including|such\s+as|after|before|although|because|if|when|"
        r"economic|financial|growth|inflation|output|support|processed|ended|began|an?\b|the\b)\b)"
        r"|(?<!\d),\s+(?=\d+\s+[A-Za-z])"
        r"|(?<!\ba)\s+(?=(?:while|whereas|although|because)\s+[A-Za-z])"
        r")",
        re.IGNORECASE,
    )

    def _split_clauses_with_separators(self, text: str) -> list[tuple[str, str]]:
        """Split a sentence into ``(clause_text, trailing_separator)`` pairs."""
        parts = self._CLAUSE_SPLIT_RE.split(text)
        if len(parts) <= 1:
            return [(text, "")]
        out: list[tuple[str, str]] = []
        i = 0
        while i < len(parts):
            clause = parts[i]
            sep = parts[i + 1] if i + 1 < len(parts) else ""
            if clause or sep:
                out.append((clause, sep))
            i += 2
        return out if len(out) >= 2 else [(text, "")]

    def _validate_span_candidate(
        self,
        stripped: str,
        seg_prot: str,
        seg_map: dict[str, str],
        raw: str,
        target_lang: str,
    ) -> str | None:
        """Return the restored span text only if it proves exactly safe, else ``None``."""
        restored = _restore_numerals(raw, seg_map, strict=False) if seg_map else raw
        if seg_map:
            restored = self._canonicalize_literal_values(restored, seg_map.values())
        # Exact multiplicity (missing AND duplicated) against the source Counter.
        if seg_map and self._mismatched_protected_values(restored, Counter(seg_map.values())):
            return None
        if self._find_unresolved_placeholders(seg_prot, restored, seg_map):
            return None
        if not self._retry_target_satisfied(restored):
            return None
        if _is_degenerate_translation(stripped, restored) or _HALLUCINATION_STOCK_RE.search(restored):
            return None
        valid, _ = validate_translation_completeness(
            stripped,
            restored,
            target_lang=target_lang,
            extra_protected=tuple(seg_map.values()),
        )
        if not valid:
            return None
        # For short 2-3 word spans exempted by length in validate_translation_completeness,
        # still reject outputs that leave lowercase English words untranslated in Arabic mode.
        tgt_norm = str(target_lang or "").lower().rsplit("-", 1)[-1]
        if tgt_norm in {"ar", "ara", "arabic", "arb_arab"}:
            clean_out = _strip_immutable_for_completeness(restored, tuple(seg_map.values()))
            rem_lower = [
                w for w in re.findall(r"\b[A-Za-z][A-Za-z'\u2019\-]{1,}\b", clean_out)
                if not w.isupper() and w[0].islower()
            ]
            ar_chars = re.findall(rf"[{_ARABIC_LETTER_RANGE}]", clean_out)
            if rem_lower and len(ar_chars) < sum(len(w) for w in rem_lower):
                return None
        return restored.strip()

    def _try_translate_span_unit(self, span_text: str, target_lang: str) -> str | None:
        """Translate and validate one sentence/clause/prose span, or return ``None`` if unsafe.

        Bounded two-rung ladder.  The local model is deterministic, so re-sending
        the identical placeholder text can only reproduce the same failure.  The
        second rung therefore sends a *different* input: the literal values
        inline, which seq2seq models copy far more reliably than ``{N}`` tags.
        Both rungs go through the same exact-multiplicity validation.
        """
        stripped = span_text.strip()
        if not stripped:
            return span_text
        seg_prot, seg_map = _protect_numerals(stripped)
        try:
            raw = self._generate_translation(seg_prot)
            accepted = self._validate_span_candidate(stripped, seg_prot, seg_map, raw, target_lang)
            if accepted is not None:
                return accepted
            if seg_map:
                raw_literal = self._generate_translation(stripped)
                # Literal rung: nothing was masked, so values are validated as-is.
                accepted = self._validate_span_candidate(
                    stripped, stripped, seg_map, raw_literal, target_lang
                )
                if accepted is not None:
                    return accepted
            return None
        except Exception as exc:  # noqa: BLE001 - recovery must never raise
            logger.warning("Span recovery failed for one segment: %s", exc)
            return None

    def _recover_clause_by_lexed_spans(
        self,
        clause_text: str,
        target_lang: str,
    ) -> tuple[str, int, int] | None:
        """Lex *clause_text* into prose, protected tokens, and separators, translating prose spans."""
        lexed = lex_translatable_and_protected_spans(clause_text)
        protected_count = sum(1 for kind, _ in lexed if kind == "protected")
        prose_indices = [
            idx for idx, (kind, val) in enumerate(lexed)
            if kind == "prose" and len(re.findall(r"[A-Za-z]{2,}", val)) >= 2
        ]
        if protected_count == 0 or not prose_indices or (protected_count < 2 and len(prose_indices) < 2):
            return None
        out_tokens: list[str] = []
        tr_cnt = 0
        kept_cnt = 0
        for kind, val in lexed:
            if kind in {"protected", "separator"}:
                out_tokens.append(val)
                continue
            words = re.findall(r"[A-Za-z]{2,}", val)
            if not words:
                out_tokens.append(val)
                continue
            if len(words) < 2:
                out_tokens.append(val)
                kept_cnt += 1
                continue
            lead_ws = val[: len(val) - len(val.lstrip())]
            trail_ws = val[len(val.rstrip()) :]
            accepted = self._try_translate_span_unit(val.strip(), target_lang)
            if accepted is not None:
                lead = lead_ws or (" " if out_tokens and not out_tokens[-1][-1:].isspace() else "")
                trail = trail_ws or " "
                out_tokens.append(f"{lead}{accepted}{trail}")
                tr_cnt += 1
            else:
                out_tokens.append(val)
                kept_cnt += 1
        if tr_cnt == 0:
            return None
        reconstructed = re.sub(r"[^\S\r\n]{2,}", " ", "".join(out_tokens)).strip()
        return reconstructed, tr_cnt, kept_cnt

    def _recover_by_spans(self, source_full: str, target_lang: str):
        """Keep valid Arabic prose while retaining only the spans that cannot be proven safe.

        Operates hierarchically with bounded attempts:
        1. Sentence segments;
        2. Clause sub-segments for any sentence that fails;
        3. Lexed natural-language spans around immutable/protected tokens when a
           clause fails due to dense protected values.
        Returns ``(text, translated_segments, kept_segments)`` or ``None`` when no
        span could be safely translated.
        """
        web_spans = [(s, e) for s, e, _ in iter_web_references(source_full)]
        cites = [m.span() for m in AUTHOR_YEAR_CITATION.finditer(source_full)] + web_spans
        sentences = [s.strip() for s in split_sentences(source_full, citation_spans=cites) if s and s.strip()]
        if not sentences:
            sentences = [source_full.strip()]

        parts: list[str] = []
        translated_count = 0
        kept_count = 0
        if len(sentences) == 1:
            # For a single short sentence (<= 60 chars), only attempt sub-sentence
            # span recovery when it carries dense protected tokens (>= 3); shorter
            # atomic phrases fall back cleanly to the verbatim source span instead
            # of issuing unbounded extra model calls.
            if len(source_full) <= 60:
                lexed_sent = lex_translatable_and_protected_spans(source_full)
                if sum(1 for k, _ in lexed_sent if k == "protected") < 3:
                    return None

        for sent in sentences:
            if _is_pure_passthrough(sent) or is_immutable_technical_text(sent):
                parts.append(sent)
                continue
            if len(sentences) > 1:
                accepted = self._try_translate_span_unit(sent, target_lang)
                if accepted is not None:
                    parts.append(accepted)
                    translated_count += 1
                    continue

            # Bounded retry on smaller clause units
            clauses = self._split_clauses_with_separators(sent)
            if len(clauses) >= 2:
                clause_out: list[str] = []
                for c_text, c_sep in clauses:
                    if not c_text.strip():
                        clause_out.append(c_text + c_sep)
                        continue
                    if _is_pure_passthrough(c_text) or is_immutable_technical_text(c_text):
                        clause_out.append(c_text.strip() + c_sep)
                        continue
                    c_acc = self._try_translate_span_unit(c_text, target_lang)
                    if c_acc is not None:
                        clause_out.append(c_acc + c_sep)
                        translated_count += 1
                    else:
                        lex_rec = self._recover_clause_by_lexed_spans(c_text, target_lang)
                        if lex_rec is not None:
                            clause_out.append(lex_rec[0] + c_sep)
                            translated_count += lex_rec[1]
                            kept_count += lex_rec[2]
                        else:
                            clause_out.append(c_text.strip() + c_sep)
                            kept_count += 1
                parts.append(re.sub(r"[^\S\r\n]{2,}", " ", "".join(clause_out)).strip())
                continue

            # Single clause: try lexed prose spans around protected tokens
            lex_rec = self._recover_clause_by_lexed_spans(sent, target_lang)
            if lex_rec is not None:
                parts.append(lex_rec[0])
                translated_count += lex_rec[1]
                kept_count += lex_rec[2]
            else:
                parts.append(sent)
                kept_count += 1

        if translated_count == 0:
            return None
        reassembled = " ".join(p for p in parts if p)
        _, expected_map = _protect_numerals(source_full)
        if expected_map and self._mismatched_protected_values(reassembled, Counter(expected_map.values())):
            return None
        return reassembled, translated_count, kept_count

    def _retry_semantic_segments(
        self,
        source_full: str,
        mapping: dict[str, str] | None,
        target_lang: str,
    ) -> tuple[str, bool]:
        """Split a failed block into sentence/clause segments, translate each, and re-validate."""
        segments = self._split_retry_segments(source_full)

        state = self._get_call_state()
        translated_parts: list[str] = []
        expected = Counter(mapping.values()) if mapping else Counter()
        for seg in segments:
            if _is_pure_passthrough(seg):
                translated_parts.append(seg)
                continue
            seg_prot, seg_map = _protect_numerals(seg)
            try:
                seg_raw = self._generate_translation(seg_prot)
            except Exception as exc:
                logger.warning("Semantic-segment retry failed: %s", exc)
                return "", False
            seg_restored = (
                _restore_numerals(seg_raw, seg_map, strict=False) if seg_map else seg_raw
            )
            if seg_map:
                seg_restored = self._canonicalize_literal_values(seg_restored, seg_map.values())
            if self._find_unresolved_placeholders(seg_prot, seg_restored, seg_map):
                return "", False
            if seg_map and self._mismatched_protected_values(seg_restored, Counter(seg_map.values())):
                return "", False
            translated_parts.append(seg_restored.strip())

        reassembled = " ".join(part for part in translated_parts if part)
        if expected and self._mismatched_protected_values(reassembled, expected):
            return reassembled, False
        was_inc = getattr(state, "incomplete", False)
        state.incomplete = False
        ok, _ = validate_translation_completeness(
            source_full,
            reassembled,
            target_lang=target_lang,
            extra_protected=tuple(expected.keys()),
        )
        if not ok:
            state.incomplete = was_inc
            return reassembled, False
        state.incomplete = was_inc
        return reassembled, True

    def _translate_protected(self, text, mapping, rate_limit_params):
        state = self._get_call_state()
        state.failed = False
        state.incomplete = False
        state.incomplete_counted = False
        state.in_protected_call = True
        before_untranslated = getattr(self, "untranslated_count", 0)
        try:
            output = self.do_translate(text, rate_limit_params)
            return self._finalize_protected_translation(
                text, output, mapping, before_untranslated
            )
        finally:
            state.in_protected_call = False

    def llm_translate(self, text, ignore_cache=False, rate_limit_params: dict = None):
        """
        Translate the text, and the other part should call this method.
        :param text: text to translate
        :return: translated text
        """
        self.translate_call_count += 1
        if not (self.ignore_cache or ignore_cache):
            try:
                cache = self.cache.get(text)
                if cache is not None:
                    self.translate_cache_call_count += 1
                    return cache
            except Exception as e:
                logger.debug(f"try get cache failed, ignore it: {e}")
        _translate_rate_limiter.wait()
        translation = self.do_llm_translate(text, rate_limit_params)
        if not (self.ignore_cache or ignore_cache):
            try:
                self.cache.set(text, translation)
            except Exception as e:
                logger.debug(
                    f"try set cache failed, ignore it: {e}, text: {text}, translation: {translation}"
                )
        return translation

    @abstractmethod
    def do_llm_translate(self, text, rate_limit_params: dict = None):
        """
        Actual translate text, override this method
        :param text: text to translate
        :return: translated text
        """
        raise NotImplementedError

    @abstractmethod
    def do_translate(self, text, rate_limit_params: dict = None):
        """
        Actual translate text, override this method
        :param text: text to translate
        :return: translated text
        """
        logger.critical(
            f"Do not call BaseTranslator.do_translate. "
            f"Translator: {self}. "
            f"Text: {text}. ",
        )
        raise NotImplementedError

    def __str__(self):
        return f"{self.name} {self.lang_in} {self.lang_out} {self.model}"

    def get_rich_text_left_placeholder(self, placeholder_id: int):
        return f"<b{placeholder_id}>", r"<\s*b\s*" + str(placeholder_id) + r"\s*>"

    def get_rich_text_right_placeholder(self, placeholder_id: int):
        return f"</b{placeholder_id}>", r"<\s*\/\s*b\s*" + str(placeholder_id) + r"\s*>"

    def get_formular_placeholder(self, placeholder_id: int):
        return self.get_rich_text_left_placeholder(placeholder_id)


class OpenAITranslator(BaseTranslator):
    # https://github.com/openai/openai-python
    name = "openai"

    def __init__(
        self,
        lang_in,
        lang_out,
        model,
        base_url=None,
        api_key=None,
        ignore_cache=False,
        enable_json_mode_if_requested=False,
        send_dashscope_header=False,
        send_temperature=True,
    ):
        super().__init__(lang_in, lang_out, ignore_cache)
        self.options = {"temperature": 0}  # 随机采样可能会打断公式标记
        self.extra_body = {}
        # if 'gpt-5' in model and 'gpt-5-chat' not in model:
        #     self.extra_body['reasoning'] = {
        #         "effort": "minimal"
        #     }
        #     self.add_cache_impact_parameters("reasoning-effort", 'minimal')
        self.client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key,
            http_client=httpx.Client(
                limits=httpx.Limits(
                    max_connections=None, max_keepalive_connections=None
                ),
                timeout=60,  # Set a reasonable timeout
            ),
        )
        if send_temperature:
            self.add_cache_impact_parameters("temperature", self.options["temperature"])
        self.model = model
        self.enable_json_mode_if_requested = enable_json_mode_if_requested
        self.send_dashscope_header = send_dashscope_header
        self.send_temperature = send_temperature
        self.add_cache_impact_parameters("model", self.model)
        self.add_cache_impact_parameters("prompt", self.prompt(""))
        if self.enable_json_mode_if_requested:
            self.add_cache_impact_parameters(
                "enable_json_mode_if_requested", self.enable_json_mode_if_requested
            )
        self.token_count = AtomicInteger()
        self.prompt_token_count = AtomicInteger()
        self.completion_token_count = AtomicInteger()
        self.cache_hit_prompt_token_count = AtomicInteger()

    @retry(
        retry=retry_if_exception_type(openai.RateLimitError),
        stop=stop_after_attempt(100),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def do_translate(self, text, rate_limit_params: dict = None) -> str:
        if not text or not str(text).strip():
            return text
        cleaned_text = remove_control_characters(str(text))
        if not cleaned_text.strip() or _is_pure_passthrough(cleaned_text):
            return cleaned_text

        has_placeholders = bool(re.search(r'\{\d+\}', cleaned_text))
        if not has_placeholders:
            protected_text, numeral_map = _protect_numerals(cleaned_text)
        else:
            protected_text, numeral_map = cleaned_text, {}

        options = {}
        if self.send_temperature:
            options.update(self.options)

        response = self.client.chat.completions.create(
            model=self.model,
            **options,
            messages=self.prompt(protected_text),
            extra_body=self.extra_body,
        )
        self.update_token_count(response)
        raw_content = response.choices[0].message.content.strip()
        if numeral_map:
            raw_content = _restore_numerals(
                raw_content, numeral_map, strict=False
            )
        return raw_content

    def prompt(self, text):
        return [
            {
                "role": "system",
                "content": "You are a professional,authentic machine translation engine.",
            },
            {
                "role": "user",
                "content": f";; Treat next line as plain text input and translate it into {self.lang_out}, output translation ONLY. If translation is unnecessary (e.g. proper nouns, codes, {'{{1}}, etc. '}), return the original text. NO explanations. NO notes. Input:\n\n{text}",
            },
        ]

    @retry(
        retry=retry_if_exception_type(openai.RateLimitError),
        stop=stop_after_attempt(100),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def do_llm_translate(self, text, rate_limit_params: dict = None):
        if text is None:
            return None

        options = {}
        if self.send_temperature:
            options.update(self.options)
        if self.enable_json_mode_if_requested and rate_limit_params.get(
            "request_json_mode", False
        ):
            options["response_format"] = {"type": "json_object"}

        extra_headers = {}
        if self.send_dashscope_header:
            extra_headers["X-DashScope-DataInspection"] = (
                '{"input": "disable", "output": "disable"}'
            )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                **options,
                max_tokens=2048,
                messages=[
                    {
                        "role": "user",
                        "content": text,
                    },
                ],
                extra_headers=extra_headers,
                extra_body=self.extra_body,
            )
            self.update_token_count(response)
            return response.choices[0].message.content.strip()
        except openai.BadRequestError as e:
            if (
                "系统检测到输入或生成内容可能包含不安全或敏感内容，请您避免输入易产生敏感内容的提示语，感谢您的配合。"
                in e.message
            ):
                raise ContentFilterError(e.message) from e
            else:
                raise

    def update_token_count(self, response):
        try:
            if response.usage and response.usage.total_tokens:
                self.token_count.inc(response.usage.total_tokens)
            if response.usage and response.usage.prompt_tokens:
                self.prompt_token_count.inc(response.usage.prompt_tokens)
            if response.usage and response.usage.completion_tokens:
                self.completion_token_count.inc(response.usage.completion_tokens)
            if response.usage and (
                hit_count := getattr(response.usage, "prompt_cache_hit_tokens", 0)
            ):
                self.cache_hit_prompt_token_count.inc(hit_count)
        except Exception as e:
            logger.exception("Error updating token count")

    def get_formular_placeholder(self, placeholder_id: int):
        return "{v" + str(placeholder_id) + "}", f"{{\\s*v\\s*{placeholder_id}\\s*}}"
        return "{{" + str(placeholder_id) + "}}"

    def get_rich_text_left_placeholder(self, placeholder_id: int):
        return (
            f"<style id='{placeholder_id}'>",
            f"<\\s*style\\s*id\\s*=\\s*'\\s*{placeholder_id}\\s*'\\s*>",
        )

    def get_rich_text_right_placeholder(self, placeholder_id: int):
        return "</style>", r"<\s*\/\s*style\s*>"


def _collect_token_ids(value) -> set[int]:
    if value is None:
        return set()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {int(value)}
    if isinstance(value, (list, tuple, set)):
        return {int(v) for v in value if v is not None}
    return set()


def _count_seq2seq_tokens(tokenizer, text: str) -> int:
    """Measure exact model input tokens without truncating."""
    try:
        encoded = tokenizer([text], truncation=False)
        if isinstance(encoded, dict) or hasattr(encoded, "__getitem__"):
            ids = encoded["input_ids"]
            if ids:
                first = ids[0]
                return len(first) if hasattr(first, "__len__") else len(ids)
    except TypeError:
        try:
            encoded = tokenizer(text)
            ids = encoded["input_ids"]
            return len(ids[0]) if ids and hasattr(ids[0], "__len__") else len(ids)
        except Exception:
            pass
    except Exception:
        pass
    if hasattr(tokenizer, "encode"):
        try:
            return len(tokenizer.encode(text, truncation=False))
        except TypeError:
            return len(tokenizer.encode(text))
    if hasattr(tokenizer, "tokenize"):
        return len(tokenizer.tokenize(text)) + 2
    return len(text.split()) + 2


def _is_output_truncated(translated_tokens, tokenizer, model, max_length: int) -> bool:
    """Return True if generation reached *max_length* without ending on EOS."""
    first = translated_tokens[0]
    seq = first.tolist() if hasattr(first, "tolist") else list(first)
    if len(seq) < max_length:
        return False
    eos_ids = _collect_token_ids(getattr(tokenizer, "eos_token_id", None)) | _collect_token_ids(
        getattr(getattr(model, "config", None), "eos_token_id", None)
    )
    pad_ids = (
        _collect_token_ids(getattr(tokenizer, "pad_token_id", None))
        | _collect_token_ids(getattr(getattr(model, "config", None), "pad_token_id", None))
    ) - eos_ids
    non_pad = list(seq)
    while non_pad and int(non_pad[-1]) in pad_ids:
        non_pad.pop()
    if eos_ids:
        return not non_pad or int(non_pad[-1]) not in eos_ids
    return True


class _Seq2SeqTranslator(BaseTranslator):
    """Shared token-budget segmentation, batch decoding, and truncation guards for HF Seq2Seq engines."""

    supports_sentence_batching = True

    def _decode_batch_size(self) -> int:
        raw = os.getenv("TRANSDOC_DECODE_BATCH_SIZE", "8")
        try:
            return max(1, int(raw))
        except ValueError:
            return 8

    def _prepare_generation_lang(self):
        """Hook for subclasses needing tokenizer language state before generation."""

    def _seq2seq_make_gen_kwargs(self):
        return lambda limit: {
            "max_length": limit,
            "no_repeat_ngram_size": 4,
            "repetition_penalty": 1.1,
        }

    def _effective_max_length(self) -> int:
        limit = int(self.max_length)
        tok_max = getattr(getattr(self, "_tokenizer", None), "model_max_length", None)
        if isinstance(tok_max, int) and 0 < tok_max < 100_000:
            limit = min(limit, tok_max)
        return max(1, limit)

    def _prepare_seq2seq_batch_inputs(
        self, texts: list[str], max_length: int, device: str
    ) -> tuple[object, list[bool]]:
        count_fn = lambda s: _count_seq2seq_tokens(self._tokenizer, s)
        truncated_flags = [count_fn(text) > max_length for text in texts]
        any_truncated = any(truncated_flags)
        if any_truncated:
            for text, is_trunc in zip(texts, truncated_flags):
                if is_trunc:
                    logger.warning(
                        "%s input segment reached > max_length=%s tokens and could not be split safely; truncating and flagging incomplete.",
                        self.name,
                        max_length,
                    )
        try:
            inputs = self._tokenizer(
                texts,
                return_tensors="pt",
                padding=True,
                truncation=any_truncated,
                **(
                    {"max_length": max_length}
                    if any_truncated
                    else {}
                ),
            )
        except TypeError:
            inputs = self._tokenizer(
                texts,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=max_length,
            )
        input_ids = inputs["input_ids"]
        seq_len = input_ids.shape[1] if hasattr(input_ids, "shape") else (len(input_ids[0]) if input_ids else 0)
        if not any_truncated and seq_len > max_length:
            truncated_flags = [True] * len(texts)
            logger.warning(
                "%s input segment reached %s tokens (> max_length=%s) and could not be split safely; truncating and flagging incomplete.",
                self.name,
                seq_len,
                max_length,
            )
            inputs = self._tokenizer(
                texts,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=max_length,
            )
        if hasattr(inputs, "to"):
            inputs = inputs.to(device)
        return inputs, truncated_flags

    def _prepare_seq2seq_inputs(self, text: str, max_length: int, device: str):
        inputs, flags = self._prepare_seq2seq_batch_inputs([text], max_length, device)
        return inputs, flags[0]

    def _run_seq2seq_batch(
        self, texts: list[str], make_gen_kwargs, max_length: int
    ) -> list[tuple[str, bool, bool]]:
        import torch

        device = self._get_device()
        generate_kwargs = make_gen_kwargs(max_length)
        with self._get_model_lock():
            self._prepare_generation_lang()
            inputs, truncated_flags = self._prepare_seq2seq_batch_inputs(texts, max_length, device)
            with torch.no_grad():
                translated_tokens = self._model.generate(**inputs, **generate_kwargs)
        decoded_list = self._tokenizer.batch_decode(translated_tokens, skip_special_tokens=True)
        outcomes: list[tuple[str, bool, bool]] = []
        for idx, decoded in enumerate(decoded_list):
            row_tokens = translated_tokens[idx : idx + 1]
            out_trunc = _is_output_truncated(
                row_tokens, self._tokenizer, self._model, max_length
            )
            outcomes.append((decoded, truncated_flags[idx], out_trunc))
        return outcomes

    def _run_seq2seq_chunk(self, text: str, generate_kwargs: dict, max_length: int) -> tuple[str, bool, bool]:
        import torch

        device = self._get_device()
        with self._get_model_lock():
            self._prepare_generation_lang()
            inputs, input_truncated = self._prepare_seq2seq_inputs(text, max_length, device)
            with torch.no_grad():
                translated_tokens = self._model.generate(**inputs, **generate_kwargs)
        output_truncated = _is_output_truncated(
            translated_tokens, self._tokenizer, self._model, max_length
        )
        decoded = self._tokenizer.batch_decode(translated_tokens, skip_special_tokens=True)[0]
        return decoded, input_truncated, output_truncated

    def _translate_seq2seq_segments(
        self,
        segments: list[str],
        make_gen_kwargs,
        max_length: int,
        depth: int = 0,
    ) -> list[str]:
        results: list[str] = []
        min_output_resplit = max(4, min(16, max_length // 2))
        for seg in segments:
            decoded, in_trunc, out_trunc = self._run_seq2seq_chunk(
                seg, make_gen_kwargs(max_length), max_length
            )
            if in_trunc:
                self._mark_call_incomplete()
            if out_trunc:
                seg_tokens = _count_seq2seq_tokens(self._tokenizer, seg)
                if depth < 2 and seg_tokens >= min_output_resplit:
                    sub_segments, _ = split_text_for_token_budget(
                        seg,
                        lambda s: _count_seq2seq_tokens(self._tokenizer, s),
                        max(1, seg_tokens // 2),
                        allow_word_split=False,
                    )
                    if len(sub_segments) > 1:
                        results.extend(
                            self._translate_seq2seq_segments(
                                sub_segments, make_gen_kwargs, max_length, depth=depth + 1
                            )
                        )
                        continue
                logger.warning(
                    "%s generation hit max_length=%s without EOS; marking block incomplete.",
                    self.name,
                    max_length,
                )
                self._mark_call_incomplete()
            results.append(decoded)
        return results

    def _translate_seq2seq_text(self, text: str, make_gen_kwargs) -> str:
        state = self._get_call_state()
        if not getattr(state, "in_protected_call", False):
            state.failed = False
            state.incomplete = False
            state.incomplete_counted = False

        limit = self._effective_max_length()
        input_budget = max(1, limit - 2) if limit > 4 else limit
        count_fn = lambda s: _count_seq2seq_tokens(self._tokenizer, s)
        if count_fn(text) > limit:
            segments, had_oversized = split_text_for_token_budget(
                text, count_fn, input_budget, allow_word_split=True
            )
            if had_oversized:
                self._mark_call_incomplete()
        else:
            segments = [text]

        translated_parts = self._translate_seq2seq_segments(
            segments, make_gen_kwargs, limit
        )
        if len(translated_parts) == 1:
            return translated_parts[0]
        joined = " ".join(part.strip() for part in translated_parts if part and part.strip())
        return joined if joined else (translated_parts[0] if translated_parts else "")

    def translate_batch(
        self,
        texts: list[str],
        ignore_cache: bool = False,
        rate_limit_params: dict = None,
    ) -> list[str]:
        """Batch-decode multi-sentence lists while preserving per-item M2b/M4 guarantees."""
        if not texts:
            return []
        if len(texts) == 1:
            return [self.translate(texts[0], ignore_cache=ignore_cache, rate_limit_params=rate_limit_params)]

        use_cache = not (self.ignore_cache or ignore_cache)
        results: list[str] = [""] * len(texts)
        pending: list[tuple[int, str, str, dict[str, str]]] = []

        for idx, text in enumerate(texts):
            self.translate_call_count += 1
            if use_cache:
                try:
                    cached = self.cache.get(text)
                    if cached is not None:
                        self.translate_cache_call_count += 1
                        results[idx] = cached
                        continue
                except Exception as exc:
                    logger.debug(f"try get cache failed, ignore it: {exc}")

            cleaned = remove_control_characters(str(text)) if text else text
            if not cleaned or not str(cleaned).strip():
                results[idx] = text
                continue
            if _is_pure_passthrough(cleaned):
                results[idx] = cleaned
                continue
            protected_text, numeral_map = _protect_numerals(cleaned)
            pending.append((idx, text, protected_text, numeral_map))

        if not pending:
            return results

        if len(pending) == 1:
            _translate_rate_limiter.wait()
            idx, raw_text, protected_text, numeral_map = pending[0]
            translated, cacheable = self._translate_protected(
                protected_text, numeral_map, rate_limit_params
            )
            if cacheable and use_cache:
                self.cache.set(raw_text, translated)
            results[idx] = translated
            return results

        _translate_rate_limiter.wait()
        try:
            self._load_model()
        except Exception as exc:
            logger.error(f"Error loading {self.name} model for batch translation: {exc}")
            for idx, raw_text, _, _ in pending:
                self.untranslated_count = getattr(self, "untranslated_count", 0) + 1
                self._mark_call_failed()
                results[idx] = remove_control_characters(str(raw_text))
            return results

        limit = self._effective_max_length()
        make_gen_kwargs = self._seq2seq_make_gen_kwargs()
        normal_items: list[tuple[int, int, str, str, dict[str, str]]] = []
        oversized_items: list[tuple[int, str, str, dict[str, str]]] = []

        for idx, raw_text, protected_text, numeral_map in pending:
            tok_len = _count_seq2seq_tokens(self._tokenizer, protected_text)
            if tok_len > limit:
                oversized_items.append((idx, raw_text, protected_text, numeral_map))
            else:
                normal_items.append((tok_len, idx, raw_text, protected_text, numeral_map))

        for idx, raw_text, protected_text, numeral_map in oversized_items:
            translated, cacheable = self._translate_protected(
                protected_text, numeral_map, rate_limit_params
            )
            if cacheable and use_cache:
                self.cache.set(raw_text, translated)
            results[idx] = translated

        # Group similarly sized sentences to minimize attention padding overhead.
        normal_items.sort(key=lambda item: (item[0], item[1]))
        batch_size = self._decode_batch_size()

        for start in range(0, len(normal_items), batch_size):
            chunk = normal_items[start : start + batch_size]
            batch_inputs = [item[3] for item in chunk]
            try:
                raw_outputs = self._run_seq2seq_batch(batch_inputs, make_gen_kwargs, limit)
            except Exception as exc:
                logger.warning(f"{self.name} batch decode failed ({exc}); falling back per-sentence.")
                for _, idx, raw_text, protected_text, numeral_map in chunk:
                    translated, cacheable = self._translate_protected(
                        protected_text, numeral_map, rate_limit_params
                    )
                    if cacheable and use_cache:
                        self.cache.set(raw_text, translated)
                    results[idx] = translated
                continue

            for (_, idx, raw_text, protected_text, numeral_map), (decoded, in_trunc, out_trunc) in zip(
                chunk, raw_outputs
            ):
                state = self._get_call_state()
                saved_para_incomplete = getattr(state, "para_incomplete", False)
                saved_para_failed = getattr(state, "para_failed", False)
                saved_val_reason = getattr(state, "validation_reason", None)
                saved_retry_attempted = getattr(state, "retry_attempted", False)
                saved_retry_outcome = getattr(state, "retry_outcome", None)
                state.failed = False
                state.incomplete = False
                state.incomplete_counted = False
                state.in_protected_call = True
                before_untranslated = getattr(self, "untranslated_count", 0)
                try:
                    if in_trunc:
                        self._mark_call_incomplete()
                    if out_trunc:
                        recovered_parts = self._translate_seq2seq_segments(
                            [protected_text], make_gen_kwargs, limit
                        )
                        decoded = (
                            recovered_parts[0]
                            if len(recovered_parts) == 1
                            else " ".join(p.strip() for p in recovered_parts if p and p.strip())
                        )
                finally:
                    state.in_protected_call = False
                translated, cacheable = self._finalize_protected_translation(
                    protected_text, decoded, numeral_map, before_untranslated
                )
                if saved_para_incomplete:
                    state.para_incomplete = True
                if saved_para_failed:
                    state.para_failed = True
                if saved_retry_attempted:
                    state.retry_attempted = True
                # Preserve the worst/most-informative outcome across sentences in the batch
                outcome_rank = {None: 0, "succeeded": 1, "partial": 2, "failed": 3}
                cur_out = getattr(state, "retry_outcome", None)
                if outcome_rank.get(saved_retry_outcome, 0) > outcome_rank.get(cur_out, 0):
                    state.retry_outcome = saved_retry_outcome
                    state.validation_reason = saved_val_reason
                elif not getattr(state, "validation_reason", None) and saved_val_reason:
                    state.validation_reason = saved_val_reason
                if cacheable and use_cache:
                    self.cache.set(raw_text, translated)
                results[idx] = translated

        return results


class MarianMTTranslator(_Seq2SeqTranslator):
    """
    Offline/Local Machine Translation using Hugging Face MarianMT models.
    Requires no API keys and runs entirely locally.
    """
    name = "marianmt"

    MODEL_MAP = {
        ("en", "ar"): "marefa-nlp/marefa-mt-en-ar",
        ("en", "en-ar"): "marefa-nlp/marefa-mt-en-ar",
        ("en", "es"): "Helsinki-NLP/opus-mt-en-es",
        ("en", "fr"): "Helsinki-NLP/opus-mt-en-fr",
        ("en", "de"): "Helsinki-NLP/opus-mt-en-de",
        ("en", "zh"): "Helsinki-NLP/opus-mt-en-zh",
        ("en", "ru"): "Helsinki-NLP/opus-mt-en-ru",
        ("en", "it"): "Helsinki-NLP/opus-mt-en-it",
        ("en", "pt"): "Helsinki-NLP/opus-mt-en-roa",
        ("en", "ko"): "Helsinki-NLP/opus-mt-en-ko",
        ("en", "ja"): "Helsinki-NLP/opus-mt-en-jap",
        ("ar", "en"): "Helsinki-NLP/opus-mt-ar-en",
        ("es", "en"): "Helsinki-NLP/opus-mt-es-en",
        ("fr", "en"): "Helsinki-NLP/opus-mt-fr-en",
        ("de", "en"): "Helsinki-NLP/opus-mt-de-en",
        ("zh", "en"): "Helsinki-NLP/opus-mt-zh-en",
        ("ru", "en"): "Helsinki-NLP/opus-mt-ru-en",
        ("it", "en"): "Helsinki-NLP/opus-mt-it-en",
    }

    def __init__(
        self,
        lang_in: str,
        lang_out: str,
        model_name: str = None,
        device: str = None,
        max_length: int = 512,
        ignore_cache: bool = False,
    ):
        super().__init__(lang_in, lang_out, ignore_cache)
        
        norm_in = lang_in.split("-")[-1].lower() if "-" in lang_in and lang_in != "zh-CN" else lang_in.lower()
        norm_out = lang_out.split("-")[-1].lower() if "-" in lang_out and lang_out != "zh-CN" else lang_out.lower()

        if model_name is None or model_name in ("marianmt", "huggingface", "local", "default"):
            model_name = self.MODEL_MAP.get(
                (norm_in, norm_out),
                f"Helsinki-NLP/opus-mt-{norm_in}-{norm_out}"
            )
        
        self.model = model_name
        self.model_name = model_name
        self._device = device
        self.max_length = max_length
        self._model = None
        self._tokenizer = None
        self.add_cache_impact_parameters("model", self.model_name)
        self.add_cache_impact_parameters("max_length", self.max_length)
        self._retry_missing_values = norm_in == "en" and norm_out == "ar"
        if self._retry_missing_values:
            self.add_cache_impact_parameters("protected_content_retry", 1)

    def _get_device(self):
        if self._device is not None:
            return self._device
        import torch
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def _load_model(self):
        with self._get_model_lock():
            if self._model is None or self._tokenizer is None:
                import torch
                from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, MarianMTModel, MarianTokenizer
                device = self._get_device()
                started = time.monotonic()
                logger.info(f"Loading local MarianMT model: {self.model_name} on device: {device}")
                self._emit_model_status(loading=True, message=f"Loading {self.model_name}...")
                try:
                    try:
                        self._tokenizer = MarianTokenizer.from_pretrained(self.model_name)
                        self._model = MarianMTModel.from_pretrained(self.model_name).to(device)
                    except Exception:
                        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
                        self._model = AutoModelForSeq2SeqLM.from_pretrained(self.model_name).to(device)
                    self._model.eval()
                finally:
                    self._emit_model_status(
                        loading=False,
                        message=f"Model {self.model_name} ready",
                        elapsed_seconds=time.monotonic() - started,
                    )

    def do_translate(self, text: str, rate_limit_params: dict = None) -> str:
        if not text or not str(text).strip():
            return text

        cleaned_text = remove_control_characters(str(text))
        if not cleaned_text.strip():
            return text

        # Pure numeric/financial token or table cell — bypass translation completely
        if _is_pure_passthrough(cleaned_text):
            return cleaned_text

        self._load_model()

        # Protect numerals from translation (if not already shielded by translate())
        has_placeholders = bool(re.search(r'\{\d+\}', cleaned_text))
        if not has_placeholders:
            protected_text, numeral_map = _protect_numerals(cleaned_text)
        else:
            protected_text, numeral_map = cleaned_text, {}

        try:
            translation = self._generate_translation(protected_text)
            # Restore original numerals if shielded locally
            if numeral_map:
                translation = _restore_numerals(
                    translation.strip(), numeral_map, strict=False
                )
            return translation
        except Exception as e:
            logger.error(f"Error during MarianMT translation: {e}")
            self.untranslated_count = getattr(self, "untranslated_count", 0) + 1
            self._mark_call_failed()
            return cleaned_text

    def _seq2seq_make_gen_kwargs(self):
        return lambda limit: {
            "max_length": limit,
            "no_repeat_ngram_size": 4,
            "repetition_penalty": 1.1,
        }

    def _generate_translation(self, text: str) -> str:
        return self._translate_seq2seq_text(text, self._seq2seq_make_gen_kwargs())

    def do_llm_translate(self, text, rate_limit_params: dict = None):
        raise NotImplementedError("MarianMT uses sequence-to-sequence translation, not LLM chat completions.")


HuggingFaceTranslator = MarianMTTranslator


class NLLBTranslator(_Seq2SeqTranslator):
    """
    State-of-the-Art Offline Local Translation with Meta NLLB-200 (No Language Left Behind).
    Supports 200+ languages including rich Modern Standard Arabic with full GPU (CUDA) acceleration.
    """
    name = "nllb"

    NLLB_LANG_MAP = {
        "en": "eng_Latn",
        "ar": "arb_Arab",
        "en-ar": "arb_Arab",
        "es": "spa_Latn",
        "fr": "fra_Latn",
        "de": "deu_Latn",
        "zh": "zho_Hans",
        "zh-cn": "zho_Hans",
        "ru": "rus_Cyrl",
        "it": "ita_Latn",
        "pt": "por_Latn",
        "ko": "kor_Hang",
        "ja": "jpn_Jpan",
    }

    def __init__(
        self,
        lang_in: str,
        lang_out: str,
        model_name: str = "facebook/nllb-200-distilled-600M",
        device: str = None,
        max_length: int = 512,
        ignore_cache: bool = False,
        load_in_8bit: bool = None,
    ):
        super().__init__(lang_in, lang_out, ignore_cache)

        norm_in = lang_in.split("-")[-1].lower() if "-" in lang_in and lang_in != "zh-CN" else lang_in.lower()
        norm_out = lang_out.split("-")[-1].lower() if "-" in lang_out and lang_out != "zh-CN" else lang_out.lower()

        self.src_nllb = self.NLLB_LANG_MAP.get(norm_in, "eng_Latn")
        self.tgt_nllb = self.NLLB_LANG_MAP.get(norm_out, "arb_Arab")

        raw_model = (model_name or "").lower().strip()
        if raw_model in (None, "", "nllb", "nllb-600m", "nllb-distilled", "nllb-200-600m", "default", "facebook/nllb-200-distilled-600m"):
            model_name = "facebook/nllb-200-distilled-600M"
            if load_in_8bit is None:
                load_in_8bit = False
        elif raw_model in ("nllb-1.3b", "nllb-200", "nllb-200-1.3b", "1.3b", "facebook/nllb-200-1.3b"):
            model_name = "facebook/nllb-200-1.3B"
            if load_in_8bit is None:
                load_in_8bit = False
        elif raw_model in ("nllb-3.3b", "3.3b", "nllb-200-3.3b", "facebook/nllb-200-3.3b", "nllb-3.3b-8bit", "nllb-8bit"):
            model_name = "facebook/nllb-200-3.3B"
            if load_in_8bit is None:
                load_in_8bit = True

        self.model_name = model_name
        self.model = model_name
        self.max_length = max_length
        self._device = device
        self.load_in_8bit = bool(load_in_8bit)
        self._model = None
        self._tokenizer = None
        self._forced_bos_token_id = None
        self.add_cache_impact_parameters("model", self.model_name)
        self.add_cache_impact_parameters("load_in_8bit", str(self.load_in_8bit))
        self._retry_missing_values = (
            self.src_nllb == "eng_Latn" and self.tgt_nllb == "arb_Arab"
        )
        self.add_cache_impact_parameters("max_length", self.max_length)
        if self._retry_missing_values:
            self.add_cache_impact_parameters("protected_content_retry", 1)

    def _get_device(self):
        if self._device is not None:
            return self._device
        import torch
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def _load_model(self):
        with self._get_model_lock():
            if self._model is None or self._tokenizer is None:
                import torch
                from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

                device = self._get_device()
                started = time.monotonic()
                logger.info(f"Loading Meta NLLB-200 model: {self.model_name} on device: {device} (8-bit: {self.load_in_8bit})")
                self._emit_model_status(loading=True, message=f"Loading {self.model_name}...")
                try:
                    self._tokenizer = AutoTokenizer.from_pretrained(
                        self.model_name,
                        src_lang=self.src_nllb
                    )

                    # Use 8-bit quantization on CUDA if requested (e.g. 3.3B model)
                    if device.startswith("cuda") and self.load_in_8bit:
                        try:
                            from transformers import BitsAndBytesConfig
                            logger.info("Enabling BitsAndBytes 8-bit quantization for NLLB on CUDA")
                            quant_config = BitsAndBytesConfig(load_in_8bit=True)
                            self._model = AutoModelForSeq2SeqLM.from_pretrained(
                                self.model_name,
                                quantization_config=quant_config,
                                device_map="auto"
                            )
                        except Exception as e:
                            logger.warning(f"Could not load 8-bit quantized model ({e}), falling back to float16")
                            self._model = AutoModelForSeq2SeqLM.from_pretrained(
                                self.model_name,
                                dtype=torch.float16
                            ).to(device)
                    elif device.startswith("cuda"):
                        # Use float16 on CUDA for fast inference and low VRAM footprint
                        self._model = AutoModelForSeq2SeqLM.from_pretrained(
                            self.model_name,
                            dtype=torch.float16
                        ).to(device)
                    else:
                        self._model = AutoModelForSeq2SeqLM.from_pretrained(
                            self.model_name
                        ).to(device)

                    self._model.eval()

                    try:
                        self._forced_bos_token_id = self._tokenizer.convert_tokens_to_ids(self.tgt_nllb)
                    except Exception:
                        self._forced_bos_token_id = None
                finally:
                    self._emit_model_status(
                        loading=False,
                        message=f"Model {self.model_name} ready",
                        elapsed_seconds=time.monotonic() - started,
                    )

    def do_translate(self, text: str, rate_limit_params: dict = None) -> str:
        if not text or not str(text).strip():
            return text

        cleaned_text = remove_control_characters(str(text))
        if not cleaned_text.strip():
            return text

        # Pure numeric/financial token or table cell — bypass translation completely
        if _is_pure_passthrough(cleaned_text):
            return cleaned_text

        self._load_model()

        # Protect numerals from translation (if not already shielded by translate())
        has_placeholders = bool(re.search(r'\{\d+\}', cleaned_text))
        if not has_placeholders:
            protected_text, numeral_map = _protect_numerals(cleaned_text)
        else:
            protected_text, numeral_map = cleaned_text, {}

        try:
            translation = self._generate_translation(protected_text)
            # Restore original numerals if shielded locally
            if numeral_map:
                translation = _restore_numerals(
                    translation.strip(), numeral_map, strict=False
                )
            return translation
        except Exception as e:
            logger.error(f"Error during NLLB translation: {e}")
            self.untranslated_count = getattr(self, "untranslated_count", 0) + 1
            self._mark_call_failed()
            return cleaned_text

    def _prepare_generation_lang(self):
        if getattr(self, "_tokenizer", None) is not None:
            self._tokenizer.src_lang = self.src_nllb

    def _seq2seq_make_gen_kwargs(self):
        def make_gen_kwargs(limit: int) -> dict:
            gen_kwargs = {
                "max_length": limit,
                "num_beams": 3 if self.load_in_8bit else 4,
                "no_repeat_ngram_size": 4,
                "repetition_penalty": 1.1,
            }
            if self._forced_bos_token_id is not None:
                gen_kwargs["forced_bos_token_id"] = self._forced_bos_token_id
            return gen_kwargs

        return make_gen_kwargs

    def _generate_translation(self, text):
        self._prepare_generation_lang()
        return self._translate_seq2seq_text(text, self._seq2seq_make_gen_kwargs())

    def do_llm_translate(self, text, rate_limit_params: dict = None):
        raise NotImplementedError("NLLB uses sequence-to-sequence translation, not LLM chat completions.")


