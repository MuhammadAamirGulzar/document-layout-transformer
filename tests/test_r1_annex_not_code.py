"""R1a: ordinary annex/table prose must never be classified as code or pseudocode."""
from __future__ import annotations

from babeldoc.format.pdf.document_il.il_version_1 import (
    Box, Cropbox, Mediabox, Page, PageLayout, PdfCharacter, PdfLine,
    PdfParagraph, PdfParagraphComposition, PdfStyle, VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
from babeldoc.format.pdf.document_il.utils.layout_semantics import is_standalone_code_text
from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
from babeldoc.format.pdf.translation_config import (
    SharedContextCrossSplitPart, TranslationConfig, WatermarkOutputMode,
)


def _cfg() -> TranslationConfig:
    cfg = TranslationConfig(
        translator=None, input_file="", lang_in="en", lang_out="ar",
        doc_layout_model=None, skip_clean=True,
        watermark_output_mode=WatermarkOutputMode.NoWatermark,
    )
    shared = SharedContextCrossSplitPart()
    shared.content_ledger = ContentLedger()
    cfg.shared_context_cross_split_part = shared
    return cfg


def _para(text, box, label="table_cell", debug_id=1):
    style = PdfStyle(font_id="F1", font_size=10.0)
    step = max(2.0, (box.x2 - box.x) / max(len(text), 1))
    chars = []
    for i, ch in enumerate(text):
        cb = Box(box.x + i * step, box.y, min(box.x2, box.x + (i + 1) * step), box.y2)
        chars.append(PdfCharacter(char_unicode=ch, pdf_character_id=ord(ch), box=cb,
                                  visual_bbox=VisualBbox(box=cb), pdf_style=style))
    return PdfParagraph(
        debug_id=debug_id, box=box, xobj_id=-1, unicode=text, layout_label=label,
        pdf_style=style,
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=PdfLine(box=box, pdf_character=chars))],
    )


def _page(layout_class, region, paras):
    return Page(
        page_number=0,
        mediabox=Mediabox(box=Box(0, 0, 595, 842)),
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        page_layout=[PageLayout(id=1, class_name=layout_class, box=region, conf=0.9)],
        pdf_paragraph=paras,
    )


def test_annex_table_with_weak_code_signals_is_not_code():
    """40 prose cells; two weak 'from ...' / trailing-';' lines must not flip the region to code."""
    triggers = {(2, 1): "from a professional", (3, 4): "Planning; Finance;", (5, 2): "and Social Affairs;"}
    paras, n = [], 0
    for r in range(8):
        for c in range(5):
            n += 1
            text = triggers.get((r, c), f"Indicator {r}.{c} measures coverage of services")
            x, y = 40 + c * 105, 650 - r * 60
            paras.append(_para(text, Box(x, y, x + 100, y + 18), debug_id=n))
    page = _page("table", Box(30, 100, 570, 700), paras)
    plist = list(page.pdf_paragraph)
    ParagraphFinder(_cfg())._preserve_pseudocode_regions(page, plist)
    assert len(plist) == 40, "annex table paragraphs were removed as code"
    assert page.pdf_character == []
    assert not any((p.pipeline_trace or {}).get("preserved_code") for p in plist)


def test_real_numbered_pseudocode_is_still_preserved():
    lines = ["1: for i \u2190 1 to n do", "2: x \u2190 x + 1", "3: if x > 3 then", "4: return x"]
    paras = [_para(t, Box(40, 230 - 20 * i, 300, 245 - 20 * i), label="plain text", debug_id=i + 1)
             for i, t in enumerate(lines)]
    page = _page("algorithm", Box(35, 150, 340, 260), paras)
    plist = list(page.pdf_paragraph)
    ParagraphFinder(_cfg())._preserve_pseudocode_regions(page, plist)
    assert plist == []
    assert "\u2190" in "".join(c.char_unicode for c in page.pdf_character)


def test_wrapped_prose_cell_starting_a_line_with_from_is_not_standalone_code():
    cell = "any sexual violence\nand sought help\nfrom a professional"
    assert is_standalone_code_text(cell, "table_cell") is False


def test_semicolon_lists_and_identifiers_are_not_standalone_code():
    assert is_standalone_code_text("Planning; Finance;\nand Construction,\nPublic Works;", "table_cell") is False
    assert is_standalone_code_text("AZ-104 certification overview", "table_cell") is False
    assert is_standalone_code_text("RR; OR;\nTotal", "wired_table_cell") is False


def test_space_grouped_budget_totals_match_numeric_pattern():
    from babeldoc.format.pdf.document_il.utils.paragraph_helper import NUMERIC_PATTERN

    for val in ("7 710", "185 004", "192 714", "24 840", "1,234,567.89", "-42.5"):
        assert NUMERIC_PATTERN.match(val), f"Expected NUMERIC_PATTERN to match {val!r}"
    for non_num in ("41-44", "2025-2029", "Total 7 710"):
        assert not NUMERIC_PATTERN.match(non_num)


def test_mid_page_table_footnote_does_not_raise_page_bottom_body_floor():
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting

    ts = Typesetting(_cfg())
    mid_table_fn = _para(
        "a Other resources are indicative.",
        Box(40, 385, 500, 398),
        label="table_footnote",
        debug_id=1,
    )
    below_prose = _para(
        "41. The country programme management plan ensures efficient resource use.",
        Box(40, 220, 500, 260),
        label="plain text",
        debug_id=2,
    )
    page = _page("table", Box(35, 380, 560, 720), [mid_table_fn, below_prose])
    zones = ts._get_page_vertical_zones(page)
    assert zones["has_footnotes"] == 0.0
    assert zones["body_floor"] < 100.0


def test_footnotes_and_mid_sentence_percentages_never_become_headings():
    pf = ParagraphFinder(_cfg())
    fn_para = _para("20 MICS Iraq, 2018.", Box(40, 62, 220, 72), label="abandon", debug_id=1)
    pct_para = _para(
        "70.3 per cent in lower secondary and 56 per cent in upper secondary.",
        Box(40, 520, 510, 534),
        label="plain text",
        debug_id=2,
    )
    date_para = _para("24 October 2024", Box(420, 690, 530, 702), label="plain text", debug_id=3)
    real_heading = _para("Programme rationale", Box(40, 640, 240, 654), label="plain text", debug_id=4)
    page = _page("plain text", Box(35, 50, 560, 720), [fn_para, pct_para, date_para, real_heading])
    pf.classify_bottom_page_footnotes(page, page.pdf_paragraph)
    pf.classify_numbered_section_headings(page.pdf_paragraph)
    assert fn_para.layout_label == "footnote"
    assert pct_para.layout_label == "plain text"
    assert date_para.layout_label == "plain text"


def test_formula_placeholder_recovery_repairs_blended_and_missing_b_tags():
    from babeldoc.format.pdf.document_il.midend.il_translator import (
        FormulaPlaceholder,
        ILTranslator,
    )

    ti = ILTranslator.TranslateInput(
        unicode="Approximately 92 per cent of children are enrolled.<b1>Results from 2018 MICS show progress.",
        placeholders=[
            FormulaPlaceholder(
                placeholder_id=1,
                formula=None,
                placeholder="<b1>",
                regex_pattern=r"<\s*b\s*1\s*>",
            )
        ],
        base_style=PdfStyle(font_id="F1", font_size=10.0),
    )
    repaired_blended = ILTranslator._recover_and_clean_formula_placeholders(
        ti,
        "ويلتحق نحو 92 في المائة من الأطفال.<b1}وتظهر نتائج المسح تقدماً.",
    )
    assert "<b1>" in repaired_blended
    assert "<b1}" not in repaired_blended

    repaired_missing = ILTranslator._recover_and_clean_formula_placeholders(
        ti,
        "ويلتحق نحو 92 في المائة من الأطفال. وتظهر نتائج المسح تقدماً.",
    )
    assert "<b1>" in repaired_missing


def test_un_doc_symbol_passthrough_and_official_un_glossary():
    from babeldoc.format.pdf.document_il.midend.domain_glossary import (
        apply_post_glossary,
        apply_pre_glossary,
        extract_document_abbreviations,
    )
    from babeldoc.translator.translator import _is_pure_passthrough, _protect_numerals

    assert _is_pure_passthrough("E/ICEF/2025/P/L.5") is True
    shielded, num_map = _protect_numerals("See document E/ICEF/2025/P/L.5 for details.")
    assert "E/ICEF/2025/P/L.5" in num_map.values()
    assert apply_pre_glossary("Summary") == "موجز"
    assert apply_pre_glossary("Programme rationale") == "الأساس المنطقي للبرنامج"
    assert "حصائل اليونيسف" in apply_pre_glossary("UNICEF outcomes and UNSDCF outcomes")
    assert "حصائل إطار التعاون (UNSDCF)" in apply_post_glossary("نتائج المعهد الدولي للصحة والتنمية")
    abbrevs = extract_document_abbreviations(
        ["focusing on water, sanitation and hygiene (WASH) services"]
    )
    assert abbrevs.get("WASH") == "water, sanitation and hygiene"


def test_prose_with_multiple_from_clauses_not_misclassified_as_sql():
    from babeldoc.format.pdf.document_il.utils.layout_semantics import is_standalone_code_text

    prose = (
        "18. The conclusions of the high-level meeting include the following: the need for "
        "country-led green transitions;\n"
        "the need to tailor support from the United Nations system and other partners;\n"
        "and lessons drawn from recent assessments across middle-income countries."
    )
    assert is_standalone_code_text(prose, "plain text") is False
    assert is_standalone_code_text("SELECT id, name FROM accounts\nWHERE balance > 0;", "plain text") is True
