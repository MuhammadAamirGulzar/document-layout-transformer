"""
Tests for RTL Formatting Enhancements:
- Item 2: Bullet Points & Hanging Indents
- Item 4: Running Headers, Footers & Page Numbers
- Item 6: Punctuation BiDi Locking (RLM \u200F)
- Item 7: Chart Legend Swatch & Label Alignment
"""

import sys
import re
from pathlib import Path

from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.format.pdf.document_il.il_version_1 import (
    Box,
    Cropbox,
    Page,
    PdfParagraph,
    PdfParagraphComposition,
    PdfSameStyleUnicodeCharacters,
    PdfStyle,
    PdfCharacter,
    Document,
)
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting, TypesettingUnit
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator


class DummyTranslator:
    def __init__(self, lang_in="en", lang_out="ar", ignore_cache=True):
        self.lang_in = lang_in
        self.lang_out = lang_out
        self.ignore_cache = ignore_cache

    def translate(self, text, rate_limit_params=None):
        return text

    def get_formular_placeholder(self, pid):
        return f"<b{pid}>", rf"<\s*b\s*{pid}\s*>"


def test_punctuation_bidi_locking():
    """Verify trailing punctuation after digits, percentages, or acronyms receives RLM (\\u200F)."""
    cfg = TranslationConfig(
        translator=None,
        input_file="",
        lang_in="en",
        lang_out="ar",
        doc_layout_model=None,
    )
    ilt = ILTranslator(DummyTranslator(), cfg)

    # 1. Trailing period after Arabic percent sign
    raw_1 = "بلغ معدل التضخم 8.6٪."
    norm_1 = ilt._normalize_arabic_output(raw_1)
    assert norm_1.endswith(".\u200F"), f"Expected RLM after period in [{norm_1}]"

    # 2. Trailing period after parenthetical acronym
    raw_2 = "تم رفع سعر الفائدة من قبل المصرف المركزي (QCBRR)."
    norm_2 = ilt._normalize_arabic_output(raw_2)
    assert norm_2.endswith(".\u200F"), f"Expected RLM after period in [{norm_2}]"

    # 3. Trailing colon after year/digits
    raw_3 = "المتوسط السنوي لعام 2022:"
    norm_3 = ilt._normalize_arabic_output(raw_3)
    assert norm_3.endswith(":\u200F"), f"Expected RLM after colon in [{norm_3}]"


def test_header_footer_mirroring():
    """Verify running headers, footers, and page numbers mirror across page midline."""
    cfg = TranslationConfig(
        translator=DummyTranslator(),
        input_file="",
        lang_in="en",
        lang_out="ar",
        doc_layout_model=None,
    )
    ts = Typesetting(cfg)

    # Page width 612, height 792 (Standard US Letter)
    page_box = Box(x=0.0, y=0.0, x2=612.0, y2=792.0)
    page = Page(cropbox=Cropbox(box=page_box))

    # Top-left header in English: x: 50.0, x2: 250.0, y: 750.0, y2: 770.0
    header_para = PdfParagraph(
        layout_label="page_header",
        box=Box(x=50.0, y=750.0, x2=250.0, y2=770.0),
    )

    # Top-right page number in English: x: 550.0, x2: 580.0, y: 750.0, y2: 770.0
    page_num_para = PdfParagraph(
        layout_label="page_number",
        box=Box(x=550.0, y=750.0, x2=580.0, y2=770.0),
    )

    # Verify detection
    assert ts._is_header_or_footer(header_para, page) is True
    assert ts._is_header_or_footer(page_num_para, page) is True
    assert ts._should_skip_margin_mirroring(header_para, page) is False
    assert ts._should_skip_margin_mirroring(page_num_para, page) is False

    # Header shift: orig_right_margin = 612 - 250 = 362. new_x = 0 + 362 = 362. shift = 362 - 50 = +312
    shift_header = ts._calculate_rtl_margin_shift(header_para, page)
    assert shift_header == 312.0, f"Expected shift 312.0, got {shift_header}"
    new_header_x = header_para.box.x + shift_header
    new_header_x2 = header_para.box.x2 + shift_header
    assert new_header_x == 362.0 and new_header_x2 == 562.0

    # Page number shift: orig_right_margin = 612 - 580 = 32. new_x = 0 + 32 = 32. shift = 32 - 550 = -518
    shift_page_num = ts._calculate_rtl_margin_shift(page_num_para, page)
    assert shift_page_num == -518.0, f"Expected shift -518.0, got {shift_page_num}"
    new_pn_x = page_num_para.box.x + shift_page_num
    new_pn_x2 = page_num_para.box.x2 + shift_page_num
    assert new_pn_x == 32.0 and new_pn_x2 == 62.0


def test_bullet_point_hanging_indent():
    """Verify bullet points wrap with a clean hanging indent on lines 1+."""
    cfg = TranslationConfig(
        translator=DummyTranslator(),
        input_file="",
        lang_in="en",
        lang_out="ar",
        doc_layout_model=None,
    )
    ts = Typesetting(cfg)

    # Create typesetting units for a bulleted sentence that spans multiple lines
    bullet_text = "• قرر مصرف قطر المركزي رفع أسعار الفائدة بمقدار 50 نقطة أساس لدعم الاستقرار النقدي والمالي في الدولة."
    style = PdfStyle(font_id="F1", font_size=12.0)
    
    font = ts.font_mapper.base_font
    units = [
        TypesettingUnit(
            unicode=ch,
            font=font,
            font_size=12.0,
            style=style,
            xobj_id=-1,
        )
        for ch in bullet_text
    ]

    # Constrain bounding box width to force wrapping into 2+ lines
    box = Box(x=50.0, y=100.0, x2=250.0, y2=200.0)
    para = PdfParagraph(layout_label="list_item", box=box)

    laid_out_units, fits = ts._layout_typesetting_units(
        typesetting_units=units,
        box=box,
        scale=1.0,
        line_skip=1.4,
        paragraph=para,
    )

    # Collect units grouped by line_id
    lines_by_id = {}
    for u in laid_out_units:
        lid = getattr(u, "line_id", 0)
        lines_by_id.setdefault(lid, []).append(u)

    assert len(lines_by_id) >= 2, f"Expected at least 2 lines, got {len(lines_by_id)}"

    line_0 = lines_by_id[0]
    line_1 = lines_by_id[1]

    # Line 0 rightmost edge should be near box.x2 (250.0)
    line_0_right = max(u.box.x2 for u in line_0 if u.box)
    assert abs(line_0_right - box.x2) < 2.0, f"Line 0 should be flush with right margin: {line_0_right} vs {box.x2}"

    # Line 1 rightmost edge MUST be indented inward (hanging indent)
    line_1_right = max(u.box.x2 for u in line_1 if u.box)
    assert line_1_right < line_0_right - 10.0, (
        f"Line 1 should be indented inward by hanging indent: line 1 right={line_1_right}, line 0 right={line_0_right}"
    )


def test_legend_swatch_placement():
    """Verify chart legend swatches render on the right edge in RTL."""
    import bidi.algorithm as ba

    # A legend line in Arabic with swatch symbol
    legend_str = "■ النفط والغاز"
    storage = ba.get_empty_storage()
    storage['base_level'] = 1
    storage['base_dir'] = 'R'
    ba.get_embedding_levels(legend_str, storage, False, False)
    for i, c in enumerate(storage['chars']):
        c['orig_idx'] = i
    ba.explicit_embed_and_overrides(storage, False)
    ba.resolve_weak_types(storage, False)
    ba.resolve_neutral_types(storage, False)
    ba.resolve_implicit_levels(storage, False)
    ba.reorder_resolved_levels(storage, False)
    ba.apply_mirroring(storage, False)
    visual_chars = [c['ch'] for c in storage['chars']]
    visual_str = ''.join(visual_chars)

    # In LTR visual rendering order, the swatch '■' should be at the visual end (rightmost in LTR placement)
    assert visual_str[-1] == "■", f"Expected swatch '■' at rightmost position in visual order: [{visual_str}]"


def test_overflow_paragraph_expansion_and_composition():
    """Verify paragraph with a 1-line bounding box whose translation wraps to 2+ lines
    expands box.y, populates pdf_paragraph_composition, and renders with zero drops.
    """
    import unittest.mock as mock
    import logging
    from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
    from babeldoc.format.pdf.document_il.backend.pdf_creater import PDFCreater

    cfg = TranslationConfig(
        translator=DummyTranslator(),
        input_file="",
        lang_in="en",
        lang_out="ar",
        doc_layout_model=None,
    )
    ts = Typesetting(cfg)

    # Standard page box: height 792, width 612
    page_box = Box(x=0.0, y=0.0, x2=612.0, y2=792.0)
    page = Page(cropbox=Cropbox(box=page_box))

    # A 1-line bounding box: height 15pt (y=700 to y2=715), narrow width 150pt
    para_box = Box(x=50.0, y=700.0, x2=200.0, y2=715.0)
    
    # Arabic title text that requires multiple lines to fit 150pt width
    text = "استراتيجية التنمية الوطنية الثالثة لدولة قطر والسياسة النقدية للمصرف المركزي"
    style = PdfStyle(font_id="base", font_size=12.0)

    paragraph = PdfParagraph(
        layout_label="title",
        box=para_box,
        unicode=text,
        debug_id=42,
        pdf_style=style,
    )
    page.pdf_paragraph.append(paragraph)

    # Build typesetting units for each character
    units = []
    for ch in text:
        w = 4.0 if ch == " " else 10.0
        c = PdfCharacter(
            char_unicode=ch,
            box=Box(x=0.0, y=0.0, x2=w, y2=12.0),
            pdf_style=style,
        )
        units.append(TypesettingUnit(char=c))

    # Typeset the paragraph using precomputed scale logic
    scale, final_units = ts._find_optimal_scale_and_layout(
        paragraph, page, units, initial_scale=1.0, apply_layout=True
    )

    # 1. Assert paragraph composition is non-empty
    assert len(paragraph.pdf_paragraph_composition) > 0, "pdf_paragraph_composition was left empty!"
    # 2. Assert paragraph box expanded downwards to accommodate wrapped lines
    assert paragraph.box.y < 700.0, f"paragraph.box.y was not expanded: {paragraph.box.y}"

    # 3. Verify PDFCreater renders the paragraph without dropping it or logging errors
    creater = PDFCreater("", Document(page=[page]), cfg, {})
    with mock.patch.object(logging.getLogger("babeldoc.format.pdf.document_il.backend.pdf_creater"), "error") as mock_error:
        chars = creater.render_paragraph_to_char(paragraph)
        assert len(chars) > 0, "PDFCreater returned zero characters!"
        for call in mock_error.call_args_list:
            assert "Unable to export paragraphs" not in str(call), f"Found dropped paragraph error: {call}"


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print("Running Advanced RTL Formatting Suite...\n")
    tests = [
        ("test_punctuation_bidi_locking", test_punctuation_bidi_locking),
        ("test_header_footer_mirroring", test_header_footer_mirroring),
        ("test_bullet_point_hanging_indent", test_bullet_point_hanging_indent),
        ("test_legend_swatch_placement", test_legend_swatch_placement),
        ("test_overflow_paragraph_expansion_and_composition", test_overflow_paragraph_expansion_and_composition),
    ]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS: {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {name}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\nResults: {passed}/{len(tests)} tests passed.")
    if passed == len(tests):
        print("ALL ADVANCED FORMATTING TESTS PASSED SUCCESSFULLY!")
    else:
        sys.exit(1)
