"""P0-3: Reading order, cross-region merge guards, atomic inline formulas, code & figure preservation."""
from __future__ import annotations

from types import SimpleNamespace

from babeldoc.format.pdf.document_il.il_version_1 import (
    BaseOperations,
    Box,
    Cropbox,
    Mediabox,
    Page,
    PageLayout,
    PdfCharacter,
    PdfFont,
    PdfFormula,
    PdfLine,
    PdfParagraph,
    PdfParagraphComposition,
    PdfStyle,
    VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.il_translator import (
    ILTranslator,
    ParagraphTranslateTracker,
)
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
from babeldoc.format.pdf.document_il.midend.typesetting import (
    Typesetting,
    TypesettingUnit,
)
from babeldoc.format.pdf.document_il.utils.layout_semantics import (
    annotate_page_figure_regions,
    can_merge_paragraphs,
    compute_page_reading_order,
    is_standalone_code_text,
)
from babeldoc.format.pdf.document_il.utils.quality_report import ContentLedger
from babeldoc.format.pdf.translation_config import (
    SharedContextCrossSplitPart,
    TranslationConfig,
    WatermarkOutputMode,
)


def _make_config() -> TranslationConfig:
    cfg = TranslationConfig(
        translator=None,
        input_file="",
        lang_in="en",
        lang_out="ar",
        doc_layout_model=None,
        skip_clean=True,
        watermark_output_mode=WatermarkOutputMode.NoWatermark,
    )
    shared = SharedContextCrossSplitPart()
    shared.content_ledger = ContentLedger()
    cfg.shared_context_cross_split_part = shared
    return cfg


def _make_para(
    text: str,
    box: Box,
    *,
    label: str = "paragraph",
    debug_id: int = 1,
) -> PdfParagraph:
    style = PdfStyle(font_id="F1", font_size=10.0)
    chars: list[PdfCharacter] = []
    step = max(2.0, (box.x2 - box.x) / max(len(text), 1))
    for i, ch in enumerate(text):
        cbox = Box(box.x + i * step, box.y, min(box.x2, box.x + (i + 1) * step), box.y2)
        chars.append(
            PdfCharacter(
                char_unicode=ch,
                pdf_character_id=ord(ch),
                box=cbox,
                visual_bbox=VisualBbox(box=cbox),
                pdf_style=style,
            )
        )
    return PdfParagraph(
        debug_id=debug_id,
        box=box,
        xobj_id=-1,
        unicode=text,
        layout_label=label,
        pdf_style=style,
        pdf_paragraph_composition=[
            PdfParagraphComposition(pdf_line=PdfLine(box=box, pdf_character=chars))
        ],
    )


def test_two_column_reading_order_and_cross_region_merge_guards():
    """Scrambled PDF stream order must resolve to banner -> left col -> right col -> footnote -> footer."""
    title = _make_para("Research Paper Title", Box(50, 740, 540, 780), label="title", debug_id=1)
    left_1 = _make_para("Left column first paragraph-", Box(50, 620, 270, 690), label="paragraph", debug_id=2)
    left_2 = _make_para("continues seamlessly here.", Box(50, 560, 270, 610), label="paragraph", debug_id=3)
    right_1 = _make_para("Right column starts here.", Box(310, 640, 540, 690), label="paragraph", debug_id=4)
    right_2 = _make_para("Right column second block.", Box(310, 560, 540, 620), label="paragraph", debug_id=5)
    footnote = _make_para("1. Author affiliation footnote.", Box(50, 70, 500, 95), label="footnote", debug_id=6)
    footer = _make_para("Page 1", Box(270, 25, 320, 42), label="page_footer", debug_id=7)
    caption = _make_para("caption below fig", Box(50, 480, 270, 500), label="figure_caption", debug_id=8)

    # Deliberately supply scrambled PDF draw order
    scrambled = [footer, right_2, footnote, left_2, right_1, title, left_1]
    page = Page(
        page_number=0,
        mediabox=Mediabox(box=Box(0, 0, 595, 842)),
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_paragraph=scrambled,
    )

    ordered, summary = compute_page_reading_order(page)
    assert [p.debug_id for p in ordered] == [1, 2, 3, 4, 5, 6, 7]
    assert summary["columns_detected"] >= 2
    assert ordered[1].pipeline_trace["column_index"] == 0
    assert ordered[3].pipeline_trace["column_index"] == 1
    assert ordered[5].pipeline_trace["reading_region"] == "footnote"
    assert ordered[6].pipeline_trace["reading_region"] == "footer"

    # Cross-column and caption/footnote/body merges are forbidden
    assert can_merge_paragraphs(left_1, left_2, page=page) is True
    assert can_merge_paragraphs(left_1, right_1, page=page) is False
    assert can_merge_paragraphs(left_2, caption, page=page) is False
    assert can_merge_paragraphs(right_2, footnote, page=page) is False

    # Same-column body hyphenation still merges while caption/right-col stay isolated
    finder = ParagraphFinder(_make_config())
    mix = [left_1, left_2, right_1, caption]
    finder.merge_hyphenated_and_continuation_paragraphs(mix, page=page)
    assert len(mix) == 3
    assert "paragraphcontinues" in mix[0].unicode or "paragraph continues" in mix[0].unicode


def test_inline_formulas_in_pure_arabic_are_atomic_ltr_and_never_wrap_split():
    """Formula atoms surrounded solely by Arabic text/spaces and LRM/LRI islands stay LTR and unbroken."""
    def make_char(ch: str) -> PdfCharacter:
        b = Box(0, 0, 6, 12)
        return PdfCharacter(
            char_unicode=ch,
            box=b,
            visual_bbox=VisualBbox(box=b),
            pdf_style=PdfStyle(font_id="base", font_size=12),
        )

    def make_formula_unit(text: str) -> TypesettingUnit:
        chars = [make_char(c) for c in text]
        return TypesettingUnit(
            formular=PdfFormula(
                box=Box(0, 0, len(text) * 6, 12),
                pdf_character=chars,
                x_offset=0,
                y_offset=0,
                x_advance=len(text) * 6,
            )
        )

    typesetter = object.__new__(Typesetting)
    typesetter.translation_config = SimpleNamespace(lang_out="ar")
    typesetter.font_mapper = SimpleNamespace(
        base_font=SimpleNamespace(char_lengths=lambda text, size: [6 for _ in text])
    )

    # Case 1: Arabic text surrounding an inline formula atom containing Greek + Latin math
    units = [TypesettingUnit(char=make_char(c)) for c in "نحسب "]
    units.extend(make_formula_unit(tok) for tok in ("W", "=", "{", "w1", ",", "w2", "}"))
    units.extend(TypesettingUnit(char=make_char(c)) for c in " باستخدام ")
    units.append(make_formula_unit("α+β=γ"))
    units.extend(TypesettingUnit(char=make_char(c)) for c in " فقط")

    box = Box(0, 0, 420, 80)
    para = PdfParagraph(box=box, layout_label="plain text")
    placed, fits = typesetter._layout_typesetting_units(units, box, 1.0, 1.2, para)
    assert fits
    rendered_chars = [c for u in placed for c in u.render()[0]]
    visual = "".join(c.char_unicode for c in sorted(rendered_chars, key=lambda c: c.box.x))
    assert "W={w1,w2}" in visual
    assert "α+β=γ" in visual

    # Case 2: Narrow line wrap near bounded \u2066...\u2069 LTR island keeps expression intact on one line
    phrase = "نستخدم الصيغة الحسابية الطويلة \u2066MOD 97 = 56\u2069 هنا"
    island_units = [TypesettingUnit(char=make_char(c)) for c in phrase]
    narrow_box = Box(0, 0, 190, 100)
    para2 = PdfParagraph(box=narrow_box, layout_label="plain text")
    placed2, fits2 = typesetter._layout_typesetting_units(island_units, narrow_box, 1.0, 1.2, para2)
    assert fits2
    # Group rendered characters by line Y and verify "MOD 97 = 56" was not torn across 2 lines
    by_line: dict[float, list[PdfCharacter]] = {}
    for u in placed2:
        for c in u.render()[0]:
            by_line.setdefault(round(c.box.y, 1), []).append(c)
    line_strings = [
        "".join(c.char_unicode for c in sorted(cs, key=lambda x: x.box.x))
        for cs in by_line.values()
    ]
    assert any("MOD 97 = 56" in ls for ls in line_strings), line_strings


def test_generalized_code_preservation_keeps_captions_translatable():
    """Unnumbered code listings are preserved, while Algorithm N captions remain translatable."""
    cfg = _make_config()
    finder = ParagraphFinder(cfg)

    cap = _make_para("Algorithm 1: Attention Layer Update", Box(40, 240, 320, 255), label="plain text", debug_id=1)
    line1 = _make_para("def attention_step(Q, K, V):", Box(40, 215, 280, 230), label="plain text", debug_id=2)
    line2 = _make_para("for idx in range(len(Q)):", Box(55, 195, 260, 210), label="plain text", debug_id=3)
    line3 = _make_para("scores = compute_dot(Q[idx], K);", Box(70, 175, 310, 190), label="plain text", debug_id=4)
    line4 = _make_para("return softmax(scores)", Box(55, 155, 250, 170), label="plain text", debug_id=5)
    prose = _make_para("The above procedure updates key representations cleanly.", Box(40, 110, 420, 130), label="paragraph", debug_id=6)

    page = Page(
        page_number=0,
        mediabox=Mediabox(box=Box(0, 0, 595, 842)),
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        page_layout=[PageLayout(id=1, class_name="algorithm", box=Box(35, 150, 340, 260), conf=0.92)],
        pdf_paragraph=[cap, line1, line2, line3, line4, prose],
    )
    paragraphs = list(page.pdf_paragraph)
    finder._preserve_pseudocode_regions(page, paragraphs)

    # Code body moved verbatim to page.pdf_character; caption and trailing prose stay in paragraphs
    assert [p.debug_id for p in paragraphs] == [1, 6]
    assert cap.layout_label == "algorithm_caption"
    assert cap.pipeline_trace["preserve_mode"] == "caption_translatable"
    preserved_text = "".join(c.char_unicode for c in page.pdf_character)
    assert "attention_step" in preserved_text
    assert "softmax" in preserved_text

    # Standalone ILTranslator guard preserves code snippets but NOT ordinary numbered prose
    assert is_standalone_code_text("SELECT user_id, total FROM invoices WHERE paid = 1;") is True
    assert is_standalone_code_text("Algorithm 2: Fast Decoder") is False
    assert is_standalone_code_text("1: Open the document\n2: Sign at bottom") is False

    ilt = ILTranslator(SimpleNamespace(lang_out="ar"), cfg)
    ilt.support_llm_translate = False
    tracker = ParagraphTranslateTracker()
    code_para = _make_para("def compute_loss(y_true, y_pred):\n    return (y_true - y_pred)", Box(0, 0, 200, 30), label="paragraph")
    res_text, res_inp = ilt.pre_translate_paragraph(code_para, tracker, {}, {})
    assert res_text is None and res_inp is None
    assert code_para.pipeline_trace["preserved_code"] is True
    assert code_para.pipeline_trace["skip_reason"] == "code_or_algorithm"


def test_figure_ticks_and_scientific_word_examples_preserved_while_captions_translate():
    """Numeric chart ticks and word-level diagram tokens stay verbatim; captions/titles stay translatable."""
    cfg = _make_config()
    fig_box = Box(40, 200, 480, 500)

    tick_num = _make_para("1e-5", Box(60, 220, 95, 232), label="fallback_line", debug_id=10)
    axis_var = _make_para("k=3", Box(180, 220, 210, 232), label="fallback_line", debug_id=11)
    # Clustered single-word diagram / linguistic activation tokens inside the figure
    tok1 = _make_para("trophy", Box(120, 380, 165, 394), label="fallback_line", debug_id=12)
    tok2 = _make_para("suitcase", Box(200, 380, 255, 394), label="fallback_line", debug_id=13)
    tok3 = _make_para("NNP", Box(280, 380, 315, 394), label="fallback_line", debug_id=14)
    # Descriptive chart title and figure caption remain translatable
    chart_title = _make_para("Validation Accuracy Across Epochs", Box(140, 470, 360, 488), label="chart_title", debug_id=15)
    fig_caption = _make_para("Figure 3: Attention activations for ambiguous pronouns.", Box(40, 165, 480, 185), label="figure_caption", debug_id=16)

    page = Page(
        page_number=0,
        mediabox=Mediabox(box=Box(0, 0, 595, 842)),
        cropbox=Cropbox(box=Box(0, 0, 595, 842)),
        pdf_font=[PdfFont(font_id="F1", name="Helvetica", xref_id=1)],
        page_layout=[PageLayout(id=5, class_name="figure", box=fig_box, conf=0.95)],
        pdf_paragraph=[tick_num, axis_var, tok1, tok2, tok3, chart_title, fig_caption],
        base_operations=BaseOperations(value=""),
    )

    records = annotate_page_figure_regions(page)
    assert len(records) >= 5
    cfg.shared_context_cross_split_part.content_ledger.record_preserved_regions(records)

    ilt = ILTranslator(SimpleNamespace(lang_out="ar"), cfg)
    ilt.support_llm_translate = False

    # Ticks, variables, POS tags, and diagram word tokens are skipped cleanly as passthrough
    for frozen_para in (tick_num, axis_var, tok1, tok2, tok3):
        t = ParagraphTranslateTracker()
        txt, inp = ilt.pre_translate_paragraph(frozen_para, t, {"F1": page.pdf_font[0]}, {})
        assert txt is None and inp is None
        assert frozen_para.pipeline_trace["preserved_figure_data"] is True

    # Chart title and figure caption STILL produce TranslateInput for translation!
    for translatable_para in (chart_title, fig_caption):
        t = ParagraphTranslateTracker()
        txt, inp = ilt.pre_translate_paragraph(translatable_para, t, {"F1": page.pdf_font[0]}, {})
        assert txt is not None and inp is not None

    report = cfg.shared_context_cross_split_part.content_ledger.build_report()
    assert report["status"] == "clean"
    assert report["summary"]["figure_preserved_count"] >= 5
