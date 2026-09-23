from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.chat import OpenAIError, compose_answer, search_products
from backend.catalog import CatalogClient, CatalogError
from backend.config import settings

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="ekt.kz AI Assistant", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")
catalog = CatalogClient()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)

DEMO_PRODUCTS = [
    {"id": "demo-1", "sku": "DEMO-001", "name": "Автоматический выключатель (демо)", "description": "Демонстрационная карточка; данные не получены из ekt.kz.", "category": "Демо", "price": None, "stock": None, "availability": None, "characteristics": None, "certificates": None, "url": None, "raw": {"demo": True}},
]


@app.get("/", include_in_schema=False)
async def home() -> FileResponse:
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/api/status")
async def status() -> dict[str, Any]:
    if not catalog.configured:
        return {"mode": "demo", "connected": False, "ai_mode": "openai" if settings.openai_api_key else "fallback", "model": settings.openai_model if settings.openai_api_key else None, "message": "Не настроены учётные данные каталога. Показаны демонстрационные данные."}
    try:
        products, pagination = await catalog.products()
        return {"mode": "live", "connected": True, "ai_mode": "openai" if settings.openai_api_key else "fallback", "model": settings.openai_model if settings.openai_api_key else None, "message": f"Каталог доступен. Получено товаров: {len(products)}.", "pagination": pagination}
    except CatalogError as exc:
        return {"mode": "demo", "connected": False, "ai_mode": "openai" if settings.openai_api_key else "fallback", "model": settings.openai_model if settings.openai_api_key else None, "message": f"{exc} Показаны демонстрационные данные."}


@app.get("/api/products")
async def list_products(page: int = Query(1, ge=1)) -> dict[str, Any]:
    try:
        products, pagination = await catalog.products(page)
        return {"mode": "live", "products": products, "pagination": pagination}
    except CatalogError as exc:
        if catalog.configured:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"mode": "demo", "products": DEMO_PRODUCTS if page == 1 else [], "pagination": None}


@app.get("/api/products/{product_id}")
async def product_detail(product_id: str) -> dict[str, Any]:
    try:
        return {"mode": "live", "product": await catalog.product(product_id)}
    except CatalogError as exc:
        if catalog.configured:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        item = next((item for item in DEMO_PRODUCTS if item["id"] == product_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="Товар не найден.") from exc
        return {"mode": "demo", "product": item}


@app.post("/api/chat")
async def chat(request: ChatRequest) -> dict[str, Any]:
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Сообщение не должно быть пустым.")

    mode = "live"
    if catalog.configured:
        try:
            all_products: list[dict[str, Any]] = []
            for page in range(1, 4):
                page_products, pagination = await catalog.products(page)
                all_products.extend(page_products)
                if not page_products:
                    break
                if pagination and any(k in pagination for k in ("last_page", "lastPage", "total_pages", "totalPages")):
                    last = next((pagination[k] for k in ("last_page", "lastPage", "total_pages", "totalPages") if k in pagination), page)
                    if page >= int(last):
                        break
            found = search_products(all_products, message)
            if found:
                hydrated = []
                for item in found[:3]:
                    if item.get("id") is not None and (not item.get("characteristics") or not item.get("price")):
                        try:
                            hydrated.append(await catalog.product(str(item["id"])))
                        except CatalogError:
                            hydrated.append(item)
                    else:
                        hydrated.append(item)
                found = hydrated
        except (CatalogError, ValueError):
            raise HTTPException(status_code=502, detail="Не удалось выполнить поиск в каталоге. Попробуйте позже.")
    else:
        mode = "demo"
        found = search_products(DEMO_PRODUCTS, message)

    try:
        answer = await compose_answer(message, found)
    except OpenAIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "answer": answer,
        "products": found,
        "catalog_mode": mode,
        "ai_mode": "openai" if settings.openai_api_key else "fallback",
        "model": settings.openai_model if settings.openai_api_key else None,
    }
