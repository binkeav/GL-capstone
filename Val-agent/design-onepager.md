# Design One-Pager: AI Agent-Based Invoice Validation Engine

## 1. Objective
Build a chat-based AI agent for invoice validation. The user interacts with the system conversationally to upload or reference extracted invoice JSON, ask validation questions, review exceptions, trigger re-validation, and request approved workflow actions.

Behind the chat interface, the agent coordinates specialist validation agents, executes deterministic finance checks through tools, and routes each invoice to auto-approval, human review, or critical rejection. Deterministic validation tools remain the source of truth for arithmetic, matching, duplicate, compliance, and fraud-control outcomes.

The first implementation is scoped to India-local invoice processing. The system assumes domestic transactions, INR currency, and GST-focused compliance. Bank remittance details such as routing numbers, IBAN, SWIFT, or payment account information are intentionally not collected, stored, or validated.

## 2. Agentic Design Principle
The system should be agentic where judgment, coordination, and explanation are useful, but deterministic where financial correctness is required.

- Agents may plan validation steps, call tools, compare evidence, summarize issues, and recommend routing.
- The primary user experience is conversational: users can ask what failed, why it failed, what evidence was used, and what action is allowed next.
- Agents must not override deterministic rule failures without an explicit human approval event.
- Every agent decision must be backed by tool output, reference data, or a logged confidence gap.
- All rule results must be auditable with `Rule_ID`, `Status`, `Expected_Value`, `Actual_Value`, and `Timestamp`.

## 3. High-Level Architecture

```text
User / AP Reviewer
        |
        v
Chat Agent
        |
        +--> Multimodal Extraction Layer
        |             |
        |             v
        |      Extracted Invoice JSON
        |
        v
Invoice Intake Agent
        |
        v
Supervisor Validation Agent
        |
        +--> Financial Validation Agent
        +--> Transaction Match Agent
        +--> Compliance & Risk Agent
        |
        v
Decision & Routing Agent
        |
        +--> ERP Auto-Approval
        +--> Human Exception Dashboard
        +--> Critical Lock + Supervisor Alert
        |
        v
Audit Trail + Agent Trace Store
```

## 4. Agent Roles

### Chat Agent
Acts as the user-facing conversational agent for AP reviewers, supervisors, and finance approvers.

- Accepts invoice JSON, invoice IDs, or user questions.
- Starts validation runs through the Supervisor Validation Agent.
- Explains validation results in plain language with cited rule IDs and evidence.
- Answers status questions such as "why was this rejected?" or "what needs review?"
- Guides users through allowed actions such as revalidate, correct extraction, escalate, reject, or approve with override.
- Enforces role permissions before invoking override or ERP submission tools.
- Does not change validation state directly; it calls approved workflow tools.

### Invoice Intake Agent
Normalizes the extracted AI JSON into the canonical invoice schema.

- Validates basic structure and required fields.
- Converts dates, currencies, quantities, and monetary values into normalized types.
- Maps requirement terms to `std_invoice.json` fields:
  - `Quantity` -> `order_items[].qty`
  - `Unit Price` -> `order_items[].unit_price`
  - `Line Amount` -> `order_items[].net_amount`
  - `Tax Amount` -> `tax`
  - `Grand Total Due` -> `total`
- Emits missing-field findings when required values are absent.

### Supervisor Validation Agent
Coordinates the validation workflow and delegates tasks to specialist agents.

- Chooses which agents must run based on available fields and policy.
- Ensures all mandatory rule packs are executed.
- Collects rule results into a single validation case file.
- Detects contradictions between agents and requests re-checks when needed.

### Financial Validation Agent
Executes arithmetic and amount consistency checks using deterministic calculator tools.

- Verifies `qty * unit_price == net_amount` with a variance of `0.01`.
- Verifies sum of line item amounts equals `subtotal`.
- Verifies `subtotal + tax + shipping - discounts == total`.
- Uses canonical `shipping` and `discounts` fields from `std_invoice.json`.

### Transaction Match Agent
Performs purchase order, goods receipt, and duplicate checks through internal lookup tools.

- Looks up `po_number`.
- Compares item descriptions, ordered quantities, invoiced quantities, and agreed prices.
- Checks received quantities against invoiced quantities.
- Flags over-billing, missing receipt records, and partial receipt cases.
- Checks `vendor_name + invoice_number`.
- Checks suspicious duplicates using `total + invoice_date`.
- Classifies verified active duplicates as fatal.

### Compliance & Risk Agent
Checks India-local regulatory, vendor, GST, and fraud-risk requirements.

- Ensures `invoice_date` is not in the future.
- Ensures invoice age is within the configured historical window, such as 90 days.
- Confirms required India GST identifiers are present, including vendor GSTIN where applicable.
- Checks that currency is supported by downstream ERP, with INR as the default local currency.
- Confirms the vendor exists in the vendor master.
- Flags GSTIN mismatch, unknown vendors, blocked vendors, unsupported currency, and high-risk duplicate signals.

### Decision & Routing Agent
Converts validation evidence into the final workflow state.

- Sends clean invoices to `STRAIGHT_THROUGH_PROCESSING`.
- Sends non-fatal failures to `HUMAN_IN_THE_LOOP`.
- Sends fatal failures to `CRITICAL_REJECTION`.
- Generates reviewer-facing explanations and error codes.

## 5. Tool Layer
Agents call tools rather than relying on model reasoning for financial checks.

| Tool | Used By | Purpose |
| --- | --- | --- |
| `conversation_store` | Chat Agent | Persist user and assistant messages |
| `invoice_case_lookup` | Chat Agent | Retrieve invoice status, rule results, and audit evidence |
| `schema_normalizer` | Intake Agent | Normalize extracted invoice JSON |
| `arithmetic_validator` | Financial Validation Agent | Run exact numeric validations |
| `po_lookup` | Transaction Match Agent | Retrieve PO header and line data |
| `goods_receipt_lookup` | Transaction Match Agent | Confirm received quantities |
| `duplicate_invoice_search` | Transaction Match Agent | Search historical invoice records |
| `vendor_master_lookup` | Compliance & Risk Agent | Confirm vendor identity, GSTIN, and vendor status |
| `compliance_policy_lookup` | Compliance & Risk Agent | Resolve India-local GST and currency rules |
| `erp_submitter` | Decision Agent | Submit approved invoices to ERP |
| `exception_case_writer` | Decision Agent | Create human review cases |
| `audit_logger` | All agents | Persist rule results and agent traces |

## 6. Agent Runtime and Persistence

- Agent runtime: `LangGraph`.
- Workflow shape: graph-based orchestration with a chat entry node and parallel validation branches after intake.
- Database: `SQLite` for local development and capstone demonstration.
- SQLite stores conversations, invoice cases, reference data stubs, rule results, agent traces, override events, and ERP submission status.
- The design should keep repository interfaces thin so SQLite can later be replaced by PostgreSQL or an enterprise database without changing agent logic.

## 7. Agent Workflow

```text
1. User sends a chat message with invoice JSON, invoice ID, or a question.
2. Chat Agent classifies intent: validate invoice, ask status, explain failure, correct data, or request override.
3. For validation requests, Intake Agent normalizes schema and records missing fields.
4. Supervisor Agent creates a validation plan.
5. LangGraph runs validation agents in parallel:
   - Financial Validation Agent
   - Transaction Match Agent
   - Compliance & Risk Agent
6. Supervisor Agent waits for all branches, aggregates rule outputs, and checks completeness.
7. Decision Agent applies routing policy.
8. Chat Agent explains the result and presents allowed next actions.
9. Audit Logger stores chat messages, rule logs, tool outputs, and agent explanations in SQLite.
10. ERP, exception dashboard, or supervisor alert receives the final payload when applicable.
```

## 8. Routing Policy

```text
if any FATAL rule fails:
    state = CRITICAL_REJECTION
elif any ERROR rule fails:
    state = HUMAN_IN_THE_LOOP
else:
    state = STRAIGHT_THROUGH_PROCESSING
```

Routing actions:
- `STRAIGHT_THROUGH_PROCESSING`: push invoice and validation metadata to ERP.
- `HUMAN_IN_THE_LOOP`: create an exception case with failed rules, evidence, and visual field references.
- `CRITICAL_REJECTION`: hard-lock the invoice, block ERP submission, and alert a supervisor.

### Severity Mapping

| Condition | Severity | Route |
| --- | --- | --- |
| `missing PO` | `ERROR` | `HUMAN_IN_THE_LOOP` |
| `partial receipt` | `ERROR` | `HUMAN_IN_THE_LOOP` |
| `duplicate suspicion` | `ERROR` | `HUMAN_IN_THE_LOOP` |
| `confirmed duplicate` | `FATAL` | `CRITICAL_REJECTION` |
| `blocked vendor` | `FATAL` | `CRITICAL_REJECTION` |
| `GSTIN mismatch` | `ERROR` | `HUMAN_IN_THE_LOOP` |

## 9. Validation Result Payload

```json
{
  "invoice": {},
  "validation": {
    "state": "HUMAN_IN_THE_LOOP",
    "errors": [
      "ERR_MATHEMATICAL_SUM_MISMATCH",
      "ERR_GSTIN_MISMATCH"
    ],
    "summary": {
      "rules_passed": 8,
      "rules_failed": 2,
      "fatal_failures": 0
    },
    "agent_trace_id": "trace_123",
    "audit_trail": [
      {
        "rule_id": "ARITH_SUBTOTAL_MATCH",
        "agent": "Financial Validation Agent",
        "status": "FAIL",
        "expected_value": "1500.00",
        "actual_value": "1525.00",
        "severity": "ERROR",
        "timestamp": "2026-06-26T00:00:00Z"
      }
    ]
  }
}
```

## 10. Memory and Audit Design

### Conversation Memory
Used by the Chat Agent to keep the current interaction coherent.

- Current invoice ID or active validation case.
- User role and allowed actions.
- Recent user questions and agent answers.
- Pending clarification or override request.

### Short-Term Case Memory
Used only while processing one invoice.

- Normalized invoice object.
- Tool outputs.
- Intermediate agent findings.
- Routing candidate and confidence gaps.

### Long-Term Audit Store
Persistent and queryable for compliance.

- Rule-level validation records.
- Agent trace IDs and tool-call metadata.
- Final routing state.
- Human override events.
- ERP submission status.

No bank remittance values are stored. Sensitive tax identifiers and source payloads should be protected in logs while retaining enough comparison evidence for audit.

## 11. SQLite Table Schema

SQLite is the source of persistence for the capstone implementation. Monetary values should be stored as `NUMERIC` values rounded to two decimal places by application code before insert.

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE invoices (
  invoice_id TEXT PRIMARY KEY,
  invoice_number TEXT,
  invoice_date TEXT,
  due_date TEXT,
  po_number TEXT,
  payment_terms TEXT,
  vendor_name TEXT,
  vendor_tax_id TEXT,
  customer_name TEXT,
  customer_tax_id TEXT,
  subtotal NUMERIC,
  tax NUMERIC,
  shipping NUMERIC DEFAULT 0,
  discounts NUMERIC DEFAULT 0,
  total NUMERIC,
  currency TEXT,
  source_payload_json TEXT NOT NULL,
  normalized_payload_json TEXT,
  validation_state TEXT NOT NULL DEFAULT 'PENDING'
    CHECK (validation_state IN (
      'PENDING',
      'STRAIGHT_THROUGH_PROCESSING',
      'HUMAN_IN_THE_LOOP',
      'CRITICAL_REJECTION'
    )),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE invoice_items (
  invoice_item_id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL,
  line_no INTEGER,
  description TEXT,
  qty NUMERIC,
  unit TEXT,
  unit_price NUMERIC,
  net_amount NUMERIC,
  tax_rate NUMERIC,
  gross_amount NUMERIC,
  FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);

CREATE TABLE conversations (
  conversation_id TEXT PRIMARY KEY,
  invoice_id TEXT,
  user_id TEXT,
  user_role TEXT,
  status TEXT NOT NULL DEFAULT 'OPEN'
    CHECK (status IN ('OPEN', 'CLOSED')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);

CREATE TABLE chat_messages (
  message_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  invoice_id TEXT,
  sender TEXT NOT NULL CHECK (sender IN ('USER', 'ASSISTANT', 'SYSTEM', 'TOOL')),
  message_text TEXT,
  message_json TEXT,
  intent TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);

CREATE TABLE vendors (
  vendor_id TEXT PRIMARY KEY,
  vendor_name TEXT NOT NULL,
  vendor_tax_id TEXT,
  status TEXT NOT NULL DEFAULT 'ACTIVE'
    CHECK (status IN ('ACTIVE', 'INACTIVE', 'BLOCKED')),
  default_currency TEXT,
  jurisdiction TEXT DEFAULT 'IN',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE purchase_orders (
  po_number TEXT PRIMARY KEY,
  vendor_id TEXT NOT NULL,
  status TEXT NOT NULL
    CHECK (status IN ('OPEN', 'PARTIALLY_RECEIVED', 'CLOSED', 'CANCELLED')),
  currency TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (vendor_id) REFERENCES vendors(vendor_id)
);

CREATE TABLE purchase_order_items (
  po_item_id TEXT PRIMARY KEY,
  po_number TEXT NOT NULL,
  line_no INTEGER,
  description TEXT,
  ordered_qty NUMERIC,
  unit TEXT,
  unit_price NUMERIC,
  FOREIGN KEY (po_number) REFERENCES purchase_orders(po_number)
);

CREATE TABLE goods_receipts (
  receipt_id TEXT PRIMARY KEY,
  po_number TEXT NOT NULL,
  po_item_id TEXT,
  received_qty NUMERIC NOT NULL,
  received_date TEXT NOT NULL,
  status TEXT NOT NULL
    CHECK (status IN ('RECEIVED', 'PARTIAL', 'REJECTED', 'REVERSED')),
  FOREIGN KEY (po_number) REFERENCES purchase_orders(po_number),
  FOREIGN KEY (po_item_id) REFERENCES purchase_order_items(po_item_id)
);

CREATE TABLE compliance_policies (
  policy_id TEXT PRIMARY KEY,
  jurisdiction TEXT NOT NULL DEFAULT 'IN',
  required_tax_id_type TEXT DEFAULT 'GSTIN',
  max_invoice_age_days INTEGER NOT NULL DEFAULT 90,
  supported_currency TEXT NOT NULL DEFAULT 'INR',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE rule_results (
  rule_result_id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  agent_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PASS', 'FAIL', 'SKIPPED')),
  severity TEXT NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'ERROR', 'FATAL')),
  expected_value TEXT,
  actual_value TEXT,
  error_code TEXT,
  evidence_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);

CREATE TABLE agent_traces (
  trace_id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL,
  langgraph_run_id TEXT,
  agent_name TEXT NOT NULL,
  node_name TEXT,
  input_json TEXT,
  output_json TEXT,
  tool_calls_json TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
  FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);

CREATE TABLE override_events (
  override_id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL,
  previous_state TEXT NOT NULL,
  new_state TEXT NOT NULL,
  failed_rule_ids_json TEXT NOT NULL,
  approver_role TEXT NOT NULL,
  approver_user_id TEXT NOT NULL,
  override_reason TEXT NOT NULL,
  supporting_notes TEXT,
  revalidation_required INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);

CREATE TABLE erp_submissions (
  submission_id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL,
  submission_status TEXT NOT NULL
    CHECK (submission_status IN ('NOT_SUBMITTED', 'SUBMITTED', 'ACCEPTED', 'REJECTED')),
  erp_reference_id TEXT,
  submitted_at TEXT,
  response_json TEXT,
  FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);

CREATE INDEX idx_invoices_vendor_invoice
  ON invoices(vendor_name, invoice_number);

CREATE INDEX idx_invoices_total_date
  ON invoices(total, invoice_date);

CREATE INDEX idx_invoice_items_invoice_id
  ON invoice_items(invoice_id);

CREATE INDEX idx_conversations_invoice_id
  ON conversations(invoice_id);

CREATE INDEX idx_chat_messages_conversation_id
  ON chat_messages(conversation_id);

CREATE INDEX idx_rule_results_invoice_id
  ON rule_results(invoice_id);

CREATE INDEX idx_agent_traces_invoice_id
  ON agent_traces(invoice_id);

CREATE INDEX idx_override_events_invoice_id
  ON override_events(invoice_id);
```

## 12. Human Override Workflow and Permissions

Human override is allowed only for invoices routed to `HUMAN_IN_THE_LOOP` or for selected recoverable `CRITICAL_REJECTION` cases after supervisor review. Overrides never delete the original validation result; they append a new approval event to the audit trail.

### Workflow

```text
1. Decision Agent creates an exception case with failed rules and evidence.
2. AP Reviewer reviews invoice fields, source document, and agent/tool findings.
3. Reviewer chooses one action:
   - approve_with_override
   - request_vendor_or_buyer_clarification
   - correct_extraction_and_revalidate
   - reject_invoice
   - escalate_to_supervisor
4. If approval requires higher permission, route to Supervisor or Finance Controller.
5. Final decision is written to the audit store.
6. ERP submission is allowed only after required approvals are complete.
```

### Approval Permissions

| Role | Allowed Actions | Restrictions |
| --- | --- | --- |
| `AP_REVIEWER` | Correct extraction errors, re-run validation, approve low-risk `ERROR` exceptions | Cannot override `FATAL` failures |
| `AP_SUPERVISOR` | Approve missing PO, partial receipt, tax metadata, and moderate amount mismatches with justification | Cannot approve confirmed duplicate or unknown vendor without controller approval |
| `FINANCE_CONTROLLER` | Approve high-value exceptions and selected recoverable critical cases | Cannot approve confirmed fraud or blocked vendor |
| `COMPLIANCE_OFFICER` | Approve GST or regulatory metadata exceptions | Cannot approve payment release alone |
| `SYSTEM_ADMIN` | Manage policies, roles, and thresholds | Cannot approve invoice payment overrides |

### Override Rules

- `missing PO`: requires `AP_SUPERVISOR` approval unless invoice is configured as non-PO eligible.
- `partial receipt`: requires `AP_SUPERVISOR` approval and may allow partial payment only up to received quantity.
- `duplicate suspicion`: requires `AP_REVIEWER` investigation; confirmed active duplicates remain `FATAL`.
- `confirmed duplicate`, `blocked vendor`, or confirmed fraud: no payment override allowed.

### Required Audit Fields

Every override event must capture:

- `override_id`
- `invoice_id`
- `previous_state`
- `new_state`
- `failed_rule_ids`
- `approver_role`
- `approver_user_id`
- `override_reason`
- `supporting_notes`
- `timestamp`
- `revalidation_required`

## 13. Guardrails

- Chat responses must clearly distinguish validation facts from suggested next actions.
- The Chat Agent may explain and guide, but cannot directly mark an invoice as approved, rejected, or overridden without calling the proper workflow tool.
- No agent can auto-approve an invoice if any mandatory validation tool failed.
- No agent can invent missing PO, receipt, GST, vendor, or transaction data.
- No agent or tool should request, store, or validate bank remittance details for the India-local scope.
- Human overrides must include reviewer identity, reason, timestamp, and changed routing state.
- Fatal duplicate and unknown vendor failures must block ERP submission.
- Model-generated explanations must cite rule IDs and tool evidence.
- Tool failures should route to `HUMAN_IN_THE_LOOP` unless policy marks the failure as critical.

## 14. Data and Integration Dependencies

Primary persistence for the capstone build is SQLite.

- Procurement database lookup by `po_number`.
- Goods receipt or inventory lookup by PO and line item.
- Historical invoice search by:
  - `vendor_name + invoice_number`
  - `total + invoice_date`
- Vendor master lookup for legal identity, GSTIN, and vendor status.
- Compliance policy source for India-local GST requirements and INR currency support.
- ERP API for approved invoice submission.
- Exception dashboard API for human review cases.

## 15. Open Decisions

- None for the current capstone scope.
