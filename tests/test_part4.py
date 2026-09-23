import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import httpx2
from fastapi.testclient import TestClient
from openai import APITimeoutError
from pydantic import ValidationError

from backend import main
from backend.ai import AIReply, AiError, answer_with_openai, compose_grounded_answer, validate_grounding
from backend.cart import DemoCartAdapter
from backend.catalog import CatalogClient, CatalogError, normalize_product
from backend.session import SessionStore


class CatalogMockTests(unittest.TestCase):
    def test_basic_auth_page_and_detail_are_normalized(self):
        seen = []

        def handler(request):
            seen.append((request.url.path, request.url.params.get("page"), request.headers.get("Authorization", "").startswith("Basic ")))
            if request.url.path.endswith("/detail"):
                return httpx.Response(200, json={"data": {"id": 515291, "article": "A-1", "name": "Выключатель", "quantity": 4, "properties": {"KATEGORIYA": "Автоматы", "TIP_USTANOVKI": "DIN"}}})
            return httpx.Response(200, json={"data": [{"id": 515291, "article": "A-1", "name": "Выключатель"}], "page": 2, "count": 1})

        client = CatalogClient(transport=httpx.MockTransport(handler))
        with patch.object(main.settings, "ekt_api_username", "demo"), patch.object(main.settings, "ekt_api_password", "secret"):
            products, pagination = asyncio.run(client.products(2))
            detail = asyncio.run(client.product("515291"))
        self.assertEqual(products[0]["sku"], "A-1")
        self.assertEqual(pagination["page"], 2)
        self.assertEqual(detail["stock"], 4)
        self.assertEqual(detail["category"], "Автоматы")
        self.assertEqual(seen, [("/api/products", "2", True), ("/api/products/detail", None, True)])

    def test_timeout_auth_and_invalid_json_fail_safely(self):
        for response in (httpx.Response(401), httpx.Response(200, text="not-json")):
            client = CatalogClient(transport=httpx.MockTransport(lambda request, response=response: response))
            with patch.object(main.settings, "ekt_api_username", "demo"), patch.object(main.settings, "ekt_api_password", "secret"):
                with self.assertRaises(CatalogError):
                    asyncio.run(client.products())

        def timeout(request):
            raise httpx.ReadTimeout("timeout", request=request)

        client = CatalogClient(transport=httpx.MockTransport(timeout))
        with patch.object(main.settings, "ekt_api_username", "demo"), patch.object(main.settings, "ekt_api_password", "secret"):
            with self.assertRaises(CatalogError):
                asyncio.run(client.products())


class OpenAIMockTests(unittest.TestCase):
    def setUp(self):
        self.reply = AIReply(
            intent="product_info", answer_text="Артикул A-1 найден в каталоге.",
            matched_product_ids=["1"], alternative_product_ids=[],
            candidate_quantity=None, needs_clarification=False, clarification_question=None,
        )
        self.product = {"id": "1", "sku": "A-1", "name": "Товар", "stock": 3}

    def run_answer(self, response):
        sdk = SimpleNamespace(responses=SimpleNamespace(parse=AsyncMock(return_value=response)))
        with patch.object(main.settings, "openai_api_key", "test-key"), patch.object(main.settings, "openai_model", "test-model"):
            result = asyncio.run(answer_with_openai("Что это?", [self.product], [], client=sdk))
        return result, sdk.responses.parse.call_args.kwargs

    def test_structured_result_and_minimal_context(self):
        result, args = self.run_answer(SimpleNamespace(status="completed", output_parsed=self.reply))
        self.assertEqual(result.matched_product_ids, ["1"])
        self.assertEqual(args["text_format"], AIReply)
        self.assertFalse(args["store"])
        context = json.loads(args["input"])
        self.assertNotIn("raw", context["catalog_results"][0])

    def test_unverified_model_prose_is_not_displayed(self):
        invented = self.reply.model_copy(update={"answer_text": "Доставка завтра и сертификат подтверждён."})
        answer = compose_grounded_answer(invented, [self.product], "live")
        self.assertIn("A-1", answer)
        self.assertNotIn("Доставка завтра", answer)
        self.assertNotIn("сертификат подтверждён", answer)

    def test_schema_and_catalog_ids_are_checked(self):
        with self.assertRaises(ValidationError):
            AIReply.model_validate({**self.reply.model_dump(), "intent": "invented"})
        with self.assertRaises(ValidationError):
            AIReply.model_validate({"intent": "unclear"})
        invalid = self.reply.model_copy(update={"matched_product_ids": ["not-in-catalog"]})
        with self.assertRaises(AiError):
            validate_grounding(invalid, [self.product], [])

    def test_refusal_or_incomplete_answer_does_not_become_success(self):
        for response in (SimpleNamespace(status="incomplete", output_parsed=self.reply), SimpleNamespace(status="completed", output_parsed=None)):
            sdk = SimpleNamespace(responses=SimpleNamespace(parse=AsyncMock(return_value=response)))
            with patch.object(main.settings, "openai_api_key", "test-key"), patch.object(main.settings, "openai_model", "test-model"):
                with self.assertRaises(AiError):
                    asyncio.run(answer_with_openai("Что это?", [self.product], [], client=sdk))

    def test_openai_timeout_is_reported_without_fallback(self):
        request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
        sdk = SimpleNamespace(responses=SimpleNamespace(parse=AsyncMock(side_effect=APITimeoutError(request))))
        with patch.object(main.settings, "openai_api_key", "test-key"), patch.object(main.settings, "openai_model", "test-model"):
            with self.assertRaisesRegex(AiError, "не ответил вовремя"):
                asyncio.run(answer_with_openai("Что это?", [self.product], [], client=sdk))


class ConversationTests(unittest.TestCase):
    def test_session_history_is_bounded_and_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = SessionStore(Path(tmp) / "state.sqlite3")
            for index in range(9):
                history.append_exchange("one-session", f"вопрос {index}", f"ответ {index}")
            history.append_exchange("other-session", "приватный вопрос", "приватный ответ")
            self.assertEqual(len(history.recent("one-session", 20)), 12)
            self.assertNotIn("приватный вопрос", str(history.recent("one-session")))
            history.remember_products("one-session", ["1"])
            self.assertEqual(history.last_product_ids("one-session"), ["1"])
            history.clear("one-session")
            self.assertEqual(history.recent("one-session"), [])
            self.assertEqual(history.last_product_ids("one-session"), [])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        database = Path(self.temp.name) / "state.sqlite3"
        self.cart = DemoCartAdapter(database)
        self.history = SessionStore(database)
        self.target = normalize_product({"id": "1", "article": "OLD-1", "name": "Кабель силовой старый", "quantity": 0, "properties": {"KATEGORIYA": "Силовой кабель", "TIP_USTANOVKI": "внутри"}})
        self.alternative = normalize_product({"id": "2", "article": "NEW-2", "name": "Кабель силовой новый", "quantity": 3, "properties": {"KATEGORIYA": "Силовой кабель", "TIP_USTANOVKI": "внутри"}})

        class FakeCatalog:
            configured = True

            async def products(inner, page=1):
                return ([self.target, self.alternative] if page == 1 else []), {"page": page, "count": 2, "per_page": 20}

            async def product(inner, product_id):
                return self.target if product_id == "1" else self.alternative

        self.patches = [
            patch.object(main, "catalog", FakeCatalog()),
            patch.object(main, "cart", self.cart),
            patch.object(main, "sessions", self.history),
            patch.object(main.settings, "openai_api_key", "test-key"),
            patch.object(main.settings, "openai_model", "test-model"),
            patch.object(main, "answer_with_openai", AsyncMock(return_value=AIReply(
                intent="availability", answer_text="Основной товар недоступен; есть подходящий кандидат.",
                matched_product_ids=["1"], alternative_product_ids=["2"],
                candidate_quantity=None, needs_clarification=False, clarification_question=None,
            ))),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(main.app)
        self.session = "workflow-session-123"

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def send(self, message):
        return self.client.post("/api/chat", json={"message": message, "session_id": self.session})

    def test_product_to_alternative_to_confirmed_demo_cart(self):
        info = self.send("Расскажите про Кабель силовой старый")
        self.assertEqual(info.status_code, 200)
        self.assertEqual([row["product"]["id"] for row in info.json()["alternatives"]], ["2"])
        self.assertIn("tip_ustanovki", info.json()["answer"].casefold())
        self.assertEqual(self.cart.contents(self.session)["total_items"], 0)
        self.assertEqual(len(self.history.recent(self.session)), 2)

        prepared = self.send("Добавь в корзину NEW-2 2 шт")
        self.assertTrue(prepared.json()["pending_confirmation"])
        self.assertEqual(self.cart.contents(self.session)["total_items"], 0)
        confirmed = self.send("Подтверждаю")
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["cart_url"], "/cart")
        self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"], 2)
        self.assertEqual(self.client.get(f"/api/cart/{self.session}").json()["total_items"], 2)

    def test_ai_or_catalog_error_never_adds_product(self):
        with patch.object(main, "answer_with_openai", AsyncMock(side_effect=AiError("OpenAI failed"))):
            response = self.send("Расскажите про Кабель силовой старый")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.cart.contents(self.session)["total_items"], 0)

        class BrokenCatalog:
            configured = True

            async def products(self, page=1):
                raise CatalogError("Каталог не ответил вовремя.")

        with patch.object(main, "catalog", BrokenCatalog()):
            response = self.send("Кабель")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.cart.contents(self.session)["total_items"], 0)

    def test_chat_hides_unverified_model_claim(self):
        invented = AIReply(
            intent="availability", answer_text="Товар сертифицирован и будет доставлен завтра.",
            matched_product_ids=["1"], alternative_product_ids=["2"],
            candidate_quantity=None, needs_clarification=False, clarification_question=None,
        )
        with patch.object(main, "answer_with_openai", AsyncMock(return_value=invented)):
            response = self.send("Расскажите про Кабель силовой старый")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("доставлен завтра", response.json()["answer"])
        self.assertIn("совпадают характеристики", response.json()["answer"])


if __name__ == "__main__":
    unittest.main()
