from __future__ import annotations

import json
from typing import Any

from val_agent.llm import answer_general_chat_with_llm, classify_intent_with_llm, summarize_validation_with_llm


def classify_intent(message: str, chat_context: list[dict[str, str]] | None = None) -> str:
    return classify_intent_with_llm(message, chat_context)


def extract_invoice_json(message: str) -> dict[str, Any] | None:
    start = message.find("{")
    end = message.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return json.loads(message[start : end + 1])


def render_response(
    final_state: str,
    rule_results: list[dict[str, Any]],
    invoice_id: str | None = None,
    vendor_name: str | None = None,
    currency: str | None = "INR",
    chat_context: list[dict[str, str]] | None = None,
) -> str:
    return summarize_validation_with_llm(final_state, rule_results, invoice_id, vendor_name, currency, chat_context)


def render_general_response(
    message: str,
    intent: str,
    chat_context: list[dict[str, str]] | None = None,
    policy: dict[str, Any] | None = None,
) -> str:
    return answer_general_chat_with_llm(message, intent, chat_context, policy)
