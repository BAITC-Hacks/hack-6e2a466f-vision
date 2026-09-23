"""One read-only catalog check and one metered OpenAI Responses API check."""

import asyncio

from backend.ai import answer_with_openai
from backend.catalog import CatalogClient
from backend.config import settings


async def main() -> None:
    catalog = CatalogClient()
    if not catalog.configured or not settings.openai_api_key or not settings.openai_model:
        raise SystemExit("Live smoke skipped: configure catalog and OpenAI credentials in .env first.")

    products, _ = await catalog.products()
    if not products or products[0].get("id") is None:
        raise RuntimeError("Live catalog returned no product with an ID.")
    detail = await catalog.product(str(products[0]["id"]))
    reply = await answer_with_openai(
        f"Что известно о товаре с артикулом {detail.get('sku') or detail['id']}?",
        [detail],
        [],
    )
    print(f"Catalog: OK ({len(products)} items on first page; detail loaded)")
    print(f"OpenAI Responses API: OK (structured intent={reply.intent}; validated product IDs={len(reply.matched_product_ids)})")


if __name__ == "__main__":
    asyncio.run(main())
