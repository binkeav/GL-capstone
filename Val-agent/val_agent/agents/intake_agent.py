from __future__ import annotations

from val_agent.state import ValidationState
from val_agent.tools.normalize import normalize_invoice


def normalize_node(state: ValidationState) -> ValidationState:
    raw_invoice = state.get("raw_invoice")
    if not raw_invoice:
        state["errors"] = ["ERR_NO_INVOICE_JSON"]
        return state
    state["normalized_invoice"] = normalize_invoice(raw_invoice)
    return state

