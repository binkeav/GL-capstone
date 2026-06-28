# Invoice Processing App

Top-level orchestration layer for OCR extraction plus Val-agent validation.

## Run

From the repository root:

```bash
ocr-agent/.venv/bin/streamlit run invoice_processing_app.py --server.port 8501
```

Then open:

```text
http://127.0.0.1:8501
```

## Flow

```text
Document upload
  -> OCR Extraction Subgraph
  -> OCR-to-Validation Adapter
  -> Extraction Quality Gate
  -> If user asks for extracted fields only: return OCR output
  -> Val-agent Validation Subgraph
  -> Unified Result Presenter
```

The Val-agent graph is reused through `run_validation_graph` and is not embedded into the OCR code.

## Chat Agent

The app includes an invoice-processing chat section backed by SQLite conversation memory, LLM-only intent detection and response summarization, and the Val-agent graph for validation actions. `OPENAI_API_KEY` must be configured for chat interactions.

Supported demo flows:

- Ask policy or capability questions.
- Upload an invoice and ask for extracted fields only.
- Ask cross-invoice questions over uploaded invoices, such as `show invoices from Acme` or `which invoices failed because of missing PO?`.
- Ask to validate the current extracted invoice.
- Ask follow-up questions such as `why did it fail?`, `show trace`, or `what should I do next?`.
- Start a fresh conversation with `New Chat`.

Supported chat intents:

- `validate_invoice_json`
- `validate_current_invoice`
- `show_field_value`
- `show_extracted_fields`
- `search_uploaded_invoices`
- `explain_current_result`
- `show_policy`
- `show_trace`
- `general_chat`

Chat history is stored in:

```text
Val-agent/data/invoice_processing.sqlite3
```

## Uploaded-Invoice RAG

Successful invoice uploads are indexed into a local persistent RAG store:

```text
Val-agent/data/invoice_processing.sqlite3
Val-agent/data/invoice_rag/
```

The app stores OCR text, extracted fields, validation state, and failed-rule evidence in SQLite, then builds a local TF-IDF vector index under `invoice_rag/`. Chat questions about prior uploads retrieve matching invoice chunks and use the LLM to answer from that retrieved context.
