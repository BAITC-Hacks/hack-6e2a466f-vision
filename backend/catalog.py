from typing import Any
import time

import httpx

from backend.config import settings


class CatalogError(Exception):
    """Safe-to-display catalog error; never contains credentials or headers."""


class CatalogClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = settings.ekt_api_base_url.rstrip("/")
        self.timeout = settings.ekt_api_timeout_seconds
        self.transport = transport
        self._page_cache: dict[int, tuple[float, list[dict[str, Any]], Any]] = {}

    @property
    def configured(self) -> bool:
        return bool(settings.ekt_api_username and settings.ekt_api_password)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.configured:
            raise CatalogError("Для live-каталога не заданы учётные данные.")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, transport=self.transport) as client:
                response = await client.get(
                    f"{self.base_url}/{path.lstrip('/')}",
                    params=params,
                    auth=httpx.BasicAuth(settings.ekt_api_username, settings.ekt_api_password),
                )
            if response.status_code in (401, 403):
                raise CatalogError("Каталог отклонил авторизацию. Проверьте настройки доступа.")
            response.raise_for_status()
            return response.json()
        except CatalogError:
            raise
        except httpx.TimeoutException as exc:
            raise CatalogError("Каталог не ответил вовремя.") from exc
        except httpx.HTTPStatusError as exc:
            raise CatalogError(f"Каталог вернул ошибку HTTP {exc.response.status_code}.") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise CatalogError("Не удалось получить корректный ответ каталога.") from exc

    async def products(self, page: int = 1) -> tuple[list[dict[str, Any]], Any]:
        cached = self._page_cache.get(page)
        if cached and cached[0] > time.monotonic():
            return cached[1], cached[2]
        payload = await self._get("products", {"page": page} if page > 1 else None)
        items, pagination = unwrap_products(payload)
        normalized = [normalize_product(item) for item in items if isinstance(item, dict)]
        self._page_cache[page] = (time.monotonic() + 60, normalized, pagination)
        return normalized, pagination

    async def product(self, product_id: str) -> dict[str, Any]:
        payload = await self._get("products/detail", {"id": product_id})
        item = payload
        if isinstance(payload, dict):
            for key in ("data", "product", "item"):
                if isinstance(payload.get(key), dict):
                    item = payload[key]
                    break
        if not isinstance(item, dict):
            raise CatalogError("В ответе каталога нет карточки товара.")
        return normalize_product(item)


def unwrap_products(payload: Any) -> tuple[list[Any], Any]:
    """Support common envelopes while preserving raw pagination for inspection."""
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        pagination = {k: v for k, v in payload.items() if k not in ("data", "products", "items", "results")}
        for key in ("data", "products", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value, pagination or None
            if isinstance(value, dict):
                nested, nested_pagination = unwrap_products(value)
                return nested, nested_pagination or pagination or None
    return [], None


def normalize_product(raw: dict[str, Any]) -> dict[str, Any]:
    """Expose known fields when present; retain unknown API fields without inventing values."""
    aliases = {
        "id": ("id", "product_id"), "sku": ("sku", "article", "articul", "code"),
        "name": ("name", "title", "product_name"), "description": ("description", "text"),
        "category": ("category", "category_name"), "price": ("price", "cost"),
        "stock": ("stock", "quantity", "amount", "balance"),
        "availability": ("availability", "available", "in_stock", "status"),
        "characteristics": ("characteristics", "attributes", "properties", "specifications"),
        "certificates": ("certificates", "certificate", "documents"),
        "url": ("url", "link", "product_url"),
    }
    normalized = {key: next((raw[name] for name in names if name in raw), None) for key, names in aliases.items()}
    properties = raw.get("properties")
    if normalized["category"] is None and isinstance(properties, dict):
        normalized["category"] = properties.get("KATEGORIYA")
    normalized["raw"] = raw
    return normalized
