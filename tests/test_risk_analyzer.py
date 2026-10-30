"""
test_risk_analyzer.py
Unit tests for Phase 4 Explainable Architecture Risk Analyzer.
Covers low-risk vs high-risk projects, weight configuration sensitivity,
factor breakdown, individual component prioritization, and missing data handling.
"""

import unittest
import networkx as nx

from analyzer.project_model import ProjectModel, ModuleInfo, FunctionInfo
from analyzer.risk_analyzer import (
    compute_project_risk_score,
    analyze_component_risks,
    analyze_architecture_risks,
    analyze_architecture_risks_from_result,
    DEFAULT_RISK_WEIGHTS,
)


class TestRiskAnalyzer(unittest.TestCase):

    def test_low_risk_project(self):
        """Clean project with low complexity, no cycles, no hubs, clean layers."""
        model = ProjectModel(name="clean_proj", root="")
        model.modules = [
            ModuleInfo(path="a.py", abs_path="", module_name="a", complexity=2),
            ModuleInfo(path="b.py", abs_path="", module_name="b", complexity=2),
            ModuleInfo(path="c.py", abs_path="", module_name="c", complexity=2),
        ]
        model.build_lookup()

        G = nx.DiGraph()
        G.add_edge("a", "b")
        G.add_edge("b", "c")

        score_res = compute_project_risk_score(model, G)
        self.assertEqual(score_res["risk_level"], "LOW")
        self.assertLess(score_res["risk_score"], 25)
        self.assertEqual(score_res["cycles_count"], 0)
        self.assertIn("cycles", score_res["factors"])
        self.assertIn("contributions", score_res)

    def test_high_risk_project_with_cycles_and_complexity(self):
        """Project with tight cycles, high complexity, and central hubs."""
        model = ProjectModel(name="tangled_proj", root="")
        model.modules = [
            ModuleInfo(path="auth.py", abs_path="", module_name="auth", complexity=35,
                       functions=[FunctionInfo(name="complex_auth", lineno=1, end_lineno=100, complexity=25)]),
            ModuleInfo(path="user.py", abs_path="", module_name="user", complexity=30),
            ModuleInfo(path="session.py", abs_path="", module_name="session", complexity=25),
        ]
        model.build_lookup()

        # Cycle: auth -> user -> session -> auth
        G = nx.DiGraph()
        G.add_edge("auth", "user")
        G.add_edge("user", "session")
        G.add_edge("session", "auth")

        score_res = compute_project_risk_score(model, G)
        self.assertIn(score_res["risk_level"], ["HIGH", "CRITICAL"])
        self.assertGreaterEqual(score_res["risk_score"], 45)
        self.assertGreaterEqual(score_res["cycles_count"], 1)

    def test_configurable_weights_affect_score(self):
        """Changing configured weights produces expected score changes."""
        model = ProjectModel(name="test_proj", root="")
        model.modules = [
            ModuleInfo(path="a.py", abs_path="", module_name="a", complexity=40),
            ModuleInfo(path="b.py", abs_path="", module_name="b", complexity=5),
        ]
        model.build_lookup()
        G = nx.DiGraph()
        G.add_edge("a", "b")

        # Heavy complexity weight vs zero complexity weight
        heavy_comp_weights = {"complexity": 0.8, "cycles": 0.05, "coupling": 0.05, "layer_violations": 0.05, "test_absence": 0.05}
        zero_comp_weights = {"complexity": 0.0, "cycles": 0.25, "coupling": 0.25, "layer_violations": 0.25, "test_absence": 0.25}

        score_heavy = compute_project_risk_score(model, G, weights=heavy_comp_weights)
        score_zero = compute_project_risk_score(model, G, weights=zero_comp_weights)

        self.assertGreater(score_heavy["risk_score"], score_zero["risk_score"])
        self.assertGreater(score_heavy["contributions"]["complexity"], score_zero["contributions"]["complexity"])

    def test_component_risk_prioritization_and_explanations(self):
        """Verify individual modules receive prioritized backlog entries with actionable guidance."""
        model = ProjectModel(name="test_proj", root="")
        m_hub = ModuleInfo(
            path="core/hub.py",
            abs_path="",
            module_name="core.hub",
            complexity=28,
            functions=[FunctionInfo(name="do_everything", lineno=1, end_lineno=80, complexity=15)],
        )
        m_leaf = ModuleInfo(path="leaf.py", abs_path="", module_name="leaf", complexity=2)
        model.modules = [m_hub, m_leaf]
        model.build_lookup()

        G = nx.DiGraph()
        G.add_edge("leaf", "core.hub")

        impact_summaries = [
            {"module_name": "core.hub", "total_dependents": 8, "affected_tests_count": 0},
            {"module_name": "leaf", "total_dependents": 0, "affected_tests_count": 0},
        ]
        layer_map = {"core.hub": "service", "leaf": "other"}

        risks = analyze_component_risks(model, G, layer_map=layer_map, impact_summaries=impact_summaries)
        self.assertGreaterEqual(len(risks), 1)

        hub_risk = next((r for r in risks if r["component"] == "core.hub"), None)
        self.assertIsNotNone(hub_risk)
        self.assertIn(hub_risk["priority"], ["HIGH", "CRITICAL"])
        self.assertIn("complexity", hub_risk["evidence"].lower())
        self.assertIn("blast radius", hub_risk["evidence"].lower())
        self.assertTrue(len(hub_risk["why_it_matters"]) > 0)
        self.assertTrue(len(hub_risk["recommended_action"]) > 0)

    def test_analyze_architecture_risks_from_result(self):
        """Verify complete risk analyzer works from serialized database dictionary."""
        result_dict = {
            "project_name": "db_test",
            "modules": [
                {"module_name": "app", "path": "app.py", "lines": 50, "code_lines": 40, "complexity": 3, "functions": [], "parse_error": None},
                {"module_name": "core", "path": "core.py", "lines": 150, "code_lines": 120, "complexity": 22, "functions": [], "parse_error": None},
            ],
            "graph_imports": {
                "nodes": [{"id": "app"}, {"id": "core"}],
                "links": [{"source": "app", "target": "core"}],
            },
            "layer_map": {"app": "presentation", "core": "service"},
            "issues": [],
            "impact_summaries": [
                {"module_name": "core", "total_dependents": 1, "affected_tests_count": 0}
            ],
        }

        risk_analysis = analyze_architecture_risks_from_result(result_dict)
        self.assertEqual(risk_analysis["status"], "ANALYZED")
        self.assertIn("project_risk", risk_analysis)
        self.assertIn("prioritized_risks", risk_analysis)
        self.assertIn(risk_analysis["project_risk"]["risk_level"], ["LOW", "MEDIUM", "HIGH", "CRITICAL"])


if __name__ == "__main__":
    unittest.main()
