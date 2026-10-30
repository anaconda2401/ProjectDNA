"""
tests/test_ai_architect_service.py
Unit tests for AIArchitectService orchestration, fallback handling, deduplication,
and source location validation.
"""

import unittest
from unittest.mock import MagicMock
from ai_architect.service import AIArchitectService
from ai_architect.groq_client import (
    GroqClient,
    GroqAuthError,
    GroqTimeoutError,
    GroqRateLimitError,
)
from ai_architect.local_reviewer import LocalReviewer
from ai_architect.schemas import ReviewResponse, ChangePlanResponse, AskQuestionResponse


class TestAIArchitectService(unittest.TestCase):
    def setUp(self):
        self.mock_result = {
            "project_name": "AuthService",
            "summary": {
                "total_files": 3,
                "total_lines": 500,
                "total_classes": 2,
                "total_functions": 8,
                "avg_complexity": 5.0,
            },
            "modules": [
                {
                    "module_name": "auth.service",
                    "path": "auth/service.py",
                    "code_lines": 280,
                    "functions": [
                        {"name": "authenticate", "complexity": 11, "lineno": 15, "end_lineno": 45, "args": ["u", "p"], "calls": ["find_user"]}
                    ],
                    "classes": [],
                    "imports": [{"module": "auth.users", "names": ["find_user"], "line": 2}],
                },
                {
                    "module_name": "auth.users",
                    "path": "auth/users.py",
                    "code_lines": 150,
                    "functions": [
                        {"name": "find_user", "complexity": 3, "lineno": 8, "end_lineno": 25, "args": ["u"], "calls": []}
                    ],
                    "classes": [],
                    "imports": [{"module": "auth.service", "names": ["verify_token"], "line": 3}],
                },
            ],
            "issues": [
                {
                    "rule": "circular_dependency",
                    "severity": "high",
                    "message": "auth.service -> auth.users -> auth.service",
                }
            ],
            "module_metrics": {
                "auth.service": {"efferent_coupling": 8, "afferent_coupling": 2, "instability": 0.8},
                "auth.users": {"efferent_coupling": 3, "afferent_coupling": 8, "instability": 0.27},
            },
            "all_functions": [
                {
                    "name": "authenticate",
                    "qualified": "auth.service.authenticate",
                    "module": "auth.service",
                    "path": "auth/service.py",
                    "lineno": 15,
                    "end_lineno": 45,
                    "complexity": 11,
                    "args": ["u", "p"],
                    "calls": ["find_user"],
                }
            ],
            "layer_map": {"auth.service": "service", "auth.users": "data"},
            "impact_summaries": {
                "auth.service": {
                    "direct_dependents": ["auth.users"],
                    "transitive_dependents": ["auth.users"],
                    "affected_tests": ["tests/test_auth.py"],
                }
            },
            "architecture_risks": {"risk_backlog": []},
        }

    def test_local_mode_executes_strictly_locally(self):
        mock_groq = MagicMock(spec=GroqClient)
        service = AIArchitectService(groq_client=mock_groq)

        resp = service.run_review(self.mock_result, mode="local")
        self.assertEqual(resp.provider, "local")
        self.assertEqual(resp.status, "completed")
        mock_groq.chat_completion_json.assert_not_called()

    def test_auto_mode_falls_back_when_groq_unavailable(self):
        mock_groq = MagicMock(spec=GroqClient)
        mock_groq.is_available.return_value = False
        service = AIArchitectService(groq_client=mock_groq)

        resp = service.run_review(self.mock_result, mode="auto")
        self.assertEqual(resp.status, "fallback_local")
        self.assertIn("Groq review unavailable", resp.status_message)
        self.assertGreater(len(resp.findings), 0)

    def test_auto_mode_falls_back_on_groq_auth_error(self):
        mock_groq = MagicMock(spec=GroqClient)
        mock_groq.is_available.return_value = True
        mock_groq.chat_completion_json.side_effect = GroqAuthError("Invalid credentials")
        service = AIArchitectService(groq_client=mock_groq)

        resp = service.run_review(self.mock_result, mode="auto")
        self.assertEqual(resp.status, "fallback_local")
        self.assertIn("Groq review unavailable", resp.status_message)
        self.assertGreater(len(resp.findings), 0)

    def test_auto_mode_falls_back_on_groq_timeout(self):
        mock_groq = MagicMock(spec=GroqClient)
        mock_groq.is_available.return_value = True
        mock_groq.chat_completion_json.side_effect = GroqTimeoutError("Timed out after 30s")
        service = AIArchitectService(groq_client=mock_groq)

        resp = service.run_review(self.mock_result, mode="auto")
        self.assertEqual(resp.status, "fallback_local")
        self.assertIn("Timed out", resp.status_message)

    def test_successful_groq_review_merged_and_deduplicated(self):
        mock_groq = MagicMock(spec=GroqClient)
        mock_groq.is_available.return_value = True
        mock_groq.model = "openai/gpt-oss-20b"
        mock_groq.chat_completion_json.return_value = {
            "review_summary": "AI Review: auth and user services require layer inversion.",
            "architecture_overview": "Service layer depends cyclically on data repository.",
            "findings": [
                {
                    "id": "AI-ARCH-001",
                    "title": "Circular Dependency between auth.service and auth.users",
                    "severity": "critical",
                    "category": "architecture",
                    "evidence": ["Mutual imports observed in auth/service.py and auth/users.py"],
                    "explanation": "Tightly coupled domain prevents independent scaling.",
                    "recommendation": "Introduce a TokenRepository protocol.",
                    "tradeoffs": ["Adds one new protocol file."],
                    "validation_steps": ["Run pytest tests/test_auth.py"],
                    "source_locations": [{"file": "auth/service.py", "line": 15}],
                },
                {
                    "id": "AI-ARCH-002",
                    "title": "Missing Error Handling in authenticate function",
                    "severity": "medium",
                    "category": "reliability",
                    "evidence": ["authenticate() directly accesses user dict without try/except"],
                    "explanation": "May raise unhandled KeyError on invalid credentials.",
                    "recommendation": "Wrap with AuthenticationError exception handler.",
                    "tradeoffs": [],
                    "validation_steps": ["Test with non-existent user id"],
                    "source_locations": [{"file": "auth/service.py", "line": 15}],
                },
                {
                    "id": "AI-ARCH-003",
                    "title": "Fabricated finding in non-existent file",
                    "severity": "low",
                    "category": "architecture",
                    "evidence": ["No evidence"],
                    "explanation": "Hallucinated",
                    "recommendation": "Fix it",
                    "source_locations": [{"file": "completely_fake/imaginary.py", "line": 99}],
                }
            ]
        }

        service = AIArchitectService(groq_client=mock_groq)
        resp = service.run_review(self.mock_result, mode="auto")

        self.assertEqual(resp.status, "completed")
        self.assertEqual(resp.provider, "combined")

        # Check deduplication: Circular dependency should be combined or merged without duplicates
        cycle_findings = [f for f in resp.findings if "circular" in f.title.lower()]
        self.assertEqual(len(cycle_findings), 1)
        self.assertEqual(cycle_findings[0].origin, "combined")

        # Check that AI-ARCH-002 is included with origin 'ai_hypothesis'
        ai_finding = next((f for f in resp.findings if "Missing Error Handling" in f.title), None)
        self.assertIsNotNone(ai_finding)
        self.assertEqual(ai_finding.origin, "ai_hypothesis")

        # Check source location validation: imaginary.py must be filtered out
        fake_finding = next((f for f in resp.findings if "Fabricated finding" in f.title), None)
        self.assertIsNotNone(fake_finding)
        self.assertEqual(len(fake_finding.source_locations), 0)

    def test_change_planner_service(self):
        service = AIArchitectService()
        plan = service.plan_change("Separate auth logic from user service", self.mock_result, mode="local")
        self.assertIsInstance(plan, ChangePlanResponse)
        self.assertGreater(len(plan.affected_files), 0)
        self.assertGreater(len(plan.implementation_sequence), 0)

    def test_ask_question_service(self):
        service = AIArchitectService()
        ans = service.ask_question("Which modules participate in a circular dependency?", self.mock_result, mode="local")
        self.assertIsInstance(ans, AskQuestionResponse)
        self.assertTrue(ans.is_grounded)
        self.assertIn("auth.service", ans.answer)


if __name__ == "__main__":
    unittest.main()
