"""M1b: glyph protection, fallback precision, variation selectors, and substitution accounting."""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from babeldoc.format.pdf.document_il.il_version_1 import (
    Box,
    Cropbox,
    Document,
    Page,
    PdfCharacter,
    PdfFont,
    PdfParagraph,
    PdfParagraphComposition,
    PdfSameStyleUnicodeCharacters,
    PdfStyle,
)
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting, TypesettingUnit
from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
from babeldoc.format.pdf.translation_config import (
    SharedContextCrossSplitPart,
    TranslationConfig,
)


def _make_config(with_ledger: bool = False) -> TranslationConfig:
    cfg = TranslationConfig(
        translator=None,
        input_file="",
        lang_in="en",
        lang_out="ar",
        doc_layout_model=None,
    )
    if with_ledger:
        shared = SharedContextCrossSplitPart()
        shared.content_ledger = ContentLedger()
        cfg.shared_context_cross_split_part = shared
    return cfg


def _make_paragraph(text: str, font_id: str = "F1", debug_id: int = 1) -> PdfParagraph:
    return PdfParagraph(
        debug_id=debug_id,
        box=Box(x=50.0, y=100.0, x2=400.0, y2=140.0),
        xobj_id=-1,
        pdf_style=PdfStyle(font_id=font_id, font_size=10.0),
        pdf_paragraph_composition=[
            PdfParagraphComposition(
                pdf_same_style_unicode_characters=PdfSameStyleUnicodeCharacters(
                    unicode=text,
                    pdf_style=PdfStyle(font_id=font_id, font_size=10.0),
                )
            )
        ],
    )


class _StubFont:
    def __init__(self, font_id: str, supported: dict[int, int]):
        self.font_id = font_id
        self.font_path = None
        self.is_bold = False
        self.is_italic = False
        self._supported = supported

    def has_glyph(self, codepoint: int) -> int:
        return self._supported.get(codepoint, 0)

    def char_lengths(self, text: str, font_size: float) -> list[float]:
        return [font_size * 0.5 for _ in text]


class _StubFontMapper:
    def __init__(self, primary_font: _StubFont, arabic_fonts: list[_StubFont] | None = None):
        self.primary_font = primary_font
        self.arabic_fonts = arabic_fonts or [primary_font]
        self.fontid2font = {primary_font.font_id: primary_font}
        self.fonts = {primary_font.font_id: primary_font}

    def map(self, source_font, value: str):
        if value and self.primary_font.has_glyph(ord(value)):
            return self.primary_font
        return None


def test_fallback_runs_only_on_actual_coverage_miss():
    """Covered glyphs must bypass NFKC and symbol fallback with 0 substitutions."""
    covered_chars = "☐☑✗ﬁ ×"
    stub_font = _StubFont("Stub", {ord(ch): 100 + i for i, ch in enumerate(covered_chars)})
    ts = Typesetting(_make_config())
    ts.font_mapper = _StubFontMapper(stub_font)

    paragraph = _make_paragraph(covered_chars, font_id="F1")
    units = ts.create_typesetting_units(
        paragraph,
        {"F1": PdfFont(font_id="F1", name="Helvetica"), "Stub": stub_font},
    )

    rendered = "".join(unit.unicode for unit in units)
    assert rendered == covered_chars
    assert ts.get_glyph_substitution_summary()["total_substitutions"] == 0


def test_variation_selectors_positive_and_negative_controls():
    """Variation selectors skip standalone rendering without hiding missing bases or neighbors."""
    ts = Typesetting(_make_config())
    fonts = {"F1": PdfFont(font_id="F1", name="Arial"), **ts.font_mapper.fontid2font}

    # Positive: base symbols with VS16 / IVS drop only the selector and render valid glyphs.
    pos_para = _make_paragraph("تحذير ⚠\uFE0F وتأكيد ☑\uFE0F وجذر 一\U000E0100")
    pos_units = ts.create_typesetting_units(pos_para, fonts)
    pos_text = "".join(unit.unicode for unit in pos_units if unit.unicode)
    assert "\u26a0" in pos_text
    assert "\u2713" in pos_text
    assert "\u4e00" in pos_text
    assert "\uFE0F" not in pos_text
    assert "\U000E0100" not in pos_text
    assert all(unit.font.has_glyph(ord(unit.unicode)) > 0 for unit in pos_units if unit.unicode)

    # Negative 1: unsupported base followed by VS16 still triggers unsupported glyph handling
    # (explicit U+FFFD replacement rather than silently dropping base or emitting .notdef).
    neg_base_para = _make_paragraph("رمز \U0001F680\uFE0F")
    neg_base_units = ts.create_typesetting_units(neg_base_para, fonts)
    neg_base_text = "".join(unit.unicode for unit in neg_base_units if unit.unicode)
    assert "\uFFFD" in neg_base_text
    assert "\uFE0F" not in neg_base_text
    assert all(unit.font.has_glyph(ord(unit.unicode)) > 0 for unit in neg_base_units if unit.unicode)

    # Negative 2: boundary neighbors outside variation selector ranges must NOT be skipped.
    assert ts._is_variation_selector(0xFE00) is True
    assert ts._is_variation_selector(0xFE0F) is True
    assert ts._is_variation_selector(0xE0100) is True
    assert ts._is_variation_selector(0xE01EF) is True
    for outside in (0xFDFF, 0xFE10, 0xFE70, 0xE00FF, 0xE01F0):
        assert ts._is_variation_selector(outside) is False

    # Negative 3: direct render of unskipped U+FE0F refuses .notdef (glyph 0).
    vs_unit = TypesettingUnit(
        unicode="\uFE0F",
        font=ts.font_mapper.arabic_fonts[0],
        font_size=10.0,
        style=PdfStyle(font_id="GoNotoKurrent-Regular.ttf", font_size=10.0),
        xobj_id=-1,
    )
    vs_unit.x = 10.0
    vs_unit.y = 20.0
    vs_unit.scale = 1.0
    with pytest.raises(RuntimeError, match="refusing .notdef output"):
        vs_unit.render()


def test_scientific_multiplication_operator_never_replaced_with_cross_glyph():
    """Scientific multiplication operators must never degrade to cross/ballot glyphs."""
    for src, dst in Typesetting._SYMBOL_GLYPH_FALLBACKS.items():
        assert not Typesetting._is_forbidden_operator_substitution(src, dst)
        if src in Typesetting._SCIENTIFIC_PROTECTED_OPERATORS:
            assert dst not in Typesetting._CROSS_GLYPHS

    # 1. Real FontMapper preserves scientific × verbatim with zero substitutions.
    ts = Typesetting(_make_config())
    fonts = {"F1": PdfFont(font_id="F1", name="Times-Roman"), **ts.font_mapper.fontid2font}
    eq_para = _make_paragraph("A × B and C ⋅ D and E ∗ F")
    eq_units = ts.create_typesetting_units(eq_para, fonts)
    eq_text = "".join(unit.unicode for unit in eq_units if unit.unicode)
    assert "\u00d7" in eq_text
    # Dot/asterisk operators fall back to math bullet/asterisk operators, never cross glyphs.
    assert "\u2219" in eq_text
    assert "*" in eq_text
    assert not any(ch in Typesetting._CROSS_GLYPHS for ch in eq_text)

    # 2. Negative control: even if all fonts lack U+00D7, it must NEVER become ✗/✕/❌/☒/x/X.
    cross_only_font = _StubFont(
        "CrossOnly",
        {
            ord("A"): 10,
            ord(" "): 11,
            ord("B"): 12,
            ord("\u2717"): 13,
            ord("\u2715"): 14,
            ord("x"): 15,
            ord("\uFFFD"): 16,
        },
    )
    stripped_ts = Typesetting(_make_config())
    stripped_ts.font_mapper = _StubFontMapper(cross_only_font)
    stripped_units = stripped_ts.create_typesetting_units(
        _make_paragraph("A × B", font_id="F1"),
        {"F1": PdfFont(font_id="F1", name="Times-Roman"), "CrossOnly": cross_only_font},
    )
    stripped_text = "".join(unit.unicode for unit in stripped_units if unit.unicode)
    assert not any(ch in Typesetting._CROSS_GLYPHS for ch in stripped_text)
    assert stripped_text == "A \uFFFD B"


def test_remaining_unsupported_glyphs_avoid_silent_notdef_and_fail_closed():
    """Unsupported visible glyphs use explicit non-.notdef U+FFFD or raise if uncovered."""
    ts = Typesetting(_make_config())
    fonts = {"F1": PdfFont(font_id="F1", name="Arial"), **ts.font_mapper.fontid2font}

    # Visible unsupported emoji uses U+FFFD (with valid non-zero glyph ID) rather than .notdef.
    emoji_para = _make_paragraph("تقرير 🚀 جديد")
    units = ts.create_typesetting_units(emoji_para, fonts)
    assert any(u.unicode == "\uFFFD" for u in units)
    assert all(u.font.has_glyph(ord(u.unicode)) > 0 for u in units if u.unicode)

    # Uncovered Cf format control is skipped safely without .notdef, while covered LRM remains.
    fmt_font = _StubFont("FmtFont", {ord("A"): 10, ord("\u200E"): 11, ord("\uFFFD"): 12})
    fmt_ts = Typesetting(_make_config())
    fmt_ts.font_mapper = _StubFontMapper(fmt_font)
    fmt_units = fmt_ts.create_typesetting_units(
        _make_paragraph("A\u200E\u2060", font_id="F1"),
        {"F1": PdfFont(font_id="F1", name="Arial"), "FmtFont": fmt_font},
    )
    assert "".join(u.unicode for u in fmt_units) == "A\u200E"

    # Negative: when even U+FFFD has no coverage, refuse silent .notdef by raising RuntimeError.
    no_repl_font = _StubFont("Bare", {ord("A"): 10})
    bare_ts = Typesetting(_make_config())
    bare_ts.font_mapper = _StubFontMapper(no_repl_font)
    with pytest.raises(RuntimeError, match="No target-font coverage"):
        bare_ts.create_typesetting_units(
            _make_paragraph("A🚀", font_id="F1"),
            {"F1": PdfFont(font_id="F1", name="Arial"), "Bare": no_repl_font},
        )


def test_substitution_counting_logging_and_quality_report(caplog, tmp_path):
    """Substitutions are counted, logged, and reported in ContentLedger/quality_report.json."""
    cfg = _make_config(with_ledger=True)
    ts = Typesetting(cfg)
    page = Page(page_number=1, cropbox=Cropbox(box=Box(x=0.0, y=0.0, x2=595.0, y2=842.0)))
    page.pdf_font = [PdfFont(font_id="F1", name="Arial")]
    pua_char = PdfCharacter(
        char_unicode="\uF0B7",
        pdf_character_id=10,
        box=Box(x=50.0, y=200.0, x2=58.0, y2=210.0),
        pdf_style=PdfStyle(font_id="F1", font_size=10.0),
        xobj_id=-1,
    )
    para = _make_paragraph("خيارات ☐ ☑ ⋅ ورمز 🚀", font_id="F1", debug_id=42)
    para.pdf_paragraph_composition.insert(0, PdfParagraphComposition(pdf_character=pua_char))
    page.pdf_paragraph = [para]
    doc = Document(page=[page])

    with caplog.at_level(logging.INFO):
        ts.typesetting_document(doc)

    summary = ts.get_glyph_substitution_summary()
    assert summary["total_substitutions"] >= 5
    assert summary["unsupported_replacement_count"] == 1
    assert summary["by_reason"]["symbol_fallback"] >= 3
    assert summary["by_reason"]["pua_bullet"] == 1
    assert summary["by_reason"]["unsupported_replacement"] == 1
    assert "Typesetting glyph substitution summary:" in caplog.text

    report_path = cfg.shared_context_cross_split_part.content_ledger.write_json(
        tmp_path / "quality_report.json",
        input_file="doc.pdf",
        lang_in="en",
        lang_out="ar",
        engine="mock",
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "needs_review"
    assert report["summary"]["unsupported_glyph_count"] == 1
    assert report["summary"]["glyph_substitution_count"] == summary["total_substitutions"]
    assert report["glyph_substitutions"]["total_substitutions"] == summary["total_substitutions"]


def test_format_controls_drive_bidi_and_shaping_without_emitting_visible_glyphs():
    """BiDi/shaping Cf controls must order/shape text during layout and never emit PdfCharacter glyphs."""
    import unicodedata

    ts = Typesetting(_make_config())
    page = Page(
        page_number=1,
        cropbox=Cropbox(box=Box(x=0.0, y=0.0, x2=595.0, y2=842.0)),
        pdf_font=[PdfFont(font_id="F1", name="Times-Roman")],
    )
    fonts = {"F1": PdfFont(font_id="F1", name="Times-Roman"), **ts.font_mapper.fontid2font}

    # 1. LRM (\u200e) and RLM (\u200f) around Latin citations and formulas:
    #    - create_typesetting_units retains \u200e/\u200f with 0 width so BiDi ordering sees them.
    #    - render_paragraph orders the citation/formula LTR and emits zero Cf PdfCharacters.
    lrm_para = _make_paragraph("نتائج \u200e(Smith, 2020; x = 1 + 2)\u200e\u200f مؤكدة.", font_id="F1", debug_id=101)
    lrm_units = ts.create_typesetting_units(lrm_para, fonts, page=page)
    assert any(u.unicode == "\u200e" for u in lrm_units)
    assert all(u.width == 0.0 for u in lrm_units if u.unicode and unicodedata.category(u.unicode) == "Cf")

    ts.render_paragraph(lrm_para, page, fonts)
    rendered_chars = [
        c.pdf_character
        for c in lrm_para.pdf_paragraph_composition
        if c.pdf_character is not None
    ]
    assert rendered_chars
    assert not any(
        ch.char_unicode and unicodedata.category(ch.char_unicode) == "Cf"
        for ch in rendered_chars
    )
    # Verify "Smith" characters progress strictly left-to-right in X coordinate
    smith_xs = [ch.box.x for ch in rendered_chars if ch.char_unicode in ("S", "m", "i", "t", "h")]
    assert len(smith_xs) == 5
    assert smith_xs == sorted(smith_xs)

    # 2. ZWJ (\u200d) and ZWNJ (\u200c) Arabic shaping:
    #    - "ب\u200d" shapes beh to initial form (\uFE91), whereas isolated "ب" is \uFE8F.
    #    - "ب\u200cه" keeps beh isolated (\uFE8F) and heh isolated (\uFEE9), whereas "به" joins (\uFE91\uFEEA).
    shaped_zwj = ts.shape_arabic_text("ب\u200d")
    shaped_iso = ts.shape_arabic_text("ب")
    assert "\uFE91" in shaped_zwj
    assert "\uFE8F" in shaped_iso

    shaped_zwnj = ts.shape_arabic_text("ب\u200cه")
    shaped_joined = ts.shape_arabic_text("به")
    assert "\uFE8F" in shaped_zwnj and "\uFEE9" in shaped_zwnj
    assert "\uFE91" in shaped_joined and "\uFEEA" in shaped_joined

    zwj_para = _make_paragraph("ب\u200d و ب\u200cه", font_id="F1", debug_id=102)
    ts.render_paragraph(zwj_para, page, fonts)
    zwj_rendered = [
        c.pdf_character.char_unicode
        for c in zwj_para.pdf_paragraph_composition
        if c.pdf_character is not None
    ]
    assert "\u200d" not in zwj_rendered and "\u200c" not in zwj_rendered
    assert "\uFE91" in zwj_rendered and "\uFE8F" in zwj_rendered

    # 3. Word joiner (\u2060) and directional isolates (\u2066..\u2069):
    isolate_para = _make_paragraph(
        "المرجع \u2066E/ICEF/2025/P/L.5\u2069 ورمز A\u2060B.",
        font_id="F1",
        debug_id=103,
    )
    iso_units = ts.create_typesetting_units(isolate_para, fonts, page=page)
    a_unit = next(u for u in iso_units if u.unicode == "A")
    assert getattr(a_unit, "no_break_after", False) is True
    ts.render_paragraph(isolate_para, page, fonts)
    iso_chars = [
        c.pdf_character
        for c in isolate_para.pdf_paragraph_composition
        if c.pdf_character is not None
    ]
    assert not any(
        ch.char_unicode and unicodedata.category(ch.char_unicode) == "Cf"
        for ch in iso_chars
    )
    # Verify E, /, I, C, E, F inside the LRI..PDI isolate progress left-to-right
    icef_xs = [ch.box.x for ch in iso_chars if ch.char_unicode in ("I", "C", "F")]
    assert len(icef_xs) == 3 and icef_xs == sorted(icef_xs)

    # 4. Mixed Arabic, numbers, and acronyms:
    mixed_para = _make_paragraph("بلغت ميزانية UNICEF نحو 192,714 دولاراً في عام 2025.", font_id="F1", debug_id=104)
    ts.render_paragraph(mixed_para, page, fonts)
    mixed_chars = [
        c.pdf_character
        for c in mixed_para.pdf_paragraph_composition
        if c.pdf_character is not None
    ]
    unicef_xs = [ch.box.x for ch in mixed_chars if ch.char_unicode in ("U", "N", "I", "C", "E", "F")]
    num_xs = [ch.box.x for ch in mixed_chars if ch.char_unicode in ("1", "9", "7", "4")]
    assert unicef_xs == sorted(unicef_xs)
    assert num_xs == sorted(num_xs)
