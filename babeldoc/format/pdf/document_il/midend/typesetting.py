from __future__ import annotations

import copy
import logging
import re
import statistics
import unicodedata
from functools import cache, partial

import pymupdf
import regex
from rtree import index
from babeldoc.format.pdf.document_il import Box
from babeldoc.format.pdf.document_il import PdfCharacter
from babeldoc.format.pdf.document_il import PdfCurve
from babeldoc.format.pdf.document_il import PdfForm
from babeldoc.format.pdf.document_il import PdfFormula
from babeldoc.format.pdf.document_il import PdfParagraphComposition
from babeldoc.format.pdf.document_il import PdfStyle
from babeldoc.format.pdf.document_il import il_version_1
from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper, is_arabic_codepoint
from babeldoc.format.pdf.document_il.utils.arabic_shaper import shape_arabic_run
from babeldoc.format.pdf.document_il.utils.formular_helper import update_formula_data
from babeldoc.format.pdf.document_il.utils.layout_helper import box_to_tuple
from babeldoc.format.pdf.document_il.utils.style_helper import GRAY80
from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.format.pdf.translation_config import WatermarkOutputMode
from arabic_reshaper import reshape
import bidi.algorithm as ba


logger = logging.getLogger(__name__)


LINE_BREAK_REGEX = regex.compile(
    r"^["
    r"a-z"
    r"A-Z"
    r"0-9"
    r"\u00C0-\u00FF"  # Latin-1 Supplement
    r"\u0100-\u017F"  # Latin Extended A
    r"\u0180-\u024F"  # Latin Extended B
    r"\u1E00-\u1EFF"  # Latin Extended Additional
    r"\u2C60-\u2C7F"  # Latin Extended C
    r"\uA720-\uA7FF"  # Latin Extended D
    r"\uAB30-\uAB6F"  # Latin Extended E
    r"\u0250-\u02A0"  # IPA Extensions
    r"\u0400-\u04FF"  # Cyrillic
    r"\u0300-\u036F"  # Combining Diacritical Marks
    r"\u0500-\u052F"  # Cyrillic Supplement
    r"\u0370-\u03FF"  # Greek and Coptic
    r"\u2DE0-\u2DFF"  # Cyrillic Extended-A
    r"\uA650-\uA69F"  # Cyrillic Extended-B
    r"\u1200-\u137F"  # Ethiopic
    r"\u1380-\u139F"  # Ethiopic Supplement
    r"\u2D80-\u2DDF"  # Ethiopic Extended
    r"\uAB00-\uAB2F"  # Ethiopic Extended-A
    r"\U0001E7E0-\U0001E7FF"  # Ethiopic Extended-B
    r"\u0E80-\u0EFF"  # Lao
    r"\u0D00-\u0D7F"  # Malayalam
    r"\u0A80-\u0AFF"  # Gujarati
    r"\u0E00-\u0E7F"  # Thai
    r"\u1000-\u109F"  # Myanmar
    r"\uAA60-\uAA7F"  # Myanmar Extended-A
    r"\uA9E0-\uA9FF"  # Myanmar Extended-B
    r"\U000116D0-\U000116FF"  # Myanmar Extended-C
    r"\u0B80-\u0BFF"  # Tamil
    r"\u0C00-\u0C7F"  # Telugu
    r"\u0B00-\u0B7F"  # Oriya
    r"\u0530-\u058F"  # Armenian
    r"\u10A0-\u10FF"  # Georgian
    r"\u1C90-\u1CBF"  # Georgian Extended
    r"\u2D00-\u2D2F"  # Georgian Supplement
    r"\u1780-\u17FF"  # Khmer
    r"\u19E0-\u19FF"  # Khmer Symbols
    r"\U00010B00-\U00010B3F"  # Avestan
    r"\u1D00-\u1D7F"  # Phonetic Extensions
    r"\u1400-\u167F"  # Unified Canadian Aboriginal Syllabics
    r"\u0B00-\u0B7F"  # Oriya
    r"\u0780-\u07BF"  # Thaana
    r"\U0001E900-\U0001E95F"  # Adlam
    r"\u1C80-\u1C8F"  # Cyrillic Extended-C
    r"\U0001E030-\U0001E08F"  # Cyrillic Extended-D
    r"\uA000-\uA48F"  # Yi Syllables
    r"\uA490-\uA4CF"  # Yi Radicals
    r"'"
    r"-"  # Hyphen
    r"\u00B7"  # Middle Dot
    r"\u02BB"  # Spacing Modifier Letters
    r"]+ $"
)

ARABIC_WORD_SPACING_RATIO = 1.65


class TypesettingUnit:
    def __str__(self):
        return self.try_get_unicode() or ""

    def __init__(
        self,
        char: PdfCharacter | None = None,
        formular: PdfFormula | None = None,
        unicode: str | None = None,
        font: pymupdf.Font | None = None,
        arabic_fallback_font: pymupdf.Font | None = None,
        original_font: il_version_1.PdfFont | None = None,
        font_size: float | None = None,
        style: PdfStyle | None = None,
        xobj_id: int | None = None,
        debug_info: bool = False,
        forced_line_break: bool = False,
    ):
        assert forced_line_break or (
            (char is not None) + (formular is not None) + (unicode is not None) == 1
        ), "Only one of chars and formular can be not None"
        self.char = char
        self.formular = formular
        self.unicode = unicode
        self.x = None
        self.y = None
        self.scale = None
        self.debug_info = debug_info
        # A newline is layout metadata, never a printable glyph.  Keeping it
        # as an explicit unit lets structured procedures and contact cards
        # retain their source records all the way to final typesetting.
        self.forced_line_break = forced_line_break
        self.arabic_fallback_font = arabic_fallback_font

        # Cache variables
        self.box_cache: Box | None = None
        self.can_break_line_cache: bool | None = None
        self.is_cjk_char_cache: bool | None = None
        self.mixed_character_blacklist_cache: bool | None = None
        self.is_space_cache: bool | None = None
        self.is_hung_punctuation_cache: bool | None = None
        self.is_cannot_appear_in_line_end_punctuation_cache: bool | None = None
        self.can_passthrough_cache: bool | None = None
        self.width_cache: float | None = None
        self.height_cache: float | None = None

        self.font_size: float | None = None

        if unicode:
            assert font_size, "Font size must be provided when unicode is provided"
            assert style, "Style must be provided when unicode is provided"
            assert len(unicode) == 1, "Unicode must be a single character"
            assert xobj_id is not None, (
                "Xobj id must be provided when unicode is provided"
            )

            self.font = font
            if font is not None and hasattr(font, "font_id"):
                self.font_id = font.font_id
            else:
                self.font_id = "base"
            if original_font:
                self.original_font = original_font
            else:
                self.original_font = None

            self.font_size = font_size
            self.style = style
            self.xobj_id = xobj_id
    
    def try_resue_cache(self, old_tu: TypesettingUnit):
        if old_tu.is_cjk_char_cache is not None:
            self.is_cjk_char_cache = old_tu.is_cjk_char_cache

        if old_tu.can_break_line_cache is not None:
            self.can_break_line_cache = old_tu.can_break_line_cache

        if old_tu.is_space_cache is not None:
            self.is_space_cache = old_tu.is_space_cache

        if old_tu.is_hung_punctuation_cache is not None:
            self.is_hung_punctuation_cache = old_tu.is_hung_punctuation_cache

        if old_tu.is_cannot_appear_in_line_end_punctuation_cache is not None:
            self.is_cannot_appear_in_line_end_punctuation_cache = (
                old_tu.is_cannot_appear_in_line_end_punctuation_cache
            )

        if old_tu.can_passthrough_cache is not None:
            self.can_passthrough_cache = old_tu.can_passthrough_cache

        if old_tu.mixed_character_blacklist_cache is not None:
            self.mixed_character_blacklist_cache = (
                old_tu.mixed_character_blacklist_cache
            )


    def try_get_unicode(self) -> str | None:
        if self.char:
            return self.char.char_unicode
        elif self.formular:
            return None
        elif self.unicode:
            return self.unicode

    @property
    def is_drawn_list_bullet(self) -> bool:
        return bool(
            self.formular
            and self.formular.pdf_curve
            and len(self.formular.pdf_character) == 1
            and self.formular.pdf_character[0].char_unicode == "\u2022"
        )

    @property
    def mixed_character_blacklist(self):
        if self.mixed_character_blacklist_cache is None:
            self.mixed_character_blacklist_cache = self.calc_mixed_character_blacklist()

        return self.mixed_character_blacklist_cache

    def calc_mixed_character_blacklist(self):
        unicode = self.try_get_unicode()
        if unicode:
            return unicode in [
                "Ãƒâ€šÃ‚Â£",
                "ÃƒËœÃ…â€™",
                "ÃƒÂ¯Ã‚Â¼Ã‚Â",
                "ÃƒÂ¯Ã‚Â¼Ã…Â¡",
                "ÃƒÂ¯Ã‚Â¼Ã¢â‚¬Â°",
            ]
        return False

    @property
    def can_break_line(self):
        if self.can_break_line_cache is None:
            self.can_break_line_cache = self.calc_can_break_line()

        return self.can_break_line_cache

    def calc_can_break_line(self):
        unicode = self.try_get_unicode()
        if not unicode:
            return True
        if LINE_BREAK_REGEX.match(unicode):
            return False
        return True

    @property
    def is_cjk_char(self):
        if self.is_cjk_char_cache is None:
            self.is_cjk_char_cache = self.calc_is_cjk_char()

        return self.is_cjk_char_cache

    def calc_is_cjk_char(self):
        if self.formular:
            return False
        unicode = self.try_get_unicode()
        if not unicode:
            return False
        if "(cid" in unicode:
            return False
        if len(unicode) > 1:
            return False
        assert len(unicode) == 1, "Unicode must be a single character"
        if unicode in [
            "ÃƒÂ¯Ã‚Â¼Ã‹â€ ",
            "ÃƒÂ¯Ã‚Â¼Ã¢â‚¬Â°",
            "ÃƒËœÃ…â€™",
            "ÃƒÂ£Ã¢â€šÂ¬Ã¢â‚¬Å¡",
            "ÃƒÂ£Ã¢â€šÂ¬Ã‚Â",
            "ÃƒÂ¯Ã‚Â¼Ã¢â‚¬Âº",
            "ÃƒÂ¯Ã‚Â¼Ã…Â¡",
            "ÃƒÂ¯Ã‚Â¼Ã…Â¸",
            "ÃƒÂ¯Ã‚Â¼Ã‚Â",
            "ÃƒÂ¯Ã‚Â¼Ã¢â‚¬Â°",
            "ÃƒËœÃ…â€™",
            "ÃƒÂ¯Ã‚Â¼Ã‚Â",
            "ÃƒÂ¯Ã‚Â¼Ã…Â¡",
            "ÃƒÂ¯Ã‚Â¼Ã¢â‚¬Â°",
        ]:
            return True
        if unicode:
            if re.match(
                r"^["
                r"\u3000-\u303f"  # CJK Symbols and Punctuation
                r"\u3040-\u309f"  # Hiragana
                r"\u30a0-\u30ff"  # Katakana
                r"\u3100-\u312f"  # Bopomofo
                r"\uac00-\ud7af"  # Hangul Syllables
                r"\u1100-\u11ff"  # Hangul Jamo
                r"\u3130-\u318f"  # Hangul Compatibility Jamo
                r"\ua960-\ua97f"  # Hangul Jamo Extended-A
                r"\ud7b0-\ud7ff"  # Hangul Jamo Extended-B
                r"\u3190-\u319f"  # Kanbun
                r"\u3200-\u32ff"  # Enclosed CJK Letters and Months
                r"\u3300-\u33ff"  # CJK Compatibility
                r"\ufe30-\ufe4f"  # CJK Compatibility Forms
                r"\u4e00-\u9fff"  # CJK Unified Ideographs
                r"\u2e80-\u2eff"  # CJK Radicals Supplement
                r"\u31c0-\u31ef"  # CJK Strokes
                r"\u2f00-\u2fdf"  # Kangxi Radicals
                r"\ufe10-\ufe1f"  # Vertical Forms
                r"]+$",
                unicode,
            ):
                return True
            try:
                unicodedata_name = unicodedata.name(unicode)
                return (
                    "CJK UNIFIED IDEOGRAPH" in unicodedata_name
                    or "FULLWIDTH" in unicodedata_name
                )
            except ValueError:
                return False
        return False

    @property
    def is_space(self):
        if self.is_space_cache is None:
            self.is_space_cache = self.calc_is_space()

        return self.is_space_cache

    def calc_is_space(self):
        if self.formular:
            return False
        unicode = self.try_get_unicode()
        return unicode == " "

    @property
    def is_hung_punctuation(self):
        if self.is_hung_punctuation_cache is None:
            self.is_hung_punctuation_cache = self.calc_is_hung_punctuation()

        return self.is_hung_punctuation_cache

    def calc_is_hung_punctuation(self):
        if self.formular:
            return False
        unicode = self.try_get_unicode()

        if unicode:
            return unicode in [
                # English punctuation
                ",",
                ".",
                ":",
                ";",
                "?",
                "!",
                # Chinese punctuation
                "ÃƒËœÃ…â€™",  # Comma
                "ÃƒÂ£Ã¢â€šÂ¬Ã¢â‚¬Å¡",  # Period
                "ÃƒÂ¯Ã‚Â¼Ã…Â¡",  # Colon
                "ÃƒÂ¯Ã‚Â¼Ã¢â‚¬Âº",  # Semicolon
                "ÃƒÂ¯Ã‚Â¼Ã…Â¸",  # Question mark
                "ÃƒÂ¯Ã‚Â¼Ã‚Â",  # Exclamation mark
                "ÃƒÂ£Ã¢â€šÂ¬Ã‚Â",  # Enumeration comma
                # Closing brackets
                ")",  # Right parenthesis
                "]",  # Right square bracket
                "}",  # Right curly bracket
                "ÃƒÂ¯Ã‚Â¼Ã¢â‚¬Â°",  # Right parenthesis
                "ÃƒÂ£Ã¢â€šÂ¬Ã¢â‚¬Ëœ",  # Right square bracket
                "ÃƒÂ£Ã¢â€šÂ¬Ã¢â‚¬Â¹",  # Right double angle bracket
                "ÃƒÂ£Ã¢â€šÂ¬Ã‚Â",  # Right single quotation mark
                "ÃƒÂ£Ã¢â€šÂ¬Ã‚Â",  # Right corner bracket
                # Connected line symbols
                "ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Å“",  # EN DASH
                "ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â",  # EM DASH
                # Special punctuation
                "Ãƒâ€šÃ‚Â·",  # Middle dot
                "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦",  # Ellipsis
                "Ãƒâ€šÃ‚Â°",  # Degree symbol
                # Slash
                "/",  # Slash
                "ÃƒÂ¯Ã‚Â¼Ã‚Â",  # Fullwidth solidus
                "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â°",  # Per mille sign
            ]
        return False

    @property
    def is_cannot_appear_in_line_end_punctuation(self):
        if self.is_cannot_appear_in_line_end_punctuation_cache is None:
            self.is_cannot_appear_in_line_end_punctuation_cache = (
                self.calc_is_cannot_appear_in_line_end_punctuation()
            )

        return self.is_cannot_appear_in_line_end_punctuation_cache

    def calc_is_cannot_appear_in_line_end_punctuation(self):
        if self.formular:
            return False
        unicode = self.try_get_unicode()
        if not unicode:
            return False
        return unicode in [
            # Opening brackets
            """,  # Left double quotation mark
            "'",  # Left single quotation mark
            "ÃƒÂ£Ã¢â€šÂ¬Ã… ",  # Left double angle bracket
            "ÃƒÂ£Ã¢â€šÂ¬Ã…Â½",  # Left single quotation mark
            # Opening brackets
            "(",  # Left parenthesis
            "[",  # Left square bracket
            "{",  # Left curly bracket
            "ÃƒÂ¯Ã‚Â¼Ã‹â€ ",  # Left parenthesis
            "ÃƒÂ£Ã¢â€šÂ¬Ã‚Â",  # Left square bracket
            "ÃƒÂ£Ã¢â€šÂ¬Ã… ",  # Left double angle bracket
            "ÃƒÂ£Ã¢â€šÂ¬Ã…Â½",  # Left single quotation mark
            # Cannot appear at end of line - combined with closing brackets
            """,  # Right double quotation mark
            "'",  # Right single quotation mark
            "ÃƒÂ£Ã¢â€šÂ¬Ã¢â‚¬Â¹",  # Right double angle bracket
            "ÃƒÂ£Ã¢â€šÂ¬Ã‚Â",  # Right single quotation mark
        ]

    def passthrough(
        self,
    ) -> tuple[list[PdfCharacter], list[PdfCurve], list[PdfForm]]:
        if self.char:
            return [self.char], [], []
        elif self.formular:
            return (
                self.formular.pdf_character,
                self.formular.pdf_curve,
                self.formular.pdf_form,
            )
        elif self.unicode:
            logger.error(f"Cannot passthrough unicode. TypesettingUnit: {self}. ")
            logger.error(f"Cannot passthrough unicode. TypesettingUnit: {self}. ")
            return [], [], []

    @property
    def can_passthrough(self):
        if self.can_passthrough_cache is None:
            self.can_passthrough_cache = self.calc_can_passthrough()

        return self.can_passthrough_cache

    def calc_can_passthrough(self):
        return self.unicode is None

    def calculate_box(self):
        if self.char:
            # Some legacy producers omit the logical character box while
            # retaining a visual box.  Prefer whichever geometry exists and
            # keep a zero-area fallback so one malformed glyph cannot abort
            # the complete document.
            source_box = self.char.box
            if source_box is None and self.char.visual_bbox:
                source_box = self.char.visual_bbox.box
            box = copy.deepcopy(source_box)
            if box is None:
                return Box(0, 0, 0, 0)
            if self.char.visual_bbox and self.char.visual_bbox.box:
                box.y = self.char.visual_bbox.box.y
                box.y2 = self.char.visual_bbox.box.y2

            return box
        elif self.formular:
            if self.formular.box is not None:
                return self.formular.box
            # Broken formula annotations are common in generated forms and
            # contact cards.  Recover their bounds from the actual painted
            # children instead of dereferencing a missing formula box.
            child_boxes = []
            for char in self.formular.pdf_character or []:
                child_box = char.box
                if child_box is None and char.visual_bbox:
                    child_box = char.visual_bbox.box
                if child_box is not None:
                    child_boxes.append(child_box)
            for graphic in [
                *(self.formular.pdf_curve or []),
                *(self.formular.pdf_form or []),
            ]:
                if graphic.box is not None:
                    child_boxes.append(graphic.box)
            if child_boxes:
                return Box(
                    min(box.x for box in child_boxes),
                    min(box.y for box in child_boxes),
                    max(box.x2 for box in child_boxes),
                    max(box.y2 for box in child_boxes),
                )
            return Box(0, 0, 0, 0)
        elif self.unicode:
            char_width = self.font.char_lengths(self.unicode, self.font_size)[0]
            if self.x is None or self.y is None or self.scale is None:
                return Box(0, 0, char_width, self.font_size)
            return Box(self.x, self.y, self.x + char_width, self.y + self.font_size)
        return Box(0, 0, 0, 0)

    @property
    def box(self):
        if not self.box_cache:
            self.box_cache = self.calculate_box()

        return self.box_cache

    @property
    def width(self):
        if self.width_cache is None:
            self.width_cache = self.calc_width()

        return self.width_cache

    def calc_width(self):
        box = self.box
        return box.x2 - box.x

    @property
    def height(self):
        if self.height_cache is None:
            self.height_cache = self.calc_height()

        return self.height_cache

    def calc_height(self):
        box = self.box
        return box.y2 - box.y

    def relocate(
        self,
        x: float,
        y: float,
        scale: float,
    ) -> TypesettingUnit:
        """Relocate and scale the typesetting unit

        Args:
            x: New x position
            y: New y position
            scale: Scale factor

        Returns:
            New relocated and scaled typesetting unit
        """
        if self.char:
            # Create new character object
            new_char = PdfCharacter(
                pdf_character_id=self.char.pdf_character_id,
                char_unicode=self.char.char_unicode,
                box=Box(
                    x=x,
                    y=y,
                    x2=x + self.width * scale,
                    y2=y + self.height * scale,
                ),
                pdf_style=PdfStyle(
                    font_id=self.char.pdf_style.font_id,
                    font_size=self.char.pdf_style.font_size * scale,
                    graphic_state=self.char.pdf_style.graphic_state,
                ),
                scale=scale,
                vertical=self.char.vertical,
                advance=self.char.advance * scale if self.char.advance else None,
                debug_info=self.debug_info,
                xobj_id=self.char.xobj_id,
            )
            new_tu = TypesettingUnit(char=new_char)
            new_tu.try_resue_cache(self)
            return new_tu

        elif self.formular:
            # Create new formula object and recursively relocate child characters
            new_chars = []
            min_x = self.formular.box.x
            min_y = self.formular.box.y

            for char in self.formular.pdf_character:
                # Calculate relative position
                rel_x = char.box.x - min_x
                rel_y = char.box.y - min_y

                visual_rel_x = char.visual_bbox.box.x - min_x
                visual_rel_y = char.visual_bbox.box.y - min_y

                # Create new character object
                new_char = PdfCharacter(
                    pdf_character_id=char.pdf_character_id,
                    char_unicode=char.char_unicode,
                    box=Box(
                        x=x + (rel_x + self.formular.x_offset) * scale,
                        y=y + (rel_y + self.formular.y_offset) * scale,
                        x2=x
                        + (rel_x + (char.box.x2 - char.box.x) + self.formular.x_offset)
                        * scale,
                        y2=y
                        + (rel_y + (char.box.y2 - char.box.y) + self.formular.y_offset)
                        * scale,
                    ),
                    visual_bbox=il_version_1.VisualBbox(
                        box=Box(
                            x=x + (visual_rel_x + self.formular.x_offset) * scale,
                            y=y + (visual_rel_y + self.formular.y_offset) * scale,
                            x2=x
                            + (
                                visual_rel_x
                                + (char.visual_bbox.box.x2 - char.visual_bbox.box.x)
                                + self.formular.x_offset
                            )
                            * scale,
                            y2=y
                            + (
                                visual_rel_y
                                + (char.visual_bbox.box.y2 - char.visual_bbox.box.y)
                                + self.formular.y_offset
                            )
                            * scale,
                        ),
                    ),
                    pdf_style=PdfStyle(
                        font_id=char.pdf_style.font_id,
                        font_size=char.pdf_style.font_size * scale,
                        graphic_state=char.pdf_style.graphic_state,
                    ),
                    scale=scale,
                    vertical=char.vertical,
                    advance=char.advance * scale if char.advance else None,
                    xobj_id=char.xobj_id,
                )
                new_chars.append(new_char)

            # Calculate bounding box from new_chars
            min_x = min(char.visual_bbox.box.x for char in new_chars)
            min_y = min(char.visual_bbox.box.y for char in new_chars)
            max_x = max(char.visual_bbox.box.x2 for char in new_chars)
            max_y = max(char.visual_bbox.box.y2 for char in new_chars)

            new_formula = PdfFormula(
                box=Box(
                    x=min_x,
                    y=min_y,
                    x2=max_x,
                    y2=max_y,
                ),
                pdf_character=new_chars,
                x_offset=self.formular.x_offset * scale,
                y_offset=self.formular.y_offset * scale,
                x_advance=self.formular.x_advance * scale,
            )

            # Handle contained curves
            new_curves = []
            for curve in self.formular.pdf_curve:
                new_curve = self._transform_curve_for_relocation(
                    curve,
                    self.formular.box.x,
                    self.formular.box.y,
                    x,
                    y,
                    scale,
                )
                new_curves.append(new_curve)
            new_formula.pdf_curve = new_curves

            # Handle contained forms
            new_forms = []
            for form in self.formular.pdf_form:
                new_form = self._transform_form_for_relocation(
                    form, self.formular.box.x, self.formular.box.y, x, y, scale
                )
                new_forms.append(new_form)
            new_formula.pdf_form = new_forms

            update_formula_data(new_formula)

            new_tu = TypesettingUnit(formular=new_formula)
            new_tu.try_resue_cache(self)
            return new_tu

        elif self.unicode:
            # For Unicode, store position info and create new TypesettingUnit
            new_unit = TypesettingUnit(
                unicode=self.unicode,
                font=self.font,
                arabic_fallback_font=self.arabic_fallback_font,
                original_font=self.original_font,
                font_size=self.font_size * scale,
                style=self.style,
                xobj_id=self.xobj_id,
                debug_info=self.debug_info,
            )
            new_unit.x = x
            new_unit.y = y
            new_unit.scale = scale
            new_unit.try_resue_cache(self)
            return new_unit

    def _transform_curve_for_relocation(
        self,
        curve,
        original_formula_x: float,
        original_formula_y: float,
        new_x: float,
        new_y: float,
        scale: float,
    ):
        """Transform a curve for formula relocation."""
        import copy

        new_curve = copy.deepcopy(curve)

        if new_curve.box:
            # Calculate relative position to formula's original position (same as chars)
            rel_x = new_curve.box.x - original_formula_x
            rel_y = new_curve.box.y - original_formula_y

            # Apply same transformation as characters
            new_curve.box = Box(
                x=new_x + (rel_x + self.formular.x_offset) * scale,
                y=new_y + (rel_y + self.formular.y_offset) * scale,
                x2=new_x
                + (
                    rel_x
                    + (new_curve.box.x2 - new_curve.box.x)
                    + self.formular.x_offset
                )
                * scale,
                y2=new_y
                + (
                    rel_y
                    + (new_curve.box.y2 - new_curve.box.y)
                    + self.formular.y_offset
                )
                * scale,
            )

        # Set relocation transform instead of modifying original CTM
        translation_x = (
            new_x + self.formular.x_offset * scale - original_formula_x * scale
        )
        translation_y = (
            new_y + self.formular.y_offset * scale - original_formula_y * scale
        )

        # Create relocation transformation matrix
        from babeldoc.format.pdf.document_il.utils.matrix_helper import (
            create_translation_and_scale_matrix,
        )

        relocation_matrix = create_translation_and_scale_matrix(
            translation_x, translation_y, scale
        )
        new_curve.relocation_transform = list(relocation_matrix)

        return new_curve

    def _transform_form_for_relocation(
        self,
        form,
        original_formula_x: float,
        original_formula_y: float,
        new_x: float,
        new_y: float,
        scale: float,
    ):
        """Transform a form for formula relocation."""
        import copy

        new_form = copy.deepcopy(form)

        if new_form.box:
            # Calculate relative position to formula's original position (same as chars)
            rel_x = new_form.box.x - original_formula_x
            rel_y = new_form.box.y - original_formula_y

            # Apply same transformation as characters
            new_form.box = Box(
                x=new_x + (rel_x + self.formular.x_offset) * scale,
                y=new_y + (rel_y + self.formular.y_offset) * scale,
                x2=new_x
                + (rel_x + (new_form.box.x2 - new_form.box.x) + self.formular.x_offset)
                * scale,
                y2=new_y
                + (rel_y + (new_form.box.y2 - new_form.box.y) + self.formular.y_offset)
                * scale,
            )

        # Set relocation transform instead of modifying original matrices
        translation_x = (
            new_x + self.formular.x_offset * scale - original_formula_x * scale
        )
        translation_y = (
            new_y + self.formular.y_offset * scale - original_formula_y * scale
        )

        # Create relocation transformation matrix
        from babeldoc.format.pdf.document_il.utils.matrix_helper import (
            create_translation_and_scale_matrix,
        )

        relocation_matrix = create_translation_and_scale_matrix(
            translation_x, translation_y, scale
        )
        new_form.relocation_transform = list(relocation_matrix)

        return new_form

    def render(
        self,
    ) -> tuple[list[PdfCharacter], list[PdfCurve], list[PdfForm]]:
        """Render the typesetting unit to PdfCharacter list

        Returns:
            PdfCharacter list
        """
        if self.can_passthrough:
            return self.passthrough()
        elif self.unicode:
            assert self.x is not None, (
                "x position must be set, should be set by `relocate`"
            )
            assert self.y is not None, (
                "y position must be set, should be set by `relocate`"
            )
            assert self.scale is not None, (
                "scale must be set, should be set by `relocate`"
            )
            x = self.x
            y = self.y

            # Calculate character width
            char_width = self.width

            # A generated target character must never be emitted as glyph 0.
            # A .notdef glyph corrupts both the visual PDF and its text layer;
            # the resolver must choose a valid font before this point.
            ch_code = ord(self.unicode) if self.unicode else 0
            if 0xE000 <= ch_code <= 0xF8FF:
                # PUA codepoints are defined by the source font, not Unicode.
                # Never emit one into the Arabic target font: map it to the
                # portable standard bullet even when a source font happened to
                # advertise a glyph for the PUA codepoint.
                self.unicode = '•'
                ch_code = 0x2022

            glyph_id = self.font.has_glyph(ch_code) if self.font is not None else 0
            if (glyph_id == 0 or glyph_id is None) and self.arabic_fallback_font is not None:
                fallback_glyph_id = self.arabic_fallback_font.has_glyph(ch_code)
                if fallback_glyph_id:
                    logger.warning(
                        "Recovered target glyph %r (U+%04X) from %s using Arabic fallback %s",
                        self.unicode,
                        ch_code,
                        self.font_id,
                        self.arabic_fallback_font.font_id,
                    )
                    self.font = self.arabic_fallback_font
                    self.font_id = self.arabic_fallback_font.font_id
                    glyph_id = fallback_glyph_id

            if self.font is None:
                logger.error("Target glyph resolver returned no font for %r (U+%04X)", self.unicode, ch_code)
                raise RuntimeError(
                    f"No target font for '{self.unicode}' (U+{ch_code:04X}); refusing .notdef output"
                )
            elif glyph_id == 0 or glyph_id is None:
                logger.error("Target font %s lacks %r (U+%04X)", self.font_id, self.unicode, ch_code)
                raise RuntimeError(
                    f"Font '{self.font_id}' lacks '{self.unicode}' (U+{ch_code:04X}); refusing .notdef output"
                )

            new_char = PdfCharacter(
                pdf_character_id=glyph_id,
                char_unicode=self.unicode,
                box=Box(
                    x=x,  # Use stored x position
                    y=y,
                    x2=x + char_width,
                    y2=y + self.font_size,
                ),
                pdf_style=PdfStyle(
                    font_id=self.font_id,
                    font_size=self.font_size,
                    graphic_state=self.style.graphic_state,
                ),
                scale=self.scale,
                vertical=False,
                advance=char_width,
                xobj_id=self.xobj_id,
                debug_info=self.debug_info,
            )
            return [new_char], [], []
        else:
            logger.error(f"Unknown typesetting unit. TypesettingUnit: {self}. ")
            logger.error(f"Unknown typesetting unit. TypesettingUnit: {self}. ")
            return [], [], []


class Typesetting:
    stage_name = "Typesetting"

    def __init__(self, translation_config: TranslationConfig):
        self.font_mapper = FontMapper(translation_config)
        self.translation_config = translation_config
        self.lang_code = self.translation_config.lang_out.upper()
        # Ensure detailed_logger attribute exists to avoid attribute access errors
        self.detailed_logger = None
        self.is_cjk = (
            # Why zh-CN/zh-HK/zh-TW here but not zh-Hans and so on?
            
            ("ZH" in self.lang_code)  # C
            or ("JA" in self.lang_code)
            or ("JP" in self.lang_code)  # J
            or ("KR" in self.lang_code)  # K
            or ("CN" in self.lang_code)
            or ("HK" in self.lang_code)
            or ("TW" in self.lang_code)
        )
        # Arabic needs extra line spacing due to diacritical marks (harakat) above/below letters
        self.is_arabic = (
            ("AR" in self.lang_code)
            or ("ARABIC" in self.lang_code)
            or ("ARA" in self.lang_code)
        )
        # A page is traversed twice (preprocessing and rendering). Table geometry
        # must be mirrored exactly once, before scale computation.
        self._rtl_mirrored_table_pages: set[int] = set()
        # ``Page`` is a slots dataclass, so per-page idempotency state belongs
        # on the typesetter rather than being attached to the IL object.
        self._vertical_collision_resolved_pages: set[int] = set()
        self._rendered_collision_resolved_pages: set[int] = set()
        self._heading_clearance_resolved_pages: set[int] = set()
        self._inline_highlights_anchored_pages: set[int] = set()
        self._translated_code_band_keys: set[tuple[int, int]] = set()

    def _is_centered_display_text(self, paragraph, page) -> bool:
        """Recognize large centered titles even when labeled as plain text."""
        if not self.is_arabic or not page.cropbox or not page.cropbox.box:
            return False
        box = paragraph.box
        style = paragraph.pdf_style
        label = (paragraph.layout_label or "").lower()
        if (
            not box or not style or (style.font_size or 0) < 20
            or paragraph.vertical or label not in {"title", "heading", "section_heading", "plain text"}
        ):
            return False
        crop = page.cropbox.box
        width = crop.x2 - crop.x
        # Use the source text's own box, rather than assuming every large heading is centered.
        return (
            0 < box.x2 - box.x < width * 0.85
            and abs((box.x + box.x2) - (crop.x + crop.x2)) <= width * 0.03
            and not self._is_inside_table_or_figure_region(box, page)
        )

    def _arabic_publication_font_size(
        self,
        paragraph: il_version_1.PdfParagraph,
        source_size: float,
        *,
        centered_display: bool = False,
    ) -> float:
        """Return a stable, readable Arabic type scale for translated text.

        Source PDFs often use several nearly-identical Latin sizes (and, on
        presentation PDFs, individual words can inherit very large or very
        small sizes).  Mirroring that noise into Arabic is the main reason a
        translated page looks uneven.  Arabic publication typography instead
        uses a small role-based scale: one body size, a modest heading step,
        and intentionally smaller table/note text.  Layout still expands a
        box before it reduces this size, so this normalisation cannot trade
        visual consistency for clipped text.
        """
        if not self.is_arabic:
            return source_size

        label = (paragraph.layout_label or "").lower()
        if centered_display:
            return min(source_size, paragraph.pdf_style.font_size)

        if "fallback_line" in label or (
            any(marker in label for marker in ("figure", "chart", "axis", "diagram"))
            and "caption" not in label
        ):
            if 0 < source_size <= 9.5:
                return round(min(9.5, source_size * 1.05), 2)

        # Preserve the source hierarchy, but keep it narrow and predictable.
        if any(marker in label for marker in ("title", "heading", "section")):
            target_size = 14.0
        elif any(marker in label for marker in ("caption", "footnote", "table_footnote")):
            target_size = 9.5
        elif "table" in label:
            target_size = 9.5
        else:
            target_size = 11.5

        # Noto Kurrent's Arabic has a smaller optical footprint than common
        # Latin source fonts.  This is the final rendered target size, not a
        # multiplier of a noisy source style, so every body paragraph matches.
        return target_size

    @staticmethod
    def _contact_frame_bounds(
        paragraph: il_version_1.PdfParagraph, page: il_version_1.Page
    ) -> tuple[float, float] | None:
        """Find the inside edges of a drawn frame enclosing a contact row."""
        row = paragraph.box
        if row is None or "contact_card" not in (paragraph.layout_label or "").lower():
            return None

        tolerance = 2.0
        edges = [
            curve.box for curve in page.pdf_curve
            if curve.box is not None and curve.xobj_id == paragraph.xobj_id
        ]
        sides = [
            edge for edge in edges
            if edge.x2 - edge.x <= tolerance
            and edge.y <= row.y + tolerance
            and edge.y2 >= row.y2 - tolerance
            and edge.y2 - edge.y > 2 * tolerance
        ]
        rules = [
            edge for edge in edges
            if edge.y2 - edge.y <= tolerance and edge.x2 - edge.x > 2 * tolerance
        ]
        frames = []
        for left in sides:
            if left.x2 > row.x + tolerance:
                continue
            for right in sides:
                if right.x < row.x2 - tolerance or right.x - left.x2 <= 8:
                    continue
                if abs(left.y - right.y) > tolerance or abs(left.y2 - right.y2) > tolerance:
                    continue
                if all(
                    any(
                        abs(rule.x - left.x2) <= tolerance
                        and abs(rule.x2 - right.x) <= tolerance
                        and abs((rule.y + rule.y2) / 2 - height) <= tolerance
                        for rule in rules
                    )
                    for height in (left.y, left.y2)
                ):
                    frames.append((left.x2, right.x))
        return min(frames, key=lambda bounds: bounds[1] - bounds[0], default=None)

    def _expand_structured_text_boxes(self, page: il_version_1.Page) -> None:
        """Provide full-width lanes for structured records and short headings.

        Structured blocks are identified from their content, not from a
        document-specific page number.  Their English labels are frequently
        painted in narrow fragments even though the surrounding section is a
        full-width instruction area.  Expanding only these labelled records
        prevents Arabic labels from shrinking and gives wrapped descriptions
        a stable alignment while leaving tables and diagrams untouched.
        """
        if not page.cropbox or not page.cropbox.box:
            return
        page_box = page.cropbox.box
        page_width = page_box.x2 - page_box.x
        content_left = page_box.x + page_width * 0.055
        content_right = page_box.x2 - page_width * 0.055
        for paragraph in page.pdf_paragraph or []:
            if not paragraph.box:
                continue
            label = (paragraph.layout_label or "").lower()
            is_structured = any(
                marker in label for marker in ("procedure_step", "contact_card")
            )
            is_narrow_heading = (
                "heading" in label
                and paragraph.box.x2 - paragraph.box.x < page_width * 0.60
            )
            if not (is_structured or is_narrow_heading):
                continue
            if "fallback_line" in label or self._is_inside_table_or_figure_region(paragraph.box, page):
                continue
            if self._is_fixed_table_paragraph(paragraph) and "table" in label:
                continue
            if self._is_arabic_output() and (frame := self._contact_frame_bounds(paragraph, page)):
                paragraph.box = Box(frame[0] + 4, paragraph.box.y, frame[1] - 4, paragraph.box.y2)
                continue
            paragraph.box = Box(
                min(paragraph.box.x, content_left),
                paragraph.box.y,
                max(paragraph.box.x2, content_right),
                paragraph.box.y2,
            )

    def _reserve_formula_lane(self, page: il_version_1.Page) -> None:
        """Keep Arabic prose out of a left-side equation/value lane.

        Technical PDFs often paint calculations in a narrow LTR strip while
        the explanatory text occupies the remaining RTL column.  Treating the
        strip as ordinary text makes later reflow cross an implicit gutter.
        This derives the gutter from isolated numeric/formula records instead
        of relying on a page number or hard-coded coordinates.
        """
        if not self._is_arabic_output() or not page.cropbox or not page.cropbox.box:
            return
        page_box = page.cropbox.box
        width = page_box.x2 - page_box.x
        candidates = []
        for paragraph in page.pdf_paragraph or []:
            if not paragraph.box or self._is_paragraph_inside_table(paragraph, page):
                continue
            text = (paragraph.unicode or "").strip()
            label = (paragraph.layout_label or "").lower()
            is_ltr_value = bool(re.fullmatch(r"[A-Za-z0-9\s./=+\-()]+", text)) and any(c.isdigit() for c in text)
            if ("formula" in label or is_ltr_value) and paragraph.box.x2 <= page_box.x + width * 0.48:
                candidates.append(paragraph.box.x2)
        if len(candidates) < 2:
            return
        lane_right = max(candidates) + width * 0.035
        for paragraph in page.pdf_paragraph or []:
            if not paragraph.box or self._is_paragraph_inside_table(paragraph, page):
                continue
            text = (paragraph.unicode or "").strip()
            label = (paragraph.layout_label or "").lower()
            is_formula = "formula" in label or (bool(re.fullmatch(r"[A-Za-z0-9\s./=+\-()]+", text)) and any(c.isdigit() for c in text))
            if not is_formula and paragraph.box.x < lane_right:
                paragraph.box.x = min(lane_right, paragraph.box.x2 - width * 0.12)

    def preprocess_document(self, document: il_version_1.Document, pbar):
        """Preprocess document - calculate optimal scale for each paragraph and cache the result"""
        all_scales: list[float] = []
        all_paragraphs: list[il_version_1.PdfParagraph] = []
        centered_display_ids: set[int] = set()

        for page in document.page:
            pbar.advance()
            self._mirror_table_grids_for_rtl(page)
            # Build font dictionary for current page rendering logic
            fonts: dict[
                str | int,
                il_version_1.PdfFont | dict[str, il_version_1.PdfFont],
            ] = {f.font_id: f for f in page.pdf_font if f.font_id}
            page_fonts = {f.font_id: f for f in page.pdf_font if f.font_id}
            for k, v in self.font_mapper.fontid2font.items():
                fonts[k] = v
            for xobj in page.pdf_xobject:
                if xobj.xobj_id is not None:
                    fonts[xobj.xobj_id] = page_fonts.copy()
                    for font in xobj.pdf_font:
                        if (
                            xobj.xobj_id in fonts
                            and isinstance(fonts[xobj.xobj_id], dict)
                            and font.font_id
                        ):
                            fonts[xobj.xobj_id][font.font_id] = font

            # Finalize table geometry before measuring text. Adjusting cell
            # heights after text placement creates the jagged/overlapping rows
            # seen in the Qatar IBAN sample.
            self._synchronize_table_row_heights(page)
            # Balance table row widths before optimal scale calculation
            self._balance_table_row_widths(page, fonts)
            self._reserve_formula_lane(page)
            # Labels such as ``Step 11`` are often extracted into a box only
            # wide enough for the short English source.  Give every structured
            # record the full text lane before Arabic measurement; otherwise
            # the label is needlessly reduced to a microscopic scale.
            self._expand_structured_text_boxes(page)
            # A malformed source frequently gives a section label and its
            # following body text intersecting fallback boxes.  Reserve the
            # label's own vertical lane before either paragraph is measured;
            # otherwise body expansion begins inside the label and the two
            # Arabic runs are committed on the same baseline.
            self._reserve_heading_clearance_from_tables(page)
            self._resolve_vertical_paragraph_collisions(page)

            # Preprocess each paragraph
            for paragraph in page.pdf_paragraph:
                all_paragraphs.append(paragraph)
                if self._is_centered_display_text(paragraph, page):
                    centered_display_ids.add(id(paragraph))
                unit_count = 0
                try:
                    typesetting_units = self._deduplicate_artificial_header_units(
                        paragraph, page, self.create_typesetting_units(paragraph, fonts, page=page)
                    )
                    unit_count = len(typesetting_units)
                    for unit in typesetting_units:
                        if unit.formular:
                            unit_count += len(unit.formular.pdf_character) - 1

                    # Get optimal scale value (if all can passthrough, scale = 1.0)
                    if all(unit.can_passthrough for unit in typesetting_units):
                        paragraph.optimal_scale = 1.0
                    else:
                        # Get optimal scale factor
                        optimal_scale = self._get_optimal_scale(
                            paragraph, page, typesetting_units
                        )
                        paragraph.optimal_scale = optimal_scale
                except Exception as e:
                    # If preprocessing paragraph fails, default scale is 1.0
                    logger.warning(f"Preprocessing paragraph failed: {e}")
                    paragraph.optimal_scale = 1.0

                if paragraph.optimal_scale is not None:
                    all_scales.extend([paragraph.optimal_scale] * unit_count)

        # Get optimal scale factor, estimate the mode of the distribution as starting point for scale selection
        if all_scales:
            try:
                modes = statistics.multimode(all_scales)
                mode_scale = min(modes)
            except statistics.StatisticsError:
                logger.warning(
                    "Could not find a mode for paragraph scales. Falling back to median."
                )
                mode_scale = statistics.median(all_scales)
            # Too many times using a smaller scale will affect readability, so use the mode as the upper limit
            # Do NOT clamp titles, headers, captions, or table content to body text mode_scale
            for paragraph in all_paragraphs:
                layout_label = (paragraph.layout_label or "").lower()
                is_hierarchy_exempt = any(
                    marker in layout_label
                    for marker in ["title", "heading", "caption", "table", "footnote", "fallback_line"]
                )
                if not is_hierarchy_exempt and id(paragraph) not in centered_display_ids:
                    if (
                        paragraph.optimal_scale is not None
                        and paragraph.optimal_scale > mode_scale
                    ):
                        paragraph.optimal_scale = mode_scale
        else:
            logger.error(
                "document_scales is empty, there seems no paragraph in this PDF"
            )

    def shape_arabic_text(self, text: str) -> str:
        """Shape Arabic text for proper glyph joining (ligatures, contextual forms).

        IMPORTANT: This function only applies arabic_reshaper for character joining.
        It does NOT apply bidi get_display() reordering, because the PDF layout engine
        handles character positioning via explicit (x,y) coordinates.

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
                    reshaped_text = reshaper.reshape(text)
                    # DO NOT call get_display() - PDF layout engine handles positioning
                    return reshaped_text
                else:
                    return text
            except Exception as e:
                logger.warning(f"Failed to shape Arabic text: {e}")
                return text

        return text

    _SYMBOL_GLYPH_FALLBACKS = {
        "\u2610": "\u25a1",  # ☐ -> □
        "\u25fb": "\u25a1",  # ◻ -> □
        "\u25fd": "\u25a1",  # ◽ -> □
        "\u2611": "\u2713",  # ☑ -> ✓
        "\u2714": "\u2713",  # ✔ -> ✓
        "\u2612": "\u22a0",  # ☒ -> ⊠
        "\u2715": "\u00d7",  # ✕ -> ×
        "\u2717": "\u00d7",  # ✗ -> ×
    }

    def _preflight_arabic_run(self, text: str, font: pymupdf.Font) -> None:
        """Validate contextual shaping against the actual embedded target font.

        HarfBuzz produces glyphs in visual order, while this engine performs
        visual ordering only after line breaking.  The glyph plan is therefore
        used as a validation/diagnostic pass here, not emitted directly.
        """
        if not self.is_arabic or not text or not getattr(font, "font_path", None):
            return
        # Mixed paragraphs combine Arabic runs with Latin labels and symbols
        # (such as checkbox glyphs or stars) that resolve_unicode maps to other
        # target fonts later.  Only validate Arabic codepoints against the
        # Arabic face while keeping word-boundary spaces for contextual shaping.
        preflight_text = "".join(
            char if is_arabic_codepoint(ord(char)) else " "
            for char in text
            if unicodedata.category(char) not in {"Cc", "Cf"}
        )
        if not preflight_text.strip():
            return
        try:
            shaped = shape_arabic_run(preflight_text, font.font_path)
        except OSError as error:
            logger.warning("HarfBuzz font preflight skipped for %s: %s", font.font_id, error)
            return
        if shaped is None:
            return
        if shaped.has_missing_glyphs:
            raise RuntimeError(
                f"HarfBuzz found missing glyphs in Arabic run for target font {font.font_id}: {text!r}"
            )
        logger.debug(
            "HarfBuzz shaped Arabic run: font=%s glyphs=%d clusters=%d",
            font.font_id,
            len(shaped.glyphs),
            len({glyph.cluster for glyph in shaped.glyphs}),
        )

    def _commit_layout_to_paragraph(
        self,
        paragraph: il_version_1.PdfParagraph,
        page: il_version_1.Page,
        typeset_units: list[TypesettingUnit],
        scale: float,
    ):
        """Helper to write laid-out units into paragraph composition, curves, and forms."""
        paragraph.scale = scale
        paragraph.pdf_paragraph_composition = []
        code_line_boxes: dict[int, list[Box]] = {}
        for unit in typeset_units:
            if unit.is_drawn_list_bullet:
                # The paragraph can still move during margin and collision
                # handling. Keep the bullet curve owned by its text.
                paragraph.pdf_paragraph_composition.append(
                    PdfParagraphComposition(pdf_formula=unit.formular)
                )
                continue
            if getattr(unit, "presentation_role", None) == "procedure_code" and unit.box:
                code_line_boxes.setdefault(getattr(unit, "line_id", -1), []).append(unit.box)
            chars, curves, forms = unit.render()
            for char in chars:
                paragraph.pdf_paragraph_composition.append(
                    PdfParagraphComposition(pdf_character=char),
                )
            for curve in curves:
                page.pdf_curve.append(curve)
            for form in forms:
                page.pdf_form.append(form)

        # Code/result rows are semantic content, not decorative leftovers.
        # Draw a controlled neutral band behind the final Arabic glyph bounds
        # so samples, IBANs and MOD-97 results read as one LTR unit.
        for line_id, boxes in code_line_boxes.items():
            key = (id(paragraph), line_id)
            if key in self._translated_code_band_keys:
                continue
            self._translated_code_band_keys.add(key)
            x1 = max(paragraph.box.x, min(box.x for box in boxes) - 5.0)
            x2 = min(paragraph.box.x2, max(box.x2 for box in boxes) + 5.0)
            y1 = min(box.y for box in boxes) - 2.0
            y2 = max(box.y2 for box in boxes) + 2.0
            if x2 > x1 and y2 > y1:
                page.pdf_rectangle.append(
                    il_version_1.PdfRectangle(
                        box=Box(x1, y1, x2, y2),
                        graphic_state=GRAY80,
                        fill_background=True,
                        # This sentinel is deliberately rendered in normal
                        # production output by PDFCreator, unlike debug boxes.
                        debug_info="translated_code_band",
                        xobj_id=paragraph.xobj_id,
                        render_order=15,
                    )
                )

    @staticmethod
    def _shift_laid_out_units_vertically(
        units: list[TypesettingUnit], delta: float
    ) -> list[TypesettingUnit]:
        """Move a completed candidate layout without changing its scale."""
        if delta <= 0.0:
            return units
        shifted: list[TypesettingUnit] = []
        for unit in units:
            if not unit.box:
                shifted.append(unit)
                continue
            relocated = unit.relocate(unit.box.x, unit.box.y + delta, 1.0)
            for attribute in ("line_id", "presentation_role"):
                if hasattr(unit, attribute):
                    setattr(relocated, attribute, getattr(unit, attribute))
            shifted.append(relocated)
        return shifted

    def _find_optimal_scale_and_layout(
        self,
        paragraph: il_version_1.PdfParagraph,
        page: il_version_1.Page,
        typesetting_units: list[TypesettingUnit],
        initial_scale: float = 1.0,
        use_english_line_break: bool = True,
        apply_layout: bool = False,
    ) -> tuple[float, list[TypesettingUnit] | None]:
        """Find the optimal scale factor and apply layout if needed

        Args:
            paragraph: Paragraph object
            page: Page object
            typesetting_units: List of typesetting units
            initial_scale: Starting scale factor
            use_english_line_break: Whether to use English line breaking rules
            apply_layout: Whether to apply layout and update the paragraph

        Returns:
            tuple[float, list[TypesettingUnit]]: (optimal scale factor, laid out typesetting units)
        """
        if not paragraph.box:
            return initial_scale, None

        box = paragraph.box
        scale = initial_scale
        layout_units = partial(
            self._layout_typesetting_units,
            centered_display=self._is_centered_display_text(paragraph, page),
        )
        # Arabic needs extra line spacing (1.5) due to diacritical marks above/below letters
        # CJK also needs 1.5 due to character height
        # Other languages use 1.3
        if self.is_cjk:
            line_skip = 1.50
        elif self.is_arabic:
            line_skip = 1.50  # Arabic needs extra space for diacritical marks (harakat)
        else:
            line_skip = 1.3

        # Role-based minimum scale floors and relief floors to preserve typographic hierarchy
        layout_label = (paragraph.layout_label or "").lower()
        is_figure_or_fallback = (
            "fallback_line" in layout_label
            or self._is_inside_table_or_figure_region(box, page, figures_only=True)
        )
        if is_figure_or_fallback:
            line_skip = 1.25
            min_scale = 0.60
            relief_floor = 0.52
        elif "title" in layout_label or "heading" in layout_label:
            min_scale = 0.90  # Headings normal floor
            relief_floor = 0.82  # Relief floor if expansion is blocked
        elif "contact_card" in layout_label:
            # A contact rectangle is a structured field block, not a footnote.
            # It must remain readable even when its source box is oversized.
            min_scale = 0.86
            relief_floor = 0.76
        elif "procedure_step" in layout_label:
            # Keep a numbered instruction and its protected formula legible.
            min_scale = 0.80
            relief_floor = 0.70
        elif "caption" in layout_label:
            min_scale = 0.82
            relief_floor = 0.75  # Relief floor if expansion is blocked
        elif "table_footnote" in layout_label or "footnote" in layout_label:
            min_scale = 0.75
            relief_floor = 0.70
        elif "table" in layout_label:
            min_scale = 0.65
            relief_floor = 0.58
        else:
            # Text is expanded/reflowed before reaching this relief floor.
            # A readable body should never become a microscopic last resort.
            min_scale = 0.82
            relief_floor = 0.76

        best_typeset_units = None
        best_scale = scale
        final_typeset_units = None

        # Compute safe inter-paragraph separation margin based on unit font height
        unit_heights = [u.height for u in typesetting_units if u.height]
        try:
            unit_h = statistics.median(unit_heights) if unit_heights else 10.0
        except Exception:
            unit_h = 10.0
        safety_margin = max(8.0, unit_h * 0.85)

        # Try initial layout at current scale
        try:
            typeset_units, all_units_fit = layout_units(
                typesetting_units,
                box,
                scale,
                line_skip,
                paragraph,
                use_english_line_break,
            )
            if typeset_units:
                best_typeset_units = typeset_units
                best_scale = scale

            if all_units_fit:
                if apply_layout:
                    self._commit_layout_to_paragraph(paragraph, page, typeset_units, scale)
                    final_typeset_units = typeset_units
                return scale, final_typeset_units
        except Exception:
            pass

        # 1. On overflow, attempt box expansion via get_max_bottom_space FIRST,
        # before any scale reduction — this preserves full-size type for titles/captions when free space exists below.
        if not is_figure_or_fallback:
            try:
                min_avail_y = self.get_max_bottom_space(box, page) + safety_margin
                if min_avail_y < box.y:
                    expanded_box = Box(x=box.x, y=min_avail_y, x2=box.x2, y2=box.y2)
                    box = expanded_box
                    if apply_layout:
                        paragraph.box = expanded_box
                    typeset_units, all_units_fit = layout_units(
                        typesetting_units,
                        box,
                        scale,
                        line_skip,
                        paragraph,
                        use_english_line_break,
                    )
                    if typeset_units:
                        best_typeset_units = typeset_units
                        best_scale = scale
                    if all_units_fit:
                        if apply_layout:
                            self._commit_layout_to_paragraph(paragraph, page, typeset_units, scale)
                            final_typeset_units = typeset_units
                        return scale, final_typeset_units
            except Exception as e:
                logger.debug(f"Error expanding bottom space: {e}")

        # 2. Only if expansion is blocked, fall into progressive scale reduction down to relief floor
        while scale > relief_floor:
            scale -= 0.05
            if scale < relief_floor:
                scale = relief_floor

            try:
                typeset_units, all_units_fit = layout_units(
                    typesetting_units,
                    box,
                    scale,
                    line_skip,
                    paragraph,
                    use_english_line_break,
                )
                if typeset_units:
                    best_typeset_units = typeset_units
                    best_scale = scale

                if all_units_fit:
                    if apply_layout:
                        self._commit_layout_to_paragraph(paragraph, page, typeset_units, scale)
                        final_typeset_units = typeset_units
                    return scale, final_typeset_units

                # Re-check bottom space expansion at this reduced scale
                if not is_figure_or_fallback:
                    min_avail_y = self.get_max_bottom_space(box, page) + safety_margin
                    if min_avail_y < box.y:
                        expanded_box = Box(x=box.x, y=min_avail_y, x2=box.x2, y2=box.y2)
                        box = expanded_box
                        if apply_layout:
                            paragraph.box = expanded_box
                        typeset_units, all_units_fit = layout_units(
                            typesetting_units,
                            box,
                            scale,
                            line_skip,
                            paragraph,
                            use_english_line_break,
                        )
                        if typeset_units:
                            best_typeset_units = typeset_units
                            best_scale = scale
                        if all_units_fit:
                            if apply_layout:
                                self._commit_layout_to_paragraph(paragraph, page, typeset_units, scale)
                                final_typeset_units = typeset_units
                            return scale, final_typeset_units
            except Exception:
                pass

            if scale <= relief_floor:
                break

        # Fallback with use_english_line_break=False if initial try used it
        if use_english_line_break:
            res_scale, res_units = self._find_optimal_scale_and_layout(
                paragraph,
                page,
                typesetting_units,
                initial_scale,
                use_english_line_break=False,
                apply_layout=apply_layout,
            )
            if res_units is not None and len(res_units) > 0:
                return res_scale, res_units

        # 3. If the loop still can't fit the text, NEVER return None.
        # Track best_typeset_units across iterations, expand paragraph.box.y to encompass
        # the best-effort layout, and populate the composition at relief_floor/min_scale.
        # The paragraph must always be formatted.
        if best_typeset_units is not None and len(best_typeset_units) > 0:
            min_unit_y = min((u.box.y for u in best_typeset_units if u.box and u.box.y is not None), default=box.y)
            safe_bottom = self.get_max_bottom_space(box, page) + safety_margin
            if min_unit_y < safe_bottom:
                delta = safe_bottom - min_unit_y
                best_typeset_units = self._shift_laid_out_units_vertically(
                    best_typeset_units, delta
                )
                min_unit_y = safe_bottom
            if min_unit_y < paragraph.box.y:
                paragraph.box = Box(
                    x=paragraph.box.x,
                    y=max(min_unit_y, safe_bottom),
                    x2=paragraph.box.x2,
                    y2=paragraph.box.y2,
                )
            if apply_layout:
                self._commit_layout_to_paragraph(paragraph, page, best_typeset_units, best_scale)
                final_typeset_units = best_typeset_units
            return best_scale, final_typeset_units

        # Emergency layout at relief_floor with use_english_line_break=False
        try:
            typeset_units, _ = layout_units(
                typesetting_units,
                box,
                relief_floor,
                line_skip,
                paragraph,
                use_english_line_break=False,
            )
            if typeset_units:
                min_unit_y = min((u.box.y for u in typeset_units if u.box and u.box.y is not None), default=box.y)
                safe_bottom = self.get_max_bottom_space(box, page) + safety_margin
                if min_unit_y < safe_bottom:
                    delta = safe_bottom - min_unit_y
                    typeset_units = self._shift_laid_out_units_vertically(
                        typeset_units, delta
                    )
                    min_unit_y = safe_bottom
                if min_unit_y < paragraph.box.y:
                    paragraph.box = Box(
                        x=paragraph.box.x,
                        y=max(min_unit_y, safe_bottom),
                        x2=paragraph.box.x2,
                        y2=paragraph.box.y2,
                    )
                if apply_layout:
                    self._commit_layout_to_paragraph(paragraph, page, typeset_units, relief_floor)
                return relief_floor, typeset_units
        except Exception:
            pass

        return min_scale, final_typeset_units

    def _get_optimal_scale(
        self,
        paragraph: il_version_1.PdfParagraph,
        page: il_version_1.Page,
        typesetting_units: list[TypesettingUnit],
        use_english_line_break: bool = True,
    ) -> float:
        """Get optimal scale factor for paragraph, without applying layout"""
        scale, _ = self._find_optimal_scale_and_layout(
            paragraph,
            page,
            typesetting_units,
            1.0,
            use_english_line_break,
            apply_layout=False,
        )
        return scale

    def retypeset_with_precomputed_scale(
        self,
        paragraph: il_version_1.PdfParagraph,
        page: il_version_1.Page,
        typesetting_units: list[TypesettingUnit],
        precomputed_scale: float,
        use_english_line_break: bool = True,
    ):
        """Use precomputed scale factor to layout typesetting units"""
        if not paragraph.box:
            return

        # Using the precomputed scale factor to layout typesetting units
        self._find_optimal_scale_and_layout(
            paragraph,
            page,
            typesetting_units,
            precomputed_scale,
            use_english_line_break,
            apply_layout=True,
        )

    def typesetting_document(self, document: il_version_1.Document):
        # Add detailed logging at the start
        if self.detailed_logger:
            self.detailed_logger.log_step("Typesetting Started")
        
        # Batch preprocess document - calculate optimal scale for each paragraph
        if self.translation_config.progress_monitor:
            with self.translation_config.progress_monitor.stage_start(
                self.stage_name,
                len(document.page) * 2,
            ) as pbar:
                # Preprocess - calculate optimal scale factor for each paragraph
                self.preprocess_document(document, pbar)

                for page_idx, page in enumerate(document.page):
                    self.translation_config.raise_if_cancelled()
                    
                    # Add detailed logging for each page
                    if self.detailed_logger:
                        self.detailed_logger.log_step(
                            f"Typesetting Page {page_idx + 1}",
                            f"Paragraphs to typeset: {len(page.pdf_paragraph) if hasattr(page, 'pdf_paragraph') else 0}"
                        )
                    
                    self.render_page(page)
                    pbar.advance()
        else:
            for page_idx, page in enumerate(document.page):
                self.translation_config.raise_if_cancelled()
                
                # Add detailed logging for each page
                if self.detailed_logger:
                    self.detailed_logger.log_step(
                        f"Typesetting Page {page_idx + 1}",
                        f"Paragraphs to typeset: {len(page.pdf_paragraph) if hasattr(page, 'pdf_paragraph') else 0}"
                    )
                
                self.render_page(page)
        
        # Add detailed logging at the end
        if self.detailed_logger:
            self.detailed_logger.log_step("Typesetting Complete")

    def render_page(self, page: il_version_1.Page):
        fonts: dict[
            str | int,
            il_version_1.PdfFont | dict[str, il_version_1.PdfFont],
        ] = {f.font_id: f for f in page.pdf_font if f.font_id}
        page_fonts = {f.font_id: f for f in page.pdf_font if f.font_id}
        for k, v in self.font_mapper.fontid2font.items():
            fonts[k] = v
        for xobj in page.pdf_xobject:
            if xobj.xobj_id is not None:
                fonts[xobj.xobj_id] = page_fonts.copy()
                for font in xobj.pdf_font:
                    if font.font_id:
                        fonts[xobj.xobj_id][font.font_id] = font
        if (
            page.page_number == 0
            and self.translation_config.watermark_output_mode
            == WatermarkOutputMode.Watermarked
        ):
            self.add_watermark(page)
        try:
            para_index = index.Index()
            para_map = {}
            
            valid_paras = [
                p
                for p in page.pdf_paragraph
                if p.box
                and all(c is not None for c in [p.box.x, p.box.y, p.box.x2, p.box.y2])
            ]

            for i, para in enumerate(valid_paras):
                para_map[i] = para
                para_index.insert(i, box_to_tuple(para.box))

            for i, p_upper in para_map.items():
                if not (p_upper.box and p_upper.box.y is not None):
                    continue
                upper_label = (p_upper.layout_label or "").lower()
                if (
                    "fallback_line" in upper_label
                    or self._is_inside_table_or_figure_region(p_upper.box, page, figures_only=True)
                ):
                    continue

                # Calculate paragraph height and set required gap accordingly
                para_height = p_upper.box.y2 - p_upper.box.y
                required_gap = 0.5 if para_height < 36 else 3

                check_area = il_version_1.Box(
                    x=p_upper.box.x,
                    y=p_upper.box.y - required_gap,
                    x2=p_upper.box.x2,
                    y2=p_upper.box.y,
                )

                candidate_ids = list(para_index.intersection(box_to_tuple(check_area)))

                conflicting_paras = []
                for para_id in candidate_ids:
                    if para_id == i:
                        continue
                    p_lower = para_map[para_id]
                    if not self._shares_reflow_layer(p_upper, p_lower):
                        continue
                    if not (
                        p_lower.box.x2 < p_upper.box.x
                        or p_lower.box.x > p_upper.box.x2
                    ):
                        conflicting_paras.append(p_lower)

                if conflicting_paras:
                    max_y2 = max(
                        p.box.y2
                        for p in conflicting_paras
                        if p.box and p.box.y2 is not None
                    )

                    new_y = max_y2 + required_gap
                    if p_upper.box and new_y < p_upper.box.y2:
                        p_upper.box.y = new_y
        except Exception as e:
            logger.warning(
                f"Failed to adjust paragraph positions on page {page.page_number}: {e}"
            )
        # A direct render_page() call (used by tests and API retries) may not
        # have passed through preprocessing, so finalize table geometry here too.
        self._synchronize_table_row_heights(page)
        # Balance table row widths before rendering
        self._balance_table_row_widths(page, fonts)
        self._reserve_heading_clearance_from_tables(page)
        self._resolve_vertical_paragraph_collisions(page)

        # Mirror decorative header banners (colored rectangles) horizontally for RTL
        self._mirror_header_decorations(page)

        header_rules = self._find_header_underlines(page)

        # Start typesetting layout rendering
        for paragraph in page.pdf_paragraph:
            self.render_paragraph(paragraph, page, fonts)
        self._audit_arabic_font_ownership(page)
        self._resolve_rendered_text_collisions(page)
        self._anchor_inline_highlight_curves(page)
        self._report_clipped_content(page)
        for paragraph, rules in header_rules:
            self._align_header_underlines(paragraph, rules, page.cropbox.box)

    def _report_clipped_content(self, page: il_version_1.Page) -> list[str]:
        """Warn about translated text that is omitted or drawn off the page.

        This is a final, read-only check: it never moves anything.  It makes
        silent loss visible in the job log instead of leaving it to be found in
        the finished PDF.
        """
        problems: list[str] = []
        crop = page.cropbox.box if page.cropbox and page.cropbox.box else None
        for paragraph in page.pdf_paragraph or []:
            label = getattr(paragraph, "debug_id", "?")
            rendered = self._rendered_text_box(paragraph)
            text = (paragraph.unicode or "").strip()
            if rendered is None:
                if text and not paragraph.vertical:
                    problems.append(f"{label}: translated text has no rendered glyphs ({text[:30]!r})")
                continue
            if crop is None:
                continue
            tolerance = 1.0
            if (
                rendered.x < crop.x - tolerance
                or rendered.x2 > crop.x2 + tolerance
                or rendered.y < crop.y - tolerance
                or rendered.y2 > crop.y2 + tolerance
            ):
                problems.append(f"{label}: rendered text leaves the page ({text[:30]!r})")
        if problems:
            logger.warning(
                "Possible missing or clipped content on page %s: %s",
                page.page_number, "; ".join(problems[:10]),
            )
        return problems

    def _shares_reflow_layer(self, first, second) -> bool:
        """Arabic reflow keeps independently painted PDF objects in their own lanes."""
        return not self.is_arabic or max(first.xobj_id or 0, 0) == max(second.xobj_id or 0, 0)

    @staticmethod
    def _is_fixed_table_paragraph(paragraph: il_version_1.PdfParagraph) -> bool:
        """Whether a paragraph owns a physical cell in a preserved table grid."""
        return "table" in (paragraph.layout_label or "").lower()

    @staticmethod
    def _is_heading_paragraph(paragraph: il_version_1.PdfParagraph) -> bool:
        label = (paragraph.layout_label or "").lower()
        return any(marker in label for marker in ("title", "heading", "section", "label"))

    def _reserve_heading_clearance_from_tables(self, page: il_version_1.Page) -> None:
        """Keep headings outside ruled table grids.

        A table is immutable once its ruled-cell geometry has been recovered.
        If a source heading slightly intrudes into the grid, move the heading
        upward before body collision handling rather than moving table cells or
        permitting the Arabic baseline to touch the top border.
        """
        page_key = id(page)
        if page_key in self._heading_clearance_resolved_pages:
            return

        headings = [p for p in page.pdf_paragraph or [] if p.box and self._is_heading_paragraph(p)]
        cells = [
            p for p in page.pdf_paragraph or []
            if p.box and self._is_fixed_table_paragraph(p)
        ]
        if not headings or not cells:
            self._heading_clearance_resolved_pages.add(page_key)
            return

        page_top = page.cropbox.box.y2 if page.cropbox and page.cropbox.box else None
        for heading in headings:
            candidates = []
            for cell in cells:
                horizontal_overlap = max(
                    0.0, min(heading.box.x2, cell.box.x2) - max(heading.box.x, cell.box.x)
                )
                if horizontal_overlap < min(heading.box.x2 - heading.box.x, cell.box.x2 - cell.box.x) * 0.20:
                    continue
                # The top row of a table is below the heading in PDF reading
                # order (smaller y), or may just touch its lower bound.
                if cell.box.y2 <= heading.box.y2 + 8.0:
                    candidates.append(cell.box.y2)
            if not candidates:
                continue
            table_top = max(candidates)
            required_bottom = table_top + 5.0
            if heading.box.y >= required_bottom:
                continue
            shift_up = required_bottom - heading.box.y
            if page_top is not None:
                shift_up = min(shift_up, max(0.0, page_top - heading.box.y2 - 2.0))
            if shift_up > 0.01:
                heading.box = Box(
                    heading.box.x, heading.box.y + shift_up,
                    heading.box.x2, heading.box.y2 + shift_up,
                )
                logger.info(
                    "Raised heading %s by %.1fpt to clear ruled table on page %s.",
                    getattr(heading, "debug_id", "?"), shift_up, page.page_number,
                )
        self._heading_clearance_resolved_pages.add(page_key)

    def _deduplicate_artificial_header_units(
        self,
        paragraph: il_version_1.PdfParagraph,
        page: il_version_1.Page,
        units: list[TypesettingUnit],
    ) -> list[TypesettingUnit]:
        """Collapse extraction-created doubled glyphs in a running header.

        Some PDFs paint the header text twice.  The source looks acceptable in
        its original font but the two independent glyph streams become visibly
        doubled after font substitution.  This deliberately acts only on a
        header/footer when the majority of ASCII letters are paired; ordinary
        words such as ``All`` or legitimate double letters are untouched.
        """
        printable = [u for u in units if not u.forced_line_break and u.try_get_unicode()]
        if len(printable) < 6:
            return units
        chars = [u.try_get_unicode() or "" for u in printable]
        raw_text = "".join(chars)
        # Some legacy PDFs do not label their running header and place it just
        # outside the generic top-band threshold.  A strongly doubled IBAN
        # banner is still unambiguously a header, never body prose.
        compact_probe = re.sub(r'([A-Za-z])\1', r'\1', raw_text)
        looks_like_iban_banner = bool(re.search(r'IBAN\s*Standards|IBAN.*Ver', compact_probe, re.I))
        if not self._is_header_or_footer(paragraph, page) and not looks_like_iban_banner:
            return units
        # The IBAN banner sometimes duplicates only punctuation and digits
        # (``Ver.. 22.. 00 --``), which does not meet the paired-letter ratio
        # below.  Those repeats are never meaningful in this fixed banner.
        if looks_like_iban_banner:
            collapsed_banner: list[TypesettingUnit] = []
            for unit in units:
                value = unit.try_get_unicode() or ""
                if (
                    collapsed_banner
                    and value
                    and value == (collapsed_banner[-1].try_get_unicode() or "")
                    and not value.isalpha()
                ):
                    continue
                collapsed_banner.append(unit)
            units = collapsed_banner
            # Broken duplicate streams also leave synthetic spaces around the
            # decimal point (``Ver. 2. 0``).  Remove only whitespace whose
            # nearest printable neighbours form digit-dot or dot-digit; normal
            # word spacing in the banner is preserved.
            compact_banner: list[TypesettingUnit] = []
            for unit_index, unit in enumerate(units):
                value = unit.try_get_unicode() or ""
                if value.isspace():
                    previous = next(
                        (
                            units[i].try_get_unicode() or ""
                            for i in range(unit_index - 1, -1, -1)
                            if (units[i].try_get_unicode() or "").strip()
                        ),
                        "",
                    )
                    following = next(
                        (
                            units[i].try_get_unicode() or ""
                            for i in range(unit_index + 1, len(units))
                            if (units[i].try_get_unicode() or "").strip()
                        ),
                        "",
                    )
                    if (previous.isdigit() and following == ".") or (
                        previous == "." and following.isdigit()
                    ):
                        continue
                compact_banner.append(unit)
            units = compact_banner
            printable = [u for u in units if not u.forced_line_break and u.try_get_unicode()]
            chars = [u.try_get_unicode() or "" for u in printable]
        latin_count = sum(char.isascii() and char.isalpha() for char in chars)
        if not latin_count:
            return units

        paired = 0
        index = 0
        while index < len(chars):
            end = index + 1
            while end < len(chars) and chars[end] == chars[index]:
                end += 1
            run_length = end - index
            if chars[index].isascii() and chars[index].isalpha() and run_length >= 2 and run_length % 2 == 0:
                paired += run_length
            index = end
        if paired / latin_count < 0.70:
            return units

        result: list[TypesettingUnit] = []
        index = 0
        while index < len(units):
            unit = units[index]
            char = unit.try_get_unicode() or ""
            if not char or char.isspace():
                result.append(unit)
                index += 1
                continue
            end = index + 1
            while end < len(units) and (units[end].try_get_unicode() or "") == char:
                end += 1
            run_length = end - index
            if run_length >= 2 and run_length % 2 == 0:
                result.extend(units[index:end:2])
            else:
                result.extend(units[index:end])
            index = end
        logger.info("Collapsed doubled running-header glyph stream for paragraph %s.", getattr(paragraph, "debug_id", "?"))
        return result

    def _resolve_vertical_paragraph_collisions(self, page: il_version_1.Page) -> None:
        """Reserve vertical lanes for overlapping non-table text regions.

        PDF extraction boxes are hints, not a reliable reading-order contract.
        In particular, a numbered heading may overlap the first fallback body
        line.  Arabic reflow makes the error conspicuous because both runs are
        right aligned.  Process regions from top to bottom, treating headings
        and ruled-table cells as fixed obstacles, and move only movable prose
        below the nearest occupied lane.

        The operation is intentionally idempotent.  ``render_page`` invokes it
        as a safe direct-call path after ``preprocess_document`` has already
        applied it during normal translation.
        """
        page_key = id(page)
        if page_key in self._vertical_collision_resolved_pages:
            return

        valid = [
            paragraph
            for paragraph in page.pdf_paragraph or []
            if paragraph.box
            and all(value is not None for value in (
                paragraph.box.x, paragraph.box.y, paragraph.box.x2, paragraph.box.y2
            ))
            and not self._is_inside_table_or_figure_region(
                paragraph.box, page, figures_only=True
            )
        ]
        if len(valid) < 2:
            self._vertical_collision_resolved_pages.add(page_key)
            return

        def order_key(paragraph: il_version_1.PdfParagraph) -> tuple[float, int]:
            # PDF y increases upward, therefore greatest y2 appears first.
            # A heading wins a same-baseline tie over its body paragraph.
            return (-paragraph.box.y2, 0 if self._is_heading_paragraph(paragraph) else 1)

        occupied: list[il_version_1.PdfParagraph] = []
        moved = 0
        for paragraph in sorted(valid, key=order_key):
            # Physical table cells already have a ruled grid and synchronized
            # row heights.  They must act as obstacles but never be shifted.
            if not self._is_fixed_table_paragraph(paragraph):
                required_shift = 0.0
                for blocker in occupied:
                    if not blocker.box or not self._shares_reflow_layer(paragraph, blocker):
                        continue
                    horizontal_overlap = max(
                        0.0,
                        min(paragraph.box.x2, blocker.box.x2) - max(paragraph.box.x, blocker.box.x),
                    )
                    min_width = max(1.0, min(
                        paragraph.box.x2 - paragraph.box.x,
                        blocker.box.x2 - blocker.box.x,
                    ))
                    # Independent columns can share vertical space.  A modest
                    # overlap threshold still catches a narrow section number
                    # placed beside a wide body box.
                    if horizontal_overlap < min_width * 0.25:
                        continue
                    gap = 5.0 if (
                        self._is_heading_paragraph(paragraph)
                        or self._is_heading_paragraph(blocker)
                    ) else 3.0
                    allowed_top = blocker.box.y - gap
                    if paragraph.box.y2 > allowed_top:
                        required_shift = max(required_shift, paragraph.box.y2 - allowed_top)

                if required_shift > 0.01:
                    paragraph.box = Box(
                        paragraph.box.x,
                        paragraph.box.y - required_shift,
                        paragraph.box.x2,
                        paragraph.box.y2 - required_shift,
                    )
                    moved += 1
                    logger.info(
                        "Reserved vertical lane for paragraph %s on page %s (shift %.1fpt).",
                        getattr(paragraph, "debug_id", "?"), page.page_number, required_shift,
                    )
            occupied.append(paragraph)

        self._vertical_collision_resolved_pages.add(page_key)
        if moved:
            logger.info("Resolved %d overlapping paragraph lane(s) on page %s.", moved, page.page_number)

    @staticmethod
    def _rendered_text_box(paragraph: il_version_1.PdfParagraph) -> Box | None:
        """Return the real glyph bounds, independent of the source paragraph box."""
        boxes: list[Box] = []
        for composition in paragraph.pdf_paragraph_composition or []:
            if composition.pdf_character and composition.pdf_character.box:
                boxes.append(composition.pdf_character.box)
            elif composition.pdf_formula:
                for char in composition.pdf_formula.pdf_character or []:
                    if char.box:
                        boxes.append(char.box)
        if not boxes:
            return None
        return Box(
            min(box.x for box in boxes), min(box.y for box in boxes),
            max(box.x2 for box in boxes), max(box.y2 for box in boxes),
        )

    @staticmethod
    def _translate_rendered_curve(curve: PdfCurve, dx: float = 0.0, dy: float = 0.0) -> None:
        if curve.box:
            curve.box.x += dx
            curve.box.x2 += dx
            curve.box.y += dy
            curve.box.y2 += dy
        transform = list(curve.relocation_transform or [1, 0, 0, 1, 0, 0])
        transform[4] += dx
        transform[5] += dy
        curve.relocation_transform = transform

    @staticmethod
    def _shift_rendered_paragraph_vertically(
        paragraph: il_version_1.PdfParagraph, shift: float
    ) -> None:
        """Move already-rendered glyphs (and formula decorations) down together."""
        def shift_box(box: Box | None) -> None:
            if box:
                box.y -= shift
                box.y2 -= shift

        shift_box(paragraph.box)
        for composition in paragraph.pdf_paragraph_composition or []:
            if composition.pdf_character:
                char = composition.pdf_character
                shift_box(char.box)
                if char.visual_bbox:
                    shift_box(char.visual_bbox.box)
            elif composition.pdf_formula:
                formula = composition.pdf_formula
                shift_box(formula.box)
                for char in formula.pdf_character or []:
                    shift_box(char.box)
                    if char.visual_bbox:
                        shift_box(char.visual_bbox.box)
                for curve in formula.pdf_curve or []:
                    Typesetting._translate_rendered_curve(curve, dy=-shift)

    def _resolve_rendered_text_collisions(self, page: il_version_1.Page) -> None:
        """Final export firewall for collisions caused by glyphs escaping source boxes.

        Translation can expand text beyond the extraction box, so a box-only
        preflight cannot prove visual safety.  Inspect the actual generated
        glyphs and, for non-table prose, push the lower-priority block below
        the collision. Separate source rows keep their reading order; heading
        priority breaks ties when the source boxes already overlap.
        """
        page_key = id(page)
        if page_key in self._rendered_collision_resolved_pages:
            return

        paragraphs = [
            paragraph for paragraph in page.pdf_paragraph or []
            if not self._is_fixed_table_paragraph(paragraph)
            and self._rendered_text_box(paragraph) is not None
            # Labels inside a chart are anchored to plotted data.  Pushing
            # them down like body prose would cover bars, lines or ticks.
            and not (
                paragraph.box is not None
                and self._is_inside_table_or_figure_region(
                    paragraph.box, page, figures_only=True
                )
            )
        ]
        source_lanes = {
            id(paragraph): (paragraph.box.y, paragraph.box.y2)
            for paragraph in paragraphs
            if paragraph.box is not None
            and paragraph.box.y is not None
            and paragraph.box.y2 is not None
        }
        moved = 0
        for _ in range(max(1, len(paragraphs))):
            changed = False
            rendered = {id(paragraph): self._rendered_text_box(paragraph) for paragraph in paragraphs}
            for index, first in enumerate(paragraphs):
                first_box = rendered[id(first)]
                if first_box is None:
                    continue
                for second in paragraphs[index + 1:]:
                    if not self._shares_reflow_layer(first, second):
                        continue
                    second_box = rendered[id(second)]
                    if second_box is None:
                        continue
                    horizontal_overlap = max(
                        0.0, min(first_box.x2, second_box.x2) - max(first_box.x, second_box.x)
                    )
                    vertical_overlap = max(
                        0.0, min(first_box.y2, second_box.y2) - max(first_box.y, second_box.y)
                    )
                    min_width = max(1.0, min(first_box.x2 - first_box.x, second_box.x2 - second_box.x))
                    if horizontal_overlap < min_width * 0.25 or vertical_overlap <= 0.25:
                        continue

                    first_lane = source_lanes.get(id(first))
                    second_lane = source_lanes.get(id(second))
                    if first_lane and second_lane and first_lane[0] >= second_lane[1]:
                        blocker, mover = first, second
                    elif first_lane and second_lane and second_lane[0] >= first_lane[1]:
                        blocker, mover = second, first
                    elif self._is_heading_paragraph(first):
                        blocker, mover = first, second
                    elif self._is_heading_paragraph(second):
                        blocker, mover = second, first
                    elif first_box.y2 >= second_box.y2:
                        blocker, mover = first, second
                    else:
                        blocker, mover = second, first
                    blocker_box = rendered[id(blocker)]
                    mover_box = rendered[id(mover)]
                    gap = 5.0 if (
                        self._is_heading_paragraph(blocker) or self._is_heading_paragraph(mover)
                    ) else 3.0
                    shift = max(0.0, mover_box.y2 - (blocker_box.y - gap))
                    if shift <= 0.01:
                        # The geometric order is inverted, but there is still
                        # a collision: separate by the measured overlap.
                        shift = vertical_overlap + gap
                    self._shift_rendered_paragraph_vertically(mover, shift)
                    moved += 1
                    changed = True
                    break
                if changed:
                    break
            if not changed:
                break

        self._rendered_collision_resolved_pages.add(page_key)
        if moved:
            logger.warning(
                "Resolved %d rendered glyph collision(s) on page %s.", moved, page.page_number
            )

    def _anchor_inline_highlight_curves(self, page: il_version_1.Page) -> None:
        """Relocate inline background highlight rectangles from LTR source lines to RTL rendered lines.

        Scientific figures and heatmaps (e.g., neuron activation visualizations)
        often paint colored background rectangles behind specific words on 1-2
        lines. When those sentences are translated into Arabic and laid out RTL,
        the rendered lines move and right-align. This method deduplicates
        stacked highlight curves and maps each contiguous highlight span from
        its cumulative LTR position along the paragraph's source lines to the
        corresponding cumulative RTL position along the paragraph's rendered
        Arabic lines.
        """
        if not self._is_arabic_output() or not getattr(page, "pdf_curve", None):
            return
        page_key = id(page)
        if page_key in self._inline_highlights_anchored_pages:
            return
        self._inline_highlights_anchored_pages.add(page_key)

        candidate_curves = [
            curve
            for curve in page.pdf_curve
            if curve.box is not None
            and curve.fill_background
            and not curve.stroke_path
            and 0.5 <= (curve.box.x2 - curve.box.x) <= 220.0
            and 3.0 <= (curve.box.y2 - curve.box.y) <= 18.0
        ]
        if not candidate_curves:
            return

        claimed_curve_ids: set[int] = set()
        duplicate_curve_ids: set[int] = set()

        for paragraph in page.pdf_paragraph or []:
            trace = paragraph.pipeline_trace or {}
            source_lines = list(trace.get("source_line_boxes") or [])
            label = (paragraph.layout_label or "").lower()
            in_figure = self._is_inside_table_or_figure_region(
                paragraph.box, page, figures_only=True
            )
            if not in_figure and not any(
                marker in label
                for marker in ("fallback_line", "figure", "chart", "axis", "diagram")
            ):
                continue
            if not source_lines:
                if (
                    paragraph.box is not None
                    and 3.0 <= (paragraph.box.y2 - paragraph.box.y) <= 18.0
                ):
                    source_lines = [
                        (paragraph.box.x, paragraph.box.y, paragraph.box.x2, paragraph.box.y2)
                    ]
                else:
                    continue

            # Collect rendered character boxes for this paragraph and cluster into visual lines
            char_boxes: list[Box] = []
            for comp in paragraph.pdf_paragraph_composition or []:
                if comp.pdf_character and comp.pdf_character.box:
                    if (comp.pdf_character.char_unicode or "").strip():
                        char_boxes.append(comp.pdf_character.box)
                elif comp.pdf_formula:
                    for ch in comp.pdf_formula.pdf_character or []:
                        if ch.box and (ch.char_unicode or "").strip():
                            char_boxes.append(ch.box)
            if not char_boxes:
                continue

            rendered_clusters: list[list[Box]] = []
            for cbox in sorted(char_boxes, key=lambda b: (-b.y2, b.x)):
                placed = False
                ch_h = max(1.0, cbox.y2 - cbox.y)
                for cluster in rendered_clusters:
                    ref_y1 = min(b.y for b in cluster)
                    ref_y2 = max(b.y2 for b in cluster)
                    ref_h = max(1.0, ref_y2 - ref_y1)
                    v_ov = min(cbox.y2, ref_y2) - max(cbox.y, ref_y1)
                    if v_ov >= 0.35 * min(ch_h, ref_h) or abs(cbox.y - ref_y1) <= 3.0:
                        cluster.append(cbox)
                        placed = True
                        break
                if not placed:
                    rendered_clusters.append([cbox])

            rendered_lines: list[tuple[float, float, float, float]] = [
                (
                    min(b.x for b in cluster),
                    min(b.y for b in cluster),
                    max(b.x2 for b in cluster),
                    max(b.y2 for b in cluster),
                )
                for cluster in rendered_clusters
            ]
            rendered_lines.sort(key=lambda r: -r[3])
            if not rendered_lines:
                continue

            # Match candidate curves to each source line of this paragraph
            line_curves: dict[int, list[PdfCurve]] = {i: [] for i in range(len(source_lines))}
            seen_geom: set[tuple[float, float, float, float]] = set()
            for curve in candidate_curves:
                if id(curve) in claimed_curve_ids:
                    continue
                if curve.xobj_id != paragraph.xobj_id:
                    continue
                cbox = curve.box
                ch_h = max(1.0, cbox.y2 - cbox.y)
                best_line_idx = None
                best_ov = 0.0
                for idx, (sx1, sy1, sx2, sy2) in enumerate(source_lines):
                    src_h = max(1.0, sy2 - sy1)
                    v_ov = min(cbox.y2, sy2) - max(cbox.y, sy1)
                    h_ov = min(cbox.x2, sx2 + 3.0) - max(cbox.x, sx1 - 3.0)
                    if v_ov >= 0.45 * min(ch_h, src_h) and h_ov > 0.5:
                        if v_ov > best_ov:
                            best_ov = v_ov
                            best_line_idx = idx
                if best_line_idx is not None:
                    claimed_curve_ids.add(id(curve))
                    geom_key = (
                        round(cbox.x, 1),
                        round(cbox.y, 1),
                        round(cbox.x2, 1),
                        round(cbox.y2, 1),
                    )
                    if geom_key in seen_geom:
                        duplicate_curve_ids.add(id(curve))
                    else:
                        seen_geom.add(geom_key)
                        line_curves[best_line_idx].append(curve)

            if not any(line_curves.values()):
                continue

            src_widths = [max(1.0, sx2 - sx1) for (sx1, sy1, sx2, sy2) in source_lines]
            total_src_w = sum(src_widths)
            dst_widths = [max(1.0, rx2 - rx1) for (rx1, ry1, rx2, ry2) in rendered_lines]
            total_dst_w = sum(dst_widths)

            for src_idx, curves_on_line in line_curves.items():
                if not curves_on_line:
                    continue
                sx1, sy1, sx2, sy2 = source_lines[src_idx]
                curves_on_line.sort(key=lambda c: c.box.x)

                # Group horizontally contiguous character highlight curves into word/phrase spans
                spans: list[list[PdfCurve]] = []
                for curve in curves_on_line:
                    if not spans or (curve.box.x - spans[-1][-1].box.x2) > 3.5:
                        spans.append([curve])
                    else:
                        spans[-1].append(curve)

                cum_src_before = sum(src_widths[:src_idx])
                for span in spans:
                    span_x1 = min(c.box.x for c in span)
                    span_x2 = max(c.box.x2 for c in span)
                    span_y1 = min(c.box.y for c in span)
                    span_y2 = max(c.box.y2 for c in span)
                    span_w = span_x2 - span_x1
                    span_center_x = 0.5 * (span_x1 + span_x2)
                    span_center_y = 0.5 * (span_y1 + span_y2)

                    rel_src_x = min(max(0.0, span_center_x - sx1), src_widths[src_idx])
                    u_center = (cum_src_before + rel_src_x) / total_src_w
                    target_dist = u_center * total_dst_w

                    # Locate the target rendered line and RTL offset along that line
                    cum_dst = 0.0
                    dst_idx = len(rendered_lines) - 1
                    dist_in_line = dst_widths[-1]
                    for k, dw in enumerate(dst_widths):
                        if target_dist <= cum_dst + dw or k == len(dst_widths) - 1:
                            dst_idx = k
                            dist_in_line = min(max(0.0, target_dist - cum_dst), dw)
                            break
                        cum_dst += dw

                    rx1, ry1, rx2, ry2 = rendered_lines[dst_idx]
                    target_center_x = rx2 - dist_in_line
                    if span_w >= (rx2 - rx1):
                        target_x1 = rx1
                    else:
                        target_x1 = max(rx1, min(rx2 - span_w, target_center_x - 0.5 * span_w))
                    target_center_y = 0.5 * (ry1 + ry2)

                    dx = target_x1 - span_x1
                    dy = target_center_y - span_center_y
                    if abs(dx) > 0.1 or abs(dy) > 0.1:
                        for curve in span:
                            self._translate_rendered_curve(curve, dx=dx, dy=dy)

        if duplicate_curve_ids:
            page.pdf_curve = [
                c for c in page.pdf_curve if id(c) not in duplicate_curve_ids
            ]

    @staticmethod
    def _is_arabic_output_character(value: str | None) -> bool:
        if not value or len(value) != 1:
            return False
        codepoint = ord(value)
        return (
            0x0600 <= codepoint <= 0x06FF
            or 0x0750 <= codepoint <= 0x077F
            or 0x08A0 <= codepoint <= 0x08FF
            or 0xFB50 <= codepoint <= 0xFDFF
            or 0xFE70 <= codepoint <= 0xFEFF
        )

    def _audit_arabic_font_ownership(self, page: il_version_1.Page) -> None:
        """Repair and verify Arabic glyph ownership immediately before export.

        Some formula and layout branches preserve source ``PdfCharacter``
        objects. This final IL audit is intentionally independent of those
        branches: an Arabic glyph can only leave the typesetter in an embedded
        Arabic target font, never in the Source Han font inherited from English.
        """
        if not self.is_arabic:
            return

        characters = list(page.pdf_character or [])
        for paragraph in page.pdf_paragraph or []:
            for composition in paragraph.pdf_paragraph_composition or []:
                if composition.pdf_character:
                    characters.append(composition.pdf_character)
                elif composition.pdf_formula:
                    characters.extend(composition.pdf_formula.pdf_character or [])

        source_fonts = {
            font.font_id: font
            for font in page.pdf_font or []
            if font.font_id
        }
        repaired = 0
        for char in characters:
            if not self._is_arabic_output_character(char.char_unicode):
                continue
            if not char.pdf_style:
                raise RuntimeError(
                    f"Arabic glyph {char.char_unicode!r} has no PDF style on page {page.page_number}"
                )
            current_font_id = char.pdf_style.font_id or ""
            if "gonotokurrent" in current_font_id.lower():
                continue
            source_font = source_fonts.get(current_font_id)
            target_font = (
                self.font_mapper.map(source_font, char.char_unicode)
                if source_font is not None
                else None
            )
            if target_font is None:
                target_font = next(
                    (
                        font for font in self.font_mapper.arabic_fonts
                        if font.has_glyph(ord(char.char_unicode))
                    ),
                    None,
                )
            if target_font is None:
                normalized = unicodedata.normalize("NFKC", char.char_unicode)
                target_font = next(
                    (
                        font for font in self.font_mapper.arabic_fonts
                        if len(normalized) == 1 and font.has_glyph(ord(normalized))
                    ),
                    None,
                )
                if target_font is not None:
                    char.char_unicode = normalized
            if target_font is None:
                raise RuntimeError(
                    f"No Arabic target-font coverage for {char.char_unicode!r} on page {page.page_number}"
                )
            style = copy.copy(char.pdf_style)
            style.font_id = target_font.font_id
            char.pdf_style = style
            char.pdf_character_id = target_font.has_glyph(ord(char.char_unicode))
            repaired += 1

        if repaired:
            logger.warning(
                "Repaired %d Arabic glyph(s) that retained a source-font style on page %d",
                repaired,
                page.page_number,
            )


    def _find_header_underlines(self, page: il_version_1.Page):
        """Match short, right-aligned filled rules to a header before text moves."""
        if not self._is_arabic_output() or not page.cropbox or not page.cropbox.box:
            return []
        crop = page.cropbox.box
        mid_x = (crop.x + crop.x2) / 2
        top = crop.y2 - (crop.y2 - crop.y) * 0.15
        thin_rules = [
            curve for curve in page.pdf_curve
            if curve.box and curve.box.y >= top and curve.box.x >= mid_x
            and 0 < curve.box.y2 - curve.box.y <= 1.5
            and curve.fill_background and not curve.stroke_path
            # The parser marks original PDF paths with 'a'.
            and curve.debug_info == "a"
            and not curve.relocation_transform
            and [part.op for part in curve.pdf_path] == ["m", "l", "l", "l", "h"]
        ]
        matches = []
        for paragraph in page.pdf_paragraph:
            box = paragraph.box
            size = paragraph.pdf_style.font_size if paragraph.pdf_style else None
            label = (paragraph.layout_label or "").lower()
            if (
                not box or not size or box.x < mid_x or box.y2 <= top
                or label not in {"abandon", "plain text", "heading", "header", "page_header"}
                or self._is_inside_table_or_figure_region(box, page)
            ):
                continue
            rules = sorted((
                curve for curve in thin_rules
                if curve.xobj_id == paragraph.xobj_id and curve.box.x2 - curve.box.x >= size * 2.5
                and curve.box.x >= box.x - 2 and abs(curve.box.x2 - box.x2) <= 2
                and box.y2 - size * 4 <= curve.box.y <= box.y2 - size * 0.5
            ), key=lambda curve: -curve.box.y)
            if 2 <= len(rules) <= 4 and all(
                first.box.y - second.box.y >= size * 0.75
                for first, second in zip(rules, rules[1:])
            ):
                matches.append((paragraph, rules))

        # Do not move a rule that could belong to more than one text block.
        rule_counts = {}
        for _, rules in matches:
            for rule in rules:
                rule_counts[id(rule)] = rule_counts.get(id(rule), 0) + 1
        return [
            (paragraph, rules) for paragraph, rules in matches
            if all(rule_counts[id(rule)] == 1 for rule in rules)
        ]

    @staticmethod
    def _align_header_underlines(paragraph, rules, crop):
        """Move original paths under the final lines when the mapping is unambiguous."""
        right_by_baseline: dict[float, float] = {}
        for composition in paragraph.pdf_paragraph_composition:
            char = composition.pdf_character
            if char and char.box and (char.char_unicode or "").strip():
                x2, baseline = char.box.x2, round(char.box.y, 2)
                right_by_baseline[baseline] = max(right_by_baseline.get(baseline, x2), x2)
        if len(right_by_baseline) != len(rules):
            return

        placements = []
        for rule, (baseline, right) in zip(rules, sorted(right_by_baseline.items(), reverse=True)):
            box = rule.box
            dx, dy = right - box.x2, baseline - 1.5 - box.y2
            moved = Box(box.x + dx, box.y + dy, box.x2 + dx, box.y2 + dy)
            if not (crop.x <= moved.x < moved.x2 <= crop.x2 and crop.y <= moved.y < moved.y2 <= crop.y2):
                return
            placements.append((rule, moved, dx, dy))

        for rule, moved, dx, dy in placements:
            rule.box = moved
            # The writer uses pdf_original_path, so moving only the box is insufficient.
            rule.relocation_transform = [1, 0, 0, 1, dx, dy]

    def _mirror_header_decorations(self, page: il_version_1.Page):
        """Mirror decorative header vector rectangles horizontally when rendering RTL."""
        if not self._is_arabic_output():
            return
        if not (page.cropbox and page.cropbox.box and hasattr(page, 'pdf_rectangle') and page.pdf_rectangle):
            return

        page_box = page.cropbox.box
        page_w = page_box.x2 - page_box.x
        page_h = page_box.y2 - page_box.y
        top_threshold = page_box.y + page_h * 0.70  # Top 30% of page

        for rect in page.pdf_rectangle:
            if not rect.box or getattr(rect, "debug_info", False):
                continue
            r_box = rect.box
            r_h = r_box.y2 - r_box.y
            r_w = r_box.x2 - r_box.x
            # Check if this is a header decorative badge/strip:
            # - In the top 30% of page
            # - Relatively small height (banner badge/accent, e.g. 4pt - 35pt)
            # - Does not span the whole page width (< 45% of page width)
            if r_box.y >= top_threshold and 4.0 <= r_h <= 35.0 and r_w < page_w * 0.45:
                # Mirror horizontally: x_new = page_box.x2 - (r_box.x2 - page_box.x)
                new_x = page_box.x2 - (r_box.x2 - page_box.x)
                new_x2 = page_box.x2 - (r_box.x - page_box.x)
                r_box.x = new_x
                r_box.x2 = new_x2

    def add_watermark(self, page: il_version_1.Page):
        page_width = page.cropbox.box.x2 - page.cropbox.box.x
        page_height = page.cropbox.box.y2 - page.cropbox.box.y
        style = il_version_1.PdfStyle(
            font_id="base",
            font_size=6,
            graphic_state=il_version_1.GraphicState(),
        )
        text = f""
        if self.translation_config.debug:
            text += ""
        page.pdf_paragraph.append(
            il_version_1.PdfParagraph(
                first_line_indent=False,
                box=il_version_1.Box(
                    x=page.cropbox.box.x + page_width * 0.05,
                    y=page.cropbox.box.y,
                    x2=page.cropbox.box.x2,
                    y2=page.cropbox.box.y2 - page_height * 0.05,
                ),
                vertical=False,
                pdf_style=style,
                pdf_paragraph_composition=[
                    il_version_1.PdfParagraphComposition(
                        pdf_same_style_unicode_characters=il_version_1.PdfSameStyleUnicodeCharacters(
                            unicode=text,
                            pdf_style=style,
                        ),
                    ),
                ],
                xobj_id=-1,
            ),
        )

    def _is_arabic_output(self) -> bool:
        """Check if the output language is Arabic for RTL margin mirroring."""
        lang_out = (self.translation_config.lang_out or "").lower()
        if lang_out in ("en-ar", "ar", "ara", "arabic"):
            return True
        if "-ar" in lang_out or "->ar" in lang_out or "/ar" in lang_out:
            return True
        return False

    def _get_effective_arabic_box(
        self, 
        paragraph: il_version_1.PdfParagraph, 
        page: il_version_1.Page,
        original_box: Box
    ) -> Box:
        """Get the effective bounding box for Arabic text layout.
        
        This calculates where the text will actually end up after RTL margin mirroring,
        and ensures the box stays within page bounds. This prevents text from overflowing
        the page margins.
        
        Args:
            paragraph: The paragraph being laid out
            page: The page containing the paragraph
            original_box: The original bounding box from the English source
            
        Returns:
            A new Box that represents the effective layout area after considering
            RTL margin mirroring and page bounds
        """
        if not page.cropbox or not page.cropbox.box:
            return original_box
        
        page_box = page.cropbox.box
        
        # Get the margin shift that will be applied later
        shift_x = self._calculate_rtl_margin_shift(paragraph, page)
        
        # Calculate where the box will be after margin shifting
        final_x = original_box.x + shift_x
        final_x2 = original_box.x2 + shift_x
        
        # Get safe page margins (2% from each edge)
        page_left_margin = page_box.x + (page_box.x2 - page_box.x) * 0.02
        page_right_margin = page_box.x2 - (page_box.x2 - page_box.x) * 0.02
        
        # Check if the shifted box would overflow the right margin
        if final_x2 > page_right_margin:
            # Calculate how much we need to shrink the box
            overflow = final_x2 - page_right_margin
            # Reduce the box width from the right side
            new_x2 = original_box.x2 - overflow
            # Ensure we don't make the box too small
            min_width = (page_box.x2 - page_box.x) * 0.1  # At least 10% of page width
            if new_x2 - original_box.x < min_width:
                new_x2 = original_box.x + min_width
            
            return Box(
                x=original_box.x,
                y=original_box.y,
                x2=new_x2,
                y2=original_box.y2
            )
        
        # Check if the shifted box would overflow the left margin
        if final_x < page_left_margin:
            # This shouldn't normally happen with RTL, but handle it
            overflow = page_left_margin - final_x
            new_x = original_box.x + overflow
            
            return Box(
                x=new_x,
                y=original_box.y,
                x2=original_box.x2,
                y2=original_box.y2
            )
        
        return original_box

    def _calculate_rtl_margin_shift(self, paragraph: il_version_1.PdfParagraph, page: il_version_1.Page) -> float:
        """Calculate the X shift needed for RTL margin mirroring.
        
        For RTL languages, the left margin should become the right margin and vice versa.
        This function calculates the shift needed to achieve this.
        
        IMPORTANT: When there are sidebars or page decorations, we mirror within the
        CONTENT AREA (excluding decorations), not the full page width.
        
        The shift is clamped to ensure the paragraph stays within page bounds.
        
        Args:
            paragraph: The paragraph to mirror
            page: The page containing the paragraph
            
        Returns:
            The X shift to apply for margin mirroring
        """
        if not paragraph.box or not page.cropbox or not page.cropbox.box:
            return 0.0
        
        box = paragraph.box
        page_box = page.cropbox.box
        
        if self._is_centered_display_text(paragraph, page):
            return 0.0

        # Framed contact rows are already right-aligned within their own lane.
        if self._is_arabic_output() and self._contact_frame_bounds(paragraph, page):
            return 0.0

        # Symmetrical page-level mirroring for running headers, footers, and page numbers
        if self._is_header_or_footer(paragraph, page):
            orig_right_margin = page_box.x2 - box.x2
            new_x = page_box.x + orig_right_margin
            return self._keep_clear_of_later_images(paragraph, page, new_x - box.x)

        # Detect content area boundaries (accounting for sidebars/decorations)
        content_left, content_right = self._get_content_area_bounds(page)
        
        # Original margins relative to content area
        original_left_margin = box.x - content_left
        original_right_margin = content_right - box.x2
        
        # For RTL: new_left_margin = original_right_margin
        # new_box.x = content_left + original_right_margin
        # shift_x = new_box.x - box.x
        
        new_x = content_left + original_right_margin
        shift_x = new_x - box.x
        
        # CLAMP: Ensure the paragraph's right edge doesn't exceed content_right after shifting
        # new_x2 = box.x2 + shift_x should be <= content_right
        new_x2 = box.x2 + shift_x
        if new_x2 > content_right:
            # Reduce the shift to keep the right edge at content_right
            overflow = new_x2 - content_right
            shift_x -= overflow
        
        # CLAMP: Ensure the paragraph's left edge doesn't go below content_left
        # new_x = box.x + shift_x should be >= content_left
        new_x_clamped = box.x + shift_x
        if new_x_clamped < content_left:
            # Increase the shift to keep the left edge at content_left
            underflow = content_left - new_x_clamped
            shift_x += underflow
        
        return self._keep_clear_of_later_images(paragraph, page, shift_x)

    def _keep_clear_of_later_images(self, paragraph, page, shift_x: float) -> float:
        """Keep a source lane when mirroring would hide visible text behind an image."""
        if not shift_x or paragraph.render_order is None or not self._is_arabic_output():
            return shift_x
        box = self._rendered_text_box(paragraph) or paragraph.box
        for form in page.pdf_form:
            image = form.box
            if (
                form.form_type != "image" or not image
                or form.xobj_id != paragraph.xobj_id or form.render_order is None
                or form.render_order <= paragraph.render_order
                or box.y >= image.y2 or box.y2 <= image.y
            ):
                continue
            source_overlaps = box.x < image.x2 and box.x2 > image.x
            shifted_overlaps = box.x + shift_x < image.x2 and box.x2 + shift_x > image.x
            if shifted_overlaps and not source_overlaps:
                return 0.0
        return shift_x

    def _get_content_area_bounds(self, page: il_version_1.Page) -> tuple[float, float]:
        """Get the left and right boundaries of the content area, excluding sidebars.
        
        Detects large rectangles or figures near page edges that are likely sidebars
        or decorative elements, and returns the content area bounds.
        
        Args:
            page: The page to analyze
            
        Returns:
            Tuple of (content_left, content_right) x-coordinates
        """
        if not page.cropbox or not page.cropbox.box:
            return (0.0, 612.0)  # Default US Letter width
        
        page_box = page.cropbox.box
        page_left = page_box.x
        page_right = page_box.x2
        page_width = page_right - page_left
        page_height = page_box.y2 - page_box.y
        
        # Start with full page as content area
        content_left = page_left
        content_right = page_right
        
        # Check for sidebar rectangles
        if hasattr(page, 'pdf_rectangle') and page.pdf_rectangle:
            for rect in page.pdf_rectangle:
                if not rect.box:
                    continue
                
                rect_box = rect.box
                rect_height = rect_box.y2 - rect_box.y
                rect_width = rect_box.x2 - rect_box.x
                
                # Is it a tall rectangle (sidebar-like)? Must be at least 30% of page height
                is_tall = rect_height > page_height * 0.3
                
                # Is it narrow enough to be a sidebar? (less than 25% of page width)
                is_narrow = rect_width < page_width * 0.25
                
                if is_tall and is_narrow:
                    # Check if it's on the left side
                    if rect_box.x < page_left + page_width * 0.2:
                        # Left sidebar - content starts after it
                        content_left = max(content_left, rect_box.x2)
                    
                    # Check if it's on the right side
                    if rect_box.x2 > page_right - page_width * 0.2:
                        # Right sidebar - content ends before it
                        content_right = min(content_right, rect_box.x)
        
        # Also check for figures that might be sidebars/decorations
        if hasattr(page, 'pdf_figure') and page.pdf_figure:
            for figure in page.pdf_figure:
                if not figure.box:
                    continue
                
                fig_box = figure.box
                fig_height = fig_box.y2 - fig_box.y
                fig_width = fig_box.x2 - fig_box.x
                
                # Is it a tall figure (sidebar-like)?
                is_tall = fig_height > page_height * 0.3
                
                # Is it narrow?
                is_narrow = fig_width < page_width * 0.25
                
                if is_tall and is_narrow:
                    # Check if it's on the left side
                    if fig_box.x < page_left + page_width * 0.2:
                        content_left = max(content_left, fig_box.x2)
                    
                    # Check if it's on the right side
                    if fig_box.x2 > page_right - page_width * 0.2:
                        content_right = min(content_right, fig_box.x)
        
        # Also check page layouts for sidebar-type elements
        if hasattr(page, 'page_layout') and page.page_layout:
            for layout in page.page_layout:
                if not layout.box:
                    continue
                
                layout_box = layout.box
                layout_height = layout_box.y2 - layout_box.y
                layout_width = layout_box.x2 - layout_box.x
                
                # Check if it's a header/footer or sidebar type layout
                layout_name = (layout.class_name or "").lower()
                
                # Skip actual content layouts
                if layout_name in ['text', 'paragraph', 'title', 'plain text', 'content']:
                    continue
                
                # Is it tall and narrow (sidebar-like)?
                is_tall = layout_height > page_height * 0.3
                is_narrow = layout_width < page_width * 0.25
                
                if is_tall and is_narrow:
                    if layout_box.x < page_left + page_width * 0.2:
                        content_left = max(content_left, layout_box.x2)
                    if layout_box.x2 > page_right - page_width * 0.2:
                        content_right = min(content_right, layout_box.x)
        
        # Ensure we have valid bounds
        if content_left >= content_right:
            # Reset to page bounds if something went wrong
            content_left = page_left
            content_right = page_right
        
        return (content_left, content_right)

    def _apply_rtl_margin_shift_to_composition(
        self, 
        composition: list[PdfParagraphComposition], 
        shift_x: float,
        page: il_version_1.Page = None
    ) -> None:
        """Apply RTL margin shift to all characters in a composition list.
        
        Args:
            composition: List of paragraph compositions to shift
            shift_x: The X shift to apply
            page: Optional page for clamping to page bounds
        """
        if shift_x == 0 or not composition:
            return
        
        # Get page bounds for clamping if page is provided
        clamp_right = None
        clamp_left = None
        if page and page.cropbox and page.cropbox.box:
            page_box = page.cropbox.box
            page_margin = (page_box.x2 - page_box.x) * 0.02
            clamp_left = page_box.x + page_margin
            clamp_right = page_box.x2 - page_margin

        # RIGID BODY CLAMPING: Clamp shift_x as a whole so that all characters
        # retain their exact relative spacing without stacking or colliding at the margin.
        effective_shift = shift_x
        if clamp_right is not None or clamp_left is not None:
            char_x2_list = []
            char_x_list = []
            for comp in composition:
                if comp.pdf_character and comp.pdf_character.box:
                    char_x_list.append(comp.pdf_character.box.x)
                    char_x2_list.append(comp.pdf_character.box.x2)
                elif comp.pdf_formula and comp.pdf_formula.box:
                    char_x_list.append(comp.pdf_formula.box.x)
                    char_x2_list.append(comp.pdf_formula.box.x2)

            if char_x2_list:
                max_x2 = max(char_x2_list)
                min_x = min(char_x_list)
                if clamp_right is not None and (max_x2 + effective_shift) > clamp_right:
                    effective_shift = clamp_right - max_x2
                if clamp_left is not None and (min_x + effective_shift) < clamp_left:
                    effective_shift = clamp_left - min_x

        if effective_shift == 0:
            return

        for comp in composition:
            if comp.pdf_character:
                char = comp.pdf_character
                if char.box:
                    char.box.x += effective_shift
                    char.box.x2 += effective_shift
                if char.visual_bbox and char.visual_bbox.box:
                    char.visual_bbox.box.x += effective_shift
                    char.visual_bbox.box.x2 += effective_shift
            elif comp.pdf_formula:
                formula = comp.pdf_formula
                if formula.box:
                    formula.box.x += effective_shift
                    formula.box.x2 += effective_shift
                for char in formula.pdf_character:
                    if char.box:
                        char.box.x += effective_shift
                        char.box.x2 += effective_shift
                    if char.visual_bbox and char.visual_bbox.box:
                        char.visual_bbox.box.x += effective_shift
                        char.visual_bbox.box.x2 += effective_shift
                for curve in formula.pdf_curve:
                    self._translate_rendered_curve(curve, dx=effective_shift)
                for form in formula.pdf_form:
                    if form.box:
                        form.box.x += effective_shift
                        form.box.x2 += effective_shift

    def _is_header_or_footer(self, paragraph: il_version_1.PdfParagraph, page: il_version_1.Page) -> bool:
        """Detect if paragraph is a running header, footer, or page number."""
        if paragraph.layout_label:
            lbl = paragraph.layout_label.lower()
            if any(h in lbl for h in ['header', 'footer', 'page_number', 'page_header', 'page_footer']):
                return True
        if paragraph.box and page.cropbox and page.cropbox.box:
            p_box = page.cropbox.box
            p_height = p_box.y2 - p_box.y
            # Top 6% or bottom 6% of page
            if paragraph.box.y > p_box.y2 - p_height * 0.06 or paragraph.box.y2 < p_box.y + p_height * 0.06:
                return True
        return False

    def _should_skip_margin_mirroring(self, paragraph: il_version_1.PdfParagraph, page: il_version_1.Page) -> bool:
        """Check if a paragraph should NOT have margin mirroring applied."""
        if self._is_header_or_footer(paragraph, page):
            return False  # Always mirror running headers, footers, and page numbers!
        return self._is_paragraph_inside_table(paragraph, page)

    def _is_paragraph_inside_table(self, paragraph: il_version_1.PdfParagraph, page: il_version_1.Page) -> bool:
        """Check if a paragraph should NOT have margin mirroring applied.
        
        This includes:
        1. Table cell content (should stay in place within table structure)
        2. Figure content (captions, axis labels, legends, text inside figures)
        3. Table footnotes
        4. Content that would overlap with page borders/sidebars after mirroring
        
        Uses multiple detection methods:
        1. Check layout_label for table/figure cell markers
        2. Check if paragraph is geometrically contained within a table/figure layout region
        3. Check if mirroring would cause overlap with page decorations
        
        Args:
            paragraph: The paragraph to check
            page: The page containing the paragraph
            
        Returns:
            True if paragraph should NOT be margin-mirrored
        """
        # Method 1: Check layout_label for content that should not be mirrored
        if paragraph.layout_label:
            layout_label_lower = paragraph.layout_label.lower()
            
            # Table cell and structural content markers
            table_cell_markers = [
                'table_cell', 'table_cell_hybrid', 'table_text', 
                'wired_table_cell', 'wireless_table_cell',
                'table_footnote', 'table_caption', 'fallback_line'
            ]
            
            # Figure content markers
            figure_content_markers = [
                'figure_text', 'figure_text_hybrid',
                'figure_caption', 'figure_title',
                'chart_title', 'chart_legend',
                'axis_label', 'axis_text'
            ]
            
            all_markers = table_cell_markers + figure_content_markers
            
            if any(marker in layout_label_lower for marker in all_markers):
                return True
        
        # Method 2: Check if paragraph is geometrically inside a table or figure layout region
        if paragraph.box:
            if self._is_inside_table_or_figure_region(paragraph.box, page):
                return True
        
        # Method 3: Check if mirroring would cause overlap with page decorations (sidebars, borders)
        if paragraph.box and page.cropbox and page.cropbox.box:
            if self._would_overlap_page_decoration(paragraph, page):
                return True
        
        return False

    def _is_inside_table_or_figure_region(
        self, para_box: Box, page: il_version_1.Page, *, figures_only: bool = False,
    ) -> bool:
        """Check if a paragraph box is geometrically inside a table or figure region.
        
        Args:
            para_box: The paragraph's bounding box
            page: The page containing the paragraph
            
        Returns:
            True if paragraph is inside a table or figure
        """
        # Get layouts from page
        if not hasattr(page, 'page_layout') or not page.page_layout:
            return False
        
        container_labels = {"figure", "chart", "graph", "diagram", "image"}
        if not figures_only:
            container_labels.update(("table", "wired_table", "wireless_table"))

        for layout in page.page_layout:
            if not layout.box:
                continue
                
            # Check if this is a table or figure layout
            layout_name = (layout.class_name or "").lower()
            
            if layout_name not in container_labels:
                continue
            
            # Check if paragraph is contained within this layout
            layout_box = layout.box
            
            # Calculate overlap
            overlap_x1 = max(para_box.x, layout_box.x)
            overlap_y1 = max(para_box.y, layout_box.y)
            overlap_x2 = min(para_box.x2, layout_box.x2)
            overlap_y2 = min(para_box.y2, layout_box.y2)
            
            if overlap_x1 < overlap_x2 and overlap_y1 < overlap_y2:
                # There is overlap - calculate how much of paragraph is inside
                overlap_area = (overlap_x2 - overlap_x1) * (overlap_y2 - overlap_y1)
                para_area = (para_box.x2 - para_box.x) * (para_box.y2 - para_box.y)
                
                if para_area > 0:
                    containment_ratio = overlap_area / para_area
                    # If more than 50% of paragraph is inside, consider it contained
                    if containment_ratio > 0.5:
                        return True
        
        return False

    def _is_table_cell(self, paragraph: il_version_1.PdfParagraph, page: il_version_1.Page) -> bool:
        """Check if paragraph is a table cell within a grid table."""
        label = (paragraph.layout_label or "").lower()
        is_table_text = any(marker in label for marker in ("table_cell", "table_text"))
        is_caption = any(marker in label for marker in ("table_caption", "table_title", "table_footnote"))
        if is_table_text and not is_caption:
            return True
        # Check if inside a table layout region
        if paragraph.box and hasattr(page, 'page_layout') and page.page_layout:
            for layout in page.page_layout:
                if layout.box and (layout.class_name or "").lower() in ['table', 'wired_table', 'wireless_table']:
                    overlap_x1 = max(paragraph.box.x, layout.box.x)
                    overlap_x2 = min(paragraph.box.x2, layout.box.x2)
                    overlap_y1 = max(paragraph.box.y, layout.box.y)
                    overlap_y2 = min(paragraph.box.y2, layout.box.y2)
                    if overlap_x1 < overlap_x2 and overlap_y1 < overlap_y2:
                        para_area = (paragraph.box.x2 - paragraph.box.x) * (paragraph.box.y2 - paragraph.box.y)
                        overlap_area = (overlap_x2 - overlap_x1) * (overlap_y2 - overlap_y1)
                        if para_area > 0 and (overlap_area / para_area) > 0.5:
                            return True
        # Fallback text inside a figure belongs to that drawing's coordinates.
        return (
            "fallback_line" in label and not is_caption and paragraph.box is not None
            and not self._is_inside_table_or_figure_region(paragraph.box, page, figures_only=True)
        )

    def _cluster_table_cells_into_rows(self, page: il_version_1.Page) -> list[list[il_version_1.PdfParagraph]]:
        """Cluster table cell paragraphs on a page into rows based on vertical overlap."""
        table_cells = [
            p for p in page.pdf_paragraph
            if p.box and p.box.x is not None and p.box.y is not None
            and p.box.x2 is not None and p.box.y2 is not None
            and self._is_table_cell(p, page)
        ]
        if not table_cells:
            return []

        # Sort cells descending by top Y (y2), then by x
        sorted_cells = sorted(table_cells, key=lambda c: (-c.box.y2, c.box.x))

        rows: list[list[il_version_1.PdfParagraph]] = []
        for cell in sorted_cells:
            cell_y = cell.box.y
            cell_y2 = cell.box.y2
            cell_h = max(cell_y2 - cell_y, 1.0)

            matched_row = None
            for row in rows:
                ref_cell = row[0]
                ref_y = ref_cell.box.y
                ref_y2 = ref_cell.box.y2
                ref_h = max(ref_y2 - ref_y, 1.0)

                v_overlap = min(cell_y2, ref_y2) - max(cell_y, ref_y)
                # Overlap must be at least 40% of the smaller cell's height
                if v_overlap > 0.40 * min(cell_h, ref_h):
                    # Check horizontal overlap with existing cells in the row
                    # (cells in the same row should be in separate columns)
                    h_collision = any(
                        not (cell.box.x2 <= other.box.x + 1.0 or cell.box.x >= other.box.x2 - 1.0)
                        for other in row
                    )
                    if not h_collision:
                        matched_row = row
                        break

            if matched_row is not None:
                matched_row.append(cell)
            else:
                rows.append([cell])

        # Sort each row horizontally left-to-right (increasing x)
        for row in rows:
            row.sort(key=lambda c: c.box.x)

        return rows

    @staticmethod
    def _boxes_overlap_horizontally(left: Box, right: Box) -> bool:
        return left.x < right.x2 and left.x2 > right.x

    def _table_cell_groups(
        self, page: il_version_1.Page
    ) -> list[tuple[list[il_version_1.PdfParagraph], Box]]:
        """Return table-cell groups with stable table bounds.

        Prefer the layout parser's table region when it is available. The
        fallback groups visually adjacent rows, which keeps independent tables
        on the same page from being mirrored as one large table.
        """
        cells = [
            p for p in page.pdf_paragraph
            if p.box and self._is_table_cell(p, page)
        ]
        if not cells:
            return []

        groups: list[tuple[list[il_version_1.PdfParagraph], Box]] = []
        assigned: set[int] = set()
        for layout in getattr(page, "page_layout", []) or []:
            layout_box = getattr(layout, "box", None)
            if not layout_box or (getattr(layout, "class_name", "") or "").lower() not in {
                "table", "wired_table", "wireless_table"
            }:
                continue
            members = []
            for cell in cells:
                overlap_x = max(0.0, min(cell.box.x2, layout_box.x2) - max(cell.box.x, layout_box.x))
                overlap_y = max(0.0, min(cell.box.y2, layout_box.y2) - max(cell.box.y, layout_box.y))
                cell_area = max((cell.box.x2 - cell.box.x) * (cell.box.y2 - cell.box.y), 1.0)
                if (overlap_x * overlap_y) / cell_area > 0.5:
                    members.append(cell)
                    assigned.add(id(cell))
            if members:
                groups.append((members, copy.deepcopy(layout_box)))

        remaining_rows = []
        for row in self._cluster_table_cells_into_rows(page):
            row = [cell for cell in row if id(cell) not in assigned]
            if row:
                remaining_rows.append(row)

        # Link rows only while their vertical gap is small and their horizontal
        # ranges overlap. This accommodates colspan headers without joining a
        # later, unrelated table.
        for row in remaining_rows:
            row_box = Box(
                min(cell.box.x for cell in row),
                min(cell.box.y for cell in row),
                max(cell.box.x2 for cell in row),
                max(cell.box.y2 for cell in row),
            )
            if groups:
                members, group_box = groups[-1]
                vertical_gap = max(0.0, group_box.y - row_box.y2)
                row_height = max(row_box.y2 - row_box.y, 1.0)
                group_height = max(group_box.y2 - group_box.y, 1.0)
                if (
                    vertical_gap <= max(18.0, min(row_height, group_height) * 1.5)
                    and self._boxes_overlap_horizontally(group_box, row_box)
                ):
                    members.extend(row)
                    groups[-1] = (
                        members,
                        Box(
                            min(group_box.x, row_box.x),
                            min(group_box.y, row_box.y),
                            max(group_box.x2, row_box.x2),
                            max(group_box.y2, row_box.y2),
                        ),
                    )
                    continue
            groups.append((list(row), row_box))

        return groups

    @staticmethod
    def _mirror_box_within_table(box: Box, table_box: Box) -> None:
        old_x, old_x2 = box.x, box.x2
        box.x = table_box.x2 - (old_x2 - table_box.x)
        box.x2 = table_box.x2 - (old_x - table_box.x)

    def _mirror_table_grids_for_rtl(self, page: il_version_1.Page) -> None:
        """Mirror cell geometry and table-local vector geometry for Arabic RTL.

        Per-cell right alignment cannot reverse an LTR table. This pass reflects
        every cell about its table bounds before layout, preserving column widths
        and moving merged headers with their child columns.
        """
        if not self._is_arabic_output() or id(page) in self._rtl_mirrored_table_pages:
            return

        table_groups = self._table_cell_groups(page)
        if not table_groups:
            self._rtl_mirrored_table_pages.add(id(page))
            return

        for cells, table_box in table_groups:
            # ParagraphFinder reconstructed these cells from the ruled grid.
            # Their physical slots must stay aligned with the grid retained in
            # the page base operations; only text inside each slot becomes RTL.
            if cells and all(
                "fixed_rtl" in (cell.layout_label or "").lower()
                for cell in cells
            ):
                continue
            for cell in cells:
                self._mirror_box_within_table(cell.box, table_box)

            # Tables may be represented by colored cell rectangles or by vector
            # rules. Mirror only elements wholly contained in this table, leaving
            # page decorations and charts untouched.
            for rect in getattr(page, "pdf_rectangle", []) or []:
                if rect.box and (
                    rect.box.x >= table_box.x - 0.5
                    and rect.box.x2 <= table_box.x2 + 0.5
                    and rect.box.y >= table_box.y - 0.5
                    and rect.box.y2 <= table_box.y2 + 0.5
                ):
                    self._mirror_box_within_table(rect.box, table_box)

            for curve in getattr(page, "pdf_curve", []) or []:
                if not curve.box or not (
                    curve.box.x >= table_box.x - 0.5
                    and curve.box.x2 <= table_box.x2 + 0.5
                    and curve.box.y >= table_box.y - 0.5
                    and curve.box.y2 <= table_box.y2 + 0.5
                ):
                    continue
                self._mirror_box_within_table(curve.box, table_box)
                for path in curve.pdf_path:
                    if path.x is not None:
                        path.x = table_box.x + table_box.x2 - path.x

        self._rtl_mirrored_table_pages.add(id(page))

    def _balance_table_row_widths(self, page: il_version_1.Page, fonts: dict):
        """Redistribute unused horizontal space from sibling columns in the same row to overflowing cells."""
        rows = self._cluster_table_cells_into_rows(page)
        for row in rows:
            if any("fixed_rtl" in (cell.layout_label or "").lower() for cell in row):
                continue
            if len(row) < 2:
                continue

            # Check each adjacent pair of cells (left cell, right cell)
            for i in range(len(row) - 1):
                cell_left = row[i]
                cell_right = row[i + 1]

                units_left = self.create_typesetting_units(cell_left, fonts)
                units_right = self.create_typesetting_units(cell_right, fonts)

                w_left = cell_left.box.x2 - cell_left.box.x
                w_right = cell_right.box.x2 - cell_right.box.x

                req_left = sum(u.width for u in units_left)
                req_right = sum(u.width for u in units_right)

                right_is_passthrough = all(u.can_passthrough for u in units_right)
                left_is_passthrough = all(u.can_passthrough for u in units_left)

                # Case 1: Left cell overflows, and Right cell has surplus
                if req_left > w_left and (right_is_passthrough or req_right + 15.0 < w_right):
                    surplus_right = w_right - (req_right + 12.0)
                    deficit_left = req_left - w_left + 6.0
                    if surplus_right > 2.0:
                        shift = min(deficit_left, surplus_right * 0.85)
                        cell_left.box.x2 += shift
                        cell_right.box.x += shift

                # Case 2: Right cell overflows, and Left cell has surplus
                elif req_right > w_right and (left_is_passthrough or req_left + 15.0 < w_left):
                    surplus_left = w_left - (req_left + 12.0)
                    deficit_right = req_right - w_right + 6.0
                    if surplus_left > 2.0:
                        shift = min(deficit_right, surplus_left * 0.85)
                        cell_left.box.x2 -= shift
                        cell_right.box.x -= shift

    def _synchronize_table_row_heights(self, page: il_version_1.Page):
        """Synchronize row height across all cells in each table row so the table does not look jagged."""
        rows = self._cluster_table_cells_into_rows(page)
        for row in rows:
            if any("fixed_rtl" in (cell.layout_label or "").lower() for cell in row):
                continue
            if len(row) < 2:
                continue

            row_min_y = min(c.box.y for c in row if c.box and c.box.y is not None)
            row_max_y2 = max(c.box.y2 for c in row if c.box and c.box.y2 is not None)

            for cell in row:
                cell.box.y = row_min_y
                cell.box.y2 = row_max_y2

    def _would_overlap_page_decoration(self, paragraph: il_version_1.PdfParagraph, page: il_version_1.Page) -> bool:
        """Check if margin mirroring would cause the paragraph to overlap with page decorations.
        
        Page decorations include sidebars, borders, colored regions, etc.
        We detect these by checking if there are rectangles/figures near the page edges
        that the mirrored paragraph would overlap with.
        
        Args:
            paragraph: The paragraph to check
            page: The page containing the paragraph
            
        Returns:
            True if mirroring would cause overlap with decorations
        """
        if not paragraph.box or not page.cropbox or not page.cropbox.box:
            return False
        
        para_box = paragraph.box
        page_box = page.cropbox.box
        
        # Calculate where the paragraph would be after mirroring
        shift_x = page_box.x + page_box.x2 - para_box.x2 - para_box.x
        
        new_x = para_box.x + shift_x
        new_x2 = para_box.x2 + shift_x
        
        # Check if the new position is outside the safe content area
        # Allow some margin (5% of page width) for borders/sidebars
        page_width = page_box.x2 - page_box.x
        safe_margin = page_width * 0.05
        
        # If the mirrored position would be too close to or beyond page edges, skip mirroring
        if new_x < page_box.x + safe_margin or new_x2 > page_box.x2 - safe_margin:
            # Additional check: see if there are rectangles (potential decorations) in the target area
            if hasattr(page, 'pdf_rectangle') and page.pdf_rectangle:
                for rect in page.pdf_rectangle:
                    if not rect.box:
                        continue
                    
                    rect_box = rect.box
                    
                    # Check if this rectangle is a page decoration (tall, near edge)
                    rect_height = rect_box.y2 - rect_box.y
                    page_height = page_box.y2 - page_box.y
                    
                    # Is it a tall rectangle (sidebar-like)?
                    is_tall = rect_height > page_height * 0.3
                    
                    # Is it near the left or right edge?
                    is_near_left = rect_box.x < page_box.x + page_width * 0.15
                    is_near_right = rect_box.x2 > page_box.x2 - page_width * 0.15
                    
                    if is_tall and (is_near_left or is_near_right):
                        # Check if mirrored paragraph would overlap with this decoration
                        if (new_x < rect_box.x2 and new_x2 > rect_box.x and
                            para_box.y < rect_box.y2 and para_box.y2 > rect_box.y):
                            return True
            
            # Also check for figures that might be decorative elements
            if hasattr(page, 'pdf_figure') and page.pdf_figure:
                for figure in page.pdf_figure:
                    if not figure.box:
                        continue
                    
                    fig_box = figure.box
                    
                    # Check if this figure is near the edge (potential decoration)
                    fig_width = fig_box.x2 - fig_box.x
                    is_near_left = fig_box.x < page_box.x + page_width * 0.15
                    is_near_right = fig_box.x2 > page_box.x2 - page_width * 0.15
                    
                    if is_near_left or is_near_right:
                        # Check if mirrored paragraph would overlap
                        if (new_x < fig_box.x2 and new_x2 > fig_box.x and
                            para_box.y < fig_box.y2 and para_box.y2 > fig_box.y):
                            return True
        
        return False

    def render_paragraph(
        self,
        paragraph: il_version_1.PdfParagraph,
        page: il_version_1.Page,
        fonts: dict[
            str | int,
            il_version_1.PdfFont | dict[str, il_version_1.PdfFont],
        ],
    ):
        typesetting_units = self._deduplicate_artificial_header_units(
            paragraph, page, self.create_typesetting_units(paragraph, fonts, page=page)
        )
        
        # Check if Arabic output for RTL margin mirroring
        is_arabic_output = self._is_arabic_output()
        
        # Check if this paragraph is table CELL content that should NOT have margin mirroring
        # Table cells should maintain their original positions within the table structure
        # Note: table_caption, table_title, table_footnote are NOT excluded - they should be mirrored
        is_table_content = self._is_paragraph_inside_table(paragraph, page)
        
        # Initialize or update pipeline trace for diagnostic reporting
        if not hasattr(paragraph, "pipeline_trace") or paragraph.pipeline_trace is None:
            paragraph.pipeline_trace = {}
        paragraph.pipeline_trace.update({
            "is_arabic_output": is_arabic_output,
            "is_table_content": is_table_content,
            "layout_label": paragraph.layout_label,
            "can_passthrough": all(unit.can_passthrough for unit in typesetting_units),
        })

        # If all typesetting units can be passed through directly, no need to layout
        if all(unit.can_passthrough for unit in typesetting_units):
            paragraph.scale = 1.0
            paragraph.pdf_paragraph_composition = self.create_passthrough_composition(
                typesetting_units,
            )
            
            # Apply RTL margin mirroring for Arabic output on passthrough paragraphs
            # Skip table content to maintain table structure
            if is_arabic_output and not is_table_content:
                shift_x = self._calculate_rtl_margin_shift(paragraph, page)
                if shift_x != 0:
                    self._apply_rtl_margin_shift_to_composition(
                        paragraph.pdf_paragraph_composition, 
                        shift_x,
                        page  # Pass page for clamping
                    )
                    # Also update the paragraph box to reflect the new position
                    if paragraph.box:
                        paragraph.box.x += shift_x
                        paragraph.box.x2 += shift_x
                        # Clamp paragraph box to page bounds
                        if page.cropbox and page.cropbox.box:
                            page_box = page.cropbox.box
                            page_margin = (page_box.x2 - page_box.x) * 0.02
                            if paragraph.box.x2 > page_box.x2 - page_margin:
                                overflow = paragraph.box.x2 - (page_box.x2 - page_margin)
                                paragraph.box.x -= overflow
                                paragraph.box.x2 -= overflow
                            if paragraph.box.x < page_box.x + page_margin:
                                underflow = (page_box.x + page_margin) - paragraph.box.x
                                paragraph.box.x += underflow
                                paragraph.box.x2 += underflow
        else:
            # Use precomputed scale factor to layout typesetting units
            precomputed_scale = (
                paragraph.optimal_scale if paragraph.optimal_scale is not None else 1.0
            )

            # If precomputed scale is available, use precomputed scale for layout
            paragraph.pdf_paragraph_composition = []
            self.retypeset_with_precomputed_scale(
                paragraph, page, typesetting_units, precomputed_scale
            )

            # Update paragraph render order for child characters
            self._update_paragraph_render_order(paragraph)
            
            # Apply RTL margin mirroring for Arabic output on layout paragraphs
            # Apply RTL margin mirroring for Arabic output on body paragraphs
            # Skip tables, figures, charts, captions, footnotes, and fallback lines
            if is_arabic_output and not self._should_skip_margin_mirroring(paragraph, page):
                shift_x = self._calculate_rtl_margin_shift(paragraph, page)
                if shift_x != 0:
                    self._apply_rtl_margin_shift_to_composition(
                        paragraph.pdf_paragraph_composition, 
                        shift_x,
                        page  # Pass page for clamping
                    )
                    # Also update the paragraph box to reflect the new position
                    if paragraph.box:
                        paragraph.box.x += shift_x
                        paragraph.box.x2 += shift_x

    def _is_arabic_char(self, char: str) -> bool:
        """Check if character is Arabic - OPTIMIZED"""
        if not char:
            return False
        try:
            code_point = ord(char[0])
            return (0x0600 <= code_point <= 0x06FF) or (0xFB50 <= code_point <= 0xFDFF) or (0xFE70 <= code_point <= 0xFEFF)
        except:
            return False

    def _layout_typesetting_units(
        self,
        typesetting_units: list[TypesettingUnit],
        box: Box,
        scale: float,
        line_skip: float,
        paragraph: il_version_1.PdfParagraph,
        use_english_line_break: bool = True,
        *,
        centered_display: bool = False,
    ) -> tuple[list[TypesettingUnit], bool]:
        """Layout typesetting units - OPTIMIZED FOR ARABIC RTL"""
        
        # Detect Arabic FIRST
        lang_out = (self.translation_config.lang_out or "").lower()
        is_arabic = any(marker in lang_out for marker in ["ar", "arabic", "ara"])
        
        # Calculate font size
        font_sizes = []
        for unit in typesetting_units:
            if unit.font_size:
                font_sizes.append(unit.font_size)
            if unit.char and unit.char.pdf_style and unit.char.pdf_style.font_size:
                font_sizes.append(unit.char.pdf_style.font_size)
        if not font_sizes:
            font_sizes = [12]
        font_sizes.sort()
        font_size = statistics.mode(font_sizes)

        space_width = (
            self.font_mapper.base_font.char_lengths(" ", font_size * scale)[0] * 0.5
        )

        # Calculate line height
        unit_heights = [unit.height for unit in typesetting_units] if typesetting_units else []
        if not unit_heights:
            avg_height = 0
        elif len(unit_heights) == 1:
            avg_height = unit_heights[0] * scale
        else:
            try:
                avg_height = statistics.mode(unit_heights) * scale
            except statistics.StatisticsError:
                avg_height = sum(unit_heights) / len(unit_heights) * scale

        # Initialize position
        current_x = box.x
        current_y = box.y2 - avg_height
        box = copy.deepcopy(box)
        line_height = 0
        current_line_heights = []
        typeset_units = []
        all_units_fit = True
        last_unit: TypesettingUnit | None = None
        line_ys = [current_y]
        current_line_index = 0
        
        if paragraph.first_line_indent:
            current_x += space_width * 4
        
        # OPTIMIZED ARABIC WORD-LEVEL PROCESSING WITH PER-LINE BIDI
        if is_arabic:
            def get_bidi_indices_and_chars(text: str, base_dir: str = 'R') -> list[tuple[int, str]]:
                try:
                    storage = ba.get_empty_storage()
                    storage['base_level'] = 1 if base_dir == 'R' else 0
                    storage['base_dir'] = base_dir
                    ba.get_embedding_levels(text, storage, False, False)
                    for i, c in enumerate(storage['chars']):
                        c['orig_idx'] = i
                    ba.explicit_embed_and_overrides(storage, False)
                    ba.resolve_weak_types(storage, False)
                    ba.resolve_neutral_types(storage, False)
                    ba.resolve_implicit_levels(storage, False)
                    ba.reorder_resolved_levels(storage, False)
                    ba.apply_mirroring(storage, False)
                    return [(c['orig_idx'], c['ch']) for c in storage['chars']]
                except Exception as e:
                    logger.warning(f"Error in per-line bidi: {e}")
                    return [(i, ch) for i, ch in enumerate(text)]

            from babeldoc.format.pdf.document_il.utils.layout_helper import BULLET_POINT_PATTERN

            # 1. Break typesetting_units into tokens:
            # Each token is either a single space unit, or a word (list of non-space units).
            # Bullet points (standard or PUA like \uF0B7) are isolated into their own tokens
            # so they never glue to adjacent words.
            tokens: list[list[TypesettingUnit]] = []
            idx = 0
            n = len(typesetting_units)
            while idx < n:
                u = typesetting_units[idx]
                if u.forced_line_break:
                    tokens.append([u])
                    idx += 1
                    continue
                u_str = u.try_get_unicode() or ""
                if u.is_space:
                    tokens.append([u])
                    idx += 1
                elif u.is_drawn_list_bullet or BULLET_POINT_PATTERN.match(u_str) or (u_str and 0xE000 <= ord(u_str[0]) <= 0xF8FF):
                    # Standalone bullet token: never glue bullet to following word
                    tokens.append([u])
                    idx += 1
                else:
                    word = [u]
                    idx += 1
                    while idx < n:
                        next_u = typesetting_units[idx]
                        if next_u.forced_line_break:
                            break
                        next_str = next_u.try_get_unicode() or ""
                        if next_u.is_space or BULLET_POINT_PATTERN.match(next_str) or (next_str and 0xE000 <= ord(next_str[0]) <= 0xF8FF):
                            break
                        word.append(next_u)
                        idx += 1
                    tokens.append(word)

            # 2. Wrap tokens into lines in LOGICAL order:
            effective_line_width = box.x2 - box.x
            first_indent = space_width * 4 if paragraph.first_line_indent else 0.0

            # Detect bullet points and list markers for RTL hanging indent (Item 2)
            is_bullet_para = False
            bullet_indent = 0.0
            if tokens and not tokens[0][0].is_space:
                first_token_str = "".join(u.try_get_unicode() or "" for u in tokens[0])
                first_char_code = ord(first_token_str[0]) if first_token_str else 0
                if (
                    BULLET_POINT_PATTERN.match(first_token_str)
                    or (0xE000 <= first_char_code <= 0xF8FF)
                    or re.match(r'^(?:\d{1,3}|[a-zA-Z]|(?:i{1,3}|iv|v|vi{1,3}|ix|x))[\.\):]', first_token_str)
                    or (paragraph.layout_label and any(m in paragraph.layout_label.lower() for m in ['list_item', 'bullet_item']))
                ):
                    is_bullet_para = True
                    first_token_w = sum(u.width * scale for u in tokens[0])
                    bullet_indent = max(first_token_w + (space_width * 2.5), 14.0)

            lines_tokens: list[list[TypesettingUnit]] = []
            current_line_tokens: list[TypesettingUnit] = []
            current_line_w = 0.0

            for token_idx, token in enumerate(tokens):
                # Explicit line boundaries originate from structured source
                # records (Step N, calculation/result, contact fields).  They
                # are semantic boundaries, so width-based wrapping must never
                # collapse two records onto the same visual line.
                if token[0].forced_line_break:
                    while current_line_tokens and current_line_tokens[-1].is_space:
                        current_line_tokens.pop()
                    lines_tokens.append(current_line_tokens)
                    current_line_tokens = []
                    current_line_w = 0.0
                    continue
                # Dynamic Table of Contents leader dot adjustment:
                # If this token is a run of leader dots (4+ dots) followed by a page number,
                # dynamically resize the dot count so title + dots + page_number fills avail on a single line!
                if all(u.unicode == '.' for u in token) and len(token) >= 4:
                    rem = tokens[token_idx + 1:]
                    non_space = [t for t in rem if not t[0].is_space]
                    if non_space and all(u.unicode.isdigit() for u in non_space[-1]):
                        pnum_w = sum(u.width * scale for u in non_space[-1])
                        dot_unit = token[0]
                        dot_w = dot_unit.width * scale
                        avail_dots = avail - current_line_w - pnum_w - (space_width * 3)
                        if avail_dots > (dot_w * 4):
                            needed = int(avail_dots / dot_w)
                            token[:] = [copy.copy(dot_unit) for _ in range(needed)]

                token_w = sum(u.width * scale for u in token)
                line_idx_current = len(lines_tokens)
                if is_bullet_para and line_idx_current > 0:
                    avail = effective_line_width - bullet_indent
                elif line_idx_current == 0 and paragraph.first_line_indent:
                    avail = effective_line_width - first_indent
                else:
                    avail = effective_line_width

                # Skip leading space on a line
                if not current_line_tokens and token[0].is_space:
                    continue

                if current_line_tokens and (current_line_w + token_w > avail):
                    # Wrap to new line: trim trailing spaces
                    while current_line_tokens and current_line_tokens[-1].is_space:
                        current_line_tokens.pop()
                    if current_line_tokens:
                        lines_tokens.append(current_line_tokens)
                    current_line_tokens = []
                    current_line_w = 0.0
                    if token[0].is_space:
                        continue

                current_line_tokens.extend(token)
                current_line_w += token_w

            if current_line_tokens:
                while current_line_tokens and current_line_tokens[-1].is_space:
                    current_line_tokens.pop()
                if current_line_tokens:
                    lines_tokens.append(current_line_tokens)

            # Record line stats in pipeline trace
            if not hasattr(paragraph, "pipeline_trace") or paragraph.pipeline_trace is None:
                paragraph.pipeline_trace = {}
            paragraph.pipeline_trace.update({
                "num_lines": len(lines_tokens),
                "is_bullet_para": is_bullet_para,
                "bullet_indent": bullet_indent if is_bullet_para else 0.0,
            })

            # Check if paragraph is inside table
            is_table_content = False
            if paragraph.layout_label:
                layout_label_lower = paragraph.layout_label.lower()
                if any(table_marker in layout_label_lower for table_marker in [
                    'table_cell', 'table_text', 'wired_table_cell', 'wireless_table_cell'
                ]):
                    is_table_content = True
            centered_table_header = paragraph.layout_label == "wired_table_cell_fixed_rtl_centered"

            # 3. Layout each line with per-line BiDi
            for line_idx, line in enumerate(lines_tokens):
                if not line:
                    continue

                # A list marker belongs at the RTL margin even when the
                # translated item consists entirely of Latin text.
                bullet_prefix = []
                if line_idx == 0 and line[0].is_drawn_list_bullet:
                    prefix_length = 2 if len(line) > 1 and line[1].is_space else 1
                    bullet_prefix = line[:prefix_length]
                    line = line[prefix_length:]

                line_characters = [unit.try_get_unicode() or "" for unit in line]
                line_str = "".join(line_characters)
                
                # Check if this line is a chronological time-series axis (e.g., sequence of month/year labels)
                # In charts, timelines progress Left-to-Right even in Arabic contexts to match the chart data curves
                months_pattern = r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|يناير|فبراير|مارس|أبريل|مايو|يونيو|يوليو|أغسطس|سبتمبر|أكتوبر|نوفمبر|ديسمبر|أوج)\b'
                is_chronological_axis = len(re.findall(months_pattern, line_str, re.I)) >= 3

                # Check if line contains isolated Latin alphanumeric codes without any Arabic text
                # (e.g. ISO country codes AL, BA, GB, CZ; SWIFT BICs, arithmetic formulas)
                has_arabic_chars = any(
                    (0x0600 <= ord(c) <= 0x06FF) or (0xFB50 <= ord(c) <= 0xFEFF)
                    for c in line_str
                )
                is_isolated_latin = (not has_arabic_chars) and bool(
                    re.match(r'^[A-Za-z0-9\s\-–—/._\(\)+:=#\$\€\£\¥\%\*]+$', line_str.strip())
                )
                is_procedure_code = (
                    "procedure_step" in (paragraph.layout_label or "").lower()
                    and is_isolated_latin
                    and bool(re.search(r'\d|[A-Z]{4,}', line_str))
                )

                if is_chronological_axis or is_isolated_latin:
                    # Keep native LTR character order matching original code, formula, or timeline
                    visual_units = line
                elif line_str.strip():
                    # Give BiDi one position per visible text unit and one atomic
                    # position per consecutive formula run. A technical expression
                    # is commonly extracted as several one-character PdfFormula
                    # objects (W, =, {, w1, ...). Reordering those objects separately
                    # reverses the expression inside an Arabic line.
                    bidi_atoms = []
                    for unit, text in zip(line, line_characters):
                        if unit.formular:
                            if bidi_atoms and bidi_atoms[-1]["is_formula"]:
                                bidi_atoms[-1]["units"].append(unit)
                            else:
                                bidi_atoms.append(
                                    {"text": "\ufffc", "units": [unit], "is_formula": True}
                                )
                        else:
                            bidi_atoms.append(
                                {
                                    "text": text if len(text) == 1 else "\ufffc",
                                    "units": [unit],
                                    "is_formula": False,
                                }
                            )

                    # A formula can contain just the subscript of an identifier
                    # whose base is a separate text unit. A neutral object in
                    # that position can move ahead of the base in an RTL line.
                    for index, atom in enumerate(bidi_atoms):
                        if not atom["is_formula"]:
                            continue
                        neighbors = bidi_atoms[max(0, index - 1):index] + bidi_atoms[index + 1:index + 2]
                        if not any(
                            not item["is_formula"]
                            and unicodedata.bidirectional(item["text"]) in {"L", "EN"}
                            for item in neighbors
                        ):
                            continue
                        directions = {
                            unicodedata.bidirectional(glyph)
                            for unit in atom["units"]
                            for char in unit.formular.pdf_character
                            for glyph in char.char_unicode or ""
                        }
                        if directions & {"L", "EN"} and not directions & {"R", "AL", "AN"}:
                            atom["text"] = "L"

                    bidi_text = "".join(atom["text"] for atom in bidi_atoms)
                    bidi_map = get_bidi_indices_and_chars(bidi_text, base_dir='R')
                    visual_units = []
                    visual_indices = []
                    for orig_idx, mirrored_char in bidi_map:
                        if orig_idx >= len(bidi_atoms):
                            continue
                        atom = bidi_atoms[orig_idx]
                        atom_units = [copy.copy(unit) for unit in atom["units"]]
                        if not atom["is_formula"] and len(atom_units) == 1:
                            unit = atom_units[0]
                            if mirrored_char and unit.unicode and unit.unicode != mirrored_char:
                                unit.unicode = mirrored_char
                        visual_units.extend(atom_units)
                        visual_indices.extend([orig_idx] * len(atom_units))

                    # LRM-delimited formulas/codes are semantic LTR islands.
                    # Unicode BiDi correctly places the island in an Arabic
                    # line, but some extracted PDF runs still have their
                    # operators reversed afterwards. Restore each island's
                    # *logical* unit sequence without moving the island.
                    marker = "\u200e"
                    start = 0
                    while True:
                        left = bidi_text.find(marker, start)
                        if left < 0:
                            break
                        right = bidi_text.find(marker, left + 1)
                        if right < 0:
                            break
                        positions = [
                            pos for pos, source_index in enumerate(visual_indices)
                            if left < source_index < right
                        ]
                        if len(positions) > 1:
                            ordered = sorted(
                                (
                                    visual_indices[pos],
                                    sequence,
                                    visual_units[pos],
                                )
                                for sequence, pos in enumerate(positions)
                            )
                            for pos, (source_index, _sequence, unit) in zip(positions, ordered):
                                visual_indices[pos] = source_index
                                visual_units[pos] = unit
                        start = right + 1
                else:
                    visual_units = line

                if bullet_prefix:
                    visual_units = [*visual_units, *reversed(bullet_prefix)]

                # Calculate total line width
                visual_line_w = sum(u.width * scale for u in visual_units)

                # Check horizontal overflow within paragraph/cell box
                if visual_line_w > (box.x2 - box.x) + 0.5:
                    all_units_fit = False

                # Hanging indent on wrapped lines (line_idx >= 1) for bullets
                if is_bullet_para and line_idx > 0:
                    current_indent = bullet_indent
                elif line_idx == 0 and paragraph.first_line_indent:
                    current_indent = first_indent
                else:
                    current_indent = 0.0

                # Determine starting X position
                if is_chronological_axis:
                    line_start_x = box.x
                elif centered_display or centered_table_header:
                    line_start_x = max(box.x, (box.x + box.x2 - visual_line_w) / 2)
                elif is_table_content:
                    line_start_x = box.x2 - visual_line_w
                    if line_start_x < box.x:
                        line_start_x = box.x
                else:
                    # Codes, equations and section identifiers retain their
                    # native LTR character order, but they are content of an
                    # RTL paragraph.  Anchoring them at ``box.x`` placed them
                    # on the English-side margin after mirroring.
                    line_start_x = (box.x2 - current_indent) - visual_line_w
                    if line_start_x < box.x:
                        line_start_x = box.x

                # Place each unit along the line LTR from line_start_x
                cur_x = line_start_x
                line_heights = []
                for u in visual_units:
                    relocated = u.relocate(cur_x, current_y, scale)
                    relocated.line_id = line_idx
                    if is_procedure_code:
                        relocated.presentation_role = "procedure_code"
                    if is_table_content and relocated.box and relocated.box.x2 > box.x2 + 0.5:
                        all_units_fit = False
                    typeset_units.append(relocated)
                    cur_x = relocated.box.x2
                    if not u.is_space:
                        line_heights.append(u.height * scale)

                # Advance Y for the next line only if there is another line to place
                if line_idx < len(lines_tokens) - 1:
                    if line_heights:
                        max_h = max(line_heights)
                        try:
                            mode_h = statistics.mode(line_heights)
                        except statistics.StatisticsError:
                            mode_h = max_h
                        current_y -= max(mode_h * line_skip, max_h * 1.05)
                    else:
                        current_y -= avg_height * line_skip

                    line_ys.append(current_y)
                    if current_y < box.y:
                        all_units_fit = False
        else:
            # ORIGINAL NON-ARABIC LOGIC (UNCHANGED)
            for i, unit in enumerate(typesetting_units):
                unit_width = unit.width * scale
                unit_height = unit.height * scale

                if current_x == box.x and unit.is_space:
                    continue

                if (last_unit and last_unit.is_cjk_char ^ unit.is_cjk_char
                    and last_unit.box and last_unit.box.y
                    and current_y - 0.1 <= last_unit.box.y2 <= current_y + line_height + 0.1
                    and not last_unit.mixed_character_blacklist and not unit.mixed_character_blacklist
                    and current_x > box.x and unit.try_get_unicode() != " "
                    and last_unit.try_get_unicode() != " "
                    and last_unit.try_get_unicode() not in ["ÃƒÂ£Ã¢â€šÂ¬Ã‚Â", "ÃƒËœÃ…â€™", "ÃƒÂ£Ã¢â€šÂ¬Ã¢â‚¬Å¡", "ÃƒÂ¯Ã‚Â¼Ã…Â¡", "ÃƒÂ¯Ã‚Â¼Ã‚Â", "ÃƒÂ¯Ã‚Â¼Ã…Â¸"]):
                    current_x += space_width * 0.5
                
                if use_english_line_break:
                    width_before_next_break_point = self._get_width_before_next_break_point(typesetting_units[i:], scale)
                else:
                    width_before_next_break_point = 0

                if not unit.is_hung_punctuation and (
                    (current_x + unit_width > box.x2) or
                    (use_english_line_break and current_x + unit_width + width_before_next_break_point > box.x2) or
                    (unit.is_cannot_appear_in_line_end_punctuation and current_x + unit_width * 2 > box.x2)):
                    
                    current_x = box.x
                    if not current_line_heights:
                        return [], False
                    max_height = max(current_line_heights)
                    mode_height = statistics.mode(current_line_heights)
                    current_y -= max(mode_height * line_skip, max_height * 1.05)
                    line_ys.append(current_y)
                    line_height = 0.0
                    current_line_heights = []

                    if current_y < box.y:
                        all_units_fit = False

                    if unit.is_space:
                        line_height = max(line_height, unit_height)
                        continue

                relocated_unit = unit.relocate(current_x, current_y, scale)
                typeset_units.append(relocated_unit)

                if not unit.is_space:
                    current_line_heights.append(unit_height)

                prev_x = current_x
                current_x = relocated_unit.box.x2
                if prev_x > current_x:
                    logger.warning(f"Position regression occurred, TypesettingUnit: {unit.box}, ")

                last_unit = relocated_unit
        
        # NOTE: Arabic RTL text flows RIGHT-to-LEFT horizontally but still TOP-to-BOTTOM vertically
        # The right-alignment is already handled in the Arabic layout logic above (lines 1909-1963)
        # DO NOT reverse Y positions - this was causing text overlap issues
        
        return typeset_units, all_units_fit

    def _apply_arabic_word_spacing(
        self,
        line_units_map: dict[int, list[TypesettingUnit]],
        spacing: float,
        box: Box = None,
    ):
        """Apply additional spacing between Arabic words post layout.
        
        If box is provided, ensures the total line width doesn't exceed box width.
        """
        if spacing <= 0:
            return

        for line_idx, units in line_units_map.items():
            if not units:
                continue
            
            # Count word gaps
            word_gap_count = sum(1 for unit in units if getattr(unit, "ends_word", False))
            if word_gap_count == 0:
                continue
            
            # Calculate total spacing that would be added
            total_spacing = word_gap_count * spacing
            
            # If box is provided, check if spacing would cause overflow
            actual_spacing = spacing
            if box is not None:
                box_width = box.x2 - box.x
                # Get current line width
                line_min_x = min(u.box.x for u in units if u.box and u.box.x is not None)
                line_max_x = max(u.box.x2 for u in units if u.box and u.box.x2 is not None)
                current_width = line_max_x - line_min_x
                
                # Calculate available space for word spacing
                available_space = box_width - current_width
                
                if available_space <= 0:
                    # Line already at or beyond box width, skip spacing
                    continue
                
                # If total spacing would exceed available space, reduce it
                if total_spacing > available_space:
                    actual_spacing = available_space / word_gap_count
            
            # Apply the (possibly reduced) spacing
            cumulative_shift = 0.0
            for unit in units:
                if cumulative_shift:
                    self._shift_unit_x(unit, cumulative_shift)
                if getattr(unit, "ends_word", False):
                    cumulative_shift += actual_spacing

    def _shift_unit_x(self, unit: TypesettingUnit, shift_x: float):
        """Shift a typesetting unit horizontally."""
        if shift_x == 0 or unit is None:
            return
        if unit.box:
            unit.box.x += shift_x
            unit.box.x2 += shift_x
        if unit.x is not None:
            unit.x += shift_x
        if unit.char and unit.char.box:
            unit.char.box.x += shift_x
            unit.char.box.x2 += shift_x
        if (
            unit.char
            and unit.char.visual_bbox
            and unit.char.visual_bbox.box
        ):
            unit.char.visual_bbox.box.x += shift_x
            unit.char.visual_bbox.box.x2 += shift_x

    def _justify_arabic_line(
        self, 
        line_units: list[TypesettingUnit], 
        target_left: float, 
        target_right: float,
        current_left: float,
        current_right: float,
    ):
        """Justify an Arabic line by distributing space between words.
        
        For RTL text, we want the line to:
        - Start from the right margin (target_right)
        - Extend to the left margin (target_left)
        - Space between words is distributed evenly to fill the gap
        
        IMPORTANT: The line must stay within bounds - left edge >= target_left
        
        Args:
            line_units: List of typesetting units in the line
            target_left: Target left edge (box.x)
            target_right: Target right edge (box.x2)
            current_left: Current line left edge (min x)
            current_right: Current line right edge (max x)
        """
        if not line_units:
            return
        
        target_width = target_right - target_left
        current_width = current_right - current_left
        
        if current_width <= 0 or target_width <= current_width:
            # Line already fills the space or is wider, just right-align
            # But clamp to prevent left-side overflow!
            shift_x = target_right - current_right
            
            # Check if shifting would push left edge beyond target_left
            new_left = current_left + shift_x
            if new_left < target_left:
                # Clamp shift to keep left edge at target_left
                shift_x = target_left - current_left
            
            for unit in line_units:
                self._shift_unit_x(unit, shift_x)
            return
        
        # Calculate the extra space to distribute
        extra_space = target_width - current_width
        
        # Find word boundaries by looking for units marked as ending a word
        # or by detecting spaces between character groups
        word_gaps = []
        sorted_units = sorted(line_units, key=lambda u: u.box.x if u.box else 0)
        
        # Count word boundaries (gaps between words)
        for i, unit in enumerate(sorted_units):
            if getattr(unit, 'ends_word', False) and i < len(sorted_units) - 1:
                word_gaps.append(i)
        
        # Alternative: detect gaps by looking at spaces between consecutive units
        if not word_gaps:
            for i in range(len(sorted_units) - 1):
                unit = sorted_units[i]
                next_unit = sorted_units[i + 1]
                if unit.box and next_unit.box:
                    gap = next_unit.box.x - unit.box.x2
                    # If gap is significant (larger than typical character spacing), it's a word gap
                    if gap > 1:  # Threshold for word gap detection
                        word_gaps.append(i)
        
        num_gaps = len(word_gaps)
        
        if num_gaps == 0:
            # No word gaps found, just right-align
            shift_x = target_right - current_right
            for unit in line_units:
                self._shift_unit_x(unit, shift_x)
            return
        
        # Distribute extra space evenly between word gaps
        space_per_gap = extra_space / num_gaps
        
        # First, right-align the line to target_right
        initial_shift = target_right - current_right
        for unit in line_units:
            self._shift_unit_x(unit, initial_shift)
        
        # Now expand gaps by shifting units to the left based on which gap they're after
        # Units after gap i get shifted left by (num_gaps - gap_index) * space_per_gap
        # This creates justified spacing from right to left
        gap_index = 0
        cumulative_shift = 0.0
        
        # Re-sort after initial shift
        sorted_units = sorted(line_units, key=lambda u: u.box.x if u.box else 0)
        
        for i, unit in enumerate(sorted_units):
            # Apply cumulative shift (negative, to move left)
            if cumulative_shift != 0:
                self._shift_unit_x(unit, -cumulative_shift)
            
            # Check if this unit ends a word (next position should expand)
            if i in word_gaps:
                cumulative_shift += space_per_gap

    def _get_width_before_next_break_point(
        self, units: list[TypesettingUnit], scale: float
    ) -> float:
        """Calculate the width before the next line break point"""
        width = 0.0
        for unit in units:
            if unit.can_break_line:
                break
            width += unit.width * scale
        return width

    def create_typesetting_units(
        self,
        paragraph: il_version_1.PdfParagraph,
        fonts: dict[str, il_version_1.PdfFont],
        *,
        page: il_version_1.Page | None = None,
    ) -> list[TypesettingUnit]:
        if not paragraph.pdf_paragraph_composition:
            return []
        result = []
        centered_display = page is not None and self._is_centered_display_text(paragraph, page)
        # Page-level paragraphs are not inside an XObject.  The rest of the
        # pipeline represents that case with -1 (rather than None), while a
        # TypesettingUnit requires a concrete ownership ID for generated text.
        paragraph_xobj_id = (
            paragraph.xobj_id
            if getattr(paragraph, "xobj_id", None) is not None
            else -1
        )
        def resolve_unicode(source_font, value: str):
            """Resolve a target character without ever accepting glyph 0.

            Arabic reshaping can generate contextual presentation forms absent
            from a target cmap. Only in that case, use its NFKC base letter;
            supported presentation forms are retained for correct joining.
            """
            if len(value) != 1:
                raise ValueError(f"Expected a single output character, got {value!r}")
            codepoint = ord(value)
            # Variation selectors change the presentation of the preceding
            # character and do not render as standalone glyphs.  Requiring a
            # target-font cmap entry for them makes otherwise valid translated
            # text fail during export (for example, U+FE0F after a symbol).
            if (
                0xFE00 <= codepoint <= 0xFE0F
                or 0xE0100 <= codepoint <= 0xE01EF
            ):
                return None, None
            if unicodedata.category(value) == "Cc":
                return None, None
            is_arabic_target = len(value) == 1 and (
                0x0600 <= ord(value) <= 0x06FF
                or 0x0750 <= ord(value) <= 0x077F
                or 0x08A0 <= ord(value) <= 0x08FF
                or 0xFB50 <= ord(value) <= 0xFDFF
                or 0xFE70 <= ord(value) <= 0xFEFF
            )
            # A translated paragraph can inherit Source Han's style ID from
            # the English PDF; that face must never become the output owner of
            # Arabic glyphs. The source style is still a weight hint: a bold
            # English heading must use GoNotoKurrent-Bold in Arabic.
            if self.is_arabic and is_arabic_target:
                weighted_target = self.font_mapper.map(source_font, value)
                if weighted_target is not None:
                    return value, weighted_target
                for arabic_font in self.font_mapper.arabic_fonts:
                    if arabic_font.has_glyph(ord(value)):
                        return value, arabic_font
            target_font = self.font_mapper.map(source_font, value)
            if target_font is not None:
                return value, target_font
            normalized = unicodedata.normalize("NFKC", value)
            if len(normalized) == 1 and normalized != value:
                target_font = self.font_mapper.map(source_font, normalized)
                if target_font is not None:
                    return normalized, target_font
            fallback_value = self._SYMBOL_GLYPH_FALLBACKS.get(value)
            if fallback_value:
                target_font = self.font_mapper.map(source_font, fallback_value)
                if target_font is not None:
                    return fallback_value, target_font
            message = (
                f"No target-font coverage for {value!r} in paragraph "
                f"{getattr(paragraph, 'debug_id', None) or '<unknown>'}"
            )
            logger.error(message)
            raise RuntimeError(message)

        @cache
        def get_font(font_id: str, xobj_id: int | None):
            if xobj_id in fonts:
                font = fonts[xobj_id][font_id]
            else:
                font = fonts[font_id]
            return font

        def unit_from_source_char(char: il_version_1.PdfCharacter) -> TypesettingUnit:
            """Retype portable bullets and Arabic glyphs in the target font."""
            value = char.char_unicode or ""
            is_pua_bullet = len(value) == 1 and 0xE000 <= ord(value) <= 0xF8FF
            is_arabic_glyph = len(value) == 1 and (
                0x0600 <= ord(value) <= 0x06FF
                or 0x0750 <= ord(value) <= 0x077F
                or 0x08A0 <= ord(value) <= 0x08FF
                or 0xFB50 <= ord(value) <= 0xFDFF
                or 0xFE70 <= ord(value) <= 0xFEFF
            )
            if (is_pua_bullet or (self.is_arabic and is_arabic_glyph)) and char.pdf_style:
                style = char.pdf_style
                char_xobj = (
                    char.xobj_id
                    if getattr(char, "xobj_id", None) is not None
                    else (
                        paragraph_xobj_id
                    )
                )
                source_font = get_font(style.font_id, char_xobj)
                target_value, target_font = resolve_unicode(
                    source_font, "•" if is_pua_bullet else value
                )
                if target_value is None:
                    return TypesettingUnit(char=char)
                return TypesettingUnit(
                    unicode=target_value,
                    font=target_font,
                    arabic_fallback_font=(self.font_mapper.arabic_fonts[0] if self.is_arabic else None),
                    original_font=source_font,
                    font_size=style.font_size,
                    style=style,
                    xobj_id=char_xobj,
                    debug_info=getattr(paragraph, "debug_info", False) or False,
                )
            return TypesettingUnit(
                char=char,
                debug_info=getattr(paragraph, "debug_info", False) or False,
            )

        for composition in paragraph.pdf_paragraph_composition:
            if composition is None:
                continue
            if composition.pdf_line:
                result.extend(
                    [
                        unit_from_source_char(char)
                        for char in composition.pdf_line.pdf_character
                    ],
                )
            elif composition.pdf_character:
                result.append(unit_from_source_char(composition.pdf_character))
            elif composition.pdf_same_style_characters:
                result.extend(
                    [
                        unit_from_source_char(char)
                        for char in composition.pdf_same_style_characters.pdf_character
                    ],
                )
            elif composition.pdf_same_style_unicode_characters:
                style = composition.pdf_same_style_unicode_characters.pdf_style
                if style is None:
                    logger.warning(
                        f"Style is None. "
                        f"Composition: {composition}. "
                        f"Paragraph: {paragraph}. ",
                    )
                    continue
                font_id = style.font_id
                if font_id is None:
                    logger.warning(
                        f"Font ID is None. "
                        f"Composition: {composition}. "
                        f"Paragraph: {paragraph}. ",
                    )
                    continue
                font = get_font(font_id, paragraph_xobj_id)
                if composition.pdf_same_style_unicode_characters.unicode:
                    unicode_text = composition.pdf_same_style_unicode_characters.unicode
                    preflight_font = (
                        (self.font_mapper.map(font, "\u0627") or self.font_mapper.arabic_fonts[0])
                        if self.is_arabic
                        else font
                    )
                    self._preflight_arabic_run(unicode_text, preflight_font)
                    shaped_text = self.shape_arabic_text(unicode_text)
                    optical_factor = 1.08 if self.is_arabic else 1.0
                    target_font_size = self._arabic_publication_font_size(
                        paragraph,
                        style.font_size * optical_factor,
                        centered_display=centered_display,
                    )
                    for char_unicode in shaped_text:
                        if char_unicode in "\r\n":
                            if char_unicode == "\n":
                                result.append(TypesettingUnit(forced_line_break=True))
                            continue
                        target_value, target_font = resolve_unicode(font, char_unicode)
                        if target_value is None:
                            continue
                        result.append(
                            TypesettingUnit(
                                unicode=target_value,
                                font=target_font,
                                arabic_fallback_font=(self.font_mapper.arabic_fonts[0] if self.is_arabic else None),
                                original_font=font,
                                # Contact rectangles in older PDFs commonly
                                # inherit a tiny label font.  A translated card
                                # is a field block and must remain readable.
                                font_size=max(
                                    target_font_size,
                                    11.5 if "contact_card" in (paragraph.layout_label or "").lower() else 0.0,
                                ),
                                style=style,
                                xobj_id=paragraph_xobj_id,
                                debug_info=composition.pdf_same_style_unicode_characters.debug_info or False,
                            )
                        )
            elif composition.pdf_formula:
                formula_chars = composition.pdf_formula.pdf_character or []
                has_pua_bullet = any(
                    char.char_unicode
                    and len(char.char_unicode) == 1
                    and 0xE000 <= ord(char.char_unicode) <= 0xF8FF
                    for char in formula_chars
                )
                has_arabic_glyph = any(
                    char.char_unicode
                    and len(char.char_unicode) == 1
                    and (
                        0x0600 <= ord(char.char_unicode) <= 0x06FF
                        or 0x0750 <= ord(char.char_unicode) <= 0x077F
                        or 0x08A0 <= ord(char.char_unicode) <= 0x08FF
                        or 0xFB50 <= ord(char.char_unicode) <= 0xFDFF
                        or 0xFE70 <= ord(char.char_unicode) <= 0xFEFF
                    )
                    for char in formula_chars
                )
                formula_has_graphics = bool(
                    composition.pdf_formula.pdf_curve or composition.pdf_formula.pdf_form
                )
                if has_pua_bullet and all(
                    not char.char_unicode
                    or char.char_unicode.isspace()
                    or (
                        len(char.char_unicode) == 1
                        and 0xE000 <= ord(char.char_unicode) <= 0xF8FF
                    )
                    for char in formula_chars
                ):
                    result.extend(unit_from_source_char(char) for char in formula_chars)
                elif has_arabic_glyph and not formula_has_graphics:
                    # Text-only formulas are layout-parser false positives.
                    # Retype them so shaped Arabic never falls back to Source Han.
                    result.extend(unit_from_source_char(char) for char in formula_chars)
                else:
                    # A genuine formula may also contain Arabic labels and
                    # presentation forms alongside its preserved graphics.
                    # Keep its geometry, but remap those characters to the
                    # embedded Arabic font instead of rendering .notdef from
                    # the source serif font.
                    if has_arabic_glyph:
                        for char in formula_chars:
                            value = char.char_unicode or ""
                            if not (
                                len(value) == 1
                                and (
                                    0x0600 <= ord(value) <= 0x06FF
                                    or 0x0750 <= ord(value) <= 0x077F
                                    or 0x08A0 <= ord(value) <= 0x08FF
                                    or 0xFB50 <= ord(value) <= 0xFDFF
                                    or 0xFE70 <= ord(value) <= 0xFEFF
                                )
                                and char.pdf_style
                            ):
                                continue
                            char_xobj = (
                                char.xobj_id
                                if char.xobj_id is not None
                                else paragraph_xobj_id
                            )
                            source_font = get_font(char.pdf_style.font_id, char_xobj)
                            target_value, target_font = resolve_unicode(source_font, value)
                            if target_value is None:
                                continue
                            style = copy.copy(char.pdf_style)
                            style.font_id = target_font.font_id
                            char.pdf_style = style
                            char.char_unicode = target_value
                            char.pdf_character_id = target_font.has_glyph(ord(target_value))
                    result.extend([TypesettingUnit(formular=composition.pdf_formula)])
            else:
                logger.error(
                    f"Unknown composition type. "
                    f"Composition: {composition}. "
                    f"Paragraph: {paragraph}. ",
                )
                continue
        unresolved = [unit for unit in result if unit.unicode is not None and unit.font is None]
        if unresolved:
            message = (
                f"Unresolved target glyphs in paragraph {getattr(paragraph, 'debug_id', None) or '<unknown>'}: "
                + ", ".join(repr(unit.unicode) for unit in unresolved[:8])
            )
            logger.error(message)
            raise RuntimeError(message)

        if any(x.width < 0 for x in result):
            logger.warning("Typesetting unit width is less than 0, please check if positioning is incorrect or if text is being drawn in reverse")
        return result

    def create_passthrough_composition(
        self,
        typesetting_units: list[TypesettingUnit],
    ) -> list[PdfParagraphComposition]:
        """Create passthrough composition from typesetting units - used when all units can be directly passed through

        Args:
            typesetting_units: List of typesetting units

        Returns:
            Paragraph composition list
        """
        composition = []
        for unit in typesetting_units:
            if unit.formular:
                # For formula units, directly create PdfParagraphComposition containing the formula object
                composition.append(PdfParagraphComposition(pdf_formula=unit.formular))
            else:
                # For character units, use existing logic to passthrough
                chars, curves, forms = unit.passthrough()
                composition.extend(
                    [PdfParagraphComposition(pdf_character=char) for char in chars],
                )
        return composition

    def get_max_right_space(self, current_box: Box, page) -> float:
        """Get the maximum right space available next to the current paragraph

        Args:
            current_box: Current paragraph bounding box
            page: Current page

        Returns:
            Maximum available right edge x position
        """
        # Get page's right margin as the upper limit
        max_x = page.cropbox.box.x2 * 0.9

        # Check for content on the right side that may interfere
        for para in page.pdf_paragraph:
            if para.box == current_box or para.box is None:  # Skip current paragraph
                continue
            # If the paragraph is on the right side of current paragraph and their vertical ranges overlap
            if para.box.x > current_box.x and not (
                para.box.y >= current_box.y2 or para.box.y2 <= current_box.y
            ):
                max_x = min(max_x, para.box.x)
        for char in page.pdf_character:
            if char.box.x > current_box.x and not (
                char.box.y >= current_box.y2 or char.box.y2 <= current_box.y
            ):
                max_x = min(max_x, char.box.x)
        # Check figures
        for figure in page.pdf_figure:
            if figure.box.x > current_box.x and not (
                figure.box.y >= current_box.y2 or figure.box.y2 <= current_box.y
            ):
                max_x = min(max_x, figure.box.x)

        return max_x

    def get_max_bottom_space(self, current_box: Box, page: il_version_1.Page) -> float:
        """Get the maximum bottom space available below the current paragraph

        Args:
            current_box: Current paragraph bounding box
            page: Current page

        Returns:
            Maximum available bottom edge y position
        """
        # Get page's bottom margin as the lower limit
        min_y = page.cropbox.box.y * 1.1

        # Check for content below that may interfere
        for para in page.pdf_paragraph:
            if para.box == current_box or para.box is None:  # Skip current paragraph
                continue
            # If the paragraph is below current paragraph and their horizontal ranges overlap
            if para.box.y2 < current_box.y and not (
                para.box.x >= current_box.x2 or para.box.x2 <= current_box.x
            ):
                min_y = max(min_y, para.box.y2)
        for char in page.pdf_character:
            if char.box.y2 < current_box.y and not (
                char.box.x >= current_box.x2 or char.box.x2 <= current_box.x
            ):
                min_y = max(min_y, char.box.y2)
        # Check figures
        for figure in page.pdf_figure:
            if figure.box.y2 < current_box.y and not (
                figure.box.x >= current_box.x2 or figure.box.x2 <= current_box.x
            ):
                min_y = max(min_y, figure.box.y2)

        return min_y

    def _update_paragraph_render_order(self, paragraph: il_version_1.PdfParagraph):
        """
        Update paragraph render order for child characters.
        From render order = paragraph's render order, sub render order starts from 1
        """
        if not hasattr(paragraph, "render_order") or paragraph.render_order is None:
            return

        main_render_order = paragraph.render_order
        sub_render_order = 1

        # Iterate through paragraph composition list
        for composition in paragraph.pdf_paragraph_composition:
            # Check for character and assign render order
            if composition.pdf_character:
                char = composition.pdf_character
                char.render_order = main_render_order
                char.sub_render_order = sub_render_order
                sub_render_order += 1
