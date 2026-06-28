from __future__ import annotations

import sqlite3
from datetime import date, datetime

from val_agent.models import RuleResult


AGENT = "Compliance & Risk Agent"


def lookup_vendor(conn: sqlite3.Connection, vendor_name: str | None, vendor_tax_id: str | None) -> dict | None:
    if vendor_tax_id:
        row = conn.execute("SELECT * FROM vendors WHERE vendor_tax_id = ?", (vendor_tax_id,)).fetchone()
        if row:
            return dict(row)
    if vendor_name:
        row = conn.execute("SELECT * FROM vendors WHERE vendor_name = ?", (vendor_name,)).fetchone()
        if row:
            return dict(row)
    return None


def lookup_compliance_policy(conn: sqlite3.Connection, jurisdiction: str = "IN") -> dict:
    row = conn.execute(
        "SELECT * FROM compliance_policies WHERE jurisdiction = ? AND active = 1 LIMIT 1",
        (jurisdiction,),
    ).fetchone()
    return dict(row) if row else {"max_invoice_age_days": 90, "supported_currency": "INR"}


def validate_compliance(conn: sqlite3.Connection, invoice: dict) -> list[RuleResult]:
    policy = lookup_compliance_policy(conn)
    vendor = lookup_vendor(conn, invoice.get("vendor_name"), invoice.get("vendor_tax_id"))
    return [
        _date_window(invoice, policy),
        _gstin_present(invoice),
        _gstin_match(invoice, vendor),
        _currency(invoice, policy),
        _vendor_active(vendor),
    ]


def _date_window(invoice: dict, policy: dict) -> RuleResult:
    raw_date = invoice.get("invoice_date")
    try:
        invoice_date = datetime.fromisoformat(raw_date).date()
        age_days = (date.today() - invoice_date).days
        passed = 0 <= age_days <= int(policy.get("max_invoice_age_days", 90))
        actual = f"{age_days} days old"
    except (TypeError, ValueError):
        passed = False
        actual = str(raw_date)
    return RuleResult(
        "COMP_DATE_WINDOW",
        AGENT,
        "PASS" if passed else "FAIL",
        "ERROR",
        "invoice date within last 90 days and not future dated",
        actual,
        None if passed else "ERR_INVALID_INVOICE_DATE",
    )


def _gstin_present(invoice: dict) -> RuleResult:
    gstin = invoice.get("vendor_tax_id")
    passed = bool(gstin)
    return RuleResult(
        "COMP_GSTIN_PRESENT",
        AGENT,
        "PASS" if passed else "FAIL",
        "ERROR",
        "vendor GSTIN present",
        str(gstin),
        None if passed else "ERR_MISSING_GSTIN",
    )


def _gstin_match(invoice: dict, vendor: dict | None) -> RuleResult:
    expected = vendor.get("vendor_tax_id") if vendor else None
    actual = invoice.get("vendor_tax_id")
    passed = bool(vendor) and expected == actual
    return RuleResult(
        "COMP_GSTIN_MATCH",
        AGENT,
        "PASS" if passed else "FAIL",
        "ERROR",
        str(expected),
        str(actual),
        None if passed else "ERR_GSTIN_MISMATCH",
    )


def _currency(invoice: dict, policy: dict) -> RuleResult:
    expected = policy.get("supported_currency", "INR")
    actual = invoice.get("currency")
    passed = actual == expected
    return RuleResult(
        "COMP_CURRENCY_INR",
        AGENT,
        "PASS" if passed else "FAIL",
        "ERROR",
        expected,
        str(actual),
        None if passed else "ERR_UNSUPPORTED_CURRENCY",
    )


def _vendor_active(vendor: dict | None) -> RuleResult:
    status = vendor.get("status") if vendor else None
    passed = status == "ACTIVE"
    severity = "FATAL" if status == "BLOCKED" or vendor is None else "ERROR"
    return RuleResult(
        "RISK_VENDOR_ACTIVE",
        AGENT,
        "PASS" if passed else "FAIL",
        severity,
        "ACTIVE",
        str(status),
        None if passed else "ERR_BLOCKED_VENDOR",
    )

