from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from val_agent.models import RuleResult


ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT.parent / "data" / "val_agent.sqlite3"
MIGRATION_PATH = ROOT / "migrations" / "001_init.sql"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(MIGRATION_PATH.read_text())
    seed_reference_data(conn)
    conn.commit()


def seed_reference_data(conn: sqlite3.Connection) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT OR IGNORE INTO compliance_policies
        (policy_id, jurisdiction, required_tax_id_type, max_invoice_age_days, supported_currency, active, created_at, updated_at)
        VALUES ('policy_in_gst', 'IN', 'GSTIN', 90, 'INR', 1, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO vendors
        (vendor_id, vendor_name, vendor_tax_id, status, default_currency, jurisdiction, created_at, updated_at)
        VALUES
        ('vendor_acme', 'Acme Supplies India', '29ABCDE1234F1Z5', 'ACTIVE', 'INR', 'IN', ?, ?),
        ('vendor_blocked', 'Blocked Vendor India', '29BBBBB1234B1Z5', 'BLOCKED', 'INR', 'IN', ?, ?)
        """,
        (now, now, now, now),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO purchase_orders
        (po_number, vendor_id, status, currency, created_at, updated_at)
        VALUES ('PO-1001', 'vendor_acme', 'OPEN', 'INR', ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO purchase_order_items
        (po_item_id, po_number, line_no, description, ordered_qty, unit, unit_price)
        VALUES
        ('po_item_1001_1', 'PO-1001', 1, 'Laptop Stand', 10, 'pcs', 1000.00),
        ('po_item_1001_2', 'PO-1001', 2, 'USB Cable', 20, 'pcs', 100.00)
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO goods_receipts
        (receipt_id, po_number, po_item_id, received_qty, received_date, status)
        VALUES
        ('grn_1001_1', 'PO-1001', 'po_item_1001_1', 10, '2026-06-01', 'RECEIVED'),
        ('grn_1001_2', 'PO-1001', 'po_item_1001_2', 20, '2026-06-01', 'RECEIVED')
        """
    )


def upsert_invoice(conn: sqlite3.Connection, raw: dict[str, Any], normalized: dict[str, Any]) -> str:
    now = utc_now()
    invoice_id = normalized.get("invoice_id") or _logical_invoice_id(conn, normalized) or new_id("inv")
    _remove_stale_logical_duplicates(conn, invoice_id, normalized)
    normalized["invoice_id"] = invoice_id
    conn.execute(
        """
        INSERT OR REPLACE INTO invoices
        (invoice_id, invoice_number, invoice_date, due_date, po_number, payment_terms,
         vendor_name, vendor_tax_id, customer_name, customer_tax_id, subtotal, tax,
         shipping, discounts, total, currency, source_payload_json, normalized_payload_json,
         validation_state, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(
          (SELECT validation_state FROM invoices WHERE invoice_id = ?), 'PENDING'
        ), COALESCE((SELECT created_at FROM invoices WHERE invoice_id = ?), ?), ?)
        """,
        (
            invoice_id,
            normalized.get("invoice_number"),
            normalized.get("invoice_date"),
            normalized.get("due_date"),
            normalized.get("po_number"),
            normalized.get("payment_terms"),
            normalized.get("vendor_name"),
            normalized.get("vendor_tax_id"),
            normalized.get("customer_name"),
            normalized.get("customer_tax_id"),
            normalized.get("subtotal"),
            normalized.get("tax"),
            normalized.get("shipping", 0),
            normalized.get("discounts", 0),
            normalized.get("total"),
            normalized.get("currency"),
            json.dumps(raw),
            json.dumps(normalized),
            invoice_id,
            invoice_id,
            now,
            now,
        ),
    )
    conn.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,))
    for item in normalized.get("order_items", []):
        conn.execute(
            """
            INSERT INTO invoice_items
            (invoice_item_id, invoice_id, line_no, description, qty, unit, unit_price, net_amount, tax_rate, gross_amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("item"),
                invoice_id,
                item.get("line_no"),
                item.get("description"),
                item.get("qty"),
                item.get("unit"),
                item.get("unit_price"),
                item.get("net_amount"),
                item.get("tax_rate"),
                item.get("gross_amount"),
            ),
        )
    conn.commit()
    return invoice_id


def _logical_invoice_id(conn: sqlite3.Connection, normalized: dict[str, Any]) -> str | None:
    vendor_name = normalized.get("vendor_name")
    invoice_number = normalized.get("invoice_number")
    if not vendor_name or not invoice_number:
        return None
    row = conn.execute(
        """
        SELECT invoice_id FROM invoices
        WHERE vendor_name = ? AND invoice_number = ?
        ORDER BY created_at
        LIMIT 1
        """,
        (vendor_name, invoice_number),
    ).fetchone()
    return row["invoice_id"] if row else None


def _remove_stale_logical_duplicates(conn: sqlite3.Connection, keep_invoice_id: str, normalized: dict[str, Any]) -> None:
    vendor_name = normalized.get("vendor_name")
    invoice_number = normalized.get("invoice_number")
    if not vendor_name or not invoice_number:
        return
    stale_rows = conn.execute(
        """
        SELECT invoice_id FROM invoices
        WHERE vendor_name = ? AND invoice_number = ? AND invoice_id <> ?
        """,
        (vendor_name, invoice_number, keep_invoice_id),
    ).fetchall()
    stale_ids = [row["invoice_id"] for row in stale_rows]
    for stale_id in stale_ids:
        conn.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (stale_id,))
        conn.execute("DELETE FROM rule_results WHERE invoice_id = ?", (stale_id,))
        conn.execute("DELETE FROM agent_traces WHERE invoice_id = ?", (stale_id,))
        conn.execute("DELETE FROM erp_submissions WHERE invoice_id = ?", (stale_id,))
        conn.execute("DELETE FROM override_events WHERE invoice_id = ?", (stale_id,))
        conn.execute("UPDATE conversations SET invoice_id = ? WHERE invoice_id = ?", (keep_invoice_id, stale_id))
        conn.execute("UPDATE chat_messages SET invoice_id = ? WHERE invoice_id = ?", (keep_invoice_id, stale_id))
        conn.execute("DELETE FROM invoices WHERE invoice_id = ?", (stale_id,))


def save_rule_results(conn: sqlite3.Connection, invoice_id: str, results: list[RuleResult]) -> None:
    conn.execute("DELETE FROM rule_results WHERE invoice_id = ?", (invoice_id,))
    now = utc_now()
    for result in results:
        conn.execute(
            """
            INSERT INTO rule_results
            (rule_result_id, invoice_id, rule_id, agent_name, status, severity, expected_value, actual_value, error_code, evidence_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("rule"),
                invoice_id,
                result.rule_id,
                result.agent_name,
                result.status,
                result.severity,
                result.expected_value,
                result.actual_value,
                result.error_code,
                json.dumps(result.evidence),
                now,
            ),
        )
    conn.commit()


def update_invoice_state(conn: sqlite3.Connection, invoice_id: str, state: str) -> None:
    conn.execute(
        "UPDATE invoices SET validation_state = ?, updated_at = ? WHERE invoice_id = ?",
        (state, utc_now(), invoice_id),
    )
    conn.commit()


def get_invoice_case(conn: sqlite3.Connection, invoice_id: str) -> dict[str, Any] | None:
    invoice = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
    if not invoice:
        return None
    rules = conn.execute(
        "SELECT * FROM rule_results WHERE invoice_id = ? ORDER BY created_at, rule_id",
        (invoice_id,),
    ).fetchall()
    return {
        "invoice": dict(invoice),
        "rule_results": [dict(row) for row in rules],
    }


def create_conversation(conn: sqlite3.Connection, user_id: str, user_role: str, invoice_id: str | None = None) -> str:
    conversation_id = new_id("conv")
    now = utc_now()
    conn.execute(
        """
        INSERT INTO conversations
        (conversation_id, invoice_id, user_id, user_role, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'OPEN', ?, ?)
        """,
        (conversation_id, invoice_id, user_id, user_role, now, now),
    )
    conn.commit()
    return conversation_id


def save_chat_message(
    conn: sqlite3.Connection,
    conversation_id: str,
    sender: str,
    text: str,
    invoice_id: str | None = None,
    intent: str | None = None,
    message_json: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO chat_messages
        (message_id, conversation_id, invoice_id, sender, message_text, message_json, intent, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("msg"),
            conversation_id,
            invoice_id,
            sender,
            text,
            json.dumps(message_json) if message_json else None,
            intent,
            utc_now(),
        ),
    )
    conn.commit()


def get_recent_chat_context(
    conn: sqlite3.Connection,
    conversation_id: str,
    limit: int = 12,
    max_chars: int = 4000,
) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT sender, message_text, intent, created_at
        FROM chat_messages
        WHERE conversation_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (conversation_id, limit),
    ).fetchall()
    messages = [
        {
            "sender": row["sender"],
            "message": row["message_text"] or "",
            "intent": row["intent"] or "",
            "created_at": row["created_at"],
        }
        for row in reversed(rows)
    ]
    trimmed: list[dict[str, str]] = []
    total_chars = 0
    for message in reversed(messages):
        message_len = len(message["message"])
        if trimmed and total_chars + message_len > max_chars:
            break
        trimmed.append(message)
        total_chars += message_len
    return list(reversed(trimmed))
