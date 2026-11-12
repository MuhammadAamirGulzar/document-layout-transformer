import pytest

from babeldoc.translator.translator import _protect_numerals, _restore_numerals


@pytest.mark.parametrize("atom", [
    "https://example.org/a/b?x=1&y=2",
    "www.example.org/paper.pdf",
    "10.1109/5.771073",
    "[12]",
    "[3, 7]",
    "[4-9]",
])
def test_bibliographic_atom_is_one_protected_value(atom):
    source = f"See {atom}, for details."
    protected, mapping = _protect_numerals(source)
    assert list(mapping.values()) == [atom]
    assert _restore_numerals(protected, mapping) == source


def test_trailing_sentence_punctuation_is_not_part_of_the_url():
    protected, mapping = _protect_numerals("Available at https://example.org/x.")
    assert list(mapping.values()) == ["https://example.org/x"]
    assert protected.endswith(".")


def test_doi_prefix_keeps_the_whole_identifier():
    protected, mapping = _protect_numerals("doi: 10.1145/3292500.3330701")
    assert "10.1145/3292500.3330701" in mapping.values()
    assert _restore_numerals(protected, mapping) == "doi: 10.1145/3292500.3330701"
