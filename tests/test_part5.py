"""Credential-free Demo Day path remains reproducible and clearly labeled."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend import main
from backend.cart import DemoCartAdapter
from backend.demo_data import DEMO_PRODUCTS
from backend.session import SessionStore
from backend.ai import AiError


class OfflineDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        database = Path(self.temp.name) / "state.sqlite3"
        self.patches = [
            patch.object(main.settings, "ekt_api_username", ""),
            patch.object(main.settings, "ekt_api_password", ""),
            patch.object(main.settings, "openai_api_key", ""),
            patch.object(main.settings, "openai_model", ""),
            patch.object(main, "cart", DemoCartAdapter(database)),
            patch.object(main, "sessions", SessionStore(database)),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(main.app)
        self.session = "offline-demo-part5"

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def send(self, message):
        return self.client.post("/api/chat", json={"message": message, "session_id": self.session})

    def test_dataset_covers_required_states(self):
        self.assertEqual(len(DEMO_PRODUCTS), 6)
        self.assertTrue(any(item["stock"] == 0 for item in DEMO_PRODUCTS))
        self.assertTrue(any(item["stock"] > 0 for item in DEMO_PRODUCTS))
        self.assertTrue(any(item["characteristics"] is None for item in DEMO_PRODUCTS))
        self.assertTrue(all(item["raw"]["demo"] for item in DEMO_PRODUCTS))

    def test_embed_widget_assets_are_served(self):
        self.assertEqual(self.client.get("/widget").status_code, 200)
        self.assertEqual(self.client.get("/static/embed.js").status_code, 200)
        self.assertIn("Демо-корзина", self.client.get("/widget").text)

    def test_analogue_and_confirmed_cart_in_offline_demo(self):
        status = self.client.get("/api/status").json()
        self.assertEqual((status["mode"], status["ai_mode"]), ("demo", "offline"))
        self.assertEqual(len(self.client.get("/api/products").json()["products"]), 6)

        answer = self.send("DEMO-OUT")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json()["catalog_mode"], "demo")
        self.assertEqual(answer.json()["ai_mode"], "offline")
        self.assertEqual([row["product"]["sku"] for row in answer.json()["alternatives"]], ["DEMO-ALT"])
        self.assertIn("сечение", answer.json()["answer"])

        prepared = self.send("Добавь DEMO-ALT 2 шт")
        self.assertTrue(prepared.json()["pending_confirmation"])
        self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"], 0)
        ambiguous = self.send("да, а есть сертификат?")
        self.assertEqual(ambiguous.status_code, 200)
        self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"], 0)
        confirmed = self.send("Подтверждаю")
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["catalog_mode"], "demo")
        self.assertEqual(confirmed.json()["cart_url"], "/cart")
        self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"], 2)
        self.send("Подтверждаю")
        self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"], 2)

    def test_demo_stock_limit_and_missing_data(self):
        too_many = self.send("Добавь DEMO-IN 9 шт")
        self.assertIn("доступно 8", too_many.json()["answer"])
        self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"], 0)
        partial = self.send("DEMO-PARTIAL")
        self.assertIn("DEMO-PARTIAL", partial.json()["answer"])
        self.assertIsNone(partial.json()["products"][0]["characteristics"])

    def test_decision_path_from_clarification_to_two_item_list(self):
        extracted = self.client.post("/api/requirements/extract", json={
            "message": "Нужен кабель", "session_id": self.session,
        })
        self.assertEqual(extracted.status_code, 200)
        self.assertEqual(extracted.json()["ai_mode"], "offline")
        self.assertIn("сечение", extracted.json()["requirements"]["clarification_question"])

        draft = extracted.json()["requirements"]
        draft["requirements"] = [{"attribute": "сечение", "value": "2,5 мм²", "required": True}]
        draft["quantity"] = 2
        draft["max_budget"] = 5000
        draft["budget_currency"] = "KZT"
        saved = self.client.put(f"/api/requirements/{self.session}", json={"requirements": draft})
        self.assertEqual(saved.status_code, 200)
        self.assertIsNone(saved.json()["requirements"]["clarification_question"])

        search = self.client.post("/api/requirements/search", json={"session_id": self.session})
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()["mode"], "demo")
        self.assertEqual(search.json()["status"], "found")
        self.assertEqual({row["product"]["id"] for row in search.json()["products"]},
                         {"demo-cable-old", "demo-cable-alt"})
        comparison = self.client.post("/api/requirements/compare", json={
            "session_id": self.session, "product_ids": ["demo-cable-old", "demo-cable-alt"],
        })
        self.assertEqual(comparison.status_code, 200)
        self.assertEqual(comparison.json()["ai_mode"], "offline")
        self.assertEqual([cell["status"] for cell in comparison.json()["matrix"]["rows"][0]["cells"]],
                         ["совпадает", "совпадает"])

        def add(product_id, quantity):
            payload = {"session_id": self.session, "product_id": product_id, "quantity": quantity}
            prepared = self.client.post("/api/cart/prepare", json=payload)
            self.assertEqual(prepared.status_code, 200)
            self.assertEqual(prepared.json()["quantity"], quantity)
            self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"],
                             0 if product_id == "demo-cable-alt" else 2)
            confirmed = self.client.post("/api/cart/confirm", json=payload)
            self.assertEqual(confirmed.status_code, 200)
            self.assertFalse(confirmed.json()["already_confirmed"])
            self.assertTrue(self.client.post("/api/cart/confirm", json=payload).json()["already_confirmed"])

        add("demo-cable-alt", 2)
        draft.update(product_terms=["автомат"], requirements=[
            {"attribute": "номинальный ток", "value": "16 А", "required": True},
        ])
        self.assertEqual(self.client.put(f"/api/requirements/{self.session}", json={"requirements": draft}).status_code, 200)
        breaker = self.client.post("/api/requirements/search", json={"session_id": self.session}).json()
        self.assertIn("demo-breaker", [row["product"]["id"] for row in breaker["products"]])
        add("demo-breaker", 2)
        cart = self.client.get(f"/api/cart/{self.session}").json()
        self.assertEqual(cart["total_items"], 4)
        self.assertEqual(cart["pricing"]["total"], "4280")
        self.assertEqual(cart["pricing"]["currency"], "KZT")
        self.assertEqual(self.client.patch(f"/api/cart/{self.session}/items/demo-breaker",
                                           json={"quantity": 9}).status_code, 409)
        self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"], 4)

    def test_openai_failure_keeps_saved_requirements_and_cart_unchanged(self):
        with patch.object(main.settings, "openai_api_key", "mock-key"), \
             patch.object(main.settings, "openai_model", "mock-model"), \
             patch.object(main, "extract_with_openai", AsyncMock(side_effect=AiError("Модель недоступна"))):
            response = self.client.post("/api/requirements/extract", json={
                "message": "Нужен кабель", "session_id": self.session,
            })
        self.assertEqual(response.status_code, 502)
        self.assertIsNone(self.client.get(f"/api/requirements/{self.session}").json()["requirements"])
        self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"], 0)

    def test_demo_comparison_shows_match_conflict_and_unknown(self):
        draft = {
            "intent": "compare", "product_terms": ["автомат"], "article": None,
            "requirements": [{"attribute": "номинальный ток", "value": "16 А", "required": True}],
            "quantity": None, "max_budget": None, "budget_currency": None,
            "clarification_question": None,
        }
        self.assertEqual(self.client.put(f"/api/requirements/{self.session}",
                                         json={"requirements": draft}).status_code, 200)
        found = self.client.post("/api/requirements/search", json={"session_id": self.session}).json()
        self.assertEqual(len(found["products"]), 3)
        ids = ["demo-breaker", "demo-breaker-conflict", "demo-breaker-unknown"]
        comparison = self.client.post("/api/requirements/compare", json={
            "session_id": self.session, "product_ids": ids,
        })
        self.assertEqual(comparison.status_code, 200)
        statuses = [cell["status"] for cell in comparison.json()["matrix"]["rows"][0]["cells"]]
        self.assertEqual(statuses, ["совпадает", "противоречит", "нет данных"])


if __name__ == "__main__":
    unittest.main()
