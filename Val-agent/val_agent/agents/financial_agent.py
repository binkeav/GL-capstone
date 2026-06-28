from __future__ import annotations

from val_agent.state import ValidationState
from val_agent.tools.arithmetic import validate_arithmetic


def financial_node(state: ValidationState) -> list[dict]:
    invoice = state.get("normalized_invoice") or {}
    return [result.to_dict() for result in validate_arithmetic(invoice)]

