from __future__ import annotations

from val_agent.state import ValidationState
from val_agent.tools.chat import classify_intent, extract_invoice_json


def chat_intake(state: ValidationState) -> ValidationState:
    message = state.get("user_message", "")
    intent = classify_intent(message, state.get("chat_context", []))
    state["intent"] = intent
    if intent == "validate_invoice":
        state["raw_invoice"] = extract_invoice_json(message)
    return state
