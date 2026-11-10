from __future__ import annotations

import copy
import json
import logging
import re
import threading
import unicodedata
from pathlib import Path

import tiktoken
from tqdm import tqdm

import babeldoc.format.pdf.document_il.il_version_1 as il_version_1
from babeldoc.babeldoc_exception.BabelDOCException import ContentFilterError
from babeldoc.format.pdf.document_il import Document
from babeldoc.format.pdf.document_il import GraphicState
from babeldoc.format.pdf.document_il import Page
from babeldoc.format.pdf.document_il import PdfFont
from babeldoc.format.pdf.document_il import PdfFormula
from babeldoc.format.pdf.document_il import PdfParagraph
from babeldoc.format.pdf.document_il import PdfParagraphComposition
from babeldoc.format.pdf.document_il import PdfSameStyleCharacters
from babeldoc.format.pdf.document_il import PdfSameStyleUnicodeCharacters
from babeldoc.format.pdf.document_il import PdfStyle
from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper
from babeldoc.format.pdf.document_il.utils.layout_helper import get_char_unicode_string
from babeldoc.format.pdf.document_il.utils.layout_helper import get_paragraph_unicode
from babeldoc.format.pdf.document_il.utils.layout_helper import is_bullet_point
from babeldoc.format.pdf.document_il.utils.layout_helper import is_same_style
from babeldoc.format.pdf.document_il.utils.layout_helper import (
    is_same_style_except_font,
)
from babeldoc.format.pdf.document_il.utils.layout_helper import (
    is_same_style_except_size,
)
from babeldoc.format.pdf.document_il.utils.paragraph_helper import (
    is_placeholder_only_paragraph,
)
from babeldoc.format.pdf.document_il.utils.paragraph_helper import (
    is_pure_numeric_paragraph,
)
from babeldoc.format.pdf.document_il.utils.style_helper import GRAY80
from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.translator.translator import BaseTranslator, _is_pure_passthrough
from babeldoc.utils.priority_thread_pool_executor import PriorityThreadPoolExecutor
from arabic_reshaper import reshape

logger = logging.getLogger(__name__)


class RichTextPlaceholder:
    def __init__(
        self,
        placeholder_id: int,
        composition: PdfSameStyleCharacters,
        left_placeholder: str,
        right_placeholder: str,
        left_regex_pattern: str = None,
        right_regex_pattern: str = None,
    ):
        self.id = placeholder_id
        self.composition = composition
        self.left_placeholder = left_placeholder
        self.right_placeholder = right_placeholder
        self.left_regex_pattern = left_regex_pattern
        self.right_regex_pattern = right_regex_pattern

    def to_dict(self) -> dict:
        return {
            "type": "rich_text",
            "id": self.id,
            "left_placeholder": self.left_placeholder,
            "right_placeholder": self.right_placeholder,
            "left_regex_pattern": self.left_regex_pattern,
            "right_regex_pattern": self.right_regex_pattern,
            "composition_chars": get_char_unicode_string(self.composition.pdf_character)
            if self.composition and self.composition.pdf_character
            else None,
        }


class FormulaPlaceholder:
    def __init__(
        self,
        placeholder_id: int,
        formula: PdfFormula,
        placeholder: str,
        regex_pattern: str,
    ):
        self.id = placeholder_id
        self.formula = formula
        self.placeholder = placeholder
        self.regex_pattern = regex_pattern

    def to_dict(self) -> dict:
        return {
            "type": "formula",
            "id": self.id,
            "placeholder": self.placeholder,
            "regex_pattern": self.regex_pattern,
            "formula_chars": get_char_unicode_string(self.formula.pdf_character)
            if self.formula and self.formula.pdf_character
            else None,
        }


class PbarContext:
    def __init__(self, pbar):
        self.pbar = pbar

    def __enter__(self):
        return self.pbar

    def __exit__(self, exc_type, exc_value, traceback):
        self.pbar.advance()


class DocumentTranslateTracker:
    def __init__(self):
        self.page = []
        self.cross_page = []
        # Track paragraphs that are combined due to cross-column detection within the same page
        self.cross_column = []

    def new_page(self):
        page = PageTranslateTracker()
        self.page.append(page)
        return page

    def new_cross_page(self):
        page = PageTranslateTracker()
        self.cross_page.append(page)
        return page

    def new_cross_column(self):
        """Create and return a new PageTranslateTracker dedicated to cross-column merging."""
        page = PageTranslateTracker()
        self.cross_column.append(page)
        return page

    def to_json(self):
        pages = []
        for page in self.page:
            paragraphs = self.convert_paragraph(page)
            pages.append({"paragraph": paragraphs})
        cross_page = []
        for page in self.cross_page:
            paragraphs = self.convert_paragraph(page)
            cross_page.append({"paragraph": paragraphs})
        cross_column = []
        for page in self.cross_column:
            paragraphs = self.convert_paragraph(page)
            cross_column.append({"paragraph": paragraphs})
        return json.dumps(
            {
                "cross_page": cross_page,
                "cross_column": cross_column,
                "page": pages,
            },
            ensure_ascii=False,
            indent=2,
        )

    def convert_paragraph(self, page):
        paragraphs = []
        for para in page.paragraph:
            i_str = getattr(para, "input", None)
            o_str = getattr(para, "output", None)
            pdf_unicode = getattr(para, "pdf_unicode", None)
            llm_translate_trackers = getattr(para, "llm_translate_trackers", None)
            placeholders = getattr(para, "placeholders", None)

            llm_translate_trackers_json = []
            if llm_translate_trackers:
                for tracker in llm_translate_trackers:
                    llm_translate_trackers_json.append(tracker.to_dict())

            placeholders_json = []
            if placeholders:
                for placeholder in placeholders:
                    placeholders_json.append(placeholder.to_dict())

            if pdf_unicode is None or i_str is None:
                continue
            paragraph_json = {
                "input": i_str,
                "output": o_str,
                "pdf_unicode": pdf_unicode,
                "llm_translate_trackers": llm_translate_trackers_json,
                "placeholders": placeholders_json,
                "multi_paragraph_id": getattr(para, "multi_paragraph_id", None),
                "multi_paragraph_index": getattr(para, "multi_paragraph_index", None),
            }
            paragraphs.append(
                paragraph_json,
            )
        return paragraphs


class PageTranslateTracker:
    def __init__(self):
        self.paragraph = []

    def new_paragraph(self):
        paragraph = ParagraphTranslateTracker()
        self.paragraph.append(paragraph)
        return paragraph


class ParagraphTranslateTracker:
    def __init__(self):
        self.llm_translate_trackers = []

    def set_pdf_unicode(self, unicode: str):
        self.pdf_unicode = unicode

    def set_input(self, input_text: str):
        self.input = input_text

    def set_placeholders(
        self, placeholders: list[RichTextPlaceholder | FormulaPlaceholder]
    ):
        self.placeholders = placeholders

    def record_multi_paragraph_id(self, mid):
        self.multi_paragraph_id = mid

    def record_multi_paragraph_index(self, index):
        self.multi_paragraph_index = index

    def set_output(self, output: str):
        self.output = output

    def new_llm_translate_tracker(self) -> LLMTranslateTracker:
        tracker = LLMTranslateTracker()
        self.llm_translate_trackers.append(tracker)
        return tracker

    def last_llm_translate_tracker(self) -> LLMTranslateTracker | None:
        if self.llm_translate_trackers:
            return self.llm_translate_trackers[-1]
        return None


class LLMTranslateTracker:
    def __init__(self):
        self.input = ""
        self.output = ""
        self.has_error = False
        self.error_message = ""
        self.placeholder_full_match = False
        self.fallback_to_translate = False

    def set_input(self, input_text: str):
        self.input = input_text

    def set_output(self, output_text: str):
        self.output = output_text

    def set_error_message(self, error_message: str):
        self.has_error = True
        self.error_message = error_message

    def set_placeholder_full_match(self):
        self.placeholder_full_match = True

    def set_fallback_to_translate(self):
        self.fallback_to_translate = True

    def to_dict(self):
        return {
            "input": self.input,
            "output": self.output,
            "has_error": self.has_error,
            "error_message": self.error_message,
            "placeholder_full_match": self.placeholder_full_match,
            "fallback_to_translate": self.fallback_to_translate,
        }


class ILTranslator:
    stage_name = "Translate Paragraphs"

    def __init__(
        self,
        translate_engine: BaseTranslator,
        translation_config: TranslationConfig,
        tokenizer=None,
    ):
        self.translate_engine = translate_engine
        self.translation_config = translation_config
        self.font_mapper = FontMapper(translation_config)
        self.shared_context_cross_split_part = (
            translation_config.shared_context_cross_split_part
        )
        if tokenizer is None:
            self.tokenizer = tiktoken.encoding_for_model("gpt-4o")
        else:
            self.tokenizer = tokenizer

        # Cache glossaries at initialization
        self._cached_glossaries = (
            self.shared_context_cross_split_part.get_glossaries_for_translation(
                self.translation_config.auto_extract_glossary
            )
        )

        self.support_llm_translate = False
        try:
            if translate_engine and hasattr(translate_engine, "do_llm_translate"):
                translate_engine.do_llm_translate(None)
                self.support_llm_translate = True
        except NotImplementedError:
            self.support_llm_translate = False

        self.use_as_fallback = False
        self.add_content_filter_hint_lock = threading.Lock()
        self.docs = None

    def shape_arabic_text(self, text: str) -> str:
        """Shape Arabic text for proper glyph joining (ligatures, contextual forms).

        IMPORTANT: This function only applies arabic_reshaper for character joining.
        It does NOT apply bidi get_display() reordering, because the PDF layout engine
        handles character positioning via explicit (x,y) coordinates. Applying get_display()
        was stripping U+0020 spaces and scrambling sentence order.

        Args:
            text: Input text to shape

        Returns:
            Shaped text if language is Arabic, original text otherwise
        """
        if not text:
            return text

        # Robust Arabic output detection
        lang_out = (self.translation_config.lang_out or "").lower()
        is_arabic = False
        if lang_out in ("en-ar", "ar", "ara", "arabic"):
            is_arabic = True
        elif "-ar" in lang_out or "->ar" in lang_out or "/ar" in lang_out:
            is_arabic = True

        if is_arabic:
            logger.debug("Shaping Arabic text (reshape only, no bidi reorder)")
            try:
                # Only reshape if text is NOT already in presentation forms
                if not re.search(r'[\uFB50-\uFDFF\uFE70-\uFEFF]', text):
                    from arabic_reshaper import ArabicReshaper
                    configuration = {
                        'delete_harakat': False,
                        'support_ligatures': True,
                        'RIAL SIGN': True,
                        'ARABIC COMMA': True,
                        'ARABIC SEMICOLON': True,
                        'ARABIC QUESTION MARK': True,
                        'ZWNJ': True,
                    }
                    reshaper = ArabicReshaper(configuration=configuration)
                    # reshape() handles glyph joining while preserving spaces
                    reshaped_text = reshaper.reshape(text)
                    # DO NOT call get_display() — the PDF layout engine
                    # handles RTL positioning via coordinates
                    return reshaped_text
                else:
                    # Already in presentation forms, return as-is
                    return text
            except Exception as e:
                logger.warning(f"Failed to shape Arabic text: {e}")
                return text

        return text

    def _pre_translate_terminology(self, text: str) -> str:
        """Lock and standardize technical financial terms before sending to translation model."""
        if not text:
            return text

        # In this cover title, DOCUMENT is a word that resembles a BIC.
        if text.strip() == "STANDARDS DOCUMENT":
            return "Standards document"

        # Financial rates and central banking terms
        text = re.sub(r'\b(?:The\s+)?QMR\s+Lending\s+Rate\b', 'سعر فائدة الإقراض في سوق قطر النقدي', text, flags=re.I)
        text = re.sub(r'\b(?:The\s+)?Repo\s+Rate\b', 'سعر اتفاقيات إعادة الشراء (الريبو)', text, flags=re.I)
        text = re.sub(r'\b(\d+(?:[,.]\d+)?)\s*(?:basis\s+points|bps)\b', r'\1 نقطة أساس', text, flags=re.I)
        text = re.sub(r'\b(?:basis\s+points|bps)\b', 'نقطة أساس', text, flags=re.I)
        text = re.sub(r'\b(?:barrels?|bbl)\b', 'برميل', text, flags=re.I)

        # Strategy and institutional abbreviations
        text = re.sub(r'\b(?:the\s+)?Third\s+National\s+Development\s+Strategy\s*\(\s*NDS\s*3?\s*\)', 'استراتيجية التنمية الوطنية الثالثة (NDS3)', text, flags=re.I)
        text = re.sub(r'\bNDS\s*3\b', 'NDS3', text, flags=re.I)

        # Currency with billion: QR 89.0 billion -> 89.0 مليار ريال قطري
        text = re.sub(r'\bQR\s*(\d+(?:[,.]\d+)?)\s*billions?\b', r'\1 مليار ريال قطري', text, flags=re.I)
        text = re.sub(r'\b(\d+(?:[,.]\d+)?)\s*billions?\s*QR\b', r'\1 مليار ريال قطري', text, flags=re.I)
        text = re.sub(r'\bQR\s*billions?\b', 'مليار ريال قطري', text, flags=re.I)
        text = re.sub(r'\bbillions?\s*QR\b', 'مليار ريال قطري', text, flags=re.I)

        # Currency with million: QR 250,661 million -> 250,661 مليون ريال قطري
        text = re.sub(r'\bQR\s*(\d+(?:[,.]\d+)?)\s*(?:mn|millions?)\b', r'\1 مليون ريال قطري', text, flags=re.I)
        text = re.sub(r'\b(\d+(?:[,.]\d+)?)\s*(?:mn|millions?)\s*QR\b', r'\1 مليون ريال قطري', text, flags=re.I)
        text = re.sub(r'\bQR\s*[-–—]?\s*(?:mn|millions?)\b', 'مليون ريال قطري', text, flags=re.I)
        text = re.sub(r'\b(?:mn|millions?)\s*QR\b', 'مليون ريال قطري', text, flags=re.I)

        # Currency standard: QR 50 -> 50 ريال قطري
        text = re.sub(r'\bQR\s*(\d+(?:[,.]\d+)?)', r'\1 ريال قطري', text)
        text = re.sub(r'(\d+(?:[,.]\d+)?)\s*QR\b', r'\1 ريال قطري', text)

        # Quarter references
        text = re.sub(r'\bQ1[- ](\d{4})\b', r'الربع الأول من \1', text)
        text = re.sub(r'\bQ2[- ](\d{4})\b', r'الربع الثاني من \1', text)
        text = re.sub(r'\bQ3[- ](\d{4})\b', r'الربع الثالث من \1', text)
        text = re.sub(r'\bQ4[- ](\d{4})\b', r'الربع الرابع من \1', text)

        # Contact and communication terminology: Tel, Fax, E-Mail
        text = re.sub(r'\bTel\b(?:\s*[:：])?', 'الهاتف:', text, flags=re.I)
        text = re.sub(r'\bFax\b(?:\s*[:：])?', 'الفاكس:', text, flags=re.I)
        text = re.sub(r'\bE-?Mail\b(?:\s*[:：])?', 'البريد الإلكتروني:', text, flags=re.I)

        # Qatar Banking Institutions standardization (prevents fragmentation, stray commas, and phonetic mistranslations)
        text = re.sub(r'\bINTERNATIONAL\s+BANK\s+OF\s+QATAR\b', 'بنك قطر الدولي', text, flags=re.I)
        text = re.sub(r'\bQATAR\s+NATIONAL\s+BANK\b', 'بنك قطر الوطني', text, flags=re.I)
        text = re.sub(r'\bCOMMERCIAL\s+BANK\s+OF\s+QATAR\b', 'البنك التجاري القطري', text, flags=re.I)
        text = re.sub(r'\bDOHA\s+BANK\b', 'بنك الدوحة', text, flags=re.I)
        text = re.sub(r'\bQATAR\s+ISLAMIC\s+BANK\b', 'مصرف قطر الإسلامي', text, flags=re.I)
        text = re.sub(r'\bAHLI\s+BANK\s+OF\s+QATAR\b', 'البنك الأهلي القطري', text, flags=re.I)
        text = re.sub(r'\bQATAR\s+INTL\s+ISLAMIC\s+BANK\b', 'بنك قطر الدولي الإسلامي', text, flags=re.I)
        text = re.sub(r'\bARAB\s+BANK\b', 'البنك العربي', text, flags=re.I)
        text = re.sub(r'\bMASHREQ\s+BANK\b', 'بنك المشرق', text, flags=re.I)
        text = re.sub(r'\bSTANDARD\s+CHARTERED\s+BANK\b', 'بنك ستاندرد تشارترد', text, flags=re.I)
        text = re.sub(r'\bUNITED\s+BANK\s+LTD\b', 'بنك يونايتد المحدود', text, flags=re.I)
        text = re.sub(r'\bBNP\s+PARIBAS\b', 'بي إن بي باريبا', text, flags=re.I)
        text = re.sub(r'\bRAYYAN\s+BANK\b', 'مصرف الريان', text, flags=re.I)
        text = re.sub(r'\bAL\s+KHALIJ\s+COMMERCIAL\s+BANK\b', 'بنك الخليج التجاري', text, flags=re.I)
        text = re.sub(r'\bBANK\s+BARWA\b', 'بنك بروة', text, flags=re.I)
        text = re.sub(r'\bQATAR\s+DEVELOPMENT\s+BANK\b', 'بنك قطر للتنمية', text, flags=re.I)

        return text

    @staticmethod
    def _leading_bullet_tag(text: str, translate_input, layout_label: str) -> str:
        """Identify a leading layout tag backed by a source bullet glyph."""
        if not text.startswith("<") or not translate_input.placeholders:
            return ""
        marker = translate_input.placeholders[0]
        if not isinstance(marker, FormulaPlaceholder) or not text.startswith(marker.placeholder):
            return ""
        glyphs = [
            char for char in marker.formula.pdf_character
            if char.char_unicode and char.char_unicode.strip()
        ]
        if len(glyphs) != 1:
            return ""
        glyph = glyphs[0]
        if is_bullet_point(glyph) or (
            layout_label == "list_item" and re.fullmatch(r"\(cid:\d+\)", glyph.char_unicode)
        ):
            return marker.placeholder
        return ""

    def _split_trailing_index_reference(self, text, source_text, translate_input):
        """Keep a trailing reference like M[X, a] intact through translation."""
        source_match = re.search(
            r"(?<=\S)[ \t]+(?P<reference>(?P<name>[A-Za-z_]\w*)"
            r"\[(?P<indices>[A-Za-z0-9_]+,[ \t]*[A-Za-z0-9_]+)\])[ \t]*\Z",
            source_text or "",
            flags=re.ASCII,
        )
        if source_match is None:
            return text, "", translate_input

        name = re.escape(source_match["name"])
        indices = re.escape(source_match["indices"])
        match = re.search(
            rf"(?<=\S)[ \t]+{name}(\[|<b\d+>){indices}(\]|<b\d+>)[ \t]*\Z",
            text,
        )
        if match is None:
            return text, "", translate_input

        # Extraction can represent the two brackets as separate formula tags.
        opening, closing = match.groups()
        brackets = {
            item.placeholder: "".join(
                char.char_unicode or "" for char in item.formula.pdf_character
            )
            for item in translate_input.placeholders
            if isinstance(item, FormulaPlaceholder) and item.placeholder in (opening, closing)
        }
        if brackets.get(opening, opening) != "[" or brackets.get(closing, closing) != "]":
            return text, "", translate_input
        if brackets:
            translate_input = self.TranslateInput(
                translate_input.unicode,
                [
                    item for item in translate_input.placeholders
                    if not isinstance(item, FormulaPlaceholder) or item.placeholder not in brackets
                ],
                translate_input.base_style,
            )
        return text[:match.start()], source_match["reference"], translate_input

    def _translate_with_line_preservation(
        self, text: str, rate_limit_params: dict, force_preserve_lines: bool = False
    ) -> str:
        """Translate text while preserving semantic source line structure."""
        if not text:
            return text

        # Pre-translation terminology replacement
        text = self._pre_translate_terminology(text)

        def translate_segment(segment: str) -> str:
            # List and section numbering is document structure.
            marker = re.match(r"^[ \t]*\d{1,3}(?:\.\d{1,3})*\.(?:[ \t]+|$)", segment)
            if marker:
                label = segment[marker.end():]
                if not label:
                    return segment
                translated = self.translate_engine.translate(label, rate_limit_params=rate_limit_params)
                return marker.group() + translated
            return self.translate_engine.translate(segment, rate_limit_params=rate_limit_params)

        # Check if text contains Table of Contents (TOC) entries with dotted leader lines
        # e.g. "1. Introduction ................................................................................................. 1306"
        toc_leader_pattern = re.compile(r'^(.*?)\s*(\.{4,})\s*(\d+)\s*$')

        # If text has multiple non-empty lines
        lines = text.split("\n")
        non_empty = [l for l in lines if l.strip()]

        # Only preserve lines for short labels/legends/table cells or TOC entries
        is_toc_block = any(toc_leader_pattern.match(l.strip()) for l in non_empty)
        is_short_labels = (
            force_preserve_lines
            or (len(non_empty) > 1 and max(len(l.split()) for l in non_empty) <= 6)
            or is_toc_block
        )

        if is_short_labels:
            translated_lines = []
            for line in lines:
                stripped_line = line.strip()
                toc_match = toc_leader_pattern.match(stripped_line) if stripped_line else None
                if toc_match:
                    title, dots, page_num = toc_match.group(1).strip(), toc_match.group(2), toc_match.group(3)
                    tr_title = translate_segment(title) if title else ""
                    translated_lines.append(f"{tr_title} {dots} {page_num}")
                elif stripped_line:
                    tr = translate_segment(line)
                    translated_lines.append(tr)
                else:
                    translated_lines.append(line)
            return "\n".join(translated_lines)
        else:
            # Check if single-line paragraph is a TOC entry
            if len(non_empty) == 1:
                toc_match = toc_leader_pattern.match(non_empty[0])
                if toc_match:
                    title, dots, page_num = toc_match.group(1).strip(), toc_match.group(2), toc_match.group(3)
                    tr_title = translate_segment(title) if title else ""
                    return f"{tr_title} {dots} {page_num}"

            # For flowing body text paragraphs, join soft linebreaks with a space so
            # the translation model receives complete, grammatically coherent sentences
            if len(non_empty) > 1:
                normalized_text = " ".join(line.strip() for line in lines if line.strip())
            else:
                normalized_text = text

            # Multi-sentence paragraph translation:
            # When a flowing paragraph contains multiple sentences (e.g. > 150 chars),
            # translate sentence-by-sentence to prevent Seq2Seq models (NLLB/Marian)
            # from truncating sentences at the tail of long paragraphs.
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', normalized_text) if s.strip()]
            if len(sentences) > 1 and len(normalized_text) > 150:
                translated_sentences = [
                    translate_segment(s)
                    for s in sentences
                ]
                return " ".join(translated_sentences)

            return translate_segment(normalized_text)

    @staticmethod
    def _structured_source_lines(paragraph: PdfParagraph) -> str | None:
        """Recover visual source lines for structured one-paragraph blocks.

        ``get_paragraph_unicode`` intentionally flattens PDF line boundaries.
        That is correct for prose, but destructive for contact cards where each
        visual row is an independent field.  Rebuild only from real PdfLine
        compositions and leave mixed formula/rich-text paragraphs untouched.
        """
        lines: list[tuple[float, float, str]] = []
        for composition in paragraph.pdf_paragraph_composition or []:
            line = composition.pdf_line
            if not line or not line.box or not line.pdf_character:
                continue
            value = get_char_unicode_string(line.pdf_character).strip()
            if value:
                lines.append((line.box.y2, line.box.x, value))
        if len(lines) < 2:
            return None
        lines.sort(key=lambda item: (-item[0], item[1]))
        return "\n".join(value for _, _, value in lines)

    def _normalize_arabic_output(self, text: str) -> str:
        """Post-translation normalization for Arabic text.

        Fixes:
        - Inconsistent percent signs (% vs ٪)
        - Stray periods around percent signs (e.g. '5.7 .٪' -> '5.7 ٪.')
        - Compound tokens like Q4-2022
        - Phonetic table header translations (يـو يـو -> Y-o-Y)
        - Petroleum economic terminology (الزيت -> النفط for sector labels)
        - Currency unit standardization (QR / كرو -> ريال قطري)
        - Financial mistranslations cleanup (bps, bbl, repo rate)
        - Chart and chapter reference cleanup (الشريط -> المخطط)
        - Atomic parenthetical acronym protection with LRM
        - Latin acronyms/numbers spacing around Arabic text
        """
        if not text:
            return text

        lang_out = (self.translation_config.lang_out or "").lower()
        is_arabic = any(marker in lang_out for marker in ["ar", "arabic", "ara"])
        if not is_arabic:
            return text

        # Extraction can isolate the quantifier from the rest of its sentence
        # as a one-word paragraph.  In Arabic output this is unambiguously the
        # quantifier "all", never a financial identifier.
        if text.strip().lower() == "all":
            return "جميع"

        # Some seq2seq decoders occasionally emit C0 control tokens around
        # protected ordinal / acronym placeholders. Unlike source controls,
        # these are not layout spaces: PDF writers render them as .notdef
        # boxes. Remove them before rich-text parsing and font selection.
        # Newlines encode records in procedures and contact cards.  They are
        # deliberately retained as layout metadata; all other control bytes
        # are decoder noise and would render as .notdef glyphs.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = "".join(
            char for char in text
            if char in {"\n", "\t"} or unicodedata.category(char) != "Cc"
        )

        # 1. Normalize percent signs: replace Western % with Arabic ٪
        text = text.replace('%', '\u066A')  # U+066A = Arabic percent sign ٪

        # 2. Fix stray periods around percent signs (e.g. "8.6 .٪" -> "8.6٪.", ".٪ 8.6" -> "8.6٪.")
        text = re.sub(r'\.\s*(\u066A)\s*(\d+(?:[,.]\d+)?)', r'\2\1.', text)
        text = re.sub(r'(\d+(?:[,.]\d+)?)\s*\.\s*(\u066A)', r'\1\2.', text)
        text = re.sub(r'(\d+(?:[,.]\d+)?)\s*(\u066A)\s*\.', r'\1\2.', text)
        text = re.sub(r'(\d+(?:[,.]\d+)?)\s+(\u066A)', r'\1\2', text)

        # 3. Compound alphanumeric tokens: preserve Q4-2022 as an atomic token
        text = re.sub(r'\bQ\s*([1-4])\s*[-–—]\s*(\d{4})\b', r'Q\1-\2', text)

        # 4. Table header YoY phonetic translation fix (يـو يـو / يـا هـو -> Y-o-Y)
        text = re.sub(r'(?:ﻳـﻮ|يـو|يو)\s*[-_–—ـ]*\s*(?:ﻳـﻮ|يـو|يو)', 'Y-o-Y', text)
        text = re.sub(r'(?:ﻳـﺎ|يـا|يا)\s*[-_–—ـ]*\s*(?:ﻫـﻮ|هـو|هو)', 'Y-o-Y', text)

        # 5. Petroleum terminology: standardize oil/non-oil sector labels
        text = re.sub(r'\bغير\s+الزيت\b', 'غير النفط', text)
        text = re.sub(r'\bالزيت\s+غير\b', 'غير النفط', text)
        text = re.sub(r'\bقطاع\s+الزيت\b', 'قطاع النفط', text)
        text = re.sub(r'\bالقطاع\s+الزيتي\b', 'قطاع النفط', text)
        if text.strip().lower() == 'oil':
            text = 'النفط'

        # 6. Currency standardization: eliminate كرو / sporadic QR
        text = re.sub(r'\bمليارات\s+(?:الكرو|كرو)\b', 'مليارات الريالات القطرية', text)
        text = re.sub(r'\b(?:الكرو|كرو)\s+مليون\b', 'مليون ريال قطري', text)
        text = re.sub(r'\bمليون\s+(?:الكرو|كرو)\b', 'مليون ريال قطري', text)
        text = re.sub(r'\b(?:الكرو|كرو)\b', 'ريال قطري', text)
        text = re.sub(r'\bمليارات\s+QR\b', 'مليار ريال قطري', text)
        text = re.sub(r'\bQR\s*(\d+(?:[,.]\d+)?)', r'\1 ريال قطري', text)
        text = re.sub(r'(\d+(?:[,.]\d+)?)\s*QR\b', r'\1 ريال قطري', text)
        text = re.sub(r'\bمليارات\s+الدولارات\b', 'مليارات الريالات القطرية', text)

        # 7. Financial unit mistranslations cleanup
        text = re.sub(r'\bبيليومتر\b', 'برميل', text)
        text = re.sub(r'\bنقاط\s+(?:أوروبية|أرضية|أوراق)\b', 'نقطة أساس', text)
        text = re.sub(r'\bإعادة\s+التأمين\b', 'سعر الريبو', text)

        # 8. Structural reference preservation (Chart / Chapter / Table)
        text = re.sub(r'\b(?:الشريط|شريط)\s+(\d+[-–—]\d+)', r'المخطط \1', text)
        text = re.sub(r'\bCHAPTER\s+(\d+)', r'الفصل \1', text, flags=re.I)
        text = re.sub(r'\bTable\s+(\d+[-–—]\d+)', r'الجدول \1', text, flags=re.I)
        text = re.sub(r'\bChart\s+(\d+[-–—]\d+)', r'المخطط \1', text, flags=re.I)

        # 9. Wrap Latin parenthetical acronyms and mixed English expressions with Left-to-Right Mark (\u200E) to preserve BiDi order
        def _wrap_lrm(m):
            matched = m.group(0)
            if any(c.isalpha() and ord(c) < 0x0600 for c in matched):
                return f"\u200E{matched}\u200E"
            return matched

        text = re.sub(r'\([A-Za-z0-9\s\-–—/._+:=#\$\€\£\¥\%\*]+\)', _wrap_lrm, text)

        # 10. Insert spaces at Arabic letters ↔ Latin/digit script boundaries (excluding Arabic percent sign \u066A)
        ar_letters = r'[\u0621-\u064A\u066E-\u06D3\u06D5\uFB50-\uFDFF\uFE70-\uFEFF]'
        # NLLB occasionally leaves a standalone English quantifier at the
        # beginning of an otherwise Arabic sentence (for example, "All البنوك").
        # It is neither a protected financial identifier nor useful LTR
        # content, and it destabilizes the line BiDi run. Normalize only this
        # Arabic-context form; do not touch genuine English-only content.
        text = re.sub(
            r'\bAll(?=(?:[\s\u200e\u200f])+' + ar_letters + r')',
            'جميع', text, flags=re.IGNORECASE,
        )
        # Isolate banking formulas and code chains as LTR runs inside Arabic.
        # This protects the visual order of "MOD 97", division signs and
        # equals signs without translating or reordering their operands.
        ltr_formula = re.compile(
            r'(?<![A-Za-z0-9])(?:[A-Z]{2}\d{2}[A-Z0-9]{11,30}|[A-Z]{2}\d{2}|'
            r'[A-Z0-9]{12,35}|(?:\d+\s*[/÷+*×]\s*\d+|\d{2,}\s*-\s*\d+)(?:\s*=\s*\d+)?|'
            r'MOD\s*\d+(?:-\d+)?)(?![A-Za-z0-9])'
        )
        text = ltr_formula.sub(lambda match: f"\u200E{match.group(0)}\u200E", text)
        text = re.sub(rf'({ar_letters})([A-Za-z0-9])', r'\1 \2', text)
        text = re.sub(rf'([A-Za-z0-9])({ar_letters})', r'\1 \2', text)

        # 11. Punctuation BiDi Locking: Ensure trailing neutral punctuation (. , : ; ! ؟)
        # following digits, Arabic percent sign (\u066A), or Latin letters/parentheses
        # stays pinned to the left end of the Arabic sentence by appending RLM (\u200F)
        text = re.sub(r'([\d\u066A%A-Za-z\)\u200E])\s*([.:;!؟])\s*$', lambda m: f"{m.group(1)}{m.group(2)}\u200F", text)

        # 12. Clean up telephone mistranslation (التلفزيون -> الهاتف) in contact context
        text = re.sub(r'\bالتلفزيون\s*:\s*(\+?\d+)', r'الهاتف: \1', text)
        text = re.sub(r'^\s*التلفزيون\s*:\s*$', 'الهاتف:', text, flags=re.M)
        if text.strip() in ['التلفزيون :', 'التلفزيون:']:
            text = 'الهاتف:'

        # 13. Clean up stray Western or Arabic commas inside bank and institutional names
        text = re.sub(r'(\b(?:بنك|مصرف|الدولي|القطري|الوطني)[\u0600-\u06FF\s]*?)[,،](\s*[\u0600-\u06FF])', r'\1\2', text)
        text = re.sub(r'(\b[\u0600-\u06FF]+)\s*,\s*([\u0600-\u06FF]+\b)', r'\1 \2', text)

        # 14. Collapse horizontal whitespace only.  Do not flatten line
        # records that were intentionally preserved above.
        text = re.sub(r'[^\S\r\n]{2,}', ' ', text).strip()

        return text

    def calc_token_count(self, text: str) -> int:
        if text is None:  # ADD THIS LINE
            return 0       # ADD THIS LINE
        try:
            return len(self.tokenizer.encode(text, disallowed_special=()))
        except Exception:
            return 0

    def translate(self, docs: Document):
        self.docs = docs
        tracker = DocumentTranslateTracker()

        if not self.translation_config.shared_context_cross_split_part.first_paragraph:
            # Try to find the first title paragraph
            title_paragraph = self.find_title_paragraph(docs)
            self.translation_config.shared_context_cross_split_part.first_paragraph = (
                copy.deepcopy(title_paragraph)
            )
            self.translation_config.shared_context_cross_split_part.recent_title_paragraph = copy.deepcopy(
                title_paragraph
            )
            if title_paragraph:
                logger.info(f"Found first title paragraph: {title_paragraph.unicode}")

        # count total paragraph
        total = sum(len(page.pdf_paragraph) for page in docs.page)
        with self.translation_config.progress_monitor.stage_start(
            self.stage_name,
            total,
        ) as pbar:
            with PriorityThreadPoolExecutor(
                max_workers=self.translation_config.pool_max_workers,
            ) as executor:
                for page in docs.page:
                    self.process_page(page, executor, pbar, tracker.new_page())

        path = self.translation_config.get_working_file_path("translate_tracking.json")

        if self.translation_config.debug:
            logger.debug(f"save translate tracking to {path}")
            with Path(path).open("w", encoding="utf-8") as f:
                f.write(tracker.to_json())

    def find_title_paragraph(self, docs: Document) -> PdfParagraph | None:
        """Find the first paragraph with layout_label 'title' in the document.

        Args:
            docs: The document to search in

        Returns:
            The first title paragraph found, or None if no title paragraph exists
        """
        for page in docs.page:
            for paragraph in page.pdf_paragraph:
                if paragraph.layout_label == "title":
                    logger.info(f"Found title paragraph: {paragraph.unicode}")
                    return paragraph
        return None

    def process_page(
        self,
        page: Page,
        executor: PriorityThreadPoolExecutor,
        pbar: tqdm | None = None,
        tracker: PageTranslateTracker = None,
    ):
        self.translation_config.raise_if_cancelled()
        for paragraph in page.pdf_paragraph:
            page_font_map = {}
            for font in page.pdf_font:
                page_font_map[font.font_id] = font
            page_xobj_font_map = {}
            for xobj in page.pdf_xobject:
                page_xobj_font_map[xobj.xobj_id] = page_font_map.copy()
                for font in xobj.pdf_font:
                    page_xobj_font_map[xobj.xobj_id][font.font_id] = font
            # self.translate_paragraph(paragraph, pbar,tracker.new_paragraph(), page_font_map, page_xobj_font_map)
            paragraph_token_count = self.calc_token_count(paragraph.unicode)
            if paragraph.layout_label == "title":
                self.shared_context_cross_split_part.recent_title_paragraph = (
                    copy.deepcopy(paragraph)
                )
            executor.submit(
                self.translate_paragraph,
                paragraph,
                page,
                pbar,
                tracker.new_paragraph(),
                page_font_map,
                page_xobj_font_map,
                priority=1048576 - paragraph_token_count,
                paragraph_token_count=paragraph_token_count,
                title_paragraph=self.translation_config.shared_context_cross_split_part.first_paragraph,
                local_title_paragraph=self.translation_config.shared_context_cross_split_part.recent_title_paragraph,
            )

    class TranslateInput:
        def __init__(
            self,
            unicode: str,
            placeholders: list[RichTextPlaceholder | FormulaPlaceholder],
            base_style: PdfStyle = None,
        ):
            self.unicode = unicode
            self.placeholders = placeholders
            self.base_style = base_style

        def get_placeholders_hint(self) -> dict[str, str] | None:
            hint = {}
            for placeholder in self.placeholders:
                if isinstance(placeholder, FormulaPlaceholder):
                    cid_count = 0
                    for char in placeholder.formula.pdf_character:
                        if re.match(r"^\(cid:\d+\)$", char.char_unicode):
                            cid_count += 1
                    if cid_count > len(placeholder.formula.pdf_character) * 0.8:
                        continue

                    hint[placeholder.placeholder] = get_char_unicode_string(
                        placeholder.formula.pdf_character
                    )
            if hint:
                return hint
            return None

    def create_formula_placeholder(
        self,
        formula: PdfFormula,
        formula_id: int,
        paragraph: PdfParagraph,
    ):
        # Formula regions are preserved byte-for-byte, so control characters
        # from a damaged source text stream bypass normal translation cleanup
        # and become visible .notdef squares in the PDF. They have no semantic
        # value in a mathematical formula; discard only C0/C1 controls while
        # retaining every printable symbol, number, and layout coordinate.
        formula.pdf_character[:] = [
            char
            for char in formula.pdf_character
            if not char.char_unicode
            or unicodedata.category(char.char_unicode[0]) != "Cc"
        ]
        placeholder = self.translate_engine.get_formular_placeholder(formula_id)
        if isinstance(placeholder, tuple):
            placeholder, regex_pattern = placeholder
        else:
            tag_match = re.match(r'^<([A-Za-z]+)(\d+)>$', str(placeholder))
            if tag_match:
                tag_name, tag_id = tag_match.groups()
                regex_pattern = rf"<\s*{tag_name}\s*{tag_id}\s*>"
            else:
                regex_pattern = re.escape(str(placeholder))
        if re.match(regex_pattern, paragraph.unicode, re.IGNORECASE):
            return self.create_formula_placeholder(formula, formula_id + 1, paragraph)

        return FormulaPlaceholder(formula_id, formula, placeholder, regex_pattern)

    def create_rich_text_placeholder(
        self,
        composition: PdfSameStyleCharacters,
        composition_id: int,
        paragraph: PdfParagraph,
    ):
        left_placeholder = self.translate_engine.get_rich_text_left_placeholder(
            composition_id,
        )
        right_placeholder = self.translate_engine.get_rich_text_right_placeholder(
            composition_id,
        )
        if isinstance(left_placeholder, tuple):
            left_placeholder, left_placeholder_regex_pattern = left_placeholder
        else:
            tag_match = re.match(r'^<([A-Za-z]+)(\d+)>$', str(left_placeholder))
            if tag_match:
                tag_name, tag_id = tag_match.groups()
                left_placeholder_regex_pattern = rf"<\s*{tag_name}\s*{tag_id}\s*>"
            else:
                left_placeholder_regex_pattern = re.escape(str(left_placeholder))

        if isinstance(right_placeholder, tuple):
            right_placeholder, right_placeholder_regex_pattern = right_placeholder
        else:
            tag_match = re.match(r'^</([A-Za-z]+)(\d+)>$', str(right_placeholder))
            if tag_match:
                tag_name, tag_id = tag_match.groups()
                right_placeholder_regex_pattern = rf"<\s*/\s*{tag_name}\s*{tag_id}\s*>"
            else:
                right_placeholder_regex_pattern = re.escape(str(right_placeholder))
        if re.match(
            f"{left_placeholder_regex_pattern}|{right_placeholder_regex_pattern}",
            paragraph.unicode,
            re.IGNORECASE,
        ):
            return self.create_rich_text_placeholder(
                composition,
                composition_id + 1,
                paragraph,
            )

        return RichTextPlaceholder(
            composition_id,
            composition,
            left_placeholder,
            right_placeholder,
            left_placeholder_regex_pattern,
            right_placeholder_regex_pattern,
        )

    def get_translate_input(
        self,
        paragraph: PdfParagraph,
        page_font_map: dict[str, PdfFont] = None,
        disable_rich_text_translate: bool | None = None,
    ):
        if not paragraph.pdf_paragraph_composition:
            return

        # Skip pure numeric paragraphs
        if is_pure_numeric_paragraph(paragraph):
            return None

        # Skip paragraphs with only placeholders
        if is_placeholder_only_paragraph(paragraph):
            return None
        if len(paragraph.pdf_paragraph_composition) == 1:
            # å¦‚æžœæ•´ä¸ªæ®µè½åªæœ‰ä¸€ä¸ªç»„æˆéƒ¨åˆ†ï¼Œé‚£ä¹ˆç›´æŽ¥è¿”å›žï¼Œä¸éœ€è¦å¥—å ä½ç¬¦ç­‰
            composition = paragraph.pdf_paragraph_composition[0]
            if (
                composition.pdf_line
                or composition.pdf_same_style_characters
                or composition.pdf_character
            ):
                return self.TranslateInput(paragraph.unicode, [], paragraph.pdf_style)
            elif composition.pdf_formula:
                # ä¸éœ€è¦ç¿»è¯‘çº¯å…¬å¼
                return None
            elif composition.pdf_same_style_unicode_characters:
                # DEBUG INSERT CHAR, NOT TRANSLATE
                return None
            else:
                logger.error(
                    f"Unknown composition type. "
                    f"Composition: {composition}. "
                    f"Paragraph: {paragraph}. ",
                )
                return None

        # å¦‚æžœæ²¡æœ‰æŒ‡å®š disable_rich_text_translateï¼Œä½¿ç”¨é…ç½®ä¸­çš„å€¼
        if disable_rich_text_translate is None:
            disable_rich_text_translate = (
                self.translation_config.disable_rich_text_translate
            )

        placeholder_id = 1
        placeholders = []
        chars = []
        for composition in paragraph.pdf_paragraph_composition:
            if composition.pdf_line:
                chars.extend(composition.pdf_line.pdf_character)
            elif composition.pdf_formula:
                formula_placeholder = self.create_formula_placeholder(
                    composition.pdf_formula,
                    placeholder_id,
                    paragraph,
                )
                placeholders.append(formula_placeholder)
                # å…¬å¼åªéœ€è¦ä¸€ä¸ªå ä½ç¬¦ï¼Œæ‰€ä»¥ id+1
                placeholder_id = formula_placeholder.id + 1
                chars.extend(formula_placeholder.placeholder)
            elif composition.pdf_character:
                chars.append(composition.pdf_character)
            elif composition.pdf_same_style_characters:
                if disable_rich_text_translate:
                    # å¦‚æžœç¦ç”¨å¯Œæ–‡æœ¬ç¿»è¯‘ï¼Œç›´æŽ¥æ·»åŠ å­—ç¬¦
                    chars.extend(composition.pdf_same_style_characters.pdf_character)
                    continue

                fonta = self.font_mapper.map(
                    page_font_map[
                        composition.pdf_same_style_characters.pdf_style.font_id
                    ],
                    "1",
                )
                fontb = self.font_mapper.map(
                    page_font_map[paragraph.pdf_style.font_id],
                    "1",
                )
                if (
                    # æ ·å¼å’Œæ®µè½åŸºå‡†æ ·å¼ä¸€è‡´ï¼Œæ— éœ€å ä½ç¬¦
                    is_same_style(
                        composition.pdf_same_style_characters.pdf_style,
                        paragraph.pdf_style,
                    )
                    # å­—å·å·®å¼‚åœ¨ 0.7-1.3 ä¹‹é—´ï¼Œå¯èƒ½æ˜¯é¦–å­—æ¯å˜å¤§æ•ˆæžœï¼Œæ— éœ€å ä½ç¬¦
                    or is_same_style_except_size(
                        composition.pdf_same_style_characters.pdf_style,
                        paragraph.pdf_style,
                    )
                    or (
                        # é™¤äº†å­—ä½“ä»¥å¤–æ ·å¼éƒ½å’ŒåŸºå‡†ä¸€æ ·ï¼Œå¹¶ä¸”å­—ä½“éƒ½æ˜ å°„åˆ°åŒä¸€ä¸ªå­—ä½“ã€‚æ— éœ€å ä½ç¬¦
                        is_same_style_except_font(
                            composition.pdf_same_style_characters.pdf_style,
                            paragraph.pdf_style,
                        )
                        and fonta
                        and fontb
                        and fonta.font_id == fontb.font_id
                    )
                    # or len(composition.pdf_same_style_characters.pdf_character) == 1
                ):
                    chars.extend(composition.pdf_same_style_characters.pdf_character)
                    continue
                placeholder = self.create_rich_text_placeholder(
                    composition.pdf_same_style_characters,
                    placeholder_id,
                    paragraph,
                )
                placeholders.append(placeholder)
                # æ ·å¼éœ€è¦ä¸€å·¦ä¸€å³ä¸¤ä¸ªå ä½ç¬¦ï¼Œæ‰€ä»¥ id+2
                placeholder_id = placeholder.id + 2
                chars.append(placeholder.left_placeholder)
                chars.extend(composition.pdf_same_style_characters.pdf_character)
                chars.append(placeholder.right_placeholder)
            else:
                logger.error(
                    "Unexpected PdfParagraphComposition type "
                    "in PdfParagraph during translation. "
                    f"Composition: {composition}. "
                    f"Paragraph: {paragraph}. ",
                )
                return None

            # å¦‚æžœå ä½ç¬¦æ•°é‡è¶…è¿‡é˜ˆå€¼ï¼Œä¸”æœªç¦ç”¨å¯Œæ–‡æœ¬ç¿»è¯‘ï¼Œåˆ™é€’å½’è°ƒç”¨å¹¶ç¦ç”¨å¯Œæ–‡æœ¬ç¿»è¯‘
            if len(placeholders) > 40 and not disable_rich_text_translate:
                logger.warning(
                    f"Too many placeholders ({len(placeholders)}) in paragraph[{paragraph.debug_id}], "
                    "disabling rich text translation for this paragraph",
                )
                return self.get_translate_input(paragraph, page_font_map, True)

        text = get_char_unicode_string(chars)
        return self.TranslateInput(text, placeholders, paragraph.pdf_style)

    def process_formula(
        self,
        formula: PdfFormula,
        formula_id: int,
        paragraph: PdfParagraph,
    ):
        placeholder = self.create_formula_placeholder(formula, formula_id, paragraph)
        if placeholder.placeholder in paragraph.unicode:
            return self.process_formula(formula, formula_id + 1, paragraph)

        return placeholder

    def process_composition(
        self,
        composition: PdfSameStyleCharacters,
        composition_id: int,
        paragraph: PdfParagraph,
    ):
        placeholder = self.create_rich_text_placeholder(
            composition,
            composition_id,
            paragraph,
        )
        if (
            placeholder.left_placeholder in paragraph.unicode
            or placeholder.right_placeholder in paragraph.unicode
        ):
            return self.process_composition(
                composition,
                composition_id + 1,
                paragraph,
            )

        return placeholder

    def parse_translate_output(
        self,
        input_text: TranslateInput,
        output: str,
        llm_translate_tracker: LLMTranslateTracker | None = None,
    ) -> [PdfParagraphComposition]:
        result = []

        # å¦‚æžœæ²¡æœ‰å ä½ç¬¦ï¼Œç›´æŽ¥è¿”å›žæ•´ä¸ªæ–‡æœ¬
        if not input_text.placeholders:
            comp = PdfParagraphComposition()
            comp.pdf_same_style_unicode_characters = PdfSameStyleUnicodeCharacters()
            comp.pdf_same_style_unicode_characters.unicode = output
            comp.pdf_same_style_unicode_characters.pdf_style = input_text.base_style
            if llm_translate_tracker:
                llm_translate_tracker.set_placeholder_full_match()
            return [comp]

        # æž„å»ºæ­£åˆ™è¡¨è¾¾å¼æ¨¡å¼
        patterns = []
        placeholder_patterns = []
        placeholder_map = {}

        for placeholder in input_text.placeholders:
            if isinstance(placeholder, FormulaPlaceholder):
                # è½¬ä¹‰ç‰¹æ®Šå­—ç¬¦
                # pattern = re.escape(placeholder.placeholder)
                pattern = placeholder.regex_pattern
                patterns.append(f"({pattern})")
                placeholder_patterns.append(f"({pattern})")
                placeholder_map[placeholder.placeholder] = placeholder
            else:
                left = placeholder.left_regex_pattern
                right = placeholder.right_regex_pattern
                patterns.append(f"({left}.*?{right})")
                placeholder_patterns.append(f"({left})")
                placeholder_patterns.append(f"({right})")
                placeholder_map[placeholder.left_placeholder] = placeholder
        all_match = True
        for pattern in patterns:
            if not re.search(pattern, output, flags=re.IGNORECASE):
                all_match = False
                break
        if all_match:
            if llm_translate_tracker:
                llm_translate_tracker.set_placeholder_full_match()
        else:
            logger.debug(f"Failed to match all placeholder for {input_text.unicode}")
        # åˆå¹¶æ‰€æœ‰æ¨¡å¼
        combined_pattern = "|".join(patterns)
        combined_placeholder_pattern = "|".join(placeholder_patterns)

        def remove_placeholder(text: str):
            return re.sub(combined_placeholder_pattern, "", text, flags=re.IGNORECASE)

        # æ‰¾åˆ°æ‰€æœ‰åŒ¹é…
        last_end = 0
        for match in re.finditer(combined_pattern, output, flags=re.IGNORECASE):
            # å¤„ç†åŒ¹é…ä¹‹å‰çš„æ™®é€šæ–‡æœ¬
            if match.start() > last_end:
                text = output[last_end : match.start()]
                if text:
                    comp = PdfParagraphComposition()
                    comp.pdf_same_style_unicode_characters = (
                        PdfSameStyleUnicodeCharacters()
                    )
                    comp.pdf_same_style_unicode_characters.unicode = remove_placeholder(
                        text,
                    )
                    comp.pdf_same_style_unicode_characters.pdf_style = (
                        input_text.base_style
                    )
                    result.append(comp)

            matched_text = match.group(0)

            # å¤„ç†å ä½ç¬¦
            if any(
                isinstance(p, FormulaPlaceholder)
                and re.match(f"^{p.regex_pattern}$", matched_text, re.IGNORECASE)
                for p in input_text.placeholders
            ):
                # å¤„ç†å…¬å¼å ä½ç¬¦
                placeholder = next(
                    p
                    for p in input_text.placeholders
                    if isinstance(p, FormulaPlaceholder)
                    and re.match(f"^{p.regex_pattern}$", matched_text, re.IGNORECASE)
                )
                comp = PdfParagraphComposition()
                comp.pdf_formula = placeholder.formula
                result.append(comp)
            else:
                # å¤„ç†å¯Œæ–‡æœ¬å ä½ç¬¦
                placeholder = next(
                    p
                    for p in input_text.placeholders
                    if not isinstance(p, FormulaPlaceholder)
                    and re.match(
                        f"^{p.left_regex_pattern}", matched_text, re.IGNORECASE
                    )
                )
                text = re.match(
                    f"^{placeholder.left_regex_pattern}(.*){placeholder.right_regex_pattern}$",
                    matched_text,
                    re.IGNORECASE,
                ).group(1)

                # A rich-text placeholder may be returned byte-for-byte (IBAN,
                # BIC, acronyms, or an untranslated English fragment).  It must
                # still be emitted as Unicode, rather than reusing the source
                # PdfCharacter objects.  Reusing them bypasses FontMapper and
                # TypesettingUnit's line layout, leaving Arabic presentation
                # forms, LRM/RLM, and text at their original source coordinates
                # in fonts such as SourceHan that have no Arabic glyphs.
                #
                # Keep the source style metadata, but always let the target
                # typesetter select and subset the Arabic-capable target font.
                comp = PdfParagraphComposition()
                comp.pdf_same_style_unicode_characters = (
                    PdfSameStyleUnicodeCharacters()
                )
                comp.pdf_same_style_unicode_characters.pdf_style = (
                    placeholder.composition.pdf_style
                )
                comp.pdf_same_style_unicode_characters.unicode = remove_placeholder(
                    text,
                )
                result.append(comp)

            last_end = match.end()

        # å¤„ç†æœ€åŽçš„æ™®é€šæ–‡æœ¬
        if last_end < len(output):
            text = output[last_end:]
            if text:
                comp = PdfParagraphComposition()
                comp.pdf_same_style_unicode_characters = PdfSameStyleUnicodeCharacters()
                comp.pdf_same_style_unicode_characters.unicode = remove_placeholder(
                    text,
                )
                comp.pdf_same_style_unicode_characters.pdf_style = input_text.base_style
                result.append(comp)

        return result

    @staticmethod
    def _is_file_inventory(text: str) -> bool:
        """Recognize dense directory listings with literal filenames."""
        files = re.findall(
            r"(?<![\w.])[a-z0-9_][\w.-]*\.(?:c|cpp|h|hpp|java|txt|md|pdf)(?![\w.])",
            text,
            flags=re.IGNORECASE,
        )
        directories = re.findall(r"(?<!\S)[\w-]+/(?=\s|$)", text)
        return (
            len(files) >= 12
            and len(directories) >= 2
            and 2 * (len(files) + len(directories)) >= len(text.split())
        )

    def pre_translate_paragraph(
        self,
        paragraph: PdfParagraph,
        tracker: ParagraphTranslateTracker,
        page_font_map: dict[str, PdfFont],
        xobj_font_map: dict[int, dict[str, PdfFont]],
    ):
        """Pre-translation processing: prepare text for translation."""
        if paragraph.vertical:
            return None, None
        tracker.set_pdf_unicode(paragraph.unicode)
        if not self.support_llm_translate and self._is_file_inventory(paragraph.unicode or ""):
            # Keep the original compositions, including filename punctuation.
            return None, None
        if paragraph.xobj_id in xobj_font_map:
            page_font_map = xobj_font_map[paragraph.xobj_id]
        disable_rich_text_translate = (
            self.translation_config.disable_rich_text_translate
        )
        if not self.support_llm_translate:
            disable_rich_text_translate = True
        layout_label = (paragraph.layout_label or "").lower()
        if "table_cell" in layout_label and re.fullmatch(r"[A-Z]", (paragraph.unicode or "").strip()):
            # Conversion-table letters and marks such as X are literal data.
            return None, None
        if any(
            marker in layout_label
            for marker in ("contact_card", "procedure_step", "heading")
        ):
            # Structure already carries the hierarchy for these records.
            # Rich-text placeholders can make a two-word heading or one-line
            # field behave like several forced fragments after translation.
            disable_rich_text_translate = True

        translate_input = self.get_translate_input(
            paragraph, page_font_map, disable_rich_text_translate
        )
        if not translate_input:
            return None, None
        if any(
            marker in layout_label
            for marker in ("procedure_step", "heading")
        ):
            structured_text = self._structured_source_lines(paragraph)
            if structured_text:
                translate_input.unicode = structured_text
        tracker.set_input(translate_input.unicode)
        tracker.set_placeholders(translate_input.placeholders)
        text = translate_input.unicode
        if (
            len(text) < self.translation_config.min_text_length
            and _is_pure_passthrough(text)
        ):
            logger.debug(
                f"Text too short to translate, skip. Text: {text}. Paragraph id: {paragraph.debug_id}."
            )
            return None, None
        return text, translate_input

    def _translate_contact_field_directly(self, text: str) -> str | None:
        """Translate a contact-field label while preserving its value exactly."""
        if not (self.translation_config.lang_out or "").upper().startswith("AR"):
            return None
        match = re.match(
            r"^\s*(tel(?:ephone)?|fax|e[ -]?mail|email)\s*[:：]\s*(.*?)\s*$",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None
        key = re.sub(r"[ -]", "", match.group(1).lower())
        label = {
            "tel": "الهاتف",
            "telephone": "الهاتف",
            "fax": "الفاكس",
            "email": "البريد الإلكتروني",
        }[key]
        value = match.group(2)
        return f"{label}: {value}" if value else f"{label}:"

    def post_translate_paragraph(
        self,
        paragraph: PdfParagraph,
        tracker: ParagraphTranslateTracker,
        translate_input,
        translated_text: str,
    ):
        """Post-translation processing: update paragraph with translated text."""
        tracker.set_output(translated_text)
        if translated_text == translate_input:
            if llm_translate_tracker := tracker.last_llm_translate_tracker():
                llm_translate_tracker.set_placeholder_full_match()
            return False
        paragraph.unicode = translated_text
        paragraph.pdf_paragraph_composition = self.parse_translate_output(
            translate_input,
            translated_text,
            tracker.last_llm_translate_tracker(),
        )
        for composition in paragraph.pdf_paragraph_composition:
            if composition.pdf_same_style_unicode_characters:
                value = composition.pdf_same_style_unicode_characters.unicode or ""
                composition.pdf_same_style_unicode_characters.unicode = "".join(
                    char for char in value
                    if char in {"\n", "\t"} or unicodedata.category(char) != "Cc"
                )
            elif composition.pdf_formula:
                composition.pdf_formula.pdf_character[:] = [
                    char
                    for char in composition.pdf_formula.pdf_character
                    if not char.char_unicode
                    or unicodedata.category(char.char_unicode[0]) != "Cc"
                ]
            if (
                composition.pdf_same_style_unicode_characters
                and composition.pdf_same_style_unicode_characters.pdf_style is None
            ):
                composition.pdf_same_style_unicode_characters.pdf_style = (
                    paragraph.pdf_style
                )
        return True

    def generate_prompt_for_llm(
        self,
        text: str,
        title_paragraph: PdfParagraph | None = None,
        local_title_paragraph: PdfParagraph | None = None,
        translate_input: TranslateInput | None = None,
    ):
        if self.translation_config.custom_system_prompt:
            llm_input = [self.translation_config.custom_system_prompt]
        else:
            llm_input = [
                f"You are a professional and reliable machine translation engine responsible for translating the input text into {self.translation_config.lang_out}."
            ]

        llm_input.append("When translating, please follow the following rules:")

        rich_text_left_placeholder = (
            self.translate_engine.get_rich_text_left_placeholder(1)
        )
        if isinstance(rich_text_left_placeholder, tuple):
            rich_text_left_placeholder = rich_text_left_placeholder[0]
        rich_text_right_placeholder = (
            self.translate_engine.get_rich_text_right_placeholder(2)
        )
        if isinstance(rich_text_right_placeholder, tuple):
            rich_text_right_placeholder = rich_text_right_placeholder[0]

        # Create a structured prompt template for LLM translation
        llm_input.append(
            f'1. Do not translate style tags, such as "{rich_text_left_placeholder}xxx{rich_text_right_placeholder}"!'
        )

        formula_placeholder = self.translate_engine.get_formular_placeholder(3)
        if isinstance(formula_placeholder, tuple):
            formula_placeholder = formula_placeholder[0]

        llm_input.append(
            f'2. Do not translate formula placeholders, such as "{formula_placeholder}". The system will automatically replace the placeholders with the corresponding formulas.'
        )
        llm_input.append(
            "3. Preserve ALL formatting elements exactly as they appear: section numbers (2.1, 3.2.1, etc.), list markers (1., 2., a., b., 1), 2), â€¢, â–ª, â—¦, -, etc.), parentheses, brackets, quotes, and bullet points."
        )
        llm_input.append(
            "4. If there is no need to translate (such as proper nouns, codes, etc.), then return the original text."
        )
        llm_input.append(
            f"5. Only output the translation result in {self.translation_config.lang_out} without explanations and annotations."
        )

        llm_context_hints = []

        if title_paragraph:
            llm_context_hints.append(
                f"The first title in the full text: {title_paragraph.unicode}"
            )
        if (
            local_title_paragraph
            and title_paragraph
            and local_title_paragraph.debug_id != title_paragraph.debug_id
        ):
            llm_context_hints.append(
                f"The most similar title in the full text: {local_title_paragraph.unicode}"
            )

        if translate_input and self.translation_config.add_formula_placehold_hint:
            placeholders_hint = translate_input.get_placeholders_hint()
            if placeholders_hint:
                llm_context_hints.append(
                    f"This is the formula placeholder hint: \n{placeholders_hint}"
                )

        active_glossary_markdown_blocks: list[str] = []
        # Use cached glossaries
        if self._cached_glossaries:
            for glossary in self._cached_glossaries:
                # Get active entries for the current text being processed (passed as 'text')
                active_entries = glossary.get_active_entries_for_text(text)

                if active_entries:
                    current_glossary_md_entries: list[str] = []
                    for original_source, target_text in sorted(active_entries):
                        current_glossary_md_entries.append(
                            f"| {original_source} | {target_text} |"
                        )

                    if current_glossary_md_entries:
                        glossary_table_md = (
                            f"### Glossary: {glossary.name}\n\n"
                            "| Source Term | Target Term |\n"
                            "|-------------|-------------|\n"
                            + "\n".join(current_glossary_md_entries)
                        )
                        active_glossary_markdown_blocks.append(glossary_table_md)

        if llm_context_hints or active_glossary_markdown_blocks:
            llm_input.append(
                "When translating, please refer to the following information to improve translation quality:"
            )
            current_hint_index = 1
            for hint_line in llm_context_hints:
                llm_input.append(f"{current_hint_index}. {hint_line}")
                current_hint_index += 1

            if active_glossary_markdown_blocks:
                llm_input.append(
                    f"{current_hint_index}. You MUST strictly adhere to the following glossaries. If a source term from a table appears in the text, use the corresponding target term in your translation:"
                )
                current_hint_index += 1
                for md_block in active_glossary_markdown_blocks:
                    llm_input.append(f"\n{md_block}\n")

        prompt_template = f"""
Now, please carefully read the following text to be translated and directly output your translation.\n\n{text}
"""
        llm_input.append(prompt_template)

        final_input = "\n".join(llm_input).strip()

        return final_input

    def add_content_filter_hint(self, page: Page, paragraph: PdfParagraph):
        with self.add_content_filter_hint_lock:
            new_box = il_version_1.Box(
                x=paragraph.box.x,
                y=paragraph.box.y2,
                x2=paragraph.box.x2,
                y2=paragraph.box.y2 + 1.1,
            )
            page.pdf_paragraph.append(
                self._create_text(
                    "ç¿»è¯‘æœåŠ¡æ£€æµ‹åˆ°å†…å®¹å¯èƒ½åŒ…å«ä¸å®‰å…¨æˆ–æ•æ„Ÿå†…å®¹ï¼Œè¯·æ‚¨é¿å…ç¿»è¯‘æ•æ„Ÿå†…å®¹ï¼Œæ„Ÿè°¢æ‚¨çš„é…åˆã€‚",
                    GRAY80,
                    new_box,
                    1,
                )
            )
            logger.info("success add content filter hint")

    def _create_text(
        self,
        text: str,
        color: GraphicState,
        box: il_version_1.Box,
        font_size: float = 4,
    ):
        style = il_version_1.PdfStyle(
            font_id="base",
            font_size=font_size,
            graphic_state=color,
        )
        return il_version_1.PdfParagraph(
            first_line_indent=False,
            box=box,
            vertical=False,
            pdf_style=style,
            unicode=text,
            pdf_paragraph_composition=[
                il_version_1.PdfParagraphComposition(
                    pdf_same_style_unicode_characters=il_version_1.PdfSameStyleUnicodeCharacters(
                        unicode=text,
                        pdf_style=style,
                        debug_info=True,
                    ),
                ),
            ],
            xobj_id=-1,
        )

    def translate_paragraph(
        self,
        paragraph: PdfParagraph,
        page: Page,
        pbar: tqdm | None = None,
        tracker: ParagraphTranslateTracker = None,
        page_font_map: dict[str, PdfFont] = None,
        xobj_font_map: dict[int, dict[str, PdfFont]] = None,
        paragraph_token_count: int = 0,
        title_paragraph: PdfParagraph | None = None,
        local_title_paragraph: PdfParagraph | None = None,
    ):
        """Translate a paragraph using pre and post processing functions."""
        self.translation_config.raise_if_cancelled()
        with PbarContext(pbar):
            try:
                if self.use_as_fallback:
                    # il translator llm only modifies unicode in some situations
                    paragraph.unicode = get_paragraph_unicode(paragraph)
                source_unicode = paragraph.unicode
                # Pre-translation processing
                text, translate_input = self.pre_translate_paragraph(
                    paragraph, tracker, page_font_map, xobj_font_map
                )
                if text is None:
                    return
                lbl = paragraph.layout_label or 'unknown'
                logger.info(f"[{lbl}] Translating paragraph {paragraph.debug_id}: {text[:80] if text else ''}")
                llm_translate_tracker = tracker.new_llm_translate_tracker()
                # Perform translation
                direct_translation = None
                index_reference = ""
                if "contact_card" in lbl.lower():
                    direct_translation = self._translate_contact_field_directly(source_unicode or text)
                elif (
                    not self.support_llm_translate
                    and "table_cell" in lbl.lower()
                    and (self.translation_config.lang_out or "").lower().rsplit("-", 1)[-1]
                    in {"ar", "ara", "arabic"}
                ):
                    # Standalone payment labels otherwise look like BICs to the
                    # generic protection rule. Keep real banking codes protected.
                    direct_translation = {
                        "INCOMING": "\u0627\u0644\u0648\u0627\u0631\u062f\u0629",
                        "OUTGOING": "\u0627\u0644\u0635\u0627\u062f\u0631\u0629",
                    }.get(text.strip().upper())
                if direct_translation is not None:
                    translated_text = direct_translation
                elif self.support_llm_translate:
                    llm_prompt = self.generate_prompt_for_llm(
                        text,
                        title_paragraph,
                        local_title_paragraph,
                        translate_input,
                    )
                    llm_translate_tracker.set_input(llm_prompt)
                    translated_text = self.translate_engine.llm_translate(
                        llm_prompt,
                        rate_limit_params={
                            "paragraph_token_count": paragraph_token_count
                        },
                    )
                    translated_text = self.shape_arabic_text(translated_text)
                    llm_translate_tracker.set_output(translated_text)
                else:
                    marker = self._leading_bullet_tag(text, translate_input, lbl.lower())
                    body = text[len(marker):].lstrip() if marker else text
                    body, index_reference, translate_input = (
                        self._split_trailing_index_reference(body, source_unicode, translate_input)
                    )
                    translated_text = self._translate_with_line_preservation(
                        body,
                        rate_limit_params={
                            "paragraph_token_count": paragraph_token_count
                        },
                        force_preserve_lines=any(
                            marker in lbl.lower()
                            for marker in ("procedure_step", "contact_card")
                        ),
                    )
                    if marker:
                        translated_text = f"{marker} {translated_text}"
                translated_text = self._normalize_arabic_output(translated_text)
                translated_text = self.shape_arabic_text(translated_text)
                # Only collapse repetitive punctuation if not a TOC leader line (\.{4,}\s*\d+)
                if not re.search(r'\.{4,}\s*\d+', translated_text):
                    translated_text = re.sub(r"[. 。…，]{20,}", ".", translated_text)
                if index_reference:
                    language = (self.translation_config.lang_out or "").lower().rsplit("-", 1)[-1]
                    if language in {"ar", "ara", "arabic"}:
                        index_reference = f"\u200e{index_reference}\u200e"
                    translated_text = f"{translated_text.rstrip()} {index_reference}"

                # Attach diagnostic pipeline trace for paragraph routing analysis
                engine_name = getattr(self.translate_engine, 'name', self.translate_engine.__class__.__name__)
                paragraph.pipeline_trace = {
                    "debug_id": paragraph.debug_id,
                    "layout_label": lbl,
                    "engine": engine_name,
                    "source_len": len(text) if text else 0,
                    "trans_len": len(translated_text) if translated_text else 0,
                    "is_pure_passthrough": _is_pure_passthrough(text) if text else False,
                }
                logger.info(
                    f"[TRACE P{paragraph.debug_id}] label='{lbl}' engine='{engine_name}' "
                    f"in_len={len(text) if text else 0} out_len={len(translated_text) if translated_text else 0}"
                )

                # Post-translation processing
                self.post_translate_paragraph(
                    paragraph, tracker, translate_input, translated_text
                )
            except ContentFilterError as e:
                logger.warning(f"ContentFilterError: {e.message}")
                self.add_content_filter_hint(page, paragraph)
                self.translate_engine.untranslated_count = getattr(self.translate_engine, "untranslated_count", 0) + 1
                return
            except Exception as e:
                logger.exception(
                    f"Error translating paragraph. Paragraph: {paragraph.debug_id} ({paragraph.unicode}). Error: {e}. ",
                )
                self.translate_engine.untranslated_count = getattr(self.translate_engine, "untranslated_count", 0) + 1
                # ignore error and continue
                return
