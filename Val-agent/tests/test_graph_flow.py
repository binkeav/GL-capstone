import json
import unittest
from unittest.mock import patch

from val_agent.db import connect, init_db
from val_agent.graph import run_validation_graph


class GraphFlowTests(unittest.TestCase):
    def test_graph_happy_path(self):
        conn = connect(":memory:")
        init_db(conn)
        invoice = {
            "invoice_number": "INV-001",
            "invoice_date": "2026-06-15",
            "po_number": "PO-1001",
            "vendor_name": "Acme Supplies India",
            "vendor_tax_id": "29ABCDE1234F1Z5",
            "subtotal": 1200,
            "tax": 216,
            "shipping": 0,
            "discounts": 0,
            "total": 1416,
            "currency": "INR",
            "order_items": [
                {
                    "line_no": 1,
                    "description": "Laptop Stand",
                    "qty": 1,
                    "unit": "pcs",
                    "unit_price": 1000,
                    "net_amount": 1000,
                },
                {"line_no": 2, "description": "USB Cable", "qty": 2, "unit": "pcs", "unit_price": 100, "net_amount": 200},
            ],
        }
        with patch("val_agent.tools.chat.classify_intent_with_llm", return_value="validate_invoice"):
            with patch("val_agent.tools.chat.summarize_validation_with_llm", return_value="State: STRAIGHT_THROUGH_PROCESSING"):
                progress_events = []
                state = run_validation_graph(
                    conn,
                    {
                        "conversation_id": "conv_test",
                        "user_id": "user",
                        "user_role": "AP_REVIEWER",
                        "user_message": json.dumps(invoice),
                        "rule_results": [],
                        "errors": [],
                        "final_state": "PENDING",
                    },
                    progress=progress_events.append,
                )
        self.assertEqual(state["final_state"], "STRAIGHT_THROUGH_PROCESSING")
        self.assertIn("Chat Agent: understanding your message.", progress_events)
        self.assertIn("Supervisor Validation Agent: validation checks complete.", progress_events)


if __name__ == "__main__":
    unittest.main()
