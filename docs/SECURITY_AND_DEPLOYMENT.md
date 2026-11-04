# Security and deployment notes

This page records code-level boundaries and questions to resolve before exposing the service. It is not a penetration test or a claim of production readiness. Source review date: 2026-11-04. Runtime, proxy and cloud settings were not inspected.

## Request and file flow

```text
Browser -> FastAPI upload -> temporary per-session directory
        -> asynchronous PDF/ML pipeline -> generated PDF files
        -> SSE progress and session-based download
```

Relevant implementation: `server.py`; UI upload and progress code: `frontend/index.html`; container listener: `Dockerfile`.

## Findings that need action before public exposure

| Boundary | Current source evidence | Risk / decision |
|---|---|---|
| Upload filename | Both `/translate/start` and `/translate` join `file.filename` to a session path (`server.py:200-206,534-550`). | Generate a server-side filename and prove resolved paths remain under the session directory. Keep the original name only as sanitized display metadata. |
| Upload size | UI claims 50 MB (`frontend/index.html:827-832`); upload handlers do not visibly enforce that cap (`server.py:171-206,510-550`). | Enforce a byte limit while receiving data, plus page/pixel/resource limits appropriate to PDF parsing. Proxy limits are deployment-specific and NOT VERIFIED. |
| File content | Handlers check `.pdf` suffix (`server.py:181-183,520-522`). | A suffix does not establish that a file is a safe, valid PDF. Test malformed and oversized PDFs and define parser failure behavior. |
| Session access | Session ID is returned after upload; progress and download routes accept it (`server.py:228-236,451-505`). | Treat it as a bearer credential. Add auth/ownership or short-lived access controls; avoid leaking IDs in logs/URLs. |
| Download path and header | The download route builds its path from the `session_id` in the URL with no format check (`server.py:472-495`) and `Content-Disposition` is not quoted (`:505`). | Path traversal is LIKELY but NOT tested. Validate the session ID format, resolve and contain the path under the session directory, and safely quote/encode download filenames. |
| Temp data | Files live under `TEMP_DIR` (`server.py:55-60`); no expiry/cleanup was found in route code. | Define retention, cleanup on success/failure/cancel, disk quotas and privacy expectations. Verify whether hosting infrastructure cleans temp storage. |
| CORS | All origins, methods and headers with credentials enabled (`server.py:40-47`). | Configure specific trusted origins and only required methods/headers. Confirm whether credentials are needed. |
| Authentication | No authentication middleware is visible in `server.py` app setup/routes. | Decide if this is a local demo, private internal service or public application; add authentication at the proper trusted layer. |
| Secrets | OpenAI key read from `OPENAI_API_KEY` (`server.py:249,553`). | Supply through secret management; never bake into image, source or logs. A full dependency exception/log review is still needed. |
| Resource exhaustion | Upload triggers PDF parsing and optional heavy ML work (`server.py:247-294`). | Bound concurrent jobs, timeout and cancel work, and set memory/CPU/GPU quotas. Large models may need a queue and one model worker. |
| Async cancellation | Executor worker and callback lifecycle in `high_level.py:415-444`; worker cleanup in `:779-784`. | Test task cancellation, browser disconnect, model error and worker exception; every job must become terminal. |
| Remote fetching | Asset download and layout RPC code exists under `babeldoc/assets/` and `babeldoc/docvision/rpc_doclayout*.py`. | Verify hosts are fixed/admin-configured and no user-controlled URL can reach private network services. Exploitability is NOT VERIFIED. |

## Container facts and open work

The current Dockerfile uses Python 3.12 slim, installs OpenCV OS libraries, installs `requirements.txt`, exposes port 7860 and starts Uvicorn bound to `0.0.0.0` (`Dockerfile:1-18`). It does not visibly define a non-root user or Docker `HEALTHCHECK`. The Docker build and resulting image size were not checked.

Before deployment, decide and test:

- One dependency source and CPU/GPU install variants (`pyproject.toml`, `requirements.txt`, `uv.lock` currently differ).
- Reverse proxy/TLS, authentication, request size, timeouts and trusted CORS origins.
- Model and OCR asset download locations, hashes, cache persistence and license terms.
- Number of application workers and concurrent model jobs; model memory may be replicated per process.
- Temp file location, encryption expectations, retention and quota.
- Non-root runtime, health check, graceful shutdown, image scanning and base image update policy.
- Logs/metrics that include job/stage IDs but never API keys or sensitive PDF contents.

## $0 hosting and experiment options

- **Hugging Face Spaces:** the repository README contains Docker Space front matter (`sdk: docker`, `app_port: 7860`). Current Hugging Face Hub documentation says Gradio and Docker Spaces require a paid subscription even when using the CPU Basic hardware. Ask the repository owner whether a Space already exists, who owns it, and whether a paid plan or community grant is available.
- **Cloudflare Quick Tunnels:** current Cloudflare documentation says Quick Tunnels do not support Server-Sent Events (SSE). The present progress route uses SSE, so a Quick Tunnel is not compatible with the current job/progress flow. A polling/status design would be a separate application change and must be tested before using this route for a demo.
- **Oracle Always Free Arm:** current Oracle documentation lists 2 OCPUs and 12 GB of memory in total for Always Free Ampere A1 resources. Treat this as a possible 600M CPU deployment target only; model fit, Arm wheels, throughput, capacity availability, and the full container build are **NOT VERIFIED**. The 3.3B model is outside the intended scope for this resource budget.
- **Kaggle or Colab GPU:** a free, quota-limited notebook GPU can be used for a one-off 3.3B experiment when available. It is an experiment environment rather than a persistent deployment. Account eligibility, GPU availability, session limits, installation success, model licensing, and runtime memory are **NOT VERIFIED** for this project.

## License and third-party assets

Project metadata declares AGPL-3.0 (`pyproject.toml:5`) and a `LICENSE` file is present. The application is network-facing (`server.py:33-47`). AGPL network source obligations may apply to modified versions offered as a service; consult the license text and legal owner before deployment. The project fetches/uses font and model assets through `babeldoc/assets/`; individual asset licenses and commercial/redistribution rights have not been fully inventoried. Track each asset’s source, version/hash, license and notice requirements.

## Verification checklist before a production deployment

- [ ] Confirm service owner, audience, data sensitivity and authentication design.
- [ ] Enforce upload byte/page limits and safe server-generated paths.
- [ ] Test malformed PDFs, path-like names, unauthorized session IDs and download isolation.
- [ ] Test cancellation, browser disconnect, timeout, worker restart and temp cleanup.
- [ ] Select supported CPU/GPU model matrix and concurrency limits.
- [ ] Build and scan container from locked dependencies; verify non-root and health check.
- [ ] Inventory model/font/data licenses and required source/attribution links.
- [ ] Review dependency advisories and transitive package licenses using an approved scanner.
