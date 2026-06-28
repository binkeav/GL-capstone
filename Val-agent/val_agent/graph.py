from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from langgraph.graph import END, StateGraph

from val_agent.agents.chat_agent import chat_intake
from val_agent.agents.compliance_risk_agent import compliance_node
from val_agent.agents.decision_agent import decision_node
from val_agent.agents.financial_agent import financial_node
from val_agent.agents.intake_agent import normalize_node
from val_agent.agents.transaction_agent import transaction_node
from val_agent.db import save_rule_results, update_invoice_state, upsert_invoice
from val_agent.models import RuleResult
from val_agent.state import ValidationState
from val_agent.tools.compliance import lookup_compliance_policy
from val_agent.tools.chat import render_general_response, render_response


ProgressCallback = Callable[[str], None]


def run_validation_graph(
    conn: sqlite3.Connection,
    state: ValidationState,
    progress: ProgressCallback | None = None,
) -> ValidationState:
    graph = build_validation_graph(conn, progress)
    return graph.invoke(state)


def build_validation_graph(
    conn: sqlite3.Connection,
    progress: ProgressCallback | None = None,
) -> Callable[[ValidationState], ValidationState]:
    workflow = StateGraph(ValidationState)
    workflow.add_node("chat_intake", lambda state: _with_progress(progress, "Chat Agent: understanding your message.", chat_intake, state))
    workflow.add_node(
        "normalize_invoice",
        lambda state: _with_progress(
            progress,
            "Invoice Intake Agent: reading invoice JSON and normalizing fields.",
            normalize_node,
            state,
        ),
    )
    workflow.add_node(
        "persist_invoice",
        lambda state: _with_progress(
            progress,
            "Supervisor Validation Agent: saving the invoice case locally.",
            lambda s: _persist_invoice(conn, s),
            state,
        ),
    )
    workflow.add_node("parallel_validations", lambda state: _run_parallel_validations(conn, state, progress))
    workflow.add_node(
        "decision_route",
        lambda state: _with_progress(
            progress,
            "Decision & Routing Agent: choosing the invoice route from the validation results.",
            decision_node,
            state,
        ),
    )
    workflow.add_node(
        "persist_audit",
        lambda state: _with_progress(
            progress,
            "Supervisor Validation Agent: writing the audit trail.",
            lambda s: _persist_audit(conn, s),
            state,
        ),
    )
    workflow.add_node(
        "respond",
        lambda state: _with_progress(progress, "Chat Agent: preparing the final response.", lambda s: _respond(conn, s), state),
    )

    workflow.set_entry_point("chat_intake")
    workflow.add_conditional_edges(
        "chat_intake",
        _route_after_chat_intake,
        {
            "validate": "normalize_invoice",
            "respond": "respond",
        },
    )
    workflow.add_conditional_edges(
        "normalize_invoice",
        _route_after_normalize,
        {
            "persist": "persist_invoice",
            "respond": "respond",
        },
    )
    workflow.add_edge("persist_invoice", "parallel_validations")
    workflow.add_edge("parallel_validations", "decision_route")
    workflow.add_edge("decision_route", "persist_audit")
    workflow.add_edge("persist_audit", "respond")
    workflow.add_edge("respond", END)
    return workflow.compile()


def _emit(progress: ProgressCallback | None, message: str) -> None:
    if progress:
        progress(message)


def _with_progress(
    progress: ProgressCallback | None,
    message: str,
    fn: Callable[[ValidationState], ValidationState],
    state: ValidationState,
) -> ValidationState:
    _emit(progress, message)
    return fn(state)


def _route_after_chat_intake(state: ValidationState) -> str:
    return "validate" if state.get("intent") == "validate_invoice" else "respond"


def _route_after_normalize(state: ValidationState) -> str:
    return "persist" if state.get("normalized_invoice") else "respond"


def _persist_invoice(conn: sqlite3.Connection, state: ValidationState) -> ValidationState:
    invoice_id = upsert_invoice(conn, state["raw_invoice"] or {}, state["normalized_invoice"] or {})
    state["invoice_id"] = invoice_id
    state["normalized_invoice"]["invoice_id"] = invoice_id
    return state


def _run_parallel_validations(
    conn: sqlite3.Connection,
    state: ValidationState,
    progress: ProgressCallback | None = None,
) -> ValidationState:
    _emit(progress, "Supervisor Validation Agent: running specialist validation agents in parallel.")
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(_run_named_validation, progress, "Financial Validation Agent: checking arithmetic totals.", financial_node, state),
            executor.submit(
                _run_named_validation,
                progress,
                "Transaction Match Agent: checking PO, goods receipt, and duplicate signals.",
                lambda s: transaction_node(conn, s),
                state,
            ),
            executor.submit(
                _run_named_validation,
                progress,
                "Compliance & Risk Agent: checking GST, INR, and vendor status.",
                lambda s: compliance_node(conn, s),
                state,
            ),
        ]
        rule_results: list[dict] = []
        for future in futures:
            rule_results.extend(future.result())

    state["rule_results"] = rule_results
    _emit(progress, "Supervisor Validation Agent: validation checks complete.")
    return state


def _run_named_validation(
    progress: ProgressCallback | None,
    message: str,
    fn: Callable[[ValidationState], list[dict]],
    state: ValidationState,
) -> list[dict]:
    _emit(progress, message)
    return fn(state)


def _persist_audit(conn: sqlite3.Connection, state: ValidationState) -> ValidationState:
    invoice_id = state["invoice_id"]
    rule_results = state.get("rule_results", [])
    typed_results = [
        RuleResult(
            rule_id=result["rule_id"],
            agent_name=result["agent_name"],
            status=result["status"],
            severity=result["severity"],
            expected_value=result.get("expected_value"),
            actual_value=result.get("actual_value"),
            error_code=result.get("error_code"),
            evidence=result.get("evidence") or {},
        )
        for result in rule_results
    ]
    save_rule_results(conn, invoice_id, typed_results)
    update_invoice_state(conn, invoice_id, state["final_state"])
    return state


def _respond(conn: sqlite3.Connection, state: ValidationState) -> ValidationState:
    if state.get("intent") != "validate_invoice":
        state["final_state"] = state.get("final_state", "PENDING")
        policy = lookup_compliance_policy(conn)
        state["assistant_response"] = render_general_response(
            state.get("user_message", ""),
            state.get("intent", "general_chat"),
            state.get("chat_context", []),
            policy,
        )
        return state
    if not state.get("normalized_invoice"):
        state["final_state"] = "HUMAN_IN_THE_LOOP"
        state["assistant_response"] = "I could not find valid invoice JSON in the message."
        return state
    state["assistant_response"] = render_response(
        state["final_state"],
        state.get("rule_results", []),
        state.get("invoice_id"),
        state.get("normalized_invoice", {}).get("vendor_name"),
        state.get("normalized_invoice", {}).get("currency"),
        state.get("chat_context", []),
    )
    return state
