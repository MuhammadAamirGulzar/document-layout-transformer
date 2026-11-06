# Engineering backlog

This is the single current planning and reconciliation document. It contains the prioritized work list, the old-document findings checked against the current source, and the learning/documentation path. The original static review is dated 2026-11-04; the maintainer-supplied G3/G4 and Git updates below are dated 2026-11-05. “Open” means the work or verification remains; it does not claim a reproduced runtime defect. Keep entries small and update evidence/status when work is done.

**Statuses:** Open = not completed; Verify = collect evidence before deciding whether code needs changing; Implemented, verify runtime = matching code/tests exist but real output is not confirmed. No item below is marked complete based only on an old document.

Evidence labels: VERIFIED = the cited source lines were read or the output was shown; it does NOT mean the code was executed. LIKELY = static evidence points to it. NOT VERIFIED = needs direct evidence.

## Git handoff — 2026-11-05

- **VERIFIED — maintainer-supplied Git state:** `cleanup-baseline` was pushed as a NEW branch to the owner's public repository on 2026-11-05, tip `8c727f1`. `main` and `afan` were not touched. The commit email is the GitHub noreply address.
- **VERIFIED — history and contribution rule:** `main` and `afan` have unrelated histories. Never merge them; a future PR goes into `afan`.

## P0 — before public or production use

| ID | Status | Item and evidence | Likely files | Acceptance evidence |
|---|---|---|---|---|
| B-01 | Open; implementation VERIFIED; browser NOT VERIFIED | The stage-label fix renames `server.py` stage-label keys to real engine names and sorts fallback matching longest-key-first. `tests/test_stage_labels.py` adds 17 tests. The maintainer's terminal run at that stage reported 94 passed + 106 subtests (VERIFIED). Browser check is still pending; keep this item open. The runtime investigation is B-20. | `server.py`, `frontend/index.html`, API tests | Record the browser's displayed stage label, its initial 0/N behavior and when it advances; add focused API/SSE coverage. |
| B-02 | Open; smoke baseline VERIFIED | A clean laptop-CPU smoke run used the 600M model with `sample_en.pdf` (2 paragraphs) through a guarded script calling `translate()`, not the web path. It took 131 seconds, peaked at 4,694 MB, recorded 0 swallowed translation errors and produced both PDFs. Add a repeatable automated smoke test; the current tests mostly exercise engine components. | `tests/`, `high_level.py`, `pdf_creater.py` | Deterministic fixture test produces readable mono and dual PDFs with expected pages/text, without downloading a large model during the test. |
| B-03 | Done; VERIFIED by tests | G3 server safety is committed: fixed upload file name (`b908064`), upload size cap 50 MB plus `%PDF-` check (`b048202`), session-id validation on download (`0d87c66`), CORS removed (`0d9fefb`), download filename header (`34cff55`), fake `/translate/stream` entry removed (`0a935c1`). | `server.py`, API tests | All six fixes VERIFIED by tests. UUID check is not authentication. |
| B-04 | Done for the new route; VERIFIED | G4 batch 1 (`3ed2c50`): background jobs, page polling of `GET /translate/status/{id}`, explicit `POST /translate/run/{id}`, duplicate run returns 409, one engine job at a time per server process. G4 batch 2 (`8c727f1`) adds cooperative cancellation, timeout, retention and worker-stop waiting; see G4 below. | `server.py`, `frontend/index.html`, `high_level.py` | VERIFIED: 199 passed + 106 subtests, 600M 2-paragraph smoke 63 s / 0 errors, browser cancel working. VERIFIED by the user's report: normal finish plus download of both PDFs after batch 2. B-21 remains open for the old route. |
| B-05 | Open | Confirm repo owner/contribution target and review AGPL plus each font/model/data asset license before deployment. | `LICENSE`, `pyproject.toml`, asset metadata, repository settings | Supervisor/owner confirms rights and asset manifest/source-offer plan. |

## P1 — engineering foundations

| ID | Status | Item and evidence | Likely files | Acceptance evidence |
|---|---|---|---|---|
| B-06 | Open; install failure VERIFIED | Reconcile dependency declarations. `requirements.txt`, `pyproject.toml` and `uv.lock` differ; RapidOCR is `==1.2.3` in requirements versus `>=1.4.4` in project metadata/lock. A fresh install of the afan branch crashed because bitstring 5.0.0 removed `BitStream`, which is imported only at `pdf_creater.py:15`; the recorded fix pins `bitstring<5`. Declare the pin in every dependency source. Map actual imports to direct dependencies before removing packages; web, Arabic and local-model paths need explicit coverage. | `requirements.txt`, `pyproject.toml`, `uv.lock`, `Dockerfile`, source imports | Import-to-package map and clean supported CPU install succeed; GPU optional set is documented; lock and container match. |
| B-07 | Open | Add PR CI for tests and lint; current branch has no workflow files while cached `origin/main` has workflows. | `.github/workflows/` | PR checks run least-privilege and do not publish or auto-write code. |
| B-08 | Open; test approach VERIFIED | `TestClient` is broken here with Starlette 0.27.0 + httpx 0.28.1, so tests use `httpx.ASGITransport` inside `asyncio.run`. The old `POST /translate` bypasses job controls (B-21 remains open). | `server.py`, `tests/` | VERIFIED test approach; old-route reconciliation remains open. |
| B-09 | Open; process boundary VERIFIED | One engine job at a time per server process; the semaphore works within one process only. Sessions and tasks are in memory and lost on restart. | `server.py`, deployment docs | Define session storage, worker topology, concurrency and memory limits. |
| B-10 | Open | Make Docker reproducible and safer: align dependency source, run as non-root, add health check, define cache/resource behavior. | `Dockerfile`, `.dockerignore`, docs | Image build and smoke test succeed; health check and graceful shutdown verified. |

## P2 — fidelity and maintainability

| ID | Status | Item and evidence | Likely files | Acceptance evidence |
|---|---|---|---|---|
| B-12 | Implemented, verify runtime | Ordinal protection appears in `_NUMERAL_PATTERN` (`translator.py:47-75`); PUA bullet normalization and handling appear in `fontmap.py:178-179` and `typesetting.py:820-824,1189-1194`; header mirror and table balancing code/tests exist. Old issue report proposals are partly stale. | Translator, typesetting, font map, regression tests | Current representative PDFs visually confirm each historical defect no longer occurs. |
| B-13 | Verify | Re-run the old visual issue sample if available; determine if table semantics, merged headers, collisions or bullet glyphs still fail. | `docs/issues.md`, current tests, pipeline | Current sample, expected output and screenshots/assertions are attached to backlog entry. |
| B-14 | Open; T4 measurement VERIFIED; true memory need NOT VERIFIED | Colab T4 3.3B run used CUDA and logged BitsAndBytes 8-bit quantization with no float16 fallback line. `nvidia-smi` showed 9,451 MiB GPU memory used, including PyTorch cache. The UI claim “~3.5 GB VRAM” is not supported by this measurement; true required VRAM is NOT VERIFIED. See Benchmark for timing and separate engine/system-memory observations. | `translator.py`, frontend, docs | Correct model labels using measured device/precision evidence; distinguish allocated/reserved GPU memory, engine peak and sampled system RAM before publishing requirements. |
| B-15 | Open | Build a software/asset bill of materials with versions, hashes, licenses and redistribution terms. | Dependency metadata, `babeldoc/assets/`, docs | Maintainer/legal reviews packages, fonts, OCR/layout models and downloaded weights. |
| B-16 | Open | Remove historical files or consolidate docs only after reference, CLI, packaging and user-data review. | `docs/`, old typesetting variants | Reversible deletion PR has reference search, tests/build and explicit approval. |
| B-11 | Open; source VERIFIED; branch failure NOT VERIFIED | `migrate_toc` is called on the web path: `web-run1.log` contains “No TOC found in the original PDF, skipping migration.” The invalid `for i in len(old_doc)` loop is inside `if translation_config.only_include_translated_page` (`high_level.py:808-812`); that branch is not reached by the current server configuration. The call is contained by `try/except` (`:764-769`). Keep P2. | `high_level.py`, focused test | Exercise a PDF with a TOC and the translated-page-only option; prove the intended loop with a regression test. |

## G1-G7 grouped issue register

This register groups the backlog by engineering area. `VERIFIED` means the cited source or supplied environment evidence establishes the narrow claim; `LIKELY` means static evidence points to it but usage/deletion safety is unresolved; `NOT VERIFIED` means a reported runtime, visual or licensing outcome still needs direct evidence. These labels do not mean an item is fixed.

### G1 — dependencies and Docker

| ID | Evidence | Item, evidence and next proof |
|---|---|---|
| B-06 | VERIFIED | Dependency declarations disagree. A fresh install of the afan branch crashed because bitstring 5.0.0 removed `BitStream`, which is imported only at `pdf_creater.py:15`; the recorded fix pins `bitstring<5`. Declare the pin in every dependency source. Supplied `pip show` evidence reports `transformers 4.57.6` required only by `magic-pdf`, and `torch 2.15.0` required only by `magic-pdf`, `torchvision` and `ultralytics`; this docs-only pass did not rerun those commands. `sentencepiece` was reported absent while NLLB works. Declare the libraries used directly by `translator.py:904-912` before removing `magic-pdf` or `ultralytics`, then prove clean CPU/GPU installs. |
| B-10 | VERIFIED | `Dockerfile:14` copies the whole build context and the tracked inventory has no `.dockerignore`. Review required assets first; then prove a reproducible non-root image, health check, cache behavior and smoke test. |
| B-15 | NOT VERIFIED | Complete the license review: AGPL-3.0 is declared, but hosting/source-offer duties and the font/model asset inventory are unresolved. NLLB weight terms are reported as likely non-commercial but were not verified from the governing model license. Record exact font licenses and redistribution terms before deployment. |

#### B-06 installation and platform evidence

- **NOT VERIFIED — reported account restriction:** Kaggle Internet was off because the account was not phone-verified. Record this as a platform/account restriction, not a project bug.
- **NOT VERIFIED — reported Colab install failure:** on Python 3.13, `rapidocr-onnxruntime>=1.4.4` had no compatible build, so `pip install -e .` failed even though `pyproject.toml:7` permits `>=3.12,<3.14`. Decide whether to narrow `requires-python` or change the dependency constraint after checking supported builds.
- **NOT VERIFIED — reported resolver outcome:** installing `transformers==4.57.6` resolved `huggingface-hub` to 0.36.2 in Colab. Preserve the resolver output; do not assume that exact Hub version is required in every environment.
- **NOT VERIFIED — completeness of direct dependencies:** the blanket claim that no manifest declares all six packages conflicts with source. `requirements.txt:15-19` already declares `arabic-reshaper`, `python-bidi` and `torch`; these are absent from the project runtime list. `transformers`, `accelerate` and `fonttools` have no direct declaration in either runtime manifest. Reconcile the actual imports and supported install paths before removing transitive providers.
- **LIKELY — stale lock metadata:** `uv.lock:125` still records `bitstring>=4.3.0`, while both runtime manifests now include `<5`; the locked package is 4.3.1 (`uv.lock:266-267`). The bitstring pin likely did not refresh lock metadata. Do not regenerate it during this docs task.
- **VERIFIED — B-08 API-test compatibility:** `TestClient` is broken here with Starlette 0.27.0 + httpx 0.28.1, so tests use `httpx.ASGITransport` inside `asyncio.run`.

### G2 — engine bugs and cleanup

| ID | Evidence | Item, evidence and next proof |
|---|---|---|
| B-17 | VERIFIED three error-swallowing locations; PDF outcome NOT VERIFIED | Location 1: MarianMT logs an error and returns cleaned source text (`translator.py:824-826`). Location 2: NLLB logs “Error during NLLB translation” and returns cleaned source text (`translator.py:1007-1009`). Location 3: the banking-code guard raises `AssertionError` for missing `IIBBAANN` after `translator.py:458` calls `_restore_numerals` (`:207`), via `_translate_with_line_preservation` (`il_translator.py:507`, called at `:1535`). The exception is caught in `translate_paragraph`, logged with `logger.exception` as “Error translating paragraph”, and the function returns with the comment “ignore error and continue”. No translation is applied to that paragraph, so it is LIKELY left as source English. What the output PDF shows for that paragraph is NOT VERIFIED. VERIFIED supplied log counts: “Error translating paragraph” appears 0 times in the 600M web run and 1 time in the Colab 3.3B run, so this path depends on model output (LIKELY). The audit counter matches only location 2's NLLB message (`translator.py:1008`), misses location 3 and reported `ERRORS 0`. Show skipped/untranslated counts to the user under B-35. |
| B-18 | VERIFIED | NLLB chooses CUDA or CPU at runtime (`translator.py:901-907`), but the selected model is an explicit request/default rather than a hardware-aware policy (`translator.py:862,877-886`), and `low_cpu_mem_usage` is not used in the loader (`translator.py:909-947`). Define a tested hardware-aware default and measure `low_cpu_mem_usage` before enabling it. |
| B-11 | VERIFIED source; branch failure NOT VERIFIED | `migrate_toc` is called by the web pipeline, as the “No TOC found... skipping migration” line in `web-run1.log` demonstrates. Only the invalid loop is conditional on `only_include_translated_page` (`high_level.py:808-812`), which the current server does not enable. The call is wrapped in `try/except` (`:764-769`). Keep P2; test the conditional branch separately. |
| B-19 | LIKELY | Static reference search found zero name references for `remove_descent.py`, `t_v5.py`, `typesetting-v1.py`, and `typesetting_v2.py` through `typesetting_v4.py`; root `Procfile` is empty and `README copy.md` has no exact references. Do not call all of `midend/` dead: its main modules are active. Confirm dynamic imports, packaging, CLI/docs use and owner intent before reversible removal. |

### G3 — server safety

| ID | Evidence | Item, evidence and next proof |
|---|---|---|
| B-03 | VERIFIED by tests; Done | G3 server safety is committed: fixed upload file name (`b908064`), upload size cap 50 MB plus `%PDF-` check (`b048202`), session-id validation on download (`0d87c66`), CORS removed (`0d9fefb`), download filename header (`34cff55`), fake `/translate/stream` entry removed (`0a935c1`). UUID check is not authentication. |
| B-09 | VERIFIED; Open | One engine job at a time per server process; the semaphore works within one process only. Sessions and tasks are in memory and lost on restart. |

**NOT VERIFIED — upload spooling:** Starlette 0.27 probably spools the whole upload before the size cap runs, so the deployment proxy must also cap body size.

### G4 — job lifecycle and progress

| ID | Evidence | Item, evidence and next proof |
|---|---|---|
| B-20 | VERIFIED web-run evidence; reconnect/timing NOT VERIFIED | First real web-path run: 14-page QCB IBAN document, NLLB 600M on laptop CPU, 1297 s, peak 3652 MB; one `POST /translate/start`, one progress connection, one finish, no “Error during NLLB” lines, download 200 OK (VERIFIED from `audit\web-run1.log`, per maintainer). This run completed. Reconnect behavior and first-progress timing remain NOT VERIFIED. Earlier guarded engine-only smoke: 2 paragraphs, 131 s, 4694 MB, 0 swallowed errors, both PDFs, first paragraph about 37 s after translation began. That timing does not establish browser timing. The first unguarded smoke failed with a bootstrapping `RuntimeError` and is not valid benchmark evidence. |
| B-26 | Fixed and confirmed in a web run (a68b370) | The recorded web run confirmed that overall progress stopped dropping after removal of the duplicate PDF write. See B-26 evidence below. |
| B-21 | VERIFIED; Open | The old `POST /translate` bypasses job controls. |
| B-04 | VERIFIED; Done for the new route | G4 batch 1 (`3ed2c50`): jobs run in the background; the page polls `GET /translate/status/{id}`; explicit `POST /translate/run/{id}`; duplicate run returns 409; one engine job at a time per server process. G4 batch 2 (`8c727f1`) adds the controls below. |
| B-34 | Decided: keep the cache off on the web path; no code change | The web path builds translators with `ignore_cache=True`, so it is not affected now. Before enabling the cache: stop caching fallback results, and read the key. See the B-34 decision below. |
| B-35 | Built; validation pending | Built and committed (841e0db). 212 passed + 106 subtests; real 600M smoke OK (UNTRANSLATED 0). A real forced-failure run and a browser check of the note are NOT VERIFIED. Not done until those are seen. Original requirement (kept): Show the user a count of skipped or untranslated paragraphs during progress and at completion. Include all three B-17 error-swallowing locations, especially 'Error translating paragraph', rather than relying on the smoke script's single-message ERRORS counter. Keep expected pass-through identifiers distinct from skipped translations. Acceptance: each error path updates the appropriate count and the user can see the final totals. Gap: the count currently appears at finish only, not during progress. |
| B-37 | Open, not started | **Page loses its job after refresh or in a new tab.** LIKELY: the page keeps the session ID only in a JavaScript variable. The server job is expected to keep running (NOT VERIFIED in a browser). A new job queues behind it because one engine job runs at a time per process. Cancel requires the ID, so the page cannot reach it after losing the ID. Planned fix: remember the ID in the URL hash and re-attach polling on load. |

**B-26 evidence:**

- **VERIFIED from a recorded real run** (2-paragraph file, 600M, 174 s, 67 events): the last four stages (Add Fonts, Generate drawing instructions, Subset font, Save PDF) run twice. Overall progress falls back to 99.4, 98.0, 99.1 and 93.7 before ending at 100.
- No stage showed current above total, so stage counters are not doubled.
- VERIFIED from code: `high_level.py` called `pdf_creater.write(translation_config)` twice unconditionally in the normal path (old lines 1242 and 1249). The second call is removed. The parse-only and first-page watermark helper writes are unchanged.
- VERIFIED: 225 passed + 106 subtests. The 600M smoke on the 2-paragraph file took 79 s of engine time, with `ERRORS 0` and `UNTRANSLATED 0`. Extracted text in both mono and dual PDFs matched the earlier double-write run (text only, 1 page; layout not compared).
- **VERIFIED from a recorded web run** (2-paragraph file, 600M, 2026-11-06): `overall_progress` drops 4 -> 0; events 67 -> 51; final-stage events (Add Fonts / Generate drawing instructions / Subset font / Save PDF) 6/8/8/10 -> 3/4/4/5; 0 "Error translating paragraph" lines in the server log.
- The duplicate pass in the old recording spanned about 16 s. Total elapsed 174 s -> 130 s is NOT a like-for-like comparison because the model-load wait differed (63 s before, 90 s now).

**Model-loading hint (shown at 0/2 VERIFIED by the user's report; hiding at 1/N NOT VERIFIED):**

- Built and committed (7f905b1), page-only.
- It shows "Loading the translation model. First use can take several minutes. This is not stuck." while the Translate Paragraphs stage is active and 0 paragraphs are done. It hides on the first finished paragraph or any other event.
- Checked by static tests and a node run on a fake page. VERIFIED by the user's report: the hint was seen at 0/2. The hint disappearing at 1/N is NOT VERIFIED (no screenshot).
- It cannot tell model loading from a slow first paragraph. A real loading flag waits for B-18.

**B-34 decision:**

- VERIFIED from code: `BaseTranslator.translate()` (`translator.py:432-463`) calls `self.cache.set(text, translation)` after `do_translate` unless `ignore_cache` is set.
- NLLB and Marian `do_translate` return the source text on error, so that fallback would be cached and reused as if translated.
- The banking-code AssertionError is raised in `_restore_numerals` before `cache.set`, so it is not cached.
- The cache key is engine name + engine params JSON + original text (`cache.py:33-47`). Whether the source and target languages are in the params is NOT VERIFIED (`translator.py:396-412` not read).
- The web path builds translators with `ignore_cache=True`, so it is not affected now.
- Before enabling the cache: stop caching fallback results, and read the key.

**B-35 limits:**

- (a) The two engine exits and the page note were checked by code shape and static text only.
- (b) B-30 (silent English with no error) is not counted.
- (c) The unit is text blocks (translate calls), not paragraphs.
- (d) The counter has no lock, so it can undercount.
- (e) A block can rarely count twice.
- (f) Only the new job route reports it; the old `POST /translate` ignores it (B-21).
- (g) The count is set at finish only, so it shows 0 while running.

**VERIFIED — G4 batch 2 (`8c727f1`):** cooperative cancel via `POST /translate/cancel/{id}`; `JOB_TIMEOUT_SECONDS` default 3600; `SESSION_TTL_SECONDS` default 86400 with a 60 s sweep; the engine slot is released only after the worker really stops. Cancel and timeout are cooperative, not a hard kill.

**VERIFIED - batch-2 validation:** 199 passed + 106 subtests; a 600M smoke run on the 2-paragraph file took 63 s with 0 errors; a browser cancel was seen working. **VERIFIED by the user's report:** normal finish plus download of both PDFs after batch 2.

**VERIFIED - still open:** B-35, model-loading hint (shown at 0/2 VERIFIED by the user's report; hiding at 1/N NOT VERIFIED), B-21, B-17, B-18 and B-37. B-26: fixed, confirmed in a web run. B-36: fixed, browser look NOT VERIFIED. B-34 is decided: keep the cache off on the web path; no code change.

**B-20 — VERIFIED supplied Colab timing:** the 14-page 3.3B smoke run used `audit\smoke.py` calling `translate()` directly. First-use progress stayed at `0/N` for about nine minutes: the first paragraph started at 11:33:39 and the first finished at 11:42:15 (about 516 s, including weight download and shard loading). This wait occurred on a T4 GPU; the 600M web run had no comparable wait. This is engine-smoke evidence, not a 3.3B browser check. Web reconnect behavior and precise browser first-progress timing remain NOT VERIFIED. See Benchmark for the full timeline.

### G5 — UI labels and choices

| ID | Evidence | Item, evidence and next proof |
|---|---|---|
| B-01 | VERIFIED implementation/tests; browser NOT VERIFIED | Implemented stage-label fix: real engine stage-name keys and longest-key-first fallback. `tests/test_stage_labels.py` contains 17 tests; full suite at that stage: 94 passed + 106 subtests, VERIFIED by the maintainer's terminal run. Keep open until the browser label/progress check is recorded. API/SSE integration coverage remains pending. |
| B-36 | Done (66842b1) | Wrong stage row lights up. Before the fix, "Finding Paragraphs" showed Processing while "Translating Content" stayed Pending. The page now finds the row by its ID `stage-<engine stage name>` and falls back to text matching only when no row has that ID. VERIFIED: 223 passed + 106 subtests, including a Node run of the real function against a fake row list. NOT VERIFIED: how it looks in a real browser (no screenshot yet). |
| B-14 | VERIFIED T4 observation; true requirements NOT VERIFIED | The Colab T4 3.3B run logged CUDA, 8-bit True and BitsAndBytes enablement with no float16 fallback line; `nvidia-smi` reported 9,451 MiB used, including PyTorch cache. This measurement does not support the UI's “~3.5 GB VRAM” claim (`frontend/index.html:874-889`). True required GPU memory, general accuracy/speed claims and portable memory requirements remain NOT VERIFIED. |

### G6 — CI, tests and maintained documentation

| ID | Evidence | Item, evidence and next proof |
|---|---|---|
| B-02 | VERIFIED | A guarded `translate()` script completed a clean 600M laptop-CPU run of `sample_en.pdf` (2 paragraphs) in 131 seconds, peaked at 4,694 MB, recorded 0 swallowed errors and produced both PDFs. This verifies an engine smoke baseline, not the web path or an automated test. The first run lacked the `if __name__ == "__main__":` guard, failed with a bootstrapping `RuntimeError`, and is not valid evidence. Add a deterministic automated smoke test that avoids downloading a large model. |
| B-07 | VERIFIED | The current branch has no workflow files; locally cached upstream refs contain candidates whose permissions/triggers remain unreviewed. Port only reviewed test/lint/security workflows. |
| B-08 | VERIFIED test approach; Open | `TestClient` is broken here with Starlette 0.27.0 + httpx 0.28.1, so tests use `httpx.ASGITransport` inside `asyncio.run`. The old `POST /translate` bypasses job controls; B-21 remains open. |
| B-25 | VERIFIED | On Windows, any standalone script that calls `translate()` needs an `if __name__ == "__main__":` guard because the engine spawns child processes. A smoke script without the guard failed with a multiprocessing bootstrapping `RuntimeError`. Document the guarded pattern and keep it in future smoke tooling. |

### G7 — translation and layout quality

| ID | Evidence | Item, evidence and next proof |
|---|---|---|
| B-22 | VERIFIED length observations; truncation cause NOT VERIFIED | Assignment_02 logs show a 746-character input becoming 130 output characters; the length reduction is VERIFIED, while the cause is NOT VERIFIED. In the 14-page benchmark, among inputs with 150 or more characters, 600M web had 20 paragraphs and 2 outputs below 60% of input length (255 to 113; 253 to 88); 3.3B Colab had 20 and 1 below that threshold (253 to 100), VERIFIED supplied log counts. The 253-character input is LIKELY the same paragraph in both runs; identity is not confirmed. Arabic often uses fewer characters than English, so this ratio is an indicator, not proof of truncation. “Shielded token found 0” warning construction exists at `translator.py:213`; preserve exact input/output spans, tokens, generation limits and warnings before changing generation logic. |
| B-23 | NOT VERIFIED | Code-like text is reported to be sent to the translator. Identify a source PDF paragraph, trace its classification/placeholders, and define expected pass-through or translation behavior before changing filters. |
| B-24 | NOT VERIFIED | Typesetting overlap is reported on `Assignment_02` page 5. Preserve source/output artifacts and record paragraph/box coordinates, font, shaping, line breaks and a visual regression before changing layout logic. |
| B-27 | NOT VERIFIED cause | Reported 14-page benchmark finding, printed p.1317: Tel and Fax numbers are dropped while their labels and the email remain. Trace source extraction, paragraph representation, translation and drawing to locate the loss; preserve both PDFs. |
| B-28 | NOT VERIFIED cause | Reported truncation in the B-22 family: p.1306 stops after “SWIFT, the” although the source ends “the registrar for IBAN”; p.1310 section 3.1(c) loses its second half. Compare extracted source, model input/output and rendered text before assigning the cause to the model or layout. |
| B-29 | NOT VERIFIED cause | Reported symbol corruption: conversion-table letters on p.1314 are translated (`I` becomes the word for “me”; `X` becomes a letter name), as is the `X` mark on p.1310. Define context-specific symbol preservation and regression examples before changing pass-through rules. |
| B-30 | VERIFIED rough count/error-log counts; PDF outcome NOT VERIFIED | Reported unchanged English: “DOCUMENT” (p.1304), “(25 Characters for Qatar)” (p.1308), and INCOMING/OUTGOING (p.1310), without a logged error attributed to those spans. Rough mono-PDF Latin-word counts (4+ letters) were 170 for 3.3B versus 171 for 600M web, with nearly identical per-page profiles (VERIFIED supplied counts); model size is LIKELY insufficient to explain leftover English, and legitimate English identifiers limit this check. Separately, the Colab traceback identifies B-17's third error-swallowing location: missing `IIBBAANN` from an `abandon` header-like source, “IIBBAANN SSttaannddaarrddss VVeerr.. 22..00 -- QQAATTAARR”. The exception is caught in `translate_paragraph`, logged with `logger.exception` as “Error translating paragraph”, and the function returns with the comment “ignore error and continue”. No translation is applied to that paragraph, so it is LIKELY left as source English. What the output PDF shows for that paragraph is NOT VERIFIED. VERIFIED supplied log counts: “Error translating paragraph” appears 0 times in the 600M web run and 1 time in the Colab 3.3B run, so this path depends on model output (LIKELY). The `ERRORS` counter misses this location. Do not attribute the other unchanged spans to this guard without paragraph-level evidence. |
| B-31 | NOT VERIFIED cause | Reported structure loss: list marker “1.” on p.1306 and section number “5.” in a heading and TOC disappear. Trace markers through paragraph grouping, translation placeholders and reconstruction; preserve printed-page and PDF-page references. |
| B-32 | NOT VERIFIED cause | Reported layout defects: cover titles shrink; the contact box overlaps the name on p.1317; gray bars and header lines are not mirrored. Preserve screenshots and box/vector coordinates. Existing layout mitigations do not establish these sample-specific outcomes as fixed. |
| B-33 | NOT VERIFIED; native review needed | Reported meaning concerns: p.1310 section 3.1(b) Arabic appears unrelated to the English; “standardized” appears as “evaluated” and “Character” as “personality”. Ask a native Arabic reviewer to compare exact source/output spans. Model attribution and causes remain NOT VERIFIED. |

## Benchmark

`sample_en.pdf`, `small.pdf` and `qcb14.pdf` are the same 14-page file.

### G4 batch-2 smoke and browser evidence

- **VERIFIED:** batch 2 is committed as `8c727f1`; tests reported 199 passed + 106 subtests.
- **VERIFIED:** a 600M smoke run on the 2-paragraph file took 63 s with 0 errors.
- **VERIFIED:** a browser cancel was seen working.
- **VERIFIED by the user's report:** normal finish plus download of both PDFs after batch 2.

### Recorded browser run

- **VERIFIED by the user's report:** normal finish plus download of both PDFs after batch 2.
- The hint was seen at 0/2. The wait before the first paragraph was 63 s (first 0/N at 32 s, first 1/N at 95 s).
- **NOT VERIFIED:** the hint disappearing at 1/N (no screenshot).
- **NOT VERIFIED:** the amber untranslated note (the count was 0, so it stayed hidden).

### Earlier 14-page web benchmark

**VERIFIED — supplied web-run evidence:** first real web-path benchmark:

| Field | Recorded value |
|---|---|
| Source | `sample_en.pdf`, 14 pages; printed pages 1304-1317, QCB IBAN document |
| Model | `facebook/nllb-200-distilled-600M` |
| Hardware | Laptop CPU |
| Duration / peak memory | 1297 s / 3652 MB |
| Requests and completion | One `POST /translate/start`, one progress connection, one finish; download 200 OK |
| Translation exception log | No “Error during NLLB” lines; this does not prove every paragraph was translated correctly |
| Log | `D:\transformer\audit\web-run1.log` (`audit\web-run1.log` relative to the workspace) |
| Expected problem pages | Printed pp.1304, 1306, 1308, 1310, 1314 and 1317; see B-27 through B-33 |

### Colab T4 NLLB-3.3B run

**VERIFIED — maintainer-supplied run results, recorded 2026-11-05:** the same 14-page benchmark was processed through `audit\smoke.py` calling `translate()` directly, rather than the web path.

| Field | Recorded value |
|---|---|
| Model / device | NLLB-3.3B, `cuda`, 8-bit True |
| Quantization evidence | “Enabling BitsAndBytes 8-bit quantization” logged; no float16 fallback line |
| Completion | 1,619 s total; `ERRORS 0`; 427 translate calls; both PDFs written |
| Engine-reported peak | 8,990.65 MB; what the engine measures is NOT VERIFIED |
| Sampled system RAM | About 5 GB; a sample is not a verified system peak |
| GPU memory observation | `nvidia-smi`: 9,451 MiB used, including PyTorch cache; true required VRAM is NOT VERIFIED |
| Rough leftover-English indicator | Latin words of 4+ letters in the mono PDF: reported count 170 versus 171 for 600M web, with nearly identical per-page profiles; legitimate English identifiers limit this check |

**VERIFIED — approximate timeline from supplied log timestamps:**

| Interval | Duration and evidence |
|---|---|
| Before translation started | About 83 s |
| First-paragraph start to first finished paragraph | 11:33:39 to 11:42:15, about 516 s; includes weights download (6 min 52 s) and shard loading (1 min 15 s) |
| Translation interval after the initial wait | 991 s for 395 traced paragraphs, about 2.5 s per traced paragraph; traced paragraphs and the 427 translate calls are different counters |
| Typesetting and saving | About 29 s |

The first-use `0/N` wait was about nine minutes even on the T4 GPU; the 600M web run had no such wait. These timings include first-use download/loading and do not establish warm-cache throughput or browser progress behavior.

**VERIFIED — banking-code guard traceback, supplied log analysis:** `translator.py:458` calls `_restore_numerals` (`:207`), which raised `AssertionError` because expected banking code `IIBBAANN` was absent from the output. Via `_translate_with_line_preservation` (`il_translator.py:507`, called at `:1535`), the exception is caught in `translate_paragraph`, logged with `logger.exception` as “Error translating paragraph”, and the function returns with the comment “ignore error and continue”. The header-like paragraph had layout label `abandon` and doubled-letter source text “IIBBAANN SSttaannddaarrddss VVeerr.. 22..00 -- QQAATTAARR”. No translation is applied to that paragraph, so it is LIKELY left as source English. What the output PDF shows for that paragraph is NOT VERIFIED. VERIFIED supplied log counts: “Error translating paragraph” appears 0 times in the 600M web run and 1 time in the Colab 3.3B run, so this path depends on model output (LIKELY). The Colab run still completed with both PDFs and `ERRORS 0`: the smoke counter only matches “Error during NLLB translation” (`translator.py:1008`), so B-17's third error-swallowing location is not counted. See B-17/B-30/B-35. This was a paragraph error within a completed run, not evidence that the whole run failed.

**VERIFIED — supplied length-ratio check (B-22):** each run had 20 paragraphs with at least 150 input characters. Below 60% of input length: 600M web had 2 (255 to 113; 253 to 88), and 3.3B Colab had 1 (253 to 100). The two 253-character inputs are LIKELY the same paragraph, pending exact-text matching. Character-length ratios alone do not prove truncation, because Arabic can be shorter than English.

### Reference timings for the 14-page file

**VERIFIED — supplied measurements:**

| Environment / model / path | Total time | Engine-reported peak memory |
|---|---|---|
| Laptop CPU / 600M / web | 1,297 s | 3,652 MB |
| Colab CPU / 600M / direct smoke | 2,750 s | 8,136 MB |
| Colab T4 / 3.3B 8-bit / direct smoke | 1,619 s | 8,991 MB (rounded from 8,990.65 MB) |

Peak memory is not comparable across environments: the measurement's meaning and scope are NOT VERIFIED. Keep engine peaks, sampled system RAM and GPU usage separate. Hardware, model size, web/direct path and first-use loading also differ, so these are reference runs rather than a controlled model-speed comparison. The UI's “~3.5 GB VRAM” claim is unsupported by the T4 observation; the actual minimum remains NOT VERIFIED (B-14).

**NOT VERIFIED / not recorded:** source hash and exact launch/browser command for this benchmark; reconnect behavior, first-progress timing, content-loss causes and native Arabic review. The earlier 2-paragraph smoke used the same filename; identify samples by hash to prevent confusion.

**NOT VERIFIED / not recorded for the Colab runs:** exact source commit, full command, local saved-log path, and source/output hashes were not supplied in this update. Do not assume they match the laptop run's commit merely because the benchmark filename is the same.

For every run, record the source hash, commit, model, hardware, command, logs and expected problem pages. Preserve source/output artifacts and distinguish printed page numbers from PDF page indexes. Keep the initial failed smoke without the `__main__` guard outside successful-run comparisons.

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
| Historical reports say 26/26, 31/31 or 35/35 tests pass. | VERIFIED maintainer-supplied batch-2 result: 199 passed + 106 subtests (`8c727f1`). No tests were run for this docs task. | VERIFIED browser cancel; VERIFIED by the user's report: normal finish plus download of both PDFs after batch 2. | Record the command, branch/commit, exit status and result for each later run. |
| NLLB long-paragraph truncation was solved (`SESSION_HANDOFF_2026_09_07.md:77-86`). | Sentence-splitting and numeral restoration logic/tests exist in translator code and current tests. | Mitigation exists; current model quality is not verified. | Re-test with supported model versions and known long-paragraph fixtures. |
| Add Mutarjim or quantized NLLB (`SESSION_HANDOFF:116-122`). | No Mutarjim engine was verified in current translator source; UI/runtime claims were not executed. | Historical exploration, not an approved requirement. | Research model provenance, license, quality and hardware needs before proposing integration. |
| Full PDF test and scratch cleanup were next steps (`SESSION_HANDOFF:123-127`). | No complete API-to-output test was found; local cache/venv folders exist. | Test gap remains. No deletion should happen before classifying files. | See B-02 and B-16. Preserve user data and environment until reviewed. |
| “Server running” and environment claims (`SESSION_HANDOFF:105-112`). | Dated record refers to a different path/branch; this audit did not start a server. | Not current verification. | Do not repeat as present state without a fresh run. |
| Transformers deprecation and Hugging Face warnings (`CLAUDE_BRIEF_ERRORS_AND_ARCHITECTURE.md:165-173`). | Old logs exist; installed library versions and current runtime warning were not checked. | Not verified and version-sensitive. | Inspect current call site/runtime before changing. |
| Machine paths and old test instructions are current. | Several old docs use `d:\Uni Stuff\...`; active checkout is `D:\transformer\transdoc-afan`. | Stale. | Keep machine-specific paths out of maintained docs; use repository-relative commands. |
| UI promises a 50 MB limit and layout selection works. | VERIFIED: upload size cap 50 MB plus `%PDF-` check committed in `b048202`. The earlier review also recorded that the handler does not accept a `layout` argument (`server.py:171-206`). | B-03 is done and VERIFIED by tests; the separate historical layout-selection finding is unchanged. | B-08 remains open; see the test approach above. |
| `migrate_toc` bug. | VERIFIED source: invalid iteration at `high_level.py:812` is inside `only_include_translated_page`; the call is wrapped by `try/except` at `:764-769`. The web log contains “No TOC found... skipping migration.” | The function is called on the web path; the buggy conditional branch is not reached by the current server. Branch failure remains NOT VERIFIED by execution. | B-11 (P2): exercise the option with a TOC-bearing PDF and add a focused regression test. |
| Progress may end early or hang. | VERIFIED: background jobs and polling (`3ed2c50`), cooperative cancel/timeout and release only after worker stop (`8c727f1`). | B-04 is done for the new route. VERIFIED by the user's report: normal finish plus download of both PDFs after batch 2. | B-21, B-35, model-loading hint (shown at 0/2 VERIFIED by the user's report; hiding at 1/N NOT VERIFIED), B-17, B-18 and B-37 remain open. B-26: fixed, confirmed in a web run. B-36: fixed, browser look NOT VERIFIED. B-34 is decided: keep the cache off on the web path; no code change. |
| Upload, session, CORS and dependency risks. | VERIFIED: G3 server safety (B-03) is done and committed; the semaphore is process-local and sessions/tasks are in memory and lost on restart. | VERIFIED fixes do not make UUID validation authentication. | B-06, B-09 and the security guide. |
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

- VERIFIED by text search/source read: the README CLI example already includes `--openai`; the current translation parser requires OpenAI selection (`babeldoc/main.py:378-413,464-469`). No README edit was needed or made. CLI execution was NOT VERIFIED in this task.
- VERIFIED by text search/source read: maintained `ARCHITECTURE.md` already states 13 stages, matching the 13 active entries in `TRANSLATE_STAGES` (`high_level.py:70-84`); the commented-out stage is excluded. No architecture edit was needed.
- VERIFIED by text search/source read: `ENGINES_AND_SERVER.md` already lists the real events from `server.py:245-428`: `init`, `stage_summary`, `progress_start`, `progress_update`, `progress_end`, `finish`, `error`. No event-name edit was needed; SSE runtime was NOT VERIFIED in this task.
- The NLLB model is loaded on first use (`translator.py:905-926`), so loading/download time may explain an apparent stall. This remains a hypothesis; B-20 requires timestamps to distinguish it from slow inference or errors.
- **VERIFIED — current lifecycle:** jobs run in the background (`3ed2c50`); the page polls `GET /translate/status/{id}` after explicit `POST /translate/run/{id}`. Duplicate run returns 409. B-04 is done for the new route; B-21 remains open because the old `POST /translate` bypasses job controls.
- Translator fallback paths return cleaned source text (`translator.py:824-826,1007-1009`), while an output file makes the server report completion (`server.py:410-418`); this is tracked as B-17.
- Docker copies the full context (`Dockerfile:14`); no `.dockerignore` exists in the tracked inventory. Review required assets before adding one under B-10.
- Explain the difference between package version, API version and product branding rather than silently unifying them.

## Beginner reading exercises

These are code-reading tasks; they do not modify the repository:

1. Find `TRANSLATE_STAGES` in `high_level.py`; follow the Paragraph stage into `paragraph_finder.py` and identify how completion is reported.
2. Trace upload in `server.py` into the explicit run request and status polling in `frontend/index.html`. Note where the upload is stored and which ID is used to access progress/downloads.
3. Compare one historical finding above (ordinals or PUA bullets) with the current test. Explain what the unit test proves and what a real PDF render would still need to verify.
4. Compare `Dockerfile`, `requirements.txt`, `pyproject.toml` and `uv.lock`; list disagreements without changing them.

**VERIFIED - current progress:** G3 server safety (B-03) is done and committed; B-04 is done for the new route. Still open: B-35, model-loading hint (shown at 0/2 VERIFIED by the user's report; hiding at 1/N NOT VERIFIED), B-21, B-17, B-18 and B-37. B-34 is decided: keep the cache off on the web path; no code change. B-26: fixed, confirmed in a web run. B-36: fixed, browser look NOT VERIFIED. VERIFIED by the user's report: normal finish plus download of both PDFs after batch 2. The remaining dependency, ownership/license, deployment, quality and cleanup plans above are unchanged.
