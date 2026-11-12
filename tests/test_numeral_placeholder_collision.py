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


@pytest.mark.parametrize(
    "identifier", ["AZ-104", "RFC-9110", "EC2-104", "QA-104", "AB-12.3", "OPS\u201117", "SECURITY-104"]
)
def test_hyphenated_identifier_round_trips_as_one_value(identifier):
    source = f"See {identifier} and {identifier}."

    protected, mapping = _protect_numerals(source)

    assert list(mapping.values()) == [identifier, identifier]
    assert _restore_numerals(protected, mapping) == source


@pytest.mark.parametrize("source", ["page-12", "x-12", "X-12", "see AB-12th", "see AB-12-CD"])
def test_prose_and_math_are_not_treated_as_hyphenated_identifiers(source):
    protected, mapping = _protect_numerals(source)

    assert not any(value.startswith(("page-", "x-", "X-", "AB-")) for value in mapping.values())
    assert _restore_numerals(protected, mapping) == source
