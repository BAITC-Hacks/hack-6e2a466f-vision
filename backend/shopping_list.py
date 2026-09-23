"""Conservative money arithmetic for the session's demonstration shopping list."""

from decimal import Decimal, InvalidOperation
from typing import Any

from backend.chat import stock_state
from backend.comparison import MATCH, build_comparison
from backend.requirements import ShoppingRequirements


CURRENCIES = {
    "₸": "KZT", "тг": "KZT", "тенге": "KZT", "kzt": "KZT",
    "₽": "RUB", "руб": "RUB", "рублей": "RUB", "rub": "RUB",
    "$": "USD", "usd": "USD", "доллар": "USD",
    "€": "EUR", "eur": "EUR", "евро": "EUR",
}


def currency_code(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    code = value.strip().casefold()
    if code in CURRENCIES:
        return CURRENCIES[code]
    return None


def confirmed_price(product: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    raw_price = product.get("price")
    currency = product.get("currency")
    if isinstance(raw_price, dict):
        currency = currency or raw_price.get("currency") or raw_price.get("currency_code")
        raw_price = raw_price["amount"] if "amount" in raw_price else raw_price.get("value")
    raw = product.get("raw")
    if isinstance(raw, dict):
        currency = currency or raw.get("currency") or raw.get("currency_code") or raw.get("price_currency")
    code = currency_code(currency)
    if isinstance(raw_price, bool) or raw_price is None:
        return None, code
    try:
        amount = Decimal(str(raw_price).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None, code
    if not amount.is_finite() or amount < 0:
        return None, code
    return amount, code


def money(value: Decimal) -> str:
    return format(value.normalize(), "f")


def summarize_cart(cart: dict[str, Any], requirements: ShoppingRequirements | None = None) -> dict[str, Any]:
    lines = []
    subtotals: dict[str, Decimal] = {}
    reasons = []
    for item in cart["items"]:
        product, quantity = item["product"], item["quantity"]
        amount, currency = confirmed_price(product)
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            reason = "Некорректное количество."
        elif amount is None:
            reason = "Цена не подтверждена каталогом."
        elif currency is None:
            reason = "Валюта цены не указана в каталоге."
        else:
            reason = None
        line_total = amount * quantity if reason is None else None
        if line_total is not None:
            subtotals[currency] = subtotals.get(currency, Decimal(0)) + line_total
        else:
            reasons.append(f"{product.get('name') or product.get('id')}: {reason}")
        lines.append({"product_id": str(product.get("id")), "quantity": quantity,
                      "unit_price": money(amount) if amount is not None else None,
                      "currency": currency, "line_total": money(line_total) if line_total is not None else None,
                      "reason": reason})
    if len(subtotals) > 1:
        reasons.append("В корзине смешаны разные валюты; общий итог не рассчитан.")
    complete = bool(lines) and not reasons and len(subtotals) == 1
    total_currency = next(iter(subtotals)) if complete else None
    total = money(next(iter(subtotals.values()))) if complete else None
    budget = {"status": "not_set", "amount": None, "currency": None, "difference": None}
    if requirements and requirements.max_budget is not None:
        budget_amount = Decimal(str(requirements.max_budget))
        budget_currency = currency_code(requirements.budget_currency)
        budget = {"status": "unknown", "amount": money(budget_amount),
                  "currency": budget_currency, "difference": None}
        if budget_currency is None:
            reasons.append("Валюта бюджета не указана; сравнение невозможно.")
        elif complete and budget_currency == total_currency:
            difference = budget_amount - next(iter(subtotals.values()))
            budget.update(status="within" if difference >= 0 else "over", difference=money(difference))
        elif complete:
            reasons.append("Валюта бюджета отличается от валюты товаров; сравнение невозможно.")
    return {"lines": lines, "complete": complete, "total": total, "currency": total_currency,
            "known_subtotals": {code: money(amount) for code, amount in subtotals.items()},
            "reasons": reasons, "budget": budget}


def cheaper_candidates(cart: dict[str, Any], requirements: ShoppingRequirements | None,
                       search_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not requirements or requirements.max_budget is None or not any(item.required for item in requirements.requirements):
        return []
    budget_currency = currency_code(requirements.budget_currency)
    if budget_currency is None:
        return []
    by_id = {str(row["product"]["id"]): row["product"] for row in search_results}
    suggestions = []
    for cart_item in cart["items"]:
        current, quantity = cart_item["product"], cart_item["quantity"]
        if str(current.get("id")) not in by_id:
            continue
        old_price, old_currency = confirmed_price(current)
        if old_price is None or old_currency != budget_currency:
            continue
        for candidate in by_id.values():
            if str(candidate["id"]) == str(current["id"]):
                continue
            new_price, new_currency = confirmed_price(candidate)
            stock_state_name, stock = stock_state(candidate)
            if new_price is None or new_currency != old_currency or new_price >= old_price:
                continue
            if stock_state_name != "available" or stock is None or stock < quantity:
                continue
            raw_candidate = candidate.get("raw")
            mode = "demo" if isinstance(raw_candidate, dict) and raw_candidate.get("demo") else "live"
            matrix = build_comparison(requirements, [current, candidate], mode)
            if not all(row["cells"][1]["status"] == MATCH for row in matrix["rows"] if row["required"]):
                continue
            suggestions.append({"replaces_id": str(current["id"]), "product_id": str(candidate["id"]),
                                "name": candidate.get("name") or candidate.get("sku"),
                                "unit_price": money(new_price), "currency": new_currency,
                                "saving": money((old_price - new_price) * quantity),
                                "quantity": quantity,
                                "note": "Совпали указанные обязательные параметры; полная совместимость не подтверждена."})
    return suggestions[:3]
