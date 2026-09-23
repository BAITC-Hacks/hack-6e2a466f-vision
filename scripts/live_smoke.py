"""Separate read-only live catalog and synthetic OpenAI checks; never send EKT cards to OpenAI."""

import asyncio

from backend.catalog import CatalogClient
from backend.config import settings
from backend.requirements import DEMO_ATTRIBUTES, extract_with_openai


async def main() -> None:
    catalog = CatalogClient()
    if catalog.configured:
        products, _ = await catalog.products()
        if not products or products[0].get("id") is None:
            raise RuntimeError("Live catalog returned no product with an ID.")
        detail = await catalog.product(str(products[0]["id"]))
        if str(detail.get("id")) != str(products[0]["id"]):
            raise RuntimeError("Live detail ID differs from list ID.")
        print(f"Catalog: OK ({len(products)} items on first page; matching detail ID)")
    else:
        print("Catalog: SKIPPED (credentials not configured)")

    if settings.openai_api_key and settings.openai_model:
        requirements = await extract_with_openai("Нужен кабель 2 шт", [], available_attributes=DEMO_ATTRIBUTES)
        if requirements.quantity != 2 or "кабель" not in requirements.product_terms:
            raise RuntimeError("OpenAI structured extraction failed synthetic-data validation.")
        print("OpenAI Responses API: OK (synthetic request, structured requirements validated)")
    else:
        print("OpenAI Responses API: SKIPPED (key or model not configured)")


if __name__ == "__main__":
    asyncio.run(main())
