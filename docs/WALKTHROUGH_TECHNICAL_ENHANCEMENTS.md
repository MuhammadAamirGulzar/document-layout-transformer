# Historical walkthrough: technical and banking enhancements

> **Historical implementation note, not a current defect list or test report.** It records changes and test output from an earlier session. Some items are now covered by current source/tests, but this document’s test totals and “all resolved” claim have not been freshly verified. Use [`ENGINEERING_BACKLOG.md`](ENGINEERING_BACKLOG.md) for current open work and [`TESTING_AND_OPERATIONS.md`](TESTING_AND_OPERATIONS.md) for current commands.

All failure modes identified in the Qatar Central Bank IBAN Standards document (`13-193.pdf`) have been resolved, regression-tested, and verified across the pipeline.

---

## 1. Summary of Changes

### 1. SWIFT / BIC & Bank Identifier Passthrough (Bug Group 1)
- **Problem**: 11-character SWIFT codes (`ABQQQAQAXXX`, `IBOQQAQAXXX`, `UNILQAQAXXX`, `KLJIQAQAXXX`, `BRWAQAQAXXX`, `QIDBQAQAXXX`) were exceeding the 6-character heuristic in `_is_pure_passthrough` and were being phonetically transliterated into Arabic nonsense words (e.g. `قيبقواق XXX`).
- **Solution**:
  - Implemented standard SWIFT BIC regex: `_SWIFT_BIC_PATTERN = re.compile(r'^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?$')`
  - Added full IBAN pattern: `_IBAN_PATTERN = re.compile(r'^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$')`
  - Added 15–35 character alphanumeric account / BBAN detection (e.g. `SCBL000000000001375025601QA00`).
  - Added 2–4 letter uppercase code checks to cover bank identifiers (`QNBA`, `CBQA`, `DOHB`, `KLJI`, etc.).
  - Added SWIFT BIC patterns to `_NUMERAL_PATTERN` to protect codes embedded inside flowing sentences.

### 2. Isolated Latin Token & ISO Country Code LTR Ordering (Bug Group 2)
- **Problem**: In the 54-country IBAN participant table, 2-letter ISO country codes sitting alone in table cells without Arabic letters (`BA`, `CZ`, `FO`, `GI`, `ME`, `SM`, `SA`, `GB`, `AE`) were being inverted by the RTL base direction in `get_bidi_indices_and_chars` (e.g. `BA` -> `A B`, `CZ` -> `Z C`, `GB` -> `B G`, `SA` -> `A S`).
- **Solution**:
  - Added `is_isolated_latin` detection to `Typesetting._layout_typesetting_units`:
    ```python
    has_arabic_chars = any(
        (0x0600 <= ord(c) <= 0x06FF) or (0xFB50 <= ord(c) <= 0xFEFF)
        for c in line_str
    )
    is_isolated_latin = (not has_arabic_chars) and bool(
        re.match(r'^[A-Za-z0-9\s\-–—/._\(\)+:=#\$\€\£\¥\%\*]+$', line_str.strip())
    )
    ```
  - When `is_isolated_latin` is True, bypasses per-line BiDi reordering, preserving strict Left-to-Right character order and aligning correctly with the column bounds (`line_start_x = box.x`).

### 3. PUA Bullet Codepoints & Hanging Indents (Bug Group 4)
- **Problem**: Microsoft Word exported documents often use Private Use Area (PUA) codepoint `\uF0B7` (decimal 61623) for bullets (``). Because it was absent from `BULLET_POINT_PATTERN`, it was tokenized as part of the first Arabic word, causing text collision and mangled words (`ابتولديها`).
- **Solution**:
  - Added `\uF0B7\uF0A7\uF076\uF0D8\uE000-\uF8FF` to `BULLET_POINT_PATTERN` in `layout_helper.py`.
  - In `_layout_typesetting_units`, isolated any bullet point as its own standalone token during word segmentation so it never adheres to adjacent Arabic text.
  - Enabled PUA bullet detection to trigger `is_bullet_para = True` and set a hanging indent `bullet_indent >= 14.0pt` on lines 1+.

### 4. Mathematical Equations & Division Formulas (Bug Group 5)
- **Problem**: Formulas like `281211210/97 = 62` or `502560126/97 =22` were broken into pieces by the translation model, with `/` treated as a neutral BiDi separator that flipped operands across linebreaks (`/281211210` on one line and `97 =62` on another).
- **Solution**:
  - Enhanced `_NUMERAL_PATTERN` to capture arithmetic division and equations atomically:
    `(\b\d+\s*[/÷]\s*\d+\s*(?:=\s*\d+)?\b)` and `(\b\d+\s*[-–—+*×]\s*\d+\s*=\s*\d+\b)`.
  - Added modulo algorithms and ISO standards: `(\b(?:MOD\s*\d+(?:-\d+)?|ISO(?:/IEC)?\s*\d+(?:-\d+)?)\b)`.
  - Ensured standalone equations match `_is_pure_passthrough`.

### 5. Contact Metadata & Telephone Terminology (Bug Group 6)
- **Problem**: `Tel` in contact person metadata (`Tel : +974 44456371`) was translated to `التلفزيون` ("Television"), and phone numbers were dropped or corrupted.
- **Solution**:
  - Added contact terminology mappings to `_pre_translate_terminology` in `il_translator.py`:
    - `Tel` / `Tel:` -> `الهاتف:`
    - `Fax` / `Fax:` -> `الفاكس:`
    - `E-Mail` / `E-Mail:` -> `البريد الإلكتروني:`
  - Added post-translation normalization to replace any remaining `التلفزيون` in telephone context with `الهاتف:`.
  - Added international phone number protection to `_NUMERAL_PATTERN` and `_is_pure_passthrough`.

### 6. Paragraph Routing & Execution Path Tracing (Bug Group 3)
- **Problem**: Need structured diagnostic visibility into which `layout_label` and execution path paragraphs take to diagnose full-sentence mirroring gaps.
- **Solution**:
  - Added `pipeline_trace: dict | None` to the `PdfParagraph` schema in `il_version_1.py`.
  - Instrumented `ILTranslator.translate_paragraph` and `Typesetting.render_paragraph` to log and record:
    - `layout_label`
    - `engine`
    - `is_arabic_output`
    - `is_table_content`
    - `can_passthrough`
    - `num_lines`
    - `is_bullet_para` and `bullet_indent`

---

## 2. Test Verification & Results

A new regression suite [`tests/test_technical_document_regression.py`](file:///d:/Uni%20Stuff/DataInsight/1/doc-translator-V2.1/tests/test_technical_document_regression.py) was built with full fixtures from `13-193.pdf`:
- **All 17 Bank Rows**: Every bank name, SWIFT code, and bank identifier verified.
- **All 54 Country Codes**: Every country and 2-letter ISO code verified for strict LTR ordering.
- **All 9 Previously Reversed Codes**: `BA`, `CZ`, `FO`, `GI`, `ME`, `SM`, `SA`, `GB`, `AE` specifically asserted.
- **PUA Bullets**: `\uF0B7` hanging indent and right margin placement verified.
- **Equations**: `281211210/97 = 62`, `MOD 97-10`, `QA00SCBL...` atomic protection verified.
- **Contact Info**: `Tel`, `Fax`, and phone numbers verified.

### Execution Output
```
Running Advanced RTL Formatting Suite...
  PASS: test_punctuation_bidi_locking
  PASS: test_header_footer_mirroring
  PASS: test_bullet_point_hanging_indent
  PASS: test_legend_swatch_placement
### 7. Table of Contents (TOC) Dotted Leader Lines Preservation (Bug Group 7)
- **Problem**: In the Table of Contents, dotted leader lines (`1. Introduction ................................. 1306`) were completely missing or reduced to truncated fragments (`...`), causing titles and page numbers to collide with no connector.
  - Root Cause A: Seq2Seq neural translation degraded on runs of 40–100 consecutive dots, causing repeating loops or broken brackets (`}}}}.مقدمة}}}`).
  - Root Cause B: Post-translation cleanup regex (`[. 。…，]{20,}`) explicitly collapsed runs of 20+ dots to a single period `.`.
  - Root Cause C: Arabic titles differ in width from English; reusing static dot counts forced page numbers to wrap to line 2.
- **Solution**:
  - In `il_translator._translate_with_line_preservation`: detected TOC entries matching `^(.*?)\s*(\.{4,})\s*(\d+)$`, translated **only the title string**, and bypassed neural models for leader dots and page numbers.
  - Exempted TOC leader sequences (`\.{4,}\s*\d+`) from punctuation collapsing.
  - In `typesetting._layout_typesetting_units`: dynamically calculated available width on the line (`avail - current_line_w - pnum_w - spacing`) and resized dot glyph count so the entire entry fits on a **single line**, with title right-aligned at `box.x2`, page number left-aligned at `box.x`, and dots connecting them across the middle.

---

## 2. Test Verification & Results

A new regression test `test_toc_leader_dots_preservation` was added to [`tests/test_technical_document_regression.py`](file:///d:/Uni%20Stuff/DataInsight/1/doc-translator-V2.1/tests/test_technical_document_regression.py):
- **All 17 Bank Rows**: Every bank name, SWIFT code, and bank identifier verified.
- **All 54 Country Codes**: Every country and 2-letter ISO code verified for strict LTR ordering.
- **All 9 Previously Reversed Codes**: `BA`, `CZ`, `FO`, `GI`, `ME`, `SM`, `SA`, `GB`, `AE` specifically asserted.
- **PUA Bullets**: `\uF0B7` hanging indent and right margin placement verified.
- **Equations**: `281211210/97 = 62`, `MOD 97-10`, `QA00SCBL...` atomic protection verified.
- **Contact Info**: `Tel`, `Fax`, and phone numbers verified.
- **TOC Leader Dots**: Numbered and unnumbered TOC entries verified for single-line fit, dot preservation, and left/right margin alignment.

### Execution Output
```
Running Advanced RTL Formatting Suite...
  PASS: test_punctuation_bidi_locking
  PASS: test_header_footer_mirroring
  PASS: test_bullet_point_hanging_indent
  PASS: test_legend_swatch_placement
Results: 4/4 tests passed.
ALL ADVANCED FORMATTING TESTS PASSED SUCCESSFULLY!

Running Financial Translation & Typesetting Regression Suite...
  PASS: test_equation_protection
  PASS: test_acronym_protection
  PASS: test_nds3_protection
  PASS: test_currency_standardization
  PASS: test_percent_formatting
  PASS: test_chapter_and_chart_references
  PASS: test_multi_line_classification
  PASS: test_typesetting_rigid_body_shift
  PASS: test_chronological_axis_detection
Results: 9/9 tests passed.
ALL REGRESSION TESTS PASSED SUCCESSFULLY!

Running Typography & Font Hierarchy Regression Suite...
  PASS: test_ar_font_family_resolution
  PASS: test_bold_weight_detection_and_mapping
  PASS: test_title_exempt_from_mode_scale
  PASS: test_role_based_min_scale_floors
  PASS: test_arabic_optical_compensation
  PASS: test_dynamic_role_aware_optical_scaling
  PASS: test_semantic_header_bold_enforcement
  PASS: test_role_aware_line_spacing
Results: 8/8 tests passed.
ALL TYPOGRAPHY TESTS PASSED SUCCESSFULLY!

Running Technical & Banking Document Regression Suite...
  PASS: test_54_country_iso_codes_ltr_preservation
  PASS: test_bank_swift_and_identifiers_passthrough
  PASS: test_contact_info_and_phone_standardization
  PASS: test_definition_whitespace_and_heading_segmentation
  PASS: test_mathematical_equations_atomic_protection
  PASS: test_paragraph_pipeline_trace_diagnostics
  PASS: test_previously_reversed_9_iso_codes_specifically
  PASS: test_pua_bullet_point_hanging_indent
  PASS: test_table_column_chunk_separation
  PASS: test_toc_leader_dots_preservation
Results: 10/10 tests passed.
ALL TECHNICAL & BANKING DOCUMENT REGRESSION TESTS PASSED SUCCESSFULLY!
```

**Overall Status**: **31 / 31 automated tests passing (100%)**. Zero regressions.
