---
title: TransDOC Translator
emoji: 📄
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# TransDOC (Doc-Translator V2.1)
> Intelligent English-to-Arabic PDF Document Translation & Typesetting with Layout Preservation

TransDOC is an enterprise-grade document translation and typesetting platform engineered specifically for translating complex PDF documents from **English (LTR) to Arabic (RTL)** while preserving the 1:1 visual structure, geometry, typography hierarchy, tables, charts, formulas, and diagrams.

Built on the **BabelDOC** intermediate-language framework, TransDOC introduces specialized algorithms for financial, economic, and government policy publications (such as Qatar Central Bank monetary bulletins and National Development Strategy reports).

---

## 🌟 Key Capabilities & Features

- **Advanced RTL Typesetting**:
  - **Per-Line BiDi Algorithm**: Breaks text into lines before applying Unicode Bidirectional reordering, preventing line-wrap corruption.
  - **Bullet Points & Inverted Hanging Indents**: First line flush with right margin; subsequent lines indented inward.
  - **Running Header/Footer & Page Number Mirroring**: Automatically mirrors top/bottom margins across the vertical midline.
  - **Chronological Axis Preservation**: Preserves LTR time-series axes (e.g. `Jan ... Dec`) to match visual chart data curves.
  - **Punctuation BiDi Locking (RLM `\u200F`)**: Ensures trailing periods, colons, and question marks after numbers/acronyms remain on the left side of Arabic sentences.

- **Typography & Font Weight Hierarchy**:
  - **Font Weight Detection & Mapping**: Automatically detects bold English fonts (`Arial-BoldMT`, `Helvetica-Bold`) and maps them to `GoNotoKurrent-Bold.ttf`.
  - **Optical Scale Compensation (`1.08x`)**: Enlarges Arabic glyphs to balance visual weight against Latin text.
  - **Hierarchy Isolation**: Protects titles, headings, captions, and footnotes from being shrunk down by body text scaling.
  - **Role-Based Scale Floors**: Strict minimum scaling thresholds (Titles `>= 0.88`, Captions `>= 0.82`, Body `>= 0.72`).

- **Financial & Technical Translation Integrity**:
  - **Numeral & Formula Protection (`_protect_numerals`)**: Shields numbers, years, percentages, and equation labels `(2018=100)` with `{0}`, `{1}` placeholders to prevent Seq2Seq looping or hallucination.
  - **Pure Passthrough Detection**: Completely bypasses translation for purely numerical table cells, years, and financial codes.
  - **Automated Terminology Normalization**: Fixes currency mistranslations (`كرو` -> `ريال قطري`, `QR million`, `QR billion`), basis points (`bps` -> `نقطة أساس`), and petroleum terms (`غير الزيت` -> `غير النفط`).

- **Multi-Engine Translation Support**:
  - **Meta NLLB-200** (`facebook/nllb-200-distilled-600M` & `1.3B`): SOTA offline translation with GPU float16 acceleration, beam search, and repetition penalties.
  - **MarianMT**: Lightweight offline local translation for CPU deployments.
  - **OpenAI GPT-4o-mini / GPT-4o**: Cloud LLM translation with dynamic bilingual glossary extraction.

- **Dual & Monolingual PDF Outputs**:
  - Export clean monolingual Arabic PDFs (`.mono.pdf`) or side-by-side bilingual reading PDFs (`.dual.pdf`).

- **Interactive Web Interface & FastAPI Server**:
  - Modern web UI (`frontend/index.html`) with drag-and-drop file upload, model picker, layout mode selector, and dual/mono download buttons.
  - Real-time Server-Sent Events (SSE) streaming progress across all 14 pipeline stages.

---

## 📚 Technical Documentation

For in-depth explanations and specifications, see the detailed documentation:

- [System Architecture & 14-Stage Pipeline](docs/ARCHITECTURE.md): Data structures (`Box`, `PdfParagraph`, `TypesettingUnit`), intermediate representation, and stage breakdown.
- [RTL Layout & Typesetting Deep-Dive](docs/RTL_AND_TYPESETTING.md): Per-line BiDi algorithm, hanging indents, page mirroring, timeline axes, and optical font scaling.
- [Financial Translation Rules & Token Protection](docs/FINANCIAL_TRANSLATION_RULES.md): Numeral protection, curly bracket placeholders, regex patterns, acronym preservation, and RLM locking.
- [Translation Engines, Server & Frontend](docs/ENGINES_AND_SERVER.md): NLLB-200, MarianMT, OpenAI, FastAPI REST/SSE endpoints, and Web UI architecture.
- [Testing, Operations & CLI Guide](docs/TESTING_AND_OPERATIONS.md): Regression test suite verification, server deployment, CLI commands, and troubleshooting.

---

## 🚀 Quick Start

### 1. Run Regression Tests
Verify that all 18 regression tests pass:
```powershell
.venv\Scripts\python tests/test_formatting_enhancements.py
.venv\Scripts\python tests/test_financial_regression.py
.venv\Scripts\python tests/test_typography_regression.py
```

### 2. Start the Web Server
Launch the FastAPI application:
```powershell
.venv\Scripts\python -m uvicorn server:app --host 0.0.0.0 --port 7860 --reload
```
Open your browser at `http://localhost:7860/` to access the translation interface.

### 3. Translate from Command Line
```powershell
.venv\Scripts\python -m babeldoc --files my_document.pdf --lang-in en --lang-out ar
```
