# TransDOC / Doc-Translator V2.1 - Agent & Project Context Guide

## Project Identity & Mission
**Doc-Translator V2.1 (TransDOC)** is an enterprise-grade document translation and typesetting engine based on **BabelDOC**, engineered specifically for high-fidelity **English-to-Arabic (LTR to RTL) PDF document translation**. 

It specializes in complex layout preservation for:
- Government policy papers and development strategies (e.g., Qatar National Development Strategy - NDS3)
- Central bank & macroeconomic bulletins (Qatar Central Bank / QCB, interest rates, monetary policy)
- Financial statements, statistical tables, and econometric indexes
- Mixed-content technical publications with equations, charts, legends, and diagrams

---

## Repository Structure
```
d:\Uni Stuff\DataInsight\1\
├── AGENTS.md                                   <- (This file) Master agent instruction & context guide
└── doc-translator-V2.1\                        <- Core application repository
    ├── .venv\                                  <- Python 3.12 virtual environment (use for all execution)
    ├── server.py                               <- FastAPI backend server with SSE streaming & dual/mono export
    ├── frontend\
    │   └── index.html                          <- Modern Web UI for upload,    ├── tests\                                  <- Comprehensive regression test suites (31/31 passing)
    │   ├── test_formatting_enhancements.py     <- Tests for RTL hanging indents, headers/footers, RLM locking, box expansion (5/5)
    │   ├── test_financial_regression.py        <- Tests for equations, acronyms, currency, axes, SWIFT roundtrip (10/10)
    │   ├── test_typography_regression.py       <- Tests for font weights, optical scale, hierarchy floors (5/5)
    │   └── test_technical_document_regression.py <- Tests for SWIFT codes, 54 ISO codes, PUA bullets, formulas, TOC dots, table cells (11/11)
    ├── docs\                                   <- Deep-dive technical documentation
    │   ├── SESSION_HANDOFF_2026_09_07.md       <- Latest session state, defect remediations, and continuation agenda
    │   ├── ARCHITECTURE.md                     <- 14-stage pipeline & Intermediate Representation (IL)
    │   ├── RTL_AND_TYPESETTING.md              <- Line-level BiDi, hanging indents, mirroring, optical scaling
    │   ├── FINANCIAL_TRANSLATION_RULES.md      <- Numeral protection, regex rules, currency, acronyms
    │   ├── ENGINES_AND_SERVER.md               <- Meta NLLB-200, MarianMT, OpenAI, FastAPI & SSE
    │   └── TESTING_AND_OPERATIONS.md           <- Testing commands, environment setup, CLI & operations
    └── babeldoc\                               <- Core translation & typesetting engine
        ├── assets\                             <- Embedded font metadata & ONNX models
        ├── format\pdf\
        │   ├── high_level.py                   <- Pipeline orchestrator (TRANSLATE_STAGES)
        │   └── document_il\
        │       ├── il_version_1.py             <- Data classes: Box, PdfParagraph, PdfFont, etc.
        │       ├── backend\pdf_creater.py      <- PDF generation, font subsetting audit, emergency fallback
        │       ├── midend\
        │       │   ├── typesetting.py          <- RTL layout, line wrapping, BiDi, downward expansion, table balancing
        │       │   ├── il_translator.py        <- Translation dispatcher, terminology, LTR parenthetical protection
        │       │   ├── il_translator_llm_only.py
        │       │   ├── paragraph_finder.py     <- Visual span clustering
        │       │   ├── layout_parser.py        <- ONNX YOLO layout detection
        │       │   └── automatic_term_extractor.py
        │       └── utils\fontmap.py            <- FontMapper: bold weight detection, PUA bullet mapping
        └── translator\
            └── translator.py                   <- MarianMT, NLLB-200, OpenAI, _protect_numerals

---

## Essential Commands

> **CRITICAL**: Always use the virtual environment python: `.venv\Scripts\python` (PowerShell on Windows). `uv` is not globally installed in PATH.

### 1. Run Regression Tests (31/31 Passing)
```powershell
# In d:\Uni Stuff\DataInsight\1\doc-translator-V2.1
.venv\Scripts\python tests/test_formatting_enhancements.py
.venv\Scripts\python tests/test_financial_regression.py
.venv\Scripts\python tests/test_typography_regression.py
.venv\Scripts\python tests/test_technical_document_regression.py
```

### 2. Run the FastAPI Server
```powershell
.venv\Scripts\python -m uvicorn server:app --host 0.0.0.0 --port 7860 --reload
```

---

## Core Engineering Rules & Architecture Principles

### 1. The Per-Line BiDi Rule (DO NOT RE-ORDER FULL PARAGRAPHS)
- **Problem**: Running `get_display()` on a complete multi-line Arabic paragraph *before* line breaking reverses the character stream globally. When the typesetting engine then measures word widths and breaks lines, line wraps occur in the middle of words and invert BiDi spans across line boundaries.
- **Rule**:
  1. Translation phase (`il_translator.py`): Call `shape_arabic_text()` (glyph joining / presentation forms) and `_normalize_arabic_output()`. **DO NOT** call `get_display()` here.
  2. Typesetting phase (`typesetting.py`): Break tokens into logical lines first based on available box width.
  3. Reorder visually **line-by-line** using `get_bidi_indices_and_chars(line_str, base_dir='R')`.

### 2. Numerical & Financial Integrity (`_protect_numerals` & Multi-Sentence Flow)
- Seq2Seq models (MarianMT, NLLB) hallucinate, translate to Eastern Arabic-Indic numerals (`٧٥`), or enter infinite repeating token loops when encountering XML tags (`<b1>`).
- **Rule**: Numeric patterns, equations `(2018=100)`, financial acronyms (`QCBRR`, `NDS3`), SWIFT/BIC codes (8/11 char), IBANs, Qatar bank identifiers, and 54 ISO codes are protected before translation into `{0}`, `{1}` curly bracket placeholders, and restored byte-for-byte post-translation.
- **Multi-Sentence Flowing Paragraphs**: Body paragraphs with multiple sentences (`>150` chars) are split at sentence boundaries (`(?<=[.!?])\s+`) and translated sentence-by-sentence to prevent Seq2Seq attention truncation from dropping tail sentences.
- **Resilient Verification**: In `_restore_numerals`, critical banking identifiers (SWIFT/BIC/IBAN) strictly fail loudly on byte corruption, while non-critical natural language numerals/terms log diagnostic warnings rather than aborting the entire paragraph back to raw English.
- Cells containing pure numbers, dates, or financial codes bypass the translation model entirely via `_is_pure_passthrough`.

### 3. RTL Typesetting Nuances
- **Bullet Points & Hanging Indents**: Line 0 starts flush with right margin (`box.x2`). Lines 1+ are indented inward by `bullet_indent` (calculated from bullet width + spacing, min 14pt).
- **Chronological Axis Preservation**: In charts with horizontal time axes (`Jan 2021 Feb 2021 ... Dec 2021`), the axis **must remain LTR** to preserve chronological alignment with the chart's visual curves above it.
- **Punctuation BiDi Locking**: Trailing punctuation (`.`, `:`, `!`, `؟`) after numbers or acronyms is pinned to the left end of the Arabic line by appending Unicode Right-to-Left Mark (RLM `\u200F`).
- **Optical Font Scaling**: Arabic glyphs in Noto Kurrent are visually smaller than Latin glyphs. Typesetting applies an optical factor of `1.08x` (`optical_factor = 1.08 if self.is_arabic else 1.0`).
- **Hierarchy Isolation**: Titles, headings, and captions are strictly exempt from being scaled down by the body text `mode_scale`. Minimum scale floors: Titles `>= 0.88`, Captions `>= 0.82`, Footnotes `>= 0.75`, Body `>= 0.72`.

### 4. Downward Box Expansion & Role Relief Floors (Zero Dropped Paragraphs)
- **Problem**: When translated text expands vertically, strict bounding box clipping caused `_find_optimal_scale_and_layout` to return `None`, leaving `pdf_paragraph_composition` empty and triggering silent paragraph drops in `pdf_creater.py`.
- **Rule**:
  1. Always query `get_max_bottom_space` to expand `box.y` downwards into available whitespace first.
  2. Apply progressive relaxation down to role relief floors (`title: 0.78`, `caption: 0.75`, `table: 0.58`, `body: 0.65`).
  3. `_find_optimal_scale_and_layout` **must never return `None`**.
  4. Emergency single-line fallback in `pdf_creater.py` ensures zero dropped paragraphs even under extreme constraint.

### 5. Table Row Balancing & Synchronized Heights
- **Problem**: Long translated text in table cells spilled into sibling code columns, and cells had jagged top/bottom bounds.
- **Rule**:
  1. `_balance_table_row_widths`: Identify narrow code columns (SWIFT codes, IDs) with surplus width and redistribute available space to text cells in the same row.
  2. `_synchronize_table_row_heights`: Harmonize `box.y` and `box.y2` across all cells in each table row.
  3. Reject candidate layouts if character positions or line width exceed `box.x2 + 0.5`.

### 6. PUA Bullet Remapping & Font Subsetting Safety
- **Problem**: Bullet codepoint `\uf0b7` (Wingdings 61623) produces `.notdef` (glyph 0) boxes because GoNotoKurrent cmap has no glyph at `0xF0B7`. Subsetting with `fallback=False` drops glyphs silently.
- **Rule**:
  1. Remap PUA bullets (`0xE000 <= cp <= 0xF8FF`, e.g. `\uf0b7`) to standard Unicode bullet `•` (`\u2022`, glyph 525).
  2. Run font subsetting with `fallback=True` and conduct a post-subset `<0000> Tj` pre-flight audit, falling back to full unsubsetted font if any missing glyphs are detected.

### 7. Mixed-Script BiDi Parenthetical Protection
- **Problem**: Latin parenthetical phrases like `(First 4 character of BIC)` and `(25 Characters for Qatar)` inside Arabic text scramble under Unicode BiDi due to neutral parentheses and European numerals.
- **Rule**: Wrap Latin parenthetical phrases in `_normalize_arabic_output` with Left-to-Right Marks (`\u200E...\u200E`) to pin English words and numerals into strict LTR visual order.

### 8. Translation Engines
- **Meta NLLB-200** (`NLLBTranslator`): State-of-the-Art offline translation with GPU float16 acceleration, beam search (`num_beams=4`), and n-gram repetition penalty. Default engine.
- **MarianMT** (`MarianMTTranslator`): Lightweight offline local seq2seq translator.
- **OpenAI** (`OpenAITranslator`): GPT-4o-mini / GPT-4o cloud translation with glossary extraction.
- *(Upcoming)* **Mutarjim / NLLB 3.3 Quantized**: Planned evaluation for enhanced enterprise Arabic translation.
