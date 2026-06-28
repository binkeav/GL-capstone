from __future__ import annotations

from typing import Any


SCALAR_FIELDS = [
    "invoice_number",
    "invoice_date",
    "due_date",
    "po_number",
    "payment_terms",
    "vendor_name",
    "vendor_tax_id",
    "customer_name",
    "customer_tax_id",
    "subtotal",
    "tax",
    "shipping",
    "discounts",
    "total",
    "currency",
]

ORDER_ITEM_FIELDS = [
    "line_no",
    "description",
    "qty",
    "unit",
    "unit_price",
    "net_amount",
    "tax_rate",
    "gross_amount",
]


def adapt_ocr_fields(fields: dict[str, Any] | None) -> dict[str, Any]:
    fields = fields or {}
    payload = {key: fields.get(key) for key in SCALAR_FIELDS}
    payload["shipping"] = fields.get("shipping", 0) or 0
    payload["discounts"] = fields.get("discounts", 0) or 0
    payload["currency"] = fields.get("currency") or "INR"
    payload["order_items"] = _adapt_items(fields.get("order_items"))
    return payload


def build_ocr_evidence(ocr_result: dict[str, Any], source_file: str | None = None) -> dict[str, Any]:
    return {
        "source_file": source_file,
        "status": ocr_result.get("status"),
        "type": ocr_result.get("type"),
        "avg_confidence": ocr_result.get("avg_confidence"),
        "detections": ocr_result.get("detections"),
        "extraction_mode": ocr_result.get("extraction_mode"),
        "extraction_error": (ocr_result.get("fields") or {}).get("extraction_error"),
        "extraction_error_detail": (ocr_result.get("fields") or {}).get("extraction_error_detail"),
        "layout": ocr_result.get("layout"),
        "layout_summary": ocr_result.get("layout_summary"),
        "page_stats": ocr_result.get("page_stats"),
        "quality": ocr_result.get("quality"),
        "raw_text": ocr_result.get("text", ""),
    }


def extraction_quality(invoice_payload: dict[str, Any], ocr_evidence: dict[str, Any]) -> dict[str, Any]:
    required_fields = ["invoice_number", "vendor_name", "invoice_date", "total", "currency"]
    missing = [field for field in required_fields if not invoice_payload.get(field)]
    confidence = ocr_evidence.get("avg_confidence")
    confidence_value = float(confidence) if isinstance(confidence, (int, float)) else None
    low_confidence = confidence_value is not None and confidence_value < 0.45
    review_required = bool(missing) or low_confidence
    reasons = []
    if missing:
        reasons.append("missing_required_fields")
    if low_confidence:
        reasons.append("low_ocr_confidence")
    if ocr_evidence.get("extraction_error"):
        reasons.append("field_extraction_error")

    return {
        "review_required": review_required,
        "missing_required_fields": missing,
        "low_confidence": low_confidence,
        "confidence": confidence_value,
        "reasons": reasons,
    }


def _adapt_items(raw_items: Any) -> list[dict[str, Any]]:
    items = []
    if not isinstance(raw_items, list):
        return items
    for idx, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        adapted = {key: item.get(key) for key in ORDER_ITEM_FIELDS}
        adapted["line_no"] = adapted.get("line_no") or idx
        items.append(adapted)
    return items
