"""One metered OpenAI comparison of two live EKT cards; run only with explicit approval."""

import asyncio

from backend.catalog import CatalogClient
from backend.comparison import build_comparison, explain_comparison
from backend.config import settings
from backend.requirement_search import search_requirements
from backend.requirements import RequestedAttribute, ShoppingRequirements


async def main() -> None:
    if not (settings.ekt_api_username and settings.ekt_api_password and
            settings.openai_api_key and settings.openai_model):
        raise SystemExit("Live catalog and OpenAI credentials/model are required.")
    requirements = ShoppingRequirements(
        intent="compare", product_terms=["автомат"], article=None,
        requirements=[RequestedAttribute(attribute="номинальный ток", value="16 А", required=True)],
        quantity=2, max_budget=None, budget_currency=None, clarification_question=None,
    )
    found = await search_requirements(CatalogClient(), requirements)
    if found["mode"] != "live" or len(found["products"]) < 2:
        raise RuntimeError("Fewer than two verified live catalog candidates; OpenAI was not called.")
    products = [row["product"] for row in found["products"][:2]]
    matrix = build_comparison(requirements, products, "live")
    explanation, mode = await explain_comparison(matrix)
    if mode != "openai" or not explanation.strip():
        raise RuntimeError("OpenAI comparison did not return a validated explanation.")
    print(f"Live comparison: OK; IDs={[column['id'] for column in matrix['columns']]}; "
          f"OpenAI explanation: validated; catalog coverage_complete={found['coverage_complete']}")


if __name__ == "__main__":
    asyncio.run(main())
