import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import main
from backend.ai import AiError
from backend.requirements import (
    ShoppingRequirements, extract_with_openai, offline_extract, validate_extracted,
)
from backend.session import SessionStore


def sample(**overrides):
    data = {
        "intent": "find", "product_terms": ["автомат"], "article": None,
        "requirements": [{"attribute": "номинальный ток", "value": "16 А", "required": True}],
        "quantity": 2, "max_budget": 5000, "budget_currency": "тг",
        "clarification_question": None,
    }
    data.update(overrides)
    return ShoppingRequirements.model_validate(data)


class ExtractionTests(unittest.TestCase):
    def test_strict_schema_and_stated_values(self):
        message = "Нужен автомат на 16 А, 2 шт, до 5000 тг"
        result = validate_extracted(sample(), message, [])
        self.assertEqual(result.quantity, 2)
        self.assertEqual(result.requirements[0].value, "16 А")
        with self.assertRaises(ValidationError):
            ShoppingRequirements.model_validate({**sample().model_dump(), "intent": "invented"})
        with self.assertRaises(ValidationError):
            ShoppingRequirements.model_validate({"intent": "find"})
        with self.assertRaises(AiError):
            validate_extracted(sample(article="FAKE-ARTICLE"), message, [])

    def test_negation_is_not_lost(self):
        message = "Нужен автомат, но не 16 А"
        positive = sample(quantity=None, max_budget=None, budget_currency=None)
        with self.assertRaisesRegex(AiError, "отрицание"):
            validate_extracted(positive, message, [])
        negative = sample(
            requirements=[{"attribute": "номинальный ток", "value": "не 16 А", "required": True}],
            quantity=None, max_budget=None, budget_currency=None,
        )
        self.assertEqual(validate_extracted(negative, message, []).requirements[0].value, "не 16 А")

    def test_one_checkable_question_for_incomplete_request(self):
        message = "Нужен автомат для дома"
        offline = offline_extract(message)
        self.assertEqual(offline.requirements, [])
        self.assertIn("номинальный ток", offline.clarification_question)
        proposed = sample(
            requirements=[], quantity=None, max_budget=None, budget_currency=None,
            clarification_question="Какой номинальный ток нужен?",
        )
        self.assertIsNotNone(validate_extracted(proposed, message, []).clarification_question)
        irrelevant = proposed.model_copy(update={"clarification_question": "Куда доставить товар?"})
        self.assertIn("номинальный ток", validate_extracted(irrelevant, message, []).clarification_question)
        no_question = proposed.model_copy(update={"clarification_question": None})
        self.assertIn("номинальный ток", validate_extracted(no_question, message, []).clarification_question)
        self.assertIsNone(validate_extracted(no_question, message, [], ["тип установки"]).clarification_question)

    def test_mock_sdk_parse_and_error_do_not_fall_back(self):
        reply = sample()
        sdk = SimpleNamespace(responses=SimpleNamespace(parse=AsyncMock(return_value=SimpleNamespace(status="completed", output_parsed=reply))))
        with patch.object(main.settings, "openai_api_key", "test-key"), patch.object(main.settings, "openai_model", "test-model"):
            result = asyncio.run(extract_with_openai("Нужен автомат на 16 А, 2 шт, до 5000 тг", [], client=sdk))
            args = sdk.responses.parse.call_args.kwargs
            self.assertEqual(result.quantity, 2)
            self.assertEqual(args["text_format"], ShoppingRequirements)
            self.assertFalse(args["store"])
            self.assertNotIn("raw", json.loads(args["input"]))
            sdk.responses.parse.return_value = SimpleNamespace(status="incomplete", output_parsed=reply)
            with self.assertRaises(AiError):
                asyncio.run(extract_with_openai("Нужен автомат", [], client=sdk))


class RequirementRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self.temp.name) / "sessions.sqlite3")
        self.store_patch = patch.object(main, "sessions", self.store)
        self.store_patch.start()
        self.client = TestClient(main.app)
        self.session = "requirements-session-123"

    def tearDown(self):
        self.store_patch.stop()
        self.temp.cleanup()

    def test_edit_and_reset_draft_without_catalog_search(self):
        with patch.object(main.settings, "openai_api_key", ""), patch.object(main.settings, "openai_model", ""):
            response = self.client.post("/api/requirements/extract", json={"message": "Нужен автомат для дома", "session_id": self.session})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ai_mode"], "offline")
        self.assertIn("номинальный ток", response.json()["requirements"]["clarification_question"])
        self.assertEqual(self.store.recent(self.session), [])

        edited = response.json()["requirements"]
        edited["requirements"] = [{"attribute": "номинальный ток", "value": "16 А", "required": True}]
        saved = self.client.put(f"/api/requirements/{self.session}", json={"requirements": edited})
        self.assertEqual(saved.status_code, 200)
        self.assertIsNone(saved.json()["requirements"]["clarification_question"])
        self.assertEqual(self.client.get(f"/api/requirements/{self.session}").json()["requirements"]["requirements"][0]["value"], "16 А")
        edited["requirements"][0]["attribute"] = "неизвестное поле"
        self.assertEqual(self.client.put(f"/api/requirements/{self.session}", json={"requirements": edited}).status_code, 422)
        self.client.delete(f"/api/session/{self.session}")
        self.assertIsNone(self.client.get(f"/api/requirements/{self.session}").json()["requirements"])

    def test_openai_error_is_visible_and_does_not_store_draft(self):
        with patch.object(main.settings, "openai_api_key", "test-key"), patch.object(main.settings, "openai_model", "test-model"):
            with patch.object(main, "extract_with_openai", AsyncMock(side_effect=AiError("Модель не ответила."))):
                response = self.client.post("/api/requirements/extract", json={"message": "Нужен автомат", "session_id": self.session})
        self.assertEqual(response.status_code, 502)
        self.assertIn("Модель не ответила", response.json()["detail"])
        self.assertIsNone(self.store.get_requirements(self.session))


if __name__ == "__main__":
    unittest.main()
