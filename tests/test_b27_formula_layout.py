"""B-27 regression: a mixed RTL line must retain its protected phone number."""

from types import SimpleNamespace

import pytest

from babeldoc.format.pdf.document_il.il_version_1 import (
    Box,
    PdfCharacter,
    PdfFormula,
    PdfParagraph,
    PdfStyle,
    VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting, TypesettingUnit


def source_character(character, x):
    box = Box(x=x, y=0, x2=x + 6, y2=12)
    return PdfCharacter(
        char_unicode=character,
        box=box,
        visual_bbox=VisualBbox(box=box),
        pdf_style=PdfStyle(font_id="base", font_size=12),
    )


@pytest.mark.parametrize("suffix", ["", " \u0627"], ids=["number-last", "text-after-number"])
def test_arabic_contact_line_keeps_phone_number_in_order(suffix):
    number = "+97444456371"
    formula = PdfFormula(
        box=Box(x=0, y=0, x2=len(number) * 6, y2=12),
        pdf_character=[source_character(char, i * 6) for i, char in enumerate(number)],
        x_offset=0,
        y_offset=0,
        x_advance=len(number) * 6,
    )
    units = [TypesettingUnit(char=source_character(char, 0)) for char in "\u0647\u0627\u062a\u0641: "]
    units.append(TypesettingUnit(formular=formula))
    units.extend(TypesettingUnit(char=source_character(char, 0)) for char in suffix)

    # This method needs a language and a space width. Supply them directly so
    # the test runs the real layout and rendering code without loading assets.
    typesetter = object.__new__(Typesetting)
    typesetter.translation_config = SimpleNamespace(lang_out="ar")
    typesetter.font_mapper = SimpleNamespace(
        base_font=SimpleNamespace(char_lengths=lambda text, size: [6])
    )
    box = Box(x=0, y=0, x2=300, y2=60)
    paragraph = PdfParagraph(box=box, layout_label="contact_card")
    placed, fits = typesetter._layout_typesetting_units(
        units, box, scale=1.0, line_skip=1.2, paragraph=paragraph
    )
    characters = [char for unit in placed for char in unit.render()[0]]
    visual_text = "".join(
        char.char_unicode for char in sorted(characters, key=lambda char: char.box.x)
    )

    assert fits
    assert visual_text.count(number) == 1, f"Rendered left to right: {visual_text!r}"
    assert visual_text.count("\u0627") == 1 + suffix.count("\u0627"), visual_text
