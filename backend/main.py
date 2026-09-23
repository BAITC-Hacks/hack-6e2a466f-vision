from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.catalog import CatalogClient, CatalogError
from backend.config import settings

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="ekt.kz AI Assistant", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")
catalog = CatalogClient()

DEMO_PRODUCTS = [
    {"id": "demo-1", "sku": "DEMO-001", "name": "Автоматический выключатель (демо)", "description": "Демонстрационная карточка; данные не получены из ekt.kz.", "category": "Демо", "price": None, "stock": None, "availability": None, "characteristics": None, "certificates": None, "url": None, "raw": {"demo": True}},
]


@app.get("/", include_in_schema=False)
async def home() -> FileResponse:
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/api/status")
async def status() -> dict[str, Any]:
    if not catalog.configured:
        return {"mode": "demo", "connected": False, "message": "Не настроены учётные данные каталога. Показаны демонстрационные данные."}
    try:
        products, pagination = await catalog.products()
        return {"mode": "live", "connected": True, "message": f"Каталог доступен. Получено товаров: {len(products)}.", "pagination": pagination}
    except CatalogError as exc:
        return {"mode": "demo", "connected": False, "message": f"{exc} Показаны демонстрационные данные."}


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
