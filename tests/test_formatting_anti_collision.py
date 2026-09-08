# -*- coding: utf-8 -*-
"""
Tests for Robust Formatting & Anti-Collision Enhancements:
- Style-Boundary Heading vs Body Text Separation (ParagraphFinder)
- Vertical Paragraph Clearance & Collision Avoidance (Typesetting)
- Acronym & Placeholder Token De-duplication (Translator)
- Top Header Decorative Banner Vector Mirroring (Typesetting)
"""

import sys
import unittest
from pathlib import Path

from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.format.pdf.document_il.il_version_1 import (
    Box,
    Cropbox,
    Page,
    PdfParagraph,
    PdfParagraphComposition,
    PdfLine,
    PdfStyle,
    PdfCharacter,
    PdfFont,
    PdfRectangle,
    GraphicState,
    VisualBbox,
    Document,
)
from babeldoc.format.pdf.document_il.midend.paragraph_finder import (
    ParagraphFinder,
    is_line_bold,
    get_line_dominant_font_size,
    is_para_bold,
)
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting, TypesettingUnit
from babeldoc.translator.translator import _protect_numerals, _restore_numerals


class DummyTranslator:
    def __init__(self, lang_in="en", lang_out="ar", ignore_cache=True):
        self.lang_in = lang_in
        self.lang_out = lang_out
        self.ignore_cache = ignore_cache

    def translate(self, text, rate_limit_params=None):
        return text


class TestFormattingAntiCollision(unittest.TestCase):
    def setUp(self):
        self.cfg_ar = TranslationConfig(
            translator=DummyTranslator("en", "ar"),
            input_file="dummy.pdf",
            lang_in="en",
            lang_out="ar",
            doc_layout_model=None,
        )

    def test_heading_body_style_boundary_split(self):
        """
        Verify that a bold heading followed by regular body text in the same block
        is cleanly split into two distinct paragraphs with proper layout_label.
        """
        pf = ParagraphFinder(self.cfg_ar)

        font_bold = PdfFont(font_id="f_bold", name="Helvetica-Bold", bold=True)
        font_regular = PdfFont(font_id="f_reg", name="Helvetica", bold=False)
        font_dict = {"f_bold": font_bold, "f_reg": font_regular}

        style_bold = PdfStyle(font_id="f_bold", font_size=10.0)
        style_reg = PdfStyle(font_id="f_reg", font_size=9.5)

        # Line 0: Bold Heading "Straight through Processing (STP)"
        heading_text = "Straight through Processing (STP)"
        chars_0 = [
            PdfCharacter(
                char_unicode=c,
                pdf_style=style_bold,
                box=Box(50, 400, 58, 412),
                visual_bbox=VisualBbox(box=Box(50, 400, 58, 412)),
            )
            for c in heading_text
        ]
        line_0 = PdfLine(box=Box(50, 400, 280, 412), pdf_character=chars_0)

        # Line 1: Regular Body "The procedure for transferring funds electronically"
        body_text = "The procedure for transferring funds electronically"
        chars_1 = [
            PdfCharacter(
                char_unicode=c,
                pdf_style=style_reg,
                box=Box(50, 385, 56, 395),
                visual_bbox=VisualBbox(box=Box(50, 385, 56, 395)),
            )
            for c in body_text
        ]
        line_1 = PdfLine(box=Box(50, 385, 450, 395), pdf_character=chars_1)

        # Paragraph containing both lines
        para = PdfParagraph(
            box=Box(50, 385, 450, 412),
            pdf_paragraph_composition=[
                PdfParagraphComposition(pdf_line=line_0),
                PdfParagraphComposition(pdf_line=line_1),
            ],
            unicode="",
            layout_label="unknown",
        )
        pf.update_paragraph_data(para, update_unicode=True)

        paragraphs = [para]
        # Run process_independent_paragraphs with font_dict
        pf.process_independent_paragraphs(paragraphs, median_width=300.0, font_dict=font_dict)

        # Assert split occurred
        self.assertEqual(len(paragraphs), 2, "Bold heading and regular body text must be split into 2 paragraphs")
        self.assertEqual(paragraphs[0].layout_label, "heading", "Heading paragraph must receive 'heading' label")
        self.assertEqual(paragraphs[1].layout_label, "paragraph", "Body paragraph must receive 'paragraph' label")
        self.assertIn("Straight through Processing", paragraphs[0].unicode)
        self.assertIn("The procedure for transferring", paragraphs[1].unicode)

        # Verify that continuation merge respects the style guard and does NOT merge them back
        pf.merge_hyphenated_and_continuation_paragraphs(paragraphs, font_dict=font_dict)
        self.assertEqual(len(paragraphs), 2, "Style guard must prevent re-merging heading and body paragraphs")

    def test_vertical_anti_collision_clearance(self):
        """
        Verify that downward expansion in Typesetting enforces the safety margin floor
        so that an expanding paragraph never collides with a lower paragraph.
        """
        ts = Typesetting(self.cfg_ar)
        page = Page(cropbox=Cropbox(box=Box(0.0, 0.0, 600.0, 800.0)))

        # Lower paragraph at y=650.0 to y2=670.0
        lower_para = PdfParagraph(
            box=Box(50.0, 650.0, 500.0, 670.0),
            unicode="Lower Paragraph Content",
            pdf_paragraph_composition=[],
        )
        page.pdf_paragraph = [lower_para]

        # Upper paragraph at y=680.0 to y2=700.0 (gap of 10pt between 680 and 670)
        upper_box = Box(50.0, 680.0, 500.0, 700.0)
        upper_para = PdfParagraph(
            box=upper_box,
            unicode="Upper Paragraph",
            pdf_paragraph_composition=[],
        )

        style = PdfStyle(font_id="base", font_size=10.0)
        # Create typesetting units representing a paragraph that needs multiple lines
        text = "This is a longer translated Arabic text that requires downward box expansion to fit cleanly."
        units = []
        for ch in text:
            w = 4.0 if ch == " " else 10.0
            c = PdfCharacter(
                char_unicode=ch,
                box=Box(0.0, 0.0, w, 12.0),
                pdf_style=style,
            )
            units.append(TypesettingUnit(char=c))

        # Check expansion
        scale, final_units = ts._find_optimal_scale_and_layout(
            upper_para, page, units, initial_scale=1.0, apply_layout=True
        )

        # Assert upper paragraph bottom y is at least 8pt above lower_para.box.y2
        clearance = upper_para.box.y - lower_para.box.y2
        self.assertGreaterEqual(
            clearance,
            8.0,
            f"Upper paragraph bottom y ({upper_para.box.y}) is too close to lower paragraph top y2 ({lower_para.box.y2}), clearance={clearance}"
        )

    def test_acronym_token_deduplication(self):
        """
        Verify that repeated acronym placeholders ({0})({0}) or (BBAN)(BBAN)
        are cleanly de-duplicated by _restore_numerals without corrupting text.
        """
        # 1. Test placeholder de-duplication
        raw_text_1 = "رقم الحساب المصرفي الأساسي ({0})({0})"
        mapping_1 = {"{0}": "BBAN"}
        restored_1 = _restore_numerals(raw_text_1, mapping_1)
        self.assertNotIn("(BBAN)(BBAN)", restored_1, f"Expected deduplicated (BBAN), got: {restored_1}")
        self.assertIn("(BBAN)", restored_1)

        # 2. Test consecutive duplicate parenthesized acronyms post-restoration
        raw_text_2 = "رقم الحساب (BBAN) (BBAN) للمؤسسات"
        restored_2 = _restore_numerals(raw_text_2, {})
        self.assertNotIn("(BBAN) (BBAN)", restored_2)
        self.assertIn("(BBAN)", restored_2)

    def test_header_vector_banner_mirroring(self):
        """
        Verify that decorative header vector rectangles (e.g. colored badge on top right)
        mirror horizontally to the left side in Arabic RTL mode.
        """
        ts = Typesetting(self.cfg_ar)
        page = Page(cropbox=Cropbox(box=Box(0.0, 0.0, 600.0, 800.0)))

        # Decorative rectangle near top header on the right: x=400, x2=500, y=720, y2=735
        header_rect = PdfRectangle(
            box=Box(400.0, 720.0, 500.0, 735.0),
            graphic_state=GraphicState(),
        )
        page.pdf_rectangle = [header_rect]

        ts._mirror_header_decorations(page)

        # In RTL mirroring across width 600:
        # new_x = 600 - 500 = 100.0
        # new_x2 = 600 - 400 = 200.0
        self.assertAlmostEqual(header_rect.box.x, 100.0, places=1, msg="Header banner left edge should mirror to 100.0")
        self.assertAlmostEqual(header_rect.box.x2, 200.0, places=1, msg="Header banner right edge should mirror to 200.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
