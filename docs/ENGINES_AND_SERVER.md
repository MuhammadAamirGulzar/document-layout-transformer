# Translation Engines, FastAPI Server & Web Frontend

## 1. Supported Translation Engines (`translator.py`)

TransDOC supports three distinct translation backends, catering to offline private environments as well as cloud-assisted LLM translation:

```
                  ┌─────────────────────────────────────────┐
                  │          Translator Selector            │
                  └──────────────────┬──────────────────────┘
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         ▼                           ▼                           ▼
┌──────────────────┐       ┌──────────────────┐       ┌──────────────────┐
│  Meta NLLB-200   │       │     MarianMT     │       │     OpenAI       │
│  (Default, SOTA) │       │ (Lightweight MT) │       │   (LLM Cloud)    │
│  - 600M / 1.3B   │       │ - HuggingFace    │       │ - GPT-4o-mini    │
│  - GPU float16   │       │ - CPU Friendly   │       │ - Auto Glossary  │
│  - Beam Search=4 │       │ - Offline        │       │ - Needs API Key  │
└──────────────────┘       └──────────────────┘       └──────────────────┘
```

### Engine 1: Meta NLLB-200 (`NLLBTranslator`) - Recommended Default
- **Model**: `facebook/nllb-200-distilled-600M` (or `facebook/nllb-200-1.3B`, `facebook/nllb-200-3.3B` with 8-bit quantization).
- **Languages**: 200+ languages, using `eng_Latn` (source) and `arb_Arab` (target Modern Standard Arabic).
- **Inference Optimization**:
  - Automatically checks `torch.cuda.is_available()`.
  - Uses `torch.float16` on CUDA for 3x speedup and minimal VRAM usage (~1.4 GB for 600M model).
  - Employs beam search (`num_beams=4`), `no_repeat_ngram_size=4`, and `repetition_penalty=1.1`.
  - Uses `forced_bos_token_id` to strictly guide target generation to Arabic.
  - **Multi-Sentence Splitting**: Long flowing body paragraphs (`>150` chars) are automatically translated sentence-by-sentence via `_translate_with_line_preservation`, preventing encoder-decoder tail truncation.
  - **Font Subsetting**: Requires `fonttools` (installed in `.venv`) for PyMuPDF CMap glyph subsetting.

### Engine 2: MarianMT (`MarianMTTranslator`)
- **Model**: `HuggingFaceTranslator` / `marefa-nlp/marefa-mt-en-ar`.
- **Characteristics**: Fast, lightweight sequence-to-sequence translation designed for standard CPU deployment without requiring external GPU hardware.

### Engine 3: OpenAI (`OpenAITranslator`)
- **Model**: `gpt-4o-mini` or `gpt-4o`.
- **Characteristics**: Leverages LLM contextual reasoning. Automatically pairs with `AutomaticTermExtractor` (Stage 7) to extract a dynamic bilingual glossary before translation, ensuring domain terms remain uniform across the entire document. Requires `OPENAI_API_KEY`.

---

## 2. FastAPI Server Architecture (`server.py`)

The server exposes an asynchronous, event-driven REST and Server-Sent Events (SSE) API.

### Endpoints:
| Method | Endpoint | Parameters | Description |
|---|---|---|---|
| `GET` | `/` | None | Serves the web frontend UI (`frontend/index.html`). |
| `GET` | `/health` | None | Health check returning status and API version. |
| `GET` | `/languages` | None | Returns JSON dictionary of supported source and target languages. |
| `POST` | `/translate/start` | `file`, `source_lang`, `target_lang`, `model` | Uploads PDF, creates session in `TEMP_DIR/babeldoc_api/<uuid>/`, returns `{"session_id": "..."}`. |
| `GET` | `/translate/progress/{id}` | `session_id` | Streams real-time progress events using SSE (`text/event-stream`). |
| `GET` | `/translate/download/{id}` | `session_id`, `type` (`mono` or `dual`) | Downloads the final `.mono.pdf` or `.dual.pdf`. |
| `POST` | `/translate` | `file`, `source_lang`, `target_lang`, `model` | Synchronous legacy endpoint for non-streaming clients. |

### Real-Time SSE Event Flow:
When the client connects to `/translate/progress/{session_id}`, the server yields:
1. `init`: Initial connection acknowledgement.
2. `stage_summary`: Complete breakdown of the 13 pipeline stages with their assigned weights.
3. `progress_start`: Notification that a specific stage has begun, with its initial counters.
4. `progress_update`: Percentage and item-count progress within the active stage.
5. `progress_end`: Notification that the active stage has completed.
6. `finish`: Overall pipeline complete. Payload contains:
   ```json
   {
     "type": "finish",
     "overall_progress": 100,
     "message": "Translation completed!",
     "has_mono": true,
     "has_dual": true
   }
   ```
7. `error`: Emitted if an unhandled exception occurs, providing diagnostic messages.

---

## 3. Web Frontend (`frontend/index.html`)

A modern, responsive user interface designed for intuitive document translation:

1. **Upload Experience**:
   - Drag-and-drop zone with animated hover effects and PDF validation.
   - Displays file name and size.
2. **Model Selection**:
   - Interactive cards for Meta NLLB-200 (Local / Recommended), MarianMT (Offline), and OpenAI GPT-4o-mini.
3. **Layout Mode Selector**:
   - **Both (Recommended)**: Produces both Dual bilingual and Mono Arabic files.
   - **Dual Bilingual**: English on the left, Arabic on the right.
   - **Monolingual**: Clean 1:1 Arabic document.
4. **Interactive Progress Bar**:
   - Displays overall completion percentage.
   - Visual stage-by-stage checkmarks with human-friendly descriptions (e.g. `📐 Analyzing Page Layout`, `✍️ Typesetting Document`).
5. **Download Action Center**:
   - Distinct download buttons for Dual and Mono PDFs, with a "Download Both" option opening two parallel tabs.
