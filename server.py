"""BabelDOC FastAPI Server - Production Ready with Real-Time Progress"""
import asyncio
import json
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
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
    version="2.1.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    'Create IL': '📄 Parsing PDF Structure',
    'Detect Scanned File': '🔍 Detecting Scanned Content',
    'Layout': '📐 Analyzing Page Layout',
    'Table': '📊 Processing Tables',
    'Paragraph': '📝 Finding Paragraphs',
    'Styles And Formulas': '🔢 Processing Formulas & Styles',
    'AutomaticTermExtractor': '📚 Extracting Terms',
    'Translate': '🌐 Translating Content',
    'Typesetting': '✍️ Typesetting Document',
    'Font Mapper': '🔤 Mapping Fonts',
    'PDF Creater': '📑 Generating PDF',
    'Subset Font': '⚙️ Subsetting Fonts',
    'Save PDF': '💾 Saving Document',
}


def get_display_stage_name(stage: str) -> str:
    """Convert internal stage name to human-readable display name"""
    # Check direct match first
    if stage in STAGE_DISPLAY_NAMES:
        return STAGE_DISPLAY_NAMES[stage]
    
    # Check if stage contains any known key
    for key, value in STAGE_DISPLAY_NAMES.items():
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
                "translate": "/translate",
                "translate_stream": "/translate/stream"
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
    
    input_path = session_dir / file.filename
    output_directory = session_dir / "output"
    output_directory.mkdir(exist_ok=True)
    
    # Save uploaded file
    with open(input_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    # Store session info
    translation_sessions[session_id] = {
        "status": "pending",
        "input_path": str(input_path),
        "output_directory": str(output_directory),
        "source_lang": source_lang,
        "target_lang": target_lang,
        "model": model,
        "filename": file.filename,
        "progress": 0,
        "stage": "Initializing",
        "result": None,
        "error": None,
    }
    
    logger.info(f"Translation session created: {session_id}")
    
    return {"session_id": session_id}


@app.get("/translate/progress/{session_id}")
async def get_translation_progress(session_id: str):
    """
    Stream translation progress events using Server-Sent Events (SSE).
    """
    if session_id not in translation_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = translation_sessions[session_id]
    
    async def event_generator():
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
            
            # Process translation and stream progress
            translate_result = None
            async for event in async_translate(config):
                event_type = event.get("type", "unknown")
                
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
                    translate_result = event.get("translate_result")
                    session["status"] = "completed"
                    session["progress"] = 100
                    session["result"] = translate_result
                    
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
                        yield f"data: {json.dumps({'type': 'error', 'error': 'Translation completed but output file not found'})}\n\n"
                    
                    break
                
                elif event_type == "error":
                    error_msg = str(event.get("error", "Unknown error"))
                    session["status"] = "error"
                    session["error"] = error_msg
                    yield f"data: {json.dumps({'type': 'error', 'error': error_msg})}\n\n"
                    break
                
                # Small delay to prevent overwhelming the client
                await asyncio.sleep(0.05)
            
        except Exception as e:
            logger.error(f"Translation error: {str(e)}", exc_info=True)
            session["status"] = "error"
            session["error"] = str(e)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/translate/download/{session_id}")
async def download_translation(session_id: str, type: Optional[str] = "mono"):
    """
    Download the translated PDF file.
    type: 'mono' (Arabic only) or 'dual' (side-by-side English + Arabic)
    """
    session = translation_sessions.get(session_id)
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
        headers={
            "Content-Disposition": f"attachment; filename={output_filename}"
        }
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
    
    input_path = session_dir / file.filename
    output_directory = session_dir / "output"
    output_directory.mkdir(exist_ok=True)
    
    try:
        # Save uploaded file
        logger.info(f"Processing translation: {file.filename}")
        logger.info(f"Language pair: {source_lang} -> {target_lang}")
        logger.info(f"Model: {model}")
        
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
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