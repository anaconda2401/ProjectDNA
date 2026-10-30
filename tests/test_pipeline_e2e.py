"""
test_pipeline_e2e.py
End-to-end integration tests for ProjectDNA pipeline, verifying graceful handling
of syntax errors, directory scanning, and interconnected analysis across all 4 phases.
"""

import os
import shutil
import tempfile
import unittest

from analyzer.pipeline import run_pipeline
from analyzer.drift_detector import compare_snapshots
from analyzer.impact_analyzer import analyze_change_impact_from_result
from analyzer.module_discovery import discover_refactoring_from_result
from analyzer.risk_analyzer import analyze_architecture_risks_from_result


class TestPipelineE2E(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="projectdna_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_pipeline_with_syntax_error_and_valid_files(self):
        """Pipeline must handle files with invalid Python syntax without crashing."""
        # Valid module A
        mod_a = os.path.join(self.tmp_dir, "valid_a.py")
        with open(mod_a, "w", encoding="utf-8") as f:
            f.write("import valid_b\ndef run():\n    return valid_b.calc()\n")

        # Valid module B
        mod_b = os.path.join(self.tmp_dir, "valid_b.py")
        with open(mod_b, "w", encoding="utf-8") as f:
            f.write("def calc():\n    return 42\n")

        # Broken module C (SyntaxError)
        mod_c = os.path.join(self.tmp_dir, "broken_c.py")
        with open(mod_c, "w", encoding="utf-8") as f:
            f.write("def broken(\n    # unclosed parenthesis\n")

        res = run_pipeline(self.tmp_dir, "E2E_Syntax_Test")

        self.assertEqual(res["summary"]["total_files"], 3)
        self.assertEqual(res["summary"]["parsed_files"], 2)
        self.assertEqual(res["summary"]["error_files"], 1)

        broken_mod = next(m for m in res["modules"] if m["module_name"] == "broken_c")
        self.assertIsNotNone(broken_mod["parse_error"])
        self.assertIn("SyntaxError", broken_mod["parse_error"])

        # Check that valid modules still have graphs and metrics
        node_ids = [n["id"] for n in res["graph_imports"]["nodes"]]
        self.assertIn("valid_a", node_ids)
        self.assertIn("valid_b", node_ids)

        # Check impact analysis still works on valid modules
        impact = analyze_change_impact_from_result(res, "valid_b")
        self.assertTrue(impact["found"])
        self.assertIn("valid_a", impact["direct_dependents"])

        # Check risk analyzer flags broken_c as a Critical risk item
        risks = analyze_architecture_risks_from_result(res)
        broken_risk = next((r for r in risks["prioritized_risks"] if r["component"] == "broken_c"), None)
        self.assertIsNotNone(broken_risk)
        self.assertEqual(broken_risk["priority"], "CRITICAL")

    def test_pipeline_drift_between_two_filesystem_versions(self):
        """Create two version folders v1 and v2, run pipeline on each, verify drift."""
        v1_dir = os.path.join(self.tmp_dir, "v1")
        v2_dir = os.path.join(self.tmp_dir, "v2")
        os.makedirs(v1_dir, exist_ok=True)
        os.makedirs(v2_dir, exist_ok=True)

        # V1: main imports service
        with open(os.path.join(v1_dir, "main.py"), "w", encoding="utf-8") as f:
            f.write("import service\nservice.action()\n")
        with open(os.path.join(v1_dir, "service.py"), "w", encoding="utf-8") as f:
            f.write("def action():\n    pass\n")

        # V2: main imports service, service imports new helper, helper imports main (creates cycle!)
        with open(os.path.join(v2_dir, "main.py"), "w", encoding="utf-8") as f:
            f.write("import service\nservice.action()\n")
        with open(os.path.join(v2_dir, "service.py"), "w", encoding="utf-8") as f:
            f.write("import helper\ndef action():\n    helper.help()\n")
        with open(os.path.join(v2_dir, "helper.py"), "w", encoding="utf-8") as f:
            f.write("import main\ndef help():\n    pass\n")

        res_v1 = run_pipeline(v1_dir, "TestV1")
        res_v2 = run_pipeline(v2_dir, "TestV2")

        drift = compare_snapshots(res_v1, res_v2)
        self.assertFalse(drift["is_identical"])

        # helper.py was added
        added = [m["module_name"] for m in drift["structural_changes"]["added_modules"]]
        self.assertIn("helper", added)

        # cycle was introduced (main -> service -> helper -> main)
        self.assertEqual(len(drift["structural_changes"]["new_cycles"]), 1)
        self.assertIn("DEGRADATION", drift["verdict"])


if __name__ == "__main__":
    unittest.main()
