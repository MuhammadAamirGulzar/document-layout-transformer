from __future__ import annotations

import copy
import logging
import re
import statistics
import unicodedata
from functools import cache

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
from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper
from babeldoc.format.pdf.document_il.utils.formular_helper import update_formula_data
from babeldoc.format.pdf.document_il.utils.layout_helper import box_to_tuple
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
        original_font: il_version_1.PdfFont | None = None,
        font_size: float | None = None,
        style: PdfStyle | None = None,
        xobj_id: int | None = None,
        debug_info: bool = False,
    ):
        assert (char is not None) + (formular is not None) + (
            unicode is not None
        ) == 1, "Only one of chars and formular can be not None"
        self.char = char
        self.formular = formular
        self.unicode = unicode
        self.x = None
        self.y = None
        self.scale = None
        self.debug_info = debug_info

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
            box = copy.deepcopy(self.char.box)
            if self.char.visual_bbox and self.char.visual_bbox.box:
                box.y = self.char.visual_bbox.box.y
                box.y2 = self.char.visual_bbox.box.y2

            return box
        elif self.formular:
            return self.formular.box
        elif self.unicode:
            char_width = self.font.char_lengths(self.unicode, self.font_size)[0]
            if self.x is None or self.y is None or self.scale is None:
                return Box(0, 0, char_width, self.font_size)
            return Box(self.x, self.y, self.x + char_width, self.y + self.font_size)

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

            # Handle case when font is None (no suitable font found for this character)
            if self.font is None:
                logger.warning(
                    f"No font available for character '{self.unicode}' (U+{ord(self.unicode):04X}), "
                    f"using font_id='{self.font_id}' with glyph_id=0"
                )
                glyph_id = 0  # Use glyph 0 as fallback (usually .notdef)
            else:
                glyph_id = self.font.has_glyph(ord(self.unicode))
                if glyph_id == 0 or glyph_id is None:
                    logger.warning(
                        f"Font '{self.font_id}' doesn't have glyph for character '{self.unicode}' (U+{ord(self.unicode):04X}), "
                        f"using glyph_id=0"
                    )
                    glyph_id = 0

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

    def preprocess_document(self, document: il_version_1.Document, pbar):
        """Preprocess document - calculate optimal scale for each paragraph and cache the result"""
        all_scales: list[float] = []
        all_paragraphs: list[il_version_1.PdfParagraph] = []

        for page in document.page:
            pbar.advance()
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

            # Preprocess each paragraph
            for paragraph in page.pdf_paragraph:
                all_paragraphs.append(paragraph)
                unit_count = 0
                try:
                    typesetting_units = self.create_typesetting_units(paragraph, fonts)
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
                if not is_hierarchy_exempt:
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
            tuple[float, list[TypesettingUnit] | None]: (optimal scale factor, laid out typesetting units or None)
        """
        if not paragraph.box:
            return initial_scale, None

        box = paragraph.box
        scale = initial_scale
        # Arabic needs extra line spacing (1.5) due to diacritical marks above/below letters
        # CJK also needs 1.5 due to character height
        # Other languages use 1.3
        if self.is_cjk:
            line_skip = 1.50
        elif self.is_arabic:
            line_skip = 1.50  # Arabic needs extra space for diacritical marks (harakat)
        else:
            line_skip = 1.3

        # Role-based minimum scale floors to preserve typographic hierarchy
        layout_label = (paragraph.layout_label or "").lower()
        if "title" in layout_label or "heading" in layout_label:
            min_scale = 0.88  # Headings never shrink below 88% of original size
        elif "caption" in layout_label:
            min_scale = 0.82
        elif "table_footnote" in layout_label or "footnote" in layout_label:
            min_scale = 0.75
        elif "table" in layout_label:
            min_scale = 0.65
        else:
            min_scale = 0.72  # Body text floor

        expand_space_flag = 0
        final_typeset_units = None

        while scale >= min_scale:
            try:
                # Try to layout typesetting units
                typeset_units, all_units_fit = self._layout_typesetting_units(
                    typesetting_units,
                    box,
                    scale,
                    line_skip,
                    paragraph,
                    use_english_line_break,
                )

                # If all typesetting units fit within the box
                if all_units_fit:
                    if apply_layout:
                        # Apply layout and write to paragraph
                        paragraph.scale = scale
                        paragraph.pdf_paragraph_composition = []
                        for unit in typeset_units:
                            chars, curves, forms = unit.render()
                            for char in chars:
                                paragraph.pdf_paragraph_composition.append(
                                    PdfParagraphComposition(pdf_character=char),
                                )
                            for curve in curves:
                                page.pdf_curve.append(curve)
                            for form in forms:
                                page.pdf_form.append(form)
                        final_typeset_units = typeset_units
                    return scale, final_typeset_units
            except Exception:
                # If layout fails, check for overflow and try a smaller scale
                pass

            # Add retypeset logic if needed
            if not hasattr(paragraph, "debug_id") or not paragraph.debug_id:
                return scale, final_typeset_units

            # Reduce scale factor
            if scale > 0.6:
                scale -= 0.05
            else:
                scale -= 0.1

            if scale < 0.7:
                space_expanded = False  # Track whether space has been added

                if expand_space_flag == 0:
                    # Try expanding bottom space
                    try:
                        min_y = self.get_max_bottom_space(box, page) + 2
                        if min_y < box.y:
                            expanded_box = Box(x=box.x, y=min_y, x2=box.x2, y2=box.y2)
                            box = expanded_box
                            if apply_layout:
                                # Update paragraph box boundary
                                paragraph.box = expanded_box
                            space_expanded = True
                    except Exception:
                        pass
                    expand_space_flag = 1

                    # If space expansion is possible, continue to try new scale
                    if space_expanded:
                        continue

                elif expand_space_flag == 1:
                    # Try expanding right space
                    try:
                        max_x = self.get_max_right_space(box, page) - 5
                        if max_x > box.x2:
                            expanded_box = Box(x=box.x, y=box.y, x2=max_x, y2=box.y2)
                            box = expanded_box
                            if apply_layout:
                                # Update paragraph box boundary
                                paragraph.box = expanded_box
                            space_expanded = True
                    except Exception:
                        pass
                    expand_space_flag = 2

                    # If space expansion is possible, continue to try new scale
                    if space_expanded:
                        continue

                # If no space can be expanded (expand_space_flag < 2), reset scale
                # When expand_space_flag >= 2, the space has been exhausted and normal scale selection continues
                if expand_space_flag < 2:
                    # Reset if there was no space expansion, retry scale loop from 1.0
                    scale = 1.0

        # If English line break fails, try fallback with no line break
        if use_english_line_break:
            return self._find_optimal_scale_and_layout(
                paragraph,
                page,
                typesetting_units,
                initial_scale,
                use_english_line_break=False,
                apply_layout=apply_layout,
            )

        # Return the smallest scale factor
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
                    if not (
                        p_lower.box
                        and p_upper.box
                        and p_lower.box.x2 < p_upper.box.x
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
        # Start typesetting layout rendering
        for paragraph in page.pdf_paragraph:
            self.render_paragraph(paragraph, page, fonts)

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
        
        # Symmetrical page-level mirroring for running headers, footers, and page numbers
        if self._is_header_or_footer(paragraph, page):
            orig_right_margin = page_box.x2 - box.x2
            new_x = page_box.x + orig_right_margin
            return new_x - box.x

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
                    if curve.box:
                        curve.box.x += effective_shift
                        curve.box.x2 += effective_shift
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

    def _is_inside_table_or_figure_region(self, para_box: Box, page: il_version_1.Page) -> bool:
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
        
        for layout in page.page_layout:
            if not layout.box:
                continue
                
            # Check if this is a table or figure layout
            layout_name = (layout.class_name or "").lower()
            
            # Table and figure structure layouts
            is_container_layout = layout_name in [
                'table', 'wired_table', 'wireless_table',
                'figure', 'chart', 'graph', 'diagram', 'image'
            ]
            
            if not is_container_layout:
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
        typesetting_units = self.create_typesetting_units(paragraph, fonts)
        
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
                u_str = u.try_get_unicode() or ""
                if u.is_space:
                    tokens.append([u])
                    idx += 1
                elif BULLET_POINT_PATTERN.match(u_str) or (u_str and 0xE000 <= ord(u_str[0]) <= 0xF8FF):
                    # Standalone bullet token: never glue bullet to following word
                    tokens.append([u])
                    idx += 1
                else:
                    word = [u]
                    idx += 1
                    while idx < n:
                        next_u = typesetting_units[idx]
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

            # 3. Layout each line with per-line BiDi
            for line_idx, line in enumerate(lines_tokens):
                if not line:
                    continue

                line_str = "".join(u.try_get_unicode() or "" for u in line)
                
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

                if is_chronological_axis or is_isolated_latin:
                    # Keep native LTR character order matching original code, formula, or timeline
                    visual_units = line
                elif line_str.strip():
                    bidi_map = get_bidi_indices_and_chars(line_str, base_dir='R')
                    visual_units = []
                    for orig_idx, mirrored_char in bidi_map:
                        if orig_idx < len(line):
                            unit = copy.copy(line[orig_idx])
                            if mirrored_char and unit.unicode and unit.unicode != mirrored_char:
                                unit.unicode = mirrored_char
                            visual_units.append(unit)
                else:
                    visual_units = line

                # Calculate total line width
                visual_line_w = sum(u.width * scale for u in visual_units)

                # Hanging indent on wrapped lines (line_idx >= 1) for bullets
                if is_bullet_para and line_idx > 0:
                    current_indent = bullet_indent
                elif line_idx == 0 and paragraph.first_line_indent:
                    current_indent = first_indent
                else:
                    current_indent = 0.0

                # Determine starting X position
                if is_chronological_axis or is_isolated_latin:
                    line_start_x = box.x
                elif is_table_content:
                    line_start_x = box.x2 - visual_line_w
                    if line_start_x < box.x:
                        line_start_x = box.x
                else:
                    line_start_x = (box.x2 - current_indent) - visual_line_w
                    if line_start_x < box.x:
                        line_start_x = box.x

                # Place each unit along the line LTR from line_start_x
                cur_x = line_start_x
                line_heights = []
                for u in visual_units:
                    relocated = u.relocate(cur_x, current_y, scale)
                    relocated.line_id = line_idx
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
    ) -> list[TypesettingUnit]:
        if not paragraph.pdf_paragraph_composition:
            return []
        result = []

        @cache
        def get_font(font_id: str, xobj_id: int | None):
            if xobj_id in fonts:
                font = fonts[xobj_id][font_id]
            else:
                font = fonts[font_id]
            return font

        for composition in paragraph.pdf_paragraph_composition:
            if composition is None:
                continue
            if composition.pdf_line:
                result.extend(
                    [
                        TypesettingUnit(char=char)
                        for char in composition.pdf_line.pdf_character
                    ],
                )
            elif composition.pdf_character:
                result.append(
                    TypesettingUnit(
                        char=composition.pdf_character,
                        debug_info=paragraph.debug_info,
                    ),
                )
            elif composition.pdf_same_style_characters:
                result.extend(
                    [
                        TypesettingUnit(char=char)
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
                font = get_font(font_id, paragraph.xobj_id)
                if composition.pdf_same_style_unicode_characters.unicode:
                    unicode_text = composition.pdf_same_style_unicode_characters.unicode
                    shaped_text = self.shape_arabic_text(unicode_text)
                    optical_factor = 1.08 if self.is_arabic else 1.0
                    result.extend(
                        [
                            TypesettingUnit(
                                unicode=char_unicode,
                                font=self.font_mapper.map(
                                    font,
                                    char_unicode,
                                ),
                                original_font=font,
                                font_size=style.font_size * optical_factor,
                                style=style,
                                xobj_id=paragraph.xobj_id,
                                debug_info=composition.pdf_same_style_unicode_characters.debug_info
                                or False,
                            )
                            for char_unicode in shaped_text  # Use shaped_text instead of original
                            if char_unicode not in ("\n",)
                        ],
                    )
            elif composition.pdf_formula:
                result.extend([TypesettingUnit(formular=composition.pdf_formula)])
            else:
                logger.error(
                    f"Unknown composition type. "
                    f"Composition: {composition}. "
                    f"Paragraph: {paragraph}. ",
                )
                continue
        result = list(
            filter(
                lambda x: x.unicode is None or x.font is not None,
                result,
            ),
        )

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
                char.box.x >= current_box.x2 or char.box.x2 <= current_box.x
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