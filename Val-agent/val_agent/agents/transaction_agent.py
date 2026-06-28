from __future__ import annotations

import sqlite3

from val_agent.models import RuleResult
from val_agent.state import ValidationState
from val_agent.tools.matching import validate_transaction_match


def transaction_node(conn: sqlite3.Connection, state: ValidationState) -> list[dict]:
    invoice = state.get("normalized_invoice") or {}
    if not invoice:
        return [
            RuleResult(
                "MATCH_PO_EXISTS",
                "Transaction Match Agent",
                "SKIPPED",
                "ERROR",
                error_code="ERR_NO_INVOICE_JSON",
            ).to_dict()
        ]
    return [result.to_dict() for result in validate_transaction_match(conn, invoice)]

