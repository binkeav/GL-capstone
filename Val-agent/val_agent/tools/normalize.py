from __future__ import annotations

from decimal import Decimal
from typing import Any

from val_agent.models import money


def _number(value: Any, default: str = "0") -> str:
    return str(money(value, default))


def _line_no(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_invoice(raw_invoice: dict[str, Any]) -> dict[str, Any]:
    items = []
    for idx, item in enumerate(raw_invoice.get("order_items") or [], start=1):
        qty = _number(item.get("qty"))
        unit_price = _number(item.get("unit_price"))
        net_amount = item.get("net_amount")
        if net_amount is None:
            net_amount = Decimal(qty) * Decimal(unit_price)
        items.append(
            {
                "line_no": _line_no(item.get("line_no"), idx),
                "description": item.get("description"),
                "qty": qty,
                "unit": item.get("unit"),
                "unit_price": unit_price,
                "net_amount": _number(net_amount),
                "tax_rate": item.get("tax_rate"),
                "gross_amount": _number(item.get("gross_amount", net_amount)),
            }
        )

    return {
        "invoice_number": raw_invoice.get("invoice_number"),
        "invoice_date": raw_invoice.get("invoice_date"),
        "due_date": raw_invoice.get("due_date"),
        "po_number": raw_invoice.get("po_number"),
        "payment_terms": raw_invoice.get("payment_terms"),
        "vendor_name": raw_invoice.get("vendor_name"),
        "vendor_tax_id": raw_invoice.get("vendor_tax_id"),
        "customer_name": raw_invoice.get("customer_name"),
        "customer_tax_id": raw_invoice.get("customer_tax_id"),
        "subtotal": _number(raw_invoice.get("subtotal")),
        "tax": _number(raw_invoice.get("tax")),
        "shipping": _number(raw_invoice.get("shipping")),
        "discounts": _number(raw_invoice.get("discounts")),
        "total": _number(raw_invoice.get("total")),
        "currency": raw_invoice.get("currency") or "INR",
        "order_items": items,
    }
