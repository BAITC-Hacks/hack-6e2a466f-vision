"""Clearly fictional catalog data for a credential-free demonstration."""

from backend.catalog import normalize_product


DEMO_PRODUCTS = [
    normalize_product({
        "id": "demo-cable-old", "article": "DEMO-OUT", "name": "Кабель ВВГнг 3×2,5 (демо, нет в наличии)",
        "description": "Вымышленная карточка для показа поиска замены; не является товаром ekt.kz.",
        "category": "Кабели", "price": 1200, "currency": "KZT", "quantity": 0,
        "properties": {"тип": "ВВГнг", "жилы": "3", "сечение": "2,5 мм²"}, "demo": True,
    }),
    normalize_product({
        "id": "demo-cable-alt", "article": "DEMO-ALT", "name": "Кабель ВВГнг 3×2,5 (демо, аналог)",
        "description": "Вымышленный аналог с совпадающими указанными характеристиками.",
        "category": "Кабели", "price": 1250, "currency": "KZT", "quantity": 6,
        "properties": {"тип": "ВВГнг", "жилы": "3", "сечение": "2,5 мм²"}, "demo": True,
    }),
    normalize_product({
        "id": "demo-breaker", "article": "DEMO-IN", "name": "Автоматический выключатель 16 А (демо)",
        "description": "Вымышленная позиция для проверки наличия и демо-корзины.",
        "category": "Автоматические выключатели", "price": 890, "currency": "KZT", "quantity": 8,
        "properties": {"номинальный ток": "16 А", "полюса": "1"}, "demo": True,
    }),
    normalize_product({
        "id": "demo-breaker-conflict", "article": "DEMO-CONFLICT", "name": "Автоматический выключатель 20 А (демо)",
        "description": "Вымышленная позиция для показа противоречащей характеристики.",
        "category": "Автоматические выключатели", "price": 760, "currency": "KZT", "quantity": 4,
        "properties": {"номинальный ток": "20 А", "полюса": "1"}, "demo": True,
    }),
    normalize_product({
        "id": "demo-breaker-unknown", "article": "DEMO-UNKNOWN", "name": "Автоматический выключатель без тока (демо)",
        "description": "Вымышленная позиция: номинальный ток в карточке не указан.",
        "category": "Автоматические выключатели", "price": 700, "currency": "KZT", "quantity": 3,
        "properties": {"полюса": "1"}, "demo": True,
    }),
    normalize_product({
        "id": "demo-partial", "article": "DEMO-PARTIAL", "name": "Светильник с неполной карточкой (демо)",
        "description": None, "category": "Светильники", "price": None, "quantity": 2,
        "properties": None, "demo": True,
    }),
]


def demo_product(product_id: str):
    return next((item for item in DEMO_PRODUCTS if str(item["id"]) == str(product_id)), None)
