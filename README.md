# GL Capstone: Invoice Processing

A unified Streamlit application for invoice OCR, structured field extraction, validation, and chat-based follow-up.

The app lets a user upload an invoice, extract key invoice fields, validate the invoice against business rules, inspect a trace of what happened, and ask follow-up questions over the current invoice or previously uploaded invoices.

## What It Does

- Upload invoices as JPG, PNG, PDF, or DOCX files.
- Extract invoice details with EasyOCR plus GPT, Auto, or local Qwen mode.
- Support extraction-only requests such as "extract the details" without running validation.
- Validate invoice payloads with the Val-agent rule graph.
- Show meaningful validation errors with the actual values used in checks.
- Search and filter uploaded invoices by vendor, status, and invoice details.
- Keep a configurable chat memory window for professional follow-up conversations.
- Store local chat, invoice, and lightweight RAG data in SQLite.

## Application Flow

```text
Document upload
  -> OCR extraction
  -> OCR-to-validation adapter
  -> Extraction quality gate
  -> Upload intent routing
  -> Extraction-only response or Val-agent validation
  -> Unified result and chat response
```

## Repository Map

| Path | Purpose |
| --- | --- |
| `invoice_processing_app.py` | Main Streamlit app entry point. |
| `invoice_processing/` | Unified orchestration, chat agent, extraction adapter, RAG, and app services. |
| `ocr-agent/` | OCR workbench and extraction backend. |
| `Val-agent/` | Invoice validation graph, rules, models, and tests. |
| `invoice-processing-design-onepager.md` | Current design summary for the invoice processing app. |
| `STREAMLIT_DEPLOYMENT.md` | Streamlit Community Cloud deployment notes. |
| `requirements.txt` | Root dependency set for cloud deployment. |

## Local Setup

Use Python 3.11.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set the required environment variables in your shell, `.env`, or Streamlit secrets:

```bash
export OPENAI_API_KEY="..."
export OPENAI_API_BASE="https://aibe.mygreatlearning.com/openai/v1"
export FIELD_EXTRACTOR_MODE="gpt"
export INVOICE_PROCESSING_LLM_MODEL="gpt-4o-mini"
export VAL_AGENT_LLM_MODEL="gpt-4o-mini"
export EASYOCR_DOWNLOAD_ENABLED="true"
```

Run the app:

```bash
streamlit run invoice_processing_app.py --server.port 8501
```

Then open:

```text
http://127.0.0.1:8501
```

## Streamlit Community Cloud

Use these settings:

- Repository: `binkeav/GL-capstone`
- Branch: `main`
- Main file path: `invoice_processing_app.py`
- Python: `3.11`

Add the same environment values in Streamlit Cloud secrets. See `STREAMLIT_DEPLOYMENT.md` for the exact TOML format.

Cloud notes:

- Use GPT mode on Streamlit Community Cloud.
- Local Qwen model directories are not intended for Streamlit Community Cloud because they are large and require local model files.
- EasyOCR model files can download at runtime when `EASYOCR_DOWNLOAD_ENABLED=true`.
- Runtime SQLite and RAG files are ephemeral on Streamlit Community Cloud.

## Testing

Run focused Val-agent tests:

```bash
PYTHONPATH=Val-agent python -m unittest Val-agent/tests/test_money.py Val-agent/tests/test_arithmetic.py
```

## Runtime Data

Local runtime data is stored under `Val-agent/data/`, including:

- `invoice_processing.sqlite3`
- `invoice_rag/`

These files are ignored by git. Do not commit local databases, model weights, OCR outputs, secrets, or generated logs.

