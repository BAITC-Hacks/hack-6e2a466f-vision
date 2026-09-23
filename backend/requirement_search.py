"""Bounded catalog candidate search for saved shopping requirements."""

import asyncio
import re
from typing import Any

from backend.catalog import CatalogClient, CatalogError
from backend.requirements import ATTRIBUTE_ALIASES, ShoppingRequirements


PAGE_LIMIT = 5
DETAIL_LIMIT = 12
RESULT_LIMIT = 5


def _words(value: Any) -> list[str]:
    return re.findall(r"[\w]+", str(value or "").casefold())


def _same_value(wanted: str, actual: str) -> bool:
    def normalized(value: str) -> str:
        return "".join(_words(value.replace(",", ".").replace("а", "a")))
    left, right = normalized(wanted), normalized(actual)
    if left == right:
        return True
    # The API sometimes omits the unit for an electrical current.
    return bool(left and right and (left == right + "a" or right == left + "a"))


def _attributes(product: dict[str, Any]) -> dict[str, str]:
    source = product.get("characteristics")
    if isinstance(source, dict):
        pairs = source.items()
    elif isinstance(source, list):
        pairs = ((row.get("name") or row.get("key"), row.get("value")) for row in source if isinstance(row, dict))
    else:
        pairs = []
    result = {}
    for key, value in pairs:
        canonical = ATTRIBUTE_ALIASES.get(str(key), ATTRIBUTE_ALIASES.get(str(key).casefold()))
        if canonical and value is not None and str(value).strip():
            result[canonical] = str(value).strip()
    return result


def _name_score(product: dict[str, Any], requirements: ShoppingRequirements) -> int:
    sku = str(product.get("sku") or "").casefold()
    product_id = str(product.get("id") or "").casefold()
    if requirements.article:
        article = requirements.article.casefold().strip()
        return 1000 if article in {sku, product_id} else 0
    words = _words(product.get("name"))
    terms = [token for term in requirements.product_terms for token in _words(term)]
    if not terms:
        return 1
    matches = sum(any(word.startswith(token[:5]) or token.startswith(word[:5]) for word in words) for token in terms)
    return matches * 10 if matches == len(terms) else 0


def _candidate(product: dict[str, Any], requirements: ShoppingRequirements) -> tuple[dict[str, Any] | None, int]:
    attributes = _attributes(product)
    matched = []
    unknown = []
    score = 0
    for item in requirements.requirements:
        actual = attributes.get(item.attribute)
        if actual is None:
            unknown.append(item.attribute)
            continue
        negative = item.value.casefold().startswith("не ")
        wanted = item.value[3:] if negative else item.value
        agrees = _same_value(wanted, actual)
        if (not agrees if negative else agrees):
            matched.append(f"{item.attribute}: {actual}")
            score += 5 if item.required else 2
        elif item.required:
            return None, 0
    return {
        "product": product,
        "matched": matched,
        "unknown": unknown,
        "budget_checked": False,
    }, score


async def search_requirements(
    catalog: CatalogClient, requirements: ShoppingRequirements, *,
    demo_products: list[dict[str, Any]] | None = None, refresh: bool = False,
) -> dict[str, Any]:
    if not requirements.article and not requirements.product_terms and not requirements.requirements:
        return {"status": "not_found", "products": [], "coverage_complete": True,
                "pages_scanned": 0, "detail_checked": 0, "message": "Укажите название, артикул или характеристику товара."}
    mode = "demo" if demo_products is not None else "live"
    if demo_products is not None:
        listed = demo_products
        pages_scanned, complete, issue = 1, True, None
    else:
        snapshot = await catalog.search_index(PAGE_LIMIT, refresh=refresh)
        listed = snapshot.products
        pages_scanned, complete, issue = snapshot.pages_scanned, snapshot.complete, snapshot.issue
    ranked = sorted(
        ((score, item) for item in listed if item.get("id") is not None
         if (score := _name_score(item, requirements)) > 0),
        key=lambda pair: pair[0], reverse=True,
    )
    shortlist = ranked[:DETAIL_LIMIT]
    if len(ranked) > DETAIL_LIMIT:
        complete = False
        issue = "Проверены детальные карточки только первых кандидатов."
    if not requirements.article and not requirements.product_terms and len(listed) > DETAIL_LIMIT:
        complete = False
        issue = "Поиск только по характеристикам проверил часть карточек. Добавьте название или артикул."
    semaphore = asyncio.Semaphore(3)

    async def hydrate(item: dict[str, Any]) -> dict[str, Any] | None:
        if demo_products is not None:
            return item
        async with semaphore:
            try:
                detail = await catalog.search_detail(str(item["id"]), refresh=refresh)
            except CatalogError:
                return None
            return detail if str(detail.get("id")) == str(item["id"]) else None

    detailed = await asyncio.gather(*(hydrate(item) for _, item in shortlist))
    scored = []
    detail_errors = 0
    for (name_score, _), detail in zip(shortlist, detailed):
        if detail is None:
            detail_errors += 1
            continue
        candidate, attribute_score = _candidate(detail, requirements)
        if candidate:
            scored.append((name_score + attribute_score, candidate))
    if detail_errors:
        complete = False
        issue = f"Не удалось проверить детальные карточки: {detail_errors}."
    scored.sort(key=lambda pair: pair[0], reverse=True)
    results = [item for _, item in scored[:RESULT_LIMIT]]
    status = "found" if results else "not_found" if complete else "incomplete"
    if mode == "demo":
        message = "Демонстрационный каталог: позиции вымышленные."
    elif complete:
        message = "Поиск завершён по доступным страницам каталога."
    else:
        message = f"Поиск неполон. {issue or 'Не все товары проверены.'}"
    return {"status": status, "products": results, "coverage_complete": complete,
            "pages_scanned": pages_scanned, "detail_checked": len(shortlist) - detail_errors,
            "message": message, "mode": mode}
