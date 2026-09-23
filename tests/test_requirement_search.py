import asyncio
import unittest
from collections import Counter
from unittest.mock import patch

import httpx

from backend.catalog import CatalogClient
from backend.requirement_search import search_requirements
from backend.requirements import ShoppingRequirements


def request(article=None, terms=None, required=None):
    return ShoppingRequirements.model_validate({
        "intent": "find", "product_terms": terms or [], "article": article,
        "requirements": required or [], "quantity": None, "max_budget": None,
        "budget_currency": None, "clarification_question": None,
    })


class RequirementSearchTests(unittest.TestCase):
    def test_pagination_detail_verification_and_repeat_cache(self):
        calls = Counter()

        def respond(req):
            key = (req.url.path, req.url.params.get("page", "1"), req.url.params.get("id", ""))
            calls[key] += 1
            if req.url.path.endswith("/detail"):
                return httpx.Response(200, json={"id": 202, "article": "B-16", "name": "Автомат 16 А", "properties": {"NOMINALNYY_TOK": "16 А"}, "quantity": 4})
            if req.url.params.get("page") == "2":
                return httpx.Response(200, json={"data": [{"id": 202, "article": "B-16", "name": "Автомат 16 А", "url": "https://ekt.kz/catalog/b-16"}], "page": 2, "per_page": 2})
            return httpx.Response(200, json={"data": [{"id": 101, "article": "X-1", "name": "Кабель"}, {"id": 102, "article": "X-2", "name": "Светильник"}], "page": 1, "per_page": 2})

        with patch("backend.catalog.settings.ekt_api_username", "user"), patch("backend.catalog.settings.ekt_api_password", "password"):
            client = CatalogClient(httpx.MockTransport(respond))
            search = request(terms=["автомат"], required=[{"attribute": "номинальный ток", "value": "16 А", "required": True}])
            first = asyncio.run(search_requirements(client, search))
            second = asyncio.run(search_requirements(client, search))
        self.assertEqual(first["status"], "found")
        self.assertTrue(first["coverage_complete"])
        self.assertEqual(first["pages_scanned"], 2)
        self.assertEqual(first["products"][0]["product"]["id"], 202)
        self.assertEqual(first["products"][0]["matched"], ["номинальный ток: 16 А"])
        self.assertEqual(first["products"][0]["product"]["url"], "https://ekt.kz/catalog/b-16")
        self.assertEqual(first, second)
        self.assertEqual(sum(calls.values()), 3)

    def test_partial_page_failure_is_not_not_found(self):
        def respond(req):
            if req.url.params.get("page") == "2":
                return httpx.Response(503)
            return httpx.Response(200, json={"data": [{"id": 101, "article": "X-1", "name": "Кабель"}], "page": 1, "per_page": 1})

        with patch("backend.catalog.settings.ekt_api_username", "user"), patch("backend.catalog.settings.ekt_api_password", "password"):
            client = CatalogClient(httpx.MockTransport(respond))
            result = asyncio.run(search_requirements(client, request(article="missing")))
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(result["pages_scanned"], 1)
        self.assertEqual(result["products"], [])

    def test_later_page_timeout_reports_partial_coverage(self):
        def respond(req):
            if req.url.params.get("page") == "2":
                raise httpx.ReadTimeout("timeout", request=req)
            return httpx.Response(200, json={"data": [{"id": 101, "name": "Кабель"}], "page": 1, "per_page": 1})

        with patch("backend.catalog.settings.ekt_api_username", "user"), \
             patch("backend.catalog.settings.ekt_api_password", "password"):
            result = asyncio.run(search_requirements(
                CatalogClient(httpx.MockTransport(respond)), request(article="missing"),
            ))
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(result["pages_scanned"], 1)

    def test_detail_failure_never_exposes_unverified_product(self):
        def respond(req):
            if req.url.path.endswith("/detail"):
                return httpx.Response(504)
            return httpx.Response(200, json={"data": [{"id": 42, "article": "A42", "name": "Автомат"}], "per_page": 20})

        with patch("backend.catalog.settings.ekt_api_username", "user"), patch("backend.catalog.settings.ekt_api_password", "password"):
            result = asyncio.run(search_requirements(CatalogClient(httpx.MockTransport(respond)), request(article="A42")))
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["products"], [])
        self.assertEqual(result["detail_checked"], 0)

    def test_page_limit_is_reported_as_incomplete(self):
        calls = []

        def respond(req):
            calls.append(req.url.params.get("page", "1"))
            return httpx.Response(200, json={"data": [{"id": int(calls[-1]), "name": "Кабель"}], "per_page": 1})

        with patch("backend.catalog.settings.ekt_api_username", "user"), patch("backend.catalog.settings.ekt_api_password", "password"):
            result = asyncio.run(search_requirements(CatalogClient(httpx.MockTransport(respond)), request(article="missing")))
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["pages_scanned"], 5)
        self.assertEqual(calls, ["1", "2", "3", "4", "5"])

    def test_conflicting_candidate_remains_visible_for_comparison(self):
        from backend.catalog import normalize_product

        products = [
            normalize_product({"id": "match", "name": "Кабель A", "properties": {"сечение": "2,5 мм²"}}),
            normalize_product({"id": "conflict", "name": "Кабель B", "properties": {"сечение": "4 мм²"}}),
        ]
        query = request(terms=["кабель"], required=[{"attribute": "сечение", "value": "2,5 мм²", "required": True}])
        result = asyncio.run(search_requirements(CatalogClient(), query, demo_products=products))
        self.assertEqual([row["product"]["id"] for row in result["products"]], ["match", "conflict"])
        self.assertEqual(result["products"][1]["conflicted"], ["сечение: 4 мм²"])


if __name__ == "__main__":
    unittest.main()
