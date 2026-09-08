# -*- coding: utf-8 -*-
"""
Technical and Banking Document Regression Test Suite.
Tests high-fidelity translation, typesetting, and numeric/code integrity for dense technical
and banking standards documents (Qatar Central Bank IBAN Standards Document 13-193.pdf).

Verifies:
  1. Full 17 Qatar Bank SWIFT / BIC codes and Bank Identifiers bypass translation (Bug Group 1).
  2. Full 54 IBAN participant country ISO 2-letter codes retain strict LTR order (Bug Group 2).
  3. PUA bullet characters (\uF0B7, Wingdings 61623) trigger hanging indents without text collision (Bug Group 4).
  4. Multi-line division/equations (281211210/97 = 62) are protected as atomic tokens (Bug Group 5).
  5. Contact communications metadata (Tel, Fax, phone numbers) are standardized cleanly (Bug Group 6).
  6. Paragraph routing and execution path tracing diagnostics (Bug Group 3).
"""

import sys
import unittest
import re

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

from babeldoc.translator.translator import (
    _protect_numerals,
    _restore_numerals,
    _is_pure_passthrough,
    _NUMERAL_PATTERN,
    _SWIFT_BIC_PATTERN,
    _IBAN_PATTERN,
)
from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting, TypesettingUnit
from babeldoc.format.pdf.document_il.il_version_1 import (
    Box,
    PdfParagraph,
    PdfStyle,
    PdfParagraphComposition,
    PdfSameStyleUnicodeCharacters,
)
from babeldoc.format.pdf.document_il.utils.layout_helper import BULLET_POINT_PATTERN


# ==============================================================================
# Fixture 1: Full 17 Qatar Bank Rows from Section 2.1.1 (Page 6 of 13-193.pdf)
# ==============================================================================
QATAR_BANKS_SWIFT_FIXTURE = [
    ("QATAR NATIONAL BANK", "QNBAQAQAXXX", "QNBA"),
    ("COMMERCIAL BANK OF QATAR", "CBQAQAQAXXX", "CBQA"),
    ("DOHA BANK", "DOHBQAQAXXX", "DOHB"),
    ("QATAR ISLAMIC BANK", "QISBQAQAXXX", "QISB"),
    ("AHLI BANK OF QATAR", "ABQQQAQAXXX", "ABQQ"),
    ("QATAR INTL ISLAMIC BANK", "QIIBQAQAXXX", "QIIB"),
    ("ARAB BANK", "ARABQAQAXXX", "ARAB"),
    ("MASHREQ BANK", "MSHQQAQAXXX", "MSHQ"),
    ("INTERNATIONAL BANK OF QATAR", "IBOQQAQAXXX", "IBOQ"),
    ("HSBC", "BBMEQAQXXXX", "BBME"),
    ("STANDARD CHARTERED BANK", "SCBLQAQXXXX", "SCBL"),
    ("UNITED BANK LTD", "UNILQAQAXXX", "UNIL"),
    ("BNP PARIBAS", "BNPAQAQAXXX", "BNPA"),
    ("RAYYAN BANK", "MAFRQAQAXXX", "MAFR"),
    ("AL KHALIJ COMMERCIAL BANK", "KLJIQAQAXXX", "KLJI"),
    ("BANK BARWA", "BRWAQAQAXXX", "BRWA"),
    ("QATAR DEVELOPMENT BANK", "QIDBQAQAXXX", "QIDB"),
]


# ==============================================================================
# Fixture 2: Full 54 Countries and ISO Codes from Table on Page 8 of 13-193.pdf
# ==============================================================================
IBAN_54_COUNTRIES_FIXTURE = [
    ("Albania", "AL"),
    ("Andorra", "AD"),
    ("Austria", "AT"),
    ("Belgium", "BE"),
    ("Bahrain", "BH"),
    ("Bosnia and Herzegovina", "BA"),  # Previously reversed to A B
    ("Bulgaria", "BG"),
    ("Croatia", "HR"),
    ("Cyprus", "CY"),
    ("Czech Republic", "CZ"),          # Previously reversed to Z C
    ("Denmark", "DK"),
    ("Estonia", "EE"),
    ("Faroe Islands", "FO"),           # Previously reversed to O F
    ("Finland", "FI"),
    ("France", "FR"),
    ("Georgia", "GE"),
    ("Germany", "DE"),
    ("Gibraltar", "GI"),               # Previously reversed to I G
    ("Greece", "GR"),
    ("Greenland", "GL"),
    ("Hungary", "HU"),
    ("Iceland", "IS"),
    ("Ireland", "IE"),
    ("Israel", "IL"),
    ("Italy", "IT"),
    ("Kazakhstan", "KZ"),
    ("Latvia", "LV"),
    ("Lebanon", "LB"),
    ("Liechtenstein", "LI"),
    ("Lithuania", "LT"),
    ("Luxembourg", "LU"),
    ("Macedonia", "MK"),
    ("Malta", "MT"),
    ("Mauritania", "MR"),
    ("Mauritius", "MU"),
    ("Monaco", "MC"),
    ("Montenegro", "ME"),              # Previously reversed to E M
    ("Netherlands", "NL"),
    ("Norway", "NO"),
    ("Poland", "PL"),
    ("Portugal", "PT"),
    ("Romania", "RO"),
    ("San Marino", "SM"),              # Previously reversed to M S
    ("Saudi Arabia", "SA"),            # Previously reversed to A S
    ("Serbia", "RS"),
    ("Slovakia", "SK"),
    ("Slovenia", "SI"),
    ("Spain", "ES"),
    ("Sweden", "SE"),
    ("Switzerland", "CH"),
    ("Tunisia", "TN"),
    ("Turkey", "TR"),
    ("United Kingdom", "GB"),          # Previously reversed to B G
    ("United Arab Emirates", "AE"),    # Previously reversed to E A
]


class TestTechnicalDocumentRegression(unittest.TestCase):
    """Regression test suite for technical, banking, and formula preservation."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = TranslationConfig(
            translator=None,
            input_file="",
            lang_in="en",
            lang_out="ar",
            doc_layout_model=None,
        )
        cls.typesetting = Typesetting(cls.cfg)
        cls.ilt = ILTranslator(None, cls.cfg)
        cls.base_font = cls.typesetting.font_mapper.base_font
        cls.style = PdfStyle(font_id="F1", font_size=10.0)

    # --------------------------------------------------------------------------
    # Bug Group 1: SWIFT / BIC codes and 4-Letter Bank Identifiers
    # --------------------------------------------------------------------------
    def test_bank_swift_and_identifiers_passthrough(self):
        """All 17 Qatar bank SWIFT/BIC codes and 4-letter bank identifiers must bypass translation."""
        for bank_name, swift_code, bank_id in QATAR_BANKS_SWIFT_FIXTURE:
            with self.subTest(bank=bank_name, swift=swift_code, bid=bank_id):
                # 1. Direct SWIFT BIC regex match
                self.assertTrue(
                    _SWIFT_BIC_PATTERN.match(swift_code),
                    f"SWIFT BIC pattern failed for {swift_code} ({bank_name})"
                )
                # 2. Pure passthrough check for SWIFT code cell
                self.assertTrue(
                    _is_pure_passthrough(swift_code),
                    f"SWIFT code {swift_code} failed _is_pure_passthrough"
                )
                # 3. Pure passthrough check for 4-letter Bank Identifier cell
                self.assertTrue(
                    _is_pure_passthrough(bank_id),
                    f"Bank Identifier {bank_id} failed _is_pure_passthrough"
                )
                # 4. SWIFT code embedded in sentence context must be protected
                sentence = f"Payments routed via {swift_code} using identifier {bank_id}."
                prot, mapping = _protect_numerals(sentence)
                self.assertIn(
                    swift_code,
                    mapping.values(),
                    f"Embedded SWIFT code {swift_code} was not protected"
                )
                restored = _restore_numerals(prot, mapping)
                self.assertEqual(
                    sentence,
                    restored,
                    f"Restoration mismatch for {sentence}"
                )

    # --------------------------------------------------------------------------
    # Bug Group 2: Full 54-Country 2-Letter ISO Codes LTR Ordering
    # --------------------------------------------------------------------------
    def test_54_country_iso_codes_ltr_preservation(self):
        """All 54 2-letter ISO country codes must retain strict Left-to-Right character order."""
        for country, code in IBAN_54_COUNTRIES_FIXTURE:
            with self.subTest(country=country, code=code):
                # 1. Verify code passes through without translation
                self.assertTrue(
                    _is_pure_passthrough(code),
                    f"Country code {code} ({country}) failed _is_pure_passthrough"
                )

                # 2. Verify typesetting in a table cell preserves strict LTR order
                units = [
                    TypesettingUnit(unicode=ch, font=self.base_font, font_size=10.0, style=self.style, xobj_id=-1)
                    for ch in code
                ]
                box = Box(x=340.0, y=200.0, x2=360.0, y2=212.0)
                para = PdfParagraph(layout_label="table_cell", box=box)
                laid_out, fits = self.typesetting._layout_typesetting_units(
                    units, box, scale=1.0, line_skip=1.2, paragraph=para
                )

                # Collect characters and their horizontal positions
                laid_chars = [u.unicode for u in laid_out]
                laid_str = "".join(laid_chars)
                self.assertEqual(
                    laid_str,
                    code,
                    f"Country code '{code}' ({country}) was reordered to '{laid_str}'"
                )

                # Check that character 0 is strictly to the left of character 1
                if len(laid_out) >= 2:
                    pos_0 = laid_out[0].box.x
                    pos_1 = laid_out[1].box.x
                    self.assertLess(
                        pos_0,
                        pos_1,
                        f"Code '{code}' characters reversed horizontally: {laid_out[0].unicode} at {pos_0} vs {laid_out[1].unicode} at {pos_1}"
                    )

    def test_previously_reversed_9_iso_codes_specifically(self):
        """Explicitly test the 9 codes confirmed reversed in earlier output."""
        reversed_targets = [
            ("BA", "A B"),
            ("CZ", "Z C"),
            ("FO", "O F"),
            ("GI", "I G"),
            ("ME", "E M"),
            ("SM", "M S"),
            ("SA", "A S"),
            ("GB", "B G"),
            ("AE", "E A"),
        ]
        for correct_code, bad_reversed in reversed_targets:
            units = [
                TypesettingUnit(unicode=ch, font=self.base_font, font_size=10.0, style=self.style, xobj_id=-1)
                for ch in correct_code
            ]
            box = Box(x=340.0, y=200.0, x2=360.0, y2=212.0)
            para = PdfParagraph(layout_label="table_cell", box=box)
            laid_out, fits = self.typesetting._layout_typesetting_units(
                units, box, scale=1.0, line_skip=1.2, paragraph=para
            )
            result_str = "".join(u.unicode for u in laid_out)
            self.assertEqual(
                result_str,
                correct_code,
                f"Expected '{correct_code}', but got '{result_str}' (must never match '{bad_reversed}')"
            )

    # --------------------------------------------------------------------------
    # Bug Group 4: PUA Bullet Codepoints (\uF0B7 / Wingdings 61623)
    # --------------------------------------------------------------------------
    def test_pua_bullet_point_hanging_indent(self):
        """PUA bullet codepoints (\uF0B7) must trigger RTL hanging indent without word collision."""
        pua_bullet = "\uF0B7"  # 61623
        self.assertTrue(
            bool(BULLET_POINT_PATTERN.match(pua_bullet)),
            r"BULLET_POINT_PATTERN must match \uF0B7"
        )

        # Multi-line Arabic list item starting with \uF0B7
        # In the original document, \uF0B7 collided with text to produce 'ابتولديها'
        text = f"{pua_bullet} تتكون من جزأين رئيسيين للتحقق من صحة الحساب المصرفي الموحد وفقاً للمعايير الدولية الصادرة عن المنظمة"
        shaped = self.ilt.shape_arabic_text(text)
        units = [
            TypesettingUnit(unicode=ch, font=self.base_font, font_size=10.0, style=self.style, xobj_id=-1)
            for ch in shaped
        ]
        box = Box(x=50.0, y=100.0, x2=250.0, y2=200.0)  # Narrow width forces wrap
        para = PdfParagraph(layout_label="list_item", box=box)

        laid_out, fits = self.typesetting._layout_typesetting_units(
            units, box, scale=1.0, line_skip=1.2, paragraph=para
        )

        # 1. Pipeline trace must record is_bullet_para = True
        self.assertTrue(
            para.pipeline_trace.get("is_bullet_para", False),
            "Paragraph starting with PUA bullet must be detected as bullet paragraph"
        )
        # 2. Hanging indent must be >= 14.0 pt
        bullet_indent = para.pipeline_trace.get("bullet_indent", 0.0)
        self.assertGreaterEqual(
            bullet_indent,
            14.0,
            f"Bullet hanging indent ({bullet_indent}pt) should be >= 14.0pt"
        )
        # 3. Bullet unit must remain isolated as a single character at the right margin (box.x2 = 250.0)
        bullet_units = [u for u in laid_out if u.unicode == pua_bullet]
        self.assertEqual(len(bullet_units), 1, "Must find exactly one isolated bullet unit")
        bullet_u = bullet_units[0]
        self.assertAlmostEqual(
            bullet_u.box.x2,
            box.x2,
            delta=1.0,
            msg=f"PUA bullet must be positioned flush with right margin {box.x2}, found at {bullet_u.box.x2}"
        )

    # --------------------------------------------------------------------------
    # Bug Group 5: Check Digit Generation Formulas and Equations
    # --------------------------------------------------------------------------
    def test_mathematical_equations_atomic_protection(self):
        """Formulas, divisions, and modulo operations must be atomically protected."""
        formulas = [
            "281211210/97 = 62",
            "502560126/97 =22",
            "221000/97= 34",
            "98-34=64",
            "620000000/97 =56",
            "560001375/97= 5",
            "221064/97 = 1",
            "MOD 97-10",
            "MOD 97",
            "ISO/IEC 7064",
            "ISO 13616-1",
            "QA00SCBL000000000001375025601",
            "SCBL000000000001375025601QA00",
        ]

        for formula in formulas:
            with self.subTest(formula=formula):
                # 1. As standalone cell, must pass through directly
                self.assertTrue(
                    _is_pure_passthrough(formula),
                    f"Formula '{formula}' failed _is_pure_passthrough"
                )

                # 2. In mixed text context, must be captured as single atomic token
                context = f"Calculate remainder using formula ({formula}) in step 3."
                prot, mapping = _protect_numerals(context)
                
                # Check that formula or parenthetical containing it is in mapping values
                found_match = any(formula in val for val in mapping.values())
                self.assertTrue(
                    found_match,
                    f"Formula '{formula}' was not atomically captured in mapping: {mapping}"
                )

                # 3. Restore byte-for-byte
                restored = _restore_numerals(prot, mapping)
                self.assertEqual(
                    context,
                    restored,
                    f"Formula '{formula}' corrupted during restore: {restored}"
                )

    # --------------------------------------------------------------------------
    # Bug Group 6: Contact Metadata and Telephone Terminology
    # --------------------------------------------------------------------------
    def test_contact_info_and_phone_standardization(self):
        """Tel, Fax, and E-mail metadata must translate accurately without 'التلفزيون' hallucination."""
        # 1. Terminology pre-translation
        sample_tel = "Tel : +974 44456371"
        pre_tel = self.ilt._pre_translate_terminology(sample_tel)
        self.assertIn("الهاتف:", pre_tel)
        self.assertNotIn("التلفزيون", pre_tel)

        sample_fax = "Fax : +974 44430382"
        pre_fax = self.ilt._pre_translate_terminology(sample_fax)
        self.assertIn("الفاكس:", pre_fax)

        sample_email = "E-Mail : bankingoperations@qcb.gov.qa"
        pre_email = self.ilt._pre_translate_terminology(sample_email)
        self.assertIn("البريد الإلكتروني:", pre_email)

        # 2. Output normalization cleans any lingering 'التلفزيون' in phone context
        hallucinated = "التلفزيون : +974 44456371"
        cleaned = self.ilt._normalize_arabic_output(hallucinated)
        self.assertEqual(cleaned, "الهاتف: +974 44456371")

        # 3. Pure passthrough for phone and fax numbers
        self.assertTrue(_is_pure_passthrough("+974 44456371"))
        self.assertTrue(_is_pure_passthrough("+974 44430382"))

    # --------------------------------------------------------------------------
    # Bug Group 3: Paragraph Routing and Pipeline Tracing
    # --------------------------------------------------------------------------
    def test_paragraph_pipeline_trace_diagnostics(self):
        """Paragraphs must carry diagnostic pipeline_trace recording routing, layout_label, and stats."""
        para = PdfParagraph(layout_label="body_text", box=Box(x=50.0, y=50.0, x2=450.0, y2=80.0))
        text = "All banks are required to validate IBAN for incoming payments."
        units = [
            TypesettingUnit(unicode=ch, font=self.base_font, font_size=10.0, style=self.style, xobj_id=-1)
            for ch in text
        ]

        # 1. Typesetting layout updates pipeline_trace
        laid_out, fits = self.typesetting._layout_typesetting_units(
            units, para.box, scale=1.0, line_skip=1.2, paragraph=para
        )

        # Assert trace exists and contains essential diagnostics
        self.assertTrue(hasattr(para, "pipeline_trace"))
        trace = para.pipeline_trace
        self.assertIsNotNone(trace)
        self.assertIn("num_lines", trace)
        self.assertIn("is_bullet_para", trace)

        # 2. render_paragraph also populates pipeline_trace
        from babeldoc.format.pdf.document_il.il_version_1 import Page, Cropbox
        page = Page(cropbox=Cropbox(box=Box(x=0.0, y=0.0, x2=595.0, y2=842.0)))
        self.typesetting.render_paragraph(para, page, {})
        self.assertEqual(para.pipeline_trace.get("layout_label"), "body_text")
        self.assertIn("is_arabic_output", para.pipeline_trace)
        self.assertIn("is_table_content", para.pipeline_trace)
        self.assertIn("can_passthrough", para.pipeline_trace)

    # --------------------------------------------------------------------------
    # Bug Group 7: Table of Contents (TOC) Dotted Leader Lines Preservation
    # --------------------------------------------------------------------------
    def test_toc_leader_dots_preservation(self):
        """TOC dotted leader lines must not be collapsed or dropped, and must dynamically fit on a single line."""
        toc_samples = [
            ("Introduction ................................................................................................. 1306", "1306"),
            ("2.1.1 Qatar Banks Swift Code and Identifier ............................................... 1309", "1309"),
            ("6 . Appendix ...............................................................................................................1421", "1421"),
        ]

        for sample_text, expected_page in toc_samples:
            with self.subTest(sample=sample_text[:30]):
                # 1. Verify translation preserves dots and page number without dropping tokens
                # Mock a lightweight translate_engine that returns genuine Arabic for title
                mock_arabic_dict = {
                    "Introduction": "المقدمة",
                    "2.1.1 Qatar Banks Swift Code and Identifier": "2.1.1 رمز ومعرف سويفت لبنوك قطر",
                    "6 . Appendix": "6. الملحق",
                }
                class MockEngine:
                    def translate(self, text, rate_limit_params=None):
                        return mock_arabic_dict.get(text.strip(), f"عنوان {text}")

                self.ilt.translate_engine = MockEngine()
                tr_line = self.ilt._translate_with_line_preservation(sample_text, rate_limit_params={})

                # Assert dots and page number are preserved
                self.assertIn(expected_page, tr_line)
                self.assertTrue(re.search(r'\.{4,}', tr_line), f"Dots were lost in: {tr_line}")

                # 2. Verify typesetting renders the TOC entry on a single line without wrapping
                shaped = self.ilt.shape_arabic_text(tr_line)
                units = [
                    TypesettingUnit(unicode=ch, font=self.base_font, font_size=10.0, style=self.style, xobj_id=-1)
                    for ch in shaped
                ]
                box = Box(x=50.0, y=100.0, x2=500.0, y2=120.0)
                para = PdfParagraph(layout_label="table_of_content", box=box)
                laid_out, fits = self.typesetting._layout_typesetting_units(
                    units, box, scale=1.0, line_skip=1.2, paragraph=para
                )

                self.assertTrue(fits, f"TOC line must fit in box: {tr_line}")

                # Verify single-line layout (all units have the same Y coordinate)
                unique_y = set(round(u.box.y, 1) for u in laid_out)
                self.assertEqual(
                    len(unique_y),
                    1,
                    f"TOC line wrapped into {len(unique_y)} lines, expected 1: {unique_y}"
                )

                # Verify leader dots exist in laid out units
                dot_count = sum(1 for u in laid_out if u.unicode == '.')
                self.assertGreaterEqual(dot_count, 10, f"Expected at least 10 leader dots, found {dot_count}")

                # Verify page number is at the left margin and title is at the right margin
                page_units = [u for u in laid_out if u.unicode in expected_page]
                self.assertTrue(len(page_units) > 0)
                leftmost_page_x = min(u.box.x for u in page_units)
                rightmost_u_x2 = max(u.box.x2 for u in laid_out)
                self.assertLess(leftmost_page_x, box.x + 100.0, "Page number should be near left margin")
                self.assertGreater(rightmost_u_x2, box.x2 - 100.0, "Title should be near right margin")

    # --------------------------------------------------------------------------
    # Bug Group 9: Table Cell Overflow & Row Height Synchronization
    # --------------------------------------------------------------------------
    def test_qatar_banks_table_cell_no_overlap_and_synchronized_row_height(self):
        """Table cells in the Qatar Banks SWIFT table must not overlap adjacent cells,
        and all cells in each row must share the same synchronized height."""
        # 1. Verify terminology translation for INTERNATIONAL BANK OF QATAR
        bank_en = "INTERNATIONAL BANK OF QATAR"
        bank_ar = self.ilt._pre_translate_terminology(bank_en)
        self.assertEqual(bank_ar, "بنك قطر الدولي")
        self.assertNotIn(",", bank_ar)
        self.assertNotIn("،", bank_ar)

        # 2. Build full 17-row Qatar Banks Swift Code table
        # Table geometry matching page 6:
        # Col 0 (Name of Bank): x=56.0, x2=215.0 (w=159.0)
        # Col 1 (Swift Code): x=220.0, x2=370.0 (w=150.0)
        # Col 2 (Bank Identifier): x=375.0, x2=476.0 (w=101.0)
        from babeldoc.format.pdf.document_il import Cropbox, Page
        page = Page(page_number=0, cropbox=Cropbox(box=Box(x=0.0, y=0.0, x2=595.0, y2=842.0)))
        page.pdf_paragraph = []

        cur_y = 450.0
        row_height = 14.0
        row_cells_map = []

        for b_name, s_code, b_id in QATAR_BANKS_SWIFT_FIXTURE:
            tr_bname = self.ilt._pre_translate_terminology(b_name)
            p_name = PdfParagraph(
                layout_label="table_cell",
                box=Box(x=56.0, y=cur_y, x2=215.0, y2=cur_y + row_height),
                unicode=tr_bname,
            )
            p_swift = PdfParagraph(
                layout_label="table_cell",
                box=Box(x=220.0, y=cur_y, x2=370.0, y2=cur_y + row_height),
                unicode=s_code,
            )
            p_id = PdfParagraph(
                layout_label="table_cell",
                box=Box(x=375.0, y=cur_y, x2=476.0, y2=cur_y + row_height),
                unicode=b_id,
            )
            page.pdf_paragraph.extend([p_name, p_swift, p_id])
            row_cells_map.append((p_name, p_swift, p_id))
            cur_y -= (row_height + 4.0)

        # 3. Render page through Typesetting
        fonts = {"base": self.base_font}
        self.typesetting.render_page(page)

        # 4. Assertions across all 17 rows
        for row_idx, (p_name, p_swift, p_id) in enumerate(row_cells_map):
            with self.subTest(row=row_idx, bank=p_name.unicode):
                # Assertion A: No cell box overlap
                self.assertLessEqual(
                    p_name.box.x2,
                    p_swift.box.x + 0.1,
                    f"Row {row_idx}: Bank cell x2 ({p_name.box.x2}) overlaps SWIFT cell x ({p_swift.box.x})"
                )
                self.assertLessEqual(
                    p_swift.box.x2,
                    p_id.box.x + 0.1,
                    f"Row {row_idx}: SWIFT cell x2 ({p_swift.box.x2}) overlaps Bank ID cell x ({p_id.box.x})"
                )

                # Assertion B: Synchronized row height
                self.assertAlmostEqual(
                    p_name.box.y,
                    p_swift.box.y,
                    delta=0.5,
                    msg=f"Row {row_idx}: Bank cell y ({p_name.box.y}) != SWIFT cell y ({p_swift.box.y})"
                )
                self.assertAlmostEqual(
                    p_swift.box.y,
                    p_id.box.y,
                    delta=0.5,
                    msg=f"Row {row_idx}: SWIFT cell y ({p_swift.box.y}) != Bank ID cell y ({p_id.box.y})"
                )
                self.assertAlmostEqual(
                    p_name.box.y2,
                    p_swift.box.y2,
                    delta=0.5,
                    msg=f"Row {row_idx}: Bank cell y2 ({p_name.box.y2}) != SWIFT cell y2 ({p_swift.box.y2})"
                )
                self.assertAlmostEqual(
                    p_swift.box.y2,
                    p_id.box.y2,
                    delta=0.5,
                    msg=f"Row {row_idx}: SWIFT cell y2 ({p_swift.box.y2}) != Bank ID cell y2 ({p_id.box.y2})"
                )

                # Assertion C: Rendered characters in p_name must NOT exceed p_swift.box.x
                if p_name.pdf_paragraph_composition:
                    for comp in p_name.pdf_paragraph_composition:
                        if comp.pdf_character and comp.pdf_character.box:
                            self.assertLessEqual(
                                comp.pdf_character.box.x2,
                                p_swift.box.x + 0.5,
                                f"Row {row_idx}: Rendered character '{comp.pdf_character.char_unicode}' "
                                f"at x2={comp.pdf_character.box.x2} spilled into SWIFT column at x={p_swift.box.x}"
                            )

    # --------------------------------------------------------------------------
    # Bug Group 10: Zero .notdef (tofu) Glyph Usage in BBAN Section
    # --------------------------------------------------------------------------
    def test_bban_characteristics_zero_notdef_glyphs(self):
        """Rendered output for the full BBAN characteristics section must contain
        zero .notdef / glyph-0 characters across all compositions."""
        bban_items = [
            "\uf0b7 It has a fixed length (25 Characters for Qatar).",
            "\uf0b7 It consists of two parts:",
            "1) Bank Identifier: The first 4 letters of SWIFT Code (BIC) or Bank Code identified by QCB.",
            "2) Core Customer Account Number: is an account number which identifies the customer's account at a Bank in Qatar. It may have maximum 21 characters (First 4 character of BIC followed by Core Account Number).",
        ]

        from babeldoc.format.pdf.document_il import Cropbox, Page
        page = Page(page_number=1, cropbox=Cropbox(box=Box(x=0.0, y=0.0, x2=595.0, y2=842.0)))
        page.pdf_font = [self.base_font]
        page.pdf_font[0].font_id = self.style.font_id
        page.pdf_paragraph = []

        cur_y = 600.0
        for item in bban_items:
            norm = self.ilt._normalize_arabic_output(item)
            p = PdfParagraph(
                layout_label="bullet_item",
                box=Box(x=56.0, y=cur_y, x2=540.0, y2=cur_y + 18.0),
                unicode=norm,
                pdf_style=self.style,
                xobj_id=-1,
                pdf_paragraph_composition=[
                    PdfParagraphComposition(
                        pdf_same_style_unicode_characters=PdfSameStyleUnicodeCharacters(
                            unicode=norm,
                            pdf_style=self.style,
                        )
                    )
                ],
            )
            page.pdf_paragraph.append(p)
            cur_y -= 25.0

        fonts = {"base": self.base_font}
        self.typesetting.render_page(page)

        # Audit every character in every paragraph's composition
        for p_idx, p in enumerate(page.pdf_paragraph):
            self.assertTrue(
                p.pdf_paragraph_composition,
                f"BBAN item {p_idx} has empty composition"
            )
            for comp in p.pdf_paragraph_composition:
                if comp.pdf_character:
                    ch = comp.pdf_character.char_unicode
                    gid = comp.pdf_character.pdf_character_id
                    if ch and not ch.isspace():
                        self.assertNotEqual(
                            gid,
                            0,
                            f"BBAN item {p_idx} produced .notdef (glyph 0) for character '{ch}' (U+{ord(ch):04X})"
                        )

    # --------------------------------------------------------------------------
    # Bug Group 11: Mixed-Script Inline BiDi Word-Order in Table Cells & Bullets
    # --------------------------------------------------------------------------
    def test_mixed_script_bidi_ordering_in_table_cell_and_bullets(self):
        """Mixed English/Arabic inline text in table cells and bullets must render
        in strict per-line reading order, matching body paragraphs without reversed parentheticals."""
        test_phrases = [
            ("bullet_item", "\uf0b7 وله طول ثابت (25 Characters for Qatar)."),
            ("bullet_item", "2) رقم الحساب: قد يحتوي على (First 4 character of BIC) متبوعة برقم الحساب."),
            ("table_cell", "رمز البنك: (First 4 character of BIC)"),
        ]

        from babeldoc.format.pdf.document_il import Cropbox, Page
        page = Page(page_number=1, cropbox=Cropbox(box=Box(x=0.0, y=0.0, x2=595.0, y2=842.0)))
        page.pdf_font = [self.base_font]
        page.pdf_font[0].font_id = self.style.font_id
        page.pdf_paragraph = []

        cur_y = 500.0
        for label, phrase in test_phrases:
            norm = self.ilt._normalize_arabic_output(phrase)
            p = PdfParagraph(
                layout_label=label,
                box=Box(x=56.0, y=cur_y, x2=540.0, y2=cur_y + 20.0),
                unicode=norm,
                pdf_style=self.style,
                xobj_id=-1,
                pdf_paragraph_composition=[
                    PdfParagraphComposition(
                        pdf_same_style_unicode_characters=PdfSameStyleUnicodeCharacters(
                            unicode=norm,
                            pdf_style=self.style,
                        )
                    )
                ],
            )
            page.pdf_paragraph.append(p)
            cur_y -= 30.0

        fonts = {"base": self.base_font}
        self.typesetting.render_page(page)

        for p_idx, (label, orig_phrase) in enumerate(test_phrases):
            p = page.pdf_paragraph[p_idx]
            self.assertTrue(p.pdf_paragraph_composition, f"Empty composition for {label}")

            # Collect rendered character stream
            rendered_chars = [
                comp.pdf_character.char_unicode
                for comp in p.pdf_paragraph_composition
                if comp.pdf_character and comp.pdf_character.char_unicode
            ]
            rendered_str = "".join(rendered_chars)

            # In the rendered visual stream, English sequences inside parentheses must retain LTR order
            if "First 4 character of BIC" in orig_phrase:
                self.assertIn("First", rendered_str)
                self.assertIn("BIC", rendered_str)
                idx_first = rendered_str.index("First")
                idx_bic = rendered_str.index("BIC")
                self.assertLess(
                    idx_first,
                    idx_bic,
                    f"English phrase was reversed inside {label}: 'First' at {idx_first} >= 'BIC' at {idx_bic}"
                )

            if "25 Characters for Qatar" in orig_phrase:
                self.assertIn("25", rendered_str)
                self.assertIn("Qatar", rendered_str)
                idx_25 = rendered_str.index("25")
                idx_qatar = rendered_str.index("Qatar")
                self.assertLess(
                    idx_25,
                    idx_qatar,
                    f"Number jumped after phrase inside {label}: '25' at {idx_25} >= 'Qatar' at {idx_qatar}"
                )


if __name__ == "__main__":
    print("Running Technical & Banking Document Regression Suite...")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestTechnicalDocumentRegression)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\nALL TECHNICAL & BANKING DOCUMENT REGRESSION TESTS PASSED SUCCESSFULLY!")
        sys.exit(0)
    else:
        print("\nSOME TESTS FAILED!")
        sys.exit(1)
