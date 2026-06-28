from __future__ import annotations

import sqlite3
from decimal import Decimal

from val_agent.models import RuleResult, money


AGENT = "Transaction Match Agent"


def lookup_po(conn: sqlite3.Connection, po_number: str | None) -> dict | None:
    if not po_number:
        return None
    po = conn.execute("SELECT * FROM purchase_orders WHERE po_number = ?", (po_number,)).fetchone()
    if not po:
        return None
    items = conn.execute("SELECT * FROM purchase_order_items WHERE po_number = ?", (po_number,)).fetchall()
    return {"po": dict(po), "items": [dict(row) for row in items]}


def validate_transaction_match(conn: sqlite3.Connection, invoice: dict) -> list[RuleResult]:
    results: list[RuleResult] = []
    po_data = lookup_po(conn, invoice.get("po_number"))
    if not po_data:
        results.append(
            RuleResult(
                "MATCH_PO_EXISTS",
                AGENT,
                "FAIL",
                "ERROR",
                expected_value="existing PO",
                actual_value=str(invoice.get("po_number")),
                error_code="ERR_MISSING_PO",
            )
        )
    else:
        results.append(RuleResult("MATCH_PO_EXISTS", AGENT, "PASS", "ERROR", "existing PO", invoice.get("po_number")))
        results.extend(_validate_receipts(conn, invoice, po_data))

    results.extend(search_duplicates(conn, invoice))
    return results


def _validate_receipts(conn: sqlite3.Connection, invoice: dict, po_data: dict) -> list[RuleResult]:
    po_number = po_data["po"]["po_number"]
    receipt_rows = conn.execute(
        """
        SELECT poi.line_no, COALESCE(SUM(gr.received_qty), 0) AS received_qty
        FROM purchase_order_items poi
        LEFT JOIN goods_receipts gr ON gr.po_item_id = poi.po_item_id AND gr.status IN ('RECEIVED', 'PARTIAL')
        WHERE poi.po_number = ?
        GROUP BY poi.line_no
        """,
        (po_number,),
    ).fetchall()
    received_by_line = {row["line_no"]: money(row["received_qty"]) for row in receipt_rows}
    failures = []
    for item in invoice.get("order_items", []):
        line_no = item.get("line_no")
        invoiced_qty = money(item.get("qty"))
        received_qty = received_by_line.get(line_no, Decimal("0.00"))
        if invoiced_qty > received_qty:
            failures.append({"line_no": line_no, "invoiced_qty": str(invoiced_qty), "received_qty": str(received_qty)})

    if failures:
        return [
            RuleResult(
                "MATCH_GOODS_RECEIVED",
                AGENT,
                "FAIL",
                "ERROR",
                expected_value="received quantity >= invoiced quantity",
                actual_value="partial receipt",
                error_code="ERR_PARTIAL_RECEIPT",
                evidence={"failures": failures},
            )
        ]
    return [RuleResult("MATCH_GOODS_RECEIVED", AGENT, "PASS", "ERROR", "received quantity >= invoiced quantity", "matched")]


def search_duplicates(conn: sqlite3.Connection, invoice: dict) -> list[RuleResult]:
    invoice_id = invoice.get("invoice_id")
    vendor_name = invoice.get("vendor_name")
    invoice_number = invoice.get("invoice_number")
    total = invoice.get("total")
    invoice_date = invoice.get("invoice_date")

    exact = conn.execute(
        """
        SELECT invoice_id FROM invoices
        WHERE vendor_name = ?
          AND invoice_number = ?
          AND invoice_id <> ?
          AND normalized_payload_json IS NOT NULL
        """,
        (vendor_name, invoice_number, invoice_id),
    ).fetchall()
    if exact:
        exact_result = RuleResult(
            "DUP_VENDOR_INVOICE_NUMBER",
            AGENT,
            "FAIL",
            "FATAL",
            "no active duplicate",
            f"{vendor_name}+{invoice_number}",
            "ERR_CONFIRMED_DUPLICATE",
        )
    else:
        exact_result = RuleResult("DUP_VENDOR_INVOICE_NUMBER", AGENT, "PASS", "FATAL", "no active duplicate", "none")

    suspicious = conn.execute(
        """
        SELECT invoice_id FROM invoices
        WHERE total = ?
          AND invoice_date = ?
          AND invoice_id <> ?
          AND normalized_payload_json IS NOT NULL
        """,
        (total, invoice_date, invoice_id),
    ).fetchall()
    if suspicious:
        suspect_result = RuleResult(
            "DUP_TOTAL_DATE",
            AGENT,
            "FAIL",
            "ERROR",
            "no suspicious duplicate",
            f"{total}+{invoice_date}",
            "ERR_DUPLICATE_SUSPICION",
        )
    else:
        suspect_result = RuleResult("DUP_TOTAL_DATE", AGENT, "PASS", "ERROR", "no suspicious duplicate", "none")
    return [exact_result, suspect_result]
