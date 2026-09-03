# Testing, CLI & Operations Guide

## 1. Regression Test Suites (`tests/`)

The repository includes 3 dedicated, standalone test suites validating all specialized RTL, financial, and typography algorithms. All test suites can run without pytest using standard Python asserts.

### Test Suite 1: RTL Formatting Enhancements (`tests/test_formatting_enhancements.py`)
Validates structural and bidirectional rendering:
- `test_punctuation_bidi_locking`: Trailing neutral punctuation (`.`, `:`, `!`) following numbers/acronyms/percent receives RLM (`\u200F`).
- `test_header_footer_mirroring`: Running headers, footers, and page numbers mirror across the page vertical midline.
- `test_bullet_point_hanging_indent`: Line 0 starts flush right; wrapped lines `1+` are indented inward by `bullet_indent`.
- `test_legend_swatch_placement`: Legend swatch symbol `■` is placed at the correct visual side in RTL rendering.

### Test Suite 2: Financial & Technical Regression (`tests/test_financial_regression.py`)
Validates terminology preservation and numeral protection:
- `test_equation_protection`: Equations like `(2018=100)` and formula placeholders `<b1>` are protected.
- `test_acronym_protection`: Standalone and parenthesized acronyms (`QCBRR`, `QCBLR`, `QCBDR`, `Chart 7-1`) are preserved.
- `test_nds3_protection`: Third National Development Strategy (`NDS3`) terminology mapping.
- `test_currency_standardization`: Eliminates `كرو` and standardizes `QR million`, `QR billion` to `ريال قطري`.
- `test_percent_formatting`: Fixes stray periods around Arabic percent signs (`8.6 .٪` -> `8.6٪.`).
- `test_chapter_and_chart_references`: Preserves `CHAPTER 03` -> `الفصل 03`, `Chart 7-1` -> `المخطط 7-1`.
- `test_multi_line_classification`: Distinguishes short chart labels from flowing multi-line paragraphs.
- `test_typesetting_rigid_body_shift`: Margin shift moves characters as a rigid body, preventing character stacking/overlap.
- `test_chronological_axis_detection`: Detects multi-month time series (`Jan ... Dec`) and preserves LTR axis order.

### Test Suite 3: Typography & Font Hierarchy (`tests/test_typography_regression.py`)
Validates font family mapping and visual scaling:
- `test_ar_font_family_resolution`: Verifies `AR_FONT_FAMILY` contains `GoNotoKurrent-Regular.ttf` and `GoNotoKurrent-Bold.ttf`.
- `test_bold_weight_detection_and_mapping`: Verifies English bold fonts map accurately to `GoNotoKurrent-Bold.ttf`.
- `test_title_exempt_from_mode_scale`: Verifies titles are exempt from body text `mode_scale` clamping.
- `test_role_based_min_scale_floors`: Verifies minimum scale floors (titles `>= 0.88`, captions `>= 0.82`, body `>= 0.72`).
- `test_arabic_optical_compensation`: Verifies Arabic text receives the `1.08x` optical font size compensation.

### Test Suite 4: Technical & Banking Standards (`tests/test_technical_document_regression.py`)
Validates alphanumeric codes, dense tables, and formula integrity:
- `test_bank_swift_and_identifiers_passthrough`: All 17 Qatar bank SWIFT codes (`ABQQQAQAXXX`, `BBMEQAQXXXX`) and 4-letter bank identifiers (`QNBA`, `CBQA`) bypass translation.
- `test_54_country_iso_codes_ltr_preservation`: All 54 country 2-letter ISO codes (`AL` ... `AE`) retain strict LTR order in table cells.
- `test_previously_reversed_9_iso_codes_specifically`: Specifically verifies `BA`, `CZ`, `FO`, `GI`, `ME`, `SM`, `SA`, `GB`, `AE` never reverse.
- `test_pua_bullet_point_hanging_indent`: PUA Wingdings bullets (`\uF0B7`, 61623) trigger hanging indents without word collision (`ابتولديها`).
- `test_mathematical_equations_atomic_protection`: Division formulas (`281211210/97 = 62`) and modulo standards (`MOD 97-10`) are atomically protected.
- `test_contact_info_and_phone_standardization`: Contact metadata (`Tel : +974 44456371` -> `الهاتف: ...`) standardizes accurately without `التلفزيون` hallucinations.
- `test_paragraph_pipeline_trace_diagnostics`: Paragraph execution tracing records layout labels and translation pathways.
- `test_toc_leader_dots_preservation`: Table of Contents leader dots (`...`) are protected from collapsing, isolated from neural translation, and dynamically resized in typesetting to fit on a single line.

---

## 2. Running the Tests

Execute all test suites from the `doc-translator-V2.1` directory using the virtual environment:

```powershell
# 1. Advanced RTL formatting suite (4/4 tests)
.venv\Scripts\python tests/test_formatting_enhancements.py

# 2. Financial translation regression suite (9/9 tests)
.venv\Scripts\python tests/test_financial_regression.py

# 3. Typography & font hierarchy suite (5/5 tests)
.venv\Scripts\python tests/test_typography_regression.py

# 4. Technical & banking standards suite (8/8 tests)
.venv\Scripts\python tests/test_technical_document_regression.py
```

Expected output:
```
ALL ADVANCED FORMATTING TESTS PASSED SUCCESSFULLY!
ALL REGRESSION TESTS PASSED SUCCESSFULLY!
ALL TYPOGRAPHY TESTS PASSED SUCCESSFULLY!
ALL TECHNICAL & BANKING DOCUMENT REGRESSION TESTS PASSED SUCCESSFULLY!
```
(26 / 26 tests passing 100%)

---

## 3. Running the Application

### Running the Production / Dev Web Server
```powershell
cd "d:\Uni Stuff\DataInsight\1\doc-translator-V2.1"
.venv\Scripts\python -m uvicorn server:app --host 0.0.0.0 --port 7860 --reload
```
Once started, visit:
- Web Interface: `http://localhost:7860/`
- Interactive API Docs (Swagger): `http://localhost:7860/docs`
- Health Check: `http://localhost:7860/health`

### Running via BabelDOC CLI Directly
Translate any PDF document from the command line:

```powershell
# Using local Meta NLLB-200 (default, offline)
.venv\Scripts\python -m babeldoc --files input.pdf --lang-in en --lang-out ar

# Using local MarianMT model
.venv\Scripts\python -m babeldoc --huggingface --files input.pdf --lang-in en --lang-out ar

# Using OpenAI GPT-4o-mini
.venv\Scripts\python -m babeldoc --openai --openai-model "gpt-4o-mini" --openai-api-key "sk-..." --files input.pdf --lang-in en --lang-out ar
```

---

## 4. Troubleshooting & Gotchas

1. **Python Virtual Environment**:
   Always run using `.venv\Scripts\python`. Do not invoke system `python` or `uv` directly if not in system PATH.
2. **Windows Unicode Console Encoding**:
   When printing Arabic strings directly in PowerShell, Python on Windows may throw `UnicodeEncodeError: 'charmap' codec can't encode characters`.
   - Fix: Use `sys.stdout.reconfigure(encoding='utf-8')` before printing Arabic text in scratch scripts.
3. **GPU vs CPU Inference for NLLB**:
   - On CUDA GPUs, NLLB loads automatically in `torch.float16`, consuming only ~1.4 GB VRAM for the 600M model.
   - On CPU, inference takes slightly longer; ensure sufficient RAM is free (~3 GB).
4. **Temporary Files**:
   Session files and uploaded PDFs are saved in `TEMP_DIR/babeldoc_api/<session_id>`. Clean up periodically if disk space is constrained.
