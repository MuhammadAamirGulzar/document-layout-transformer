# -*- coding: utf-8 -*-
"""
Tests for Robust Formatting & Anti-Collision Enhancements:
- Style-Boundary Heading vs Body Text Separation (ParagraphFinder)
- Vertical Paragraph Clearance & Collision Avoidance (Typesetting)
- Acronym & Placeholder Token De-duplication (Translator)
- Top Header Decorative Banner Vector Mirroring (Typesetting)
"""

import sys
import re
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
    PdfFormula,
    PdfFont,
    PdfSameStyleCharacters,
    PdfRectangle,
    PdfCurve,
    PageLayout,
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
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator, RichTextPlaceholder
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

    def test_stacked_definition_fragments_are_reassembled_geometrically(self):
        """A shuffled glossary definition must become one logical paragraph."""
        pf = ParagraphFinder(self.cfg_ar)
        style = PdfStyle(font_id="f_reg", font_size=10.0)

        def fragment(text, bottom, top):
            chars = [
                PdfCharacter(
                    char_unicode=char,
                    pdf_style=style,
                    box=Box(50, bottom, 56, top),
                    visual_bbox=VisualBbox(box=Box(50, bottom, 56, top)),
                )
                for char in text
            ]
            paragraph = PdfParagraph(
                box=Box(50, bottom, 410, top),
                pdf_paragraph_composition=[
                    PdfParagraphComposition(pdf_line=PdfLine(
                        box=Box(50, bottom, 410, top), pdf_character=chars
                    ))
                ],
                layout_label="paragraph",
            )
            pf.update_paragraph_data(paragraph, update_unicode=True)
            return paragraph

        first = fragment("The BBAN is an identifier", 370, 380)
        second = fragment("used by financial institutions", 355, 365)
        third = fragment("to identify an account.", 340, 350)
        # Extraction order is deliberately not visual reading order.
        paragraphs = [third, first, second]
        pf.coalesce_stacked_text_fragments(paragraphs, font_dict={})

        self.assertEqual(len(paragraphs), 1)
        self.assertEqual(
            paragraphs[0].unicode,
            "The BBAN is an identifier used by financial institutions to identify an account.",
        )

    def test_same_baseline_definition_fragments_are_reassembled(self):
        """A source line split into adjacent runs must become one RTL paragraph."""
        pf = ParagraphFinder(self.cfg_ar)
        style = PdfStyle(font_id="f_reg", font_size=10.0)

        def line(text, x, bottom, char_width=5.0):
            chars = []
            for index, value in enumerate(text):
                box = Box(x + index * char_width, bottom, x + (index + 1) * char_width, bottom + 10)
                chars.append(PdfCharacter(
                    char_unicode=value,
                    pdf_style=style,
                    box=box,
                    visual_bbox=VisualBbox(box=box),
                ))
            return PdfLine(
                box=Box(x, bottom, x + len(text) * char_width, bottom + 10),
                pdf_character=chars,
            )

        left_line = line("The BBAN is the identifier", 50, 400)
        right_first = line("used by financial institutions", left_line.box.x2 + 4, 400)
        right_second = line("which identifies the account.", 50, 385)
        left = PdfParagraph(
            box=left_line.box,
            pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=left_line)],
            layout_label="paragraph",
        )
        right = PdfParagraph(
            box=Box(50, 385, right_first.box.x2, 410),
            pdf_paragraph_composition=[
                PdfParagraphComposition(pdf_line=right_first),
                PdfParagraphComposition(pdf_line=right_second),
            ],
            layout_label="paragraph",
        )
        pf.update_paragraph_data(left, update_unicode=True)
        pf.update_paragraph_data(right, update_unicode=True)
        paragraphs = [right, left]

        pf.coalesce_stacked_text_fragments(paragraphs, font_dict={})

        self.assertEqual(len(paragraphs), 1)
        self.assertEqual(
            paragraphs[0].unicode,
            "The BBAN is the identifier used by financial institutions which identifies the account.",
        )

    def test_same_baseline_style_fragments_form_one_sentence(self):
        """Three differently labelled runs on one line retain word spaces."""
        pf = ParagraphFinder(self.cfg_ar)
        style = PdfStyle(font_id="f_reg", font_size=10.0)

        def fragment(text, x, label):
            chars = []
            for index, value in enumerate(text):
                box = Box(x + index * 5.0, 220.0, x + (index + 1) * 5.0, 232.0)
                chars.append(PdfCharacter(
                    char_unicode=value, pdf_style=style, box=box,
                    visual_bbox=VisualBbox(box=box),
                ))
            line = PdfLine(
                box=Box(x, 220.0, x + len(text) * 5.0, 232.0),
                pdf_character=chars,
            )
            return PdfParagraph(
                box=line.box, unicode=text, layout_label=label,
                pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=line)],
            )

        first = fragment("All", 50.0, "plain text")
        second = fragment("banks are required", 69.0, "title")
        third = fragment("to validate IBAN.", 158.0, "plain text")
        paragraphs = [third, first, second]
        pf.merge_same_line_sentence_fragments(paragraphs)

        self.assertEqual(len(paragraphs), 1)
        self.assertEqual(paragraphs[0].unicode, "All banks are required to validate IBAN.")

    def test_ruled_table_cells_are_reconstructed_and_kept_on_grid(self):
        """Characters spanning columns are split by vector rules before RTL layout."""
        pf = ParagraphFinder(self.cfg_ar)
        style = PdfStyle(font_id="f_reg", font_size=10.0)
        chars = []
        for value, x in (("A", 20.0), ("B", 70.0)):
            box = Box(x, 85.0, x + 6.0, 95.0)
            chars.append(PdfCharacter(
                char_unicode=value,
                pdf_style=style,
                box=box,
                visual_bbox=VisualBbox(box=box),
            ))
        source = PdfParagraph(
            box=Box(10.0, 80.0, 110.0, 100.0),
            pdf_paragraph_composition=[
                PdfParagraphComposition(pdf_line=PdfLine(
                    box=Box(20.0, 85.0, 76.0, 95.0),
                    pdf_character=chars,
                ))
            ],
            layout_label="table",
        )
        page = Page(cropbox=Cropbox(box=Box(0.0, 0.0, 200.0, 200.0)))
        page.page_layout = [PageLayout(
            box=Box(10.0, 80.0, 110.0, 100.0), id=1, conf=1.0, class_name="table"
        )]
        page.pdf_curve = [
            PdfCurve(box=Box(x - 0.2, 80.0, x + 0.2, 100.0), graphic_state=GraphicState())
            for x in (10.0, 60.0, 110.0)
        ]
        paragraphs = [source]

        pf.reconstruct_wired_table_cells(page, paragraphs)

        self.assertEqual([paragraph.unicode for paragraph in paragraphs], ["A", "B"])
        self.assertTrue(all("fixed_rtl" in paragraph.layout_label for paragraph in paragraphs))
        original_boxes = [(paragraph.box.x, paragraph.box.x2) for paragraph in paragraphs]
        page.pdf_paragraph = paragraphs
        Typesetting(self.cfg_ar)._mirror_table_grids_for_rtl(page)
        self.assertEqual(
            [(paragraph.box.x, paragraph.box.x2) for paragraph in paragraphs],
            original_boxes,
        )

    def test_continuous_ruled_grid_keeps_each_row_separate(self):
        """Normal full-height column rules must not collapse rows into one band."""
        pf = ParagraphFinder(self.cfg_ar)
        style = PdfStyle(font_id="f_reg", font_size=10.0)
        entries = [("A", 25.0, 10.0), ("B", 125.0, 10.0),
                   ("C", 25.0, 45.0), ("D", 125.0, 45.0),
                   ("E", 25.0, 80.0), ("F", 125.0, 80.0)]
        chars = []
        for value, x, y in entries:
            box = Box(x, y, x + 7.0, y + 10.0)
            chars.append(PdfCharacter(
                char_unicode=value, pdf_style=style, box=box,
                visual_bbox=VisualBbox(box=box),
            ))
        source = PdfParagraph(
            box=Box(0.0, 0.0, 200.0, 100.0),
            pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=PdfLine(
                box=Box(0.0, 0.0, 200.0, 100.0), pdf_character=chars
            ))],
            layout_label="table",
        )
        page = Page(cropbox=Cropbox(box=Box(0.0, 0.0, 250.0, 150.0)))
        page.page_layout = [PageLayout(
            box=Box(0.0, 0.0, 200.0, 100.0), id=1, conf=1.0, class_name="table"
        )]
        page.pdf_curve = [
            PdfCurve(box=Box(x - .2, 0.0, x + .2, 100.0), graphic_state=GraphicState())
            for x in (0.0, 100.0, 200.0)
        ] + [
            PdfCurve(box=Box(0.0, y - .2, 200.0, y + .2), graphic_state=GraphicState())
            for y in (0.0, 33.0, 66.0, 100.0)
        ]

        paragraphs = [source]
        pf.reconstruct_wired_table_cells(page, paragraphs)

        self.assertEqual([paragraph.unicode for paragraph in paragraphs], list("ABCDEF"))
        self.assertEqual(
            [(round(paragraph.box.y), round(paragraph.box.y2)) for paragraph in paragraphs],
            [(1, 32), (1, 32), (34, 65), (34, 65), (67, 99), (67, 99)],
        )

    def test_continuous_grid_preserves_merged_header_cell(self):
        """An inner column rule that starts below a header must not split it."""
        pf = ParagraphFinder(self.cfg_ar)
        style = PdfStyle(font_id="f_reg", font_size=10.0)

        def char(value, x, y):
            box = Box(x, y, x + 7.0, y + 10.0)
            return PdfCharacter(char_unicode=value, pdf_style=style, box=box,
                                visual_bbox=VisualBbox(box=box))

        source = PdfParagraph(
            box=Box(0.0, 0.0, 200.0, 100.0),
            pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=PdfLine(
                box=Box(0.0, 0.0, 200.0, 100.0),
                pdf_character=[char("H", 95.0, 80.0), char("L", 25.0, 20.0), char("R", 125.0, 20.0)],
            ))], layout_label="table",
        )
        page = Page(cropbox=Cropbox(box=Box(0.0, 0.0, 250.0, 150.0)))
        page.page_layout = [PageLayout(
            box=Box(0.0, 0.0, 200.0, 100.0), id=1, conf=1.0, class_name="table"
        )]
        page.pdf_curve = [
            PdfCurve(box=Box(x - .2, 0.0, x + .2, 100.0), graphic_state=GraphicState())
            for x in (0.0, 200.0)
        ] + [
            PdfCurve(box=Box(99.8, 0.0, 100.2, 60.0), graphic_state=GraphicState()),
        ] + [
            PdfCurve(box=Box(0.0, y - .2, 200.0, y + .2), graphic_state=GraphicState())
            for y in (0.0, 60.0, 100.0)
        ]

        paragraphs = [source]
        pf.reconstruct_wired_table_cells(page, paragraphs)

        header = next(paragraph for paragraph in paragraphs if paragraph.unicode == "H")
        self.assertAlmostEqual(header.box.x, 2.0)
        self.assertAlmostEqual(header.box.x2, 198.0)
        self.assertEqual(sorted(paragraph.unicode for paragraph in paragraphs), ["H", "L", "R"])

    def test_preserved_rich_text_is_retypeset_as_unicode(self):
        """Protected BIC/IBAN text must not retain source character geometry."""
        style = PdfStyle(font_id="source_font", font_size=10.0)
        source = PdfSameStyleCharacters(
            pdf_style=style,
            pdf_character=[PdfCharacter(char_unicode=char, pdf_style=style) for char in "DOHB"],
        )
        placeholder = RichTextPlaceholder(
            1, source, "<r1>", "</r1>", re.escape("<r1>"), re.escape("</r1>")
        )
        input_text = ILTranslator.TranslateInput(
            "<r1>DOHB</r1>", [placeholder], style
        )

        composition = ILTranslator.parse_translate_output(
            object.__new__(ILTranslator), input_text, "<r1>DOHB</r1>"
        )

        self.assertEqual(len(composition), 1)
        self.assertIsNone(composition[0].pdf_same_style_characters)
        self.assertEqual(composition[0].pdf_same_style_unicode_characters.unicode, "DOHB")

    def test_duplicate_character_ownership_is_purged(self):
        """A ghost paragraph must not export a second copy of the same glyph."""
        pf = ParagraphFinder(self.cfg_ar)
        style = PdfStyle(font_id="base", font_size=10.0)
        shared_char = PdfCharacter(
            char_unicode="A",
            pdf_style=style,
            box=Box(50.0, 400.0, 58.0, 412.0),
            visual_bbox=VisualBbox(box=Box(50.0, 400.0, 58.0, 412.0)),
        )
        first = PdfParagraph(
            pdf_paragraph_composition=[
                PdfParagraphComposition(pdf_line=PdfLine(pdf_character=[shared_char]))
            ],
            debug_id="owner",
        )
        ghost = PdfParagraph(
            pdf_paragraph_composition=[
                PdfParagraphComposition(pdf_line=PdfLine(pdf_character=[shared_char]))
            ],
            debug_id="ghost",
        )
        paragraphs = [first, ghost]

        pf.purge_duplicate_character_ownership(paragraphs)

        self.assertEqual(paragraphs, [first])
        self.assertEqual(
            len(first.pdf_paragraph_composition[0].pdf_line.pdf_character), 1
        )

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

    def test_heading_lane_is_reserved_before_body_reflow(self):
        """Overlapping fallback body text must move below a section heading."""
        ts = Typesetting(self.cfg_ar)
        page = Page(cropbox=Cropbox(box=Box(0.0, 0.0, 600.0, 800.0)))
        heading = PdfParagraph(
            box=Box(80.0, 400.0, 520.0, 416.0), unicode="3.2 Verification",
            layout_label="heading",
        )
        body = PdfParagraph(
            box=Box(80.0, 390.0, 520.0, 410.0), unicode="Body overlaps heading",
            layout_label="paragraph",
        )
        following_body = PdfParagraph(
            box=Box(80.0, 365.0, 520.0, 380.0), unicode="Following text",
            layout_label="paragraph",
        )
        page.pdf_paragraph = [body, following_body, heading]

        ts._resolve_vertical_paragraph_collisions(page)

        self.assertLessEqual(body.box.y2, heading.box.y - 5.0)
        self.assertLessEqual(following_body.box.y2, body.box.y - 3.0)
        original_body_box = (body.box.y, body.box.y2)
        ts._resolve_vertical_paragraph_collisions(page)
        self.assertEqual((body.box.y, body.box.y2), original_body_box)

    def test_numbered_section_label_is_classified_without_bold_font(self):
        """Older PDFs often encode section labels in the same font as body text."""
        section = PdfParagraph(
            unicode="3.2 Validation", layout_label="paragraph",
            box=Box(80.0, 400.0, 400.0, 416.0),
        )
        body = PdfParagraph(
            unicode="All banks are required to validate IBAN.", layout_label="paragraph",
            box=Box(80.0, 375.0, 520.0, 395.0),
        )

        ParagraphFinder.classify_numbered_section_headings([section, body])

        self.assertEqual(section.layout_label, "heading")
        self.assertEqual(body.layout_label, "paragraph")

    def test_procedure_and_contact_blocks_keep_structural_labels(self):
        """End-page instructions and contact fields must not be folded into prose."""
        step = PdfParagraph(
            unicode="Step 6:\nRepeat step No. 3 using MOD 97",
            layout_label="heading",
        )
        contact = PdfParagraph(
            unicode="Tel : +974 44456371\nFax : +974 44430382\nE-Mail : a@qcb.gov.qa",
            layout_label="table_cell",
        )

        ParagraphFinder.classify_structured_text_blocks([step, contact])

        self.assertEqual(step.layout_label, "procedure_step")
        self.assertEqual(contact.layout_label, "contact_card")

    def test_contact_card_recovers_visual_source_lines(self):
        """A multi-line contact block must not become one microscopic sentence."""
        style = PdfStyle(font_id="base", font_size=10.0)

        def make_line(text, y):
            chars = []
            x = 50.0
            for value in text:
                box = Box(x, y, x + 5.0, y + 10.0)
                chars.append(PdfCharacter(
                    char_unicode=value,
                    box=box,
                    visual_bbox=VisualBbox(box=Box(box.x, box.y, box.x2, box.y2)),
                    pdf_style=style,
                ))
                x += 5.0
            return PdfParagraphComposition(pdf_line=PdfLine(
                box=Box(50.0, y, x, y + 10.0), pdf_character=chars
            ))

        paragraph = PdfParagraph(
            unicode="Tel: +974 44456371 Fax: +974 44430382",
            layout_label="contact_card",
            pdf_paragraph_composition=[
                make_line("Tel: +974 44456371", 100.0),
                make_line("Fax: +974 44430382", 80.0),
            ],
        )
        recovered = ILTranslator._structured_source_lines(paragraph)
        self.assertEqual(recovered, "Tel: +974 44456371\nFax: +974 44430382")

    def test_embedded_procedure_steps_split_on_visual_lines(self):
        """Two steps extracted as one block remain independent records."""
        style = PdfStyle(font_id="base", font_size=10.0)

        def make_line(text, y):
            chars = []
            x = 50.0
            for value in text:
                box = Box(x, y, x + 5.0, y + 10.0)
                chars.append(PdfCharacter(
                    char_unicode=value,
                    box=box,
                    visual_bbox=VisualBbox(box=Box(box.x, box.y, box.x2, box.y2)),
                    pdf_style=style,
                ))
                x += 5.0
            return PdfParagraphComposition(pdf_line=PdfLine(
                box=Box(50.0, y, x, y + 10.0), pdf_character=chars
            ))

        paragraph = PdfParagraph(
            unicode="Step 3: Apply MOD 97 Step 4: Calculate remainder",
            layout_label="paragraph",
            pdf_paragraph_composition=[
                make_line("Step 3:", 100.0),
                make_line("Apply MOD 97", 82.0),
                make_line("Step 4:", 64.0),
                make_line("Calculate remainder", 46.0),
            ],
        )
        paragraphs = [paragraph]
        ParagraphFinder(self.cfg_ar).split_embedded_structured_lines(paragraphs)

        self.assertEqual(len(paragraphs), 4)
        self.assertEqual(
            [paragraph.layout_label for paragraph in paragraphs],
            ["procedure_step", "procedure_step_body", "procedure_step", "procedure_step_body"],
        )
        self.assertEqual([paragraph.unicode for paragraph in paragraphs], [
            "Step 3:", "Apply MOD 97", "Step 4:", "Calculate remainder"
        ])

    def test_embedded_numbered_heading_splits_surrounding_prose(self):
        """A section marker inside one source block keeps reading order."""
        style = PdfStyle(font_id="base", font_size=10.0)

        def make_line(text, y):
            chars = []
            x = 50.0
            for value in text:
                box = Box(x, y, x + 5.0, y + 10.0)
                chars.append(PdfCharacter(
                    char_unicode=value,
                    box=box,
                    visual_bbox=VisualBbox(box=Box(box.x, box.y, box.x2, box.y2)),
                    pdf_style=style,
                ))
                x += 5.0
            return PdfParagraphComposition(pdf_line=PdfLine(
                box=Box(50.0, y, x, y + 10.0), pdf_character=chars
            ))

        paragraph = PdfParagraph(
            unicode="by the QCB 3.2 Validation All banks are required",
            layout_label="paragraph",
            pdf_paragraph_composition=[
                make_line("by the QCB", 100.0),
                make_line("3.2 Validation", 82.0),
                make_line("All banks are required", 64.0),
            ],
        )
        paragraphs = [paragraph]
        ParagraphFinder(self.cfg_ar).split_embedded_structured_lines(paragraphs)

        self.assertEqual([paragraph.layout_label for paragraph in paragraphs], [
            "paragraph", "heading", "paragraph"
        ])
        self.assertEqual([paragraph.unicode for paragraph in paragraphs], [
            "by the QCB", "3.2 Validation", "All banks are required"
        ])

    def test_heading_prefix_continuation_joins_previous_paragraph(self):
        """A lowercase prefix before a section marker stays with prior prose."""
        style = PdfStyle(font_id="base", font_size=10.0)

        def make_line(text, y):
            chars = []
            x = 50.0
            for value in text:
                box = Box(x, y, x + 5.0, y + 10.0)
                chars.append(PdfCharacter(
                    char_unicode=value,
                    box=box,
                    visual_bbox=VisualBbox(box=Box(box.x, box.y, box.x2, box.y2)),
                    pdf_style=style,
                ))
                x += 5.0
            return PdfParagraphComposition(pdf_line=PdfLine(
                box=Box(50.0, y, x, y + 10.0), pdf_character=chars
            ))

        previous = PdfParagraph(
            unicode="specified", layout_label="paragraph", xobj_id=0,
            box=Box(50.0, 118.0, 95.0, 128.0),
            pdf_paragraph_composition=[make_line("specified", 118.0)],
        )
        mixed = PdfParagraph(
            unicode="by the QCB 3.2 Validation All banks are required",
            layout_label="paragraph", xobj_id=0,
            pdf_paragraph_composition=[
                make_line("by the QCB", 100.0),
                make_line("3.2 Validation", 82.0),
                make_line("All banks are required", 64.0),
            ],
        )
        paragraphs = [previous, mixed]
        ParagraphFinder(self.cfg_ar).split_embedded_structured_lines(paragraphs)

        self.assertEqual([paragraph.layout_label for paragraph in paragraphs], [
            "paragraph", "heading", "paragraph"
        ])
        self.assertIn("by the QCB", paragraphs[0].unicode)

    def test_contact_field_direct_translation_preserves_value(self):
        """Phone, fax and e-mail payloads never depend on MT placeholders."""
        translator = ILTranslator.__new__(ILTranslator)
        translator.translation_config = self.cfg_ar
        self.assertEqual(
            translator._translate_contact_field_directly("Tel : +974 44456371"),
            "الهاتف: +974 44456371",
        )
        self.assertEqual(
            translator._translate_contact_field_directly("E-Mail: ahena@qcb.gov.qa"),
            "البريد الإلكتروني: ahena@qcb.gov.qa",
        )

    def test_structured_procedure_boxes_expand_to_readable_width(self):
        """A narrow Step label receives the full instruction lane before layout."""
        ts = Typesetting(self.cfg_ar)
        page = Page(cropbox=Cropbox(box=Box(0.0, 0.0, 600.0, 800.0)))
        step = PdfParagraph(
            unicode="Step 11:", layout_label="procedure_step",
            box=Box(50.0, 300.0, 90.0, 315.0),
        )
        page.pdf_paragraph = [step]
        ts._expand_structured_text_boxes(page)
        self.assertGreaterEqual(step.box.x2 - step.box.x, 500.0)

    def test_formula_without_parent_box_recovers_child_geometry(self):
        """Malformed formula metadata cannot abort a full PDF render."""
        child_box = Box(25.0, 40.0, 35.0, 52.0)
        child = PdfCharacter(
            char_unicode="7",
            box=child_box,
            visual_bbox=VisualBbox(box=Box(25.0, 40.0, 35.0, 52.0)),
            pdf_style=PdfStyle(font_id="base", font_size=10.0),
        )
        unit = TypesettingUnit(formular=PdfFormula(box=None, pdf_character=[child]))
        self.assertEqual((unit.box.x, unit.box.y, unit.box.x2, unit.box.y2), (25.0, 40.0, 35.0, 52.0))

    def test_narrow_heading_boxes_expand_to_readable_width(self):
        """Layout headings receive a stable lane even without numeric prefixes."""
        ts = Typesetting(self.cfg_ar)
        page = Page(cropbox=Cropbox(box=Box(0.0, 0.0, 600.0, 800.0)))
        heading = PdfParagraph(
            unicode="Validation of IBAN",
            layout_label="heading",
            box=Box(480.0, 300.0, 550.0, 315.0),
        )
        page.pdf_paragraph = [heading]
        ts._expand_structured_text_boxes(page)
        self.assertGreaterEqual(heading.box.x2 - heading.box.x, 500.0)

    def test_repeated_unmapped_list_markers_split_rows_but_keep_wrapped_text(self):
        finder = ParagraphFinder(self.cfg_ar)
        self.cfg_ar.split_short_lines = False

        def line(text, y, marker_font=None):
            chars = []
            if marker_font:
                box = Box(74, y, 78, y + 12)
                chars.append(PdfCharacter(
                    char_unicode="(cid:136)", box=box, visual_bbox=VisualBbox(box=box),
                    pdf_style=PdfStyle(font_id=marker_font, font_size=10),
                ))
            for i, char in enumerate(text):
                x = 84 + i * 5
                box = Box(x, y, x + 5, y + 12)
                chars.append(PdfCharacter(
                    char_unicode=char, box=box, visual_bbox=VisualBbox(box=box),
                    pdf_style=PdfStyle(font_id="body", font_size=10),
                ))
            return finder.create_line(chars)

        for font_id, expected in (("symbol", 2), ("body", 1)):
            with self.subTest(marker_font=font_id):
                paragraphs = [PdfParagraph(
                    layout_label="plain text",
                    pdf_paragraph_composition=[
                        line("First item", 100, font_id),
                        line("continues here", 85),
                        line("Second item", 70, font_id),
                    ],
                )]
                finder.process_independent_paragraphs(paragraphs, median_width=150)

                self.assertEqual(len(paragraphs), expected)
                if expected == 2:
                    self.assertEqual([paragraph.layout_label for paragraph in paragraphs], ["list_item"] * 2)
                    self.assertEqual(len(paragraphs[0].pdf_paragraph_composition), 2)
                    self.assertIn("continues here", paragraphs[0].unicode)
                    self.assertIn("Second item", paragraphs[1].unicode)

    def test_contact_card_stays_in_its_closed_frame(self):
        ts = Typesetting(self.cfg_ar)
        page = Page(cropbox=Cropbox(box=Box(0, 0, 600, 800)))
        page.pdf_curve = [
            PdfCurve(box=Box(100, 200, 100.5, 300)),
            PdfCurve(box=Box(410, 200, 410.5, 300)),
            PdfCurve(box=Box(100.5, 199.5, 410, 200)),
            PdfCurve(box=Box(100.5, 300, 410, 300.5)),
        ]
        row = PdfParagraph(
            layout_label="contact_card", box=Box(110, 270, 290, 284)
        )
        page.pdf_paragraph = [row]

        ts._expand_structured_text_boxes(page)

        self.assertGreater(row.box.x, 100.5)
        self.assertLess(row.box.x2, 410)
        self.assertEqual((row.box.y, row.box.y2), (270, 284))
        self.assertEqual(ts._calculate_rtl_margin_shift(row, page), 0)

    def test_contact_card_without_closed_frame_keeps_a_wide_lane(self):
        ts = Typesetting(self.cfg_ar)
        page = Page(cropbox=Cropbox(box=Box(0, 0, 600, 800)))
        page.pdf_curve = [
            PdfCurve(box=Box(100, 200, 100.5, 300)),
            PdfCurve(box=Box(410, 200, 410.5, 300)),
            PdfCurve(box=Box(100.5, 199.5, 410, 200)),
        ]
        row = PdfParagraph(
            layout_label="contact_card", box=Box(110, 270, 290, 284)
        )
        page.pdf_paragraph = [row]

        ts._expand_structured_text_boxes(page)

        self.assertGreater(row.box.x2 - row.box.x, 500)

    def test_rendered_glyph_collision_moves_body_not_heading(self):
        """The final audit must use character boxes, not stale paragraph boxes."""
        ts = Typesetting(self.cfg_ar)
        page = Page(cropbox=Cropbox(box=Box(0.0, 0.0, 600.0, 800.0)))

        def rendered_paragraph(text, box, label):
            char = PdfCharacter(
                char_unicode=text, box=box, visual_bbox=VisualBbox(box=Box(
                    box.x, box.y, box.x2, box.y2
                )), pdf_style=PdfStyle(font_id="base", font_size=12.0),
            )
            return PdfParagraph(
                # Deliberately non-overlapping source boxes: only the rendered
                # glyph geometry reveals the collision.
                box=Box(box.x, box.y - 35.0, box.x2, box.y - 20.0),
                layout_label=label,
                pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=char)],
            )

        heading = rendered_paragraph("Heading", Box(100.0, 400.0, 400.0, 420.0), "heading")
        body = rendered_paragraph("Body", Box(100.0, 395.0, 500.0, 415.0), "paragraph")
        page.pdf_paragraph = [body, heading]

        ts._resolve_rendered_text_collisions(page)

        heading_box = ts._rendered_text_box(heading)
        body_box = ts._rendered_text_box(body)
        self.assertLessEqual(body_box.y2, heading_box.y - 5.0)
        self.assertEqual((heading_box.y, heading_box.y2), (400.0, 420.0))

    def test_rendered_heading_does_not_move_a_preceding_bullet_below_it(self):
        ts = Typesetting(self.cfg_ar)
        page = Page(cropbox=Cropbox(box=Box(0, 0, 600, 800)))
        bullet = PdfParagraph(
            box=Box(100, 543, 230, 556),
            layout_label="plain text",
            pdf_paragraph_composition=[
                PdfParagraphComposition(pdf_character=PdfCharacter(
                    char_unicode="Resume parsing", box=Box(420, 547, 520, 556),
                )),
            ],
        )
        heading = PdfParagraph(
            box=Box(100, 522, 245, 534),
            layout_label="title",
            pdf_paragraph_composition=[
                PdfParagraphComposition(pdf_character=PdfCharacter(
                    char_unicode="Error Production Method", box=Box(410, 523, 520, 552),
                )),
            ],
        )
        page.pdf_paragraph = [heading, bullet]

        ts._resolve_rendered_text_collisions(page)

        bullet_box = ts._rendered_text_box(bullet)
        heading_box = ts._rendered_text_box(heading)
        self.assertEqual((bullet_box.y, bullet_box.y2), (547, 556))
        self.assertLessEqual(heading_box.y2, bullet_box.y - 5)

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

    def test_doubled_running_header_glyphs_are_collapsed(self):
        """A duplicated extraction stream must not become IIBBAANN after retyping."""
        ts = Typesetting(self.cfg_ar)
        page = Page(cropbox=Cropbox(box=Box(0.0, 0.0, 600.0, 800.0)))
        paragraph = PdfParagraph(
            box=Box(80.0, 760.0, 520.0, 775.0),
            unicode="IIBBAANN SSttaannddaarrddss",
            layout_label="page_header",
        )
        style = PdfStyle(font_id="base", font_size=10.0)
        units = [
            TypesettingUnit(char=PdfCharacter(char_unicode=char, pdf_style=style, box=Box(0, 0, 1, 1)))
            for char in paragraph.unicode
        ]

        collapsed = ts._deduplicate_artificial_header_units(paragraph, page, units)
        self.assertEqual("".join(unit.try_get_unicode() or "" for unit in collapsed), "IBAN Standards")

    def test_heading_is_raised_above_table_grid(self):
        """Headings must reserve a gap above immutable ruled tables."""
        ts = Typesetting(self.cfg_ar)
        page = Page(cropbox=Cropbox(box=Box(0.0, 0.0, 600.0, 800.0)))
        heading = PdfParagraph(box=Box(80.0, 492.0, 520.0, 507.0), layout_label="heading")
        cell = PdfParagraph(box=Box(80.0, 400.0, 520.0, 495.0), layout_label="table_cell")
        page.pdf_paragraph = [heading, cell]

        ts._reserve_heading_clearance_from_tables(page)
        self.assertGreaterEqual(heading.box.y, cell.box.y2 + 5.0)

    def test_table_grid_rtl_mirroring(self):
        """RTL table conversion mirrors the whole grid, not just each text run."""
        ts = Typesetting(self.cfg_ar)
        page = Page(cropbox=Cropbox(box=Box(0.0, 0.0, 200.0, 200.0)))
        left = PdfParagraph(box=Box(10.0, 100.0, 40.0, 120.0), unicode="POSITION", layout_label="table_cell")
        middle = PdfParagraph(box=Box(40.0, 100.0, 80.0, 120.0), unicode="1-2", layout_label="table_cell")
        right = PdfParagraph(box=Box(80.0, 100.0, 150.0, 120.0), unicode="9-29", layout_label="table_cell")
        lower_left = PdfParagraph(box=Box(10.0, 80.0, 40.0, 100.0), unicode="Example", layout_label="table_cell")
        lower_middle = PdfParagraph(box=Box(40.0, 80.0, 80.0, 100.0), unicode="QA", layout_label="table_cell")
        lower_right = PdfParagraph(box=Box(80.0, 80.0, 150.0, 100.0), unicode="DOHB", layout_label="table_cell")
        table_fill = PdfRectangle(box=Box(10.0, 80.0, 150.0, 120.0), graphic_state=GraphicState())
        page.pdf_paragraph = [left, middle, right, lower_left, lower_middle, lower_right]
        page.pdf_rectangle = [table_fill]

        ts._mirror_table_grids_for_rtl(page)

        self.assertEqual((left.box.x, left.box.x2), (120.0, 150.0))
        self.assertEqual((right.box.x, right.box.x2), (10.0, 80.0))
        self.assertEqual((table_fill.box.x, table_fill.box.x2), (10.0, 150.0))

        # The operation is deliberately idempotent because the page is visited
        # during preprocessing and rendering.
        ts._mirror_table_grids_for_rtl(page)
        self.assertEqual((left.box.x, left.box.x2), (120.0, 150.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
