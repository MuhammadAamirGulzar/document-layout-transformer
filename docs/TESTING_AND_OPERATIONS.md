# Testing and operations

This page describes the current repository layout and available commands. Commands below were **not run as part of this documentation change**. **VERIFIED — maintainer-supplied G4 batch-2 evidence (`8c727f1`):** 199 passed + 106 subtests. Older documents remain historical snapshots.

**VERIFIED - B-35 (841e0db):** 212 passed + 106 subtests; real 600M smoke OK (UNTRANSLATED 0). A real forced-failure run and a browser check of the note are NOT VERIFIED.

**VERIFIED - model-loading hint (7f905b1):** 217 passed + 106 subtests (190.83 s). How it looks in a real browser is NOT VERIFIED.

## Environment and dependencies

- The repository specifies Python `3.12` in `.python-version`; `pyproject.toml` permits Python `>=3.12,<3.14`.
- This `.venv` ran a passing test suite on the maintainer's laptop earlier; a fresh install needs `bitstring<5` (see B-06). Rerun `pip check` and the full suite after any install and record the date, commit and result.
- Always use the repository virtual-environment interpreter, `.\.venv\Scripts\python.exe`, for project commands on Windows. Do not substitute a system Python unless the maintainer explicitly intends to test a separate environment.
- Dependencies are currently declared in `requirements.txt` and `pyproject.toml` and resolved in `uv.lock`; these files disagree. See the dependency item in [`ENGINEERING_BACKLOG.md`](ENGINEERING_BACKLOG.md) before relying on a clean install.
- Do not install a different dependency set into a shared environment without checking with the maintainer.

## Test inventory

| Test file | Main focus (from test names and imports) |
|---|---|
| `tests/test_financial_regression.py` | Numeral/formula protection, terminology, financial identifiers and related translation/typesetting helpers |
| `tests/test_formatting_anti_collision.py` | Paragraph grouping, table geometry, style boundaries, collision handling and RTL layout |
| `tests/test_formatting_enhancements.py` | BiDi punctuation, headers/footers, bullets, overflow and glyph handling |
| `tests/test_technical_document_regression.py` | Banking identifiers, equations, fonts, technical text, tables and mixed-script behavior |
| `tests/test_typography_regression.py` | Font selection, weight mapping, hierarchy and Arabic scaling |
| `tests/test_stage_labels.py` | VERIFIED implementation evidence: 17 stage-label tests; browser verification is NOT VERIFIED and B-01 stays open |
| `tests/test_untranslated_count.py` | 13 tests for B-35 |
| `tests/test_model_loading_hint.py` | 5 static tests |

**VERIFIED — current test approach:** `TestClient` is broken here with Starlette 0.27.0 + httpx 0.28.1, so tests use `httpx.ASGITransport` inside `asyncio.run`.

## G3/G4 validation and operating controls

- **VERIFIED by tests:** G3 server safety (B-03) is done and committed: fixed upload file name (`b908064`), upload size cap 50 MB plus `%PDF-` check (`b048202`), session-id validation on download (`0d87c66`), CORS removed (`0d9fefb`), download filename header (`34cff55`), fake `/translate/stream` entry removed (`0a935c1`).
- **VERIFIED — G4 batch 1 (`3ed2c50`):** jobs run in the background; the page polls `GET /translate/status/{id}`; explicit `POST /translate/run/{id}`; duplicate run returns 409; one engine job at a time per server process. B-04 is done for the new route.
- **VERIFIED — G4 batch 2 (`8c727f1`):** cooperative cancel via `POST /translate/cancel/{id}`; `JOB_TIMEOUT_SECONDS` default 3600; `SESSION_TTL_SECONDS` default 86400 with a 60 s sweep; the engine slot is released only after the worker really stops.
- **VERIFIED — results:** 199 passed + 106 subtests; a 600M smoke run on the 2-paragraph file took 63 s with 0 errors; a browser cancel was seen working.
- **NOT VERIFIED:** a normal browser finish plus download after batch 2.
- **VERIFIED — limits:** cancel and timeout are cooperative, not a hard kill. The semaphore works within one process only; sessions and tasks are in memory and lost on restart. UUID check is not authentication.
- **NOT VERIFIED — upload spooling:** Starlette 0.27 probably spools the whole upload before the size cap runs, so the deployment proxy must also cap body size.
- **VERIFIED - still open:** B-35, model-loading hint (built, browser check pending), B-21 (old `POST /translate` bypasses job controls), B-17 and B-18. **NOT VERIFIED - still open:** B-26. B-34 is decided: keep the cache off on the web path; no code change.

## Git handoff

- **VERIFIED:** `cleanup-baseline` was pushed as a NEW branch to the owner's public repository on 2026-11-05, tip `8c727f1`. `main` and `afan` were not touched. The commit email is the GitHub noreply address.
- **VERIFIED:** `main` and `afan` have unrelated histories. Never merge them; a future PR goes into `afan`.

## Run tests (not run during this documentation update)

From the repository root, if the current environment already has the project’s test dependencies:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

To run one test file:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_formatting_anti_collision.py
```

For a named test, append `-k` with part of its name, for example:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_technical_document_regression.py -k swift
```

These commands are documented, not executed here. Record the date, branch/commit, exact command, exit code and summary whenever reporting a test result. Do not copy stale totals from older notes.

## Start the web server for local development

If dependencies are already installed, start from the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 7860
```

Then visit `http://127.0.0.1:7860/`; `/health` is the health endpoint, and `http://127.0.0.1:7860/docs` is FastAPI's interactive Swagger API page. This is a suggested local command, not a verified server run. Binding to `127.0.0.1` keeps it local to the machine. The Dockerfile binds to `0.0.0.0` for container networking; public exposure requires the controls in [`SECURITY_AND_DEPLOYMENT.md`](SECURITY_AND_DEPLOYMENT.md).

## Windows Unicode console encoding

If a Windows command or diagnostic script fails with `UnicodeEncodeError` while printing Arabic or other Unicode text, configure standard output before printing:

```python
import sys

sys.stdout.reconfigure(encoding="utf-8")
```

This changes console output encoding for that process; it does not change PDF text, translation behavior or source files.

## Debugging a translation

When a job appears stuck or ends without a PDF, collect:

1. Branch and commit, OS/Python, chosen model and CPU/GPU details.
2. Input PDF page count and whether text is selectable.
3. Timestamped server log from job start to terminal event.
4. Status-poll responses shown to the browser.
5. Whether output files exist in that job’s output directory.

Do not include secrets or sensitive document text in public bug reports. VERIFIED supplied evidence: the first 14-page QCB IBAN web run completed with 600M on laptop CPU in 1297 s, peak 3652 MB, one submission/progress connection/finish, no “Error during NLLB” lines and download 200 OK. Reconnect behavior and first-progress timing remain NOT VERIFIED. See B-20 and the Benchmark section in [`ENGINEERING_BACKLOG.md`](ENGINEERING_BACKLOG.md).

### Smoke-script error-counter coverage

**VERIFIED — supplied Colab trace analysis:** the smoke script's `ERRORS` counter only matches “Error during NLLB translation” (`translator.py:1008`). It misses the separate “Error translating paragraph” path, including the banking-code byte-identity guard assertion for `IIBBAANN` (B-17/B-30). The Colab run completed with `ERRORS 0` and both PDFs despite that paragraph error. Treat the printed counter as one-path coverage, not proof that no errors occurred.

Also count both messages in the saved log with PowerShell `Select-String`. Replace the placeholder with the actual saved log path; these commands are documented, not executed in this docs task:

```powershell
$savedLog = 'path\to\saved-run.log'
@(Select-String -LiteralPath $savedLog -SimpleMatch 'Error during NLLB translation').Count
@(Select-String -LiteralPath $savedLog -SimpleMatch 'Error translating paragraph').Count
```

These are matching-log-line counts, not necessarily distinct failed paragraphs. Inspect the surrounding traceback and paragraph ID before assigning cause or impact. The guard appears to fail closed (LIKELY); its paragraph's output PDF appearance and whether the same paragraph failed in the 600M web run remain NOT VERIFIED.

## Packaging for notebook experiments

**LIKELY packaging failure; archive execution NOT VERIFIED here:** Git stores LFS pointer stubs for the CMap assets under `babeldoc/pdfminer/cmap/` (pointer text is visible in committed blobs). A plain `git archive` zip retains those stubs instead of usable asset contents, making it unsuitable for this experiment. Build the zip from the working tree with real LFS asset contents present; confirm those files are hydrated before packaging. Include the required source/assets, not `.git`, `.venv`, secrets or unrelated uploads. No zip was built during this docs task.

## Deployment pointers

The current Dockerfile uses Python 3.12, installs `requirements.txt`, exposes port 7860 and starts Uvicorn. Deployment readiness is NOT VERIFIED. Resolve ownership and license decisions (B-05, B-15) before deployment, along with dependency alignment, health checks, non-root execution, resource limits, secrets, temporary-file retention and model caching. See [`SECURITY_AND_DEPLOYMENT.md`](SECURITY_AND_DEPLOYMENT.md).
