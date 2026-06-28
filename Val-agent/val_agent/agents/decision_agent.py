from __future__ import annotations

from val_agent.models import RuleResult
from val_agent.state import ValidationState
from val_agent.tools.routing import decide_route


def decision_node(state: ValidationState) -> ValidationState:
    results = [
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
        for result in state.get("rule_results", [])
    ]
    state["final_state"] = decide_route(results)
    state["errors"] = [result.error_code for result in results if result.failed and result.error_code]
    return state

