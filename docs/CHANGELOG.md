# Changelog

This file records repository changes by release or reviewed change. It does not list planned work; see [`ENGINEERING_BACKLOG.md`](ENGINEERING_BACKLOG.md).

## Unreleased

- `841e0db`: the page shows how many text blocks were left untranslated.
- `7f905b1`: the page shows a hint while the translation model loads.
- `66842b1`: the progress page highlights the correct stage row.
- `a68b370`: removed the duplicate PDF write. The recorded web run showed progress drops falling from 4 to 0. The duplicate pass spanned about 16 s in the old recording.

### G3/G4 and Git handoff — 2026-11-05

- **VERIFIED by tests — G3 server safety (B-03), done and committed:** fixed upload file name (`b908064`); upload size cap 50 MB plus `%PDF-` check (`b048202`); session-id validation on download (`0d87c66`); CORS removed (`0d9fefb`); download filename header (`34cff55`); fake `/translate/stream` entry removed (`0a935c1`).
- **VERIFIED — G4 batch 1 (`3ed2c50`):** jobs run in the background; the page polls `GET /translate/status/{id}`; explicit `POST /translate/run/{id}`; duplicate run returns 409; one engine job at a time per server process. B-04 is done for the new route.
- **VERIFIED — G4 batch 2 (`8c727f1`):** cooperative cancel via `POST /translate/cancel/{id}`; `JOB_TIMEOUT_SECONDS` default 3600; `SESSION_TTL_SECONDS` default 86400 with a 60 s sweep; the engine slot is released only after the worker really stops.
- **VERIFIED — validation:** 199 passed + 106 subtests; a 600M smoke run on the 2-paragraph file took 63 s with 0 errors; a browser cancel was seen working. **NOT VERIFIED:** a normal browser finish plus download after batch 2.
- **VERIFIED — test approach:** `TestClient` is broken here with Starlette 0.27.0 + httpx 0.28.1, so tests use `httpx.ASGITransport` inside `asyncio.run`.
- **VERIFIED — Git:** `cleanup-baseline` was pushed as a NEW branch to the owner's public repository on 2026-11-05, tip `8c727f1`. `main` and `afan` were not touched. The commit email is the GitHub noreply address. `main` and `afan` have unrelated histories: never merge them; a future PR goes into `afan`.

### Documentation cleanup

- Documentation cleanup: added a project guide, a current engineering backlog with historical-issue reconciliation, security/deployment notes, and this changelog; refreshed testing/operations guidance and marked older issue/status reports as historical references.
- No application code changes are included in this documentation update.

## Historical notes

Older technical changes and issue investigations remain in the dated documents linked from [`PROJECT_GUIDE.md`](PROJECT_GUIDE.md). Their test totals and runtime statements describe the original report date and are not current verification.
