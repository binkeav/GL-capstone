# LLD One-Pager: Chat-Based Invoice Validation Agent

## 1. MVP Scope
Build a local India-focused chat agent that accepts invoice JSON, runs deterministic validation through LangGraph, stores all evidence in SQLite, and replies with clear validation status, failed rules, and allowed next actions.

Out of scope for MVP: bank remittance details, real ERP integration, real OCR/extraction, production auth, and external procurement systems.

## 2. Proposed Python Structure

```text
val_agent/
  app.py                  # CLI or simple chat entrypoint
  graph.py                # LangGraph state graph and node wiring
  state.py                # ValidationState TypedDict / dataclass
  models.py               # Invoice, item, rule result models
  db.py                   # SQLite connection and repository helpers
  migrations/001_init.sql # SQLite schema from design-onepager.md
  tools/
    chat.py               # conversation_store, invoice_case_lookup
    normalize.py          # schema_normalizer
    arithmetic.py         # arithmetic_validator
    matching.py           # po_lookup, goods_receipt_lookup, duplicate search
    compliance.py         # vendor_master_lookup, GST/INR policy lookup
    routing.py            # decision, exception writer, ERP stub
  agents/
    chat_agent.py
    intake_agent.py
    supervisor_agent.py
    financial_agent.py
    transaction_agent.py
    compliance_risk_agent.py
    decision_agent.py
tests/
  test_arithmetic.py
  test_routing.py
  test_graph_flow.py
```

## 3. LangGraph State

```python
class ValidationState(TypedDict):
    conversation_id: str
    user_id: str
    user_role: str
    user_message: str
    intent: str
    invoice_id: str | None
    raw_invoice: dict | None
    normalized_invoice: dict | None
    rule_results: list[dict]
    errors: list[str]
    final_state: Literal[
        "PENDING",
        "STRAIGHT_THROUGH_PROCESSING",
        "HUMAN_IN_THE_LOOP",
        "CRITICAL_REJECTION",
    ]
    assistant_response: str
```

## 4. Graph Nodes

```text
chat_intake
  -> if intent == validate_invoice: normalize_invoice
  -> if intent == ask_status: lookup_case -> respond
  -> if intent == request_override: permission_check -> respond

normalize_invoice
  -> supervisor_plan

supervisor_plan
  -> financial_validation
  -> transaction_match
  -> compliance_risk

financial_validation + transaction_match + compliance_risk run in parallel.

aggregate_results
  -> decision_route
  -> persist_audit
  -> respond
```

## 5. Deterministic Rule IDs

| Rule ID | Agent | Failure Code |
| --- | --- | --- |
| `ARITH_LINE_ITEM_MATCH` | Financial | `ERR_LINE_ITEM_AMOUNT_MISMATCH` |
| `ARITH_SUBTOTAL_MATCH` | Financial | `ERR_SUBTOTAL_MISMATCH` |
| `ARITH_TOTAL_MATCH` | Financial | `ERR_GRAND_TOTAL_MISMATCH` |
| `MATCH_PO_EXISTS` | Transaction | `ERR_MISSING_PO` |
| `MATCH_GOODS_RECEIVED` | Transaction | `ERR_PARTIAL_RECEIPT` |
| `DUP_VENDOR_INVOICE_NUMBER` | Transaction | `ERR_CONFIRMED_DUPLICATE` |
| `DUP_TOTAL_DATE` | Transaction | `ERR_DUPLICATE_SUSPICION` |
| `COMP_DATE_WINDOW` | Compliance | `ERR_INVALID_INVOICE_DATE` |
| `COMP_GSTIN_PRESENT` | Compliance | `ERR_MISSING_GSTIN` |
| `COMP_GSTIN_MATCH` | Compliance | `ERR_GSTIN_MISMATCH` |
| `COMP_CURRENCY_INR` | Compliance | `ERR_UNSUPPORTED_CURRENCY` |
| `RISK_VENDOR_ACTIVE` | Compliance | `ERR_BLOCKED_VENDOR` |

Severity defaults:
- `FATAL`: confirmed duplicate, blocked vendor.
- `ERROR`: missing PO, partial receipt, duplicate suspicion, GSTIN mismatch, arithmetic mismatch, invalid date, unsupported currency.
- `INFO` / `WARNING`: reserved for non-blocking observations.

## 6. Tool Signatures

```python
def normalize_invoice(raw_invoice: dict) -> dict: ...
def validate_arithmetic(invoice: dict) -> list[RuleResult]: ...
def lookup_po(po_number: str) -> dict | None: ...
def lookup_goods_receipts(po_number: str) -> list[dict]: ...
def search_duplicates(invoice: dict) -> list[RuleResult]: ...
def lookup_vendor(vendor_name: str, vendor_tax_id: str | None) -> dict | None: ...
def lookup_compliance_policy(jurisdiction: str = "IN") -> dict: ...
def decide_route(rule_results: list[RuleResult]) -> str: ...
def save_rule_results(invoice_id: str, results: list[RuleResult]) -> None: ...
def save_chat_message(conversation_id: str, sender: str, text: str) -> None: ...
```

## 7. SQLite MVP Tables
Implement the full schema from `design-onepager.md`, but seed only the MVP reference tables first:

- `vendors`
- `purchase_orders`
- `purchase_order_items`
- `goods_receipts`
- `compliance_policies`
- `invoices`
- `invoice_items`
- `rule_results`
- `agent_traces`
- `conversations`
- `chat_messages`

ERP and override tables can be created now but used as stubs until the chat validation flow works.

## 8. Chat Behaviors

Supported intents:
- `validate_invoice`: user provides invoice JSON.
- `ask_status`: user asks about an invoice ID.
- `explain_failure`: user asks why a rule failed.
- `correct_and_revalidate`: user provides corrected fields.
- `request_override`: user asks to approve despite failures.

Response format:
```text
State: HUMAN_IN_THE_LOOP
Failed checks: ARITH_TOTAL_MATCH, COMP_GSTIN_MATCH
Why: total expected INR 1,180.00 but invoice says INR 1,200.00; vendor GSTIN differs from vendor master.
Next actions: correct extraction and revalidate, or escalate to AP_SUPERVISOR.
```

## 9. MVP Test Cases

1. Happy path: all arithmetic, PO, receipt, duplicate, GST, INR, and vendor checks pass -> `STRAIGHT_THROUGH_PROCESSING`.
2. Arithmetic failure: subtotal or total mismatch -> `HUMAN_IN_THE_LOOP`.
3. Missing PO -> `HUMAN_IN_THE_LOOP` with `ERR_MISSING_PO`.
4. Partial receipt -> `HUMAN_IN_THE_LOOP` with `ERR_PARTIAL_RECEIPT`.
5. Confirmed duplicate -> `CRITICAL_REJECTION`.
6. Blocked vendor -> `CRITICAL_REJECTION`.
7. GSTIN mismatch -> `HUMAN_IN_THE_LOOP`.
8. Chat status lookup returns stored rule results and final state.

## 10. Build Order

1. Create SQLite migration and seed data.
2. Implement data models and DB helpers.
3. Implement deterministic validators.
4. Wire LangGraph nodes and routing.
5. Add chat CLI entrypoint.
6. Add MVP tests.
7. Add seeded demo invoices for pass, review, and critical rejection.
