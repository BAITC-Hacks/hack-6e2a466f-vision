"""Credential-free Demo Day path remains reproducible and clearly labeled."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import main
from backend.cart import DemoCartAdapter
from backend.demo_data import DEMO_PRODUCTS
from backend.session import SessionStore


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
        self.assertEqual(len(DEMO_PRODUCTS), 4)
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
        self.assertEqual(len(self.client.get("/api/products").json()["products"]), 4)

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


if __name__ == "__main__":
    unittest.main()
