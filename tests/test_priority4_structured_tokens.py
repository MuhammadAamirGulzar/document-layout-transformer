"""Priority 4 acceptance tests: centralized structured-token integrity."""
from __future__ import annotations

import re

from types import SimpleNamespace

from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
from babeldoc.format.pdf.document_il.utils.structured_tokens import (
    isolate_ltr_structured_spans,
    protect_structured_tokens,
    restore_structured_tokens,
)
from babeldoc.translator.translator import _protect_numerals, _restore_numerals


def test_web_contact_paths_and_ids_protected_and_restored_verbatim():
    samples = [
        "Visit https://data.example.org/search?q=iraq_2025&page=2#sec1 for updates.",
        "Email team.iraq+ops@unicef.org or call +974 4445 6371 / +1-555-019-2834.",
        r"Read /var/log/pipeline/app.log and C:\Users\name\report_v2.xlsx before archive.tar.gz.",
        "Release v2.1.3 (commit sha256:0123456789abcdef0123456789abcdef, addr 0xdeadbeef) for uuid 123e4567-e89b-12d3-a456-426614174000.",
        "Invoice INV-2025-0042 references PO-12-908 and document E/ICEF/2025/P/L.5.",
        "On 2025-11-17 (period 2022–2025), 25.4 mg/L at 98.6 °C with p < 0.05 and R² = 0.94 accounted for 4.2%.",
    ]
    for src in samples:
        prot, mapping = protect_structured_tokens(src)
        assert len(mapping) >= 1, f"Expected tokens protected in: {src}"
        restored = restore_structured_tokens(prot, mapping)
        assert restored == src


def test_chemistry_code_json_and_negative_controls():
    chem = "Photosynthesis: 6 CO₂ + 6 H₂O → C₆H₁₂O₆ + 6 O₂ requires Ca²⁺ and Mg2+ cofactors."
    prot_c, map_c = _protect_numerals(chem)
    assert any("CO₂" in v and "→" in v for v in map_c.values())
    assert "Ca²⁺" in map_c.values()
    assert "Mg2+" in map_c.values()
    assert _restore_numerals(prot_c, map_c) == chem

    code_line = 'Run `SELECT id, val FROM metrics` with payload {"status": "ok", "count": 3}.'
    prot_j, map_j = protect_structured_tokens(code_line)
    assert any("SELECT id, val FROM metrics" in v for v in map_j.values())
    assert any('"status": "ok"' in v for v in map_j.values())
    assert restore_structured_tokens(prot_j, map_j) == code_line

    # Negative controls: ordinary uppercase words and generic parenthetical words stay translatable!
    assert _protect_numerals("NATIONAL DEVELOPMENT PROGRAM") == ("NATIONAL DEVELOPMENT PROGRAM", {})
    assert _protect_numerals("Please review (NOTE) carefully.") == ("Please review (NOTE) carefully.", {})


def test_deterministic_restoration_handles_noisy_placeholders_and_windows_backslashes():
    win_src = r"Export saved to C:\Users\name\nested_v1.pdf under code INV-2025-009."
    prot, mapping = protect_structured_tokens(win_src)
    # Simulate noisy NLLB tags { 0 }, <b1>, Arabic-Indic digits
    noisy = prot.replace("{0}", "{ 0 }").replace("{1}", "<b1>")
    restored = restore_structured_tokens(noisy, mapping)
    assert r"C:\Users\name\nested_v1.pdf" in restored
    assert "INV-2025-009" in restored

    # Duplicate / missing occurrence check stays deterministic
    pair_src = "Compare 15.2 mg/L on 2025-01-10 against 18.4 mg/L on 2025-02-10."
    p_pair, m_pair = protect_structured_tokens(pair_src)
    assert len(m_pair) == 4
    # Reordering tags inside Arabic sentence still restores each slot to its exact original token
    reordered_ar = "قارن {2} بتاريخ {3} مقابل {0} بتاريخ {1}."
    r_ar = restore_structured_tokens(reordered_ar, m_pair)
    assert "18.4 mg/L" in r_ar and "2025-02-10" in r_ar
    assert r_ar.index("18.4 mg/L") < r_ar.index("15.2 mg/L")


def test_bidi_isolation_wraps_technical_tokens_without_double_marks():
    ar = "أرسل التقرير إلى team.iraq+ops@unicef.org أو +974 4445 6371 عبر /var/log/app.log أو C:\\Users\\name\\out.pdf [12]."
    isolated = isolate_ltr_structured_spans(ar)
    assert "\u200eteam.iraq+ops@unicef.org\u200e" in isolated
    assert "\u200e+974 4445 6371\u200e" in isolated
    assert "\u200e/var/log/app.log\u200e" in isolated
    assert "\u200e[12]\u200e" in isolated
    assert "\u200e\u200e" not in isolated


def test_il_translator_preserves_bidi_for_urls_phones_paths_and_code_passthrough():
    tracker = object.__new__(ILTranslator)
    tracker.translation_config = SimpleNamespace(lang_out="ar")
    ar_out = tracker._normalize_arabic_output(
        "راجع https://data.example.org/a?x=1&y=2 وتواصل على +974 4445 6371 أو C:\\Users\\name\\a.csv."
    )
    assert "\u200ehttps://data.example.org/a?x=1&y=2\u200e" in ar_out
    assert "\u200e+974 4445 6371\u200e" in ar_out
    assert "\u200eC:\\Users\\name\\a.csv\u200e" in ar_out
    assert "\u200e\u200e" not in ar_out

    # Pure LTR code/SQL/JSON block must not get polluted with Arabic BiDi wrappers when no Arabic exists
    sql = "SELECT id, total FROM budget_2025 WHERE amount > 100;"
    assert isolate_ltr_structured_spans(sql) == sql
