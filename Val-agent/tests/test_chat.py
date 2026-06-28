import os
import unittest
from unittest.mock import patch

from val_agent.tools.chat import classify_intent, render_general_response


class ChatTests(unittest.TestCase):
    def test_classify_intent_uses_llm_for_general_chat(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}):
            with patch("val_agent.tools.chat.classify_intent_with_llm", return_value="general_chat"):
                self.assertEqual(classify_intent("hi"), "general_chat")

    def test_render_general_response_uses_llm(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}):
            with patch("val_agent.tools.chat.answer_general_chat_with_llm", return_value="Paste invoice JSON to begin."):
                self.assertEqual(render_general_response("hi", "general_chat"), "Paste invoice JSON to begin.")

    def test_render_general_response_answers_policy_from_context(self):
        policy = {
            "max_invoice_age_days": 90,
            "supported_currency": "INR",
            "required_tax_id_type": "GSTIN",
        }

        with patch("val_agent.tools.chat.answer_general_chat_with_llm", return_value="LLM policy answer") as llm:
            response = render_general_response("what is the allowed days for submitting the invoice", "general_chat", policy=policy)

        self.assertEqual(response, "LLM policy answer")
        llm.assert_called_once_with(
            "what is the allowed days for submitting the invoice",
            "general_chat",
            None,
            policy,
        )


if __name__ == "__main__":
    unittest.main()
