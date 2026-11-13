import contextlib
import logging
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
    split_text_for_token_budget,
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
# Pattern priority (highest-specificity first to avoid partial matches):
_CITATION_SURNAME = r"[A-ZÀ-ÖØ-Þ][^\W\d_]*(?:[-'’][^\W\d_]+)*"
_CITATION_AUTHORS = rf"{_CITATION_SURNAME}(?:\s+et\s+al\.|\s+(?:and|&)\s+{_CITATION_SURNAME})?"
_CITATION_YEAR = r"(?:18|19|20)\d{2}[a-z]?"
_CITATION_ENTRY = rf"{_CITATION_AUTHORS},\s*{_CITATION_YEAR}"
AUTHOR_YEAR_CITATION = re.compile(
    rf"\({_CITATION_ENTRY}(?:;\s*{_CITATION_ENTRY})*\)"
    rf"|\b{_CITATION_SURNAME}\s+et\s+al\.\s*\({_CITATION_YEAR}\)",
)

_NUMERAL_PATTERN = re.compile(
    # 0. Bibliographic atoms that must survive byte-for-byte: URLs, DOIs,
    # and numeric bracket citations such as [12], [3, 7] or [4-9].
    r'(https?://[^\s<>"\u201d\u2019]*[^\s<>"\u201d\u2019.,;:!?)\]]|www\.[^\s<>"\u201d\u2019]*[^\s<>"\u201d\u2019.,;:!?)\]])|'
    r'(\b10\.\d{4,9}/[^\s<>"\u201d\u2019]*[^\s<>"\u201d\u2019.,;:!?)\]])|'
    r'(\[\s*\d{1,3}(?:\s*[,\u2013\u2014-]\s*\d{1,3})*\s*\])|'
    rf"({AUTHOR_YEAR_CITATION.pattern})|"
    # 1. Parenthetical mixed tokens: (2018=100), (NDS3), (281211210/97 =62), (Chart 7-1)
    # Exclude prose parentheticals with lowercase words such as (First 4 character of BIC),
    # (e.g. QA00 for QATAR), or (29 characters for QATAR) so their text is translated while
    # inner numbers and codes remain shielded by rules 2-14.
    r'(\(\s*(?:(?:Chart|Table|Fig(?:ure)?)\s+)?[A-Z0-9\s,.\-+±×÷=/%$€£¥٪\u2013\u2014:;_]*\d[A-Z0-9\s,.\-+±×÷=/%$€£¥٪\u2013\u2014:;_]*\))|'
    # 2. Arithmetic division / equations: 281211210/97 = 62, 502560126/97 = 22, 221000/97= 34
    r'(\b\d+\s*[/÷]\s*\d+\s*(?:=\s*\d+)?\b)|'
    # 3. Arithmetic operations with equals: 98-34=64, 2018=100
    r'(\b\d+\s*[-–—+*×]\s*\d+\s*=\s*\d+\b)|'
    r'(\b\d+\s*=\s*\d+\b)|'
    # 4. Modulo and international standard references: MOD 97-10, MOD 97, ISO/IEC 7064, ISO 13616-1
    r'(\b(?:MOD\s*\d+(?:-\d+)?|ISO(?:/IEC)?\s*\d+(?:-\d+)?)\b)|'
    # 5. Full IBAN strings (with or without 4-character block spaces) and long account strings (15-35 chars)
    r'(\b[A-Z]{2}\d{2}(?:\s+[A-Z0-9]{4}){2,7}(?:\s+[A-Z0-9]{1,4})?\b)|'
    r'(\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b)|'
    r'(\b(?=[A-Z0-9]{15,35}\b)(?=[A-Z0-9]*\d)[A-Z0-9]+\b)|'
    # Compact identifiers must be protected before shorter prefixes or numbers.
    r'((?<![\w\-\u2010\u2011])[A-Z][A-Z0-9]{1,9}[-\u2010\u2011][0-9]{2,6}'
    r'(?:[.-][0-9]{1,6})*(?!\w|[-\u2010\u2011]\w|\.\d))|'

    # 6. SWIFT BIC codes embedded in text (8 or 11 uppercase chars): ABQQQAQAXXX, BBMEQAQXXXX, SCBLQAQXXXX
    r'(\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b)|'
    # 7. Bank Identifiers (4-letter routing codes): BBME, QNBA, CBQA, DOHB, IBOQ, etc.
    r'(\b(?:QNBA|CBQA|DOHB|QISB|ABQQ|QIIB|ARAB|MSHQ|IBOQ|BBME|SCBL|UNIL|BNPA|MAFR|KLJI|BRWA|QIDB)\b)|'
    # 8. All 54 ISO 2-letter Country Codes: AL, AD, AT, BE, BA, BG, HR, QA, etc.
    r'(\b(?:AL|AD|AT|BE|BA|BG|HR|CY|CZ|DK|EE|FO|FI|FR|GE|DE|GI|GR|GL|HU|IS|IE|IL|IT|JO|KW|LV|LB|LI|LT|LU|MK|MT|MR|MU|MD|MC|ME|NL|NO|PK|PL|PT|QA|RO|SM|SA|RS|SK|SI|ES|SE|CH|TN|TR|AE|GB)\b)|'
    # 9. Mixed alphanumeric codes containing both letters and numbers (3 to 35 chars): Q1, H2, MOD97, A01
    r'(\b(?=[A-Z0-9]{3,35}\b)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]+\b)|'
    # 10. Compound numeric references: 2-1, Chart 4-4, 7-3, 5.1.1, 2.1.2
    # Ordinals must precede the generic number alternatives below. Otherwise the
    # word-boundary checks on the numeric alternatives leave the suffix behind
    # (``5th`` becomes ``{0}th``), which lets MT and BiDi reorder it.
    r'(\b\d+(?:st|nd|rd|th)\s*[-\u2013\u2014]\s*\d+(?:st|nd|rd|th)\b)|'
    r'(\b\d+(?:st|nd|rd|th)\b)|'
    r'(\b\d+[-\u2013\u2014]\d+\b)|'
    r'(\b\d+(?:\.\d+){1,3}\b)|'
    # 11. Known financial/statistical acronyms that must never be translated
    r'(\b(?:QCB(?:RR|LR|DR)?|AOIR|QMR|NDS\d*|CPI|PPI|GDP|GNP|IMF|OPEC|FISIM|BBAN|IBAN|BIC|SWIFT|STP|TERMS|QATCH|Y-o-Y|Q-o-Q|MoM|RHS|LHS|USD|EUR|GBP|SAR|AED|QR|BPS|bps)\b)|'
    # 12. International phone and fax numbers: +974 44456371, +974 44430382
    r'(\+\d{1,4}\s*\d{3,4}\s*\d{3,4}(?:\s*\d{3,4})?)|'
    # 13. Numbers: integers, comma-separated thousands, decimals, percentages
    #    e.g. 75, 75.3, 75,432, 1,200,000.5, 8.6%, -3.2, +4.5
    r'([+-]?(?<!\w)\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s*[%\u066A])?(?!\w))|'
    # 14. Plain integers not already caught (catches standalone digits)
    r'((?<!\w)\d+(?:\.\d+)?(?!\w))'
)


def _protect_numerals(text: str) -> tuple[str, dict[str, str]]:
    """Replace numeric and reference patterns with curly bracket placeholders before translation.
    
    Using placeholders like {0}, {1} is recognized by Seq2Seq translation models
    (MarianMT, NLLB) as untranslated tokens, avoiding XML tag corruption (<1 b>, 0>)
    and token repetition loops.
    
    Returns:
        tuple of (text with placeholders, mapping of placeholder -> original value)
    """
    mapping = {}
    counter = [0]
    
    # Reserve IDs used by numeric placeholders and by PDF formula/style tags.
    # A decoder may rewrite {1} as <b1>, so the IDs must stay distinct.
    existing_placeholders = set(re.findall(r'\{\d+\}', text))
    existing_placeholders.update(
        f"{{{tag_id}}}"
        for tag_id in re.findall(r"<\s*/?\s*b\s*([0-9]+)\s*>", text, re.IGNORECASE)
    )

    def _replacer(match):
        original = match.group(0)
        if original in existing_placeholders:
            return original
        if _is_plain_caps_word(original):
            return original
        placeholder = f"{{{counter[0]}}}"
        while placeholder in existing_placeholders:
            counter[0] += 1
            placeholder = f"{{{counter[0]}}}"
        mapping[placeholder] = original
        counter[0] += 1
        return placeholder
    
    protected = _NUMERAL_PATTERN.sub(_replacer, text)
    return protected, mapping


def _restore_numerals(text: str, mapping: dict[str, str]) -> str:
    """Restore placeholders with original values after translation.
    
    Tries multiple fallback patterns because Seq2Seq models sometimes:
    - Add spaces inside brackets: { 0 } instead of {0}
    - Drop one bracket: {0 or 0}
    - Convert to angle brackets: <0> or <b0>
    - Substitute digit 0 with letter O/o: {O} or {o}
    - Convert digits to Arabic-Indic: {٠}
    - Convert to square brackets or parentheses: [0], (0)
    """
    # Deduplicate repeated identical parenthesized tokens before and during restoration:
    # e.g. (BBAN) (BBAN) -> (BBAN)
    text = re.sub(r'(\([A-Za-z0-9\-_]{2,15}\))(?:\s*\1)+', r'\1', text)

    if not mapping:
        return text

    # Deduplicate repeated identical placeholder tags before substitution:
    # e.g. ({0})({0}) -> ({0}), {0}{0} -> {0}
    text = re.sub(r'(\(\s*\{\s*\d+\s*\}\s*\))(?:\s*\1)+', r'\1', text)
    text = re.sub(r'(\{\s*\d+\s*\})(?:\s*\1)+', r'\1', text)

    for placeholder, original in mapping.items():
        tag_num = placeholder.strip("{}")
        # Primary: standard {0} or with internal whitespace { 0 }
        text = re.sub(r'\{\s*' + re.escape(tag_num) + r'\s*\}', original, text)
        
        # Fallback: model substituted digit 0 with uppercase/lowercase O (e.g. {O} or {o})
        if tag_num == "0":
            text = re.sub(r'\{\s*[Oo]\s*\}', original, text)
            text = re.sub(r'<\s*(?:b\s*)?[Oo]\s*>', original, text)
            text = re.sub(r'\[\s*[Oo]\s*\]', original, text)

        # Fallback: model converted digits to Arabic-Indic (e.g. {٠}, {١})
        arabic_indic_num = "".join(chr(0x0660 + int(c)) if c.isdigit() else c for c in tag_num)
        if arabic_indic_num != tag_num:
            text = re.sub(r'\{\s*' + re.escape(arabic_indic_num) + r'\s*\}', original, text)
            text = re.sub(r'<\s*(?:b\s*)?' + re.escape(arabic_indic_num) + r'\s*>', original, text)

        # Fallback: bracket partially dropped — {0 or 0}
        text = re.sub(r'(?<!\d)\{' + re.escape(tag_num) + r'(?!\d)', original, text)
        text = re.sub(r'(?<!\d)' + re.escape(tag_num) + r'\}(?!\d)', original, text)

        # Fallback: square brackets [0] or parentheses (0)
        text = re.sub(r'\[\s*' + re.escape(tag_num) + r'\s*\]', original, text)
        text = re.sub(r'\(\s*' + re.escape(tag_num) + r'\s*\)', original, text)

        # Fallback: model converted to XML-style — <0>, < 0 >, <b0>, <b 0>
        text = re.sub(r'<\s*(?:b\s*)?' + re.escape(tag_num) + r'\s*>', original, text)

    # Post-restore spacing cleanup:
    # Deduplicate repeated identical parenthesized tokens: (BBAN)(BBAN) -> (BBAN)
    text = re.sub(r'(\([A-Za-z0-9\-_]{2,15}\))(?:\s*\1)+', r'\1', text)

    # NLLB can occasionally repeat a restored critical token immediately
    # after itself (with or without a space).  This is especially dangerous
    # in the MOD-97 walkthrough because a duplicated IBAN looks plausible but
    # is financially wrong.  Only collapse *adjacent* copies of a token that
    # came from the source mapping; independent source occurrences remain.
    for original in set(mapping.values()):
        if not (_SWIFT_BIC_PATTERN.match(original) or _IBAN_PATTERN.match(original)):
            continue
        duplicate = re.compile(r'(' + re.escape(original) + r')(?:\s*\1)+')
        text = duplicate.sub(original, text)

    # Insert space between digits and Arabic-script letters (NOT Latin letters,
    # and NOT Arabic percent/decimal punctuation \u0660-\u066D, to preserve
    # codes like Q1, H2, P6, CHAPTER 03 and percentages like 20٪).
    text = re.sub(r'(\d)([\u0621-\u064A\u066E-\u06D3\u06D5])', r'\1 \2', text)
    text = re.sub(r'([\u0621-\u064A\u066E-\u06D3\u06D5])(\d)', r'\1 \2', text)
    # Remove stray spaces before punctuation
    text = re.sub(r'\s+([,.:;!?،؛])', r'\1', text)
    # Collapse any double spaces left by restoration
    text = re.sub(r'[^\S\r\n]{2,}', ' ', text).strip()

    # Byte-identity verification:
    # For critical SWIFT/BIC codes and IBAN identifiers, assert the restored output exactly equals
    # the original input byte-for-byte to prevent code corruption.
    # For natural language numerals/words that Seq2Seq models might idiomatically rephrase or condense,
    # log a diagnostic warning rather than crashing the pipeline and reverting the paragraph to English.
    expected_counts = Counter(mapping.values())
    for original_token, expected_count in expected_counts.items():
        actual_count = text.count(original_token)
        if actual_count < expected_count:
            is_critical = bool(_is_valid_bic(original_token) or _IBAN_PATTERN.match(original_token))
            if is_critical:
                raise AssertionError(
                    f"Byte-identity check failed! Expected at least {expected_count} occurrence(s) "
                    f"of critical banking code '{original_token}', but found {actual_count} in restored text:\n  '{text}'"
                )
            else:
                logger.warning(
                    f"Shielded token '{original_token}' expected {expected_count} occurrence(s), "
                    f"found {actual_count} in restored text. Preserving translated content."
                )
        elif actual_count > expected_count and (
            _is_valid_bic(original_token) or _IBAN_PATTERN.match(original_token)
        ):
            raise AssertionError(
                f"Byte-identity check failed! Expected exactly {expected_count} occurrence(s) "
                f"of critical banking code '{original_token}', found {actual_count}:\n  '{text}'"
            )

    return text


# Pattern: text that is entirely made of digits, numeric punctuation, symbols,
# and/or known short financial codes — no translatable natural-language words.
# These cells should bypass the translation model entirely.
_PURE_PASSTHROUGH_PATTERN = re.compile(
    r'^[\s\d,.\-+±×÷=()/%$€£¥٪\u2013\u2014\u00B1\u2019\u2018:;\'\"]*$'
)

# Explicit SWIFT BIC regex: 4-letter bank code + 2-letter country + 2-char location + opt 3-char branch
_SWIFT_BIC_PATTERN = re.compile(r'^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?$')

# ISO 3166-1 alpha-2 country codes (plus XK, which SWIFT uses for Kosovo).
# A real BIC has one of these in characters 5-6. Ordinary ALL-CAPS English words that
# are 8 or 11 letters long (INCOMING, NATIONAL, DEVELOPMENT) do not.
_ISO_COUNTRY_CODES = frozenset(
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG "
    "BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI "
    "CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH "
    "ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ "
    "GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT "
    "JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS "
    "LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU "
    "MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG "
    "PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG "
    "SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK "
    "TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU "
    "WF WS XK YE YT ZA ZM ZW "
    .split()
)


def _is_valid_bic(token: str) -> bool:
    """BIC-shaped token whose characters 5-6 are a real country code."""
    return bool(_SWIFT_BIC_PATTERN.match(token)) and token[4:6] in _ISO_COUNTRY_CODES


def _is_plain_caps_word(token: str) -> bool:
    """All-letter 8/11 character caps word that only looks like a BIC."""
    return (
        token.isalpha()
        and bool(_SWIFT_BIC_PATTERN.match(token))
        and token[4:6] not in _ISO_COUNTRY_CODES
    )


def _is_code_like(word: str) -> bool:
    """Known code, 11-character BIC, or 8-character BIC from a known bank ID."""
    return word in _KNOWN_CODES or (
        _is_valid_bic(word) and (len(word) == 11 or word[:4] in _KNOWN_CODES)
    )


def _caps_phrase_to_sentence_case(text: str) -> str:
    """Turn an ALL-CAPS phrase of ordinary words into sentence case.

    Models read 'STANDARDS DOCUMENT' badly, and a word like DOCUMENT has the shape of a
    BIC. Phrases holding a known code, a BIC from a known bank, or a short acronym are
    left alone. An 8-letter BIC from an unknown bank inside such a phrase is treated as a
    word (known limit).
    """
    if not text:
        return text
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        words = stripped.split()
        if (
            len(words) >= 2
            and all(re.fullmatch(r"[A-Z]{4,}", word) for word in words)
            and not any(_is_code_like(word) for word in words)
        ):
            line = line.replace(stripped, stripped.capitalize(), 1)
        lines.append(line)
    return "\n".join(lines)

# Full IBAN account pattern: 2-letter country + 2 check digits + up to 30 alphanumeric chars
_IBAN_PATTERN = re.compile(r'^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$')

# Phone / Fax numbers with optional country code
_PHONE_PATTERN = re.compile(r'^\+?\d{1,4}(?:[\s\-]\d{3,4}){2,4}$')

# Known short codes/acronyms that should never be translated
_KNOWN_CODES = frozenset({
    "QR", "USD", "EUR", "GBP", "SAR", "AED",
    "QCBRR", "QCBLR", "QCBDR", "AOIR", "QMR",
    "CPI", "PPI", "GDP", "GNP", "IMF", "OPEC",
    "NDS", "NDS3", "FISIM", "RHS", "LHS",
    "Q1", "Q2", "Q3", "Q4",
    "H1", "H2",
    "YoY", "Y-o-Y", "QoQ", "Q-o-Q", "MoM",
    "bps", "Bps", "BPS",
    "BBAN", "IBAN", "BIC", "SWIFT", "STP", "TERMS", "QATCH",
    # Qatar bank identifiers used as standalone table cells.  Keeping this
    # explicit is safer than treating every 3-4 letter uppercase English word
    # (for example JULY or TYPE) as an untranslatable code.
    "QNBA", "CBQA", "DOHB", "QISB", "ABQQ", "QIIB", "ARAB", "MSHQ",
    "IBOQ", "BBME", "SCBL", "UNIL", "BNPA", "MAFR", "KLJI", "BRWA", "QIDB",
})


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

    # Direct match for SWIFT/BIC, IBAN, Phone numbers, or Modulo algorithm strings
    if _is_valid_bic(stripped):
        return True
    if _IBAN_PATTERN.match(stripped):
        return True
    if _PHONE_PATTERN.match(stripped):
        return True
    if re.fullmatch(r'^(?:MOD\s*\d+(?:-\d+)?|ISO(?:/IEC)?\s*\d+(?:-\d+)?)$', stripped, re.IGNORECASE):
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
        # Is it a known passthrough code?
        if clean in _KNOWN_CODES or clean.upper() in _KNOWN_CODES:
            continue
        # Is it a SWIFT BIC code or IBAN string?
        if _is_valid_bic(clean) or _IBAN_PATTERN.match(clean):
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
        self.add_cache_impact_parameters("segmentation_version", SEGMENTATION_VERSION)

    @property
    def incomplete_count(self):
        blocks = getattr(self, "_incomplete_blocks", None)
        return blocks.value if blocks is not None else 0

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

    def _consume_paragraph_status(self) -> tuple[bool, bool]:
        state = self._get_call_state()
        failed = bool(getattr(state, "para_failed", False))
        incomplete = bool(getattr(state, "para_incomplete", False))
        state.para_failed = False
        state.para_incomplete = False
        return failed, incomplete

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

        if _is_pure_passthrough(cleaned_text):
            return cleaned_text

        # Shielding placeholder mechanism applied uniformly across all translation engines
        protected_text, numeral_map = _protect_numerals(cleaned_text)
        translation, cacheable = self._translate_protected(
            protected_text, numeral_map, rate_limit_params
        )

        if cacheable and not (self.ignore_cache or ignore_cache):
            self.cache.set(text, translation)
        return translation

    def _translate_protected(self, text, mapping, rate_limit_params):
        state = self._get_call_state()
        state.failed = False
        state.incomplete = False
        state.incomplete_counted = False
        state.in_protected_call = True
        before_untranslated = getattr(self, "untranslated_count", 0)
        try:
            output = self.do_translate(text, rate_limit_params)
        finally:
            state.in_protected_call = False
        restored = _restore_numerals(output, mapping)
        if (
            getattr(state, "failed", False)
            or getattr(state, "incomplete", False)
            or getattr(self, "untranslated_count", 0) > before_untranslated
        ):
            return restored, False
        return restored, True

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
            raw_content = _restore_numerals(raw_content, numeral_map)
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
    """Shared token-budget segmentation and truncation guards for HF Seq2Seq engines."""

    def _effective_max_length(self) -> int:
        limit = int(self.max_length)
        tok_max = getattr(getattr(self, "_tokenizer", None), "model_max_length", None)
        if isinstance(tok_max, int) and 0 < tok_max < 100_000:
            limit = min(limit, tok_max)
        return max(1, limit)

    def _prepare_seq2seq_inputs(self, text: str, max_length: int, device: str):
        try:
            inputs = self._tokenizer(
                [text],
                return_tensors="pt",
                padding=True,
                truncation=False,
            )
            was_truncated = False
        except TypeError:
            inputs = self._tokenizer(
                [text],
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=max_length,
            )
            was_truncated = False
        input_ids = inputs["input_ids"]
        seq_len = input_ids.shape[1] if hasattr(input_ids, "shape") else len(input_ids[0])
        if seq_len > max_length:
            was_truncated = True
            logger.warning(
                "%s input segment reached %s tokens (> max_length=%s) and could not be split safely; truncating and flagging incomplete.",
                self.name,
                seq_len,
                max_length,
            )
            inputs = self._tokenizer(
                [text],
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=max_length,
            )
        if hasattr(inputs, "to"):
            inputs = inputs.to(device)
        return inputs, was_truncated

    def _run_seq2seq_chunk(self, text: str, generate_kwargs: dict, max_length: int) -> tuple[str, bool, bool]:
        import torch

        device = self._get_device()
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

    def _get_device(self):
        if self._device is not None:
            return self._device
        import torch
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def _load_model(self):
        if self._model is None or self._tokenizer is None:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, MarianMTModel, MarianTokenizer
            device = self._get_device()
            logger.info(f"Loading local MarianMT model: {self.model_name} on device: {device}")
            try:
                self._tokenizer = MarianTokenizer.from_pretrained(self.model_name)
                self._model = MarianMTModel.from_pretrained(self.model_name).to(device)
            except Exception:
                self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
                self._model = AutoModelForSeq2SeqLM.from_pretrained(self.model_name).to(device)
            self._model.eval()

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
            translation = self._translate_seq2seq_text(
                protected_text,
                lambda limit: {
                    "max_length": limit,
                    "no_repeat_ngram_size": 4,
                    "repetition_penalty": 1.1,
                },
            )
            # Restore original numerals if shielded locally
            if numeral_map:
                translation = _restore_numerals(translation.strip(), numeral_map)
            return translation
        except Exception as e:
            logger.error(f"Error during MarianMT translation: {e}")
            self.untranslated_count = getattr(self, "untranslated_count", 0) + 1
            self._mark_call_failed()
            return cleaned_text

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
            self.model_name == "facebook/nllb-200-distilled-600M"
            and self.src_nllb == "eng_Latn"
            and self.tgt_nllb == "arb_Arab"
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
        if self._model is None or self._tokenizer is None:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
            
            device = self._get_device()
            logger.info(f"Loading Meta NLLB-200 model: {self.model_name} on device: {device} (8-bit: {self.load_in_8bit})")
            
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
                translation = _restore_numerals(translation.strip(), numeral_map)
            return translation
        except Exception as e:
            logger.error(f"Error during NLLB translation: {e}")
            self.untranslated_count = getattr(self, "untranslated_count", 0) + 1
            self._mark_call_failed()
            return cleaned_text

    def _translate_protected(self, text, mapping, rate_limit_params):
        first, cacheable = super()._translate_protected(text, mapping, rate_limit_params)
        state = self._get_call_state()
        if getattr(state, "failed", False) or (not cacheable and not getattr(state, "incomplete", False)):
            return first, False
        if not self._retry_missing_values or not mapping:
            return first, cacheable

        expected = Counter(mapping.values())
        missing = {
            value for value, count in expected.items()
            if self._protected_value_count(first, value) < count
        }
        if not missing:
            return first, cacheable

        was_incomplete = getattr(state, "incomplete", False)
        # Do not expose full account numbers (including grouped IBANs) or bank codes.
        literal = {
            tag: value for tag, value in mapping.items()
            if value in missing
            and not (_is_valid_bic(value) or _IBAN_PATTERN.fullmatch(re.sub(r"\s+", "", value)))
        }
        if literal:
            retry_text = re.sub(r"\{\d+\}", lambda match: literal.get(match[0], match[0]), text)
            remaining = {tag: value for tag, value in mapping.items() if tag not in literal}
            state.incomplete = False
            try:
                candidate = _restore_numerals(self._generate_translation(retry_text), remaining)
                candidate = self._canonicalize_literal_values(candidate, literal.values())
            except Exception:
                logger.warning("NLLB protected-content retry failed")
            else:
                retry_incomplete = getattr(state, "incomplete", False)
                if (
                    not retry_incomplete
                    and re.search(r"[\u0621-\u064A]", candidate)
                    and all(
                        self._protected_value_count(candidate, value) == count
                        for value, count in expected.items()
                    )
                ):
                    return candidate, (not was_incomplete)

        self._inc_incomplete_once()
        logger.warning("NLLB output is missing protected content. Check the translated block.")
        return self._retain_missing_protected_values(first, text, mapping, expected), False

    def _retain_missing_protected_values(self, translated, source, mapping, expected):
        """Keep exact non-sensitive values visible when both model attempts drop them.

        A third model guess would still be unreliable. Append the exact source
        values in an isolated parenthetical and leave the block marked
        incomplete so a reviewer can check their intended placement.
        """
        retained = []
        for value, expected_count in expected.items():
            missing_count = expected_count - self._protected_value_count(
                translated, value
            )
            if missing_count <= 0:
                continue
            compact_value = re.sub(r"\s+", "", value)
            if _is_valid_bic(value) or _IBAN_PATTERN.fullmatch(compact_value):
                continue

            tags = [tag for tag, mapped in mapping.items() if mapped == value]
            source_value = value
            for tag in tags:
                currency_match = re.search(
                    rf"([€£¥$])\s*{re.escape(tag)}", source
                )
                if currency_match:
                    source_value = f"{currency_match.group(1)} {value}"
                    break
            retained.extend([source_value] * missing_count)

        if not retained:
            return translated

        literal_text = "، ".join(f"\u200e{value}\u200e" for value in retained)
        stripped = translated.rstrip()
        punctuation = re.search(r"([.!?؟،؛:]+)$", stripped)
        if punctuation:
            body = stripped[:punctuation.start()].rstrip()
            return f"{body} ({literal_text}){punctuation.group(1)}"
        return f"{stripped} ({literal_text})"

    @staticmethod
    def _canonicalize_literal_values(text: str, values) -> str:
        """Canonicalize Arabic/spaced percentage and parenthetical forms back to source tokens."""
        if not text:
            return text
        for value in set(values):
            if value.endswith("%") and value[:-1].replace(".", "", 1).isdigit():
                num = re.escape(value[:-1])
                pct_pattern = re.compile(
                    rf"(?<![A-Za-z_\d])(?:{num}\s*(?:[%\u066A]|في\s+المائة|بالمائة|بالمئة)|[%\u066A]\s*{num})(?![A-Za-z_\d])"
                )
                text = pct_pattern.sub(value, text)
        return text

    @staticmethod
    def _protected_value_count(text, value):
        if value.endswith("%") and value[:-1].replace(".", "", 1).isdigit():
            num = re.escape(value[:-1])
            pct_pattern = re.compile(
                rf"(?<![A-Za-z_\d])(?:{num}\s*(?:[%\u066A]|في\s+المائة|بالمائة|بالمئة)|[%\u066A]\s*{num})(?![A-Za-z_\d])"
            )
            return len(pct_pattern.findall(text))
        # Allow surrounding Arabic prose, but distinguish IBANX from IBAN and 13 from 3.
        left = r"(?<![A-Za-z_\d])" if value[0].isalnum() else ""
        right = r"(?![A-Za-z_\d])" if value[-1].isalnum() else ""
        if value.isdecimal():
            # A bare number is also different from part of a decimal or thousands group.
            text = re.sub(r"\{\s*\d+\s*\}", "", text)
            left += r"(?<![+\-\u2212])(?<!\d[.,\u066B\u066C])"
            right += r"(?![.,\u066B\u066C]\d)"
        return len(re.findall(left + re.escape(value) + right, text))

    def _generate_translation(self, text):
        self._tokenizer.src_lang = self.src_nllb

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

        return self._translate_seq2seq_text(text, make_gen_kwargs)

    def do_llm_translate(self, text, rate_limit_params: dict = None):
        raise NotImplementedError("NLLB uses sequence-to-sequence translation, not LLM chat completions.")


