import unittest
from decimal import Decimal

from val_agent.models import money
from val_agent.tools.normalize import normalize_invoice


class MoneyParsingTests(unittest.TestCase):
    def test_money_handles_ocr_amount_noise(self):
        self.assertEqual(money("INR 73,79,000.00}"), Decimal("7379000.00"))
        self.assertEqual(money("{1,06,400.00 ."), Decimal("106400.00"))
        self.assertEqual(money("7 447 220.00"), Decimal("7447220.00"))
        self.assertEqual(money("$1,234.50"), Decimal("1234.50"))

    def test_money_defaults_malformed_values_without_crashing(self):
        self.assertEqual(money("not an amount"), Decimal("0.00"))
        self.assertEqual(money(""), Decimal("0.00"))

    def test_normalize_invoice_handles_bad_line_item_amount(self):
        invoice = normalize_invoice(
            {
                "subtotal": "1,000.00",
                "tax": "180.00",
                "total": "1,180.00",
                "order_items": [
                    {
                        "description": "Service",
                        "qty": "1",
                        "unit_price": "INR 1,000.00)",
                        "net_amount": "{1,000.00",
                        "gross_amount": "1,180.00}",
                    }
                ],
            }
        )
        self.assertEqual(invoice["order_items"][0]["unit_price"], "1000.00")
        self.assertEqual(invoice["order_items"][0]["net_amount"], "1000.00")
        self.assertEqual(invoice["order_items"][0]["gross_amount"], "1180.00")


if __name__ == "__main__":
    unittest.main()
