"""BabelDOC FastAPI Server - Production Ready with Real-Time Progress"""
import asyncio
import json
import logging
import math
import threading
import time
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from babeldoc.format.pdf.high_level import async_translate, init
from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.progress_monitor import ProgressMonitor
from babeldoc.translator.translator import OpenAITranslator, MarianMTTranslator, NLLBTranslator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Suppress verbose logs
logging.getLogger("httpx").setLevel("CRITICAL")
logging.getLogger("openai").setLevel("CRITICAL")

# Initialize FastAPI app
app = FastAPI(
    title="TransDOC Translation API",
    description="Intelligent PDF Translation with Layout Preservation",
    version="1.0.0"
)

# No CORS middleware: the page is served by this server and calls it on the same origin.

# Serve frontend static files
try:
    app.mount("/static", StaticFiles(directory="frontend"), name="static")
except RuntimeError:
    logger.warning("Frontend directory not found, skipping static file serving")

# Temporary directory for file processing
TEMP_DIR = Path(tempfile.gettempdir()) / "babeldoc_api"
TEMP_DIR.mkdir(exist_ok=True)

# Store active translation sessions
translation_sessions = {}

# Language code mapping
LANGUAGE_CODES = {
    'en': 'en',
    'ar': 'en-ar',
    'es': 'es',
    'fr': 'fr',
    'de': 'de',
    'zh': 'zh',
    'ja': 'ja',
    'ko': 'ko',
    'pt': 'pt',
    'ru': 'ru',
    'it': 'it',
}

# Human-readable stage names
STAGE_DISPLAY_NAMES = {
    'Parse PDF and Create Intermediate Representation': '📄 Parsing PDF Structure',
    'DetectScannedFile': '🔍 Detecting Scanned Content',
    'Parse Page Layout': '📐 Analyzing Page Layout',
    'Parse Table': '📊 Processing Tables',
    'Parse Paragraphs': '📝 Finding Paragraphs',
    'Parse Formulas and Styles': '🔢 Processing Formulas & Styles',
    'Automatic Term Extraction': '📚 Extracting Terms',
    'Translate Paragraphs': '🌐 Translating Content',
    'Typesetting': '✍️ Typesetting Document',
    'Add Fonts': '🔤 Mapping Fonts',
    'Generate drawing instructions': '📑 Generating PDF',
    'Subset font': '⚙️ Subsetting Fonts',
    'Save PDF': '💾 Saving Document',
}


def get_display_stage_name(stage: str) -> str:
    """Convert internal stage name to human-readable display name"""
    # Check direct match first
    if stage in STAGE_DISPLAY_NAMES:
        return STAGE_DISPLAY_NAMES[stage]
    
    # Check if stage contains any known key
    for key, value in sorted(STAGE_DISPLAY_NAMES.items(), key=lambda kv: len(kv[0]), reverse=True):
        if key.lower() in stage.lower():
            return value
    
    return f"⏳ {stage}"


@app.on_event("startup")
async def startup_event():
    logger.info("Initializing TransDOC...")
    try:
        init()
        logger.info("TransDOC initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize BabelDOC: {e}")


@app.get("/")
@app.head("/")
async def root():
    """Serve the frontend HTML"""
    try:
        with open("frontend/index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return JSONResponse({
            "name": "BabelDOC API",
            "version": "1.0.0",
            "status": "running",
            "endpoints": {
                "health": "/health",
                "languages": "/languages",
                "translate": "/translate"
            }
        })


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "babeldoc-api",
        "version": "1.0.0"
    }


@app.get("/languages")
async def get_supported_languages():
    """Get list of supported languages"""
    return {
        "supported_languages": {
            "en": "English",
            "ar": "Arabic",
            "es": "Spanish",
            "fr": "French",
            "de": "German",
            "zh": "Chinese",
            "ja": "Japanese",
            "ko": "Korean",
            "pt": "Portuguese",
            "ru": "Russian",
            "it": "Italian",
        },
        "count": len(LANGUAGE_CODES)
    }


UPLOAD_CHUNK = 1024 * 1024


def _max_upload_bytes():
    """Upload size limit in bytes. Set MAX_UPLOAD_MB to change it (default 50)."""
    try:
        megabytes = float(os.getenv("MAX_UPLOAD_MB", "50"))
    except ValueError:
        megabytes = 50.0
    return int(megabytes * 1024 * 1024)


def _copy_limited(src, buffer):
    """Copy an upload to disk in chunks.

    Stops with 413 when the limit is passed and with 400 when the file is empty or
    has no %PDF- header in its first 1024 bytes. On any of these the partly written
    file and its session folder (the folder that holds it) are removed.
    """
    limit = _max_upload_bytes()
    total = 0
    first = True
    try:
        while True:
            chunk = src.read(UPLOAD_CHUNK)
            if not chunk:
                break
            if first:
                first = False
                if b"%PDF-" not in chunk[:1024]:
                    raise HTTPException(status_code=400, detail="File is not a valid PDF")
            total += len(chunk)
            if total > limit:
                raise HTTPException(
                    status_code=413,
                    detail=f"File is larger than {limit / (1024 * 1024):g} MB",
                )
            buffer.write(chunk)
        if first:
            raise HTTPException(status_code=400, detail="File is empty")
    except HTTPException:
        name = buffer.name
        buffer.close()
        shutil.rmtree(Path(name).parent, ignore_errors=True)
        raise


SESSION_ID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|session_[0-9a-f]{16}"
)


def _valid_session_id(value):
    """True only for ids this server makes: a uuid4 or the older session_ + 16 hex."""
    return bool(value) and SESSION_ID_PATTERN.fullmatch(value) is not None


def _display_name(name):
    """Name shown to the user. It is never used as a path on disk."""
    cleaned = (name or "").replace("\\", "/").split("/")[-1].strip()
    return cleaned or "document.pdf"


@app.post("/translate/start")
async def start_translation(
    file: UploadFile = File(...),
    source_lang: str = Form(...),
    target_lang: str = Form(...),
    model: Optional[str] = Form("nllb"),
):
    """
    Start a translation job and return a session ID for progress tracking.
    """
    # Validate file type
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
    # Validate languages
    if source_lang not in LANGUAGE_CODES:
        raise HTTPException(status_code=400, detail=f"Unsupported source language: {source_lang}")
    
    if target_lang not in LANGUAGE_CODES:
        raise HTTPException(status_code=400, detail=f"Unsupported target language: {target_lang}")
    
    if source_lang == target_lang:
        raise HTTPException(status_code=400, detail="Source and target languages must be different")
    
    # Create session
    session_id = str(uuid.uuid4())
    session_dir = TEMP_DIR / session_id
    session_dir.mkdir(exist_ok=True)
    
    input_path = session_dir / "input.pdf"
    output_directory = session_dir / "output"
    output_directory.mkdir(exist_ok=True)
    
    # Save uploaded file
    with open(input_path, "wb") as buffer:
        _copy_limited(file.file, buffer)
    
    # Store session info
    translation_sessions[session_id] = {
        "_created_at": time.time(),
        "status": "pending",
        "input_path": str(input_path),
        "output_directory": str(output_directory),
        "source_lang": source_lang,
        "target_lang": target_lang,
        "model": model,
        "filename": _display_name(file.filename),
        "progress": 0,
        "stage": "Initializing",
        "result": None,
        "error": None,
    }
    
    logger.info(f"Translation session created: {session_id}")
    
    return {"session_id": session_id}


# One model job at a time in this server process. Uploads remain upload-only.
_job_slots = asyncio.Semaphore(1)
_terminal_states = {"completed", "error", "cancelled", "timed_out"}


def _setting_seconds(name, default):
    try:
        value = float(os.getenv(name, str(default)))
        if math.isfinite(value) and value > 0:
            return value
    except ValueError:
        pass
    return float(default)


def _finish_stopped(session):
    reason = session.get("_stop_reason", "cancelled")
    message = "Translation timed out" if reason == "timed_out" else "Translation cancelled"
    session["status"] = reason
    session["error"] = message
    session["_finished_at"] = time.time()
    _job_event(session, {"type": reason, "error": message})


def _request_stop(session, reason):
    if session.get("status") in _terminal_states:
        return False
    if session.get("_stop_reason"):
        return True
    session["_stop_reason"] = reason
    if session.get("_cancel_signal") is not None:
        session["_cancel_signal"].set()
    if session.get("_stop_event") is not None:
        session["_stop_event"].set()
    if session.get("_task") is None:
        _finish_stopped(session)
    else:
        session["status"] = "cancelling"
        _job_event(session, {"type": "stopping", "message": "Stopping after the current operation..."})
    return True


async def _acquire_job_slot(session):
    if session.get("_stop_reason"):
        return False
    acquire = asyncio.create_task(_job_slots.acquire())
    stop = asyncio.create_task(session["_stop_event"].wait())
    transferred = False
    try:
        await asyncio.wait({acquire, stop}, return_when=asyncio.FIRST_COMPLETED)
        if session.get("_stop_reason"):
            return False
        await acquire
        transferred = True
        return True
    finally:
        for task in (acquire, stop):
            if not task.done():
                task.cancel()
        try:
            await asyncio.gather(acquire, stop, return_exceptions=True)
        finally:
            if not transferred and acquire.done() and not acquire.cancelled() and acquire.exception() is None and acquire.result():
                _job_slots.release()


async def _watch_job_deadline(session):
    idle_limit = _setting_seconds("JOB_TIMEOUT_SECONDS", 3600)
    while session.get("status") not in _terminal_states:
        last_activity = session.get("_last_activity_at", time.monotonic())
        if time.monotonic() - last_activity >= idle_limit:
            _request_stop(session, "timed_out")
            return
        await asyncio.sleep(0.05)


def _purge_expired_sessions():
    # Only tracked, inactive sessions are eligible. Never infer orphan ownership.
    root = TEMP_DIR.resolve()
    now = time.time()
    ttl = _setting_seconds("SESSION_TTL_SECONDS", 86400)
    removed = []
    for sid, session in list(translation_sessions.items()):
        if not _valid_session_id(sid):
            continue
        task = session.get("_task")
        if task is not None and not task.done():
            continue
        state = session.get("status")
        stamp = session.get("_finished_at") if state in _terminal_states else session.get("_created_at")
        if state not in _terminal_states and state != "pending":
            continue
        if stamp is None or now - stamp < ttl:
            continue
        target = TEMP_DIR / sid
        # Check the exact resolved child, and reject symlink/junction aliases.
        if target.is_symlink() or target.is_junction() or target.resolve().parent != root:
            logger.warning("Refusing unsafe session cleanup target: %s", sid)
            continue
        try:
            if target.exists():
                shutil.rmtree(target)
        except OSError:
            logger.exception("Could not remove expired session %s", sid)
            continue
        translation_sessions.pop(sid, None)
        removed.append(sid)
    return removed


_retention_task = None


@app.on_event("startup")
async def _start_retention():
    global _retention_task
    async def sweep():
        while True:
            try:
                _purge_expired_sessions()
            except Exception:
                logger.exception("Session expiry sweep failed")
            await asyncio.sleep(60)
    _retention_task = asyncio.create_task(sweep())


@app.on_event("shutdown")
async def _stop_retention_and_jobs():
    if _retention_task is not None:
        _retention_task.cancel()
        await asyncio.gather(_retention_task, return_exceptions=True)
    jobs = []
    for session in translation_sessions.values():
        task = session.get("_task")
        if task is not None and not task.done():
            _request_stop(session, "cancelled")
            jobs.append(task)
    if jobs:
        await asyncio.gather(*jobs, return_exceptions=True)


@app.post("/translate/cancel/{session_id}")
async def cancel_translation_job(session_id: str):
    session = _job_session(session_id)
    if not _request_stop(session, "cancelled"):
        if session.get("status") != "cancelled":
            raise HTTPException(status_code=409, detail="Job has already ended")
    return {"session_id": session_id, "status": session["status"]}


def _job_session(session_id):
    _purge_expired_sessions()
    if not _valid_session_id(session_id) or session_id not in translation_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    return translation_sessions[session_id]


def _job_event(session, event):
    # The timeout protects against a stalled engine.  Long documents that keep
    # reporting progress must not be stopped only because their total runtime
    # exceeds the configured interval.
    session["_last_activity_at"] = time.monotonic()
    session["_event_id"] = session.get("_event_id", 0) + 1
    event = dict(event, event_id=session["_event_id"])
    history = session.setdefault("_events", [])
    history.append(event)
    # Bound retained progress history; preserve the stage summary for late readers.
    del history[:-512]
    if event.get("type") == "stage_summary":
        session["_stage_summary"] = event


def _job_error(session, message):
    session["status"] = "error"
    session["error"] = message
    session["_finished_at"] = time.time()
    _job_event(session, {"type": "error", "error": message})


async def _run_job(session):
    acquired = False
    watcher = None
    try:
        acquired = await _acquire_job_slot(session)
        if not acquired or session.get("_stop_reason"):
            _finish_stopped(session)
            return
        watcher = asyncio.create_task(_watch_job_deadline(session))
        final = None
        async for raw in _translation_events(session):
            event = json.loads(raw.removeprefix("data: ").strip())
            if session.get("_stop_reason"):
                session["status"] = "cancelling"
                continue
            if event.get("type") == "finish":
                final = event
            else:
                _job_event(session, event)
        if session.get("_stop_reason"):
            _finish_stopped(session)
        elif session.get("status") == "error":
            return
        elif final is None:
            _job_error(session, "Translation ended without a finish event")
        elif not session.get("output_path") or not Path(session["output_path"]).is_file():
            _job_error(session, "Translation completed but output file not found")
        else:
            session["status"] = "completed"
            session["progress"] = 100
            session["_finished_at"] = time.time()
            _job_event(session, final)
    except asyncio.CancelledError:
        if session.get("_stop_reason"):
            _finish_stopped(session)
        else:
            _job_error(session, "Translation task was cancelled")
        raise
    except Exception as exc:
        logger.exception("Background translation failed")
        if session.get("_stop_reason"):
            _finish_stopped(session)
        else:
            _job_error(session, str(exc))
    finally:
        try:
            if watcher is not None:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
        finally:
            if acquired:
                _job_slots.release()
            if session.get("status") in _terminal_states:
                session.setdefault("_finished_at", time.time())


def _schedule_job(session):
    # No await between the check and assignment: duplicate requests share one task.
    if session.get("_task") is not None or session.get("status") in _terminal_states:
        return False
    session.setdefault("_created_at", time.time())
    session["_cancel_signal"] = threading.Event()
    session["_stop_event"] = asyncio.Event()
    session["status"] = "queued"
    session["error"] = None
    _job_event(session, {"type": "init", "message": "Queued for translation..."})
    session["_task"] = asyncio.create_task(_run_job(session))
    return True


def _job_events_after(session, after):
    events = [event for event in session.get("_events", []) if event["event_id"] > after]
    summary = session.get("_stage_summary")
    if after == 0 and summary and summary not in events:
        events.insert(0, summary)
    return events


@app.post("/translate/run/{session_id}")
async def run_translation_job(session_id: str):
    session = _job_session(session_id)
    if not _schedule_job(session):
        raise HTTPException(status_code=409, detail="Job already started")
    return {"session_id": session_id, "status": session["status"]}


@app.get("/translate/status/{session_id}")
async def translation_job_status(session_id: str, after: int = 0):
    session = _job_session(session_id)
    if after < 0:
        raise HTTPException(status_code=400, detail="Event cursor must be non-negative")
    return {
        "session_id": session_id,
        "status": session["status"],
        "progress": session.get("progress", 0),
        "stage": session.get("stage", "Initializing"),
        "error": session.get("error"),
        "has_mono": bool(session.get("mono_output_path")),
        "has_dual": bool(session.get("dual_output_path")),
        "untranslated_count": int(session.get("untranslated_count", 0)),
        "incomplete_count": session.get("incomplete_count", 0),
        "events": _job_events_after(session, after),
        "next_event_id": session.get("_event_id", 0),
    }


@app.get("/translate/progress/{session_id}")
async def get_translation_progress(session_id: str):
    session = _job_session(session_id)
    # Compatibility for existing SSE clients: start once, then only observe.
    _schedule_job(session)

    async def observe():
        cursor = 0
        while True:
            for event in _job_events_after(session, cursor):
                yield f"data: {json.dumps(event)}\n\n"
                cursor = max(cursor, event["event_id"])
            if session["status"] in _terminal_states:
                return
            await asyncio.sleep(0.05)

    return StreamingResponse(observe(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


def _untranslated_count(config):
    """How many text blocks the engine could not translate. Never raises."""
    try:
        value = getattr(getattr(config, "translator", None), "untranslated_count", 0)
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


async def _translation_events(session):
    """Generate SSE events for translation progress"""
    try:
        # Update session status
        session["status"] = "processing"

        # Send initial event
        yield f"data: {json.dumps({'type': 'init', 'message': 'Starting translation...'})}\n\n"

        # Create translator
        selected_model = session.get("model", "nllb")
        openai_api_key = os.getenv("OPENAI_API_KEY")

        if selected_model and selected_model.startswith("gpt") and openai_api_key:
            logger.info(f"Using OpenAITranslator with model {selected_model}")
            translator = OpenAITranslator(
                lang_in=LANGUAGE_CODES[session["source_lang"]],
                lang_out=LANGUAGE_CODES[session["target_lang"]],
                model=selected_model,
                api_key=openai_api_key,
                ignore_cache=True
            )
            auto_extract = True
        elif selected_model and ("nllb" in selected_model.lower() or "facebook" in selected_model.lower()):
            logger.info(f"Using local Meta NLLBTranslator (model: {selected_model})")
            translator = NLLBTranslator(
                lang_in=LANGUAGE_CODES[session["source_lang"]],
                lang_out=LANGUAGE_CODES[session["target_lang"]],
                model_name=selected_model,
                ignore_cache=True
            )
            auto_extract = False
        else:
            logger.info("Using local MarianMTTranslator (offline, no API key required)")
            translator = MarianMTTranslator(
                lang_in=LANGUAGE_CODES[session["source_lang"]],
                lang_out=LANGUAGE_CODES[session["target_lang"]],
                ignore_cache=True
            )
            auto_extract = False

        # Configure translation
        config = TranslationConfig(
            translator=translator,
            input_file=session["input_path"],
            lang_in=LANGUAGE_CODES[session["source_lang"]],
            lang_out=LANGUAGE_CODES[session["target_lang"]],
            output_dir=session["output_directory"],
            doc_layout_model=None,
            pages=None,
            skip_clean=False,
            auto_extract_glossary=auto_extract,
        )

        config._job_cancel_event = session["_cancel_signal"]

        # Process translation and stream progress
        translate_result = None
        finish_seen = False
        async for event in async_translate(config):
            event_type = event.get("type", "unknown")
            if finish_seen and event_type == "error" and event.get("error") is asyncio.CancelledError:
                # ProgressMonitor.on_finish emits this class as a completion sentinel.
                continue

            if event_type == "stage_summary":
                # Send stage information
                stages = event.get("stages", [])
                stage_info = [
                    {
                        "name": s["name"],
                        "display_name": get_display_stage_name(s["name"]),
                        "percent": round(s["percent"] * 100, 1)
                    }
                    for s in stages
                ]
                yield f"data: {json.dumps({'type': 'stage_summary', 'stages': stage_info})}\n\n"

            elif event_type == "progress_start":
                stage = event.get("stage", "Unknown")
                display_name = get_display_stage_name(stage)
                session["stage"] = display_name

                progress_event = {
                    "type": "progress_start",
                    "stage": stage,
                    "display_name": display_name,
                    "stage_progress": 0,
                    "stage_current": event.get("stage_current", 0),
                    "stage_total": event.get("stage_total", 0),
                    "overall_progress": event.get("overall_progress", 0),
                    "part_index": event.get("part_index", 1),
                    "total_parts": event.get("total_parts", 1),
                }
                yield f"data: {json.dumps(progress_event)}\n\n"

            elif event_type == "progress_update":
                stage = event.get("stage", "Unknown")
                display_name = get_display_stage_name(stage)
                overall_progress = event.get("overall_progress", 0)

                session["stage"] = display_name
                session["progress"] = overall_progress

                progress_event = {
                    "type": "progress_update",
                    "stage": stage,
                    "display_name": display_name,
                    "stage_progress": round(event.get("stage_progress", 0), 1),
                    "stage_current": event.get("stage_current", 0),
                    "stage_total": event.get("stage_total", 0),
                    "overall_progress": round(overall_progress, 1),
                    "part_index": event.get("part_index", 1),
                    "total_parts": event.get("total_parts", 1),
                }
                yield f"data: {json.dumps(progress_event)}\n\n"

            elif event_type == "progress_end":
                stage = event.get("stage", "Unknown")
                display_name = get_display_stage_name(stage)
                overall_progress = event.get("overall_progress", 0)

                session["progress"] = overall_progress

                progress_event = {
                    "type": "progress_end",
                    "stage": stage,
                    "display_name": display_name,
                    "stage_progress": 100,
                    "stage_current": event.get("stage_total", 0),
                    "stage_total": event.get("stage_total", 0),
                    "overall_progress": round(overall_progress, 1),
                    "part_index": event.get("part_index", 1),
                    "total_parts": event.get("total_parts", 1),
                }
                yield f"data: {json.dumps(progress_event)}\n\n"

            elif event_type == "finish":
                finish_seen = True
                translate_result = event.get("translate_result")
                session["result"] = translate_result
                session["untranslated_count"] = _untranslated_count(config)
                session["incomplete_count"] = getattr(
                    getattr(config, "translator", None), "incomplete_count", 0
                )

                # Find output files (both mono and dual side-by-side)
                mono_pdf = None
                dual_pdf = None
                output_directory = Path(session["output_directory"])

                if translate_result:
                    if hasattr(translate_result, 'mono_pdf_path') and translate_result.mono_pdf_path:
                        mono_pdf = translate_result.mono_pdf_path
                    elif hasattr(translate_result, 'no_watermark_mono_pdf_path') and translate_result.no_watermark_mono_pdf_path:
                        mono_pdf = translate_result.no_watermark_mono_pdf_path

                    if hasattr(translate_result, 'dual_pdf_path') and translate_result.dual_pdf_path:
                        dual_pdf = translate_result.dual_pdf_path
                    elif hasattr(translate_result, 'no_watermark_dual_pdf_path') and translate_result.no_watermark_dual_pdf_path:
                        dual_pdf = translate_result.no_watermark_dual_pdf_path

                # Fallback to searching output directory
                if not mono_pdf or not Path(mono_pdf).exists():
                    monos = list(output_directory.glob("*.mono.pdf"))
                    if monos:
                        mono_pdf = str(monos[0])
                    else:
                        all_pdfs = [p for p in output_directory.glob("*.pdf") if not p.name.endswith(".dual.pdf")]
                        if all_pdfs:
                            mono_pdf = str(all_pdfs[0])

                if not dual_pdf or not Path(dual_pdf).exists():
                    duals = list(output_directory.glob("*.dual.pdf"))
                    if duals:
                        dual_pdf = str(duals[0])

                session["mono_output_path"] = str(mono_pdf) if mono_pdf and Path(mono_pdf).exists() else None
                session["dual_output_path"] = str(dual_pdf) if dual_pdf and Path(dual_pdf).exists() else None
                session["output_path"] = session["mono_output_path"] or session["dual_output_path"]

                if session["output_path"]:
                    finish_payload = {
                        "type": "finish",
                        "overall_progress": 100,
                        "message": "Translation completed!",
                        "has_mono": bool(session["mono_output_path"]),
                        "has_dual": bool(session["dual_output_path"])
                    }
                    yield f"data: {json.dumps(finish_payload)}\n\n"
                else:
                    session["status"] = "error"
                    session["error"] = "Translation completed but output file not found"
                    yield f"data: {json.dumps({'type': 'error', 'error': 'Translation completed but output file not found'})}\n\n"

                # Drain the engine after its finish event.

            elif event_type == "error":
                error_msg = str(event.get("error", "Unknown error"))
                session["status"] = "error"
                session["error"] = error_msg
                yield f"data: {json.dumps({'type': 'error', 'error': error_msg})}\n\n"
                # Drain engine cleanup before releasing the job slot.

            # Small delay to prevent overwhelming the client
            await asyncio.sleep(0.05)

    except Exception as e:
        logger.error(f"Translation error: {str(e)}", exc_info=True)
        session["status"] = "error"
        session["error"] = str(e)
        yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"



@app.get("/translate/download/{session_id}")
async def download_translation(session_id: str, type: Optional[str] = "mono"):
    """
    Download the translated PDF file.
    type: 'mono' (Arabic only) or 'dual' (side-by-side English + Arabic)
    """
    if not _valid_session_id(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    session = translation_sessions.get(session_id)
    if session and session.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Job has no completed download")
    req_type = (type or "mono").lower()

    output_path = None
    output_filename = "translated.pdf"

    if session and session.get("status") == "completed":
        stem = Path(session.get("filename", "document")).stem
        if req_type == "dual" and session.get("dual_output_path"):
            output_path = session["dual_output_path"]
            output_filename = f"{stem}_dual_bilingual.pdf"
        else:
            output_path = session.get("mono_output_path") or session.get("output_path")
            output_filename = f"{stem}_translated_mono.pdf"
    else:
        # Fallback to checking disk in TEMP_DIR (preserves downloads across server reloads)
        session_dir = TEMP_DIR / session_id
        if not session_dir.exists():
            raise HTTPException(status_code=404, detail="Session not found")
        output_dir = session_dir / "output"
        if not output_dir.exists():
            raise HTTPException(status_code=404, detail="Session output not found")
        
        duals = list(output_dir.glob("*.dual.pdf"))
        monos = list(output_dir.glob("*.mono.pdf"))
        
        if req_type == "dual" and duals:
            output_path = str(duals[0])
            output_filename = duals[0].name
        elif monos:
            output_path = str(monos[0])
            output_filename = monos[0].name
        else:
            all_pdfs = list(output_dir.glob("*.pdf"))
            if all_pdfs:
                output_path = str(all_pdfs[0])
                output_filename = all_pdfs[0].name
            else:
                raise HTTPException(status_code=404, detail="No PDF output found in session")

    if not output_path or not Path(output_path).exists():
        raise HTTPException(status_code=404, detail=f"Output file ({req_type}) not found")
    
    return FileResponse(
        path=output_path,
        filename=output_filename,
        media_type="application/pdf",
    )


@app.post("/translate")
async def translate_document(
    file: UploadFile = File(...),
    source_lang: str = Form(...),
    target_lang: str = Form(...),
    model: Optional[str] = Form("nllb"),
):
    """
    Translate a PDF document (non-streaming, for backwards compatibility).
    """
    # Validate file type
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
    # Validate languages
    if source_lang not in LANGUAGE_CODES:
        raise HTTPException(status_code=400, detail=f"Unsupported source language: {source_lang}")
    
    if target_lang not in LANGUAGE_CODES:
        raise HTTPException(status_code=400, detail=f"Unsupported target language: {target_lang}")
    
    if source_lang == target_lang:
        raise HTTPException(status_code=400, detail="Source and target languages must be different")
    
    # Create session directory
    session_id = f"session_{os.urandom(8).hex()}"
    session_dir = TEMP_DIR / session_id
    session_dir.mkdir(exist_ok=True)
    
    input_path = session_dir / "input.pdf"
    output_directory = session_dir / "output"
    output_directory.mkdir(exist_ok=True)
    
    try:
        # Save uploaded file
        logger.info(f"Processing translation: {file.filename}")
        logger.info(f"Language pair: {source_lang} -> {target_lang}")
        logger.info(f"Model: {model}")
        
        with open(input_path, "wb") as buffer:
            _copy_limited(file.file, buffer)
        
        # Create translator
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if model and model.startswith("gpt") and openai_api_key:
            logger.info(f"Using OpenAITranslator with model {model}")
            translator = OpenAITranslator(
                lang_in=LANGUAGE_CODES[source_lang],
                lang_out=LANGUAGE_CODES[target_lang],
                model=model,
                api_key=openai_api_key,
                ignore_cache=True
            )
            auto_extract = True
        elif model and ("nllb" in model.lower() or "facebook" in model.lower()):
            logger.info(f"Using local Meta NLLBTranslator (model: {model})")
            translator = NLLBTranslator(
                lang_in=LANGUAGE_CODES[source_lang],
                lang_out=LANGUAGE_CODES[target_lang],
                model_name=model,
                ignore_cache=True
            )
            auto_extract = False
        else:
            logger.info("Using local MarianMTTranslator (offline, no API key required)")
            translator = MarianMTTranslator(
                lang_in=LANGUAGE_CODES[source_lang],
                lang_out=LANGUAGE_CODES[target_lang],
                ignore_cache=True
            )
            auto_extract = False
        
        # Configure translation
        config = TranslationConfig(
            translator=translator,
            input_file=str(input_path),
            lang_in=LANGUAGE_CODES[source_lang],
            lang_out=LANGUAGE_CODES[target_lang],
            output_dir=str(output_directory),
            doc_layout_model=None,
            pages=None,
            skip_clean=False,
            auto_extract_glossary=auto_extract,
        )
        
        # Perform translation
        logger.info("Starting translation process...")
        
        translate_result = None
        async for event in async_translate(config):
            if event["type"] == "progress_update":
                logger.debug(
                    f"Progress: {event['stage']} - "
                    f"{event['stage_current']}/{event['stage_total']} "
                    f"(Overall: {event['overall_progress']}%)"
                )
            elif event["type"] == "finish":
                translate_result = event["translate_result"]
                logger.info("Translation completed successfully")
                break
            elif event["type"] == "error":
                error_msg = event.get("error", "Unknown error")
                logger.error(f"Translation error: {error_msg}")
                raise HTTPException(status_code=500, detail=f"Translation failed: {error_msg}")
        
        if translate_result is None:
            raise HTTPException(status_code=500, detail="Translation completed but no result returned")
        
        # Find the output PDF
        output_pdf = None
        
        if hasattr(translate_result, 'mono_pdf_path') and translate_result.mono_pdf_path:
            output_pdf = translate_result.mono_pdf_path
        elif hasattr(translate_result, 'no_watermark_mono_pdf_path') and translate_result.no_watermark_mono_pdf_path:
            output_pdf = translate_result.no_watermark_mono_pdf_path
        
        if not output_pdf or not Path(output_pdf).exists():
            pdf_files = list(output_directory.glob("*.pdf"))
            if pdf_files:
                output_pdf = pdf_files[0]
        
        if not output_pdf:
            raise HTTPException(status_code=500, detail="Translation completed but output file not found")
        
        if isinstance(output_pdf, str):
            output_pdf = Path(output_pdf)
        
        if not output_pdf.exists():
            raise HTTPException(status_code=500, detail=f"Translation completed but output file does not exist")
        
        logger.info(f"Translation successful: {output_pdf}")
        
        output_filename = f"translated_{file.filename}"
        
        return FileResponse(
            path=str(output_pdf),
            filename=output_filename,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={output_filename}"
            }
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Translation error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Translation failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", 7860))
    
    logger.info(f"Starting TransDOC API server on port {port}")
    
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
        reload=False
    )
