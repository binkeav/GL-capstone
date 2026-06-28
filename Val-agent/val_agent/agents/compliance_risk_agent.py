from __future__ import annotations

import sqlite3

from val_agent.state import ValidationState
from val_agent.tools.compliance import validate_compliance


def compliance_node(conn: sqlite3.Connection, state: ValidationState) -> list[dict]:
    invoice = state.get("normalized_invoice") or {}
    return [result.to_dict() for result in validate_compliance(conn, invoice)]

