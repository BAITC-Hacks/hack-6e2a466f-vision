import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.cart import DemoCartAdapter
from backend import main
from backend.chat import (
    extract_quantity,
    find_alternatives,
    is_confirmation,
    is_purchase_intent,
    is_refusal,
    stock_state,
)


class ConfirmationTests(unittest.TestCase):
    def test_only_clear_standalone_confirmation_is_accepted(self):
        self.assertTrue(is_confirmation("Подтверждаю"))
        self.assertTrue(is_confirmation("да."))
        self.assertFalse(is_confirmation("да, а какая цена?"))
        self.assertFalse(is_confirmation("наверное да"))

    def test_refusal_is_separate_from_confirmation(self):
        self.assertTrue(is_refusal("Отмена"))
        self.assertTrue(is_refusal("не надо"))
        self.assertFalse(is_refusal("не уверен"))

    def test_refusal_cancels_pending_without_adding(self):
        with tempfile.TemporaryDirectory() as tmp:
            cart = DemoCartAdapter(Path(tmp) / "cart.sqlite3")
            cart.prepare("session-test-123", "p-1", 1)
            if is_refusal("отмена"):
                cart.cancel("session-test-123")
            self.assertEqual(cart.contents("session-test-123")["total_items"], 0)
            self.assertIsNone(cart.pending("session-test-123"))

    def test_quantity_and_purchase_intent(self):
        self.assertEqual(extract_quantity("Добавь 3 шт"), 3)
        self.assertEqual(extract_quantity("количество: -2"), -2)
        self.assertIsNone(extract_quantity("Добавь в корзину ABC-30"))
        self.assertTrue(is_purchase_intent("Добавь в корзину ABC-30"))


class DemoCartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cart = DemoCartAdapter(Path(self.temp.name) / "cart.sqlite3")
        self.session = "session-test-123"
        self.product = {"id": "p-1", "sku": "A-1", "name": "Кабель"}

    def tearDown(self):
        self.temp.cleanup()

    def test_cart_changes_only_after_confirmation_and_repeat_is_idempotent(self):
        self.cart.prepare(self.session, "p-1", 2)
        self.assertEqual(self.cart.contents(self.session)["total_items"], 0)

        first = self.cart.add_confirmed(self.session, self.product, 2, stock=5)
        self.assertEqual(first["total_items"], 2)

        repeated = self.cart.add_confirmed(self.session, self.product, 2, stock=5)
        self.assertTrue(repeated["already_confirmed"])
        self.assertEqual(repeated["total_items"], 2)

    def test_nonpositive_and_overstock_quantities_are_rejected(self):
        self.cart.prepare(self.session, "p-1", 0)
        with self.assertRaises(ValueError):
            self.cart.add_confirmed(self.session, self.product, 0, stock=5)
        self.cart.prepare(self.session, "p-1", 6)
        with self.assertRaises(ValueError):
            self.cart.add_confirmed(self.session, self.product, 6, stock=5)
        self.assertEqual(self.cart.contents(self.session)["total_items"], 0)

    def test_pending_confirmation_is_bound_to_item_and_quantity(self):
        self.cart.prepare(self.session, "p-1", 2)
        with self.assertRaises(ValueError):
            self.cart.add_confirmed(self.session, {"id": "p-2"}, 2, stock=5)
        with self.assertRaises(ValueError):
            self.cart.add_confirmed(self.session, self.product, 3, stock=5)
        self.assertEqual(self.cart.contents(self.session)["total_items"], 0)


class AlternativesTests(unittest.TestCase):
    def test_only_available_products_with_matching_real_features_are_suggested(self):
        target = {"id": "old", "category": "Кабель", "characteristics": {"сечение": "2.5", "материал": "медь"}, "stock": 0}
        products = [
            {"id": "old", "category": "Кабель", "characteristics": {"сечение": "2.5"}, "stock": 0},
            {"id": "match", "name": "Кабель 2", "category": "Кабель", "characteristics": {"сечение": "2.5", "материал": "медь"}, "stock": 4},
            {"id": "empty", "category": "Кабель", "characteristics": {"сечение": "2.5"}, "stock": 0},
            {"id": "unknown", "category": "Кабель", "characteristics": {"сечение": "2.5"}},
        ]
        alternatives, limitation = find_alternatives(target, products)
        self.assertIsNone(limitation)
        self.assertEqual([row["product"]["id"] for row in alternatives], ["match"])
        self.assertIn("сечение", alternatives[0]["reason"])

    def test_missing_shared_fields_explains_why_no_analogue_can_be_compared(self):
        target = {"id": "old", "category": None, "characteristics": None, "stock": 0}
        alternatives, limitation = find_alternatives(target, [{"id": "candidate", "stock": 3}])
        self.assertEqual(alternatives, [])
        self.assertIsNotNone(limitation)

    def test_unknown_stock_is_not_treated_as_available(self):
        self.assertEqual(stock_state({"stock": None, "availability": None})[0], "unknown")


class FakeCatalog:
    configured = True
    item = {
        "id": "sku-id-1", "sku": "SKU-001", "name": "Кабель ВВГ",
        "description": "Каталожная карточка", "category": "Кабель",
        "price": 100, "stock": 5, "availability": "в наличии",
        "characteristics": {"сечение": "2.5 мм²"}, "certificates": None,
        "url": None, "raw": {"id": "sku-id-1"},
    }

    async def products(self, page=1):
        return ([self.item] if page == 1 else []), {"last_page": 1}

    async def product(self, product_id):
        if product_id != self.item["id"]:
            raise AssertionError("Unexpected product id")
        return dict(self.item)


class ChatEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.adapter = DemoCartAdapter(Path(self.temp.name) / "cart.sqlite3")
        self.catalog_patch = patch.object(main, "catalog", FakeCatalog())
        self.cart_patch = patch.object(main, "cart", self.adapter)
        self.openai_patch = patch.object(main.settings, "openai_api_key", "")
        self.catalog_patch.start()
        self.cart_patch.start()
        self.openai_patch.start()
        from fastapi.testclient import TestClient
        self.client = TestClient(main.app)
        self.session = "test-chat-session-001"

    def tearDown(self):
        self.openai_patch.stop()
        self.cart_patch.stop()
        self.catalog_patch.stop()
        self.temp.cleanup()

    def send(self, message):
        return self.client.post("/api/chat", json={"message": message, "session_id": self.session})

    def test_chat_requires_clear_confirmation_before_add(self):
        response = self.send("Добавь в корзину SKU-001 2 шт")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["pending_confirmation"])
        self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"], 0)

        self.send("да, а есть сертификат?")
        self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"], 0)

        self.send("подтверждаю")
        self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"], 2)
        self.send("подтверждаю")
        self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"], 2)

    def test_cancel_and_invalid_quantities_never_change_cart(self):
        self.send("Добавь в корзину SKU-001 2 шт")
        self.send("отмена")
        self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"], 0)

        too_many = self.send("Добавь в корзину SKU-001 6 шт")
        self.assertIn("доступно 5", too_many.json()["answer"])
        zero = self.send("Добавь в корзину SKU-001 0 шт")
        self.assertIn("положительным", zero.json()["answer"])
        self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"], 0)


if __name__ == "__main__":
    unittest.main()
