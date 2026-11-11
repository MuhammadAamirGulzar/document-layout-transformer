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


def formula_unit(text):
    characters = [source_character(character, index * 6) for index, character in enumerate(text)]
    return TypesettingUnit(
        formular=PdfFormula(
            box=Box(x=0, y=0, x2=len(text) * 6, y2=12),
            pdf_character=characters,
            x_offset=0,
            y_offset=0,
            x_advance=len(text) * 6,
        )
    )


def render_left_to_right(units):
    typesetter = object.__new__(Typesetting)
    typesetter.translation_config = SimpleNamespace(lang_out="ar")
    typesetter.font_mapper = SimpleNamespace(
        base_font=SimpleNamespace(char_lengths=lambda text, size: [6 for _ in text])
    )
    paragraph = PdfParagraph(
        box=Box(x=0, y=0, x2=500, y2=80),
        layout_label="plain text",
    )
    placed, fits = typesetter._layout_typesetting_units(
        units,
        paragraph.box,
        scale=1.0,
        line_skip=1.2,
        paragraph=paragraph,
    )
    assert fits
    characters = [character for unit in placed for character in unit.render()[0]]
    return "".join(
        character.char_unicode
        for character in sorted(characters, key=lambda character: character.box.x)
    )


def test_adjacent_formula_objects_remain_one_ltr_expression():
    units = [TypesettingUnit(char=source_character(character, 0)) for character in "\u064a\u062d\u0633\u0628 "]
    units.extend(
        formula_unit(text)
        for text in ("W", "=", "{", "w1", ",", "w2", "}")
    )
    units.extend(
        TypesettingUnit(char=source_character(character, 0))
        for character in " \u0628\u0639\u062f \u0627\u0644\u062a\u062f\u0631\u064a\u0628"
    )

    assert "W={w1,w2}" in render_left_to_right(units)


def test_multiple_inline_math_islands_keep_internal_order():
    units = [
        TypesettingUnit(char=source_character(character, 0))
        for character in "\u0627\u0644\u0646\u0645\u0648\u0630\u062c "
    ]
    units.append(formula_unit("M"))
    units.extend(
        TypesettingUnit(char=source_character(character, 0))
        for character in " \u064a\u062d\u062a\u0648\u064a \u0639\u0644\u0649 "
    )
    units.append(formula_unit("L"))
    units.extend(
        TypesettingUnit(char=source_character(character, 0))
        for character in " \u0637\u0628\u0642\u0627\u062a "
    )
    units.extend(formula_unit(text) for text in ("{", "l1", ",", "l2", "}"))

    rendered = render_left_to_right(units)
    assert "{l1,l2}" in rendered
    assert rendered.count("M") == 1
    assert rendered.count("L") == 1
