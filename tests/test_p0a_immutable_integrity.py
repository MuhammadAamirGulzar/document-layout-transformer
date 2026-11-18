"""P0-A tests: immutable-content integrity with positive and negative controls."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from babeldoc.format.pdf.document_il import (
    Box,
    Page,
    PdfCharacter,
    PdfFormula,
    PdfLine,
    PdfParagraph,
    PdfParagraphComposition,
    PdfStyle,
    VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.il_translator import (
    FormulaPlaceholder,
    ILTranslator,
    normalize_year_over_year,
)
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas
from babeldoc.format.pdf.document_il.utils.layout_semantics import is_standalone_code_text
from babeldoc.translator import translator as tm


def _make_char(text: str, x: float, y: float = 100.0, size: float = 10.0) -> PdfCharacter:
    box = Box(x=x, y=y, x2=x + 6.0, y2=y + size)
    return PdfCharacter(
        char_unicode=text,
        box=box,
        visual_bbox=VisualBbox(box=box),
        pdf_style=PdfStyle(font_id="base", font_size=size),
    )


def _dummy_nllb(monkeypatch, first: str, retry: str | None = None):
    cache = Mock()
    cache.get.return_value = None
    monkeypatch.setattr(tm, "TranslationCache", lambda *args, **kwargs: cache)
    monkeypatch.setattr(tm._translate_rate_limiter, "wait", lambda: None)
    engine = tm.NLLBTranslator("en", "ar", model_name="nllb")
    monkeypatch.setattr(engine, "do_translate", Mock(return_value=first))
    if retry is not None:
        monkeypatch.setattr(engine, "_generate_translation", Mock(return_value=retry))
    return engine, cache


def test_multi_hyphen_ids_paths_filenames_uuids_and_negative_controls():
    src = (
        "Track MCA-TS-0417 and INV-2026-00417 at /srv/reports/2026/budget-q1.xlsx "
        "with notes_2026-03.txt, transdoc_final-v2.pdf, UUID 550e8400-e29b-41d4-a716-446655440000, "
        "and sha256:0123456789abcdef0123456789abcdef for well-known LONG-TERM-CARE cases."
    )
    protected, mapping = tm._protect_numerals(src)
    vals = list(mapping.values())
    assert "MCA-TS-0417" in vals
    assert "INV-2026-00417" in vals
    assert "/srv/reports/2026/budget-q1.xlsx" in vals
    assert "notes_2026-03.txt" in vals
    assert "transdoc_final-v2.pdf" in vals
    assert "550e8400-e29b-41d4-a716-446655440000" in vals
    assert "sha256:0123456789abcdef0123456789abcdef" in vals
    assert "well-known" in protected
    assert "LONG-TERM-CARE" in protected
    assert tm._restore_numerals(protected, mapping) == src

    assert tm._is_pure_passthrough("MCA-TS-0417") is True
    assert tm._is_pure_passthrough("/srv/reports/2026/budget-q1.xlsx") is True
    assert tm._is_pure_passthrough("transdoc_final-v2.pdf") is True
    assert tm._is_pure_passthrough("well-known") is False
    assert tm._is_pure_passthrough("LONG-TERM-CARE") is False


def test_pvalues_intervals_and_un_symbol_stay_intact_without_b1_splits():
    sf = object.__new__(StylesAndFormulas)
    # p < 0.001 and (-0.7, -0.1) fragments must stay translatable plain text, not opaque <b1>.
    for fragment in ("< 0", "\u22120", "-0.7"):
        formula = PdfFormula(
            pdf_character=[_make_char(ch, i * 5) for i, ch in enumerate(fragment)],
            x_offset=0,
            y_offset=0,
            x_advance=20,
        )
        assert sf.is_translatable_formula(formula) is True

    stat_src = "Significance p < 0.001 with 95% CI (-0.7, -0.1) and (−0.7, −0.1)."
    prot, mapping = tm._protect_numerals(stat_src)
    vals = list(mapping.values())
    assert "p < 0.001" in vals
    assert "(-0.7, -0.1)" in vals
    assert "(−0.7, −0.1)" in vals
    assert tm._restore_numerals(prot, mapping) == stat_src

    # Verify E/ICEF/2025/P/L.5 does not mark '/ICEF/...' as a superscript corner mark.
    chars = [_make_char("E", 10, size=20.3)]
    for idx, ch in enumerate("/ICEF/2025/P/L.5", 1):
        chars.append(_make_char(ch, 10 + idx * 5, size=9.96))
    comp = PdfParagraphComposition(pdf_line=PdfLine(pdf_character=chars))
    sf.font_mapper = SimpleNamespace(has_char=lambda c: True)
    sf.translation_config = SimpleNamespace(formular_char_pattern=None)
    tagged, _ = sf._classify_characters_in_composition(comp, set(), False, 0)
    assert all(not corner for _, _, corner in tagged)


def test_leading_bracket_citation_placeholder_recovery_and_inline_formula_guard():
    ph = FormulaPlaceholder(
        1,
        PdfFormula(pdf_character=[PdfCharacter(char_unicode="[3]")], is_corner_mark=False),
        "<b1>",
        r"<\s*b\s*1\s*>",
    )
    src = ILTranslator.TranslateInput("<b1> Based on the 2024 survey.", [ph])
    recovered = ILTranslator._recover_and_clean_formula_placeholders(
        src, "استناداً إلى مسح 2024."
    )
    assert recovered.startswith("<b1> ")

    # Inline math placeholder not in citation/footnote format must NOT be guessed at start.
    math_ph = FormulaPlaceholder(
        1,
        PdfFormula(pdf_character=[PdfCharacter(char_unicode="x+y")], is_corner_mark=False),
        "<b1>",
        r"<\s*b\s*1\s*>",
    )
    math_src = ILTranslator.TranslateInput("<b1> denotes the sum.", [math_ph])
    assert ILTranslator._recover_and_clean_formula_placeholders(math_src, "يمثل المجموع.") == "يمثل المجموع."


def test_spaced_and_contiguous_toc_leaders_and_letter_spaced_headings():
    il = object.__new__(ILTranslator)
    il.translate_engine = Mock(model_name="stub", name="stub")
    il.translate_engine.translate.return_value = "مقدمة"
    il.translate_config = None

    spaced = il._translate_with_line_preservation("Introduction . . . . . . . . 14", {})
    assert spaced.startswith("مقدمة ") and spaced.endswith(" 14")
    assert "........" in spaced

    contiguous = il._translate_with_line_preservation("Introduction ........ 14", {})
    assert contiguous == spaced

    collapsed = il._pre_translate_terminology("L E T T E R - S P A C E D HEADING")
    assert "LETTER-SPACED" in collapsed
    assert il._pre_translate_terminology("L. Okafor-Reyes attended U S A") == "L. Okafor-Reyes attended U S A"


def test_code_detection_formats_and_negative_controls():
    assert is_standalone_code_text("SELECT id, name FROM users WHERE active = 1;") is True
    assert is_standalone_code_text('{\n  "status": "ok",\n  "paid": true\n}') is True
    assert is_standalone_code_text("server:\n  host: 127.0.0.1\n  enabled: true") is True
    assert is_standalone_code_text('<div class="card">\n  <span>OK</span>\n</div>') is True
    assert is_standalone_code_text("--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n-old()\n+new()") is True
    assert is_standalone_code_text("$ curl -s https://example.org/api | grep ok") is True
    assert is_standalone_code_text(r"/^[A-Z]{2}-\d{4}$/i") is True

    assert is_standalone_code_text("Email: info@example.com\nPhone: +974 44456371") is False
    assert is_standalone_code_text("1: Open the document\n2: Sign at bottom") is False
    assert is_standalone_code_text("Select from the options below.") is False


def test_scientific_codon_amino_protection_and_prose_negative_control():
    page = Page(
        pdf_paragraph=[
            PdfParagraph(unicode="Table B6: Genetic code codons and amino acids"),
            PdfParagraph(unicode="UUU"),
            PdfParagraph(unicode="AUG"),
            PdfParagraph(unicode="GCC"),
            PdfParagraph(unicode="The start codon AUG encodes Met while GCC encodes Ala."),
        ]
    )
    prose_page = Page(pdf_paragraph=[PdfParagraph(unicode="We met at the fee office, alas it was closed.")])

    il = object.__new__(ILTranslator)
    il.translation_config = SimpleNamespace(
        lang_in="en",
        lang_out="ar",
        min_text_length=2,
        disable_rich_text_translate=True,
        shared_context_cross_split_part=SimpleNamespace(),
        raise_if_cancelled=lambda: None,
    )
    il.support_llm_translate = False
    il.use_as_fallback = False
    il.translate_engine = Mock(model_name="stub", name="stub")
    il.translate_engine.translate.side_effect = lambda s, **kw: (
        s.replace("The start codon", "كودون البدء")
        .replace("encodes", "يشفر")
        .replace("while", "بينما")
        if "{91" in s
        else "التقينا في مكتب الرسوم للأسف"
    )

    from babeldoc.format.pdf.document_il.midend import il_translator as ilt_mod

    cell_p = PdfParagraph(unicode="GCC", layout_label="table_cell")
    assert ilt_mod._paragraph_bio_tokens_active(cell_p, page, "GCC") is True
    sent_p = page.pdf_paragraph[4]
    prot, bmap = ilt_mod._protect_bio_tokens(sent_p.unicode)
    assert set(bmap.values()) == {"AUG", "Met", "GCC", "Ala"}
    restored = ilt_mod._restore_bio_tokens(
        prot.replace("The start codon", "كودون البدء").replace("encodes", "يشفر"),
        bmap,
    )
    assert all(tok in restored for tok in ("AUG", "Met", "GCC", "Ala"))

    # Negative control: ordinary English page has no genetics context.
    assert ilt_mod._paragraph_bio_tokens_active(prose_page.pdf_paragraph[0], prose_page, prose_page.pdf_paragraph[0].unicode) is False


def test_visual_ltr_arabic_unreversal_and_passthrough():
    pf = object.__new__(ParagraphFinder)
    # Visual LTR order (x ascending) using shaped presentation forms: "مرحبا بكم"
    # Leftmost visually is "بكم" (م final, ك medial, ب initial), rightmost is "مرحبا" (ا final, ب medial, ح medial, ر, م initial)
    visual_welcome = "\ufee2\ufedc\ufe91 \ufe8e\ufe92\ufea3\ufead\ufee3"
    # "الفاتورة رقم 417" visually LTR: "417 " + reversed presentation forms for رقم الفاتورة
    visual_invoice = "417 \ufee1\ufed8\ufead \ufe94\ufead\ufeee\ufe98\ufe8e\ufed4\ufedf\ufe8d"

    line1 = PdfLine(pdf_character=[_make_char(c, i * 6) for i, c in enumerate(visual_welcome)])
    pf._normalize_visual_rtl_runs(line1)
    logical1 = "".join(c.char_unicode for c in line1.pdf_character)
    assert logical1 == "مرحبا بكم"
    assert tm._is_pure_passthrough(logical1) is True

    line2 = PdfLine(pdf_character=[_make_char(c, i * 6) for i, c in enumerate(visual_invoice)])
    pf._normalize_visual_rtl_runs(line2)
    logical2 = "".join(c.char_unicode for c in line2.pdf_character)
    assert logical2 == "الفاتورة رقم 417"
    assert tm._is_pure_passthrough(logical2) is True


def test_degenerate_arabic_hallucination_rejected_and_marked_incomplete(monkeypatch):
    # Heavily vocalized repeating loop
    hallucinated = "إِنَّهُ لَا يُمْكِنُ أَنْ يُمْكِنَ " * 4
    assert tm._is_degenerate_translation("4.205, 4", hallucinated) is True
    assert tm._is_degenerate_translation("Normal sentence", "جملة عربية طبيعية واضحة ومفهومة تماماً.") is False

    engine, cache = _dummy_nllb(monkeypatch, hallucinated, retry=hallucinated)
    out = engine.translate("Sample rate is 4.2%.")
    assert engine.incomplete_count == 1
    assert "4.2%" in out
    assert out == "Sample rate is 4.2%."
    cache.set.assert_not_called()


def test_url_is_reassembled_and_kept_out_of_formula_and_arabic_rewrites():
    url = "https://example.org/data/?option=com_content&view=article&id=1470&name=A%20B"
    broken = url.replace("com_content", "com_\ncontent")
    il = object.__new__(ILTranslator)
    il.translation_config = SimpleNamespace(lang_out="ar")
    assert il._pre_translate_terminology(f"See {broken}, then continue.") == f"See {url}, then continue."
    assert il._pre_translate_terminology("See https://example.org/ for details.") == "See https://example.org/ for details."
    path = "https://example.org/Fax/Allbanks"
    assert il._pre_translate_terminology(f"Read {path}") == f"Read {path}"
    output = il._normalize_arabic_output(f"راجع {url} ثم قارن 15%")
    assert f"\u200e{url}\u200e" in output
    assert "15٪" in output

    compositions = [
        PdfParagraphComposition(pdf_line=PdfLine(pdf_character=[
            _make_char(ch, i * 6, y=200 - 15 * row) for i, ch in enumerate(line)
        ]))
        for row, line in enumerate(broken.splitlines())
    ]
    paragraph = PdfParagraph(pdf_paragraph_composition=compositions)
    styles = object.__new__(StylesAndFormulas)
    styles.font_mapper = SimpleNamespace(has_char=lambda _: True)
    styles.translation_config = SimpleNamespace(formular_char_pattern=None, formular_font_pattern=None)
    styles.process_page_formulas(Page(pdf_paragraph=[paragraph]))
    assert all(comp.pdf_line for comp in paragraph.pdf_paragraph_composition)


def test_yoy_repair_preserves_names_even_in_financial_paragraphs():
    il = object.__new__(ILTranslator)
    il.translation_config = SimpleNamespace(lang_out="ar")
    assert il._normalize_arabic_output("التقرير في نيويورك") == "التقرير في نيويورك"
    assert normalize_year_over_year("New York", "نيو يورك") == "نيو يورك"
    assert normalize_year_over_year("YoY in New York", "يو يو في نيويورك") == "Y-o-Y في نيويورك"


def test_heading_words_are_not_bics_but_acronyms_and_explicit_bank_codes_survive():
    heading = "CLIMATE, ENERGY AND ENVIRONMENT (CEE) CONTRIBUTE TO THE INCREASED FREQUENCY."
    encoded, mapping = tm._protect_numerals(heading)
    assert "ENVIRONMENT" in encoded
    assert not tm._is_pure_passthrough("ENVIRONMENT")
    assert "(CEE)" in mapping.values()
    assert tm._restore_numerals(encoded, mapping) == heading
    assert "DEUTDEFFABC" in tm._protect_numerals("SWIFT BIC: DEUTDEFFABC")[1].values()
    assert tm._protect_numerals("(NOTE)") == ("(NOTE)", {})


def test_chemical_reaction_survives_or_uses_reviewable_source_fallback(monkeypatch):
    formula = "6 CO₂ + 6 H₂O → C₆H₁₂O₆ + 6 O₂"
    text = f"The reaction is {formula}. Calcium forms Ca²⁺ ions."
    protected, values = tm._protect_numerals(text)
    assert {formula, "Ca²⁺"} <= set(values.values())
    assert tm._restore_numerals(protected, values) == text
    assert tm._protect_numerals("OPEN → CLOSE") == ("OPEN → CLOSE", {})
    assert "Mg2+" in tm._protect_numerals("Magnesium forms Mg2+ ions.")[1].values()

    wrong = "لا توجد أسباب إضافية للاستمرار في إدارة المواد الغذائية (6، 6، 6، 6، 2)."
    engine, cache = _dummy_nllb(monkeypatch, wrong, retry=wrong)
    assert engine.translate(text) == text
    assert engine.incomplete_count == 1
    cache.set.assert_not_called()
