"""
test_module_discovery.py
Unit tests for Automatic Module Discovery & Refactoring Recommendations (Phase 3).
Covers cohesive module clusters, misplaced module detection, split-responsibility detection,
insufficient evidence on small projects, and generic token filtering.
"""

import unittest
import networkx as nx

from analyzer.project_model import ProjectModel, ModuleInfo, ClassInfo, FunctionInfo, ImportInfo
from analyzer.module_discovery import (
    discover_refactoring_recommendations,
    discover_refactoring_from_result,
    compute_module_affinity,
    _tokenize,
    GENERIC_STOPWORDS,
)


class TestModuleDiscovery(unittest.TestCase):

    def test_generic_stopwords_filtered(self):
        """Verify generic software words like 'utils', 'helper', 'manager' are excluded."""
        tokens = _tokenize("payment_utils_helper_manager_data_service")
        self.assertIn("payment", tokens)
        self.assertIn("service", tokens)
        self.assertNotIn("utils", tokens)
        self.assertNotIn("helper", tokens)
        self.assertNotIn("manager", tokens)
        self.assertNotIn("data", tokens)

    def test_small_project_reports_insufficient_evidence(self):
        """Projects with < 4 modules must safely report INSUFFICIENT_EVIDENCE."""
        model = ProjectModel(name="tiny_proj", root="")
        model.modules = [
            ModuleInfo(path="a.py", abs_path="", module_name="a"),
            ModuleInfo(path="b.py", abs_path="", module_name="b"),
        ]
        model.build_lookup()
        G = nx.DiGraph()

        res = discover_refactoring_recommendations(model, G, min_project_modules=4)
        self.assertEqual(res["status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("at least 4 modules", res["message"])
        self.assertEqual(res["candidate_groups"], [])

    def test_cohesive_module_cluster_detected_with_evidence(self):
        """
        Verify that a group of related billing modules (billing_routes, billing_service,
        billing_processor, invoice_model) are discovered together.
        """
        m_routes = ModuleInfo(
            path="api/billing_routes.py",
            abs_path="",
            module_name="api.billing_routes",
            functions=[FunctionInfo(name="process_billing_request", lineno=1, end_lineno=10)],
            imports=[ImportInfo(module="services.billing_service", names=["BillingService"])],
        )
        m_service = ModuleInfo(
            path="services/billing_service.py",
            abs_path="",
            module_name="services.billing_service",
            classes=[ClassInfo(name="BillingService", lineno=1, end_lineno=50)],
            imports=[ImportInfo(module="engine.billing_processor", names=["InvoiceProcessor"])],
        )
        m_proc = ModuleInfo(
            path="engine/billing_processor.py",
            abs_path="",
            module_name="engine.billing_processor",
            classes=[ClassInfo(name="InvoiceProcessor", lineno=1, end_lineno=50)],
            imports=[ImportInfo(module="models.invoice_model", names=["Invoice"])],
        )
        m_invoice = ModuleInfo(
            path="models/invoice_model.py",
            abs_path="",
            module_name="models.invoice_model",
            classes=[ClassInfo(name="Invoice", lineno=1, end_lineno=30)],
        )
        # Unrelated module
        m_unrelated = ModuleInfo(
            path="network/socket_stream.py",
            abs_path="",
            module_name="network.socket_stream",
            functions=[FunctionInfo(name="listen_socket_stream", lineno=1, end_lineno=20)],
        )

        model = ProjectModel(name="test_proj", root="")
        model.modules = [m_routes, m_service, m_proc, m_invoice, m_unrelated]
        model.build_lookup()

        G = nx.DiGraph()
        G.add_edge("api.billing_routes", "services.billing_service")
        G.add_edge("services.billing_service", "engine.billing_processor")
        G.add_edge("engine.billing_processor", "models.invoice_model")

        res = discover_refactoring_recommendations(model, G)
        self.assertEqual(res["status"], "ANALYZED")
        groups = res["candidate_groups"]
        self.assertGreaterEqual(len(groups), 1)

        # Find the billing cluster
        billing_grp = next((g for g in groups if "billing" in g["suggested_group"].lower() or "invoice" in g["suggested_group"].lower()), None)
        self.assertIsNotNone(billing_grp)
        self.assertIn(billing_grp["confidence"], ["MEDIUM", "HIGH"])
        self.assertTrue(len(billing_grp["evidence"]) > 0)
        # Network module should not be in the billing cluster
        self.assertNotIn("network.socket_stream", billing_grp["candidate_modules"])

    def test_misplaced_module_recommendation(self):
        """
        Verify that a payment file placed in 'utils/' that heavily depends on
        and shares vocabulary with 'payment' package is flagged as misplaced.
        """
        m_util_pay = ModuleInfo(
            path="utils/payment_charge.py",
            abs_path="",
            module_name="utils.payment_charge",
            functions=[FunctionInfo(name="charge_payment_card", lineno=1, end_lineno=20)],
            imports=[
                ImportInfo(module="payment.gateway", names=["StripeGateway"]),
                ImportInfo(module="payment.ledger", names=["LedgerRecord"]),
            ],
        )
        m_pay_gw = ModuleInfo(
            path="payment/gateway.py",
            abs_path="",
            module_name="payment.gateway",
            classes=[ClassInfo(name="StripeGateway", lineno=1, end_lineno=30)],
        )
        m_pay_ledg = ModuleInfo(
            path="payment/ledger.py",
            abs_path="",
            module_name="payment.ledger",
            classes=[ClassInfo(name="LedgerRecord", lineno=1, end_lineno=30)],
        )
        m_other_util = ModuleInfo(
            path="utils/string_cleaner.py",
            abs_path="",
            module_name="utils.string_cleaner",
            functions=[FunctionInfo(name="clean_ascii_string", lineno=1, end_lineno=20)],
        )

        model = ProjectModel(name="test_proj", root="")
        model.modules = [m_util_pay, m_pay_gw, m_pay_ledg, m_other_util]
        model.build_lookup()

        G = nx.DiGraph()
        G.add_edge("utils.payment_charge", "payment.gateway")
        G.add_edge("utils.payment_charge", "payment.ledger")

        res = discover_refactoring_recommendations(model, G)
        self.assertEqual(res["status"], "ANALYZED")
        misplaced = res["misplaced_modules"]

        # utils.payment_charge should be identified as candidate to relocate
        self.assertTrue(any(m["module_name"] == "utils.payment_charge" for m in misplaced))

    def test_split_module_candidate(self):
        """Verify large module with disjoint classes/functions is flagged for splitting."""
        m_large = ModuleInfo(
            path="monolith/god_service.py",
            abs_path="",
            module_name="monolith.god_service",
            lines=400,
            code_lines=350,
            classes=[
                ClassInfo(name="UserAuthenticator", lineno=1, end_lineno=100, methods=[
                    FunctionInfo(name="auth_login", lineno=10, end_lineno=30),
                    FunctionInfo(name="auth_logout", lineno=35, end_lineno=50),
                ]),
                ClassInfo(name="ReportExporter", lineno=110, end_lineno=220, methods=[
                    FunctionInfo(name="export_csv", lineno=120, end_lineno=150),
                    FunctionInfo(name="export_pdf", lineno=160, end_lineno=200),
                ]),
                ClassInfo(name="EmailNotificationSender", lineno=230, end_lineno=340, methods=[
                    FunctionInfo(name="send_smtp_email", lineno=240, end_lineno=280),
                ]),
            ],
            functions=[
                FunctionInfo(name="format_date", lineno=350, end_lineno=370),
                FunctionInfo(name="validate_ip", lineno=375, end_lineno=390),
            ],
        )
        m2 = ModuleInfo(path="m2.py", abs_path="", module_name="m2")
        m3 = ModuleInfo(path="m3.py", abs_path="", module_name="m3")
        m4 = ModuleInfo(path="m4.py", abs_path="", module_name="m4")

        model = ProjectModel(name="test_proj", root="")
        model.modules = [m_large, m2, m3, m4]
        model.build_lookup()
        G = nx.DiGraph()

        res = discover_refactoring_recommendations(model, G)
        splits = res["split_module_candidates"]
        self.assertTrue(any(s["module_name"] == "monolith.god_service" for s in splits))

    def test_discover_refactoring_from_result(self):
        """Verify discovery runs cleanly from serialized result dict."""
        sample_res = {
            "project_name": "db_test",
            "modules": [
                {"module_name": f"mod_{i}", "path": f"mod_{i}.py", "lines": 50, "code_lines": 40, "classes": [], "functions": [], "imports": []}
                for i in range(5)
            ],
            "graph_imports": {
                "nodes": [{"id": f"mod_{i}"} for i in range(5)],
                "links": [{"source": "mod_0", "target": "mod_1"}],
            },
        }
        res = discover_refactoring_from_result(sample_res)
        self.assertEqual(res["status"], "ANALYZED")


if __name__ == "__main__":
    unittest.main()
