import re
from typing import Any


def search_products(products: list[dict[str, Any]], query: str, limit: int = 5) -> list[dict[str, Any]]:
    query_clean = query.casefold().strip()
    tokens = [t for t in re.findall(r"[\w-]+", query_clean) if len(t) > 1]
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for index, product in enumerate(products):
        sku = str(product.get("sku") or "").casefold()
        product_id = str(product.get("id") or "").casefold()
        name = str(product.get("name") or "").casefold()
        searchable = " ".join(str(v) for v in product.values() if isinstance(v, (str, int, float))).casefold()
        score = (100 if sku and sku == query_clean else 70 if sku and sku in tokens else 0)
        score += 60 if product_id and product_id in tokens else 0
        score += 15 if name and query_clean in name else 0
        score += sum(3 for token in tokens if token in searchable)
        if score:
            ranked.append((score, -index, product))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in ranked[:limit]]


def fallback_answer(question: str, products: list[dict[str, Any]], mode: str = "live") -> str:
    source = "демонстрационных данных" if mode == "demo" else "загруженных данных каталога"
    if not products:
        return f"Подходящих товаров среди {source} не нашлось. Уточните название, артикул или характеристики: без них нельзя надёжно сравнить аналоги."
    chunks = []
    for p in products[:3]:
        facts = [str(p.get("name") or "Название не указано")]
        for label, key in (("Артикул", "sku"), ("Цена", "price"), ("Наличие", "availability"), ("Остаток", "stock")):
            value = p.get(key)
            if value is not None and value != "":
                facts.append(f"{label}: {value}")
        features = _features(p)
        if features:
            facts.append("Характеристики: " + ", ".join(f"{key}: {value}" for key, value in list(features.items())[:4]))
        chunks.append("; ".join(facts))
    intro = "Нашёл в демонстрационном наборе" if mode == "demo" else "Нашёл в каталоге"
    return intro + ":\n" + "\n".join(f"• {chunk}" for chunk in chunks)


def stock_state(product: dict[str, Any]) -> tuple[str, int | None]:
    stock = product.get("stock")
    if isinstance(stock, bool):
        stock_count = int(stock)
    elif isinstance(stock, (int, float)):
        stock_count = int(stock)
    elif isinstance(stock, str) and re.fullmatch(r"\s*-?\d+(?:[.,]\d+)?\s*", stock):
        stock_count = int(float(stock.replace(",", ".")))
    else:
        stock_count = None
    availability = product.get("availability")
    if stock_count is not None:
        return ("available" if stock_count > 0 else "unavailable"), max(0, stock_count)
    if isinstance(availability, bool):
        return ("available" if availability else "unavailable"), None
    text = str(availability or "").casefold().strip()
    if text in {"нет", "нет в наличии", "отсутствует", "недоступен", "out of stock", "unavailable", "false", "0"} or "отсутств" in text or "out of stock" in text:
        return "unavailable", None
    if text in {"в наличии", "есть в наличии", "есть", "available", "in stock", "true", "1"}:
        return "available", None
    return "unknown", None


def _features(product: dict[str, Any]) -> dict[str, str]:
    source = product.get("characteristics")
    result: dict[str, str] = {}
    if isinstance(source, dict):
        for key, value in source.items():
            name = str(key).casefold().strip()
            if any(term in name for term in (
                "article", "artikul", "артикул", "priority", "novinka", "spetspredlozhenie",
                "cml2_", "kratnost", "blog_post", "torgovaya_marka",
            )):
                continue
            if isinstance(value, (str, int, float, bool)) and str(value).strip():
                result[name] = str(value).casefold().strip()
    elif isinstance(source, list):
        for item in source:
            if isinstance(item, dict):
                key = item.get("name") or item.get("title") or item.get("key")
                value = item.get("value") or item.get("text")
                if key and value is not None:
                    result[str(key).casefold().strip()] = str(value).casefold().strip()
    return result


def find_alternatives(target: dict[str, Any], products: list[dict[str, Any]], limit: int = 3) -> tuple[list[dict[str, Any]], str | None]:
    target_features = _features(target)
    target_category = str(target.get("category") or "").casefold().strip()
    candidates: list[tuple[int, dict[str, Any], list[str], bool, int | None]] = []
    for product in products:
        if str(product.get("id")) == str(target.get("id")) or not product.get("id"):
            continue
        availability, stock = stock_state(product)
        if availability != "available" or stock == 0:
            continue
        features = _features(product)
        shared = [key for key in target_features.keys() & features.keys() if target_features[key] == features[key]]
        same_category = bool(target_category and target_category == str(product.get("category") or "").casefold().strip())
        # A broad category alone is not enough evidence of a compatible replacement.
        if not shared:
            continue
        candidates.append((len(shared) * 2 + int(same_category), product, sorted(shared), same_category, stock))
    candidates.sort(key=lambda row: row[0], reverse=True)
    result = []
    for _, product, shared, same_category, stock in candidates[:limit]:
        reasons = []
        if same_category:
            reasons.append(f"та же категория: {product['category']}")
        if shared:
            reasons.append("совпадают характеристики: " + ", ".join(shared[:3]))
        if stock is not None:
            reasons.append(f"остаток: {stock}")
        result.append({"product": product, "reason": "; ".join(reasons)})
    limitation = None if result else "В каталоге недостаточно общих характеристик и данных о наличии, чтобы надёжно сопоставить аналог."
    return result, limitation


def extract_quantity(message: str) -> int | None:
    text = message.casefold().replace(",", ".")
    patterns = (
        r"(?:количеств\w*|штук\w*|шт\.?|единиц\w*)\s*[:=]?\s*(-?\d+)",
        r"(-?\d+)\s*(?:шт\.?|штук\w*|единиц\w*)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def is_confirmation(message: str) -> bool:
    normalized = re.sub(r"[.!\s]+$", "", message.casefold().strip())
    return normalized in {"да", "подтверждаю", "подтверждаю добавление", "да, подтверждаю", "согласен", "согласна"}


def is_refusal(message: str) -> bool:
    normalized = re.sub(r"[.!\s]+$", "", message.casefold().strip())
    return normalized in {"нет", "отмена", "отменить", "не надо", "не добавляй", "не подтверждаю"}


def is_purchase_intent(message: str) -> bool:
    return bool(re.search(r"\b(добавь|добавить|положи|купить|заказать|в корзину)\b", message.casefold()))
