import json
import os
import unittest
from unittest.mock import patch

from val_agent.llm import DEFAULT_BASE_URL, answer_general_chat_with_llm, classify_intent_with_llm, summarize_validation_with_llm


class _FakeResponse:
    def __init__(self, output_text):
        self.choices = [_FakeChoice(output_text)]


class _FakeChoice:
    def __init__(self, output_text):
        self.message = _FakeMessage(output_text)


class _FakeMessage:
    def __init__(self, output_text):
        self.content = output_text


class _FakeCompletions:
    def __init__(self, output_text):
        self.output_text = output_text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self.output_text)


class _FakeChat:
    def __init__(self, output_text):
        self.completions = _FakeCompletions(output_text)


class _FakeClient:
    def __init__(self, output_text, **kwargs):
        self.chat = _FakeChat(output_text)


class LlmTests(unittest.TestCase):
    def test_default_api_base(self):
        self.assertEqual(DEFAULT_BASE_URL, "https://aibe.mygreatlearning.com/openai/v1")

    def test_classify_intent_requires_llm_configuration(self):
        with patch.dict(os.environ, {"INVOICE_PROCESSING_SKIP_DOTENV": "1"}, clear=True):
            with self.assertRaises(RuntimeError):
                classify_intent_with_llm("please validate this invoice")

    def test_summarize_validation_requires_llm_configuration(self):
        with patch.dict(os.environ, {"INVOICE_PROCESSING_SKIP_DOTENV": "1"}, clear=True):
            with self.assertRaises(RuntimeError):
                summarize_validation_with_llm("HUMAN_IN_THE_LOOP", [], "inv_1", "Acme Supplies India")

    def test_answer_general_chat_requires_llm_configuration(self):
        with patch.dict(os.environ, {"INVOICE_PROCESSING_SKIP_DOTENV": "1"}, clear=True):
            with self.assertRaises(RuntimeError):
                answer_general_chat_with_llm("hi", "general_chat")

    def test_classify_intent_with_llm(self):
        fake_client = _FakeClient(json.dumps({"intent": "validate_invoice"}))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}):
            with patch("val_agent.llm.OpenAI", return_value=fake_client):
                self.assertEqual(
                    classify_intent_with_llm(
                        "please validate this invoice",
                        [{"sender": "USER", "message": "we are reviewing INV-001"}],
                    ),
                    "validate_invoice",
                )
        prompt = json.loads(fake_client.chat.completions.calls[0]["messages"][1]["content"])
        self.assertEqual(prompt["recent_chat_context"][0]["message"], "we are reviewing INV-001")

    def test_summarize_validation_with_llm(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}):
            with patch("val_agent.llm.OpenAI", return_value=_FakeClient("Summary from LLM")):
                summary = summarize_validation_with_llm(
                    "HUMAN_IN_THE_LOOP",
                    [{"rule_id": "ARITH_TOTAL_MATCH", "status": "FAIL", "error_code": "ERR_GRAND_TOTAL_MISMATCH"}],
                    "inv_1",
                    "Acme Supplies India",
                    "INR",
                    [{"sender": "ASSISTANT", "message": "Previous validation needed review."}],
                )
                self.assertEqual(summary, "Summary from LLM")

    def test_answer_general_chat_with_llm(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}):
            with patch("val_agent.llm.OpenAI", return_value=_FakeClient("Hi, I can help validate invoices.")):
                answer = answer_general_chat_with_llm("hi", "general_chat")
                self.assertEqual(answer, "Hi, I can help validate invoices.")

    def test_summary_prompt_requests_plain_language_and_next_steps(self):
        fake_client = _FakeClient("Summary from LLM")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}):
            with patch("val_agent.llm.OpenAI", return_value=fake_client):
                summarize_validation_with_llm(
                    "HUMAN_IN_THE_LOOP",
                    [{"rule_id": "ARITH_TOTAL_MATCH", "status": "FAIL", "error_code": "ERR_GRAND_TOTAL_MISMATCH"}],
                    "inv_1",
                    "Acme Supplies India",
                    "INR",
                    [{"sender": "USER", "message": "what should I do next?"}],
                )
        prompt = json.loads(fake_client.chat.completions.calls[0]["messages"][1]["content"])
        self.assertIn("Use normal language", " ".join(prompt["constraints"]))
        self.assertIn("Next steps", " ".join(prompt["constraints"]))
        self.assertEqual(prompt["currency"], "INR")
        self.assertEqual(prompt["recent_chat_context"][0]["message"], "what should I do next?")
        self.assertTrue(prompt["recommended_next_steps"])

    def test_general_chat_prompt_receives_context(self):
        fake_client = _FakeClient("Use the previous invoice context.")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}):
            with patch("val_agent.llm.OpenAI", return_value=fake_client):
                answer_general_chat_with_llm(
                    "what next?",
                    "general_chat",
                    [{"sender": "ASSISTANT", "message": "The invoice needed review."}],
                )
        prompt = json.loads(fake_client.chat.completions.calls[0]["messages"][1]["content"])
        self.assertEqual(prompt["recent_chat_context"][0]["message"], "The invoice needed review.")
        prompt_text = json.dumps(prompt)
        self.assertNotIn("invoice JSON", prompt_text)
        self.assertNotIn("uv run", prompt_text)
        self.assertIn("upload one", prompt_text)

    def test_policy_prompt_asks_for_plain_business_rules(self):
        fake_client = _FakeClient("Plain policy answer")
        policy = {
            "policy_id": "policy_in_gst",
            "jurisdiction": "IN",
            "required_tax_id_type": "GSTIN",
            "max_invoice_age_days": 90,
            "supported_currency": "INR",
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}):
            with patch("val_agent.llm.OpenAI", return_value=fake_client):
                answer_general_chat_with_llm("give the policies", "general_chat", policy=policy)

        prompt = json.loads(fake_client.chat.completions.calls[0]["messages"][1]["content"])
        constraints = " ".join(prompt["constraints"])
        self.assertIn("plain business rules", constraints)
        self.assertIn("do not show raw field names", constraints)
        self.assertIn("policy_id", constraints)


if __name__ == "__main__":
    unittest.main()
