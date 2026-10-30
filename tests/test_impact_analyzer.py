"""
test_impact_analyzer.py
Unit tests for Change Impact Analysis (Phase 1).
Covers chains, branches, cycles, disconnected nodes, unknown modules,
symbol tracking, and risk assessment.
"""

import unittest
import networkx as nx

from analyzer.project_model import ProjectModel, ModuleInfo, ClassInfo, FunctionInfo
from analyzer.impact_analyzer import (
    find_direct_dependents,
    find_transitive_dependents,
    analyze_change_impact,
    compute_all_impact_summaries,
    assess_change_risk,
)


class TestImpactAnalyzer(unittest.TestCase):

    def setUp(self):
        # Build synthetic project model
        self.model = ProjectModel(name="test_proj", root="/fake/root")

    def test_direct_and_transitive_chain(self):
        """
        Topology:
          app -> service -> db
          (app imports service, service imports db)
        When 'db' changes:
          Direct dependent: 'service'
          Transitive dependents: 'app'
          Path from app: ['app', 'service', 'db']
        """
        G = nx.DiGraph()
        G.add_edge("app", "service")
        G.add_edge("service", "db")

        direct = find_direct_dependents("db", G)
        self.assertEqual(direct, ["service"])

        transitive, paths = find_transitive_dependents("db", G)
        self.assertEqual(sorted(transitive), ["app"])
        self.assertIn("app", paths)
        # Path shows who imports who leading down to db: [app, service, db]
        self.assertEqual(paths["app"], ["app", "service", "db"])

    def test_branching_dependents(self):
        """
        Topology:
          web -> core
          api -> core
          client -> api
        When 'core' changes:
          Direct dependents: ['api', 'web']
          Transitive dependents: ['client']
        """
        G = nx.DiGraph()
        G.add_edge("web", "core")
        G.add_edge("api", "core")
        G.add_edge("client", "api")

        direct = find_direct_dependents("core", G)
        self.assertEqual(sorted(direct), ["api", "web"])

        transitive, paths = find_transitive_dependents("core", G)
        self.assertEqual(sorted(transitive), ["client"])
        self.assertEqual(paths["client"], ["client", "api", "core"])

    def test_cyclic_imports_terminates_and_no_duplicates(self):
        """
        Topology:
          cycle_a -> cycle_b -> cycle_c -> cycle_a
          external -> cycle_a
        When 'cycle_b' changes:
          Traversal must terminate cleanly and produce no duplicate nodes.
        """
        G = nx.DiGraph()
        G.add_edge("cycle_a", "cycle_b")
        G.add_edge("cycle_b", "cycle_c")
        G.add_edge("cycle_c", "cycle_a")
        G.add_edge("external", "cycle_a")

        direct = find_direct_dependents("cycle_b", G)
        self.assertEqual(direct, ["cycle_a"])

        transitive, paths = find_transitive_dependents("cycle_b", G)
        # Transitive dependents must include cycle_c and external, without cycle_b itself
        self.assertEqual(len(transitive), len(set(transitive)), "Must contain no duplicates")
        self.assertNotIn("cycle_b", transitive, "Target itself should not be in transitive dependents")
        self.assertIn("cycle_c", transitive)
        self.assertIn("external", transitive)

    def test_disconnected_module(self):
        """Module with no incoming or outgoing dependencies."""
        G = nx.DiGraph()
        G.add_node("isolated")
        G.add_edge("a", "b")

        direct = find_direct_dependents("isolated", G)
        self.assertEqual(direct, [])

        transitive, paths = find_transitive_dependents("isolated", G)
        self.assertEqual(transitive, [])
        self.assertEqual(paths, {})

    def test_unknown_module_handled_gracefully(self):
        """Querying a module not in the graph should not crash."""
        G = nx.DiGraph()
        G.add_edge("a", "b")

        result = analyze_change_impact("nonexistent_module", self.model, G)
        self.assertFalse(result["found"])
        self.assertEqual(result["direct_dependents"], [])
        self.assertEqual(result["transitive_dependents"], [])
        self.assertEqual(result["risk_level"], "LOW")

    def test_symbol_tracking_and_test_identification(self):
        """Verify downstream subclasses, callers, and test modules are identified."""
        m_auth = ModuleInfo(
            path="services/auth.py",
            abs_path="/fake/services/auth.py",
            module_name="services.auth",
            classes=[
                ClassInfo(name="BaseAuth", lineno=10, end_lineno=50)
            ],
            functions=[
                FunctionInfo(name="verify_token", lineno=55, end_lineno=70)
            ]
        )
        m_web = ModuleInfo(
            path="web/routes.py",
            abs_path="/fake/web/routes.py",
            module_name="web.routes",
        )
        m_test = ModuleInfo(
            path="tests/test_auth.py",
            abs_path="/fake/tests/test_auth.py",
            module_name="tests.test_auth",
        )
        self.model.modules = [m_auth, m_web, m_test]
        self.model.build_lookup()

        G = nx.DiGraph()
        G.add_edge("web.routes", "services.auth")
        G.add_edge("tests.test_auth", "services.auth")

        layer_map = {
            "services.auth": "service",
            "web.routes": "presentation",
            "tests.test_auth": "test",
        }
        callers_map = {
            "verify_token": ["web.routes.login_endpoint"]
        }
        subclasses_map = {
            "BaseAuth": ["web.routes.CustomAuth"]
        }

        result = analyze_change_impact(
            "services.auth",
            self.model,
            G,
            layer_map=layer_map,
            callers_map=callers_map,
            subclasses_map=subclasses_map,
        )

        self.assertTrue(result["found"])
        self.assertIn("web.routes", result["direct_dependents"])
        self.assertIn("tests.test_auth", result["direct_dependents"])
        self.assertIn("tests.test_auth", result["affected_tests"])

        # Check affected symbols
        affected_classes = result["affected_classes"]
        self.assertTrue(any(c["parent_class"] == "BaseAuth" for c in affected_classes))

        affected_functions = result["affected_functions"]
        self.assertTrue(any(f["target_function"] == "verify_token" for f in affected_functions))

    def test_risk_scoring_scale_and_rationale(self):
        """Test risk score levels and generated explanations."""
        # Low risk: 0 dependents
        level_low, exp_low, score_low = assess_change_risk(
            "leaf", direct_count=0, transitive_count=0,
            affected_tests_count=0, in_cycle=False, is_hub=False, centrality=0.0
        )
        self.assertEqual(level_low, "LOW")
        self.assertIn("No direct project dependents", exp_low)

        # Critical risk: 6 direct, 10 transitive, hub, in cycle
        level_crit, exp_crit, score_crit = assess_change_risk(
            "core", direct_count=6, transitive_count=10,
            affected_tests_count=2, in_cycle=True, is_hub=True, centrality=0.4
        )
        self.assertEqual(level_crit, "CRITICAL")
        self.assertGreaterEqual(score_crit, 75)
        self.assertIn("circular dependency", exp_crit)
        self.assertIn("central architectural dependency hub", exp_crit)

    def test_compute_all_impact_summaries(self):
        """Test project-wide summaries sorting."""
        m_a = ModuleInfo(path="a.py", abs_path="/fake/a.py", module_name="a")
        m_b = ModuleInfo(path="b.py", abs_path="/fake/b.py", module_name="b")
        m_c = ModuleInfo(path="c.py", abs_path="/fake/c.py", module_name="c")
        self.model.modules = [m_a, m_b, m_c]
        self.model.build_lookup()

        G = nx.DiGraph()
        G.add_edge("a", "b")
        G.add_edge("b", "c")

        summaries = compute_all_impact_summaries(self.model, G)
        self.assertEqual(len(summaries), 3)
        # c has 2 dependents (b direct, a transitive), b has 1 (a direct), a has 0
        names_in_order = [s["module_name"] for s in summaries]
        self.assertEqual(names_in_order[0], "c")
        self.assertEqual(names_in_order[-1], "a")

    def test_analyze_change_impact_from_result(self):
        """Verify analyzing change impact from serialized dictionary (DB record)."""
        from analyzer.impact_analyzer import analyze_change_impact_from_result

        result_dict = {
            "project_name": "test_db_project",
            "modules": [
                {
                    "path": "mod_a.py",
                    "module_name": "mod_a",
                    "lines": 20,
                    "code_lines": 15,
                    "complexity": 2,
                    "classes": [],
                    "functions": [{"name": "func_a", "lineno": 5, "end_lineno": 10, "complexity": 1}],
                    "parse_error": None,
                },
                {
                    "path": "mod_b.py",
                    "module_name": "mod_b",
                    "lines": 30,
                    "code_lines": 25,
                    "complexity": 4,
                    "classes": [],
                    "functions": [],
                    "parse_error": None,
                },
            ],
            "layer_map": {"mod_a": "utility", "mod_b": "service"},
            "graph_imports": {
                "nodes": [{"id": "mod_a"}, {"id": "mod_b"}],
                "links": [{"source": "mod_b", "target": "mod_a"}],  # mod_b imports mod_a
            },
            "all_functions": [
                {"name": "func_a", "callers": ["mod_b.call_a"]}
            ],
            "all_classes": [],
        }

        impact = analyze_change_impact_from_result(result_dict, "mod_a")
        self.assertTrue(impact["found"])
        self.assertIn("mod_b", impact["direct_dependents"])
        self.assertEqual(impact["target"], "mod_a")
        self.assertTrue(any(f["target_function"] == "func_a" for f in impact["affected_functions"]))


if __name__ == "__main__":
    unittest.main()
