from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from invoice_processing.rag_store import list_uploaded_invoices, search_uploaded_invoices


ROOT = Path(__file__).resolve().parents[1]
VAL_ROOT = ROOT / "Val-agent"
OCR_ROOT = ROOT / "ocr-agent"

if not os.getenv("INVOICE_PROCESSING_SKIP_DOTENV"):
    load_dotenv(ROOT / ".env")
    load_dotenv(OCR_ROOT / ".env")
    load_dotenv(VAL_ROOT / ".env")

if str(VAL_ROOT) not in sys.path:
    sys.path.insert(0, str(VAL_ROOT))

from val_agent.db import (  # noqa: E402
    connect,
    create_conversation,
    get_recent_chat_context,
    init_db,
    save_chat_message,
)
from val_agent.graph import run_validation_graph  # noqa: E402
from val_agent.tools.compliance import lookup_compliance_policy  # noqa: E402


DEFAULT_CHAT_DB = VAL_ROOT / "data" / "invoice_processing.sqlite3"


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


DEFAULT_MEMORY_WINDOW_MESSAGES = _env_int("INVOICE_PROCESSING_MEMORY_WINDOW_MESSAGES", 12, 1, 50)
DEFAULT_MEMORY_WINDOW_CHARS = _env_int("INVOICE_PROCESSING_MEMORY_WINDOW_CHARS", 4000, 500, 20000)
ALLOWED_INTENTS = {
    "validate_invoice_json",
    "validate_current_invoice",
    "show_field_value",
    "show_extracted_fields",
    "search_uploaded_invoices",
    "explain_current_result",
    "show_policy",
    "show_trace",
    "general_chat",
}
ALLOWED_UPLOAD_INTENTS = {"extract_only", "validate", "unknown"}


def ensure_conversation(
    *,
    db_path: str | Path = DEFAULT_CHAT_DB,
    conversation_id: str | None = None,
    user_id: str = "demo_user",
    user_role: str = "AP_REVIEWER",
) -> str:
    conn = connect(db_path)
    init_db(conn)
    try:
        if conversation_id and _conversation_exists(conn, conversation_id):
            return conversation_id
        return create_conversation(conn, user_id, user_role)
    finally:
        conn.close()


def run_processing_chat_turn(
    message: str,
    *,
    conversation_id: str,
    db_path: str | Path = DEFAULT_CHAT_DB,
    current_invoice_payload: dict[str, Any] | None = None,
    current_processing_result: dict[str, Any] | None = None,
    user_id: str = "demo_user",
    user_role: str = "AP_REVIEWER",
    persist_user_message: bool = True,
) -> dict[str, Any]:
    conn = connect(db_path)
    init_db(conn)
    try:
        if persist_user_message:
            save_chat_message(conn, conversation_id, "USER", message)
        chat_context = get_chat_memory_context(conn, conversation_id)
        intent = classify_processing_intent(
            message,
            chat_context,
            current_invoice_payload=current_invoice_payload,
            current_processing_result=current_processing_result,
        )

        if intent == "validate_invoice_json":
            result = _run_val_agent_message(conn, conversation_id, message, user_id, user_role, chat_context)
        elif intent == "validate_current_invoice":
            if not current_invoice_payload:
                result = _static_assistant_result(
                    summarize_processing_response(
                        message,
                        intent,
                        chat_context,
                        current_invoice_payload=current_invoice_payload,
                        current_processing_result=current_processing_result,
                    ),
                    intent,
                )
            else:
                result = _run_val_agent_message(
                    conn,
                    conversation_id,
                    json.dumps(current_invoice_payload, indent=2),
                    user_id,
                    user_role,
                    chat_context,
                )
        elif intent == "show_field_value":
            result = _static_assistant_result(
                render_field_value_response(message, current_invoice_payload),
                intent,
            )
        elif intent == "show_extracted_fields":
            result = _static_assistant_result(
                render_extracted_fields_response(current_processing_result),
                intent,
            )
        elif intent == "search_uploaded_invoices":
            result = _static_assistant_result(
                answer_uploaded_invoice_search(message, db_path=db_path),
                intent,
            )
        elif intent == "show_policy":
            policy = lookup_compliance_policy(conn)
            result = _static_assistant_result(
                summarize_processing_response(
                    message,
                    intent,
                    chat_context,
                    current_invoice_payload=current_invoice_payload,
                    current_processing_result=current_processing_result,
                    response_context={"policy": policy},
                ),
                intent,
            )
        elif intent == "show_trace":
            result = _static_assistant_result(
                summarize_processing_response(
                    message,
                    intent,
                    chat_context,
                    current_invoice_payload=current_invoice_payload,
                    current_processing_result=current_processing_result,
                    response_context={"trace_events": _trace_events(current_processing_result)},
                ),
                intent,
            )
        elif intent == "explain_current_result":
            result = _static_assistant_result(
                summarize_processing_response(
                    message,
                    intent,
                    chat_context,
                    current_processing_result=current_processing_result,
                    current_invoice_payload=current_invoice_payload,
                ),
                intent,
            )
        else:
            result = _static_assistant_result(
                summarize_processing_response(
                    message,
                    intent,
                    chat_context,
                    current_processing_result=current_processing_result,
                    current_invoice_payload=current_invoice_payload,
                ),
                intent,
            )

        assistant_text = result.get("assistant_response", "")
        save_chat_message(
            conn,
            conversation_id,
            "ASSISTANT",
            assistant_text,
            invoice_id=result.get("invoice_id"),
            intent=intent,
            message_json={"intent": intent, "action": result.get("action")},
        )
        result["intent"] = intent
        result["messages"] = list_chat_messages(conversation_id, db_path=db_path)
        return result
    finally:
        conn.close()


def get_chat_memory_context(
    conn,
    conversation_id: str,
    *,
    limit: int = DEFAULT_MEMORY_WINDOW_MESSAGES,
    max_chars: int = DEFAULT_MEMORY_WINDOW_CHARS,
) -> list[dict[str, str]]:
    return get_recent_chat_context(conn, conversation_id, limit=limit, max_chars=max_chars)


def load_chat_memory_context(
    conversation_id: str,
    *,
    db_path: str | Path = DEFAULT_CHAT_DB,
    limit: int = DEFAULT_MEMORY_WINDOW_MESSAGES,
    max_chars: int = DEFAULT_MEMORY_WINDOW_CHARS,
) -> list[dict[str, str]]:
    conn = connect(db_path)
    init_db(conn)
    try:
        return get_chat_memory_context(conn, conversation_id, limit=limit, max_chars=max_chars)
    finally:
        conn.close()


def list_chat_messages(
    conversation_id: str,
    *,
    db_path: str | Path = DEFAULT_CHAT_DB,
    limit: int = 50,
) -> list[dict[str, Any]]:
    conn = connect(db_path)
    init_db(conn)
    try:
        rows = conn.execute(
            """
            SELECT sender, message_text, intent, invoice_id, created_at
            FROM chat_messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (conversation_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def append_chat_message(
    conversation_id: str,
    sender: str,
    text: str,
    *,
    db_path: str | Path = DEFAULT_CHAT_DB,
    invoice_id: str | None = None,
    intent: str | None = None,
    message_json: dict[str, Any] | None = None,
) -> None:
    conn = connect(db_path)
    init_db(conn)
    try:
        save_chat_message(
            conn,
            conversation_id,
            sender,
            text,
            invoice_id=invoice_id,
            intent=intent,
            message_json=message_json,
        )
    finally:
        conn.close()


def classify_processing_intent(
    message: str,
    chat_context: list[dict[str, str]],
    *,
    current_invoice_payload: dict[str, Any] | None = None,
    current_processing_result: dict[str, Any] | None = None,
) -> str:
    routed_intent = _preclassify_retrieval_intent(message, current_invoice_payload)
    if routed_intent:
        return routed_intent

    client = _openai_client_required()
    prompt = {
        "task": "Classify the user's intent in a unified invoice processing chat.",
        "allowed_intents": sorted(ALLOWED_INTENTS),
        "classification_rules": [
            "If the user asks for one specific extracted field, use show_field_value.",
            "Examples for show_field_value: what is the total amount, invoice date, vendor name, tax amount, PO number, currency.",
            "Use show_extracted_fields only when the user asks for all extracted fields, OCR output, extracted data, or the full extraction result.",
            "Use search_uploaded_invoices when the user asks about uploaded invoices collectively, previous invoices, invoices by vendor, failed invoices, or asks to find/list/compare invoices.",
        ],
        "intent_definitions": {
            "validate_invoice_json": "User pasted invoice JSON or asks to validate JSON in the message.",
            "validate_current_invoice": "User asks to validate the currently extracted/current invoice.",
            "show_field_value": "User asks for one specific extracted invoice field, such as total amount, tax, vendor name, invoice date, PO number, or currency.",
            "show_extracted_fields": "User asks to view only extracted invoice fields/OCR output without validation.",
            "search_uploaded_invoices": "User asks across uploaded invoices or previous invoices using retrieval.",
            "explain_current_result": "User asks why it passed/failed, what happened, or what to do next.",
            "show_policy": "User asks about validation policy, allowed invoice age, GSTIN, INR, or compliance rules.",
            "show_trace": "User asks for processing steps, trace, progress, or agent path.",
            "general_chat": "Greetings, capabilities, or anything else.",
        },
        "message": message,
        "recent_chat_context": chat_context,
        "current_context": _current_context_summary(current_invoice_payload, current_processing_result),
    }
    response = client.chat.completions.create(
        model=os.getenv("INVOICE_PROCESSING_LLM_MODEL", os.getenv("VAL_AGENT_LLM_MODEL", "gpt-4o-mini")),
        messages=[
            {"role": "system", "content": "Return strict JSON with one key: intent."},
            {"role": "user", "content": json.dumps(prompt)},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content or "{}")
    intent = data.get("intent")
    if intent not in ALLOWED_INTENTS:
        raise ValueError(f"Unsupported invoice-processing intent from LLM: {intent}")
    return intent


def classify_upload_intent(
    message: str,
    chat_context: list[dict[str, str]],
) -> str:
    client = _openai_client_required()
    prompt = {
        "task": "Classify the user's intent for a message sent together with an uploaded invoice file.",
        "allowed_intents": sorted(ALLOWED_UPLOAD_INTENTS),
        "rules": [
            "Return only JSON.",
            "Use extract_only when the user asks to extract, read, parse, OCR, scan, show, or get invoice details/fields without asking for validation.",
            "Examples for extract_only: extract the details, show extracted fields, OCR this invoice, read this invoice, get invoice details.",
            "Use validate when the user asks to validate, process, check compliance, verify, approve, reject, pass, fail, or run policy checks.",
            "Use unknown when the request is ambiguous, conversational, or asks a question that is not clearly extraction-only or validation.",
            "When both extraction and validation are requested, use validate.",
        ],
        "message": message,
        "recent_chat_context": chat_context,
    }
    response = client.chat.completions.create(
        model=os.getenv("INVOICE_PROCESSING_LLM_MODEL", os.getenv("VAL_AGENT_LLM_MODEL", "gpt-4o-mini")),
        messages=[
            {"role": "system", "content": "You classify uploaded-invoice actions. Return strict JSON with one key: intent."},
            {"role": "user", "content": json.dumps(prompt)},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content or "{}")
    intent = data.get("intent")
    if intent not in ALLOWED_UPLOAD_INTENTS:
        raise ValueError(f"Unsupported upload intent from LLM: {intent}")
    return intent


def _preclassify_retrieval_intent(message: str, current_invoice_payload: dict[str, Any] | None) -> str | None:
    text = (message or "").lower()
    if not text.strip():
        return None

    collection_terms = (
        "uploaded invoice",
        "uploaded invoices",
        "previous invoice",
        "previous invoices",
        "past invoice",
        "past invoices",
        "all invoice",
        "all invoices",
        "indexed invoice",
        "indexed invoices",
        "across invoices",
        "invoices from",
        "invoice history",
    )
    search_terms = (
        "show invoices",
        "list invoices",
        "find invoices",
        "search invoices",
        "which invoices",
        "what invoices",
        "any invoices",
        "how many invoices",
        "failed invoices",
        "blocked invoices",
        "missing po",
        "missing purchase order",
        "duplicate invoices",
    )
    if any(term in text for term in collection_terms + search_terms):
        return "search_uploaded_invoices"

    current_field_terms = (
        "total",
        "amount",
        "vendor",
        "invoice date",
        "invoice number",
        "po number",
        "tax",
        "currency",
    )
    if current_invoice_payload and any(term in text for term in current_field_terms):
        return "show_field_value"
    return None


def summarize_processing_response(
    message: str,
    intent: str,
    chat_context: list[dict[str, str]],
    *,
    current_invoice_payload: dict[str, Any] | None = None,
    current_processing_result: dict[str, Any] | None = None,
    response_context: dict[str, Any] | None = None,
) -> str:
    client = _openai_client_required()
    prompt = {
        "task": "Answer the user inside a unified invoice processing app.",
        "message": message,
        "intent": intent,
        "recent_chat_context": chat_context,
        "current_context": _current_context_summary(current_invoice_payload, current_processing_result),
        "response_context": response_context or {},
        "constraints": [
            "Use normal language, not system or developer language.",
            "Be concise and practical.",
            "Use the current invoice context when available.",
            "Do not invent validation facts.",
            "If validation results exist, treat them as source of truth.",
            "Use short sections only when useful.",
            "For policy questions, use the supplied policy context and translate it into plain business rules.",
            "For policy questions, do not show raw field names such as policy_id, jurisdiction, required_tax_id_type, max_invoice_age_days, or supported_currency.",
            "For policy questions, answer with simple bullets such as accepted currency, tax ID required, invoice age limit, future-date rule, and vendor status rule.",
            "For trace questions, summarize the supplied trace events.",
            "If no current invoice exists, invite the user to upload an invoice.",
            "For greetings or general questions, briefly explain that the app can read invoices, check them against policy, flag issues, explain review reasons, show status, and suggest next steps.",
            "Do not mention JSON, APIs, code, commands, internal state, or other technical implementation details to the user.",
            "If the user asks for extracted fields only, do not discuss validation or policy results.",
        ],
    }
    response = client.chat.completions.create(
        model=os.getenv("INVOICE_PROCESSING_LLM_MODEL", os.getenv("VAL_AGENT_LLM_MODEL", "gpt-4o-mini")),
        messages=[
            {"role": "system", "content": "You are the chat agent for an invoice processing app."},
            {"role": "user", "content": json.dumps(prompt)},
        ],
        temperature=0.2,
    )
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("The LLM returned an empty invoice-processing chat response.")
    return content


def summarize_processed_invoice_result(
    processing_result: dict[str, Any],
    *,
    chat_context: list[dict[str, str]] | None = None,
) -> str:
    client = _openai_client_required()
    payload = processing_result.get("invoice_payload") or {}
    validation = processing_result.get("validation_result") or {}
    evidence = processing_result.get("ocr_evidence") or {}
    quality = processing_result.get("extraction_quality") or {}
    rule_results = validation.get("rule_results", [])
    prompt = {
        "task": "Summarize a freshly processed invoice for a chat UI.",
        "invoice": {
            "invoice_number": payload.get("invoice_number"),
            "vendor_name": payload.get("vendor_name"),
            "total": payload.get("total"),
            "currency": payload.get("currency"),
            "invoice_date": payload.get("invoice_date"),
            "po_number": payload.get("po_number"),
        },
        "processing": {
            "final_state": processing_result.get("final_state"),
            "invoice_id": validation.get("invoice_id"),
            "ocr_confidence": evidence.get("avg_confidence"),
            "extraction_error": evidence.get("extraction_error"),
            "quality_reasons": quality.get("reasons", []),
        },
        "failed_rule_results": [item for item in rule_results if item.get("status") == "FAIL"],
        "recent_chat_context": chat_context or [],
        "constraints": [
            "Write the response as the assistant's first message after upload processing.",
            "Use Markdown with short sections.",
            "Start with the invoice number and business outcome.",
            "Explain failed checks in normal language before any codes.",
            "Mention exact amounts or expected/actual values only when present in the rule results.",
            "Give one concrete next step.",
            "Do not invent facts beyond the supplied processing result.",
            "Do not mention OCR mode or model names.",
            "Keep it concise.",
        ],
    }
    response = client.chat.completions.create(
        model=os.getenv("INVOICE_PROCESSING_LLM_MODEL", os.getenv("VAL_AGENT_LLM_MODEL", "gpt-4o-mini")),
        messages=[
            {"role": "system", "content": "You summarize invoice processing outcomes for Accounts Payable users."},
            {"role": "user", "content": json.dumps(prompt)},
        ],
        temperature=0.2,
    )
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("The LLM returned an empty upload summary.")
    detail_block = _failed_rule_detail_block(rule_results, payload.get("currency"))
    if detail_block:
        content = f"{content}\n\n{detail_block}"
    return content


def answer_uploaded_invoice_search(
    message: str,
    *,
    db_path: str | Path = DEFAULT_CHAT_DB,
    top_k: int = 5,
) -> str:
    filters = _invoice_list_filters(message)
    direct = _direct_uploaded_invoice_answer(message, db_path, filters=filters)
    if direct:
        return direct

    hits = search_uploaded_invoices(
        db_path,
        message,
        top_k=top_k,
        vendor_name=filters.get("vendor_name"),
        final_state=filters.get("final_state"),
    )
    if not hits:
        return "I could not find matching uploaded invoices yet. Upload and process invoices first, then ask again."

    client = _openai_client_required()
    context = [
        {
            "source_file": hit.get("source_file"),
            "invoice_number": hit.get("invoice_number"),
            "vendor_name": hit.get("vendor_name"),
            "total": hit.get("total"),
            "currency": hit.get("currency"),
            "final_state": hit.get("final_state"),
            "score": round(float(hit.get("score", 0)), 4),
            "text": hit.get("chunk_text"),
        }
        for hit in hits
    ]
    prompt = {
        "task": "Answer a question using retrieved uploaded invoice records.",
        "question": message,
        "retrieved_invoice_context": context,
        "constraints": [
            "Use only the retrieved invoice context.",
            "If the answer is not present, say it was not found in uploaded invoices.",
            "Use business-friendly language.",
            "Mention invoice numbers, vendor names, totals, and states when helpful.",
            "Do not mention vector stores, embeddings, chunks, scores, JSON, APIs, or implementation details.",
            "Keep the answer concise.",
        ],
    }
    response = client.chat.completions.create(
        model=os.getenv("INVOICE_PROCESSING_LLM_MODEL", os.getenv("VAL_AGENT_LLM_MODEL", "gpt-4o-mini")),
        messages=[
            {"role": "system", "content": "You answer questions about previously uploaded invoices."},
            {"role": "user", "content": json.dumps(prompt)},
        ],
        temperature=0.1,
    )
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("The LLM returned an empty uploaded-invoice search response.")
    return content


def _direct_uploaded_invoice_answer(
    message: str,
    db_path: str | Path,
    *,
    filters: dict[str, str | None] | None = None,
) -> str | None:
    text = (message or "").lower()
    filters = filters or {}
    docs = list_uploaded_invoices(
        db_path,
        vendor_name=filters.get("vendor_name"),
        final_state=filters.get("final_state"),
    )
    if "how many" in text or "count" in text:
        qualifier = _filter_summary(filters)
        return f"There are **{len(docs)}** uploaded invoices indexed{qualifier}."
    if ("list" in text or "show" in text) and ("invoice" in text or "invoices" in text):
        if not docs:
            qualifier = _filter_summary(filters)
            return f"No uploaded invoices are indexed{qualifier}."
        qualifier = _filter_summary(filters)
        lines = [f"Here are the uploaded invoices I found{qualifier}:", ""]
        for doc in docs[:10]:
            amount = _money_value(doc.get("total"), doc.get("currency"))
            invoice_number = _display_value(doc.get("invoice_number"))
            vendor = _display_value(doc.get("vendor_name"))
            state = _display_value(doc.get("final_state"))
            lines.append(f"- **{invoice_number}** — {vendor}, {amount}, {state}")
        if len(docs) > 10:
            lines.append(f"- ...and {len(docs) - 10} more.")
        return "\n".join(lines)
    return None


def _invoice_list_filters(message: str) -> dict[str, str | None]:
    text = (message or "").strip()
    lowered = text.lower()
    state_aliases = {
        "failed": "HUMAN_IN_THE_LOOP",
        "failure": "HUMAN_IN_THE_LOOP",
        "fail": "HUMAN_IN_THE_LOOP",
        "needs review": "HUMAN_IN_THE_LOOP",
        "review": "HUMAN_IN_THE_LOOP",
        "human": "HUMAN_IN_THE_LOOP",
        "approved": "STRAIGHT_THROUGH_PROCESSING",
        "passed": "STRAIGHT_THROUGH_PROCESSING",
        "pass": "STRAIGHT_THROUGH_PROCESSING",
        "straight through": "STRAIGHT_THROUGH_PROCESSING",
        "blocked": "CRITICAL_REJECTION",
        "critical": "CRITICAL_REJECTION",
        "rejected": "CRITICAL_REJECTION",
    }
    final_state = None
    explicit_state = re.search(
        r"\b(HUMAN_IN_THE_LOOP|STRAIGHT_THROUGH_PROCESSING|CRITICAL_REJECTION|PENDING)\b",
        text,
        re.IGNORECASE,
    )
    if explicit_state:
        final_state = explicit_state.group(1).upper()
    else:
        for phrase, state in state_aliases.items():
            if phrase in lowered:
                final_state = state
                break

    vendor_name = None
    vendor_match = re.search(
        r"\b(?:vendor|supplier)\s+(?:name\s+)?(?:is\s+|=|:)?([A-Za-z0-9 &.,'_-]+?)(?:\s+(?:with|where|status|state|that|which)\b|$)",
        text,
        re.IGNORECASE,
    )
    if vendor_match:
        vendor_name = vendor_match.group(1).strip(" .,:;")
    for prefix in ("for vendor ", "by vendor ", "from vendor ", "for supplier ", "by supplier ", "from supplier "):
        if prefix in lowered:
            start = lowered.index(prefix) + len(prefix)
            vendor_name = text[start:].strip(" .,:;")
            vendor_name = re.split(r"\s+(?:with|where|status|state)\b", vendor_name, maxsplit=1, flags=re.IGNORECASE)[0].strip(" .,:;")
            break
    return {"vendor_name": vendor_name, "final_state": final_state}


def _filter_summary(filters: dict[str, str | None]) -> str:
    parts = []
    if filters.get("vendor_name"):
        parts.append(f"for vendor **{filters['vendor_name']}**")
    if filters.get("final_state"):
        parts.append(f"with status **{filters['final_state']}**")
    return " " + " and ".join(parts) if parts else ""


def _run_val_agent_message(
    conn,
    conversation_id: str,
    message: str,
    user_id: str,
    user_role: str,
    chat_context: list[dict[str, str]],
) -> dict[str, Any]:
    progress_events: list[str] = []
    state = run_validation_graph(
        conn,
        {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "user_role": user_role,
            "user_message": message,
            "chat_context": chat_context,
            "final_state": "PENDING",
            "rule_results": [],
            "errors": [],
        },
        progress=progress_events.append,
    )
    return {
        "action": "val_agent_validation",
        "state": state,
        "progress_events": progress_events,
        "invoice_id": state.get("invoice_id"),
        "final_state": state.get("final_state"),
        "assistant_response": state.get("assistant_response", ""),
        "rule_results": state.get("rule_results", []),
    }


def _static_assistant_result(text: str, intent: str) -> dict[str, Any]:
    return {
        "action": intent,
        "assistant_response": text,
        "progress_events": [],
        "rule_results": [],
    }


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


def _trace_events(current_processing_result: dict[str, Any] | None) -> list[str]:
    result = current_processing_result or {}
    events = list(result.get("progress_events") or [])
    payload = result.get("invoice_payload") or {}
    evidence = result.get("ocr_evidence") or {}
    validation = result.get("validation_result") or {}

    if payload:
        events.append(
            "Extracted values: "
            f"invoice={_display_value(payload.get('invoice_number'))}, "
            f"vendor={_display_value(payload.get('vendor_name'))}, "
            f"subtotal={_display_value(payload.get('subtotal'))}, "
            f"tax={_display_value(payload.get('tax'))}, "
            f"shipping={_display_value(payload.get('shipping'))}, "
            f"discounts={_display_value(payload.get('discounts'))}, "
            f"total={_display_value(payload.get('total'))}, "
            f"currency={_display_value(payload.get('currency'))}."
        )
    if evidence:
        events.append(
            "OCR evidence: "
            f"source={_display_value(evidence.get('source_file'))}, "
            f"confidence={_display_value(evidence.get('avg_confidence'))}, "
            f"extraction={_display_value(evidence.get('extraction_mode'))}."
        )
    for rule in validation.get("rule_results", []):
        evidence_bits = []
        rule_evidence = rule.get("evidence") or {}
        formula = rule_evidence.get("formula")
        if formula:
            evidence_bits.append(f"formula={formula}")
        if rule_evidence:
            values = {
                key: value
                for key, value in rule_evidence.items()
                if key not in {"formula", "reason"} and value not in (None, "")
            }
            if values:
                evidence_bits.append(
                    "values="
                    + ", ".join(f"{key}={_display_value(value)}" for key, value in values.items())
                )
        if rule_evidence.get("reason"):
            evidence_bits.append(f"reason={rule_evidence['reason']}")
        suffix = f" ({'; '.join(evidence_bits)})" if evidence_bits else ""
        events.append(
            "Validation rule: "
            f"{rule.get('rule_id')} {rule.get('status')} - "
            f"expected={_display_value(rule.get('expected_value'))}, "
            f"actual={_display_value(rule.get('actual_value'))}, "
            f"code={_display_value(rule.get('error_code'))}{suffix}."
        )
    return events


def render_extracted_fields_response(current_processing_result: dict[str, Any] | None) -> str:
    if not current_processing_result:
        return "I do not have an extracted invoice yet. Please upload an invoice first."

    payload = (current_processing_result or {}).get("invoice_payload") or {}
    evidence = (current_processing_result or {}).get("ocr_evidence") or {}
    quality = (current_processing_result or {}).get("extraction_quality") or {}
    if quality.get("review_required") or (current_processing_result or {}).get("extraction_status") == "NEEDS_REVIEW":
        lines = ["I extracted the invoice fields, but some values need review before validation."]
    else:
        lines = ["Here are the fields I extracted from the invoice:"]
    lines.append("")
    fields = [
        ("Invoice number", payload.get("invoice_number")),
        ("Invoice date", payload.get("invoice_date")),
        ("Due date", payload.get("due_date")),
        ("PO number", payload.get("po_number")),
        ("Payment terms", payload.get("payment_terms")),
        ("Vendor name", payload.get("vendor_name")),
        ("Vendor tax ID", payload.get("vendor_tax_id")),
        ("Customer name", payload.get("customer_name")),
        ("Customer tax ID", payload.get("customer_tax_id")),
        ("Subtotal", payload.get("subtotal")),
        ("Tax", payload.get("tax")),
        ("Shipping", payload.get("shipping")),
        ("Discounts", payload.get("discounts")),
        ("Total", payload.get("total")),
        ("Currency", payload.get("currency")),
    ]
    lines.append("| Field | Extracted value |")
    lines.append("| --- | --- |")
    for label, value in fields:
        lines.append(f"| {label} | {_display_value(value)} |")

    items = payload.get("order_items") or []
    if items:
        lines.append("")
        lines.append("**Line items**")
        lines.append("")
        lines.append("| Line | Description | Qty | Unit price | Net amount | Tax | Gross amount |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for item in items:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _display_value(item.get("line_no")),
                        _display_value(item.get("description")),
                        _display_value(item.get("qty")),
                        _display_value(item.get("unit_price")),
                        _display_value(item.get("net_amount")),
                        _display_value(item.get("tax_rate")),
                        _display_value(item.get("gross_amount")),
                    ]
                )
                + " |"
            )

    notes = []
    confidence = evidence.get("avg_confidence")
    if isinstance(confidence, (int, float)):
        notes.append(f"OCR confidence: {confidence:.2f}")
    if quality.get("reasons"):
        notes.append("Extraction notes: " + ", ".join(str(reason).replace("_", " ") for reason in quality["reasons"]))
    if quality.get("missing_required_fields"):
        missing = ", ".join(str(field).replace("_", " ") for field in quality["missing_required_fields"])
        notes.append(f"Missing required fields: {missing}")
    if quality.get("low_confidence"):
        notes.append("OCR confidence is below the review threshold")
    if notes:
        lines.append("")
        lines.append("**Extraction notes:** " + "; ".join(notes))
    return "\n".join(lines)


def render_field_value_response(message: str, current_invoice_payload: dict[str, Any] | None) -> str:
    payload = current_invoice_payload or {}
    if not payload:
        return "I do not have an extracted invoice yet. Please upload an invoice first."

    field = _field_from_message(message)
    if field is None:
        return "I can answer a specific invoice field, such as total amount, vendor name, invoice date, tax, or PO number."

    value = payload.get(field)
    label = FIELD_LABELS.get(field, field.replace("_", " "))
    if field in {"total", "subtotal", "tax", "shipping", "discounts"}:
        value_text = _money_value(value, payload.get("currency"))
    else:
        value_text = _display_value(value)
    return f"The {label.lower()} is **{value_text}**."


FIELD_ALIASES = {
    "total": ("total", "total amount", "grand total", "amount due", "invoice amount"),
    "subtotal": ("subtotal", "sub total"),
    "tax": ("tax", "tax amount", "gst", "gst amount"),
    "shipping": ("shipping", "freight"),
    "discounts": ("discount", "discounts"),
    "currency": ("currency",),
    "vendor_name": ("vendor", "vendor name", "supplier", "supplier name"),
    "vendor_tax_id": ("vendor tax", "vendor gstin", "supplier gstin", "gstin"),
    "customer_name": ("customer", "customer name", "buyer", "buyer name"),
    "customer_tax_id": ("customer tax", "customer gstin", "buyer gstin"),
    "invoice_number": ("invoice number", "invoice no", "invoice id"),
    "invoice_date": ("invoice date", "date"),
    "due_date": ("due date",),
    "po_number": ("po number", "purchase order", "po"),
    "payment_terms": ("payment terms", "terms"),
}

FIELD_LABELS = {
    "invoice_number": "Invoice number",
    "invoice_date": "Invoice date",
    "due_date": "Due date",
    "po_number": "PO number",
    "payment_terms": "Payment terms",
    "vendor_name": "Vendor name",
    "vendor_tax_id": "Vendor tax ID",
    "customer_name": "Customer name",
    "customer_tax_id": "Customer tax ID",
    "subtotal": "Subtotal",
    "tax": "Tax",
    "shipping": "Shipping",
    "discounts": "Discounts",
    "total": "Total amount",
    "currency": "Currency",
}


def _field_from_message(message: str) -> str | None:
    text = (message or "").lower()
    ordered_fields = [
        "total",
        "subtotal",
        "tax",
        "shipping",
        "discounts",
        "currency",
        "vendor_tax_id",
        "vendor_name",
        "customer_tax_id",
        "customer_name",
        "invoice_number",
        "invoice_date",
        "due_date",
        "po_number",
        "payment_terms",
    ]
    for field in ordered_fields:
        if any(alias in text for alias in FIELD_ALIASES[field]):
            return field
    return None


def _money_value(value: Any, currency: Any) -> str:
    if value in (None, "") or str(value).strip().lower() in {"none", "null"}:
        return "Not found"
    currency_text = "" if currency in (None, "") else str(currency)
    return f"{currency_text} {value}".strip()


def _display_value(value: Any) -> str:
    if value in (None, "") or str(value).strip().lower() in {"none", "null"}:
        return "Not found"
    return str(value).replace("|", "\\|")


def _current_context_summary(
    current_invoice_payload: dict[str, Any] | None,
    current_processing_result: dict[str, Any] | None,
) -> dict[str, Any]:
    validation = (current_processing_result or {}).get("validation_result") or {}
    evidence = (current_processing_result or {}).get("ocr_evidence") or {}
    payload = current_invoice_payload or {}
    return {
        "has_current_invoice": bool(payload),
        "invoice_number": payload.get("invoice_number"),
        "vendor_name": payload.get("vendor_name"),
        "total": payload.get("total"),
        "currency": payload.get("currency"),
        "final_state": (current_processing_result or {}).get("final_state"),
        "invoice_id": validation.get("invoice_id"),
        "failed_rules": [
            {
                "rule_id": item.get("rule_id"),
                "error_code": item.get("error_code"),
                "severity": item.get("severity"),
                "expected_value": item.get("expected_value"),
                "actual_value": item.get("actual_value"),
                "evidence": item.get("evidence"),
                "detail": _format_failed_rule_detail(item, payload.get("currency")),
            }
            for item in validation.get("rule_results", [])
            if item.get("status") == "FAIL"
        ],
        "ocr_confidence": evidence.get("avg_confidence"),
        "extraction_mode": evidence.get("extraction_mode"),
        "has_extracted_fields": bool(payload),
    }


def _openai_client_required() -> OpenAI:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for invoice-processing chat LLM operations.")
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
    try:
        return OpenAI(base_url=base_url) if base_url else OpenAI()
    except Exception as exc:
        raise RuntimeError(f"Unable to initialize invoice-processing LLM client: {exc}") from exc


def _conversation_exists(conn, conversation_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
    return row is not None
