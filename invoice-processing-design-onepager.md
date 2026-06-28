# Design One-Pager: Unified Invoice Processing Application

## 1. Compatibility Verdict
The current OCR agent output and Val-agent input are compatible enough to integrate, but they should not be wired together by passing the full OCR response directly.

Use this integration contract:

```text
Val-agent input = adapt_ocr_fields(ocr_result["fields"])
```

The OCR backend emits a `fields` object with the same primary keys Val-agent expects: invoice header fields, vendor/customer tax fields, totals, currency, and `order_items`. Val-agent also expects `shipping` and `discounts`; OCR does not currently emit them, but Val-agent already normalizes missing values to `0.00`.

The older artifact `ocr-agent/outputs/extracted_invoices.jsonl` uses a different nested shape (`invoice`, `items`, `subtotal`) and should not be treated as the live integration contract.

## 2. Schema Fit

| Val-agent Field | OCR `fields` Output | Match | Adapter Rule |
| --- | --- | --- | --- |
| `invoice_number` | `invoice_number` | Yes | Pass through |
| `invoice_date` | `invoice_date` | Yes | Pass through, normalize date later if needed |
| `due_date` | `due_date` | Yes | Pass through |
| `po_number` | `po_number` | Yes | Pass through |
| `payment_terms` | `payment_terms` | Yes | Pass through |
| `vendor_name` | `vendor_name` | Yes | Pass through |
| `vendor_tax_id` | `vendor_tax_id` | Yes | Pass through |
| `customer_name` | `customer_name` | Yes | Pass through |
| `customer_tax_id` | `customer_tax_id` | Yes | Pass through |
| `subtotal` | `subtotal` | Yes | Pass through as string/number |
| `tax` | `tax` | Yes | Pass through as string/number |
| `shipping` | Not emitted | Partial | Default to `0` |
| `discounts` | Not emitted | Partial | Default to `0` |
| `total` | `total` | Yes | Pass through as string/number |
| `currency` | `currency` | Yes | Pass through; Val-agent defaults missing value to `INR` |
| `order_items[].qty` | `order_items[].qty` | Yes | Pass through |
| `order_items[].unit_price` | `order_items[].unit_price` | Yes | Pass through |
| `order_items[].net_amount` | `order_items[].net_amount` | Yes | Pass through or let Val-agent derive `qty * unit_price` |
| `order_items[].gross_amount` | `order_items[].gross_amount` | Yes | Pass through or let Val-agent default to `net_amount` |

## 3. Objective
Create a single invoice processing application that accepts invoice documents, extracts structured invoice data, optionally validates the invoice against finance and compliance rules, and returns a reviewer-ready processing result.

The unified app combines:

- OCR Agent: document ingestion, OCR, field extraction, OCR confidence, layout metadata.
- Val-agent: schema normalization, arithmetic validation, PO/receipt matching, duplicate checks, compliance checks, decision routing, and user-facing explanation.

Current orchestration: a top-level Invoice Processing Orchestrator Graph calls OCR and Val-agent as separate subgraphs. OCR extracts fields and evidence; Val-agent remains the dedicated deterministic validation subgraph.

The app supports two upload actions:

- `extract_only`: extract and show invoice fields without running validation.
- `validate`: extract, adapt, quality-check, and run Val-agent validation.

When the upload request is ambiguous, the app falls back to the broader invoice-processing chat classifier instead of assuming validation.

## 4. High-Level Architecture

```text
User / AP Reviewer
        |
        v
Unified Streamlit Invoice App
        |
        v
Document Upload + Upload Intent Classifier
  - extract_only
  - validate
  - unknown fallback
        |
        v
Invoice Processing Orchestrator Graph
        |
        v
Document Intake Node
        |
        v
OCR Extraction Subgraph
  - image/PDF/DOCX handling
  - EasyOCR text extraction
  - multi-variant preprocessing for dark-background invoices
  - GPT/Qwen/regex field extraction
  - OCR confidence + evidence
        |
        v
OCR-to-Validation Adapter
  - select `ocr_result["fields"]`
  - add `shipping = 0`
  - add `discounts = 0`
  - attach OCR evidence separately
        |
        v
Extraction Quality Gate
  - continue when required fields are usable
  - route to correction when confidence is low
        |
        +----------------------+
        |                      |
        v                      v
Extraction-only Result    Val-agent Validation Subgraph
  - COMPLETE              - chat/intake-compatible invoice payload
  - NEEDS_REVIEW          - normalize invoice schema
                          - persist invoice case
                          - run validation agents in parallel
                          - decide route
                          - persist audit trail
        |                      |
        +----------+-----------+
                   |
                   v
Final Invoice State / Extraction Status
  - PENDING for clean extraction-only
  - STRAIGHT_THROUGH_PROCESSING
  - HUMAN_IN_THE_LOOP
  - CRITICAL_REJECTION
        |
        v
Reviewer Explanation + Audit Evidence
```

## 5. Multilayer LangGraph Design

The unified app introduces a graph above the existing agents. This graph coordinates the end-to-end invoice lifecycle and leaves Val-agent's internal validation graph unchanged.

```text
invoice_processing_orchestrator
    |
    v
document_intake
    |
    v
ocr_extraction_subgraph
    |
    v
ocr_to_validation_adapter
    |
    v
extraction_quality_gate
    |                         |                         |
    | extract_only            | review                  | validate
    v                         v                         v
extraction_only_result    human_correction         val_agent_subgraph
    |                         |                         |
    v                         v                         v
    END                  unified_result_presenter   unified_result_presenter
```

### Orchestrator Graph

Responsibilities:

- Own the end-to-end case lifecycle.
- Route between OCR, extraction-only display, extraction review, validation, and result presentation.
- Preserve subgraph boundaries so OCR and Val-agent can evolve independently.
- Store cross-cutting trace metadata such as case ID, file name, OCR evidence, validation result, extraction status, and final display payload.

### OCR Extraction Subgraph

Responsibilities:

- Accept uploaded document bytes and file metadata from the orchestrator.
- Call `Milestone1NotebookAPI.process_upload`.
- Produce `ocr_result`, including raw OCR text, confidence, extraction mode, layout metadata, and extracted fields.
- Use multiple OCR preprocessing variants for difficult scans, including adaptive thresholding, inverted thresholding, CLAHE, inverted CLAHE, grayscale, and inverted grayscale.
- Return evidence and extracted fields only, not approval or rejection decisions.

### OCR-to-Validation Adapter

Responsibilities:

- Read `ocr_result["fields"]`.
- Produce canonical `invoice_payload`.
- Add `shipping = 0` and `discounts = 0` when missing.
- Preserve `ocr_evidence` separately from the validation payload.

### Extraction Quality Gate

Responsibilities:

- Check required fields such as `invoice_number`, `vendor_name`, `invoice_date`, `total`, and `currency`.
- Check OCR confidence and extraction fallback mode.
- Route to `extraction_only_result` when the user asked only to extract fields.
- Route to `val_agent_subgraph` when the user asked for validation and extraction is usable.
- Route to `human_correction` when extraction is incomplete or low-confidence.

The gate may create a `HUMAN_IN_THE_LOOP` outcome for extraction quality issues, but it should not approve or reject invoices.

### Extraction-only Result

Responsibilities:

- End the graph after OCR, adaptation, and quality checking.
- Return `extraction_status = COMPLETE` when extracted fields are usable.
- Return `extraction_status = NEEDS_REVIEW` and `final_state = HUMAN_IN_THE_LOOP` when required fields are missing, OCR confidence is low, or field extraction warnings exist.
- Preserve `validation_result = {}` so downstream UI and chat can distinguish extraction-only results from validated invoices.

### Val-agent Validation Subgraph

Responsibilities:

- Accept the adapted `invoice_payload`.
- Reuse the existing Val-agent graph as-is for normalization, persistence, financial validation, transaction matching, compliance/risk checks, routing, audit persistence, and response generation.
- Return `validation_result`, including normalized invoice, rule results, final state, invoice ID, and assistant response.

The orchestrator should treat Val-agent as a stable validation engine and should not duplicate its rule logic.

## 6. Adapter Contract

The adapter should keep validation input clean and preserve OCR metadata separately.

```json
{
  "invoice_payload": {
    "invoice_number": "INV-001",
    "invoice_date": "2026-06-15",
    "due_date": "2026-07-15",
    "po_number": "PO-1001",
    "payment_terms": "Net 30",
    "vendor_name": "Acme Supplies India",
    "vendor_tax_id": "29ABCDE1234F1Z5",
    "customer_name": "Demo Buyer India",
    "customer_tax_id": "29ZZZZZ9999Z1Z5",
    "subtotal": "1200.00",
    "tax": "216.00",
    "shipping": "0.00",
    "discounts": "0.00",
    "total": "1416.00",
    "currency": "INR",
    "order_items": []
  },
  "ocr_evidence": {
    "source_file": "invoice.pdf",
    "avg_confidence": 0.91,
    "extraction_mode": "gpt-4o-mini",
    "layout_summary": {},
    "raw_text": "..."
  },
  "extraction_quality": {
    "review_required": false,
    "missing_required_fields": [],
    "low_confidence": false,
    "confidence": 0.91,
    "reasons": []
  },
  "extraction_status": "COMPLETE"
}
```

Val-agent should receive only `invoice_payload` for deterministic validation. `ocr_evidence` should be stored for reviewer display and audit context, not used as a substitute for normalized invoice fields.

## 7. Unified Workflow

```text
1. Reviewer uploads invoice document and optionally types an instruction.
2. Upload Intent Classifier returns `extract_only`, `validate`, or `unknown`.
3. If the action is unknown, the full invoice-processing chat classifier decides the best app intent.
4. Overall Invoice Processing Orchestrator Graph starts the invoice case.
5. Orchestrator calls the OCR Extraction Subgraph.
6. OCR Agent extracts text and structured fields.
7. Adapter converts OCR fields to Val-agent invoice payload and preserves OCR evidence.
8. Extraction Quality Gate checks required fields and confidence.
9. If the user asked for extraction only, the graph exits with extracted fields and `extraction_status`.
10. If validation was requested and extraction needs correction, orchestrator routes to human review.
11. If validation was requested and extraction is usable, orchestrator calls the Val-agent Validation Subgraph.
12. Val-agent normalizes money, dates, line items, and defaults.
13. Val-agent validation agents run in parallel:
   - Financial Validation Agent
   - Transaction Match Agent
   - Compliance & Risk Agent
14. Val-agent Decision Agent assigns final route.
15. Orchestrator combines OCR evidence, extraction quality, and validation result.
16. Unified app shows:
   - extracted fields and line items
   - extraction notes and review reasons
   - validation state when validation ran
   - failed/passed rule results with actual values
   - trace events across OCR, adapter, quality gate, and validation
   - reviewer explanation and next action
```

## 8. Chat and Intent Design

The Streamlit app uses two intent layers:

### Upload Intent Classifier

The upload classifier is used only when a file is attached. It returns a tiny schema:

```json
{
  "intent": "extract_only | validate | unknown"
}
```

Routing:

- `extract_only` maps to `show_extracted_fields` and runs the graph with `extraction_only = true`.
- `validate` maps to `document_processed` and runs the full validation path.
- `unknown` falls back to the broader invoice-processing chat classifier.

This prevents an attached-file request such as "extract the details" from accidentally running deterministic validation, while still allowing natural language when the user is not explicit.

### Processing Chat Intents

Supported processing chat intents:

- `validate_invoice_json`
- `validate_current_invoice`
- `show_field_value`
- `show_extracted_fields`
- `search_uploaded_invoices`
- `explain_current_result`
- `show_policy`
- `show_trace`
- `general_chat`

The chat layer uses deterministic renderers for extracted fields, single-field answers, direct uploaded-invoice lists, and failed-rule detail blocks. It uses the LLM for concise summaries, policy explanations, trace summaries, and retrieval-grounded answers.

Chat prompts use a bounded memory window instead of the full transcript. Defaults:

- last `12` messages
- max `4000` characters

These can be tuned with `INVOICE_PROCESSING_MEMORY_WINDOW_MESSAGES` and `INVOICE_PROCESSING_MEMORY_WINDOW_CHARS`.

## 9. Persistence, RAG, and Traceability

The app persists conversation messages and uploaded invoice records in SQLite. Successful uploads are indexed into a local TF-IDF RAG store so the user can ask cross-invoice questions such as:

- "list failed invoices"
- "show invoices from Acme"
- "which invoices are missing PO?"

The UI also shows a unique uploaded-invoice list in the sidebar. It deduplicates by vendor plus invoice number when available, and falls back to source file, invoice date, and total when invoice identity is incomplete.

Stored RAG context includes:

- source file
- invoice number
- vendor name
- invoice date
- totals and currency
- final state or extraction state
- failed validation rule evidence
- raw OCR text

Trace responses include:

- progress events from the top-level graph
- extracted values such as subtotal, tax, shipping, discounts, and total
- OCR evidence such as confidence and extraction mode
- validation rule expected values, actual values, formulas, and evidence values

## 10. Error and Review Messaging

Errors and review outcomes are separated:

- Hard processing errors use `processing_error` and explain what failed, such as Qwen unavailable, Qwen parse failure, GPT unavailable, OCR failure, or field extraction failure.
- Extraction quality issues use `extraction_status = NEEDS_REVIEW`, not a hard error.
- Validation failures include deterministic detail blocks with rule title, explanation, expected value, actual value, formula, evidence values, code, and currency when relevant.
- Noisy OCR amount strings are normalized before Decimal parsing, so values such as currency-prefixed amounts, comma-formatted totals, braces, trailing punctuation, and digit-spaced amounts do not crash validation.

Example validation detail:

```text
Subtotal Mismatch: The calculated subtotal does not match the extracted subtotal;
Expected: 1200.00; Actual: 1000.00; Formula: sum(line net amounts) = subtotal;
Values: line_net_sum=1200.00, subtotal=1000.00; Code: ERR_SUBTOTAL_MISMATCH
```

## 11. Integration Design Choices

### Recommended MVP
Use one Streamlit app backed by a top-level LangGraph orchestration:

- `invoice_processing_orchestrator` owns the full case flow.
- `ocr_extraction_subgraph` calls `Milestone1NotebookAPI.process_upload(file)`.
- `ocr_to_validation_adapter` maps OCR output into Val-agent input.
- `extraction_quality_gate` decides whether to show extracted fields, continue to validation, or request correction.
- `extraction_only_result` exits after extraction when validation was not requested.
- `val_agent_subgraph` calls the existing Val-agent graph without changing its internal nodes.
- `unified_result_presenter` combines OCR evidence and Val-agent results for the UI.

This preserves Val-agent as an independent validation engine while giving the application one end-to-end graph for progress events, branching, audit trace, and final state.

### Boundary
Keep OCR and validation decoupled through the orchestrator state and adapter. OCR may be uncertain; Val-agent validation must remain deterministic and auditable.

### Human Review
If OCR confidence is low or required fields are missing, mark extraction as needing review. For validation requests, route to `human_correction` before Val-agent validation. For extraction-only requests, show extracted fields with review notes and do not present the result as clean.

## 12. Risks and Controls

| Risk | Control |
| --- | --- |
| OCR extracts wrong vendor or total | Show extracted fields before validation and preserve raw OCR text |
| OCR emits missing `shipping` / `discounts` | Adapter defaults both to `0` |
| OCR confidence is low | Quality gate sets review reasons and blocks validation unless corrected |
| Dark-background invoice text is missed | Try and merge multiple OCR preprocessing variants, including inverted and contrast-enhanced variants |
| Qwen is slow on CPU | Keep GPT/regex fallback available; cap Qwen generation time |
| Old OCR output artifact has different schema | Use live `ocr_result["fields"]`, not old JSONL artifacts |
| Validation requires India-local data | Keep Val-agent policy defaults: INR, GSTIN, active vendor, 90-day window |
| OCR node becomes too powerful | Restrict OCR to extraction and confidence signals; final routing remains with Decision Agent |
| Graph state becomes noisy | Store `invoice_payload`, `ocr_evidence`, and `validation_result` separately |
| Val-agent internals become coupled to OCR | Call Val-agent as a subgraph/service through a stable `invoice_payload` contract |
| Upload intent is misclassified | Use a tiny upload-intent schema and fallback only for unknown requests |
| DB handles leak across uploads | Close orchestrator SQLite connections with `try/finally` |
| Extraction-only records look validated | Preserve `validation_result = {}` and expose `extraction_status` |
| Cached OCR API state crosses sessions | Prefer passing extraction mode per request or isolate API instances per session |
| OCR emits malformed numeric strings | Normalize OCR amount noise before Decimal conversion |

## 13. Success Criteria

- User can upload an invoice and get a validation decision in one flow.
- User can upload an invoice and request extraction-only without validation running.
- OCR output is converted to Val-agent input without manual JSON reshaping.
- Missing optional financial fields default safely.
- Every final decision includes rule evidence, actual values, and OCR evidence.
- Clean invoices route to `STRAIGHT_THROUGH_PROCESSING`.
- Exceptions route to `HUMAN_IN_THE_LOOP`.
- Fatal issues route to `CRITICAL_REJECTION`.
- Extraction-only quality issues show `NEEDS_REVIEW` style messaging rather than a clean complete outcome.
- Top-level LangGraph trace shows extraction, adaptation, quality gate, validation, decision, and audit steps in one flow.
- Val-agent graph remains reusable for non-OCR invoice JSON input.
- Uploaded invoices can be listed and filtered by status/vendor in chat.
- UI lists unique uploaded invoices in the sidebar.
- Chat responses use a bounded previous-message context window.

## 14. Current Implementation Map

| Concern | Current Location |
| --- | --- |
| Streamlit app | `invoice_processing_app.py` |
| Top-level LangGraph orchestration | `invoice_processing/orchestrator.py` |
| OCR-to-validation adapter | `invoice_processing/adapter.py` |
| Chat intents and response formatting | `invoice_processing/chat_agent.py` |
| Uploaded-invoice RAG store | `invoice_processing/rag_store.py` |
| OCR backend | `ocr-agent/milestone2_backend.py` |
| Validation graph | `Val-agent/val_agent/graph.py` |
| Money parsing and typed results | `Val-agent/val_agent/models.py` |
| Arithmetic validation details | `Val-agent/val_agent/tools/arithmetic.py` |

## 15. Remaining Implementation Work

1. Add focused tests for upload-intent routing:
   - `extract the details` -> extraction-only
   - `validate this invoice` -> full validation
   - ambiguous upload text -> safe fallback/clarification behavior
2. Add graph tests for:
   - extraction-only exits before Val-agent
   - extraction-only with missing fields sets `extraction_status = NEEDS_REVIEW`
   - validation path still routes through Val-agent
3. Store `processing_mode` in the uploaded-invoice RAG table so extraction-only and validated records can be filtered clearly.
4. Avoid mutable shared OCR API state by passing extraction mode per request or creating session-scoped OCR API instances.
5. Add a reviewer correction workflow for `HUMAN_IN_THE_LOOP` extraction results.
