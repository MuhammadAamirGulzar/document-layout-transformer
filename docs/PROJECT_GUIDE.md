# TransDOC project guide

This is the starting point for learning and navigating the repository. It describes the current source layout; it does not claim that a full PDF translation was run or that every model is available on every machine.

## What the application does

TransDOC adapts BabelDOC to translate PDF documents, especially English to Arabic, while trying to preserve page layout. A PDF stores positioned text and graphics, so translated sentences can take more space than their source. TransDOC extracts document objects, groups text, translates it, lays out Arabic text, and writes a new PDF.

## Follow one document through the application

```text
Browser: frontend/index.html
  -> HTTP upload/progress/download: server.py (FastAPI)
  -> stage coordinator: babeldoc/format/pdf/high_level.py
  -> PDF content extraction: document_il/frontend/il_creater.py
  -> scan/layout/table/paragraph processing: document_il/midend/
  -> translation: document_il/midend/il_translator.py
       -> model engines/cache: babeldoc/translator/
  -> Arabic shaping and typesetting: document_il/utils/ and midend/typesetting.py
  -> PDF writing: document_il/backend/pdf_creater.py
  -> progress and output response: server.py -> browser
```

## Important files

| File or folder | Purpose | Read next |
|---|---|---|
| `frontend/index.html` | Upload form, model/layout choices, progress display, download controls | `docs/ENGINES_AND_SERVER.md` |
| `server.py` | FastAPI routes; saves uploads, creates translation sessions, streams progress and serves output files | `docs/ENGINES_AND_SERVER.md`, then `docs/SECURITY_AND_DEPLOYMENT.md` |
| `babeldoc/format/pdf/high_level.py` | Coordinates pipeline stages and asynchronous progress | `docs/ARCHITECTURE.md` |
| `babeldoc/format/pdf/translation_config.py` | Per-run inputs, options, working directories and cleanup | `docs/ARCHITECTURE.md` |
| `babeldoc/format/pdf/document_il/frontend/il_creater.py` | Reads PDF page content into the internal document representation | `docs/ARCHITECTURE.md` |
| `babeldoc/format/pdf/document_il/il_version_1.py` | Data structures for pages, text, fonts, paragraphs and boxes | `docs/ARCHITECTURE.md` |
| `babeldoc/format/pdf/document_il/midend/` | Scan detection, layout/table parsing, paragraph grouping, translation and typesetting | `docs/RTL_AND_TYPESETTING.md`, `docs/FINANCIAL_TRANSLATION_RULES.md` |
| `babeldoc/translator/translator.py` | Translator base and OpenAI, MarianMT and NLLB backends | `docs/ENGINES_AND_SERVER.md` |
| `babeldoc/translator/cache.py` | Persistent translation cache | `docs/ENGINES_AND_SERVER.md` |
| `babeldoc/format/pdf/document_il/utils/` | Arabic shaping, fonts, geometry and PDF helper code | `docs/RTL_AND_TYPESETTING.md` |
| `babeldoc/format/pdf/document_il/backend/pdf_creater.py` | Writes translated output PDF content | `docs/ARCHITECTURE.md` |
| `tests/` | Unit/regression tests for translation rules, geometry, typography and layout behavior | `docs/TESTING_AND_OPERATIONS.md` |
| `Dockerfile` | Container build and web server command | `docs/SECURITY_AND_DEPLOYMENT.md` |
| `pyproject.toml`, `requirements.txt`, `uv.lock` | Project metadata, dependency declarations and resolved lock | `docs/TESTING_AND_OPERATIONS.md` |

## Key ideas in plain language

- **Intermediate Language (IL):** an in-memory structured description of the PDF. The pipeline can work with pages, paragraphs, characters and boxes without immediately rewriting the PDF.
- **Bounding box:** a rectangle that says where something sits on a page, usually using its left, top, right and bottom coordinates.
- **Layout detection:** a model or rule that estimates regions such as text, tables or figures on a page.
- **OCR:** software that turns text in a page image into text characters. It is needed when a page has an image of text instead of selectable PDF text.
- **Typesetting:** deciding where translated words and glyphs fit, how lines wrap, and what direction they read in.
- **BiDi:** Unicode’s rules for displaying right-to-left Arabic mixed with left-to-right numbers or English words.
- **Server-Sent Events (SSE):** a long-lived HTTP response that lets the server send progress updates to the browser.

## The learning path

1. Use this file to orient yourself.
2. Read `docs/ENGINES_AND_SERVER.md` to follow upload and progress.
3. Read `docs/ARCHITECTURE.md` to understand stages and document data.
4. Read `docs/RTL_AND_TYPESETTING.md` and `docs/FINANCIAL_TRANSLATION_RULES.md` before editing Arabic layout or protected financial tokens.
5. Use `docs/TESTING_AND_OPERATIONS.md` to learn the current test inventory and commands.
6. Check `docs/ENGINEERING_BACKLOG.md` before choosing project work.

## Documentation status

The architecture and feature notes include historical details. Current work items live only in `ENGINEERING_BACKLOG.md`; security/deployment assumptions live in `SECURITY_AND_DEPLOYMENT.md`; test commands and verification status live in `TESTING_AND_OPERATIONS.md`. Historical issue reports are retained as dated records and must not be treated as proof that an issue still exists or is fixed.
