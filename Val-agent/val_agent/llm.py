from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


VAL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = VAL_ROOT.parent
OCR_ROOT = PROJECT_ROOT / "ocr-agent"

if not os.getenv("INVOICE_PROCESSING_SKIP_DOTENV"):
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(OCR_ROOT / ".env")
    load_dotenv(VAL_ROOT / ".env")

DEFAULT_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://aibe.mygreatlearning.com/openai/v1")
DEFAULT_MODEL = os.getenv("VAL_AGENT_LLM_MODEL", "gpt-4o-mini")
ALLOWED_INTENTS = {
    "validate_invoice",
    "ask_status",
    "explain_failure",
    "correct_and_revalidate",
    "request_override",
    "general_chat",
}


def openai_client() -> OpenAI:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for chat LLM operations.")
    return OpenAI(base_url=DEFAULT_BASE_URL)


def classify_intent_with_llm(message: str, chat_context: list[dict[str, str]] | None = None) -> str:
    client = openai_client()
    prompt = {
        "task": "Classify the user's chat intent for an invoice validation assistant.",
        "allowed_intents": sorted(ALLOWED_INTENTS),
        "rules": [
            "Return only JSON.",
            "If the user includes invoice JSON or asks to validate an invoice, use validate_invoice.",
            "If the user asks why something failed, use explain_failure.",
            "If the user asks to approve despite failures, use request_override.",
            "If the user provides corrected fields, use correct_and_revalidate.",
            "If the user asks about an existing invoice, use ask_status.",
            "If the user greets, asks what you can do, or sends a message without invoice context, use general_chat.",
        ],
        "message": message,
        "recent_chat_context": chat_context or [],
    }
    response = client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": "You classify invoice assistant chat intent. Return strict JSON only."},
            {"role": "user", "content": json.dumps(prompt)},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    data = json.loads(content)
    intent = data.get("intent")
    if intent not in ALLOWED_INTENTS:
        raise ValueError(f"Unsupported LLM intent: {intent}")
    return intent


def summarize_validation_with_llm(
    final_state: str,
    rule_results: list[dict[str, Any]],
    invoice_id: str | None,
    vendor_name: str | None,
    currency: str | None = "INR",
    chat_context: list[dict[str, str]] | None = None,
) -> str:
    client = openai_client()
    failed = [result for result in rule_results if result.get("status") == "FAIL"]
    passed_count = len([result for result in rule_results if result.get("status") == "PASS"])
    failed_count = len(failed)
    action_map = {
        "STRAIGHT_THROUGH_PROCESSING": [
            "No manual correction is needed.",
            "The invoice can move forward for ERP submission or payment workflow.",
        ],
        "HUMAN_IN_THE_LOOP": [
            "Review the failed checks shown below.",
            "Correct any extraction or source-data issue, then run validation again.",
            "If the data is correct but policy allows an exception, escalate to the AP supervisor.",
        ],
        "CRITICAL_REJECTION": [
            "Do not submit this invoice to ERP.",
            "Review the critical issue and escalate to the finance controller or supervisor.",
        ],
    }
    prompt = {
        "task": "Write a clear, friendly invoice validation response for a business user.",
        "constraints": [
            "Use normal language, not system or developer language.",
            "Do not lead with internal codes. Put codes only after the plain-language explanation.",
            "Use short sections: Result, What I found, Next steps.",
            "For STRAIGHT_THROUGH_PROCESSING, say the invoice looks good and can move forward.",
            "For HUMAN_IN_THE_LOOP, say it needs review and explain what to fix.",
            "For CRITICAL_REJECTION, say it is blocked and explain why.",
            "Do not invent facts.",
            "Mention the exact validation state once.",
            "Cite failed rule IDs and error codes only as supporting details.",
            "Do not mention or request bank remittance details.",
            "This is India-local invoice processing. Use INR for money values.",
            "Never use dollar signs unless the currency field is explicitly USD.",
            "Keep deterministic validation results as source of truth.",
            "End with concrete next steps.",
        ],
        "invoice_id": invoice_id,
        "vendor_name": vendor_name,
        "currency": currency or "INR",
        "state": final_state,
        "counts": {
            "passed_checks": passed_count,
            "failed_checks": failed_count,
        },
        "failed_rule_results": failed,
        "recommended_next_steps": action_map.get(final_state, []),
        "recent_chat_context": chat_context or [],
    }
    response = client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": "You summarize invoice validation results for Accounts Payable reviewers."},
            {"role": "user", "content": json.dumps(prompt)},
        ],
        temperature=0,
    )
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("The LLM returned an empty validation summary.")
    detail_block = _failed_rule_detail_block(rule_results, currency)
    if detail_block:
        content = f"{content}\n\n{detail_block}"
    return content


_RULE_TITLES = {
    "ARITH_LINE_ITEM_MATCH": "Line Item Amount Mismatch",
    "ARITH_SUBTOTAL_MATCH": "Subtotal Mismatch",
    "ARITH_TOTAL_MATCH": "Total Mismatch",
    "MATCH_PO_EXISTS": "Purchase Order Missing",
    "MATCH_GOODS_RECEIVED": "Goods Receipt Mismatch",
    "DUP_VENDOR_INVOICE_NUMBER": "Duplicate Invoice Number",
    "DUP_TOTAL_DATE": "Possible Duplicate Total/Date",
    "COMP_DATE_WINDOW": "Invoice Date Invalid",
    "COMP_GSTIN_PRESENT": "Vendor GSTIN Missing",
    "COMP_GSTIN_MATCH": "Vendor GSTIN Mismatch",
    "COMP_CURRENCY_INR": "Unsupported Currency",
    "RISK_VENDOR_ACTIVE": "Vendor Status Issue",
}

_ERROR_EXPLANATIONS = {
    "ERR_LINE_ITEM_AMOUNT_MISMATCH": "A line amount does not equal quantity multiplied by unit price.",
    "ERR_SUBTOTAL_MISMATCH": "The calculated subtotal does not match the extracted subtotal.",
    "ERR_GRAND_TOTAL_MISMATCH": "The invoice total does not equal subtotal plus tax and shipping minus discounts.",
    "ERR_MISSING_PO": "The purchase order was not found.",
    "ERR_PARTIAL_RECEIPT": "The received quantity is lower than the invoiced quantity.",
    "ERR_CONFIRMED_DUPLICATE": "Another invoice has the same vendor and invoice number.",
    "ERR_DUPLICATE_SUSPICION": "Another invoice has the same total and invoice date.",
    "ERR_INVALID_INVOICE_DATE": "The invoice date is outside the allowed policy window or is invalid.",
    "ERR_MISSING_GSTIN": "The vendor GSTIN was not found on the invoice.",
    "ERR_GSTIN_MISMATCH": "The invoice GSTIN does not match the vendor master record.",
    "ERR_UNSUPPORTED_CURRENCY": "The invoice currency is not supported by policy.",
    "ERR_BLOCKED_VENDOR": "The vendor is not active or is blocked.",
}


def _failed_rule_detail_block(rule_results: list[dict[str, Any]], currency: str | None = None) -> str:
    failed = [rule for rule in rule_results if rule.get("status") == "FAIL"]
    if not failed:
        return ""
    lines = ["**Validation details**"]
    for rule in failed:
        lines.append(f"- {_format_failed_rule_detail(rule, currency)}")
    return "\n".join(lines)


def _format_failed_rule_detail(rule: dict[str, Any], currency: str | None = None) -> str:
    rule_id = str(rule.get("rule_id") or "Validation Check")
    error_code = rule.get("error_code")
    title = _RULE_TITLES.get(rule_id, rule_id.replace("_", " ").title())
    explanation = _ERROR_EXPLANATIONS.get(str(error_code), "The validation check failed.")
    pieces = [f"**{title}:** {explanation}"]
    expected = rule.get("expected_value")
    actual = rule.get("actual_value")
    if expected not in (None, ""):
        pieces.append(f"Expected: {_display_value(expected)}")
    if actual not in (None, ""):
        pieces.append(f"Actual: {_display_value(actual)}")

    evidence = rule.get("evidence") or {}
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            evidence = {"evidence": evidence}
    if isinstance(evidence, dict):
        formula = evidence.get("formula")
        if formula:
            pieces.append(f"Formula: {formula}")
        values = {
            key: value
            for key, value in evidence.items()
            if key not in {"formula", "reason"} and value not in (None, "", [])
        }
        if values:
            pieces.append(
                "Values: "
                + ", ".join(f"{key}={_display_value(value)}" for key, value in values.items())
            )
        if evidence.get("reason"):
            pieces.append(f"Reason: {evidence['reason']}")
    if error_code:
        pieces.append(f"Code: {error_code}")
    if currency and rule_id.startswith("ARITH"):
        pieces.append(f"Currency: {currency}")
    return "; ".join(pieces)


def _display_value(value: Any) -> str:
    if value in (None, "") or str(value).strip().lower() in {"none", "null"}:
        return "Not found"
    return str(value)


def answer_general_chat_with_llm(
    message: str,
    intent: str,
    chat_context: list[dict[str, str]] | None = None,
    policy: dict[str, Any] | None = None,
) -> str:
    client = openai_client()
    prompt = {
        "task": "Reply to a normal chat message for an invoice validation assistant.",
        "intent": intent,
        "message": message,
        "recent_chat_context": chat_context or [],
        "capabilities": [
            "Review uploaded invoices.",
            "Explain failed checks in simple language.",
            "Show next steps for human review.",
            "Answer questions about invoice status and policy.",
            "Support India-local invoice processing with GST and INR checks.",
        ],
        "active_policy": policy or {},
        "constraints": [
            "Use warm, normal language.",
            "Do not use emojis.",
            "Do not use system jargon.",
            "Do not mention bank remittance details.",
            "Guide the user toward actual work.",
            "Use the recent chat context when it helps answer follow-up questions.",
            "If there is no current invoice, invite the user to upload one.",
            "Do not mention JSON, APIs, code, CLI commands, system prompts, or other technical implementation details.",
            "For policy questions, translate active_policy into plain business rules.",
            "For policy questions, do not show raw field names such as policy_id, jurisdiction, required_tax_id_type, max_invoice_age_days, or supported_currency.",
            "For policy questions, answer with simple bullets such as accepted currency, tax ID required, invoice age limit, future-date rule, and vendor status rule.",
            "For greetings, include a short capability overview instead of saying only that no invoice is available.",
            "Keep it under 120 words.",
        ],
        "example_next_actions": [
            "Upload an invoice to process.",
            "Ask why an invoice needs review.",
            "Ask what the invoice policy requires.",
        ],
    }
    response = client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": "You are a helpful chat assistant for invoice validation."},
            {"role": "user", "content": json.dumps(prompt)},
        ],
        temperature=0.2,
    )
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("The LLM returned an empty general chat response.")
    return content
