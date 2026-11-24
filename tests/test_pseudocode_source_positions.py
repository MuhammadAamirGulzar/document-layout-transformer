import pytest

from babeldoc.format.pdf.document_il.il_version_1 import (
    Box, Page, PageLayout, PdfCharacter, PdfLine, PdfParagraph,
    PdfParagraphComposition, VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder


def source_line(text, x, y):
    chars = []
    for index, value in enumerate(text):
        left = x + 2.5 * index
        box = Box(left, y, left + 2.5, y + 6)
        chars.append(PdfCharacter(
            char_unicode=value, pdf_character_id=ord(value), box=box,
            visual_bbox=VisualBbox(box=box),
        ))
    box = Box(x, y, x + 2.5 * len(text), y + 6)
    return PdfParagraph(
        box=box, layout_label="fallback_line",
        pdf_paragraph_composition=[PdfParagraphComposition(
            pdf_line=PdfLine(box=box, pdf_character=chars),
        )],
    )


@pytest.mark.parametrize("region_label, operator", [("table", "←"), ("pseudocode", ":=")])
def test_preserves_whole_algorithm_with_source_line_numbers_and_indent(region_label, operator):
    page = Page(page_layout=[PageLayout(box=Box(0, 20, 170, 110), class_name=region_label)])
    algorithm = [
        source_line("1: function Collect(items)", 4, 95),
        source_line("2:", 4, 80),
        source_line("for item in items do", 20, 79),
        source_line("3:", 4, 64),
        source_line(f"total {operator} total + item", 36, 63),
        source_line("end for", 20, 49),
        source_line("return total", 20, 34),
    ]
    body = source_line("The discussion follows.", 10, 8)
    expected = [char for p in algorithm for char in ParagraphFinder._paragraph_source_characters(p)]
    paragraphs = algorithm + [body]
    object.__new__(ParagraphFinder)._preserve_pseudocode_regions(page, paragraphs)

    assert paragraphs == [body]
    assert [id(char) for char in page.pdf_character] == [id(char) for char in expected]


def test_keeps_ordinary_numbered_instructions_on_translation_path():
    page = Page(page_layout=[PageLayout(box=Box(0, 20, 170, 110), class_name="table")])
    paragraphs = [
        source_line("1: Open the document", 4, 95),
        source_line("2: Enter your name", 4, 80),
        source_line("3: Return the form", 4, 65),
    ]
    original = list(paragraphs)
    object.__new__(ParagraphFinder)._preserve_pseudocode_regions(page, paragraphs)

    assert paragraphs == original
    assert not page.pdf_character


def test_preserves_algorithm_labelled_as_plain_text_with_bare_gutter_numbers():
    page = Page(page_layout=[PageLayout(box=Box(0, 20, 170, 110), class_name="plain text")])
    algorithm = [
        source_line("1", 4, 95), source_line("for item in items do", 20, 95),
        source_line("2", 4, 80), source_line("total := total + item", 36, 80),
        source_line("3", 4, 65), source_line("end for", 20, 65),
        source_line("4", 4, 50), source_line("return total", 20, 50),
    ]
    body = source_line("The discussion follows.", 10, 8)
    paragraphs = algorithm + [body]
    object.__new__(ParagraphFinder)._preserve_pseudocode_regions(page, paragraphs)

    assert paragraphs == [body]
