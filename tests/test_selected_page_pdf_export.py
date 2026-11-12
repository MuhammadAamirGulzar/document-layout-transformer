from types import SimpleNamespace

import pymupdf
import pytest

from babeldoc.format.pdf.document_il.backend.pdf_creater import PDFCreater
from babeldoc.format.pdf.high_level import migrate_toc
from babeldoc.format.pdf.translation_config import TranslateResult


@pytest.mark.parametrize(
    ("alternating", "translated_first", "bookmark_pages"),
    [(False, False, (1, 2)), (True, False, (2, 4)), (True, True, (1, 3))],
)
def test_selected_page_pairs_and_bookmarks(
    tmp_path, monkeypatch, alternating, translated_first, bookmark_pages,
):
    source_path = tmp_path / "source.pdf"
    mono_path = tmp_path / "mono.pdf"
    dual_path = tmp_path / "dual.pdf"
    with pymupdf.open() as source:
        for number in range(1, 6):
            source.new_page().insert_text((30, 30), f"Original {number}")
        source.set_toc([
            [1, "Omitted parent", 1],
            [2, "Selected child", 2],
            [3, "Omitted branch", 3],
            [4, "Selected descendant", 4],
            [1, "Omitted last", 5],
        ])
        source.save(source_path)

    config = SimpleNamespace(
        input_file=source_path, only_include_translated_page=True,
        should_translate_page=lambda page: page in {2, 4},
        use_alternating_pages_dual=alternating, dual_translate_first=translated_first,
        get_working_file_path=lambda name: tmp_path / name, skip_clean=True,
    )
    creator = object.__new__(PDFCreater)
    creator.original_pdf_path = source_path
    with pymupdf.open() as translated:
        for number in (2, 4):
            translated.new_page().insert_text((30, 30), f"Translated {number}")
        translated.save(mono_path)
        with creator._create_dual_pdf(translated, dual_path, config, [1, 3]) as dual:
            dual.save(dual_path)

    monkeypatch.setattr(
        PDFCreater, "save_pdf_with_timeout",
        staticmethod(lambda document, path, **kwargs: document.save(path)),
    )
    migrate_toc(config, TranslateResult(mono_path, dual_path))

    with pymupdf.open(mono_path) as mono:
        assert mono.get_toc() == [[1, "Selected child", 1], [2, "Selected descendant", 2]]
    with pymupdf.open(dual_path) as dual:
        assert dual.get_toc() == [
            [1, "Selected child", bookmark_pages[0]],
            [2, "Selected descendant", bookmark_pages[1]],
        ]
        contents = [page.get_text().splitlines() for page in dual]
    pairs = [[f"Original {number}", f"Translated {number}"] for number in (2, 4)]
    if alternating:
        if translated_first:
            pairs = [list(reversed(pair)) for pair in pairs]
        pairs = [[text] for pair in pairs for text in pair]
    assert contents == pairs
