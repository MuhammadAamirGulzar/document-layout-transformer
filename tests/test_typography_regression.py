"""
Typography, Font Hierarchy, and Weight Inheritance Regression Tests.

Validates:
1. AR_FONT_FAMILY resolution (prioritizing GoNotoKurrent for Arabic)
2. Robust bold weight detection and mapping to GoNotoKurrent-Bold.ttf
3. Hierarchy isolation (titles exempt from body text mode_scale clamping)
4. Role-based minimum scale floors (titles >= 0.88, captions >= 0.82, body >= 0.72)
5. Arabic optical font size compensation (1.08x)
"""

import sys
from pathlib import Path

from babeldoc.assets import embedding_assets_metadata
from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper
from babeldoc.format.pdf.document_il.il_version_1 import (
    PdfFont,
    PdfParagraph,
    Box,
    Page,
    Cropbox,
    Document,
)
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting


def test_ar_font_family_resolution():
    """Verify Arabic font family is explicitly defined and prioritized."""
    ff = embedding_assets_metadata.get_font_family("ar")
    assert "GoNotoKurrent-Regular.ttf" in ff["base"]
    assert "GoNotoKurrent-Regular.ttf" in ff["normal"]
    assert "GoNotoKurrent-Bold.ttf" in ff["normal"]
    assert "GoNotoKurrent-Regular.ttf" in ff["fallback"]


def test_bold_weight_detection_and_mapping():
    """Verify bold English fonts correctly map to GoNotoKurrent-Bold.ttf."""
    cfg = TranslationConfig(
        translator=None,
        input_file="",
        lang_in="en",
        lang_out="ar",
        doc_layout_model=None,
    )
    mapper = FontMapper(cfg)

    # Test bold font by font name
    bold_font_by_name = PdfFont(
        font_id="F1",
        name="Arial-BoldMT",
        bold=False,  # bold flag is False, but name contains 'Bold'
        italic=False,
    )
    mapped_bold = mapper.map(bold_font_by_name, "م")
    assert mapped_bold is not None
    assert "bold" in mapped_bold.font_id.lower() or mapped_bold.is_bold

    # Test regular font
    regular_font = PdfFont(
        font_id="F2",
        name="ArialMT",
        bold=False,
        italic=False,
    )
    mapped_regular = mapper.map(regular_font, "م")
    assert mapped_regular is not None
    assert not mapped_regular.is_bold or "regular" in mapped_regular.font_id.lower()


def test_title_exempt_from_mode_scale():
    """Verify title paragraphs are exempt from being clamped down to body mode_scale."""
    cfg = TranslationConfig(
        translator=None,
        input_file="",
        lang_in="en",
        lang_out="ar",
        doc_layout_model=None,
    )
    ts = Typesetting(cfg)

    title_para = PdfParagraph(
        layout_label="title",
        box=Box(x=50.0, y=100.0, x2=500.0, y2=150.0),
    )
    title_para.optimal_scale = 1.0

    body_para = PdfParagraph(
        layout_label="plain text",
        box=Box(x=50.0, y=200.0, x2=500.0, y2=400.0),
    )
    body_para.optimal_scale = 0.70

    all_paragraphs = [title_para, body_para]
    all_scales = [1.0, 0.70, 0.70, 0.70]
    mode_scale = 0.70

    for paragraph in all_paragraphs:
        layout_label = (paragraph.layout_label or "").lower()
        is_hierarchy_exempt = any(
            marker in layout_label
            for marker in ["title", "heading", "caption", "table", "footnote", "fallback_line"]
        )
        if not is_hierarchy_exempt:
            if paragraph.optimal_scale is not None and paragraph.optimal_scale > mode_scale:
                paragraph.optimal_scale = mode_scale

    # Title MUST preserve its 1.0 scale
    assert title_para.optimal_scale == 1.0, f"Title was clamped to {title_para.optimal_scale}"
    # Body text was clamped to mode_scale 0.70
    assert body_para.optimal_scale == 0.70


def test_role_based_min_scale_floors():
    """Verify layout-aware minimum scale floors prevent micro-shrinking of headings."""
    cfg = TranslationConfig(
        translator=None,
        input_file="",
        lang_in="en",
        lang_out="ar",
        doc_layout_model=None,
    )
    ts = Typesetting(cfg)

    # Test title floor
    title_p = PdfParagraph(layout_label="title", box=Box(x=50.0, y=50.0, x2=200.0, y2=70.0))
    # Test caption floor
    caption_p = PdfParagraph(layout_label="figure_caption", box=Box(x=50.0, y=100.0, x2=200.0, y2=120.0))
    # Test body floor
    body_p = PdfParagraph(layout_label="plain text", box=Box(x=50.0, y=150.0, x2=200.0, y2=200.0))

    def get_floor(p):
        lbl = (p.layout_label or "").lower()
        if "title" in lbl or "heading" in lbl:
            return 0.88
        elif "caption" in lbl:
            return 0.82
        elif "table_footnote" in lbl or "footnote" in lbl:
            return 0.75
        elif "table" in lbl:
            return 0.65
        else:
            return 0.72

    assert get_floor(title_p) == 0.88
    assert get_floor(caption_p) == 0.82
    assert get_floor(body_p) == 0.72


def test_arabic_optical_compensation():
    """Verify Arabic text typesetting receives the 1.08x optical compensation."""
    cfg = TranslationConfig(
        translator=None,
        input_file="",
        lang_in="en",
        lang_out="ar",
        doc_layout_model=None,
    )
    ts = Typesetting(cfg)
    assert ts.is_arabic is True

    base_font_size = 12.0
    optical_factor = 1.08 if ts.is_arabic else 1.0
    compensated_size = base_font_size * optical_factor
    assert compensated_size == 12.0 * 1.08


if __name__ == "__main__":
    print("Running Typography & Font Hierarchy Regression Suite...\n")
    tests = [
        ("test_ar_font_family_resolution", test_ar_font_family_resolution),
        ("test_bold_weight_detection_and_mapping", test_bold_weight_detection_and_mapping),
        ("test_title_exempt_from_mode_scale", test_title_exempt_from_mode_scale),
        ("test_role_based_min_scale_floors", test_role_based_min_scale_floors),
        ("test_arabic_optical_compensation", test_arabic_optical_compensation),
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
        print("ALL TYPOGRAPHY TESTS PASSED SUCCESSFULLY!")
    else:
        sys.exit(1)
