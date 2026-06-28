from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Literal


ValidationRoute = Literal[
    "PENDING",
    "STRAIGHT_THROUGH_PROCESSING",
    "HUMAN_IN_THE_LOOP",
    "CRITICAL_REJECTION",
]

RuleStatus = Literal["PASS", "FAIL", "SKIPPED"]
RuleSeverity = Literal["INFO", "WARNING", "ERROR", "FATAL"]


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    agent_name: str
    status: RuleStatus
    severity: RuleSeverity
    expected_value: str | None = None
    actual_value: str | None = None
    error_code: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def failed(self) -> bool:
        return self.status == "FAIL"


def money(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        value = default
    text = _clean_money_text(value)
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal(str(default)).quantize(Decimal("0.01"))


def _clean_money_text(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    text = str(value).strip()
    if not text:
        return "0"
    text = text.replace("\u20b9", "").replace("$", "").replace("INR", "").replace("USD", "")
    text = text.replace(",", "")
    text = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    text = text.replace("O", "0").replace("o", "0")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return match.group(0) if match else "0"
