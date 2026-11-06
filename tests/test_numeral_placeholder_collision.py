"""B-40: numeral restoration must leave existing PDF layout tags intact."""

import pytest

from babeldoc.translator.translator import _protect_numerals, _restore_numerals


@pytest.mark.parametrize(
    "source",
    [
        "The remaining part of the IBAN from the <b1>- <b2>position is known as BBAN.",
        "List of IBAN participants as of <b1> June 2013.",
        "RESULT FULL IBAN<b1>QA64SCBL00000000000137502560",
        "IBAN uses <b1>local code</b1> known as BBAN.",
    ],
    ids=["ordinal-range", "date", "iban-equation", "rich-text"],
)
def test_numeral_round_trip_keeps_layout_tags(source):
    protected, mapping = _protect_numerals(source)
    # Echo the protected text, so any mutation comes from the pipeline.
    assert _restore_numerals(protected, mapping) == source


def test_numeral_restoration_still_accepts_unreserved_b_tag():
    source = "The IBAN has an identifier."
    protected, mapping = _protect_numerals(source)
    placeholder = next(key for key, value in mapping.items() if value == "IBAN")
    rewritten = protected.replace(placeholder, f"<b{placeholder[1:-1]}>")
    assert _restore_numerals(rewritten, mapping) == source
