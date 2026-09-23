import asyncio
from pathlib import Path
import re
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.ai import AiError, answer_with_openai
from backend.cart import DemoCartAdapter
from backend.chat import (
    extract_quantity, fallback_answer, find_alternatives,
    is_confirmation, is_purchase_intent, is_refusal, search_products, stock_state,
)
from backend.catalog import CatalogClient, CatalogError
from backend.config import settings
from backend.demo_data import DEMO_PRODUCTS, demo_product
from backend.requirements import (
    DEMO_ATTRIBUTES, LIVE_ATTRIBUTES, ShoppingRequirements, extract_with_openai,
    offline_extract, validate_attributes,
)
from backend.session import SessionStore

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="ekt.kz AI Assistant", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")
catalog = CatalogClient()
cart = DemoCartAdapter()
sessions = SessionStore()


def ai_configured() -> bool:
    return bool(settings.openai_api_key and settings.openai_model)


def known_attributes() -> list[str]:
    return LIVE_ATTRIBUTES if catalog.configured else DEMO_ATTRIBUTES


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    session_id: str = Field(min_length=8, max_length=100)


class RequirementsUpdate(BaseModel):
    requirements: ShoppingRequirements

async def live_alternatives(target: dict[str, Any], products: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    """Hydrate a small relevant shortlist before comparing real catalog characteristics and stock."""
    target_terms = set(re.findall(r"[\w-]{4,}", str(target.get("name") or "").casefold()))
    shortlist = []
    for product in products:
        if product.get("id") is None or str(product["id"]) == str(target.get("id")):
            continue
        name_terms = set(re.findall(r"[\w-]{4,}", str(product.get("name") or "").casefold()))
        overlap = len(target_terms & name_terms)
        if overlap:
            shortlist.append((overlap, product))
    shortlist.sort(key=lambda row: row[0], reverse=True)
    semaphore = asyncio.Semaphore(3)

    async def detail(item: dict[str, Any]) -> dict[str, Any] | None:
        async with semaphore:
            try:
                return await catalog.product(str(item["id"]))
            except CatalogError:
                return None

    detailed = await asyncio.gather(*(detail(item) for _, item in shortlist[:8]))
    return find_alternatives(target, [item for item in detailed if item is not None], limit=3)


async def alternatives_for(target: dict[str, Any], products: list[dict[str, Any]], mode: str) -> tuple[list[dict[str, Any]], str | None]:
    if mode == "demo":
        return find_alternatives(target, products, limit=3)
    return await live_alternatives(target, products)


@app.get("/", include_in_schema=False)
async def home() -> FileResponse:
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/widget", include_in_schema=False)
async def widget_page() -> FileResponse:
    return FileResponse(ROOT / "frontend" / "widget.html")


@app.get("/cart", include_in_schema=False)
async def cart_page() -> FileResponse:
    return FileResponse(ROOT / "frontend" / "cart.html")


@app.get("/api/status")
async def status() -> dict[str, Any]:
    ai_mode = "openai" if ai_configured() else "offline"
    if not catalog.configured:
        return {"mode": "demo", "connected": False, "ai_mode": ai_mode, "model": settings.openai_model if ai_configured() else None, "message": "Не настроены учётные данные каталога. Показаны демонстрационные данные."}
    try:
        products, pagination = await catalog.products()
        return {"mode": "live", "connected": True, "ai_mode": ai_mode, "model": settings.openai_model if ai_configured() else None, "message": f"Каталог доступен. Получено товаров: {len(products)}.", "pagination": pagination}
    except CatalogError as exc:
        return {"mode": "error", "connected": False, "ai_mode": ai_mode, "model": settings.openai_model if ai_configured() else None, "message": f"{exc} Каталог недоступен."}


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
        item = demo_product(product_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Товар не найден.") from exc
        return {"mode": "demo", "product": item}


@app.get("/api/cart/{session_id}")
async def get_cart(session_id: str) -> dict[str, Any]:
    return cart.contents(session_id)


@app.get("/api/session/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    return {"messages": sessions.recent(session_id, limit=12), "pending_confirmation": cart.pending(session_id) is not None}


@app.delete("/api/session/{session_id}")
async def clear_session(session_id: str) -> dict[str, bool]:
    cart.cancel(session_id)
    sessions.clear(session_id)
    return {"cleared": True}


@app.post("/api/requirements/extract")
async def extract_shopping_requirements(request: ChatRequest) -> dict[str, Any]:
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Опишите, какой товар нужен.")
    available = known_attributes()
    if ai_configured():
        try:
            extracted = await extract_with_openai(message, sessions.recent(request.session_id), available_attributes=available)
        except AiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        mode = "openai"
    else:
        extracted = offline_extract(message, available)
        mode = "offline"
    sessions.save_requirements(request.session_id, extracted.model_dump())
    return {"requirements": extracted.model_dump(), "available_attributes": available, "ai_mode": mode}


@app.get("/api/requirements/{session_id}")
async def get_shopping_requirements(session_id: str) -> dict[str, Any]:
    return {"requirements": sessions.get_requirements(session_id), "available_attributes": known_attributes()}


@app.put("/api/requirements/{session_id}")
async def update_shopping_requirements(session_id: str, request: RequirementsUpdate) -> dict[str, Any]:
    available = known_attributes()
    try:
        updated = validate_attributes(request.requirements, available)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated.clarification_question and any(item.attribute in updated.clarification_question.casefold() for item in updated.requirements):
        updated = updated.model_copy(update={"clarification_question": None})
    sessions.save_requirements(session_id, updated.model_dump())
    return {"requirements": updated.model_dump(), "available_attributes": available}


@app.post("/api/chat")
async def chat(request: ChatRequest) -> dict[str, Any]:
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Сообщение не должно быть пустым.")
    result = await _chat_impl(request, message)
    if result.get("answer"):
        sessions.append_exchange(request.session_id, message, result["answer"])
    products = result.get("products") or []
    if products and products[0].get("id") is not None:
        sessions.remember_products(request.session_id, [str(products[0]["id"])])
    result.setdefault("ai_mode", "openai" if ai_configured() else "offline")
    return result


async def _chat_impl(request: ChatRequest, message: str) -> dict[str, Any]:

    pending = cart.pending(request.session_id)
    if pending:
        pending_mode = "demo" if demo_product(str(pending["product_id"])) is not None else "live"
        if is_refusal(message):
            cart.cancel(request.session_id)
            return {"answer": "Хорошо, товар не добавлен. Корзина не изменилась.", "products": [], "cart": cart.contents(request.session_id), "catalog_mode": pending_mode}
        if is_confirmation(message):
            if pending["quantity"] is None:
                return {"answer": "Сначала укажите количество в штуках или напишите «отмена».", "products": [], "catalog_mode": pending_mode}
            if pending_mode == "demo":
                if catalog.configured:
                    raise HTTPException(status_code=409, detail="Режим каталога изменился; начните добавление заново.")
                fresh = demo_product(str(pending["product_id"]))
            else:
                if not catalog.configured:
                    raise HTTPException(status_code=409, detail="Live-каталог не настроен; корзина не изменилась.")
                try:
                    fresh = await catalog.product(str(pending["product_id"]))
                except CatalogError as exc:
                    raise HTTPException(status_code=502, detail="Не удалось перепроверить товар и остаток. Корзина не изменилась.") from exc
            state, stock = stock_state(fresh)
            if state != "available" or stock is None or stock < 1:
                raise HTTPException(status_code=409, detail="Свежий доступный остаток не подтверждён. Корзина не изменилась.")
            try:
                updated = cart.add_confirmed(request.session_id, fresh, int(pending["quantity"]), stock)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=f"{exc} Корзина не изменилась.") from exc
            if updated.get("already_confirmed"):
                return {"answer": "Это добавление уже было обработано; корзина не изменена повторно.", "products": [fresh], "cart": updated, "catalog_mode": pending_mode, "cart_url": "/cart"}
            return {"answer": f"Добавлено {pending['quantity']} шт. «{fresh.get('name') or fresh.get('sku') or fresh['id']}» в демонстрационную корзину.", "products": [fresh], "cart": updated, "catalog_mode": pending_mode, "cart_url": "/cart"}
        quantity = extract_quantity(message)
        if quantity is not None:
            if quantity <= 0:
                return {"answer": "Количество должно быть положительным. Добавление пока не подтверждено.", "products": [], "catalog_mode": pending_mode}
            cart.set_quantity(request.session_id, quantity)
            if pending_mode == "demo":
                if catalog.configured:
                    raise HTTPException(status_code=409, detail="Режим каталога изменился; начните добавление заново.")
                pending_product = demo_product(str(pending["product_id"]))
            else:
                if not catalog.configured:
                    raise HTTPException(status_code=409, detail="Live-каталог не настроен.")
                try:
                    pending_product = await catalog.product(str(pending["product_id"]))
                except CatalogError as exc:
                    raise HTTPException(status_code=502, detail="Не удалось загрузить карточку товара.") from exc
            state, stock = stock_state(pending_product)
            if state != "available" or stock is None:
                return {"answer": "Наличие с точным остатком не подтверждено. Добавить товар нельзя.", "products": [pending_product], "catalog_mode": pending_mode}
            if quantity > stock:
                return {"answer": f"Запрошено {quantity} шт., а в каталоге доступно {stock}. Укажите меньшее количество или напишите «отмена».", "products": [pending_product], "catalog_mode": pending_mode}
            return {"answer": f"Подтвердите добавление: {quantity} шт. «{pending_product.get('name') or pending_product.get('sku') or pending_product['id']}» в демонстрационную корзину. Ответьте «Подтверждаю» или «Отмена».", "products": [pending_product], "catalog_mode": pending_mode, "pending_confirmation": True}
        return {"answer": "Добавление ещё не выполнено. Ответьте точным количеством в штуках, «Подтверждаю» или «Отмена».", "products": [], "catalog_mode": pending_mode, "pending_confirmation": True}

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
            if not found and re.fullmatch(r"\d{5,}", message):
                try:
                    found = [await catalog.product(message)]
                except CatalogError:
                    pass
            if not found and re.search(r"\b(он|она|оно|его|этот|эта|этого|стоимость|цена|наличие|характеристики)\b", message.casefold()):
                previous_ids = set(sessions.last_product_ids(request.session_id))
                found = [p for p in all_products if str(p.get("id")) in previous_ids][:3]
            if found:
                hydrated = []
                for item in found[:3]:
                    if item.get("id") is not None and any(item.get(key) is None for key in ("characteristics", "category", "stock", "availability", "price")):
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
        all_products = DEMO_PRODUCTS
        found = search_products(DEMO_PRODUCTS, message)

    if is_purchase_intent(message):
        if not found:
            return {"answer": "Не нашёл товар для добавления. Уточните название или артикул.", "products": [], "catalog_mode": mode}
        if len(found) > 1 and not any(str(p.get("sku") or "").casefold() in message.casefold() for p in found if p.get("sku")):
            choices = ", ".join(str(p.get("sku") or p.get("name") or p.get("id")) for p in found[:3])
            return {"answer": f"Нашёл несколько совпадений ({choices}). Уточните артикул, пожалуйста.", "products": found[:3], "catalog_mode": mode}
        selected = found[0]
        if selected.get("id") is None:
            return {"answer": "У товара нет каталожного идентификатора; добавить его нельзя.", "products": [selected], "catalog_mode": mode}
        if mode == "demo":
            selected = demo_product(str(selected["id"]))
            if selected is None:
                raise HTTPException(status_code=409, detail="Демо-товар не найден; корзина не изменилась.")
        else:
            try:
                selected = await catalog.product(str(selected["id"]))
            except CatalogError as exc:
                raise HTTPException(status_code=502, detail="Не удалось перепроверить карточку товара.") from exc
        state, stock = stock_state(selected)
        if state != "available" or stock is None or stock < 1:
            alternatives, limitation = await alternatives_for(selected, all_products, mode)
            if alternatives:
                explain = "\n".join(f"• {row['product'].get('name') or row['product'].get('sku')}: {row['reason']}" for row in alternatives)
                source = "демо-набора" if mode == "demo" else "каталога"
                answer = f"У выбранного товара нет подтверждённого доступного остатка. Возможные аналоги из {source}:\n{explain}\nЕсли хотите добавить один из них, напишите его артикул и количество."
                if ai_configured():
                    try:
                        reply = await answer_with_openai(message, [selected] + [row["product"] for row in alternatives], sessions.recent(request.session_id), alternatives)
                        answer = reply.answer_text + "\n\nПроверенные совпадения каталога:\n" + explain
                    except AiError as exc:
                        raise HTTPException(status_code=502, detail=str(exc)) from exc
            else:
                answer = f"У выбранного товара нет подтверждённого доступного остатка. {limitation}"
            return {"answer": answer, "products": [selected] + [r["product"] for r in alternatives], "catalog_mode": mode, "alternatives": alternatives}
        quantity = extract_quantity(message)
        if quantity is not None and quantity <= 0:
            return {"answer": "Количество должно быть положительным. Корзина не изменилась.", "products": [selected], "catalog_mode": mode}
        if quantity is not None and quantity > stock:
            return {"answer": f"В каталоге доступно {stock} шт.; запрошено {quantity}. Укажите меньшее количество.", "products": [selected], "catalog_mode": mode}
        cart.prepare(request.session_id, str(selected["id"]), quantity)
        if quantity is None:
            answer = f"Сколько штук «{selected.get('name') or selected.get('sku') or selected['id']}» добавить? Доступный остаток: {stock}."
        else:
            answer = f"Подтвердите добавление: {quantity} шт. «{selected.get('name') or selected.get('sku') or selected['id']}» в демонстрационную корзину. Ответьте «Подтверждаю» или «Отмена»."
        return {"answer": answer, "products": [selected], "catalog_mode": mode, "pending_confirmation": True}

    alternatives = []
    if found:
        state, stock = stock_state(found[0])
        if state == "unavailable" or stock == 0:
            alternatives, limitation = await alternatives_for(found[0], all_products, mode)
    response_products = found + [row["product"] for row in alternatives]
    if ai_configured():
        try:
            reply = await answer_with_openai(message, response_products, sessions.recent(request.session_id), alternatives)
            answer = reply.answer_text
        except AiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    else:
        answer = fallback_answer(message, found, mode)
    if alternatives:
        source = "демо-набора" if mode == "demo" else "каталога"
        answer += f"\n\nПроверенные аналоги из {source}:\n" + "\n".join(f"• {row['product'].get('name') or row['product'].get('sku')}: {row['reason']}" for row in alternatives)
    elif found and (stock_state(found[0])[0] == "unavailable"):
        answer += f"\n\n{limitation}"
    return {
        "answer": answer,
        "products": response_products,
        "alternatives": alternatives,
        "catalog_mode": mode,
        "ai_mode": "openai" if ai_configured() else "offline",
        "model": settings.openai_model if ai_configured() else None,
    }
