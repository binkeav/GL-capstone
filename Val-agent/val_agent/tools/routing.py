from __future__ import annotations

from val_agent.models import RuleResult, ValidationRoute


def decide_route(rule_results: list[RuleResult]) -> ValidationRoute:
    failed = [result for result in rule_results if result.failed]
    if any(result.severity == "FATAL" for result in failed):
        return "CRITICAL_REJECTION"
    if any(result.severity == "ERROR" for result in failed):
        return "HUMAN_IN_THE_LOOP"
    return "STRAIGHT_THROUGH_PROCESSING"

