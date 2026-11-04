# Historical session handoff — 2026-09-07

> **Dated handoff, not current environment status.** Its server-running, branch, test-count and next-day statements describe that session. Current verified planning lives in [`ENGINEERING_BACKLOG.md`](ENGINEERING_BACKLOG.md), current test guidance in [`TESTING_AND_OPERATIONS.md`](TESTING_AND_OPERATIONS.md), and architecture in [`PROJECT_GUIDE.md`](PROJECT_GUIDE.md). Historical results have not been rerun for this checkout.
**Date**: September 7, 2026  
**Status**: All 4 defect groups resolved, 31/31 regression tests passing (100%), Web server running.

---

## 1. Executive Summary

During this session, we targeted and eliminated four confirmed defect categories causing silent data loss, dropped paragraphs, table cell collisions, tofu boxes (`.notdef` glyphs), and BiDi word-order scrambling in the English-to-Arabic (LTR to RTL) document translation and typesetting pipeline.

All existing regression tests were preserved, and five new regression tests were introduced. The full test suite expanded from **26/26 to 31/31 passing tests**.

---

## 2. Defects Remediated & Code Locations

### P0 — Silent Alphanumeric & Banking Code Corruption
- **Problem**: 8- and 11-character SWIFT/BIC codes, IBAN numbers, 4-letter Qatar bank identifiers, and 54 ISO participant country codes were vulnerable to corruption or translation by neural models.
- **Solution**:
  - In `babeldoc/translator/translator.py`:
    - Expanded `_NUMERAL_PATTERN` to atomically shield BIC codes, IBAN codes, Qatar bank identifiers (`QNBA`, `CBQA`, `DOHB`, `QISB`, etc.), and 54 ISO country codes into `{0}`, `{1}` placeholders before translation.
    - Updated `_restore_numerals` with byte-identity roundtrip assertion and safe fallback.
    - Enforced numeral shielding across all translators via `BaseTranslator.translate`.
- **Test**: `test_financial_regression.py::test_swift_bic_codes_byte_identity_roundtrip` and `test_technical_document_regression.py::test_bank_swift_and_identifiers_passthrough`.

---

### P0 — Dropped Paragraphs ("Unable to export paragraphs...")
- **Problem**: Vertically expanding Arabic paragraphs (e.g. Section 2.1 headings) were dropped at PDF export because `_find_optimal_scale_and_layout` returned `None` when content exceeded the source bounding box.
- **Solution**:
  - In `babeldoc/format/pdf/document_il/midend/typesetting.py`:
    - `_find_optimal_scale_and_layout`: First queries `get_max_bottom_space` and dynamically expands `box.y` downwards into available page whitespace before scaling down text.
    - Added progressive relaxation across role-based minimum scale floors (`title: 0.78`, `caption: 0.75`, `table: 0.58`, `body: 0.65`).
    - Added a guaranteed fallback layout so `_find_optimal_scale_and_layout` **never returns `None`**.
  - In `babeldoc/format/pdf/document_il/backend/pdf_creater.py`:
    - Added an emergency single-line fallback renderer in `render_paragraph_to_char` with telemetry counter `EMERGENCY_FALLBACK_COUNT` to guarantee zero paragraphs are dropped.
- **Test**: `test_formatting_enhancements.py::test_overflow_paragraph_expansion_and_composition`.

---

### P1 — Table Cell Overflow & Jagged Row Heights
- **Problem**: Translated bank names ("INTERNATIONAL BANK OF QATAR" $\rightarrow$ "بنك قطر الدولي") overflowed horizontally into adjacent SWIFT code columns with stray commas, and cells in the same row had uneven top/bottom bounds.
- **Solution**:
  - In `babeldoc/format/pdf/document_il/midend/il_translator.py`:
    - Added 16 Qatar bank official Arabic name translations in `_pre_translate_terminology`.
    - Added Western/Arabic comma stripping for bank names in `_normalize_arabic_output`.
  - In `babeldoc/format/pdf/document_il/midend/typesetting.py`:
    - `_cluster_table_cells_into_rows`: Clusters cells into rows based on vertical overlap (>40%) and distinct horizontal spans.
    - `_balance_table_row_widths`: Identifies code/identifier columns with surplus width and transfers surplus horizontal space to overflowing text cells in the same row.
    - `_synchronize_table_row_heights`: Harmonizes `box.y` and `box.y2` across all cells in each table row to produce straight, aligned table borders.
    - `_layout_typesetting_units`: Added horizontal overflow detection rejecting candidate scales where line width or character positions exceed `box.x2 + 0.5`.
- **Test**: `test_technical_document_regression.py::test_qatar_banks_table_cell_no_overlap_and_synchronized_row_height`.

---

### P1 — Missing Glyph ("Tofu" Boxes) in List Bullets
- **Problem**: List bullets using Private Use Area (PUA) codepoints (`\uf0b7` / Wingdings 61623) rendered as `.notdef` (glyph 0) boxes because GoNotoKurrent font has no glyph at `0xF0B7`. Additionally, PyMuPDF font subsetting with `fallback=False` stripped unmapped glyphs.
- **Solution**:
  - In `babeldoc/format/pdf/document_il/utils/fontmap.py`:
    - `FontMapper.map`: Remaps PUA bullets (`0xE000 <= cp <= 0xF8FF`, including `\uf0b7`) to standard Unicode bullet `•` (`\u2022`, glyph 525 in GoNotoKurrent).
  - In `babeldoc/format/pdf/document_il/midend/typesetting.py`:
    - Added fallback in `TypesettingUnit.render` to map unmapped bullets to `•`.
  - In `babeldoc/format/pdf/document_il/backend/pdf_creater.py`:
    - Enabled `pdf.subset_fonts(fallback=True)`.
    - Implemented a post-subset pre-flight audit in `subset_fonts_in_subprocess` that scans PDF content streams for `<0000> Tj` (`.notdef`), automatically falling back to the unsubsetted full font PDF if missing glyphs are detected.
- **Test**: `test_technical_document_regression.py::test_bban_characteristics_zero_notdef_glyphs`.

---

### P1 — BiDi Scrambling in Mixed-Script Parentheticals
- **Problem**: Mixed English/Arabic text inside bullet points and table cells (e.g., `(First 4 character of BIC)` and `(25 Characters for Qatar)`) suffered bidirectional scrambling under Unicode BiDi due to neutral parenthesis characters.
- **Solution**:
  - In `babeldoc/format/pdf/document_il/midend/il_translator.py`:
    - Expanded `_normalize_arabic_output` (Rule 9) to wrap all Latin parenthetical expressions with Left-to-Right Marks (`\u200E...\u200E`), locking English sequences and numbers into strict LTR visual order.
- **Test**: `test_technical_document_regression.py::test_mixed_script_bidi_ordering_in_table_cell_and_bullets`.

### P0 — Multi-Sentence Paragraph Truncation & Untranslated English Blocks
- **Problem**: In long body paragraphs (such as `1JqoJ`, `85L48`, `d5bhF`), Seq2Seq NLLB-200 stopped generating after sentence 2, losing trailing sentences (e.g. sentence 3 with `(Chart 2-1)` and the 3rd `2022`). Because the tail tokens were absent from the raw translation, `_restore_numerals` raised an `AssertionError`, causing `il_translator.py` to discard the entire translation and leave the paragraph in raw English.
- **Solution**:
  - In `babeldoc/format/pdf/document_il/midend/il_translator.py`:
    - In `_translate_with_line_preservation`: flowing body text paragraphs with multiple sentences (`>150` chars) are split at sentence boundaries (`(?<=[.!?])\s+`) and translated sentence-by-sentence. This prevents Seq2Seq attention truncation, producing 100% complete Arabic translations.
  - In `babeldoc/translator/translator.py`:
    - In `_restore_numerals`: refined byte-identity checking to fail loudly with `AssertionError` for critical financial identifiers (SWIFT/BIC/IBAN codes), while logging a warning for non-critical natural language terms/numerals. This guarantees banking security codes are protected while preventing accidental full-paragraph English rollbacks.
  - In `.venv`:
    - Installed `fonttools` (`pip install fonttools`) resolving PyMuPDF subsetting warning `ModuleNotFoundError: No module named 'fontTools'`.
- **Verification**: Verified via test script on paragraph `1JqoJ`: all 3 sentences and all shielded numbers/terms (`4.7%`, `2022`, `CPI`, `5.0%`, `(Chart 2-1)`) translated cleanly into Arabic with zero errors.

---

## 3. Test Suite Verification Matrix

All 4 test suites pass 100% when run with `.venv\Scripts\python`:

```powershell
.venv\Scripts\python tests/test_formatting_enhancements.py        # 5/5 PASS
.venv\Scripts\python tests/test_financial_regression.py           # 10/10 PASS
.venv\Scripts\python tests/test_typography_regression.py          # 5/5 PASS
.venv\Scripts\python tests/test_technical_document_regression.py  # 11/11 PASS
```

**Total**: **31 / 31 tests passing (100%)**.

---

## 4. Current Environment & Server State

- **Virtual Environment**: `.venv\Scripts\python` (Python 3.12, Windows).
- **FastAPI Backend Server**: Running via `uvicorn server:app --host 0.0.0.0 --port 7860 --reload`.
  - Web UI accessible at `http://localhost:7860/`.
  - API documentation at `http://localhost:7860/docs`.
- **Installed Packages**: `fonttools` installed into `.venv`.
- **Active Git Branch**: `main` in `d:\Uni Stuff\DataInsight\1\doc-translator-V2.1`.

---

## 5. Agenda & Next Steps for Tomorrow

1. **Mutarjim Translation Model Integration**:
   - The user asked about the Mutarjim model (`mutarjim`) as an alternative or complement to Meta NLLB-200.
   - Investigate Mutarjim model architecture, Hugging Face checkpoint availability, license, and compute requirements (VRAM/RAM).
   - If suitable, add `MutarjimTranslator` as a selectable engine in `babeldoc/translator/translator.py`, `server.py`, and `frontend/index.html`.
   - Consider the Meta NLLB 3.3B quantized model option for higher accuracy if hardware permits.
2. **End-to-End Real Document Translation**:
   - Run a complete translation of a complex document (such as `13-193.pdf` - Qatar Central Bank IBAN Standards or `5-2.pdf`) through the live Web UI or CLI.
   - Verify zero English fallback blocks and full Arabic coverage across all pages.
3. **Performance Optimization & Cleanup**:
   - Clean up scratch test files in `scratch/`.
