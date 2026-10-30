"""
tests/test_local_reviewer.py
Unit tests for deterministic LocalReviewer engine.
"""

import unittest
from ai_architect.local_reviewer import LocalReviewer
from ai_architect.schemas import ReviewResponse, ChangePlanResponse, AskQuestionResponse


class TestLocalReviewer(unittest.TestCase):
    def setUp(self):
        self.reviewer = LocalReviewer()
        self.mock_result = {
            "summary": {
                "total_files": 4,
                "total_lines": 800,
                "total_classes": 3,
                "total_functions": 12,
                "avg_complexity": 6.5,
            },
            "modules": [
                {
                    "module_name": "app.auth",
                    "path": "app/auth.py",
                    "code_lines": 350,
                    "functions": [
                        {"name": "login", "complexity": 14, "lineno": 10, "end_lineno": 50, "args": ["u", "p"], "calls": ["check_pw", "jwt_encode"]}
                    ],
                    "classes": [],
                    "imports": [
                        {"module": "app.users", "names": ["get_user"], "line": 2},
                        {"module": "os", "names": ["path"], "line": 3},
                    ],
                },
                {
                    "module_name": "app.users",
                    "path": "app/users.py",
                    "code_lines": 120,
                    "functions": [
                        {"name": "get_user", "complexity": 2, "lineno": 5, "end_lineno": 20, "args": ["uid"], "calls": ["find"]}
                    ],
                    "classes": [],
                    "imports": [
                        {"module": "app.auth", "names": ["verify_token"], "line": 1},
                    ],
                },
                {
                    "module_name": "app.utils",
                    "path": "app/utils.py",
                    "code_lines": 80,
                    "functions": [
                        {"name": "helper", "complexity": 1, "lineno": 4, "end_lineno": 10, "args": [], "calls": []}
                    ],
                    "classes": [],
                    "imports": [
                        {"module": "sys", "names": ["argv"], "line": 1},
                        {"module": "math", "names": ["sin", "cos"], "line": 2},
                    ],
                },
            ],
            "module_metrics": {
                "app.auth": {
                    "efferent_coupling": 9,
                    "afferent_coupling": 2,
                    "instability": 0.82,
                },
                "app.users": {
                    "efferent_coupling": 3,
                    "afferent_coupling": 11,
                    "instability": 0.21,
                },
            },
            "issues": [
                {
                    "rule": "circular_dependency",
                    "severity": "high",
                    "message": "app.auth -> app.users -> app.auth",
                },
                {
                    "rule": "god_module",
                    "module": "app.auth",
                    "severity": "high",
                    "message": "Module has excessive code lines (350)",
                },
            ],
            "all_functions": [
                {
                    "name": "login",
                    "qualified": "app.auth.login",
                    "module": "app.auth",
                    "path": "app/auth.py",
                    "lineno": 10,
                    "end_lineno": 50,
                    "complexity": 14,
                    "args": ["u", "p"],
                    "calls": ["check_pw", "jwt_encode"],
                    "docstring": "",
                }
            ],
            "layer_map": {
                "app.auth": "service",
                "app.users": "data",
                "app.utils": "utility",
            },
            "impact_summaries": {
                "app.auth": {
                    "direct_dependents": ["app.users"],
                    "transitive_dependents": ["app.users"],
                    "affected_tests": ["tests/test_auth.py"],
                }
            },
            "architecture_risks": {
                "risk_backlog": [
                    {
                        "component": "app.auth",
                        "severity": "HIGH",
                        "risk_score": 78.5,
                        "evidence": ["Circular dependency", "High complexity"],
                        "rationale": "High coupling and cycle prevent isolation.",
                    }
                ]
            },
        }

    def test_local_reviewer_produces_valid_response(self):
        resp = self.reviewer.review(self.mock_result)
        self.assertIsInstance(resp, ReviewResponse)
        self.assertEqual(resp.provider, "local")
        self.assertEqual(resp.review_mode, "local")
        self.assertEqual(resp.status, "completed")
        self.assertGreater(len(resp.findings), 0)

        # Check for circular dependency finding
        cycle_finding = next((f for f in resp.findings if f.rule_id == "circular_dependency"), None)
        self.assertIsNotNone(cycle_finding)
        self.assertIn("app.auth", cycle_finding.title)
        self.assertEqual(cycle_finding.origin, "static_rule")

        # Check for high complexity finding
        cc_finding = next((f for f in resp.findings if f.rule_id == "high_complexity"), None)
        self.assertIsNotNone(cc_finding)
        self.assertIn("login", cc_finding.title)

        # Check for coupling finding
        coupling_finding = next((f for f in resp.findings if "high_efferent_coupling" in str(f.rule_id)), None)
        self.assertIsNotNone(coupling_finding)

    def test_local_reviewer_source_locations(self):
        resp = self.reviewer.review(self.mock_result)
        for f in resp.findings:
            self.assertIn(f.severity, ["critical", "high", "medium", "low", "info"])
            self.assertIn(f.category, ["architecture", "coupling", "complexity", "maintainability", "testing", "security", "reliability"])
            self.assertEqual(f.origin, "static_rule")
            self.assertTrue(len(f.recommendation) > 5)
            self.assertTrue(len(f.explanation) > 5)

    def test_change_planner_local(self):
        plan = self.reviewer.plan_change_local("Refactor auth module", self.mock_result)
        self.assertIsInstance(plan, ChangePlanResponse)
        self.assertEqual(plan.provider, "local")
        self.assertTrue(any("auth" in f for f in plan.affected_files))
        self.assertGreater(len(plan.implementation_sequence), 0)
        self.assertGreater(len(plan.potential_risks), 0)

    def test_ask_question_local(self):
        # Q1: What depends on auth?
        q1 = self.reviewer.ask_question_local("What depends on app.auth?", self.mock_result)
        self.assertIsInstance(q1, AskQuestionResponse)
        self.assertTrue(q1.is_grounded)
        self.assertIn("app.users", q1.answer)

        # Q2: Cycles?
        q2 = self.reviewer.ask_question_local("Are there any circular dependencies?", self.mock_result)
        self.assertTrue(q2.is_grounded)
        self.assertIn("app.auth -> app.users -> app.auth", q2.answer)


if __name__ == "__main__":
    unittest.main()
