from types import SimpleNamespace
from unittest.mock import Mock

from babeldoc.format.pdf.document_il.il_version_1 import PdfParagraph
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator


FILE_INVENTORY = (
    "project/ src/ For C/C++: main.c main.cpp grammar.c grammar.cpp parser.c "
    "parser.cpp tree.c tree.cpp input/ grammar1.txt grammar2.txt grammar3.txt "
    "output/ parsing_table.txt parsing_trace.txt docs/ report.pdf README.md"
)


def prepare(text, *, llm=False):
    engine = object.__new__(ILTranslator)
    engine.support_llm_translate = llm
    engine.translation_config = SimpleNamespace(disable_rich_text_translate=False, min_text_length=5)
    prepared = engine.TranslateInput(text, [])
    engine.get_translate_input = Mock(return_value=prepared)
    paragraph = PdfParagraph(unicode=text, layout_label="plain text")
    result = engine.pre_translate_paragraph(paragraph, Mock(), {}, {})
    return result, engine.get_translate_input


def test_local_translation_keeps_dense_file_inventory_verbatim():
    result, get_input = prepare(FILE_INVENTORY)
    assert result == (None, None)
    get_input.assert_not_called()


def test_prose_that_mentions_files_still_reaches_translator():
    text = "Open src/ and read main.c and README.md before you change the parser."
    result, get_input = prepare(text)
    assert result[0] == text
    get_input.assert_called_once()


def test_prose_heavy_block_with_file_list_still_reaches_translator():
    text = FILE_INVENTORY + " " + (
        "The parser must report a useful error whenever a production cannot be read. "
        "Explain the behavior in your report and describe how the tests cover each error. "
        "Use meaningful names, include the expected results, and document the assumptions."
    )
    result, get_input = prepare(text)
    assert result[0] == text
    get_input.assert_called_once()


def test_llm_translation_keeps_its_existing_path():
    result, get_input = prepare(FILE_INVENTORY, llm=True)
    assert result[0] == FILE_INVENTORY
    get_input.assert_called_once()
