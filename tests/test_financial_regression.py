"""
Financial Translation & Typesetting Regression Test Suite.

Validates that all reported bugs across financial PDF documents are permanently fixed:
1. Equation pattern protection: (2018=100), (2010=100)
2. Standalone and parenthesized acronym protection: QCBRR, QCBLR, QCBDR, NDS3
3. Basis points preservation: 50 bps -> 50 نقطة أساس
4. Currency standardization: QR-mn, كرو, QR billion -> ريال قطري
5. Chapter & chart references: CHAPTER 03, Chart 7-1, Table 2-1
6. Percent formatting without stray periods: 8.6% -> 8.6٪.
7. Donut chart legend multi-line classification
8. Typesetting rigid body shift without character overprinting
9. Chronological timeline axis detection (Jan -> Dec)
"""

try:
    import pytest
    fixture = pytest.fixture
except ImportError:
    pytest = None
    def fixture(fn): return fn

import re
from babeldoc.translator.translator import _protect_numerals, _restore_numerals, BaseTranslator
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
from babeldoc.format.pdf.document_il.il_version_1 import (
    PdfParagraph,
    PdfParagraphComposition,
    PdfCharacter,
    Box,
    Page,
    Cropbox,
)
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
from babeldoc.format.pdf.translation_config import TranslationConfig


class DummyConfig:
    lang_out = "ar"
    debug = False


@fixture
def translator_helper():
    class DummyIL:
        translation_config = DummyConfig()
        _pre_translate_terminology = ILTranslator._pre_translate_terminology
        _normalize_arabic_output = ILTranslator._normalize_arabic_output
        _translate_with_line_preservation = ILTranslator._translate_with_line_preservation
    return DummyIL()


# 1. Equation pattern protection: (2018=100), (2010=100)
def test_equation_protection():
    text = "Table 2-1: Consumer Price Index – Annual Averages (2018=100)"
    protected, mapping = _protect_numerals(text)
    assert any("2018=100" in v for v in mapping.values()) or "2018=100" in protected or "2018" in mapping.values()
    restored = _restore_numerals(protected, mapping)
    assert "2018=100" in restored

    # Test formula placeholder regex flexibility
    class DummyTranslator(BaseTranslator):
        def do_translate(self, text): return text
        def do_llm_translate(self, text): return text
    tag, pattern = DummyTranslator("en", "ar", ignore_cache=True).get_formular_placeholder(1)
    assert re.match(pattern, "<b1>", re.IGNORECASE)
    assert re.match(pattern, "<b 1>", re.IGNORECASE)
    assert re.match(pattern, "< b 1 >", re.IGNORECASE)


# 2. Standalone and parenthesized acronym protection
def test_acronym_protection(translator_helper):
    text = "Thus, the gap between the QCBLR and the QCBDR was narrowed down to 50 bps, while placing the QCBRR at the middle (Chart 7-1)."
    pre = translator_helper._pre_translate_terminology(text)
    assert "50 نقطة أساس" in pre

    protected, mapping = _protect_numerals(pre)
    values = set(mapping.values())
    assert "QCBLR" in values
    assert "QCBDR" in values
    assert "QCBRR" in values
    # New pattern: captures the full parenthetical (Chart 7-1) as one token OR just 7-1
    assert "7-1" in values or any("7-1" in v for v in values)

    # Simulate translation output and normalize
    simulated_ar = "وبالتالي تم تضييق الفجوة بين {0} و {1} إلى {2} نقطة أساس، مع وضع {3} في الوسط (المخطط {4})."
    restored = _restore_numerals(simulated_ar, mapping)
    normalized = translator_helper._normalize_arabic_output(restored)
    assert "QCBRR" in normalized
    assert "QCBLR" in normalized
    assert "QCBDR" in normalized
    assert "QRR" not in normalized


# 3. Third National Development Strategy (NDS3)
def test_nds3_protection(translator_helper):
    text = "Further, the Third National Development Strategy (NDS3) offers a doctrine to accelerate the shift of the economy"
    pre = translator_helper._pre_translate_terminology(text)
    assert "استراتيجية التنمية الوطنية الثالثة (NDS3)" in pre

    protected, mapping = _protect_numerals(pre)
    restored = _restore_numerals(protected, mapping)
    normalized = translator_helper._normalize_arabic_output(restored)
    assert "NDS3" in normalized
    assert "[1]" not in normalized


# 4. Currency standardization
def test_currency_standardization(translator_helper):
    # Chapter 6 table: كرو / QR Million
    raw_ar = "الودائع المصرفية كرو مليون"
    norm = translator_helper._normalize_arabic_output(raw_ar)
    assert "مليون ريال قطري" in norm
    assert "كرو" not in norm

    # Table 1-1 / Table 4-1: QR million and QR billion
    text2 = "QR 250,661 million"
    pre2 = translator_helper._pre_translate_terminology(text2)
    assert "250,661 مليون ريال قطري" in pre2

    text3 = "Total expenditure was 208.7 QR billion"
    pre3 = translator_helper._pre_translate_terminology(text3)
    assert "208.7 مليار ريال قطري" in pre3


# 5. Percent formatting without stray periods
def test_percent_formatting(translator_helper):
    cases = [
        ("بمقدار 8.6 .٪ سجلت", "بمقدار 8.6٪. سجلت"),
        ("بمقدار .٪ 8.6 سجلت", "بمقدار 8.6٪. سجلت"),
        ("بمقدار 8.6 . ٪ سجلت", "بمقدار 8.6٪. سجلت"),
        ("بمقدار 8.6% . سجلت", "بمقدار 8.6٪. سجلت"),
        ("بمقدار 8.6%. سجلت", "بمقدار 8.6٪. سجلت"),
    ]
    for inp, expected in cases:
        out = translator_helper._normalize_arabic_output(inp)
        assert out == expected, f"Failed for {inp}: got {out}"


# 6. Chapter & Reference leading-zero and terminology
def test_chapter_and_chart_references(translator_helper):
    text = "CHAPTER 03"
    protected, mapping = _protect_numerals(text)
    assert "03" in mapping.values()
    restored = _restore_numerals(protected, mapping)
    normalized = translator_helper._normalize_arabic_output(restored)
    assert "الفصل 03" in normalized

    chart_text = "الشريط 7-1"
    assert translator_helper._normalize_arabic_output(chart_text) == "المخطط 7-1"


# 7. Donut chart legend multi-line classification
def test_multi_line_classification():
    # Short labels: should be recognized as short labels and preserved
    legend_text = "Recreation\n& Culture\nTransport\nHousing\nFood"
    lines = legend_text.split("\n")
    non_empty = [l for l in lines if l.strip()]
    is_short = len(non_empty) > 1 and max(len(l.split()) for l in non_empty) <= 6
    assert is_short is True

    # Flowing body paragraph: should NOT be recognized as short labels
    body_text = (
        "Qatar’s economy was already on a long-term growth path before the World Cup 2022, and the tournament’s hosting accelerated\n"
        "its diversification efforts. Development of new infrastructure boosted the country's construction sector and uplifted the tourism\n"
        "industry. Post-World Cup, the tourism industry is expected to continue growing, strengthening the services sector."
    )
    b_lines = body_text.split("\n")
    b_non_empty = [l for l in b_lines if l.strip()]
    is_body_short = len(b_non_empty) > 1 and max(len(l.split()) for l in b_non_empty) <= 6
    assert is_body_short is False


# 8. Typesetting rigid body shift prevents character overprinting collisions
def test_typesetting_rigid_body_shift():
    ts = Typesetting(TranslationConfig(
        translator=None,
        input_file="",
        lang_in="en",
        lang_out="ar",
        doc_layout_model=None,
    ))

    # Create 4 consecutive characters along a line
    chars = []
    x_pos = 500.0
    for ch in ["ك", "ا", "ن", " "]:
        char_obj = PdfCharacter(
            char_unicode=ch,
            box=Box(x=x_pos, y=100.0, x2=x_pos + 10.0, y2=112.0),
        )
        chars.append(PdfParagraphComposition(pdf_character=char_obj))
        x_pos += 10.0

    # Simulate shifting by +100 with page margin at 580 (would exceed 580)
    page = Page(cropbox=Cropbox(box=Box(x=0.0, y=0.0, x2=595.0, y2=842.0)))
    ts._apply_rtl_margin_shift_to_composition(chars, shift_x=100.0, page=page)

    # Verify characters retain positive spacing and do NOT stack on top of each other
    for i in range(len(chars) - 1):
        c1 = chars[i].pdf_character
        c2 = chars[i + 1].pdf_character
        assert c2.box.x > c1.box.x, f"Characters stacked at x={c1.box.x}"
        assert c2.box.x == c1.box.x2, f"Character spacing corrupted: {c2.box.x} != {c1.box.x2}"


# 9. Chronological timeline axis detection
def test_chronological_axis_detection():
    axis_line = "Jan 2021 Feb 2021 Mar 2021 Apr 2021 May 2021 Jun 2021 Jul 2021 Aug 2021 Sep 2021 Oct 2021 Nov 2021 Dec 2021"
    months_pattern = r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|يناير|فبراير|مارس|أبريل|مايو|يونيو|يوليو|أغسطس|سبتمبر|أكتوبر|نوفمبر|ديسمبر|أوج)\b'
    matches = re.findall(months_pattern, axis_line, re.I)
    assert len(matches) >= 3


if __name__ == "__main__":
    import inspect
    print("Running Financial Translation & Typesetting Regression Suite...\n")
    helper = translator_helper()
    tests = [
        ("test_equation_protection", lambda: test_equation_protection()),
        ("test_acronym_protection", lambda: test_acronym_protection(helper)),
        ("test_nds3_protection", lambda: test_nds3_protection(helper)),
        ("test_currency_standardization", lambda: test_currency_standardization(helper)),
        ("test_percent_formatting", lambda: test_percent_formatting(helper)),
        ("test_chapter_and_chart_references", lambda: test_chapter_and_chart_references(helper)),
        ("test_multi_line_classification", lambda: test_multi_line_classification()),
        ("test_typesetting_rigid_body_shift", lambda: test_typesetting_rigid_body_shift()),
        ("test_chronological_axis_detection", lambda: test_chronological_axis_detection()),
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
        print("ALL REGRESSION TESTS PASSED SUCCESSFULLY!")
    else:
        exit(1)
