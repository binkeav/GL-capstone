import unittest

from val_agent.app import _read_interactive_message, is_exit_message


class AppTests(unittest.TestCase):
    def test_exit_words(self):
        for message in ["bye", "done", "exit", "quit", " q ", "goodbye"]:
            self.assertTrue(is_exit_message(message))

    def test_non_exit_message(self):
        self.assertFalse(is_exit_message("validate this invoice"))

    def test_read_interactive_message_collects_multiline_json(self):
        lines = iter(
            [
                "{",
                '  "invoice_number": "INV-001",',
                '  "order_items": []',
                "}",
            ]
        )

        message = _read_interactive_message(lambda prompt: next(lines))

        self.assertIn('"invoice_number": "INV-001"', message)
        self.assertTrue(message.strip().endswith("}"))

    def test_read_interactive_message_keeps_plain_text_single_line(self):
        message = _read_interactive_message(lambda prompt: "hi")

        self.assertEqual(message, "hi")


if __name__ == "__main__":
    unittest.main()
