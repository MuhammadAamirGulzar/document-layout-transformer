"""Centralized detection, protection, restoration, and BiDi isolation for immutable structured tokens."""
from __future__ import annotations

import logging
import re
from collections import Counter

from babeldoc.format.pdf.document_il.utils.web_references import iter_web_references
from babeldoc.format.pdf.document_il.utils.web_references import restore_web_references
from babeldoc.format.pdf.document_il.utils.web_references import stash_web_references

logger = logging.getLogger(__name__)

_CITATION_SURNAME = r"[A-ZÀ-ÖØ-Þ][^\W\d_]*(?:[-'’][^\W\d_]+)*"
_CITATION_AUTHORS = rf"{_CITATION_SURNAME}(?:\s+et\s+al\.|\s+(?:and|&)\s+{_CITATION_SURNAME})?"
_CITATION_YEAR = r"(?:18|19|20)\d{2}[a-z]?"
_CITATION_ENTRY = rf"{_CITATION_AUTHORS},\s*{_CITATION_YEAR}"
AUTHOR_YEAR_CITATION = re.compile(
    rf"\({_CITATION_ENTRY}(?:;\s*{_CITATION_ENTRY})*\)"
    rf"|\b{_CITATION_SURNAME}\s+et\s+al\.\s*\({_CITATION_YEAR}\)",
)

_CHEM_SPECIES = r"(?:[0-9]+[ \t]*)?(?:[A-Z][a-z]?[0-9₀-₉]*)+(?:[⁰¹²³⁴⁵⁶⁷⁸⁹]*[⁺⁻])?"
_CHEM_SIDE = rf"{_CHEM_SPECIES}(?:[ \t]*\+[ \t]*{_CHEM_SPECIES})*"
CHEM_REACTION_RE = (
    rf"(?<!\w)(?=[A-Za-z0-9₀-₉⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻+→⇌⇄\-> \t]*[0-9₀-₉⁺⁻])"
    rf"{_CHEM_SIDE}[ \t]*(?:<->|->|[→⇌⇄])[ \t]*{_CHEM_SIDE}(?!\w)"
)
CHEM_ION_RE = (
    r"(?<!\w)(?:(?:[A-Z][a-z]?[0-9₀-₉]*)+[⁰¹²³⁴⁵⁶⁷⁸⁹]*[⁺⁻]"
    r"|[A-Z][a-z]?[0-9₀-₉]+[+-])(?=\W|$)"
)
ATOMIC_EXPRESSION_RE = re.compile(rf"(?:{CHEM_REACTION_RE}|{CHEM_ION_RE})")

IBAN_PATTERN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
SWIFT_BIC_PATTERN = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?$")
PHONE_PATTERN = re.compile(r"^\+?\d{1,4}(?:[\s\-]\d{2,4}){2,4}$")

ISO_COUNTRY_CODES = frozenset(
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
    "WF WS XK YE YT ZA ZM ZW ".split()
)

KNOWN_CODES = frozenset({
    "QR", "USD", "EUR", "GBP", "SAR", "AED",
    "QCBRR", "QCBLR", "QCBDR", "AOIR", "QMR",
    "CPI", "PPI", "GDP", "GNP", "IMF", "OPEC",
    "NDS", "NDS3", "FISIM", "RHS", "LHS",
    "Q1", "Q2", "Q3", "Q4",
    "H1", "H2",
    "YoY", "Y-o-Y", "QoQ", "Q-o-Q", "MoM",
    "bps", "Bps", "BPS",
    "BBAN", "IBAN", "BIC", "SWIFT", "STP", "TERMS", "QATCH",
    "QNBA", "CBQA", "DOHB", "QISB", "ABQQ", "QIIB", "ARAB", "MSHQ",
    "IBOQ", "BBME", "SCBL", "UNIL", "BNPA", "MAFR", "KLJI", "BRWA", "QIDB",
})

_BIC_CONTEXT = re.compile(r"\b(?:SWIFT|BIC|bank\s+identifier)\b", re.IGNORECASE)

_MEASUREMENT_UNITS = (
    r"mg/L|µg/L|ug/L|mmol/L|µg/mL|kg/ha|km/h|kWh|MWh|GWh|ppm|ppb|mmHg|"
    r"mL|mg|kg|km|cm|mm|Hz|kHz|MHz|GHz|°C|°F"
)
# A quantity's symbol/unit belongs to the same protected span as its value.
# Leaving either side outside a placeholder lets a model attach it to another number.
_QUANTITY_NUMBER = r"(?:\d{1,3}(?:\.\d{3})+,\d+|\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?|\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[KMBT]|[ \t]*(?:bn|mn))?"
_CURRENCY = r"(?:US\$|[$€£¥₹]|USD|EUR|GBP|SAR|AED|QAR|PKR|JPY|CHF|CAD|AUD)"
QUANTITY_PATTERN = (
    rf"(?<![\w.])[+\-−]?(?:{_CURRENCY}[ \t]*[+\-−]?[ \t]*{_QUANTITY_NUMBER}"
    rf"|{_QUANTITY_NUMBER}[ \t]*{_CURRENCY}"
    rf"|\d+(?:\.\d+)?[ \t]+(?:bn|mn)"
    rf"|{_QUANTITY_NUMBER}[ \t]+(?:bps|BPS|basis[ \t]+points?|(?:نقطة|نقاط)[ \t]+أساس))(?!\w|\.\d)"
)
_PAREN_QUANTITY_PATTERN = (
    rf"\([ \t]*[+\-−]?(?:{_CURRENCY}[ \t]*[+\-−]?[ \t]*{_QUANTITY_NUMBER}"
    rf"|{_QUANTITY_NUMBER}[ \t]*{_CURRENCY})[ \t]*\)"
)
_SLASH_CODE_PATTERN = (
    r"\b(?=[A-Za-z0-9./\-]*\d)[A-Z][A-Z0-9.]{0,8}(?:/[A-Za-z0-9.\-]+){1,}\b"
)
_STANDARD_CODE_PATTERN = (
    r"\b[A-Z][A-Za-z]{0,4}\.\d+(?:\.\d+)*(?:-[A-Z][A-Za-z]{0,4}\.\d+(?:\.\d+)*)?\b"
)
PERCENT_VALUE_RE = re.compile(
    r"^([+\-\u2212]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+\-]?\d+)?)\s*([%\u066A]|per[ \t]+cent|percent)$",
    re.IGNORECASE,
)
UNIT_VALUE_RE = re.compile(
    rf"^([+\-\u2212]?(?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d+)?)\s*({_MEASUREMENT_UNITS})$"
)
NUMERIC_VALUE_RE = re.compile(
    r"^[+\-\u2212]?(?:\d{1,3}(?:\.\d{3})+,\d+|\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?|\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)*)(?:[eE][+\-]?\d+)?[KMBT]?$"
)

STRUCTURED_TOKEN_PATTERN = re.compile(
    # Inline code / JSON objects (must not match placeholder {0})
    r'(`[^`\n]{1,140}`)|'
    r'(\{(?:"[^"\n]+"|\d+)\s*:\s*[^}\n]{1,160}\})|'
    # Web URLs and DOIs
    r'(https?://[^\s<>"\u201d\u2019]*[^\s<>"\u201d\u2019.,;:!?)\]]|www\.[^\s<>"\u201d\u2019]*[^\s<>"\u201d\u2019.,;:!?)\]])|'
    rf'({CHEM_REACTION_RE}|{CHEM_ION_RE})|'
    r'(\([A-Z]{2,10}\))|'
    r'(\b10\.\d{4,9}/[^\s<>"\u201d\u2019]*[^\s<>"\u201d\u2019.,;:!?)\]])|'
    r'([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})|'
    # Windows paths, Unix paths, filenames
    r'([A-Za-z]:\\(?:[^\s\\/:*?"<>|]+\\)*[^\s\\/:*?"<>|]*[^\s\\/:*?"<>|.,;:!?)\]])|'
    r'((?<![A-Za-z0-9_])(?:/|\.\.?/)[A-Za-z0-9._\-]+(?:/[A-Za-z0-9._\-]+)+/?)|'
    r'((?<![A-Za-z0-9_])[A-Za-z0-9][A-Za-z0-9._\-]*\.(?:pdf|xlsx?|csv|tsv|json|ya?ml|xml|html?|sql|py|sh|txt|md|docx?|pptx?|png|jpe?g|svg|zip|tar\.gz|log|ini|cfg|env)\b)|'
    # UUIDs, hashes, hex addresses, semantic versions
    r'(\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b)|'
    r'(\b(?:sha256:)?[0-9a-f]{16,64}\b|\b0x[0-9a-fA-F]{4,}\b)|'
    r'(\bv\d+(?:\.\d+){1,3}(?:-[A-Za-z0-9.\-]+)?\b)|'
    # ISO / calendar dates
    r'(\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?\b)|'
    r'(\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b)|'
    r'(\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b)|'
    # Prefixed document/invoice/PO references and multi-hyphen IDs
    r'(\b(?:INV|PO|REF|TXN|VAT|TIN|EIN|DOC|ID)\s*#\s*\d[A-Z0-9\-/]*\b)|'
    r'((?<![\w\-\u2010\u2011])[A-Z][A-Z0-9]{1,9}(?:[-\u2010\u2011][A-Z0-9]{1,9})+[-\u2010\u2011][0-9]{2,6}'
    r'(?:[.-][0-9]{1,6})*(?!\w|[-\u2010\u2011]\w|\.\d))|'
    # Statistical p-values and R² expressions
    r'(\b[pP]\s*(?:<=|>=|[<>≤≥=])\s*0?\.\d+\b|\bR[²2]\s*=\s*\d+(?:\.\d+)?\b)|'
    rf'({_SLASH_CODE_PATTERN})|'
    rf'({_STANDARD_CODE_PATTERN})|'
    r'(\[\s*\d{1,3}(?:\s*[,\u2013\u2014-]\s*\d{1,3})*\s*\])|'
    rf"({AUTHOR_YEAR_CITATION.pattern})|"
    # Parenthetical mixed tokens
    r'(\(\s*(?:(?:Chart|Table|Fig(?:ure)?)\s+)?[A-Z0-9\s,.\-+\u2212±×÷=/%$€£¥٪\u2013\u2014:;_]*\d[A-Z0-9\s,.\-+\u2212±×÷=/%$€£¥٪\u2013\u2014:;_]*\))|'
    # Arithmetic division/operations with equals
    r'(\b\d+\s*[/÷]\s*\d+\s*(?:=\s*\d+)?\b)|'
    r'(\b\d+\s*[-–—+*×]\s*\d+\s*=\s*\d+\b)|'
    r'(\b\d+\s*=\s*\d+\b)|'
    # Modulo and ISO standard references
    r'(\b(?:MOD\s*\d+(?:-\d+)?|ISO(?:/IEC)?\s*\d+(?:-\d+)?)\b)|'
    # Full IBAN and long account numbers
    r'(\b[A-Z]{2}\d{2}(?:\s+[A-Z0-9]{4}){2,7}(?:\s+[A-Z0-9]{1,4})?\b)|'
    r'(\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b)|'
    r'(\b(?=[A-Z0-9]{15,35}\b)(?=[A-Z0-9]*\d)[A-Z0-9]+\b)|'
    r'((?<![\w\-\u2010\u2011])[A-Z][A-Z0-9]{1,9}[-\u2010\u2011][0-9]{2,6}'
    r'(?:[.-][0-9]{1,6})*(?!\w|[-\u2010\u2011]\w|\.\d))|'
    # SWIFT BIC codes
    r'(\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b)|'
    # Bank Identifiers
    r'(\b(?:QNBA|CBQA|DOHB|QISB|ABQQ|QIIB|ARAB|MSHQ|IBOQ|BBME|SCBL|UNIL|BNPA|MAFR|KLJI|BRWA|QIDB)\b)|'
    # Country codes
    r'(\b(?:AL|AD|AT|BE|BA|BG|HR|CY|CZ|DK|EE|FO|FI|FR|GE|DE|GI|GR|GL|HU|IS|IE|IL|IT|JO|KW|LV|LB|LI|LT|LU|MK|MT|MR|MU|MD|MC|ME|NL|NO|PK|PL|PT|QA|RO|SM|SA|RS|SK|SI|ES|SE|CH|TN|TR|AE|GB)\b)|'
    # Mixed alphanumeric codes
    r'(\b(?=[A-Z0-9]{3,35}\b)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]+\b)|'
    # Scientific notation
    r'((?<![A-Za-z0-9_.])[+\-\u2212]?(?:\d+(?:\.\d+)?|\.\d+)[eE][+\-]?\d+(?:\s*[%\u066A])?(?!\w))|'
    # Ordinals, hyphen ranges, multi-dot decimals
    r'(\b\d+(?:st|nd|rd|th)\s*[-\u2013\u2014]\s*\d+(?:st|nd|rd|th)\b)|'
    r'(\b\d+(?:st|nd|rd|th)\b)|'
    r'(\b\d+[-\u2013\u2014]\d+\b)|'
    r'(\b\d+(?:\.\d+){2,3}\b)|'
    # Amounts and basis points before standalone codes/numbers.
    rf'({QUANTITY_PATTERN})|'
    # Financial/statistical acronyms
    r'(\b(?:QCB(?:RR|LR|DR)?|AOIR|QMR|NDS\d*|CPI|PPI|GDP|GNP|IMF|OPEC|FISIM|BBAN|IBAN|BIC|SWIFT|STP|TERMS|QATCH|Y-o-Y|Q-o-Q|MoM|RHS|LHS|USD|EUR|GBP|SAR|AED|QR|BPS|bps)\b)|'
    # International phone numbers (spaces or hyphens)
    r'(\+\d{1,4}(?:[\s\-]\d{2,4}){2,4})|'
    # Numbers with scientific/measurement units
    rf'((?<![A-Za-z0-9_.])[+\-\u2212]?(?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d+)?\s*(?:{_MEASUREMENT_UNITS})(?![A-Za-z0-9_]))|'
    # European (1.234,56), South Asian (1,25,000), magnitude (1.2M), and standard numbers / percentages
    r'((?<![A-Za-z0-9_.])[+\-\u2212]?(?:\d{1,3}(?:\.\d{3})+,\d+|\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?)(?![A-Za-z0-9_])(?!\.\d))|'
    r'((?<![A-Za-z0-9_.])[+\-\u2212]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?[KMBT](?![A-Za-z0-9_]))|'
    r'((?<![A-Za-z0-9_.])[+\-\u2212]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*(?:[%\u066A]|(?:[Pp]er[ \t]+[Cc]ent|[Pp]ercent)(?![A-Za-z0-9_])))?(?!\w)(?!\.\d))|'
    r'((?<!\w)\d+(?:\.\d+)?(?!\w|\.\d))'
)

PASSTHROUGH_ATOM_RE = re.compile(
    r"^(?:"
    r"https?://\S+|www\.\S+|10\.\d{4,9}/\S+|"
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}|"
    r"[A-Za-z]:\\(?:[^\\/:*?\"<>|\s]+\\)*[^\\/:*?\"<>|\s]+|"
    r"(?:/|\.\.?/)[A-Za-z0-9._\-]+(?:/[A-Za-z0-9._\-]+)+/?|"
    r"[A-Za-z0-9][A-Za-z0-9._\-]*\.(?:pdf|xlsx?|csv|tsv|json|ya?ml|xml|html?|sql|py|sh|txt|md|docx?|pptx?|png|jpe?g|svg|zip|tar\.gz|log|ini|cfg|env)|"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|"
    r"(?:sha256:)?[0-9a-f]{16,64}|0x[0-9a-fA-F]{4,}|"
    r"[A-Z][A-Z0-9]{1,9}(?:[-\u2010\u2011][A-Z0-9]{1,9})*[-\u2010\u2011][0-9]{2,6}(?:[.-][0-9]{1,6})*|"
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?|"
    r"[pP]\s*(?:<=|>=|[<>≤≥=])\s*0?\.\d+"
    r")$"
)

_BIDI_ISOLATE_TOKEN_RE = re.compile(
    r"[\u200e\u200f\u2067\u2069]*(?:"
    rf"{_PAREN_QUANTITY_PATTERN}|"
    rf"{QUANTITY_PATTERN}|"
    r"(?:https?://|www\.)[^\s<>\"\u201d\u2019\u200e\u200f]*[^\s<>\"\u201d\u2019.,;:!?)\]\u200e\u200f]"
    r"|(?<![A-Za-z0-9.])10\.\d{4,9}/[^\s<>\"\u201d\u2019\u200e\u200f]*[^\s<>\"\u201d\u2019.,;:!?)\]\u200e\u200f]"
    r"|[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
    r"|[A-Za-z]:\\(?:[^\s\\/:*?\"<>|\u200e\u200f]+\\)*[^\s\\/:*?\"<>|\u200e\u200f]*[^\s\\/:*?\"<>|.,;:!?)\]\u200e\u200f]"
    r"|(?<![A-Za-z0-9_\u200e\u200f])(?:/|\.\.?/)[A-Za-z0-9._\-]+(?:/[A-Za-z0-9._\-]+)+/?"
    r"|[A-Za-z0-9][A-Za-z0-9._\-]*\.(?:pdf|xlsx?|csv|tsv|json|ya?ml|xml|html?|sql|py|sh|txt|md|docx?|pptx?|png|jpe?g|svg|zip|tar\.gz|log|ini|cfg|env)\b"
    r"|\+\d{1,4}(?:[\s\-]\d{2,4}){2,4}"
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|(?:sha256:)?[0-9a-f]{16,64}|0x[0-9a-fA-F]{4,}"
    r"|[A-Z][A-Z0-9]{1,9}(?:[-\u2010\u2011][A-Z0-9]{1,9})+[-\u2010\u2011][0-9]{2,6}(?:[.-][0-9]{1,6})*"
    rf"|{_SLASH_CODE_PATTERN}"
    rf"|(?:ITU-T[ \t]+)?{_STANDARD_CODE_PATTERN}"
    r"|\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?\b"
    r"|\[\s*\d{1,3}(?:\s*[,\u2013\u2014-]\s*\d{1,3})*\s*\]"
    rf"|{CHEM_REACTION_RE}|{CHEM_ION_RE}"
    r")[\u200e\u200f\u2067\u2069]*"
)


def is_valid_bic(token: str) -> bool:
    """BIC-shaped token whose characters 5-6 are a real country code."""
    return bool(SWIFT_BIC_PATTERN.match(token)) and token[4:6] in ISO_COUNTRY_CODES


def is_plain_caps_word(token: str, source: str = "") -> bool:
    """All-letter 8/11 character caps word that only looks like a BIC."""
    return (
        token.isalpha()
        and bool(SWIFT_BIC_PATTERN.match(token))
        and (
            not is_valid_bic(token)
            or (
                len(token) == 11
                and not token.endswith("XXX")
                and token[:4] not in KNOWN_CODES
                and not _BIC_CONTEXT.search(source)
            )
        )
    )


def is_code_like(word: str) -> bool:
    """Known code, 11-character BIC, or 8-character BIC from a known bank ID."""
    return word in KNOWN_CODES or (
        is_valid_bic(word)
        and not is_plain_caps_word(word)
        and (len(word) == 11 or word[:4] in KNOWN_CODES)
    )


def caps_phrase_to_sentence_case(text: str) -> str:
    """Turn an ALL-CAPS phrase of ordinary words into sentence case."""
    if not text:
        return text
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        words = stripped.split()
        if (
            len(words) >= 2
            and all(re.fullmatch(r"[A-Z]{4,}", word) for word in words)
            and not any(is_code_like(word) for word in words)
        ):
            line = line.replace(stripped, stripped.capitalize(), 1)
        lines.append(line)
    return "\n".join(lines)


def canonicalize_literal_values(text: str, values) -> str:
    """Canonicalize Arabic/spaced percentage forms and dash-range spacing back to source tokens."""
    if not text:
        return text
    for value in set(values):
        pct_match = PERCENT_VALUE_RE.match(value)
        if pct_match:
            if pct_match.group(2) not in {"%", "\u066A"}:
                # Preserve localized Arabic percentages instead of rewriting back to English "per cent".
                continue
            num = re.escape(pct_match.group(1))
            left = r"(?<![A-Za-z0-9_.\u066B\u066C+\-\u2212])(?<!\d,)"
            right = r"(?![A-Za-z_\d])"
            pct_pattern = re.compile(
                rf"{left}(?:{num}\s*(?:[%\u066A]|في\s+المائة|بالمائة|بالمئة)|[%\u066A]\s*{num}(?![.,\u066B\u066C]\d)){right}"
            )
            text = pct_pattern.sub(lambda _m, orig=value: orig, text)
            continue
        range_match = re.fullmatch(r"(\d+)\s*([-\u2013\u2014])\s*(\d+)", value)
        if range_match:
            a, _sep, b = range_match.groups()
            left = r"(?<![A-Za-z0-9_.\u066B\u066C+\-\u2010\u2011\u2012\u2013\u2014\u2212])(?<!\d/)"
            right = r"(?![A-Za-z0-9_])(?![.,\u066B\u066C]\d)(?![-\u2010\u2011\u2012\u2013\u2014/]\d)"
            range_pattern = re.compile(
                rf"{left}{re.escape(a)}\s*[-\u2010\u2011\u2012\u2013\u2014]\s*{re.escape(b)}{right}"
            )
            text = range_pattern.sub(lambda _m, orig=value: orig, text)
            continue
        unit_match = UNIT_VALUE_RE.match(value)
        if unit_match:
            num, unit = unit_match.groups()
            left = r"(?<![A-Za-z0-9_.\u066B\u066C+\-\u2010\u2011\u2012\u2013\u2014\u2212])(?<!\d,)"
            right = r"(?![A-Za-z0-9_])"
            unit_pattern = re.compile(rf"{left}{re.escape(num)}\s*{re.escape(unit)}{right}")
            text = unit_pattern.sub(lambda _m, orig=value: orig, text)
    return text


def protected_value_count(text: str, value: str) -> int:
    """Boundary-aware count of protected literal occurrences in *text*."""
    if not text or not value:
        return 0
    if "{" not in value:
        text = re.sub(r"\{\s*\d+\s*\}", " ", text)
    pct_match = PERCENT_VALUE_RE.match(value)
    if pct_match:
        num = re.escape(pct_match.group(1))
        left = r"(?<![A-Za-z0-9_.\u066B\u066C+\-\u2212])(?<!\d,)"
        right = r"(?![A-Za-z_\d])"
        pct_pattern = re.compile(
            rf"{left}(?:{num}\s*(?:[%\u066A]|(?:في\s+ال|بال)ما?ئة|per[ \t]+cent|percent)|[%\u066A]\s*{num}(?![.,\u066B\u066C]\d)){right}",
            re.IGNORECASE,
        )
        return len(pct_pattern.findall(text))
    range_match = re.fullmatch(r"(\d+)\s*([-\u2013\u2014])\s*(\d+)", value)
    if range_match:
        a, _sep, b = range_match.groups()
        left = r"(?<![A-Za-z0-9_.\u066B\u066C+\-\u2010\u2011\u2012\u2013\u2014\u2212])(?<!\d/)"
        right = r"(?![A-Za-z0-9_])(?![.,\u066B\u066C]\d)(?![-\u2010\u2011\u2012\u2013\u2014/]\d)"
        range_pattern = re.compile(
            rf"{left}{re.escape(a)}\s*[-\u2010\u2011\u2012\u2013\u2014]\s*{re.escape(b)}{right}"
        )
        return len(range_pattern.findall(text))
    unit_match = UNIT_VALUE_RE.match(value)
    if unit_match:
        num, unit = unit_match.groups()
        left = r"(?<![A-Za-z0-9_.\u066B\u066C+\-\u2010\u2011\u2012\u2013\u2014\u2212])(?<!\d,)"
        right = r"(?![A-Za-z0-9_])"
        return len(re.findall(rf"{left}{re.escape(num)}\s*{re.escape(unit)}{right}", text))
    if NUMERIC_VALUE_RE.match(value):
        left = r"(?<![A-Za-z0-9_.\u066B\u066C+\-\u2010\u2011\u2012\u2013\u2014\u2212])(?<!\d,)(?<!\d/)(?<!\d:)"
        right = r"(?![A-Za-z0-9_])(?![.,\u066B\u066C]\d)(?![-\u2010\u2011\u2012\u2013\u2014/:]\d)(?!\s*[%\u066A])"
        return len(re.findall(left + re.escape(value) + right, text))
    # For parenthetical values like "(±0.5)", "(3 × 4 = 12)", or "(20 °C)",
    # allow optional spaces next to parens and math/unit operators without relaxing ISO dates.
    if value.startswith("(") and value.endswith(")") and len(value) >= 3:
        inner = re.sub(r"\s*([+\u2212±×÷=°])\s*", r"\1", value[1:-1].strip())
        parts: list[str] = []
        for tok in re.findall(r"\s+|[+\u2212±×÷=°]|[^\s+\u2212±×÷=°]+", inner):
            if tok.isspace():
                parts.append(r"\s+")
            elif tok in "+\u2212±×÷=°":
                parts.append(r"\s*" + re.escape(tok) + r"\s*")
            else:
                parts.append(re.escape(tok))
        return len(re.findall(r"\(\s*" + "".join(parts) + r"\s*\)", text))
    left = r"(?<![A-Za-z_\d])" if value[0].isalnum() else (r"(?<!\d)" if value[0] in "+-\u2212" else "")
    right = r"(?![A-Za-z_\d])" if value[-1].isalnum() else ""
    return len(re.findall(left + re.escape(value) + right, text))


def protect_structured_tokens(text: str) -> tuple[str, dict[str, str]]:
    """Replace immutable structured tokens with ``{0}``, ``{1}`` placeholders in reading order."""
    if not text:
        return text, {}

    mapping: dict[str, str] = {}
    counter = 0

    existing_placeholders = set(re.findall(r"\{\d+\}", text))
    existing_placeholders.update(
        f"{{{tag_id}}}"
        for tag_id in re.findall(r"<\s*/?\s*b\s*([0-9]+)\s*>", text, re.IGNORECASE)
    )

    spans: list[tuple[int, int, str]] = list(iter_web_references(text))
    occupied = list(spans)
    for match in STRUCTURED_TOKEN_PATTERN.finditer(text):
        s, e = match.start(), match.end()
        if any(not (e <= os or s >= oe) for os, oe, _ in occupied):
            continue
        spans.append((s, e, match.group(0)))

    spans.sort(key=lambda item: (item[0], -item[1]))

    out_parts: list[str] = []
    pos = 0
    for start, end, original in spans:
        if start < pos:
            continue
        out_parts.append(text[pos:start])
        skip = False
        if original in existing_placeholders:
            skip = True
        elif is_plain_caps_word(original, text):
            skip = True
        elif re.fullmatch(r"\([A-Z]{2,10}\)", original):
            acronym = original[1:-1]
            previous = re.findall(r"[A-Za-z]+", text[max(0, start - 140) : start])
            initials = "".join(w[0].upper() for w in previous if w.lower() not in {"and", "of", "the", "for", "in"})
            if acronym not in KNOWN_CODES and not initials.endswith(acronym):
                skip = True

        if skip:
            out_parts.append(original)
        else:
            placeholder = f"{{{counter}}}"
            while placeholder in existing_placeholders:
                counter += 1
                placeholder = f"{{{counter}}}"
            mapping[placeholder] = original
            counter += 1
            out_parts.append(placeholder)
        pos = end

    out_parts.append(text[pos:])
    return "".join(out_parts), mapping


def restore_structured_tokens(
    text: str,
    mapping: dict[str, str],
    *,
    strict: bool = True,
) -> str:
    """Restore structured-token placeholders deterministically.

    The translator uses ``strict=False`` while it is still able to retry or
    fall back safely. Direct callers retain strict byte-identity validation.
    """
    expected_mult = Counter(mapping.values()) if mapping else Counter()

    def _dedupe_parenthesized(value: str) -> str:
        """Collapse model-made adjacent repeats without erasing genuine source repeats."""

        def _collapse(match: re.Match[str]) -> str:
            token = match.group(1)
            copies = len(re.findall(re.escape(token), match.group(0)))
            keep = min(copies, max(1, expected_mult.get(token, 1)))
            return " ".join([token] * keep)

        return re.sub(r"(\([A-Za-z0-9\-_]{2,15}\))(?:\s*\1)+", _collapse, value)

    text = _dedupe_parenthesized(text)
    if not mapping:
        return text

    text = re.sub(r"(\(\s*\{\s*\d+\s*\}\s*\))(?:\s*\1)+", r"\1", text)
    text = re.sub(r"(\{\s*\d+\s*\})(?:\s*\1)+", r"\1", text)

    sorted_items = sorted(
        mapping.items(),
        key=lambda item: (int(item[0].strip("{}")) if item[0].strip("{}").isdigit() else 0, item[0]),
    )
    for placeholder, original in sorted_items:
        tag_num = placeholder.strip("{}")

        def repl(_match: re.Match[str], tag: str = tag_num) -> str:
            # Restored values are parked behind a private sentinel so later alias
            # patterns can never re-scan (and corrupt) an already restored value.
            return f"\uE010{tag}\uE011"

        text, canonical_hits = re.subn(r"\{\s*" + re.escape(tag_num) + r"\s*\}", repl, text)
        if canonical_hits:
            # The canonical tag survived, so looser aliases ([5], (5), <5>) are
            # genuine source text and must never be consumed as this tag.
            continue
        if tag_num == "0":
            text = re.sub(r"\{\s*[Oo]\s*\}", repl, text)
            text = re.sub(r"<\s*(?:b\s*)?[Oo]\s*>", repl, text)
            text = re.sub(r"\[\s*[Oo]\s*\]", repl, text)

        arabic_indic_num = "".join(chr(0x0660 + int(c)) if c.isdigit() else c for c in tag_num)
        if arabic_indic_num != tag_num:
            text = re.sub(r"\{\s*" + re.escape(arabic_indic_num) + r"\s*\}", repl, text)
            text = re.sub(r"<\s*(?:b\s*)?" + re.escape(arabic_indic_num) + r"\s*>", repl, text)

        # Aliases are applied to the first occurrence only: one tag, one value.
        text = re.sub(r"(?<!\d)\{" + re.escape(tag_num) + r"(?!\d)", repl, text, count=1)
        text = re.sub(r"(?<!\d)" + re.escape(tag_num) + r"\}(?!\d)", repl, text, count=1)
        text = re.sub(r"\[\s*" + re.escape(tag_num) + r"\s*\]", repl, text, count=1)
        text = re.sub(r"\(\s*" + re.escape(tag_num) + r"\s*\)", repl, text, count=1)
        text = re.sub(r"<\s*(?:b\s*)?" + re.escape(tag_num) + r"\s*>", repl, text, count=1)

    text = re.sub(
        r"\uE010(\d+)\uE011",
        lambda m: mapping.get("{" + m[1] + "}", ""),
        text,
    )
    text = _dedupe_parenthesized(text)

    for original in set(mapping.values()):
        if not (SWIFT_BIC_PATTERN.match(original) or IBAN_PATTERN.match(original)):
            continue
        keep_max = max(1, expected_mult.get(original, 1))

        def _collapse_identifier(match: re.Match[str], orig: str = original, cap: int = keep_max) -> str:
            copies = len(re.findall(re.escape(orig), match.group(0)))
            return " ".join([orig] * min(copies, cap))

        duplicate = re.compile(r"(" + re.escape(original) + r")(?:\s*\1)+")
        text = duplicate.sub(_collapse_identifier, text)

    text, urls = stash_web_references(text)
    text = re.sub(r"(\d)([\u0621-\u064A\u066E-\u06D3\u06D5])", r"\1 \2", text)
    text = re.sub(r"([\u0621-\u064A\u066E-\u06D3\u06D5])(\d)", r"\1 \2", text)
    text = re.sub(r"\s+([,.:;!?،؛])", r"\1", text)
    text = re.sub(r"[^\S\r\n]{2,}", " ", text).strip()
    text = restore_web_references(text, urls)

    expected_counts = Counter(mapping.values())
    for original_token, expected_count in expected_counts.items():
        actual_count = protected_value_count(text, original_token)
        if actual_count < expected_count:
            is_critical = bool(is_valid_bic(original_token) or IBAN_PATTERN.match(original_token))
            if strict and is_critical:
                raise AssertionError(
                    f"Byte-identity check failed! Expected at least {expected_count} occurrence(s) "
                    f"of critical banking code '{original_token}', but found {actual_count} in restored text:\n  '{text}'"
                )
            if strict:
                logger.warning(
                    f"Shielded token '{original_token}' expected {expected_count} occurrence(s), "
                    f"found {actual_count} in restored text. Preserving translated content."
                )
        elif strict and actual_count > expected_count and (
            is_valid_bic(original_token) or IBAN_PATTERN.match(original_token)
        ):
            raise AssertionError(
                f"Byte-identity check failed! Expected exactly {expected_count} occurrence(s) "
                f"of critical banking code '{original_token}', found {actual_count}:\n  '{text}'"
            )

    return text


def isolate_ltr_structured_spans(text: str) -> str:
    """Wrap LTR technical tokens and Arabic quantity+unit spans in BiDi isolates cleanly."""
    if not text or not re.search(r"[\u0600-\u06FF]", text):
        return text

    def wrap(match: re.Match[str]) -> str:
        token = match.group(0).strip("\u200e\u200f\u2067\u2069")
        if not token:
            return match.group(0)
        # Arabic unit labels (e.g. "25 نقطة أساس", "+25 نقطة أساس") keep their native
        # RTL direction and stay bound across BiDi runs and line wraps via RLI/PDI.
        # Explicit leading signs on numbers inside an RTL isolate need LRM bounds so
        # UBA does not flip "+25" into "25+".
        if re.search(r"[\u0621-\u064A]", token):
            signed_token = re.sub(
                r"^([+\-\u2212]\d[\d.,\u066B\u066C]*)(?=\s)",
                "\u200e\\1\u200e",
                token,
            )
            return f"\u2067{signed_token}\u2069"
        # Bound each LTR island with RLM+LRM...LRM+RLM so neutral separators
        # (such as '·') between consecutive LTR tokens cannot merge them into
        # one reversed run or detach a following number from its Arabic unit.
        return f"\u200f\u200e{token}\u200e\u200f"

    wrapped = _BIDI_ISOLATE_TOKEN_RE.sub(wrap, text)
    wrapped = re.sub(r"\u200e{2,}", "\u200e", wrapped)
    wrapped = re.sub(r"\u200f{2,}", "\u200f", wrapped)
    return wrapped


def is_immutable_technical_text(text: str | None) -> bool:
    """True when *text* holds only protected structured tokens, symbols and short codes.

    Such text (identifiers, numbers, units, URLs, formulas, short all-caps codes) is
    correct when it stays unchanged, so it must not be reported as a failed
    translation.  Any translatable word (letters, mixed case or a long
    all-caps word) makes the text translatable.
    """
    raw = (text or "").strip()
    if not raw:
        return True
    if PASSTHROUGH_ATOM_RE.match(raw):
        return True
    protected, _mapping = protect_structured_tokens(raw)
    residual = re.sub(r"\{\d+\}", " ", protected)
    for word in re.findall(r"[^\W\d_]{2,}", residual):
        if not word.isupper() or len(word) > 3:
            return False
    return True


_STRUCTURAL_SEP_EDGE_RE = re.compile(
    r"^(\s*[,.;:!?()\[\]\u2013\u2014\-/]*\s*)(.*?)(\s*[,.;:!?()\[\]\u2013\u2014\-/]*\s*)$",
    re.DOTALL,
)


def lex_translatable_and_protected_spans(text: str) -> list[tuple[str, str]]:
    """Lex *text* into ordered ``(kind, value)`` tokens where ``kind`` is
    ``'prose'``, ``'protected'``, or ``'separator'``.

    Concatenating ``value`` across all returned tuples reproduces *text* verbatim.
    """
    if not text:
        return []
    protected, mapping = protect_structured_tokens(text)
    if not mapping:
        return [("prose", text)]

    tag_re = re.compile(r"(\{\d+\})")
    raw_parts = tag_re.split(protected)
    tokens: list[tuple[str, str]] = []
    for part in raw_parts:
        if not part:
            continue
        if part in mapping:
            tokens.append(("protected", mapping[part]))
            continue
        m = _STRUCTURAL_SEP_EDGE_RE.match(part)
        if m:
            lead_sep, core, trail_sep = m.group(1), m.group(2), m.group(3)
            if lead_sep:
                tokens.append(("separator", lead_sep))
            if core:
                # Further split internal semicolon / colon / dash separators
                sub_parts = re.split(r"(\s*[;:\u2013\u2014]\s*)", core)
                for sp in sub_parts:
                    if not sp:
                        continue
                    if re.fullmatch(r"\s*[;:\u2013\u2014]\s*", sp):
                        tokens.append(("separator", sp))
                    elif is_immutable_technical_text(sp):
                        tokens.append(("separator", sp))
                    else:
                        tokens.append(("prose", sp))
            if trail_sep:
                tokens.append(("separator", trail_sep))
        else:
            tokens.append(("prose", part))
    return tokens
