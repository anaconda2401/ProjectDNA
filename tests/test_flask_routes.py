"""
test_flask_routes.py
Integration tests for Flask application routes, including Phase 1 Change Impact endpoints.
"""

import unittest
from app import app
import database as db


class TestFlaskRoutes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

        # Insert a sample analysis record to test with
        sample_result = {
            "project_name": "sample_flask_test",
            "elapsed": 0.1,
            "scan_warnings": [],
            "summary": {
                "total_files": 3,
                "parsed_files": 3,
                "error_files": 0,
                "total_lines": 120,
                "total_classes": 2,
                "total_functions": 4,
                "total_complexity": 8,
                "avg_complexity": 2.67,
            },
            "modules": [
                {
                    "path": "app.py",
                    "module_name": "app",
                    "lines": 50,
                    "code_lines": 40,
                    "complexity": 3,
                    "classes": [],
                    "functions": [{"name": "main", "lineno": 10, "end_lineno": 20, "complexity": 1}],
                    "parse_error": None,
                    "layer": "presentation",
                },
                {
                    "path": "services/auth.py",
                    "module_name": "services.auth",
                    "lines": 40,
                    "code_lines": 35,
                    "complexity": 3,
                    "classes": [],
                    "functions": [{"name": "login", "lineno": 5, "end_lineno": 15, "complexity": 2}],
                    "parse_error": None,
                    "layer": "service",
                },
                {
                    "path": "db/models.py",
                    "module_name": "db.models",
                    "lines": 30,
                    "code_lines": 25,
                    "complexity": 2,
                    "classes": [{"name": "User", "lineno": 5, "end_lineno": 25, "methods": [], "bases": []}],
                    "functions": [],
                    "parse_error": None,
                    "layer": "data",
                },
            ],
            "layer_map": {
                "app": "presentation",
                "services.auth": "service",
                "db.models": "data",
            },
            "graph_imports": {
                "nodes": [{"id": "app"}, {"id": "services.auth"}, {"id": "db.models"}],
                "links": [
                    {"source": "app", "target": "services.auth"},
                    {"source": "services.auth", "target": "db.models"},
                ],
            },
            "all_classes": [
                {"name": "User", "module": "db.models", "path": "db/models.py", "method_count": 0, "methods": [], "bases": [], "subclasses": []}
            ],
            "all_functions": [
                {"name": "login", "module": "services.auth", "path": "services/auth.py", "lineno": 5, "complexity": 2, "args": [], "callers": ["app.main"], "calls": []}
            ],
            "module_metrics": [],
            "issues": [],
            "impact_summaries": [
                {
                    "module_name": "db.models",
                    "path": "db/models.py",
                    "layer": "data",
                    "direct_count": 1,
                    "transitive_count": 1,
                    "total_dependents": 2,
                    "affected_tests_count": 0,
                    "risk_level": "MEDIUM",
                    "risk_score": 45,
                    "explanation": "Downstream blast radius across 2 modules.",
                }
            ],
        }

        cls.analysis_id = db.save_analysis("sample_flask_test", sample_result)

    @classmethod
    def tearDownClass(cls):
        db.delete_analysis(cls.analysis_id)

    def test_index_route(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"ProjectDNA", resp.data)

    def test_dashboard_route(self):
        resp = self.client.get(f"/dashboard/{self.analysis_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"sample_flask_test", resp.data)
        self.assertIn(b"Change Impact", resp.data)

    def test_impact_page_route(self):
        resp = self.client.get(f"/impact/{self.analysis_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Change Impact Analysis", resp.data)
        self.assertIn(b"db.models", resp.data)

    def test_impact_page_with_target_param(self):
        resp = self.client.get(f"/impact/{self.analysis_id}?target=services.auth")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"services.auth", resp.data)

    def test_api_impact_endpoint(self):
        resp = self.client.get(f"/api/impact/{self.analysis_id}?module=db.models")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["found"])
        self.assertEqual(data["target"], "db.models")
        self.assertIn("services.auth", data["direct_dependents"])
        self.assertIn("app", data["transitive_dependents"])

    def test_api_impact_missing_module(self):
        resp = self.client.get(f"/api/impact/{self.analysis_id}")
        self.assertEqual(resp.status_code, 400)

    def test_drift_page_route(self):
        resp = self.client.get(f"/drift/{self.analysis_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Architecture Drift", resp.data)

    def test_api_drift_endpoint(self):
        resp = self.client.get(f"/api/drift?base_id={self.analysis_id}&target_id={self.analysis_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["is_identical"])
        self.assertEqual(data["verdict"], "IDENTICAL")

    def test_api_drift_missing_args(self):
        resp = self.client.get("/api/drift")
        self.assertEqual(resp.status_code, 400)

    def test_discovery_page_route(self):
        resp = self.client.get(f"/discovery/{self.analysis_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Module Discovery", resp.data)

    def test_api_discovery_endpoint(self):
        resp = self.client.get(f"/api/discovery/{self.analysis_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("status", data)

    def test_risks_page_route(self):
        resp = self.client.get(f"/risks/{self.analysis_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Architecture Risk", resp.data)
        self.assertIn(b"Contributing Risk Factors", resp.data)

    def test_api_risks_endpoint_with_custom_weights(self):
        resp = self.client.get(f"/api/risks/{self.analysis_id}?w_complexity=0.7&w_cycles=0.3")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("project_risk", data)
        self.assertIn("prioritized_risks", data)
        self.assertEqual(data["status"], "ANALYZED")

    def test_nonexistent_analysis_404(self):
        resp = self.client.get("/dashboard/99999999")
        self.assertEqual(resp.status_code, 404)

        resp2 = self.client.get("/impact/99999999")
        self.assertEqual(resp2.status_code, 404)

        resp3 = self.client.get("/drift/99999999")
        self.assertEqual(resp3.status_code, 404)

        resp4 = self.client.get("/discovery/99999999")
        self.assertEqual(resp4.status_code, 404)

        resp5 = self.client.get("/risks/99999999")
        self.assertEqual(resp5.status_code, 404)

        resp6 = self.client.get("/ai-architect/99999999")
        self.assertEqual(resp6.status_code, 404)

    def test_ai_architect_page_route(self):
        resp = self.client.get(f"/ai-architect/{self.analysis_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"AI Architect", resp.data)
        self.assertIn(b"Change Planner", resp.data)
        self.assertIn(b"Ask ProjectDNA", resp.data)

    def test_api_ai_review_post_and_get(self):
        # Post local review
        resp = self.client.post(
            f"/api/ai-review/{self.analysis_id}",
            json={"mode": "local"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("review_summary", data)
        self.assertIn("findings", data)
        self.assertEqual(data["provider"], "local")

        # Get latest saved review
        get_resp = self.client.get(f"/api/ai-review/{self.analysis_id}")
        self.assertEqual(get_resp.status_code, 200)
        get_data = get_resp.get_json()
        self.assertIn("review_summary", get_data)

    def test_api_ai_plan_endpoint(self):
        resp = self.client.post(
            f"/api/ai-plan/{self.analysis_id}",
            json={"proposed_change": "Separate auth from database logic", "mode": "local"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("summary", data)
        self.assertIn("affected_files", data)
        self.assertIn("implementation_sequence", data)

        # Empty body returns 400
        bad_resp = self.client.post(
            f"/api/ai-plan/{self.analysis_id}",
            json={},
        )
        self.assertEqual(bad_resp.status_code, 400)

    def test_api_ai_ask_endpoint(self):
        resp = self.client.post(
            f"/api/ai-ask/{self.analysis_id}",
            json={"question": "What are the most complex functions?", "mode": "local"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("answer", data)
        self.assertIn("is_grounded", data)

        # Empty question returns 400
        bad_resp = self.client.post(
            f"/api/ai-ask/{self.analysis_id}",
            json={},
        )
        self.assertEqual(bad_resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
