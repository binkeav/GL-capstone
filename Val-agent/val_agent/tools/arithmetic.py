from __future__ import annotations

from decimal import Decimal

from val_agent.models import RuleResult, money


AGENT = "Financial Validation Agent"
TOLERANCE = Decimal("0.01")


def _result(
    rule_id: str,
    passed: bool,
    expected: str,
    actual: str,
    error_code: str,
    *,
    evidence: dict | None = None,
    status: str | None = None,
) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        agent_name=AGENT,
        status=status or ("PASS" if passed else "FAIL"),
        severity="INFO" if status == "SKIPPED" else "ERROR",
        expected_value=expected,
        actual_value=actual,
        error_code=None if passed or status == "SKIPPED" else error_code,
        evidence=evidence or {},
    )


def validate_arithmetic(invoice: dict) -> list[RuleResult]:
    results: list[RuleResult] = []
    line_sum = Decimal("0.00")

    items = invoice.get("order_items", [])
    for item in items:
        expected = money(item.get("qty")) * money(item.get("unit_price"))
        actual = money(item.get("net_amount"))
        line_sum += actual
        passed = abs(expected - actual) <= TOLERANCE
        results.append(
            _result(
                "ARITH_LINE_ITEM_MATCH",
                passed,
                str(expected.quantize(Decimal("0.01"))),
                str(actual),
                "ERR_LINE_ITEM_AMOUNT_MISMATCH",
                evidence={
                    "qty": str(money(item.get("qty"))),
                    "unit_price": str(money(item.get("unit_price"))),
                    "net_amount": str(actual),
                    "formula": "qty * unit_price = net_amount",
                },
            )
        )

    subtotal = money(invoice.get("subtotal"))
    if items:
        results.append(
            _result(
                "ARITH_SUBTOTAL_MATCH",
                abs(line_sum - subtotal) <= TOLERANCE,
                str(line_sum.quantize(Decimal("0.01"))),
                str(subtotal),
                "ERR_SUBTOTAL_MISMATCH",
                evidence={
                    "line_net_sum": str(line_sum.quantize(Decimal("0.01"))),
                    "subtotal": str(subtotal),
                    "formula": "sum(line net amounts) = subtotal",
                },
            )
        )
    else:
        results.append(
            _result(
                "ARITH_SUBTOTAL_MATCH",
                True,
                "not checked",
                str(subtotal),
                "ERR_SUBTOTAL_MISMATCH",
                status="SKIPPED",
                evidence={
                    "subtotal": str(subtotal),
                    "reason": "No line items were extracted, so subtotal cannot be reconciled to line net amounts.",
                },
            )
        )

    expected_total = (
        subtotal + money(invoice.get("tax")) + money(invoice.get("shipping")) - money(invoice.get("discounts"))
    )
    actual_total = money(invoice.get("total"))
    results.append(
        _result(
            "ARITH_TOTAL_MATCH",
            abs(expected_total - actual_total) <= TOLERANCE,
            str(expected_total.quantize(Decimal("0.01"))),
            str(actual_total),
            "ERR_GRAND_TOTAL_MISMATCH",
            evidence={
                "subtotal": str(subtotal),
                "tax": str(money(invoice.get("tax"))),
                "shipping": str(money(invoice.get("shipping"))),
                "discounts": str(money(invoice.get("discounts"))),
                "expected_total": str(expected_total.quantize(Decimal("0.01"))),
                "actual_total": str(actual_total),
                "formula": "subtotal + tax + shipping - discounts = total",
            },
        )
    )
    return results
