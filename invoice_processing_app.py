from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import streamlit as st

from invoice_processing.logging_config import configure_logging
from invoice_processing.chat_agent import (
    DEFAULT_MEMORY_WINDOW_CHARS,
    DEFAULT_MEMORY_WINDOW_MESSAGES,
    append_chat_message,
    classify_processing_intent,
    classify_upload_intent,
    ensure_conversation,
    list_chat_messages,
    load_chat_memory_context,
    render_extracted_fields_response,
    run_processing_chat_turn,
    summarize_processed_invoice_result,
)
from invoice_processing.orchestrator import (
    OCR_ROOT,
    VAL_ROOT,
    Milestone1NotebookAPI,
    run_invoice_processing,
)
from invoice_processing.rag_store import index_uploaded_invoice, list_uploaded_invoices, rag_status


LOG_FILE = configure_logging()
logger = logging.getLogger(__name__)

st.set_page_config(page_title="Invoice Processing", layout="wide")

st.markdown(
    """
    <style>
    .block-container {
        max-width: 980px;
        padding-top: 1.25rem;
        padding-bottom: 6rem;
    }
    [data-testid="stChatMessage"] {
        border-radius: 8px;
        padding: 0.45rem 0.25rem;
    }
    .app-header {
        border-bottom: 1px solid rgba(49, 51, 63, 0.16);
        padding-bottom: 0.85rem;
        margin-bottom: 1rem;
    }
    .app-kicker {
        color: #667085;
        font-size: 0.82rem;
        font-weight: 600;
        letter-spacing: 0;
        text-transform: uppercase;
    }
    .app-title {
        font-size: 1.85rem;
        font-weight: 700;
        line-height: 1.2;
        margin: 0.15rem 0 0.25rem 0;
    }
    .app-subtitle {
        color: #475467;
        font-size: 0.95rem;
        margin: 0;
    }
    .empty-panel {
        border: 1px solid rgba(49, 51, 63, 0.16);
        border-radius: 8px;
        padding: 1rem 1.1rem;
        margin: 0.75rem 0 1rem 0;
        background: rgba(248, 250, 252, 0.72);
    }
    .empty-title {
        font-weight: 650;
        margin-bottom: 0.25rem;
    }
    .empty-copy {
        color: #475467;
        margin: 0;
    }
    .attachment-line {
        color: #475467;
        font-size: 0.86rem;
        margin-top: 0.35rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="app-header">
      <div class="app-kicker">AP review workspace</div>
      <div class="app-title">Invoice Processing</div>
      <p class="app-subtitle">Upload invoices, extract fields, validate policy and totals, then ask follow-up questions in one thread.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def _ocr_api() -> Milestone1NotebookAPI:
    return Milestone1NotebookAPI(OCR_ROOT)


def _chat_db_path() -> Path:
    return VAL_ROOT / "data" / "invoice_processing.sqlite3"


def _ensure_chat_state() -> str:
    conversation_id = ensure_conversation(
        db_path=_chat_db_path(),
        conversation_id=st.session_state.get("chat_conversation_id"),
    )
    st.session_state["chat_conversation_id"] = conversation_id
    return conversation_id


def _chat_input_parts(value: Any) -> tuple[str, list[Any]]:
    if value is None:
        return "", []
    if isinstance(value, str):
        return value.strip(), []
    text = getattr(value, "text", None)
    files = getattr(value, "files", None)
    if isinstance(value, dict):
        text = value.get("text", text)
        files = value.get("files", files)
    return (text or "").strip(), list(files or [])


def _format_user_upload_text(text: str, file_names: list[str]) -> str:
    user_text = text or "Please process the attached invoice."
    if file_names:
        user_text += "\n\nAttached: " + ", ".join(file_names)
    return user_text


def _render_user_message(text: str, file_names: list[str] | None = None) -> None:
    file_names = file_names or []
    with st.chat_message("user"):
        st.markdown(text or "Please process the attached invoice.")
        if file_names:
            st.markdown(
                f"<div class='attachment-line'>Attached: {', '.join(file_names)}</div>",
                unsafe_allow_html=True,
            )


def _render_assistant_text(text: str, *, intent: str | None = None) -> None:
    if intent == "llm_error" or _looks_like_error_message(text):
        st.error(text or "Something went wrong.")
    else:
        st.markdown(text or "")


def _render_chat_messages(messages: list[dict[str, Any]]) -> None:
    if not messages:
        st.markdown(
            """
            <div class="empty-panel">
              <div class="empty-title">Ready for an invoice</div>
              <p class="empty-copy">Attach a JPG, PDF, or DOCX invoice and ask what you want done: extract fields, process it, explain a result, or check policy.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    for message in messages:
        role = "assistant" if message.get("sender") == "ASSISTANT" else "user"
        with st.chat_message(role):
            if role == "assistant":
                _render_assistant_text(message.get("message_text") or "", intent=message.get("intent"))
            else:
                st.markdown(message.get("message_text") or "")


def _looks_like_error_message(text: Any) -> bool:
    normalized = str(text or "").lower()
    return normalized.startswith(("error:", "llm chat failed:", "llm response generation failed:"))


def _record_user_upload(conversation_id: str, text: str, file_names: list[str]) -> None:
    user_text = _format_user_upload_text(text, file_names)
    append_chat_message(
        conversation_id,
        "USER",
        user_text,
        db_path=_chat_db_path(),
        intent="document_upload",
    )


def _process_uploaded_file(
    conversation_id: str,
    uploaded_file: Any,
    text: str,
    extraction_mode: str,
    *,
    extraction_only: bool = False,
) -> tuple[str, str]:
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    api = _ocr_api()
    api.field_extractor_mode = extraction_mode
    progress_events: list[str] = []
    source_file = getattr(uploaded_file, "name", "invoice")
    logger.info(
        "Starting upload processing source_file=%s extraction_mode=%s extraction_only=%s conversation_id=%s",
        source_file,
        extraction_mode,
        extraction_only,
        conversation_id,
    )

    with st.status(f"Processing {source_file}...", expanded=False) as status:
        def _progress(message: str) -> None:
            progress_events.append(message)
            logger.info("Progress source_file=%s message=%s", source_file, message)
            status.update(label=message)

        result = run_invoice_processing(
            uploaded_file,
            db_path=_chat_db_path(),
            ocr_api=api,
            progress=_progress,
            extraction_only=extraction_only,
        )
        if extraction_only:
            if _processing_errors(result):
                status.update(label="Could not extract invoice fields.", state="error")
            elif _extraction_needs_review(result):
                status.update(label="Extracted fields need review.", state="complete")
            else:
                status.update(label="Extracted invoice fields.", state="complete")
        else:
            if _processing_errors(result):
                status.update(label="Invoice processing failed.", state="error")
            else:
                status.update(label="Invoice processed.", state="complete")

    st.session_state["invoice_processing_result"] = result
    try:
        errors = _processing_errors(result)
        if errors:
            logger.error(
                "Upload processing finished with errors source_file=%s errors=%s result_keys=%s",
                source_file,
                errors,
                sorted(result.keys()),
            )
        else:
            logger.info(
                "Upload processing succeeded source_file=%s final_state=%s extraction_mode=%s",
                source_file,
                result.get("final_state"),
                (result.get("ocr_result") or {}).get("extraction_mode"),
            )
        rag_doc_id = None
        if not errors:
            rag_doc_id = index_uploaded_invoice(
                _chat_db_path(),
                result,
                conversation_id=conversation_id,
            )
        if errors:
            assistant_text = _processing_error_response(errors, extraction_only=extraction_only)
            intent = "processing_error"
        elif extraction_only:
            assistant_text = render_extracted_fields_response(result)
            intent = "show_extracted_fields"
        else:
            assistant_text = summarize_processed_invoice_result(
                result,
                chat_context=load_chat_memory_context(conversation_id, db_path=_chat_db_path()),
            )
            intent = "document_processed"
        message_json = {
            "final_state": result.get("final_state"),
            "source_file": getattr(uploaded_file, "name", None),
            "progress_events": progress_events,
            "user_message": text,
            "extraction_only": extraction_only,
            "errors": errors,
            "rag_doc_id": rag_doc_id,
        }
    except Exception as exc:
        logger.exception("Failed while preparing upload summary source_file=%s", source_file)
        assistant_text = f"LLM response generation failed: {exc}"
        intent = "llm_error"
        message_json = {
            "error": str(exc),
            "stage": "upload_summary",
            "final_state": result.get("final_state"),
            "source_file": getattr(uploaded_file, "name", None),
        }
    append_chat_message(
        conversation_id,
        "ASSISTANT",
        assistant_text,
        db_path=_chat_db_path(),
        invoice_id=(result.get("validation_result") or {}).get("invoice_id"),
        intent=intent,
        message_json=message_json,
    )
    return assistant_text, intent


def _processing_errors(result: dict[str, Any]) -> list[str]:
    errors = [str(error) for error in (result.get("errors") or []) if error]
    ocr_result = result.get("ocr_result") or {}
    if ocr_result.get("status") == "error":
        errors.append(str(ocr_result.get("error") or "OCR failed."))
    evidence = result.get("ocr_evidence") or {}
    if evidence.get("extraction_error"):
        detail = evidence.get("extraction_error_detail")
        if detail:
            errors.append(f"Field extraction failed: {evidence['extraction_error']}: {detail}")
        else:
            errors.append(f"Field extraction failed: {evidence['extraction_error']}")
    deduped = []
    for error in errors:
        if error not in deduped:
            deduped.append(error)
    return deduped


def _extraction_needs_review(result: dict[str, Any]) -> bool:
    quality = (result or {}).get("extraction_quality") or {}
    return bool(
        (result or {}).get("extraction_status") == "NEEDS_REVIEW"
        or (result or {}).get("extraction_review_required")
        or quality.get("review_required")
    )


def _processing_error_response(errors: list[str], *, extraction_only: bool) -> str:
    action = "extract the invoice fields" if extraction_only else "process the invoice"
    friendly_errors = [_friendly_processing_error(error) for error in errors[:5]]
    lines = [f"Error: I could not {action}."]
    lines.append("")
    lines.append("What went wrong:")
    for error in friendly_errors:
        lines.append(f"- {error}")
    lines.append("")
    if any("Qwen" in error for error in friendly_errors):
        lines.append("Try Auto or GPT extraction mode, or retry with a clearer invoice.")
    else:
        lines.append("Please try another file or retry with a clearer invoice.")
    return "\n".join(lines)


def _friendly_processing_error(error: str) -> str:
    if "qwen_parse_error" in error:
        return "Qwen returned invoice details in a format I could not read reliably."
    if "qwen_unavailable" in error:
        detail = error.split("qwen_unavailable", 1)[-1].strip(" :")
        if detail:
            return f"Qwen is not available on this machine right now: {detail}"
        return "Qwen is not available on this machine right now."
    if "qwen_error" in error:
        detail = error.split("qwen_error", 1)[-1].strip(" :")
        if detail:
            return f"Qwen failed while extracting invoice fields: {detail}"
        return "Qwen failed while extracting invoice fields."
    if "gpt_parse_error" in error:
        return "The GPT extractor returned invoice details in a format I could not read reliably."
    if "gpt_unavailable" in error:
        return "GPT extraction is not configured right now."
    if "gpt_policy_block" in error:
        return "GPT extraction was blocked by a content policy check."
    return error


def _run_text_chat(conversation_id: str, text: str) -> dict[str, Any]:
    current_result = st.session_state.get("invoice_processing_result") or {}
    current_payload = current_result.get("invoice_payload")
    logger.info("Starting text chat turn conversation_id=%s text_chars=%s", conversation_id, len(text or ""))
    with st.spinner("Thinking..."):
        try:
            result = run_processing_chat_turn(
                text,
                conversation_id=conversation_id,
                db_path=_chat_db_path(),
                current_invoice_payload=current_payload,
                current_processing_result=current_result,
                persist_user_message=False,
            )
        except Exception as exc:
            logger.exception("Text chat turn failed conversation_id=%s", conversation_id)
            assistant_text = f"LLM chat failed: {exc}"
            append_chat_message(
                conversation_id,
                "ASSISTANT",
                assistant_text,
                db_path=_chat_db_path(),
                intent="llm_error",
                message_json={"error": str(exc), "stage": "chat_turn"},
            )
            result = {"intent": "llm_error", "assistant_response": assistant_text}
    logger.info("Finished text chat turn conversation_id=%s intent=%s", conversation_id, result.get("intent"))
    st.session_state["chat_last_result"] = result
    return result


def _queue_user_text_turn(conversation_id: str, text: str) -> None:
    append_chat_message(
        conversation_id,
        "USER",
        text,
        db_path=_chat_db_path(),
        intent="user_message",
    )
    st.session_state["pending_chat_turn"] = {"conversation_id": conversation_id, "text": text}


def _upload_intent(conversation_id: str, text: str) -> str:
    if not text.strip():
        return "document_processed"
    chat_context = load_chat_memory_context(conversation_id, db_path=_chat_db_path())
    upload_intent = classify_upload_intent(text, chat_context)
    logger.info(
        "Upload action classified conversation_id=%s upload_intent=%s text_chars=%s",
        conversation_id,
        upload_intent,
        len(text or ""),
    )
    if upload_intent == "extract_only":
        return "show_extracted_fields"
    if upload_intent == "validate":
        return "document_processed"
    return classify_processing_intent(
        text,
        chat_context,
        current_invoice_payload=st.session_state.get("invoice_processing_result", {}).get("invoice_payload"),
        current_processing_result=st.session_state.get("invoice_processing_result"),
    )


def _unique_invoice_rows(limit: int = 500) -> list[dict[str, Any]]:
    rows = list_uploaded_invoices(_chat_db_path(), limit=limit)
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        invoice_number = str(row.get("invoice_number") or "").strip().lower()
        vendor_name = str(row.get("vendor_name") or "").strip().lower()
        if invoice_number or vendor_name:
            key = ("invoice", vendor_name, invoice_number, "")
        else:
            key = (
                "file",
                str(row.get("source_file") or "").strip().lower(),
                str(row.get("invoice_date") or "").strip().lower(),
                str(row.get("total") or "").strip().lower(),
            )
        if key not in unique:
            unique[key] = row
    return list(unique.values())


def _render_unique_invoice_list() -> None:
    invoices = _unique_invoice_rows()
    if not invoices:
        st.caption("No uploaded invoices yet.")
        return

    st.caption(f"Unique invoices: {len(invoices)}")
    for invoice in invoices:
        invoice_number = invoice.get("invoice_number") or "Invoice number missing"
        vendor = invoice.get("vendor_name") or "Vendor missing"
        state = invoice.get("final_state") or "PENDING"
        total = invoice.get("total")
        currency = invoice.get("currency") or ""
        total_text = f"{currency} {total}".strip() if total not in (None, "") else "Total missing"
        source = invoice.get("source_file") or "Unknown source"
        with st.expander(f"{invoice_number} · {vendor}", expanded=False):
            st.caption(f"State: {state}")
            st.caption(f"Total: {total_text}")
            st.caption(f"Date: {invoice.get('invoice_date') or 'Not found'}")
            st.caption(f"Source: {source}")


with st.sidebar:
    st.subheader("Session")
    extraction_mode = st.selectbox(
        "Extraction mode",
        ["gpt", "auto", "qwen"],
        help="Use gpt for remote LLM extraction. Qwen works locally but can be slow on CPU.",
    )
    if st.button("New Chat", width="stretch"):
        st.session_state["chat_conversation_id"] = ensure_conversation(db_path=_chat_db_path(), conversation_id=None)
        st.session_state.pop("invoice_processing_result", None)
        st.session_state.pop("chat_last_result", None)
        st.rerun()

    current = st.session_state.get("invoice_processing_result") or {}
    if current:
        st.caption(f"Current state: {current.get('final_state', 'PENDING')}")
        validation = current.get("validation_result") or {}
        if validation.get("invoice_id"):
            st.caption(f"Invoice: {validation['invoice_id']}")
    status = rag_status(_chat_db_path())
    if status.get("documents_indexed"):
        st.caption(f"Indexed invoices: {status['documents_indexed']}")
    with st.expander("Uploaded invoices", expanded=True):
        _render_unique_invoice_list()
    st.caption(
        f"Memory window: last {DEFAULT_MEMORY_WINDOW_MESSAGES} messages, "
        f"{DEFAULT_MEMORY_WINDOW_CHARS:,} chars"
    )
    st.divider()
    # st.caption(f"Logs: {LOG_FILE}")


conversation_id = _ensure_chat_state()
_ = _render_chat_messages(list_chat_messages(conversation_id, db_path=_chat_db_path()))

pending_turn = st.session_state.pop("pending_chat_turn", None)
if pending_turn and pending_turn.get("conversation_id") == conversation_id:
    _run_text_chat(conversation_id, pending_turn.get("text", ""))
    st.rerun()

chat_value = st.chat_input(
    "Ask about an invoice or attach one to process",
    accept_file=True,
    file_type=["jpg", "jpeg", "png", "pdf", "docx"],
)

if chat_value:
    text, files = _chat_input_parts(chat_value)
    if files:
        file_names = [getattr(file, "name", "invoice") for file in files]
        _record_user_upload(conversation_id, text, file_names)
        _render_user_message(text, file_names)
        try:
            upload_intent = _upload_intent(conversation_id, text)
            logger.info(
                "Upload intent classified conversation_id=%s intent=%s text_chars=%s files=%s",
                conversation_id,
                upload_intent,
                len(text or ""),
                file_names,
            )
        except Exception as exc:
            logger.exception("Upload intent classification failed conversation_id=%s", conversation_id)
            assistant_text = f"LLM chat failed: {exc}"
            append_chat_message(
                conversation_id,
                "ASSISTANT",
                assistant_text,
                db_path=_chat_db_path(),
                intent="llm_error",
                message_json={"error": str(exc), "stage": "upload_intent"},
            )
            with st.chat_message("assistant"):
                _render_assistant_text(assistant_text, intent="llm_error")
            st.rerun()
        with st.chat_message("assistant"):
            assistant_text, intent = _process_uploaded_file(
                conversation_id,
                files[0],
                text,
                extraction_mode,
                extraction_only=upload_intent == "show_extracted_fields",
            )
            _render_assistant_text(assistant_text, intent=intent)
        if len(files) > 1:
            limit_text = "I processed the first attached invoice. Please send additional invoices one at a time for this demo."
            append_chat_message(
                conversation_id,
                "ASSISTANT",
                limit_text,
                db_path=_chat_db_path(),
                intent="document_upload_limit",
            )
            with st.chat_message("assistant"):
                st.info(limit_text)
        st.rerun()
    elif text:
        append_chat_message(
            conversation_id,
            "USER",
            text,
            db_path=_chat_db_path(),
            intent="user_message",
        )
        _render_user_message(text)
        with st.chat_message("assistant"):
            result = _run_text_chat(conversation_id, text)
            _render_assistant_text(result.get("assistant_response", ""), intent=result.get("intent"))
        st.rerun()
