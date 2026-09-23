import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend import main
from backend.ai import AiError
from backend.catalog import normalize_product
from backend.comparison import (
    ComparisonChoice, build_comparison, explain_comparison, product_page_url, sentence_options, validate_choice,
)
from backend.requirements import ShoppingRequirements
from backend.session import SessionStore


def requirements(intent="compare"):
    return ShoppingRequirements.model_validate({
        "intent": intent, "product_terms": ["кабель"], "article": None,
        "requirements": [{"attribute": "сечение", "value": "2,5 мм²", "required": True}],
        "quantity": None, "max_budget": None, "budget_currency": None,
        "clarification_question": None,
    })


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.products = [
            normalize_product({"id": "one", "name": "Кабель A", "properties": {"сечение": "2,5 мм²"}, "quantity": 0}),
            normalize_product({"id": "two", "name": "Кабель B", "properties": {"сечение": "4 мм²"}, "quantity": 3}),
            normalize_product({"id": "three", "name": "Кабель C", "properties": {"сечение": ""}, "quantity": 3}),
        ]

    def test_match_conflict_and_missing_value_are_distinct(self):
        matrix = build_comparison(requirements(), self.products, "demo")
        self.assertEqual([cell["status"] for cell in matrix["rows"][0]["cells"]],
                         ["совпадает", "противоречит", "нет данных"])
        self.assertIsNone(matrix["rows"][0]["cells"][2]["actual"])
        self.assertTrue(all(column["url"].startswith("/api/products/") for column in matrix["columns"]))
        self.assertEqual(matrix["alternative_candidate_ids"], [])
        self.assertEqual([column["availability_display"] for column in matrix["columns"]],
                         ["0 шт.", "3 шт.", "3 шт."])
        self.assertTrue(all(column["price_display"] == "нет данных" for column in matrix["columns"]))

    def test_price_and_availability_are_part_of_verified_columns(self):
        priced = normalize_product({"id": "priced", "name": "Кабель с ценой", "price": 1200,
                                    "currency": "KZT", "quantity": 2, "properties": {"сечение": "2,5 мм²"}})
        unknown = normalize_product({"id": "unknown", "name": "Кабель без цены",
                                     "properties": {"сечение": "2,5 мм²"}})
        matrix = build_comparison(requirements(), [priced, unknown], "demo")
        self.assertEqual(matrix["columns"][0]["price_display"], "1200 KZT")
        self.assertEqual(matrix["columns"][0]["availability_display"], "2 шт.")
        self.assertEqual(matrix["columns"][1]["price_display"], "нет данных")
        self.assertEqual(matrix["columns"][1]["availability_display"], "нет данных")
        self.assertIn("price_display", [item["id"] for item in sentence_options(matrix)])

    def test_alternative_requires_confirmed_required_fields_and_stock(self):
        replacement = normalize_product({"id": "four", "name": "Кабель D", "properties": {"сечение": "2,5 мм²"}, "quantity": 2})
        matrix = build_comparison(requirements("alternative"), [self.products[0], self.products[1], self.products[2], replacement], "demo")
        self.assertEqual(matrix["alternative_candidate_ids"], ["four"])
        self.assertIn("не подтверждена", matrix["alternative_note"])

    def test_model_can_only_select_verified_sentences_and_ids(self):
        matrix = build_comparison(requirements(), self.products[:2], "demo")
        options = sentence_options(matrix)
        with self.assertRaises(AiError):
            validate_choice(ComparisonChoice(sentence_ids=["r0"], product_ids=["invented"]), matrix, options)
        with self.assertRaises(AiError):
            validate_choice(ComparisonChoice(sentence_ids=["invented"], product_ids=["one"]), matrix, options)

    def test_product_link_allows_only_ekt_https(self):
        product = {"id": 42, "url": "https://ekt.kz/catalog/item-42"}
        self.assertEqual(product_page_url(product, "live"), product["url"])
        product["url"] = "https://ekt.kz.evil.example/item"
        self.assertEqual(product_page_url(product, "live"), "/api/products/42")

    def test_openai_receives_matrix_but_cannot_write_new_facts(self):
        matrix = build_comparison(requirements(), self.products[:2], "demo")
        choice = ComparisonChoice(sentence_ids=["r0"], product_ids=["one", "two"])
        sdk = SimpleNamespace(responses=SimpleNamespace(parse=AsyncMock(return_value=SimpleNamespace(status="completed", output_parsed=choice))))
        with patch("backend.comparison.settings.openai_api_key", "test-key"), patch("backend.comparison.settings.openai_model", "test-model"):
            explanation, mode = asyncio.run(explain_comparison(matrix, client=sdk))
        kwargs = sdk.responses.parse.call_args.kwargs
        self.assertEqual(mode, "openai")
        self.assertIn("противоречит", explanation)
        self.assertEqual(kwargs["text_format"], ComparisonChoice)
        self.assertFalse(kwargs["store"])
        supplied = json.loads(kwargs["input"])["verified_matrix"]
        self.assertEqual(supplied["rows"], matrix["rows"])
        self.assertNotIn("url", supplied["columns"][0])

    def test_openai_refusal_keeps_comparison_unexplained(self):
        matrix = build_comparison(requirements(), self.products[:2], "demo")
        sdk = SimpleNamespace(responses=SimpleNamespace(parse=AsyncMock(return_value=SimpleNamespace(
            status="incomplete", output_parsed=None,
        ))))
        with patch("backend.comparison.settings.openai_api_key", "test-key"), \
             patch("backend.comparison.settings.openai_model", "test-model"):
            with self.assertRaisesRegex(AiError, "не вернула объяснение"):
                asyncio.run(explain_comparison(matrix, client=sdk))


class ComparisonRouteTests(unittest.TestCase):
    def test_only_found_ids_can_be_compared(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.sqlite3")
            session_id = "comparison-session-123"
            store.save_requirements(session_id, requirements().model_dump())
            with patch.object(main, "sessions", store), patch.object(main, "catalog", SimpleNamespace(configured=False)):
                with patch.object(main.settings, "openai_api_key", ""), patch.object(main.settings, "openai_model", ""):
                    client = TestClient(main.app)
                    bad = client.post("/api/requirements/compare", json={"session_id": session_id, "product_ids": ["demo-cable-old", "invented"]})
                    good = client.post("/api/requirements/compare", json={"session_id": session_id, "product_ids": ["demo-cable-old", "demo-cable-alt"]})
            self.assertEqual(bad.status_code, 422)
            self.assertEqual(good.status_code, 200)
            self.assertEqual(len(good.json()["matrix"]["columns"]), 2)
            self.assertEqual(good.json()["ai_mode"], "offline")


if __name__ == "__main__":
    unittest.main()
