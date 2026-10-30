"""
tests/test_context_builder.py
Unit tests for ContextBuilder, secret redaction, and sensitive path filtering.
"""

import unittest
import json
from ai_architect.context_builder import (
    redact_secrets,
    is_sensitive_path,
    build_review_context,
)


class TestContextBuilder(unittest.TestCase):
    def test_secret_redaction_api_keys(self):
        text = "GROQ_API_KEY='gsk_abcdef1234567890abcdef1234567890' and password = 'SuperSecretPassword123!'"
        redacted = redact_secrets(text)
        self.assertNotIn("gsk_abcdef1234567890abcdef1234567890", redacted)
        self.assertNotIn("SuperSecretPassword123!", redacted)
        self.assertIn("[REDACTED", redacted)

    def test_secret_redaction_private_keys(self):
        rsa = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0\n-----END RSA PRIVATE KEY-----"
        redacted = redact_secrets(rsa)
        self.assertNotIn("MIIEowIBAAKCAQEA0", redacted)
        self.assertIn("[REDACTED_CREDENTIAL]", redacted)

    def test_sensitive_path_detection(self):
        self.assertTrue(is_sensitive_path(".env"))
        self.assertTrue(is_sensitive_path("/path/to/.env"))
        self.assertTrue(is_sensitive_path("config/.env.local"))
        self.assertTrue(is_sensitive_path("id_rsa"))
        self.assertTrue(is_sensitive_path("credentials.json"))
        self.assertFalse(is_sensitive_path("app/auth.py"))
        self.assertFalse(is_sensitive_path("services/users.py"))

    def test_build_review_context_structure(self):
        mock_result = {
            "project_name": "TestProject",
            "summary": {
                "total_files": 5,
                "total_lines": 1200,
                "total_classes": 4,
                "total_functions": 18,
                "avg_complexity": 4.2,
            },
            "modules": [
                {"module_name": "mod_a", "path": "mod_a.py"},
                {"module_name": "mod_b", "path": "mod_b.py"},
            ],
            "issues": [
                {"rule": "circular_dependency", "severity": "high", "message": "mod_a -> mod_b -> mod_a"}
            ],
            "all_functions": [
                {"name": "heavy_fn", "qualified": "mod_a.heavy_fn", "complexity": 15, "lineno": 20, "path": "mod_a.py"}
            ],
            "module_metrics": {
                "mod_a": {"afferent_coupling": 2, "efferent_coupling": 8, "instability": 0.8}
            },
            "layer_map": {"mod_a": "service", "mod_b": "data"},
            "external_libraries": [{"name": "requests", "usage_count": 3}],
        }

        ctx_str = build_review_context(mock_result)
        ctx = json.loads(ctx_str)

        self.assertEqual(ctx["summary"]["project_name"], "TestProject")
        self.assertEqual(ctx["summary"]["total_files"], 5)
        self.assertIn("service", ctx["layers"])
        self.assertEqual(len(ctx["detected_static_issues"]), 1)
        self.assertEqual(len(ctx["top_coupled_modules"]), 1)
        self.assertEqual(len(ctx["most_complex_functions"]), 1)


if __name__ == "__main__":
    unittest.main()
