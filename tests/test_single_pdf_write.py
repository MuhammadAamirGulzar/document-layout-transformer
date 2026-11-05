"""B-26: the normal translate path must build the output PDFs once, not twice."""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "babeldoc/format/pdf/high_level.py"


def _text():
    return SRC.read_text(encoding="utf-8")


def test_normal_path_writes_the_pdf_once():
    text = _text()
    start = text.index('detailed_logger.start_stage("Generate Drawing Instructions and Save PDF")')
    end = text.index("return result", start)
    assert text[start:end].count("pdf_creater.write(") == 1


def test_file_has_exactly_three_write_calls():
    # parse-only path, normal path, first-page watermark helper
    assert _text().count("pdf_creater.write(") == 3
