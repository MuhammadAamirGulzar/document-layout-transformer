# Engineering backlog

This is the single current planning and reconciliation document. It contains the prioritized work list, the old-document findings checked against the current source, and the learning/documentation path. Items are based on static source review as of 2026-11-04. “Open” means the work or verification remains; it does not claim a reproduced runtime defect. Keep entries small and update evidence/status when work is done.

**Statuses:** Open = not completed; Verify = collect evidence before deciding whether code needs changing; Implemented, verify runtime = matching code/tests exist but real output is not confirmed. No item below is marked complete based only on an old document.

Evidence labels: VERIFIED = the cited source lines were read or the output was shown; it does NOT mean the code was executed. LIKELY = static evidence points to it. NOT VERIFIED = needs direct evidence.

## P0 — before public or production use

| ID | Status | Item and evidence | Likely files | Acceptance evidence |
|---|---|---|---|---|
| B-01 | Open; label cause VERIFIED | Correct the misleading “Finding Paragraphs” stage label. The mapping checks `Paragraph` at `server.py:83` before `Translate` at `:86`, so `Translate Paragraphs` can receive the paragraph-finding label. The separate stall cause is B-20. | `server.py`, `frontend/index.html`, API tests | A focused API/SSE test proves paragraph finding and paragraph translation receive distinct, accurate labels. |
| B-02 | Open; smoke baseline VERIFIED | A clean laptop-CPU smoke run used the 600M model with `sample_en.pdf` (2 paragraphs) through a guarded script calling `translate()`, not the web path. It took 131 seconds, peaked at 4,694 MB, recorded 0 swallowed translation errors and produced both PDFs. Add a repeatable automated smoke test; the current tests mostly exercise engine components. | `tests/`, `high_level.py`, `pdf_creater.py` | Deterministic fixture test produces readable mono and dual PDFs with expected pages/text, without downloading a large model during the test. |
| B-03 | Open; traversal LIKELY | Harden upload paths, size/content validation, session access and temporary-file expiry. Upload routes join the client filename to the session path (`server.py:200-206,534-550`); the UI’s 50 MB text is not a server limit (`frontend/index.html:827-832`). The download route builds its path from the `session_id` in the URL with no format check (`server.py:472-495`) and `Content-Disposition` is not quoted (`:505`); path traversal is LIKELY but NOT tested. | `server.py`, API tests | Server-generated disk name, enforced byte/page limits, valid PDF check, path containment, denied unauthorized downloads and tested expiry/cleanup. |
| B-04 | Open | Define and test async terminal behavior for success, errors, cancellation, disconnect and timeout. | `high_level.py:415-444,779-784`, `progress_monitor.py`, `server.py` | Every submitted job reaches one clear terminal state; no browser or worker waits forever. |
| B-05 | Open | Confirm repo owner/contribution target and review AGPL plus each font/model/data asset license before deployment. | `LICENSE`, `pyproject.toml`, asset metadata, repository settings | Supervisor/owner confirms rights and asset manifest/source-offer plan. |

## P1 — engineering foundations

| ID | Status | Item and evidence | Likely files | Acceptance evidence |
|---|---|---|---|---|
| B-06 | Open; install failure VERIFIED | Reconcile dependency declarations. `requirements.txt`, `pyproject.toml` and `uv.lock` differ; RapidOCR is `==1.2.3` in requirements versus `>=1.4.4` in project metadata/lock. A fresh install of the afan branch crashed because bitstring 5.0.0 removed `BitStream`, which is imported only at `pdf_creater.py:15`; local commit `5b412d4` pins `bitstring<5` (not pushed). Declare the pin in every dependency source. Map actual imports to direct dependencies before removing packages; web, Arabic and local-model paths need explicit coverage. | `requirements.txt`, `pyproject.toml`, `uv.lock`, `Dockerfile`, source imports | Import-to-package map and clean supported CPU install succeed; GPU optional set is documented; lock and container match. |
| B-07 | Open | Add PR CI for tests and lint; current branch has no workflow files while cached `origin/main` has workflows. | `.github/workflows/` | PR checks run least-privilege and do not publish or auto-write code. |
| B-08 | Open | Add API tests and unify duplicate model-selection/translation setup in `/translate/start` and `/translate`. | `server.py`, `tests/` | Both routes have explicit compatibility behavior and common validation covered by tests. |
| B-09 | Open | Define session storage, worker topology, concurrency and memory limits. Sessions currently live in a module-level dictionary (`server.py:59-60`). | `server.py`, deployment docs | Multi-worker behavior and concurrent model use are tested within documented CPU/RAM/GPU limits. |
| B-10 | Open | Make Docker reproducible and safer: align dependency source, run as non-root, add health check, define cache/resource behavior. | `Dockerfile`, `.dockerignore`, docs | Image build and smoke test succeed; health check and graceful shutdown verified. |

## P2 — fidelity and maintainability

| ID | Status | Item and evidence | Likely files | Acceptance evidence |
|---|---|---|---|---|
| B-12 | Implemented, verify runtime | Ordinal protection appears in `_NUMERAL_PATTERN` (`translator.py:47-75`); PUA bullet normalization and handling appear in `fontmap.py:178-179` and `typesetting.py:820-824,1189-1194`; header mirror and table balancing code/tests exist. Old issue report proposals are partly stale. | Translator, typesetting, font map, regression tests | Current representative PDFs visually confirm each historical defect no longer occurs. |
| B-13 | Verify | Re-run the old visual issue sample if available; determine if table semantics, merged headers, collisions or bullet glyphs still fail. | `docs/issues.md`, current tests, pipeline | Current sample, expected output and screenshots/assertions are attached to backlog entry. |
| B-14 | Verify | Check model precision/quantization, translation quality, resource use and model labels on CPU/GPU. Historical model claims were not reproduced in this audit. | `translator.py`, frontend, docs | Results include model ID/version, hardware, memory, latency and quality examples. |
| B-15 | Open | Build a software/asset bill of materials with versions, hashes, licenses and redistribution terms. | Dependency metadata, `babeldoc/assets/`, docs | Maintainer/legal reviews packages, fonts, OCR/layout models and downloaded weights. |
| B-16 | Open | Remove historical files or consolidate docs only after reference, CLI, packaging and user-data review. | `docs/`, old typesetting variants | Reversible deletion PR has reference search, tests/build and explicit approval. |
| B-11 | Verify then implement | Confirmed at `high_level.py:808-812`: the invalid `for i in len(old_doc)` loop is inside `if translation_config.only_include_translated_page`. The `migrate_toc` call is CLI-only and contained by `try/except` (`:764-769`), so failure is logged rather than aborting translation. | `high_level.py`, focused test | Caller path understood and regression test proves intended behavior after correction. |

## G1-G7 grouped issue register

This register groups the backlog by engineering area. `VERIFIED` means the cited source or supplied environment evidence establishes the narrow claim; `LIKELY` means static evidence points to it but usage/deletion safety is unresolved; `NOT VERIFIED` means a reported runtime, visual or licensing outcome still needs direct evidence. These labels do not mean an item is fixed.

### G1 — dependencies and Docker

| ID | Evidence | Item, evidence and next proof |
|---|---|---|
| B-06 | VERIFIED | Dependency declarations disagree. A fresh install of the afan branch crashed because bitstring 5.0.0 removed `BitStream`, which is imported only at `pdf_creater.py:15`; local commit `5b412d4` pins `bitstring<5` (not pushed). Declare the pin in every dependency source. Supplied `pip show` evidence reports `transformers 4.57.6` required only by `magic-pdf`, and `torch 2.15.0` required only by `magic-pdf`, `torchvision` and `ultralytics`; this docs-only pass did not rerun those commands. `sentencepiece` was reported absent while NLLB works. Declare the libraries used directly by `translator.py:904-912` before removing `magic-pdf` or `ultralytics`, then prove clean CPU/GPU installs. |
| B-10 | VERIFIED | `Dockerfile:14` copies the whole build context and the tracked inventory has no `.dockerignore`. Review required assets first; then prove a reproducible non-root image, health check, cache behavior and smoke test. |
| B-15 | NOT VERIFIED | Complete the license review: AGPL-3.0 is declared, but hosting/source-offer duties and the font/model asset inventory are unresolved. NLLB weight terms are reported as likely non-commercial but were not verified from the governing model license. Record exact font licenses and redistribution terms before deployment. |

### G2 — engine bugs and cleanup

| ID | Evidence | Item, evidence and next proof |
|---|---|---|
| B-17 | VERIFIED | Confirmed at `translator.py:1007-1009`: the NLLB exception handler logs the error and returns the cleaned original text; MarianMT has the same fallback at `:824-826`. Downstream work continues, and an existing output makes the server emit `finish` with “Translation completed!” (`server.py:410-418`). Add an explicit failure/fallback status and tests proving the job cannot be reported as a clean translation success. |
| B-18 | VERIFIED | NLLB chooses CUDA or CPU at runtime (`translator.py:901-907`), but the selected model is an explicit request/default rather than a hardware-aware policy (`translator.py:862,877-886`), and `low_cpu_mem_usage` is not used in the loader (`translator.py:909-947`). Define a tested hardware-aware default and measure `low_cpu_mem_usage` before enabling it. |
| B-11 | VERIFIED | Confirmed at `high_level.py:808-812`: the invalid iteration is inside `if translation_config.only_include_translated_page`. `migrate_toc` is called inside `try/except` (`:764-769`), so the known failure path is contained and CLI-only here; branch reachability and the intended loop remain to be tested. This is P2. |
| B-19 | LIKELY | Static reference search found zero name references for `remove_descent.py`, `t_v5.py`, `typesetting-v1.py`, and `typesetting_v2.py` through `typesetting_v4.py`; root `Procfile` is empty and `README copy.md` has no exact references. Do not call all of `midend/` dead: its main modules are active. Confirm dynamic imports, packaging, CLI/docs use and owner intent before reversible removal. |

### G3 — server safety

| ID | Evidence | Item, evidence and next proof |
|---|---|---|
| B-03 | VERIFIED source; traversal LIKELY | Client filenames reach upload paths and the UI-only 50 MB statement is not an enforced server limit (`server.py:200-206,534-550`; `frontend/index.html:827-832`). The download route builds its path from the `session_id` in the URL with no format check (`server.py:472-495`) and `Content-Disposition` is not quoted (`:505`); path traversal is LIKELY but NOT tested. Add generated names, containment, byte/page/content checks, authorization and expiry tests. |
| B-09 | VERIFIED | Sessions are process-local in a module dictionary (`server.py:59-60`); concurrency, ownership, worker topology and memory limits need an explicit deployment contract and tests. |

### G4 — job lifecycle and progress

| ID | Evidence | Item, evidence and next proof |
|---|---|---|
| B-20 | NOT VERIFIED cause; VERIFIED smoke evidence | The cause of the reported web stall at `0/N` is not verified. A clean guarded script calling `translate()`—not the web path—processed `sample_en.pdf` (2 paragraphs) with 600M on laptop CPU in 131 seconds, peaked at 4,694 MB, recorded 0 swallowed errors and produced both PDFs; its first paragraph finished about 37 seconds after the translate stage began. The first unguarded run failed with a multiprocessing bootstrapping `RuntimeError` and is not valid runtime evidence. Capture web SSE/model-load timing and terminal state to distinguish slow work from a true web hang. |
| B-26 | NOT VERIFIED | Progress counters are reported to look doubled in some output. Preserve an SSE trace and compare emitted current/total values with the engine's unit counts before changing counter aggregation or UI rendering. |
| B-21 | VERIFIED | `POST /translate/start` and the legacy `POST /translate` are distinct routes (`server.py:171,510`) but duplicate model-selection/translation workflow logic. This is not a duplicate decorator for the same path. Define compatibility, share validation/setup and test both. |
| B-04 | LIKELY | Translation work is tied to consumption of the SSE progress route (`server.py:228-294`), so reconnect/disconnect behavior may duplicate or strand work. Reproduce reconnect, cancellation, timeout and error terminal states before redesigning the lifecycle. |

### G5 — UI labels and choices

| ID | Evidence | Item, evidence and next proof |
|---|---|---|
| B-01 | VERIFIED | The server checks `Paragraph` at `server.py:83` before `Translate` at `:86`, so `Translate Paragraphs` can be labelled “Finding Paragraphs”; the frontend also displays that paragraph label (`frontend/index.html:1072`). Fix the ordering/labels with focused API/SSE coverage. The runtime stall investigation is B-20. |
| B-14 | NOT VERIFIED | UI claims for 3.3B/600M accuracy, speed and memory (`frontend/index.html:874-889`) were not benchmarked. Publish hardware-aware choices only with recorded model ID, device, memory, latency, truncation and quality evidence. |

### G6 — CI, tests and maintained documentation

| ID | Evidence | Item, evidence and next proof |
|---|---|---|
| B-02 | VERIFIED | A guarded `translate()` script completed a clean 600M laptop-CPU run of `sample_en.pdf` (2 paragraphs) in 131 seconds, peaked at 4,694 MB, recorded 0 swallowed errors and produced both PDFs. This verifies an engine smoke baseline, not the web path or an automated test. The first run lacked the `if __name__ == "__main__":` guard, failed with a bootstrapping `RuntimeError`, and is not valid evidence. Add a deterministic automated smoke test that avoids downloading a large model. |
| B-07 | VERIFIED | The current branch has no workflow files; locally cached upstream refs contain candidates whose permissions/triggers remain unreviewed. Port only reviewed test/lint/security workflows. |
| B-08 | VERIFIED | API routes and duplicated setup lack focused compatibility, validation and terminal-state coverage. Add tests before unifying the implementations. |
| B-25 | VERIFIED | On Windows, any standalone script that calls `translate()` needs an `if __name__ == "__main__":` guard because the engine spawns child processes. A smoke script without the guard failed with a multiprocessing bootstrapping `RuntimeError`. Document the guarded pattern and keep it in future smoke tooling. |

### G7 — translation and layout quality

| ID | Evidence | Item, evidence and next proof |
|---|---|---|
| B-22 | VERIFIED | Assignment_02 run logs show the 600M model reducing a 746-character input to 130 output characters. The observed truncation is verified from those logs; its root cause is not. “Shielded token found 0” warning construction exists in `translator.py:213`; preserve the exact paragraph and record tokens, generation limits, output and warning before changing generation logic. |
| B-23 | NOT VERIFIED | Code-like text is reported to be sent to the translator. Identify a source PDF paragraph, trace its classification/placeholders, and define expected pass-through or translation behavior before changing filters. |
| B-24 | NOT VERIFIED | Typesetting overlap is reported on `Assignment_02` page 5. Preserve source/output artifacts and record paragraph/box coordinates, font, shaping, line breaks and a visual regression before changing layout logic. |

## Done-item policy

For each item, record the commit/PR, test command and result, and any manual PDF/browser check. An implementation without its required runtime check stays “Implemented, verify runtime.” Do not copy old test totals into this backlog as current results.

## Historical findings reconciled against current code

This section carries forward the consolidated review of the old issue reports and handoffs. The original reports remain in `docs/` for now, but are marked as historical snapshots. Their statements are evidence of what was reported at the time, not current status.

| Old finding or claim | Current source evidence | Reconciled status | Follow-up |
|---|---|---|---|
| Ordinals such as `5th`/`29th` are not protected (`issues.md:122-135,195-203`). | `_NUMERAL_PATTERN` includes ordinal forms/ranges in `babeldoc/translator/translator.py:47-75`; `_protect_numerals` applies it at `:89-117`. | Implemented in source; actual PDF result not verified. The old proposed regex change is stale. | Keep focused regression coverage; later inspect a representative translated PDF. |
| PUA bullets produce tofu boxes (`issues.md:103-118,212-216`). | PUA handling exists in `fontmap.py:178-179` and `typesetting.py:820-824,1189-1194,3245-3332`; tests include PUA bullet cases. | Mitigation and tests exist; visual result not verified. Old blanket cleanup proposal is stale. | Inspect glyphs in rendered output, including after font subsetting. |
| Header banner vector fails to mirror (`issues.md:16-23,156-157`). | `_mirror_header_decorations` is called at `typesetting.py:1708` and defined at `:2181`; a matching regression test exists. | Implemented in source/test; output not freshly rendered. | No reimplementation; verify old PDF sample if available. |
| Table columns are never mirrored (`issues.md:79-99,185-210`). | Current code clusters table cells and balances rows (`typesetting.py:2728,2819,2918-2964`); RTL grid test exists at `tests/test_formatting_anti_collision.py:843`. | Old claim is stale/incomplete; complex merged headers and real document output remain unverified. | Reproduce with the historical table PDF and record cell-order expectations. |
| Duplicate text smudges / coordinate collisions (`issues.md:18,60-75`). | Current anti-collision tests cover duplicate ownership and rendered collisions; typesetting uses vertical clearance (`typesetting.py:1363-1390`). | Mitigations/tests exist; historical sample not re-run. | Reproduce the source PDF before treating as open or fixed. |
| Paragraph merge, acronym duplication and line grouping defects. | Safeguards exist in `paragraph_finder.py:1054-1133`; regression cases cover visual reassembly and acronym deduplication. | Several mitigations appear incorporated; old screenshots unverified. | Treat remaining cases as sample-specific reproduction tasks. |
| Historical reports say 26/26, 31/31 or 35/35 tests pass. | Current checkout contains five test files, including anti-collision tests. No test run was done for this documentation update. | Historical counts are stale and current pass count is not verified. | Report only fresh command, branch/commit, exit status and result. |
| NLLB long-paragraph truncation was solved (`SESSION_HANDOFF_2026_09_07.md:77-86`). | Sentence-splitting and numeral restoration logic/tests exist in translator code and current tests. | Mitigation exists; current model quality is not verified. | Re-test with supported model versions and known long-paragraph fixtures. |
| Add Mutarjim or quantized NLLB (`SESSION_HANDOFF:116-122`). | No Mutarjim engine was verified in current translator source; UI/runtime claims were not executed. | Historical exploration, not an approved requirement. | Research model provenance, license, quality and hardware needs before proposing integration. |
| Full PDF test and scratch cleanup were next steps (`SESSION_HANDOFF:123-127`). | No complete API-to-output test was found; local cache/venv folders exist. | Test gap remains. No deletion should happen before classifying files. | See B-02 and B-16. Preserve user data and environment until reviewed. |
| “Server running” and environment claims (`SESSION_HANDOFF:105-112`). | Dated record refers to a different path/branch; this audit did not start a server. | Not current verification. | Do not repeat as present state without a fresh run. |
| Transformers deprecation and Hugging Face warnings (`CLAUDE_BRIEF_ERRORS_AND_ARCHITECTURE.md:165-173`). | Old logs exist; installed library versions and current runtime warning were not checked. | Not verified and version-sensitive. | Inspect current call site/runtime before changing. |
| Machine paths and old test instructions are current. | Several old docs use `d:\Uni Stuff\...`; active checkout is `D:\transformer\transdoc-afan`. | Stale. | Keep machine-specific paths out of maintained docs; use repository-relative commands. |
| UI promises a 50 MB limit and layout selection works. | UI text at `frontend/index.html:827-832`; handler does not accept a `layout` argument and no server size cap is visible (`server.py:171-206`). | Verified interface mismatch. | Address in B-03 and B-08 with API tests. |
| `migrate_toc` bug. | `for i in len(old_doc)` appears at `high_level.py:812`; the CLI-path call is wrapped by `try/except` at `:764-769`. | Invalid iteration if that branch runs, but the call-site failure is logged and contained; reachability is not verified. | B-11 (P2): understand the caller and add a targeted test before correction. |
| Progress may end early or hang. | Async callback/worker lifecycle is in `high_level.py:415-444,779-784` and `progress_monitor.py:139-147,250-259`. | Lifecycle needs tests; the reported stall cause is unknown. | B-20 and B-04: collect logs/SSE trace; do not guess cause. |
| Upload, session, CORS and dependency risks. | Upload path uses client name (`server.py:200-206,534-550`), in-memory sessions (`:59-60`), permissive CORS (`:40-47`), divergent dependency declarations. | Verified code-level concerns. | B-03, B-06, B-09 and the security guide. |
| CI/security workflows should be restored from `origin/main`. | Cached upstream ref has test, lint and CodeQL workflows; current branch does not. Git histories have no common base. | Workflow availability verified from local refs; suitability not verified. | Port selected workflows after reviewing permissions, triggers and dependency setup; avoid blind history merge or publish workflow. |
| AGPL and attribution are settled by the main project license. | Project declares AGPL-3.0 and has upstream attribution, but asset metadata references fonts/models without a complete verified inventory. | Project license declaration verified; legal deployment and asset rights unresolved. | B-05/B-15 and `SECURITY_AND_DEPLOYMENT.md`. |

## Documentation map and consolidation plan

The current maintained set and its purpose:

1. `docs/PROJECT_GUIDE.md` — starting map and beginner learning order.
2. `docs/ENGINEERING_BACKLOG.md` — this file; sole current task/status list plus reconciliation of old issue reports.
3. `docs/TESTING_AND_OPERATIONS.md` — test inventory and commands; results are only current when freshly recorded.
4. `docs/SECURITY_AND_DEPLOYMENT.md` — security boundaries and deployment decisions.
5. `docs/CHANGELOG.md` — completed changes by release, not planned work.
6. `docs/ARCHITECTURE.md` and `docs/ENGINES_AND_SERVER.md` — detailed pipeline and server explanations.
7. `docs/RTL_AND_TYPESETTING.md` and `docs/FINANCIAL_TRANSLATION_RULES.md` — specialist technical references; keep separate because folding them into the overview would make it too large.

The four older reports (`issues.md`, `CLAUDE_BRIEF_ERRORS_AND_ARCHITECTURE.md`, `SESSION_HANDOFF_2026_09_07.md`, `WALKTHROUGH_TECHNICAL_ENHANCEMENTS.md`) contain historical screenshots, logs and implementation detail. They have been marked historical, not deleted. A later tidy-up may move them to `docs/archive/` after checking for external links and extracting any unique evidence; do not discard them until that review is complete. `AGENTS.md` now points to the maintained project guide and no longer carries a duplicated tree or test-count snapshot.

The README now links to the maintained testing guide once and uses `--openai` in its CLI example. Do not report old test counts or old “server running” statements as current status.

## Corrections from the independent review

These source-based corrections have been reconciled into the maintained documents or the grouped register above. Runtime outcomes remain unverified where explicitly labelled.

- The README CLI example now includes `--openai`; the current parser requires OpenAI selection (`babeldoc/main.py:378-413,464-469`). Do not describe the CLI as a local NLLB/Marian path without verifying an actual parser option and execution path.
- `ARCHITECTURE.md` now states the 13 entries in `TRANSLATE_STAGES` (`high_level.py:70-84`).
- `ENGINES_AND_SERVER.md` now lists the event names from `server.py:245-428`: `init`, `stage_summary`, `progress_start`, `progress_update`, `progress_end`, `finish`, `error`.
- The NLLB model is loaded on first use (`translator.py:905-926`), so loading/download time may explain an apparent stall. This remains a hypothesis; B-20 requires timestamps to distinguish it from slow inference or errors.
- Job work occurs while the SSE progress route is consumed (`server.py:228-294`). Duplicate work on reconnect needs reproduction before choosing a background-task/status-endpoint design; add that case to B-04.
- Translator fallback paths return cleaned source text (`translator.py:824-826,1007-1009`), while an output file makes the server report completion (`server.py:410-418`); this is tracked as B-17.
- Docker copies the full context (`Dockerfile:14`); no `.dockerignore` exists in the tracked inventory. Review required assets before adding one under B-10.
- Explain the difference between package version, API version and product branding rather than silently unifying them.

## Beginner reading exercises

These are code-reading tasks; they do not modify the repository:

1. Find `TRANSLATE_STAGES` in `high_level.py`; follow the Paragraph stage into `paragraph_finder.py` and identify how completion is reported.
2. Trace upload in `server.py` into the browser EventSource in `frontend/index.html`. Note where the upload is stored and which ID is used to access progress/downloads.
3. Compare one historical finding above (ordinals or PUA bullets) with the current test. Explain what the unit test proves and what a real PDF render would still need to verify.
4. Compare `Dockerfile`, `requirements.txt`, `pyproject.toml` and `uv.lock`; list disagreements without changing them.

Suggested engineering sequence: create the guarded smoke script first; fix the stage label and add API tests (G5/G6); address server safety (G3); make the job lifecycle reliable (G4); reconcile dependencies (G1); prepare and verify deployment; then work through translation and layout quality (G7).
