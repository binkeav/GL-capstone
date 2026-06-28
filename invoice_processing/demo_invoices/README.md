# Demo Invoices

Sample invoices for the unified invoice processing app.

## Files

| Sample | DOCX Upload | Direct JSON | Expected State |
| --- | --- | --- | --- |
| Straight-through | `01_straight_through_acme.docx` | `01_straight_through_acme.json` | `STRAIGHT_THROUGH_PROCESSING` |
| Human review | `02_human_review_mismatch.docx` | `02_human_review_mismatch.json` | `HUMAN_IN_THE_LOOP` |
| Critical rejection | `03_critical_blocked_vendor.docx` | `03_critical_blocked_vendor.json` | `CRITICAL_REJECTION` |

Use the DOCX files with the upload flow. Use the JSON files in the app's Direct JSON Validation section.

## Regenerate

```bash
ocr-agent/.venv/bin/python invoice_processing/demo_invoices/generate_demo_invoices.py
```
