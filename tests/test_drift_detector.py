"""
test_drift_detector.py
Unit tests for Temporal Architecture Drift Detection (Phase 2).
Covers identical scans, added/removed modules, modified modules, renames,
dependency additions/removals, circular dependency shifts, metric diffs,
and issue resolution/introduction.
"""

import unittest
from analyzer.drift_detector import compare_snapshots


class TestDriftDetector(unittest.TestCase):

    def _sample_snapshot(self):
        return {
            "schema_version": "2.0",
            "project_name": "sample_proj",
            "summary": {
                "total_files": 3,
                "total_lines": 300,
                "total_code_lines": 240,
                "total_complexity": 15,
                "avg_complexity": 5.0,
            },
            "modules": [
                {
                    "module_name": "app",
                    "path": "app.py",
                    "lines": 100,
                    "code_lines": 80,
                    "complexity": 5,
                    "file_hash": "hash_app_1",
                    "layer": "presentation",
                },
                {
                    "module_name": "service",
                    "path": "service.py",
                    "lines": 100,
                    "code_lines": 80,
                    "complexity": 5,
                    "file_hash": "hash_service_1",
                    "layer": "service",
                },
                {
                    "module_name": "db",
                    "path": "db.py",
                    "lines": 100,
                    "code_lines": 80,
                    "complexity": 5,
                    "file_hash": "hash_db_1",
                    "layer": "data",
                },
            ],
            "graph_imports": {
                "nodes": [{"id": "app"}, {"id": "service"}, {"id": "db"}],
                "links": [
                    {"source": "app", "target": "service"},
                    {"source": "service", "target": "db"},
                ],
            },
            "module_metrics": [
                {"module": "app", "coupling": 1},
                {"module": "service", "coupling": 2},
                {"module": "db", "coupling": 1},
            ],
            "issues": [
                {"category": "style", "title": "Empty __init__.py", "location": "app.py"}
            ],
        }

    def test_identical_snapshots_produce_zero_drift(self):
        """Two identical scans of an unchanged project must report 0 false changes."""
        snap1 = self._sample_snapshot()
        snap2 = self._sample_snapshot()

        drift = compare_snapshots(snap1, snap2)
        self.assertTrue(drift["is_identical"])
        self.assertEqual(drift["verdict"], "IDENTICAL")
        self.assertEqual(drift["structural_changes"]["added_modules"], [])
        self.assertEqual(drift["structural_changes"]["removed_modules"], [])
        self.assertEqual(drift["structural_changes"]["modified_modules"], [])
        self.assertEqual(drift["structural_changes"]["added_dependencies"], [])
        self.assertEqual(drift["structural_changes"]["removed_dependencies"], [])
        self.assertEqual(drift["structural_changes"]["new_cycles"], [])
        self.assertEqual(drift["structural_changes"]["resolved_cycles"], [])
        self.assertEqual(drift["issue_changes"]["new_issues"], [])
        self.assertEqual(drift["issue_changes"]["resolved_issues"], [])
        self.assertEqual(drift["metric_changes"]["metrics_diff"]["complexity_diff"], 0)

    def test_added_and_removed_dependencies(self):
        """Verify adding and removing edges between modules."""
        snap1 = self._sample_snapshot()
        snap2 = self._sample_snapshot()

        # In snap2, app stops importing service, but imports db directly
        snap2["graph_imports"]["links"] = [
            {"source": "app", "target": "db"},
            {"source": "service", "target": "db"},
        ]

        drift = compare_snapshots(snap1, snap2)
        self.assertFalse(drift["is_identical"])

        added_edges = drift["structural_changes"]["added_dependencies"]
        removed_edges = drift["structural_changes"]["removed_dependencies"]

        self.assertEqual(len(added_edges), 1)
        self.assertEqual(added_edges[0], {"source": "app", "target": "db"})

        self.assertEqual(len(removed_edges), 1)
        self.assertEqual(removed_edges[0], {"source": "app", "target": "service"})

    def test_circular_dependency_introduction_and_resolution(self):
        """Verify detecting new circular dependency and resolving it."""
        snap_clean = self._sample_snapshot()
        snap_cycle = self._sample_snapshot()

        # Introduce cycle: db imports app (app -> service -> db -> app)
        snap_cycle["graph_imports"]["links"].append({"source": "db", "target": "app"})

        # Compare clean -> cycle: new cycle detected
        drift_to_cycle = compare_snapshots(snap_clean, snap_cycle)
        self.assertEqual(len(drift_to_cycle["structural_changes"]["new_cycles"]), 1)
        self.assertEqual(drift_to_cycle["structural_changes"]["resolved_cycles"], [])
        self.assertIn("DEGRADATION", drift_to_cycle["verdict"])

        # Compare cycle -> clean: resolved cycle detected
        drift_to_clean = compare_snapshots(snap_cycle, snap_clean)
        self.assertEqual(drift_to_clean["structural_changes"]["new_cycles"], [])
        self.assertEqual(len(drift_to_clean["structural_changes"]["resolved_cycles"]), 1)
        self.assertEqual(drift_to_clean["verdict"], "REFACTORED")

    def test_modified_file_hash_and_complexity_spike(self):
        """Verify tracking of file content changes and complexity diffs."""
        snap1 = self._sample_snapshot()
        snap2 = self._sample_snapshot()

        # Modify service.py
        snap2["modules"][1]["file_hash"] = "hash_service_v2"
        snap2["modules"][1]["complexity"] = 12
        snap2["modules"][1]["lines"] = 140
        snap2["summary"]["total_complexity"] = 22
        snap2["summary"]["total_lines"] = 340

        drift = compare_snapshots(snap1, snap2)
        mods = drift["structural_changes"]["modified_modules"]
        self.assertEqual(len(mods), 1)
        self.assertEqual(mods[0]["module_name"], "service")
        self.assertEqual(mods[0]["complexity_diff"], 7)
        self.assertEqual(mods[0]["lines_diff"], 40)
        self.assertEqual(drift["metric_changes"]["metrics_diff"]["complexity_diff"], 7)

    def test_renamed_module_detection(self):
        """Verify conservative rename detection via identical file hash."""
        snap1 = self._sample_snapshot()
        snap2 = self._sample_snapshot()

        # In snap2, db.py is renamed to repository.py, keeping same hash_db_1
        snap2["modules"] = [
            snap2["modules"][0],
            snap2["modules"][1],
            {
                "module_name": "repository",
                "path": "repository.py",
                "lines": 100,
                "code_lines": 80,
                "complexity": 5,
                "file_hash": "hash_db_1",
                "layer": "data",
            },
        ]

        drift = compare_snapshots(snap1, snap2)
        renames = drift["structural_changes"]["renamed_modules"]
        self.assertEqual(len(renames), 1)
        self.assertEqual(renames[0]["old_module"], "db")
        self.assertEqual(renames[0]["new_module"], "repository")

    def test_issue_lifecycle_new_and_resolved(self):
        """Verify distinguishing newly added issues from resolved issues."""
        snap1 = self._sample_snapshot()
        snap2 = self._sample_snapshot()

        # snap1 has: Empty __init__.py in app.py
        # In snap2, that issue is resolved, but a new High complexity issue is introduced
        snap2["issues"] = [
            {"category": "complexity", "title": "High complexity in `run`", "location": "service.py:45"}
        ]

        drift = compare_snapshots(snap1, snap2)
        self.assertEqual(drift["issue_changes"]["new_count"], 1)
        self.assertEqual(drift["issue_changes"]["resolved_count"], 1)
        self.assertEqual(drift["issue_changes"]["new_issues"][0]["category"], "complexity")
        self.assertEqual(drift["issue_changes"]["resolved_issues"][0]["category"], "style")


if __name__ == "__main__":
    unittest.main()
