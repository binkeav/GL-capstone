from __future__ import annotations

from typing import Any, Literal, TypedDict


class ValidationState(TypedDict, total=False):
    conversation_id: str
    user_id: str
    user_role: str
    user_message: str
    chat_context: list[dict[str, str]]
    intent: str
    invoice_id: str | None
    raw_invoice: dict[str, Any] | None
    normalized_invoice: dict[str, Any] | None
    rule_results: list[dict[str, Any]]
    errors: list[str]
    llm_used: bool
    final_state: Literal[
        "PENDING",
        "STRAIGHT_THROUGH_PROCESSING",
        "HUMAN_IN_THE_LOOP",
        "CRITICAL_REJECTION",
    ]
    assistant_response: str
