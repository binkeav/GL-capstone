# Design One-Pager: OCR Agent for Invoice Extraction

## 1. Objective
Build a local OCR-assisted invoice extraction workbench that accepts image, PDF, and DOCX invoices, extracts readable text, converts that text into structured invoice fields, and supports lightweight question answering over processed documents.

The OCR agent is the upstream extraction layer for the broader invoice validation workflow. Its output should be a normalized invoice JSON payload that can be passed to validation agents for arithmetic, compliance, duplicate, and routing checks.

## 2. Scope
In scope:

- Upload and process JPG, PNG, PDF, and DOCX invoice documents.
- Run OCR on scanned pages and embedded images.
- Extract standard invoice fields and line items.
- Compare extraction providers across GPT, local Qwen, and regex fallback.
- Show OCR text, extracted fields, quality metrics, and layout metadata.
- Support local RAG chat over uploaded OCR text.

Out of scope for this layer:

- Final invoice approval or rejection.
- ERP submission.
- Deterministic financial validation.
- Vendor master, PO, goods receipt, and duplicate checks.
- Production authentication, multi-user storage, and audit workflow ownership.

## 3. High-Level Architecture

```text
User / Reviewer
        |
        v
Streamlit OCR Workbench
        |
        v
Upload Router
  |        |        |
  v        v        v
Image    PDF      DOCX
OCR      Page     Native Text +
         Render   Embedded Image OCR
        \ | /
         v
OpenCV Preprocessing
        |
        v
EasyOCR Text Extraction
        |
        +--> Optional Layout-Aware Region OCR
        |
        v
Field Extraction Selector
  |             |             |
  v             v             v
GPT-4o-mini   Local Qwen     Regex Fallback
        \       |       /
         v      v      v
Structured Invoice JSON
        |
        +--> UI Preview
        +--> RAG Chat Index
        +--> Downstream Validation Agent
```

## 4. Core Components

### Streamlit Frontend
File: `app_milestone2_frontend.py`

- Provides upload controls for images, PDFs, and DOCX files.
- Lets the user choose extraction mode: `auto`, `gpt`, or `qwen`.
- Displays raw OCR text, extracted JSON fields, layout summary, and layout box overlay.
- Keeps processed document text in session state for RAG chat.

### OCR Backend
File: `milestone2_backend.py`

- Defines `Milestone1NotebookAPI`, the main backend API.
- Loads EasyOCR local model files.
- Computes quality metrics: blur, brightness, and contrast.
- Preprocesses images with grayscale conversion, denoising, and adaptive thresholding.
- Routes uploads by file type.
- Returns a single structured result payload for the frontend.

### Layout-Aware OCR
Expected files:

- `.venv-layout/bin/python`
- `scripts/layout_worker.py`

The backend can call a separate layout worker to detect document regions such as text, title, list, table, and figure blocks. When unavailable, the system falls back to full-page OCR and reports layout worker status in metadata.

### Field Extraction
Supported extraction paths:

| Mode | Behavior |
| --- | --- |
| `gpt` | Use GPT-4o-mini, then regex fallback on error or parse failure |
| `qwen` | Use local Qwen VL model, then regex fallback on error or parse failure |
| `auto` | Try GPT first, then local Qwen, then regex fallback |

The output schema includes invoice metadata, totals, currency, tax fields, and `order_items`.

### RAG Chat
The frontend chunks OCR text, retrieves relevant chunks using token overlap, and asks GPT-4o-mini to answer using only retrieved context. If GPT is unavailable, it shows the top retrieved chunks directly.

## 5. Processing Workflow

```text
1. User uploads an invoice document.
2. Frontend passes the file to `process_upload`.
3. Backend selects image, PDF, or DOCX processing.
4. Image pages are converted to RGB arrays.
5. OpenCV preprocessing prepares the image for OCR.
6. EasyOCR extracts text and confidence scores.
7. Optional layout worker detects regions and OCR runs on cropped regions.
8. OCR text is sent to the configured field extractor.
9. Extracted fields are normalized to the expected JSON shape.
10. Frontend displays text, JSON fields, quality metrics, and layout metadata.
11. OCR text is indexed in session state for local RAG chat.
```

## 6. Structured Output Contract

```json
{
  "status": "success",
  "type": "pdf",
  "avg_confidence": 0.91,
  "text": "raw OCR text",
  "fields": {
    "invoice_number": "INV-123",
    "invoice_date": "2026-06-01",
    "due_date": null,
    "po_number": "PO-456",
    "payment_terms": "Net 30",
    "vendor_name": "Example Vendor",
    "vendor_tax_id": null,
    "customer_name": "Example Customer",
    "customer_tax_id": null,
    "subtotal": "1000.00",
    "tax": "180.00",
    "total": "1180.00",
    "currency": "INR",
    "order_items": []
  },
  "extraction_mode": "gpt-4o-mini",
  "layout_summary": {
    "regions_total": 12,
    "crop_chunks_total": 8
  }
}
```

## 7. Runtime Dependencies

- Python application stack: Streamlit, OpenCV, EasyOCR, NumPy, pypdfium2, python-docx.
- GPT extraction: `OPENAI_API_KEY` and `OPENAI_API_BASE` in `.env`.
- Local OCR models: `craft_mlt_25k.pth` and `english_g2.pth`.
- Local Qwen extraction: `qwen-vl` model directory.
- Layout-aware OCR: isolated `.venv-layout` runtime and `scripts/layout_worker.py`.

## 8. Current Run-State Notes

- The frontend imports the checked-in backend module, `milestone2_backend.py`.
- EasyOCR model files are required at startup and are now present in the project root.
- The local Qwen2-VL model is now present in `qwen-vl/`; on this machine it runs on CPU and can be slow.
- The layout worker files are optional but missing, so layout OCR will fall back to full-page OCR.
- `README.md` documents local setup and launch commands.

## 9. Success Criteria

- A reviewer can upload an invoice and receive OCR text plus structured invoice JSON.
- The app clearly shows which extractor was used and whether fallback was activated.
- OCR confidence and quality metrics help explain weak extraction cases.
- Extracted JSON can be handed to the validation agent without manual reshaping.
- Missing optional runtimes degrade gracefully with clear metadata.

## 10. Near-Term Improvements

- Make EasyOCR model loading configurable instead of hard-failing without local files.
- Consider using a smaller/faster local text model for CPU-only extraction, or enable GPU/MPS acceleration for Qwen.
- Persist processed documents and RAG index outside Streamlit session state.
- Add automated smoke tests for image, PDF, DOCX, and fallback extraction paths.
