import contextlib
import logging
import re
import threading
import time
import unicodedata
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
_NUMERAL_PATTERN = re.compile(
    # 1. Parenthetical mixed tokens: (2018=100), (QCBRR), (NDS3), (QR-mn), (281211210/97 =62)
    r'(\([^)]*\d[^)]*\))|'
    # 2. Arithmetic division / equations: 281211210/97 = 62, 502560126/97 = 22, 221000/97= 34
    r'(\b\d+\s*[/÷]\s*\d+\s*(?:=\s*\d+)?\b)|'
    # 3. Arithmetic operations with equals: 98-34=64, 2018=100
    r'(\b\d+\s*[-–—+*×]\s*\d+\s*=\s*\d+\b)|'
    r'(\b\d+\s*=\s*\d+\b)|'
    # 4. Modulo and international standard references: MOD 97-10, MOD 97, ISO/IEC 7064, ISO 13616-1
    r'(\b(?:MOD\s*\d+(?:-\d+)?|ISO(?:/IEC)?\s*\d+(?:-\d+)?)\b)|'
    # 5. Long alphanumeric account/IBAN strings (15-35 chars with at least one digit)
    r'(\b(?=[A-Z0-9]{15,35}\b)(?=[A-Z0-9]*\d)[A-Z0-9]+\b)|'
    # 6. SWIFT BIC codes embedded in text: ABQQQAQAXXX, BBMEQAQXXXX, SCBLQAQXXXX
    r'(\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b)|'
    # 7. Compound numeric references: 2-1, Chart 4-4, 7-3, 5.1.1, 2.1.2
    r'(\b\d+[-\u2013\u2014]\d+\b)|'
    r'(\b\d+(?:\.\d+){1,3}\b)|'
    # 8. Known financial/statistical acronyms that must never be translated
    r'(\b(?:QCB(?:RR|LR|DR)?|AOIR|QMR|NDS\d*|CPI|PPI|GDP|FISIM|BBAN|IBAN|BIC|SWIFT|Y-o-Y|Q-o-Q|RHS|LHS|USD|QR)\b)|'
    # 9. International phone and fax numbers: +974 44456371, +974 44430382
    r'(\+\d{1,4}\s*\d{3,4}\s*\d{3,4}(?:\s*\d{3,4})?)|'
    # 10. Numbers: integers, comma-separated thousands, decimals, percentages
    #    e.g. 75, 75.3, 75,432, 1,200,000.5, 8.6%, -3.2, +4.5
    r'([+-]?(?<!\w)\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s*[%\u066A])?(?!\w))|'
    # 11. Plain integers not already caught (catches standalone digits)
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
    
    def _replacer(match):
        original = match.group(0)
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
    """
    for placeholder, original in mapping.items():
        tag_num = placeholder.strip("{}")
        # Primary: standard {0} or with internal whitespace { 0 }
        text = re.sub(r'\{\s*' + re.escape(tag_num) + r'\s*\}', original, text)
        # Fallback: bracket partially dropped — {0 or 0}
        text = re.sub(r'(?<!\d)\{' + re.escape(tag_num) + r'(?!\d)', original, text)
        text = re.sub(r'(?<!\d)' + re.escape(tag_num) + r'\}(?!\d)', original, text)
        # Fallback: model converted to XML-style — <0>, < 0 >, <b0>, <b 0>
        text = re.sub(r'<\s*(?:b\s*)?' + re.escape(tag_num) + r'\s*>', original, text)

    # Post-restore spacing cleanup:
    # Insert space between digits and Arabic-script letters (NOT Latin letters,
    # to preserve codes like Q1, H2, P6, CHAPTER 03 from being split).
    text = re.sub(r'(\d)([\u0600-\u06FF])', r'\1 \2', text)
    text = re.sub(r'([\u0600-\u06FF])(\d)', r'\1 \2', text)
    # Remove stray spaces before punctuation
    text = re.sub(r'\s+([,.:;!?،؛])', r'\1', text)
    # Collapse any double spaces left by restoration
    text = re.sub(r'  +', ' ', text).strip()
    return text


# Pattern: text that is entirely made of digits, numeric punctuation, symbols,
# and/or known short financial codes — no translatable natural-language words.
# These cells should bypass the translation model entirely.
_PURE_PASSTHROUGH_PATTERN = re.compile(
    r'^[\s\d,.\-+±×÷=()/%$€£¥٪\u2013\u2014\u00B1\u2019\u2018:;\'\"]*$'
)

# Explicit SWIFT BIC regex: 4-letter bank code + 2-letter country + 2-char location + opt 3-char branch
_SWIFT_BIC_PATTERN = re.compile(r'^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?$')

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
    if _SWIFT_BIC_PATTERN.match(stripped):
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
        if _SWIFT_BIC_PATTERN.match(clean) or _IBAN_PATTERN.match(clean):
            continue
        # Is it a 2-4 letter uppercase code (e.g. ISO country codes AL, BA, GB; bank IDs QNBA, CBQA)?
        if re.fullmatch(r'[A-Z]{2,4}', clean):
            continue
        # Is it an alphanumeric code up to 12 chars (e.g. "BBMEQAQXXXX", "SCBLQAQXXXX", "Q1", "H2")?
        if re.fullmatch(r'[A-Z][A-Z0-9]{0,11}', clean):
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
        translation = self.do_translate(text, rate_limit_params)
        if not (self.ignore_cache or ignore_cache):
            self.cache.set(text, translation)
        return translation

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
        if _is_pure_passthrough(cleaned_text):
            return cleaned_text

        options = {}
        if self.send_temperature:
            options.update(self.options)

        response = self.client.chat.completions.create(
            model=self.model,
            **options,
            messages=self.prompt(text),
            extra_body=self.extra_body,
        )
        self.update_token_count(response)
        return response.choices[0].message.content.strip()

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


class MarianMTTranslator(BaseTranslator):
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
        import torch

        # Protect numerals from translation
        protected_text, numeral_map = _protect_numerals(cleaned_text)

        device = self._get_device()
        try:
            inputs = self._tokenizer(
                [protected_text],
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=self.max_length
            ).to(device)
            
            with torch.no_grad():
                translated_tokens = self._model.generate(
                    **inputs,
                    max_length=self.max_length,
                    no_repeat_ngram_size=4,
                    repetition_penalty=1.1,
                )
            
            translation = self._tokenizer.batch_decode(translated_tokens, skip_special_tokens=True)[0]
            # Restore original numerals
            translation = _restore_numerals(translation.strip(), numeral_map)
            return translation
        except Exception as e:
            logger.error(f"Error during MarianMT translation: {e}")
            return cleaned_text

    def do_llm_translate(self, text, rate_limit_params: dict = None):
        raise NotImplementedError("MarianMT uses sequence-to-sequence translation, not LLM chat completions.")


HuggingFaceTranslator = MarianMTTranslator


class NLLBTranslator(BaseTranslator):
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
    ):
        super().__init__(lang_in, lang_out, ignore_cache)

        norm_in = lang_in.split("-")[-1].lower() if "-" in lang_in and lang_in != "zh-CN" else lang_in.lower()
        norm_out = lang_out.split("-")[-1].lower() if "-" in lang_out and lang_out != "zh-CN" else lang_out.lower()

        self.src_nllb = self.NLLB_LANG_MAP.get(norm_in, "eng_Latn")
        self.tgt_nllb = self.NLLB_LANG_MAP.get(norm_out, "arb_Arab")

        if model_name in (None, "nllb", "nllb-600m", "nllb-distilled", "nllb-200-600M", "default", "facebook/nllb-200-distilled-600M"):
            model_name = "facebook/nllb-200-distilled-600M"
        elif model_name in ("nllb-1.3b", "nllb-200", "nllb-200-1.3B", "1.3b", "facebook/nllb-200-1.3B"):
            model_name = "facebook/nllb-200-1.3B"

        self.model_name = model_name
        self.model = model_name
        self.max_length = max_length
        self._device = device
        self._model = None
        self._tokenizer = None
        self._forced_bos_token_id = None
        self.add_cache_impact_parameters("model", self.model_name)

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
            logger.info(f"Loading Meta NLLB-200 model: {self.model_name} on device: {device}")
            
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                src_lang=self.src_nllb
            )
            
            # Use float16 on CUDA for fast inference and low VRAM footprint
            if device.startswith("cuda"):
                self._model = AutoModelForSeq2SeqLM.from_pretrained(
                    self.model_name,
                    torch_dtype=torch.float16
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
        import torch

        # Protect numerals from translation
        protected_text, numeral_map = _protect_numerals(cleaned_text)

        device = self._get_device()
        try:
            self._tokenizer.src_lang = self.src_nllb
            inputs = self._tokenizer(
                [protected_text],
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=self.max_length
            ).to(device)

            gen_kwargs = {
                "max_length": self.max_length,
                "num_beams": 4,
                "no_repeat_ngram_size": 4,
                "repetition_penalty": 1.1,
            }
            if self._forced_bos_token_id is not None:
                gen_kwargs["forced_bos_token_id"] = self._forced_bos_token_id

            with torch.no_grad():
                translated_tokens = self._model.generate(**inputs, **gen_kwargs)

            translation = self._tokenizer.batch_decode(translated_tokens, skip_special_tokens=True)[0]
            # Restore original numerals
            translation = _restore_numerals(translation.strip(), numeral_map)
            return translation
        except Exception as e:
            logger.error(f"Error during NLLB translation: {e}")
            return cleaned_text

    def do_llm_translate(self, text, rate_limit_params: dict = None):
        raise NotImplementedError("NLLB uses sequence-to-sequence translation, not LLM chat completions.")


