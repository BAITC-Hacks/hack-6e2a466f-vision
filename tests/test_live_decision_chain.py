"""Full live-mode route chain with catalog and OpenAI doubles; no external transmission."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend import main
from backend.cart import DemoCartAdapter
from backend.catalog import CatalogIndex, normalize_product
from backend.requirements import ShoppingRequirements
from backend.session import SessionStore


class LiveDecisionChainTests(unittest.TestCase):
    def test_extract_edit_search_compare_and_confirm(self):
        first = normalize_product({
            "id": 101, "article": "TEST-16", "name": "Автомат 16 А", "quantity": 4,
            "price": 1000, "currency": "KZT", "properties": {"NOMINALNYY_TOK": "16 А"},
        })
        second = normalize_product({
            "id": 102, "article": "TEST-20", "name": "Автомат 20 А", "quantity": 3,
            "price": 900, "currency": "KZT", "properties": {"NOMINALNYY_TOK": "20 А"},
        })

        class CatalogDouble:
            configured = True

            async def search_index(self, page_limit=5, refresh=False):
                return CatalogIndex([first, second], 1, True)

            async def search_detail(self, product_id, refresh=False):
                return {"101": first, "102": second}[product_id]

            async def product(self, product_id):
                return {"101": first, "102": second}[product_id]

        extracted = ShoppingRequirements(
            intent="find", product_terms=["автомат"], article=None, requirements=[],
            quantity=None, max_budget=None, budget_currency=None,
            clarification_question="Какой номинальный ток (А) нужен?",
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite3"
            with patch.object(main, "catalog", CatalogDouble()), \
                 patch.object(main, "cart", DemoCartAdapter(database)), \
                 patch.object(main, "sessions", SessionStore(database)), \
                 patch.object(main.settings, "openai_api_key", "mock-key"), \
                 patch.object(main.settings, "openai_model", "mock-model"), \
                 patch.object(main, "extract_with_openai", AsyncMock(return_value=extracted)), \
                 patch.object(main, "explain_comparison", AsyncMock(return_value=("Проверенное сравнение.", "openai"))):
                client = TestClient(main.app)
                session = "live-chain-test-session"
                parsed = client.post("/api/requirements/extract", json={
                    "session_id": session, "message": "Нужен автомат",
                })
                self.assertEqual(parsed.status_code, 200)
                self.assertEqual(parsed.json()["ai_mode"], "openai")
                draft = parsed.json()["requirements"]
                draft["requirements"] = [{"attribute": "номинальный ток", "value": "16 А", "required": True}]
                draft["quantity"] = 2
                self.assertEqual(client.put(f"/api/requirements/{session}",
                                            json={"requirements": draft}).status_code, 200)
                found = client.post("/api/requirements/search", json={"session_id": session}).json()
                self.assertEqual(found["mode"], "live")
                self.assertEqual({str(row["product"]["id"]) for row in found["products"]}, {"101", "102"})
                compared = client.post("/api/requirements/compare", json={
                    "session_id": session, "product_ids": ["101", "102"],
                })
                self.assertEqual(compared.status_code, 200)
                self.assertEqual(compared.json()["ai_mode"], "openai")
                self.assertEqual([cell["status"] for cell in compared.json()["matrix"]["rows"][0]["cells"]],
                                 ["совпадает", "противоречит"])
                self.assertEqual(compared.json()["matrix"]["columns"][0]["price_display"], "1000 KZT")

                action = {"session_id": session, "product_id": "101", "quantity": 2}
                self.assertEqual(client.post("/api/cart/prepare", json=action).status_code, 200)
                self.assertEqual(client.get(f"/api/cart/{session}").json()["total_items"], 0)
                self.assertEqual(client.post("/api/cart/confirm", json=action).status_code, 200)
                self.assertEqual(client.get(f"/api/cart/{session}").json()["total_items"], 2)
                self.assertTrue(client.post("/api/cart/confirm", json=action).json()["already_confirmed"])


if __name__ == "__main__":
    unittest.main()
