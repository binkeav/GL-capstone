import unittest

from val_agent.tools.arithmetic import validate_arithmetic


class ArithmeticTests(unittest.TestCase):
    def test_arithmetic_happy_path(self):
        invoice = {
            "subtotal": "1200.00",
            "tax": "216.00",
            "shipping": "0.00",
            "discounts": "0.00",
            "total": "1416.00",
            "order_items": [
                {"qty": "1", "unit_price": "1000.00", "net_amount": "1000.00"},
                {"qty": "2", "unit_price": "100.00", "net_amount": "200.00"},
            ],
        }
        results = validate_arithmetic(invoice)
        self.assertTrue(all(result.status == "PASS" for result in results))

    def test_arithmetic_total_failure(self):
        invoice = {
            "subtotal": "1200.00",
            "tax": "216.00",
            "shipping": "0.00",
            "discounts": "0.00",
            "total": "1400.00",
            "order_items": [{"qty": "1", "unit_price": "1200.00", "net_amount": "1200.00"}],
        }
        results = validate_arithmetic(invoice)
        self.assertTrue(any(result.rule_id == "ARITH_TOTAL_MATCH" and result.status == "FAIL" for result in results))


if __name__ == "__main__":
    unittest.main()
