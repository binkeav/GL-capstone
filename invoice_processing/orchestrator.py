from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Literal, TypedDict

from langgraph.graph import END, StateGraph

from invoice_processing.adapter import adapt_ocr_fields, build_ocr_evidence, extraction_quality
from invoice_processing.logging_config import configure_logging


ROOT = Path(__file__).resolve().parents[1]
OCR_ROOT = ROOT / "ocr-agent"
VAL_ROOT = ROOT / "Val-agent"
configure_logging()
logger = logging.getLogger(__name__)


def _ensure_agent_paths() -> None:
    for path in (str(OCR_ROOT), str(VAL_ROOT)):
        if path not in sys.path:
            sys.path.insert(0, path)


_ensure_agent_paths()

from milestone2_backend import Milestone1NotebookAPI  # noqa: E402
from val_agent.db import connect, create_conversation, init_db  # noqa: E402
from val_agent.graph import run_validation_graph  # noqa: E402


ProgressCallback = Callable[[str], None]


class InvoiceProcessingState(TypedDict, total=False):
    document_file: Any
    source_file: str | None
    user_id: str
    user_role: str
    conversation_id: str | None
    ocr_result: dict[str, Any]
    ocr_evidence: dict[str, Any]
    invoice_payload: dict[str, Any]
    extraction_quality: dict[str, Any]
    extraction_review_required: bool
    validation_result: dict[str, Any]
    extraction_only: bool
    extraction_status: Literal["COMPLETE", "NEEDS_REVIEW"]
    final_state: Literal[
        "PENDING",
        "HUMAN_IN_THE_LOOP",
        "STRAIGHT_THROUGH_PROCESSING",
        "CRITICAL_REJECTION",
    ]
    assistant_response: str
    progress_events: list[str]
    errors: list[str]


def run_invoice_processing(
    document_file: Any,
    *,
    db_path: str | Path = VAL_ROOT / "data" / "val_agent.sqlite3",
    ocr_api: Milestone1NotebookAPI | None = None,
    user_id: str = "demo_user",
    user_role: str = "AP_REVIEWER",
    progress: ProgressCallback | None = None,
    extraction_only: bool = False,
) -> InvoiceProcessingState:
    source_file = getattr(document_file, "name", None)
    logger.info(
        "run_invoice_processing start source_file=%s db_path=%s user_id=%s user_role=%s extraction_only=%s",
        source_file,
        db_path,
        user_id,
        user_role,
        extraction_only,
    )
    conn = connect(db_path)
    try:
        init_db(conn)
        graph = build_invoice_processing_graph(conn, ocr_api or Milestone1NotebookAPI(OCR_ROOT), progress)
        result = graph.invoke(
            {
                "document_file": document_file,
                "source_file": source_file,
                "user_id": user_id,
                "user_role": user_role,
                "final_state": "PENDING",
                "errors": [],
                "progress_events": [],
                "extraction_only": extraction_only,
            }
        )
    finally:
        conn.close()
    logger.info(
        "run_invoice_processing finish source_file=%s final_state=%s errors=%s extraction_mode=%s",
        source_file,
        result.get("final_state"),
        result.get("errors"),
        (result.get("ocr_result") or {}).get("extraction_mode"),
    )
    return result


def run_invoice_payload_validation(
    invoice_payload: dict[str, Any],
    *,
    db_path: str | Path = VAL_ROOT / "data" / "val_agent.sqlite3",
    user_id: str = "demo_user",
    user_role: str = "AP_REVIEWER",
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        init_db(conn)
        state: InvoiceProcessingState = {
            "invoice_payload": invoice_payload,
            "user_id": user_id,
            "user_role": user_role,
            "final_state": "PENDING",
            "errors": [],
            "progress_events": [],
        }
        return _run_val_agent_subgraph(conn, state, progress)["validation_result"]
    finally:
        conn.close()


def build_invoice_processing_graph(
    conn,
    ocr_api: Milestone1NotebookAPI,
    progress: ProgressCallback | None = None,
):
    workflow = StateGraph(InvoiceProcessingState)
    workflow.add_node("document_intake", lambda state: _document_intake(state, progress))
    workflow.add_node("ocr_extraction_subgraph", lambda state: _ocr_extraction_subgraph(ocr_api, state, progress))
    workflow.add_node("ocr_to_validation_adapter", lambda state: _ocr_to_validation_adapter(state, progress))
    workflow.add_node("extraction_quality_gate", lambda state: _extraction_quality_gate(state, progress))
    workflow.add_node("extraction_only_result", lambda state: _extraction_only_result(state, progress))
    workflow.add_node("human_correction", lambda state: _human_correction(state, progress))
    workflow.add_node("val_agent_subgraph", lambda state: _run_val_agent_subgraph(conn, state, progress))
    workflow.add_node("unified_result_presenter", lambda state: _unified_result_presenter(state, progress))

    workflow.set_entry_point("document_intake")
    workflow.add_edge("document_intake", "ocr_extraction_subgraph")
    workflow.add_edge("ocr_extraction_subgraph", "ocr_to_validation_adapter")
    workflow.add_edge("ocr_to_validation_adapter", "extraction_quality_gate")
    workflow.add_conditional_edges(
        "extraction_quality_gate",
        _route_after_extraction_quality,
        {
            "extract": "extraction_only_result",
            "review": "human_correction",
            "validate": "val_agent_subgraph",
        },
    )
    workflow.add_edge("extraction_only_result", END)
    workflow.add_edge("human_correction", "unified_result_presenter")
    workflow.add_edge("val_agent_subgraph", "unified_result_presenter")
    workflow.add_edge("unified_result_presenter", END)
    return workflow.compile()


def _emit(state: InvoiceProcessingState, progress: ProgressCallback | None, message: str) -> None:
    state.setdefault("progress_events", []).append(message)
    logger.info(
        "workflow_event source_file=%s final_state=%s message=%s",
        state.get("source_file"),
        state.get("final_state"),
        message,
    )
    if progress:
        progress(message)


def _document_intake(state: InvoiceProcessingState, progress: ProgressCallback | None) -> InvoiceProcessingState:
    _emit(state, progress, "Invoice Processing: document received.")
    if state.get("document_file") is None:
        state.setdefault("errors", []).append("No document file was provided.")
        state["final_state"] = "HUMAN_IN_THE_LOOP"
    return state


def _ocr_extraction_subgraph(
    ocr_api: Milestone1NotebookAPI,
    state: InvoiceProcessingState,
    progress: ProgressCallback | None,
) -> InvoiceProcessingState:
    if state.get("final_state") == "HUMAN_IN_THE_LOOP" and state.get("errors"):
        return state
    _emit(state, progress, "OCR Extraction Subgraph: extracting text and invoice fields.")
    document = state["document_file"]
    if hasattr(document, "seek"):
        document.seek(0)
    try:
        state["ocr_result"] = ocr_api.process_upload(document)
        fields = (state.get("ocr_result") or {}).get("fields") or {}
        logger.info(
            "ocr_extraction completed source_file=%s status=%s extraction_mode=%s extraction_error=%s detail=%s text_chars=%s",
            state.get("source_file"),
            (state.get("ocr_result") or {}).get("status"),
            (state.get("ocr_result") or {}).get("extraction_mode"),
            fields.get("extraction_error"),
            fields.get("extraction_error_detail"),
            len((state.get("ocr_result") or {}).get("text") or ""),
        )
    except Exception as exc:
        logger.exception("ocr_extraction failed source_file=%s", state.get("source_file"))
        state["ocr_result"] = {"status": "error", "error": str(exc), "fields": {}, "text": ""}
        state.setdefault("errors", []).append(f"OCR failed: {exc}")
    return state


def _ocr_to_validation_adapter(
    state: InvoiceProcessingState,
    progress: ProgressCallback | None,
) -> InvoiceProcessingState:
    _emit(state, progress, "OCR Adapter: mapping extracted fields to validation schema.")
    ocr_result = state.get("ocr_result") or {}
    state["invoice_payload"] = adapt_ocr_fields(ocr_result.get("fields"))
    state["ocr_evidence"] = build_ocr_evidence(ocr_result, state.get("source_file"))
    logger.info(
        "ocr_adapter completed source_file=%s extraction_error=%s payload_invoice_id=%s payload_total=%s",
        state.get("source_file"),
        (state.get("ocr_evidence") or {}).get("extraction_error"),
        (state.get("invoice_payload") or {}).get("invoice_id"),
        (state.get("invoice_payload") or {}).get("total_amount"),
    )
    return state


def _extraction_quality_gate(
    state: InvoiceProcessingState,
    progress: ProgressCallback | None,
) -> InvoiceProcessingState:
    _emit(state, progress, "Extraction Quality Gate: checking required fields and OCR confidence.")
    quality = extraction_quality(state.get("invoice_payload") or {}, state.get("ocr_evidence") or {})
    if (state.get("ocr_result") or {}).get("status") == "error":
        quality["review_required"] = True
        quality.setdefault("reasons", []).append("ocr_error")
    state["extraction_quality"] = quality
    state["extraction_review_required"] = bool(quality.get("review_required"))
    logger.info(
        "quality_gate completed source_file=%s review_required=%s reasons=%s missing=%s",
        state.get("source_file"),
        state.get("extraction_review_required"),
        quality.get("reasons"),
        quality.get("missing_required_fields"),
    )
    return state


def _route_after_extraction_quality(state: InvoiceProcessingState) -> str:
    if state.get("extraction_only"):
        return "extract"
    if state.get("extraction_review_required"):
        return "review"
    return "validate"


def _extraction_only_result(
    state: InvoiceProcessingState,
    progress: ProgressCallback | None,
) -> InvoiceProcessingState:
    _emit(state, progress, "Invoice Processing: extracted fields prepared without validation.")
    state["validation_result"] = {}
    state["assistant_response"] = ""
    if state.get("extraction_review_required"):
        state["extraction_status"] = "NEEDS_REVIEW"
        state["final_state"] = "HUMAN_IN_THE_LOOP"
    else:
        state["extraction_status"] = "COMPLETE"
        state["final_state"] = state.get("final_state", "PENDING")
    logger.info(
        "extraction_only completed source_file=%s status=%s review_required=%s reasons=%s errors=%s extraction_mode=%s",
        state.get("source_file"),
        state.get("extraction_status"),
        state.get("extraction_review_required"),
        (state.get("extraction_quality") or {}).get("reasons"),
        state.get("errors"),
        (state.get("ocr_result") or {}).get("extraction_mode"),
    )
    return state


def _human_correction(
    state: InvoiceProcessingState,
    progress: ProgressCallback | None,
) -> InvoiceProcessingState:
    _emit(state, progress, "Human Correction: extraction needs reviewer attention before validation.")
    state["final_state"] = "HUMAN_IN_THE_LOOP"
    missing = ", ".join(state.get("extraction_quality", {}).get("missing_required_fields", [])) or "none"
    reasons = ", ".join(state.get("extraction_quality", {}).get("reasons", [])) or "extraction_review"
    state["assistant_response"] = (
        "Result: HUMAN_IN_THE_LOOP\n\n"
        "The invoice extraction needs review before deterministic validation can run.\n"
        f"Missing required fields: {missing}\n"
        f"Review reasons: {reasons}\n\n"
        "Next steps: correct the extracted invoice fields and run validation again."
    )
    state["validation_result"] = {}
    return state


def _run_val_agent_subgraph(conn, state: InvoiceProcessingState, progress: ProgressCallback | None) -> InvoiceProcessingState:
    _emit(state, progress, "Val-agent Subgraph: running deterministic invoice validation.")
    conversation_id = state.get("conversation_id") or create_conversation(
        conn,
        state.get("user_id", "demo_user"),
        state.get("user_role", "AP_REVIEWER"),
    )
    state["conversation_id"] = conversation_id
    invoice_payload = state.get("invoice_payload") or {}
    val_progress: list[str] = []
    val_state = run_validation_graph(
        conn,
        {
            "conversation_id": conversation_id,
            "user_id": state.get("user_id", "demo_user"),
            "user_role": state.get("user_role", "AP_REVIEWER"),
            "user_message": json.dumps(invoice_payload),
            "chat_context": [],
            "final_state": "PENDING",
            "rule_results": [],
            "errors": [],
        },
        progress=val_progress.append,
    )
    for event in val_progress:
        _emit(state, progress, event)
    state["validation_result"] = {
        "invoice_id": val_state.get("invoice_id"),
        "final_state": val_state.get("final_state"),
        "normalized_invoice": val_state.get("normalized_invoice"),
        "rule_results": val_state.get("rule_results", []),
        "errors": val_state.get("errors", []),
        "assistant_response": val_state.get("assistant_response"),
    }
    state["final_state"] = val_state.get("final_state", "PENDING")
    state["assistant_response"] = val_state.get("assistant_response", "")
    logger.info(
        "val_agent completed source_file=%s conversation_id=%s final_state=%s errors=%s rule_count=%s",
        state.get("source_file"),
        conversation_id,
        state.get("final_state"),
        val_state.get("errors"),
        len(val_state.get("rule_results", [])),
    )
    return state


def _unified_result_presenter(
    state: InvoiceProcessingState,
    progress: ProgressCallback | None,
) -> InvoiceProcessingState:
    _emit(state, progress, "Unified Result Presenter: preparing OCR and validation result.")
    if not state.get("assistant_response") and state.get("validation_result"):
        state["assistant_response"] = state["validation_result"].get("assistant_response", "")
    return state
