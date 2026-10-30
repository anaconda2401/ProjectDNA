"""
test_fixtures.py
Tests ProjectDNA analysis against realistic multi-package Python repository layouts.
"""

import os
import shutil
import tempfile
import unittest

from analyzer.pipeline import run_pipeline
from analyzer.impact_analyzer import analyze_change_impact_from_result
from analyzer.module_discovery import discover_refactoring_from_result
from analyzer.risk_analyzer import analyze_architecture_risks_from_result


class TestRealisticFixtures(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="projectdna_repo_fixture_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_ecommerce_micro_architecture_fixture(self):
        """
        Builds a realistic 7-module Python package layout:
          - domain/models.py
          - domain/events.py
          - storage/repository.py
          - services/order_service.py
          - api/controllers.py
          - utils/date_helper.py
          - tests/test_orders.py
        Verifies end-to-end integration across all 4 phases.
        """
        # Create directories
        for d in ["domain", "storage", "services", "api", "utils", "tests"]:
            os.makedirs(os.path.join(self.tmp_dir, d), exist_ok=True)

        # 1. domain/models.py
        with open(os.path.join(self.tmp_dir, "domain", "models.py"), "w", encoding="utf-8") as f:
            f.write("""
class OrderItem:
    def __init__(self, sku: str, price: float):
        self.sku = sku
        self.price = price

class Order:
    def __init__(self, order_id: str):
        self.order_id = order_id
        self.items = []
        self.status = 'PENDING'

    def add_item(self, item: OrderItem):
        self.items.append(item)
""")

        # 2. domain/events.py
        with open(os.path.join(self.tmp_dir, "domain", "events.py"), "w", encoding="utf-8") as f:
            f.write("""
from domain.models import Order

class OrderPlacedEvent:
    def __init__(self, order: Order):
        self.order_id = order.order_id
""")

        # 3. storage/repository.py
        with open(os.path.join(self.tmp_dir, "storage", "repository.py"), "w", encoding="utf-8") as f:
            f.write("""
from domain.models import Order

class OrderRepository:
    def __init__(self):
        self._db = {}

    def save(self, order: Order):
        self._db[order.order_id] = order

    def get(self, order_id: str) -> Order:
        return self._db.get(order_id)
""")

        # 4. services/order_service.py
        with open(os.path.join(self.tmp_dir, "services", "order_service.py"), "w", encoding="utf-8") as f:
            f.write("""
from domain.models import Order, OrderItem
from domain.events import OrderPlacedEvent
from storage.repository import OrderRepository
from utils.date_helper import current_timestamp

class OrderService:
    def __init__(self, repo: OrderRepository):
        self.repo = repo

    def create_order(self, order_id: str, items_data: list):
        order = Order(order_id)
        for item in items_data:
            order.add_item(OrderItem(item['sku'], item['price']))
        self.repo.save(order)
        event = OrderPlacedEvent(order)
        ts = current_timestamp()
        return order
""")

        # 5. api/controllers.py
        with open(os.path.join(self.tmp_dir, "api", "controllers.py"), "w", encoding="utf-8") as f:
            f.write("""
from services.order_service import OrderService
from storage.repository import OrderRepository

class OrderController:
    def __init__(self):
        self.service = OrderService(OrderRepository())

    def post_order(self, payload: dict):
        return self.service.create_order(payload['id'], payload['items'])
""")

        # 6. utils/date_helper.py
        with open(os.path.join(self.tmp_dir, "utils", "date_helper.py"), "w", encoding="utf-8") as f:
            f.write("""
import time

def current_timestamp() -> float:
    return time.time()
""")

        # 7. tests/test_orders.py
        with open(os.path.join(self.tmp_dir, "tests", "test_orders.py"), "w", encoding="utf-8") as f:
            f.write("""
import unittest
from services.order_service import OrderService
from storage.repository import OrderRepository

class TestOrders(unittest.TestCase):
    def test_create(self):
        svc = OrderService(OrderRepository())
        order = svc.create_order('1', [{'sku': 'A', 'price': 10.0}])
        self.assertEqual(order.order_id, '1')
""")

        # Run pipeline
        res = run_pipeline(self.tmp_dir, "ECommerceApp")

        self.assertEqual(res["summary"]["total_files"], 7)
        self.assertEqual(res["summary"]["error_files"], 0)

        # Phase 1 verification: Change impact on domain.models
        impact_models = analyze_change_impact_from_result(res, "domain.models")
        self.assertTrue(impact_models["found"])
        # Direct dependents should include events, repository, order_service
        self.assertIn("domain.events", impact_models["direct_dependents"])
        self.assertIn("storage.repository", impact_models["direct_dependents"])
        self.assertIn("services.order_service", impact_models["direct_dependents"])
        # Transitive dependents should include api.controllers and tests.test_orders
        self.assertIn("api.controllers", impact_models["transitive_dependents"])
        self.assertIn("tests.test_orders", impact_models["transitive_dependents"])
        self.assertIn("tests.test_orders", impact_models["affected_tests"])

        # Phase 3 verification: Module discovery
        rec = discover_refactoring_from_result(res)
        self.assertEqual(rec["status"], "ANALYZED")
        # Should cluster domain/services/storage components
        self.assertGreaterEqual(len(rec["candidate_groups"]), 1)

        # Phase 4 verification: Architecture risk scoring
        risks = analyze_architecture_risks_from_result(res)
        self.assertEqual(risks["status"], "ANALYZED")
        self.assertEqual(risks["project_risk"]["cycles_count"], 0)
        # Because tests cover services and models, risk should be low/medium
        self.assertIn(risks["project_risk"]["risk_level"], ["LOW", "MEDIUM"])


if __name__ == "__main__":
    unittest.main()
