"""Read-only live search and comparison; outputs IDs and status counts, not card contents."""

import asyncio
from collections import Counter

from backend.catalog import CatalogClient
from backend.comparison import build_comparison
from backend.requirement_search import search_requirements
from backend.requirements import RequestedAttribute, ShoppingRequirements


async def main() -> None:
    catalog = CatalogClient()
    if not catalog.configured:
        raise SystemExit("Live catalog credentials are not configured.")
    requirements = ShoppingRequirements(
        intent="compare", product_terms=["автомат"], article=None,
        requirements=[RequestedAttribute(attribute="номинальный ток", value="16 А", required=True)],
        quantity=2, max_budget=None, budget_currency=None, clarification_question=None,
    )
    found = await search_requirements(catalog, requirements)
    ids = [str(row["product"]["id"]) for row in found["products"]]
    print(f"Search: {found['status']}; mode={found['mode']}; pages={found['pages_scanned']}; "
          f"details={found['detail_checked']}; coverage_complete={found['coverage_complete']}; IDs={ids}")
    if len(ids) >= 2:
        matrix = build_comparison(requirements, [row["product"] for row in found["products"][:2]], "live")
        labels = {"совпадает": "match", "противоречит": "conflict", "нет данных": "unknown"}
        counts = Counter(labels[cell["status"]] for row in matrix["rows"] for cell in row["cells"])
        print(f"Comparison: 2 catalog IDs; statuses={dict(counts)}; "
              f"links={len([column for column in matrix['columns'] if column['url']])}")
    else:
        print("Comparison: SKIPPED (fewer than two verified IDs)")


if __name__ == "__main__":
    asyncio.run(main())
