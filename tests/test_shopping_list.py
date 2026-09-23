import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import main
from backend.cart import DemoCartAdapter
from backend.catalog import normalize_product
from backend.requirements import ShoppingRequirements
from backend.session import SessionStore
from backend.shopping_list import cheaper_candidates, summarize_cart


def requirements(terms=None, budget=5000, currency="тг", attributes=None):
    return ShoppingRequirements.model_validate({
        "intent": "find", "product_terms": terms or ["кабель"], "article": None,
        "requirements": attributes or [], "quantity": 2, "max_budget": budget,
        "budget_currency": currency, "clarification_question": None,
    })


def cart(*items):
    return {"items": [{"product": product, "quantity": quantity} for product, quantity in items]}


class ShoppingListMathTests(unittest.TestCase):
    def test_two_items_exact_total_and_budget_difference(self):
        result = summarize_cart(cart(({"id": "a", "name": "A", "price": "1250.50", "currency": "KZT"}, 2),
                                     ({"id": "b", "name": "B", "price": 890, "currency": "₸"}, 1)), requirements(budget=3000))
        self.assertTrue(result["complete"])
        self.assertEqual(result["total"], "3391")
        self.assertEqual(result["budget"]["status"], "over")
        self.assertEqual(result["budget"]["difference"], "-391")
        self.assertEqual([line["line_total"] for line in result["lines"]], ["2501", "890"])

    def test_mixed_and_unknown_currency_or_price_do_not_create_total(self):
        mixed = summarize_cart(cart(({"id": "a", "price": 100, "currency": "KZT"}, 1),
                                    ({"id": "b", "price": 5, "currency": "USD"}, 1)), requirements())
        self.assertIsNone(mixed["total"])
        self.assertEqual(mixed["known_subtotals"], {"KZT": "100", "USD": "5"})
        self.assertEqual(mixed["budget"]["status"], "unknown")
        unknown = summarize_cart(cart(({"id": "a", "price": 100}, 1),
                                      ({"id": "b", "price": None, "currency": "KZT"}, 1)), requirements())
        self.assertIsNone(unknown["total"])
        self.assertTrue(all(line["line_total"] is None for line in unknown["lines"]))
        self.assertIn("валюта", unknown["lines"][0]["reason"].casefold())
        self.assertIn("Цена", unknown["lines"][1]["reason"])
        partial = summarize_cart(cart(({"id": "a", "price": 100, "currency": "KZT"}, 2),
                                      ({"id": "b", "price": None, "currency": "KZT"}, 1)), requirements())
        self.assertFalse(partial["complete"])
        self.assertIsNone(partial["total"])
        self.assertEqual(partial["known_subtotals"], {"KZT": "200"})

    def test_budget_currency_must_be_explicit_and_match(self):
        item = cart(({"id": "a", "price": 100, "currency": "KZT"}, 1))
        self.assertEqual(summarize_cart(item, requirements(currency=None))["budget"]["status"], "unknown")
        self.assertEqual(summarize_cart(item, requirements(currency="USD"))["budget"]["status"], "unknown")
        self.assertEqual(summarize_cart(item, requirements(budget=100))["budget"]["status"], "within")

    def test_cheaper_option_requires_price_stock_and_required_match(self):
        required = [{"attribute": "сечение", "value": "2,5 мм²", "required": True}]
        current = normalize_product({"id": "old", "name": "Кабель старый", "price": 2000, "currency": "KZT", "quantity": 2, "properties": {"сечение": "2,5 мм²"}})
        valid = normalize_product({"id": "new", "name": "Кабель новый", "price": 1500, "currency": "KZT", "quantity": 3, "properties": {"сечение": "2,5 мм²"}})
        wrong = normalize_product({"id": "wrong", "name": "Кабель другой", "price": 1000, "currency": "KZT", "quantity": 4, "properties": {"сечение": "4 мм²"}})
        no_stock = normalize_product({"id": "unknown", "name": "Кабель неизвестный", "price": 900, "currency": "KZT", "properties": {"сечение": "2,5 мм²"}})
        rows = [{"product": product} for product in [current, valid, wrong, no_stock]]
        options = cheaper_candidates(cart((current, 2)), requirements(attributes=required), rows)
        self.assertEqual([option["product_id"] for option in options], ["new"])
        self.assertEqual(options[0]["saving"], "1000")


class ShoppingListRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        database = Path(self.temp.name) / "cart.sqlite3"
        self.adapter = DemoCartAdapter(database)
        self.store = SessionStore(database)
        self.patches = [patch.object(main, "cart", self.adapter), patch.object(main, "sessions", self.store),
                        patch.object(main, "catalog", SimpleNamespace(configured=False))]
        for item in self.patches:
            item.start()
        self.client = TestClient(main.app)
        self.session = "shopping-list-session-123"
        self.store.save_requirements(self.session, requirements().model_dump())

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def action(self, path, product_id, quantity):
        return self.client.post(path, json={"session_id": self.session, "product_id": product_id, "quantity": quantity})

    def test_two_positions_double_confirmation_change_and_remove(self):
        self.assertEqual(self.action("/api/cart/prepare", "demo-cable-alt", 2).status_code, 200)
        self.assertEqual(self.adapter.contents(self.session)["total_items"], 0)
        first = self.action("/api/cart/confirm", "demo-cable-alt", 2)
        self.assertEqual(first.status_code, 200)
        repeated = self.action("/api/cart/confirm", "demo-cable-alt", 2)
        self.assertTrue(repeated.json()["already_confirmed"])
        self.assertEqual(self.adapter.contents(self.session)["total_items"], 2)
        self.assertEqual(self.action("/api/cart/confirm", "demo-cable-alt", 3).status_code, 409)
        self.assertEqual(self.action("/api/cart/prepare", "demo-cable-alt", 5).status_code, 409)

        self.store.save_requirements(self.session, requirements(terms=["автомат"]).model_dump())
        self.assertEqual(self.action("/api/cart/prepare", "demo-breaker", 2).status_code, 200)
        self.assertEqual(self.action("/api/cart/confirm", "demo-breaker", 2).status_code, 200)
        viewed = self.client.get(f"/api/cart/{self.session}").json()
        self.assertEqual(viewed["total_items"], 4)
        self.assertEqual(viewed["pricing"]["total"], "4280")
        self.assertEqual(viewed["pricing"]["budget"]["status"], "within")

        over = self.client.patch(f"/api/cart/{self.session}/items/demo-cable-alt", json={"quantity": 7})
        self.assertEqual(over.status_code, 409)
        self.assertEqual(self.adapter.contents(self.session)["total_items"], 4)
        changed = self.client.patch(f"/api/cart/{self.session}/items/demo-cable-alt", json={"quantity": 3})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["total_items"], 5)
        self.client.delete(f"/api/cart/{self.session}/items/demo-breaker")
        self.assertEqual(self.adapter.contents(self.session)["total_items"], 3)

    def test_unknown_stock_blocks_prepare_and_cart_remains_empty(self):
        unknown = normalize_product({"id": "demo-cable-alt", "name": "Кабель", "price": 1250, "currency": "KZT"})
        with patch.object(main, "demo_product", return_value=unknown):
            response = self.action("/api/cart/prepare", "demo-cable-alt", 1)
        self.assertEqual(response.status_code, 409)
        self.assertIn("остаток неизвестен", response.json()["detail"])
        self.assertEqual(self.adapter.contents(self.session)["total_items"], 0)

    def test_cancel_and_overstock_do_not_add(self):
        self.assertEqual(self.action("/api/cart/prepare", "demo-cable-alt", 7).status_code, 409)
        self.assertEqual(self.action("/api/cart/prepare", "demo-cable-alt", 1).status_code, 200)
        self.client.post("/api/cart/cancel", json={"session_id": self.session})
        self.assertEqual(self.action("/api/cart/confirm", "demo-cable-alt", 1).status_code, 409)
        self.assertEqual(self.adapter.contents(self.session)["total_items"], 0)

    def test_fresh_stock_can_block_confirmation_and_parallel_double_click(self):
        self.assertEqual(self.action("/api/cart/prepare", "demo-cable-alt", 2).status_code, 200)
        unavailable = normalize_product({"id": "demo-cable-alt", "name": "Кабель", "price": 1250, "currency": "KZT", "quantity": 0})
        with patch.object(main, "demo_product", return_value=unavailable):
            blocked = self.action("/api/cart/confirm", "demo-cable-alt", 2)
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(self.adapter.contents(self.session)["total_items"], 0)

        product = main.demo_product("demo-cable-alt")
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(lambda _: self.adapter.add_confirmed(self.session, product, 2, 6), range(2)))
        self.assertEqual(self.adapter.contents(self.session)["total_items"], 2)
        self.assertEqual(sum(bool(outcome.get("already_confirmed")) for outcome in outcomes), 1)

    def test_budget_options_endpoint_only_uses_verified_cheaper_candidate(self):
        old = normalize_product({"id": "old", "name": "Кабель дорогой", "price": 2000, "currency": "KZT", "quantity": 3, "properties": {"сечение": "2,5 мм²"}})
        new = normalize_product({"id": "new", "name": "Кабель дешевле", "price": 1500, "currency": "KZT", "quantity": 3, "properties": {"сечение": "2,5 мм²"}})
        wrong = normalize_product({"id": "wrong", "name": "Кабель несовпадающий", "price": 1000, "currency": "KZT", "quantity": 3, "properties": {"сечение": "4 мм²"}})
        desired = requirements(attributes=[{"attribute": "сечение", "value": "2,5 мм²", "required": True}])
        self.store.save_requirements(self.session, desired.model_dump())
        self.adapter.prepare(self.session, "old", 2)
        self.adapter.add_confirmed(self.session, old, 2, 3)
        with patch.object(main, "DEMO_PRODUCTS", [old, new, wrong]):
            response = self.client.get(f"/api/cart/{self.session}/budget-options")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([option["product_id"] for option in response.json()["options"]], ["new"])


if __name__ == "__main__":
    unittest.main()
