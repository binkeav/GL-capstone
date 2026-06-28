# Val Agent

Local chat-based invoice validation agent for India-local invoice processing.

## Install Runtime Dependencies

```bash
cd /Users/binuk/GL-Capstone
uv add langgraph
```

`langgraph` is the intended graph runtime and is installed in the parent `gl-capstone` uv environment.

## Required LLM Chat Layer

Intent classification and response summarization use the OpenAI SDK. Deterministic validation does not use the LLM.

```bash
source .env
```

Set `OPENAI_API_KEY` inside `.env` before running live LLM calls. `OPENAI_BASE_URL` defaults to `https://aibe.mygreatlearning.com/openai/v1`.

## Run Demo

Run from the project directory:

```bash
cd /Users/binuk/GL-Capstone/Val-agent
```

Demo invoices are available under `data/`:

| File | Expected route | Scenario |
| --- | --- | --- |
| `data/demo_invoice_pass.json` | `STRAIGHT_THROUGH_PROCESSING` | Clean invoice with matching PO, receipt, totals, GSTIN, currency, and active vendor. |
| `data/demo_invoice_review.json` | `HUMAN_IN_THE_LOOP` | Review case with a missing PO and grand-total mismatch. |
| `data/demo_invoice_multiple_failures.json` | `HUMAN_IN_THE_LOOP` | Combined review case with line-item, subtotal, grand-total, receipt, and currency failures. |
| `data/demo_invoice_error_line_item_amount.json` | `HUMAN_IN_THE_LOOP` | Single `ERR_LINE_ITEM_AMOUNT_MISMATCH` failure. |
| `data/demo_invoice_error_subtotal.json` | `HUMAN_IN_THE_LOOP` | Single `ERR_SUBTOTAL_MISMATCH` failure. |
| `data/demo_invoice_error_grand_total.json` | `HUMAN_IN_THE_LOOP` | Single `ERR_GRAND_TOTAL_MISMATCH` failure. |
| `data/demo_invoice_error_partial_receipt.json` | `HUMAN_IN_THE_LOOP` | Single `ERR_PARTIAL_RECEIPT` failure. |
| `data/demo_invoice_error_currency.json` | `HUMAN_IN_THE_LOOP` | Single `ERR_UNSUPPORTED_CURRENCY` failure. |
| `data/demo_invoice_critical.json` | `CRITICAL_REJECTION` | Blocked vendor case. |

Run any sample with:

```bash
./run_agent.sh --file data/demo_invoice_pass.json
```

Useful demo sequence:

```bash
./run_agent.sh --file data/demo_invoice_pass.json
./run_agent.sh --file data/demo_invoice_review.json
./run_agent.sh --file data/demo_invoice_multiple_failures.json
./run_agent.sh --file data/demo_invoice_error_line_item_amount.json
./run_agent.sh --file data/demo_invoice_error_subtotal.json
./run_agent.sh --file data/demo_invoice_error_grand_total.json
./run_agent.sh --file data/demo_invoice_error_partial_receipt.json
./run_agent.sh --file data/demo_invoice_error_currency.json
./run_agent.sh --file data/demo_invoice_critical.json
```

Use `--active` when your shell already shows `(gl-capstone)`. It tells uv to use the active environment and avoids the `VIRTUAL_ENV does not match` warning.

For interactive chat:

```bash
./run_agent.sh
```

The chat stays open until you type `bye`, `done`, `exit`, `quit`, `stop`, or `goodbye`.

The agent remembers recent messages in the same session. The context window is bounded to the latest 12 messages and about 4,000 characters, so follow-up questions can refer to the prior invoice without growing the prompt forever.

You can paste multi-line invoice JSON directly into the chat. The CLI waits until the JSON block is complete before validating it.

During validation, the agent prints progress messages such as normalizing fields, running checks in parallel, writing the audit trail, and preparing the final response.

## Run Tests

```bash
cd /Users/binuk/GL-Capstone/Val-agent
uv run --active python -m unittest discover -s tests
```
