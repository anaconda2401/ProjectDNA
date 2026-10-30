"""
tests/test_groq_client.py
Unit tests for isolated GroqClient with mocked API responses and error scenarios.
Zero live network dependencies during unit test execution.
"""

import unittest
from unittest.mock import MagicMock, patch
import json

from ai_architect.groq_client import (
    GroqClient,
    GroqAuthError,
    GroqRateLimitError,
    GroqTimeoutError,
    GroqConnectionError,
    GroqResponseError,
    GroqProviderError,
)

try:
    from groq import (
        AuthenticationError,
        RateLimitError,
        APITimeoutError,
        APIConnectionError,
        InternalServerError,
        BadRequestError,
    )
except ImportError:
    pass


class TestGroqClient(unittest.TestCase):
    def setUp(self):
        self.client = GroqClient(
            api_key="gsk_testdummyapikey1234567890abcdef",
            model="openai/gpt-oss-20b",
            timeout=5.0,
            enabled=True,
        )

    def test_client_availability(self):
        self.assertTrue(self.client.is_available())

        disabled_client = GroqClient(api_key="gsk_key", enabled=False)
        self.assertFalse(disabled_client.is_available())

        no_key_client = GroqClient(api_key="", enabled=True)
        self.assertFalse(no_key_client.is_available())

    @patch("ai_architect.groq_client.Groq")
    def test_valid_json_completion(self, mock_groq_class):
        mock_instance = MagicMock()
        mock_groq_class.return_value = mock_instance

        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "review_summary": "Architecture looks solid.",
            "findings": []
        })
        mock_response = MagicMock(choices=[mock_choice])
        mock_instance.chat.completions.create.return_value = mock_response

        # Reinitialize internal client
        self.client._client = mock_instance
        res = self.client.chat_completion_json("System prompt", "User prompt")

        self.assertIn("review_summary", res)
        self.assertEqual(res["review_summary"], "Architecture looks solid.")

    @patch("ai_architect.groq_client.Groq")
    def test_markdown_wrapped_json_parsing(self, mock_groq_class):
        mock_instance = MagicMock()
        mock_groq_class.return_value = mock_instance

        mock_choice = MagicMock()
        mock_choice.message.content = "```json\n{\n  \"status\": \"parsed_successfully\"\n}\n```"
        mock_instance.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

        self.client._client = mock_instance
        res = self.client.chat_completion_json("System prompt", "User prompt")
        self.assertEqual(res.get("status"), "parsed_successfully")

    @patch("ai_architect.groq_client.Groq")
    def test_malformed_json_raises_response_error(self, mock_groq_class):
        mock_instance = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "This is not valid JSON at all!"
        mock_instance.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

        self.client._client = mock_instance
        with self.assertRaises(GroqResponseError):
            self.client.chat_completion_json("Sys", "User")

    @patch("ai_architect.groq_client.Groq")
    def test_auth_error_raises_groq_auth_error_without_retrying(self, mock_groq_class):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = AuthenticationError(
            message="Invalid API Key: gsk_testdummyapikey1234567890abcdef",
            response=MagicMock(status_code=401),
            body=None,
        )

        self.client._client = mock_instance
        with self.assertRaises(GroqAuthError) as ctx:
            self.client.chat_completion_json("Sys", "User")

        # Must only call once (no repeated retries on permanent auth error)
        self.assertEqual(mock_instance.chat.completions.create.call_count, 1)
        # Check that API key was redacted in error message
        self.assertNotIn("gsk_testdummyapikey1234567890abcdef", str(ctx.exception))
        self.assertIn("[REDACTED_API_KEY]", str(ctx.exception))

    @patch("ai_architect.groq_client.Groq")
    def test_timeout_error_handling(self, mock_groq_class):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = APITimeoutError(
            request=MagicMock()
        )

        self.client._client = mock_instance
        self.client.max_retries = 1
        with self.assertRaises(GroqTimeoutError):
            self.client.chat_completion_json("Sys", "User")

    @patch("ai_architect.groq_client.Groq")
    def test_rate_limit_error_handling(self, mock_groq_class):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = RateLimitError(
            message="Rate limit reached for model",
            response=MagicMock(status_code=429),
            body=None,
        )

        self.client._client = mock_instance
        self.client.max_retries = 1
        with self.assertRaises(GroqRateLimitError):
            self.client.chat_completion_json("Sys", "User")

    @patch("ai_architect.groq_client.Groq")
    def test_connection_error_handling(self, mock_groq_class):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = APIConnectionError(
            request=MagicMock()
        )

        self.client._client = mock_instance
        self.client.max_retries = 1
        with self.assertRaises(GroqConnectionError):
            self.client.chat_completion_json("Sys", "User")


if __name__ == "__main__":
    unittest.main()
