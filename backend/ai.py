"""OpenAI Responses API adapter with strict schema and backend validation."""

import json
import re
from typing import Any, Literal

from openai import (
    APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI,
    AuthenticationError, BadRequestError, RateLimitError,
)
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from backend.config import settings
from backend.chat import fallback_answer
from backend.prompt import SYSTEM_PROMPT


class AiError(Exception):
    """Safe user-facing OpenAI error."""


class AIReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    intent: Literal["product_info", "availability", "alternatives", "purchase_intent", "general_terms", "unclear"]
    answer_text: str
    matched_product_ids: list[str]
    alternative_product_ids: list[str]
    candidate_quantity: int | None
    needs_clarification: bool
    clarification_question: str | None

    @model_validator(mode="after")
    def validate_answer(self) -> "AIReply":
        if not self.answer_text.strip() or len(self.answer_text) > 600:
            raise ValueError("Invalid answer length")
        if self.clarification_question is not None and len(self.clarification_question) > 240:
            raise ValueError("Invalid clarification length")
        if self.candidate_quantity is not None and self.candidate_quantity <= 0:
            raise ValueError("Invalid quantity")
        if len(self.matched_product_ids) > 5 or len(self.alternative_product_ids) > 3:
            raise ValueError("Too many product IDs")
        return self


def _safe_product(product: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "sku", "name", "description", "category", "price", "stock", "availability", "characteristics", "certificates", "url")
    return {key: product.get(key) for key in keys}


def validate_grounding(reply: AIReply, products: list[dict[str, Any]], alternatives: list[dict[str, Any]]) -> AIReply:
    allowed = {str(p["id"]) for p in products if p.get("id") is not None}
    approved_alternatives = {str(row["product"]["id"]) for row in alternatives if row["product"].get("id") is not None}
    if not set(reply.matched_product_ids).issubset(allowed):
        raise AiError("Ответ модели ссылается на товар вне результатов поиска. Попробуйте ещё раз.")
    if not set(reply.alternative_product_ids).issubset(approved_alternatives):
        raise AiError("Ответ модели содержит неподтверждённый аналог. Попробуйте ещё раз.")
    if re.search(r"\b(добавлен[аоы]?|оформлен[аоы]?|added to (?:your |the )?cart)\b", reply.answer_text.casefold()):
        raise AiError("Ответ модели содержит неподтверждённое действие. Попробуйте ещё раз.")
    return reply


def compose_grounded_answer(reply: AIReply, products: list[dict[str, Any]], mode: str) -> str:
    """Use the model's validated selection, but display product facts only from backend cards.

    Free-form model prose cannot be proved against arbitrary catalog fields, so it is not
    sent to the buyer. The cloud model still classifies the request and selects IDs.
    """
    selected = set(reply.matched_product_ids)
    verified = [product for product in products if str(product.get("id")) in selected]
    if verified:
        return fallback_answer("", verified, mode)
    if products:
        return "Не удалось однозначно выбрать товар по вопросу. Уточните артикул или нужную характеристику."
    return fallback_answer("", [], mode)


async def answer_with_openai(
    message: str,
    products: list[dict[str, Any]],
    history: list[dict[str, str]],
    alternatives: list[dict[str, Any]] | None = None,
    client: Any | None = None,
) -> AIReply:
    if not settings.openai_api_key or not settings.openai_model:
        raise AiError("Offline demo — OpenAI API is not configured.")
    alternatives = alternatives or []
    context = {
        "user_message": message,
        "recent_conversation": history[-8:],
        "catalog_results": [_safe_product(p) for p in products[:5]],
        "backend_approved_alternatives": [
            {"product": _safe_product(row["product"]), "matching_evidence": row["reason"]}
            for row in alternatives[:3]
        ],
        "store_faq": None,
    }
    sdk = client or AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds, max_retries=0)
    try:
        response = await sdk.responses.parse(
            model=settings.openai_model,
            instructions=SYSTEM_PROMPT,
            input=json.dumps(context, ensure_ascii=False),
            text_format=AIReply,
            store=False,
            max_output_tokens=600,
        )
        if getattr(response, "status", "completed") != "completed" or getattr(response, "output_parsed", None) is None:
            raise AiError("Модель не вернула полный структурированный ответ. Попробуйте ещё раз.")
        parsed = AIReply.model_validate(response.output_parsed)
        return validate_grounding(parsed, products, alternatives)
    except (AuthenticationError, RateLimitError) as exc:
        raise AiError("OpenAI API отклонил запрос или достигнут лимит. Проверьте ключ и лимиты проекта.") from exc
    except APITimeoutError as exc:
        raise AiError("OpenAI API не ответил вовремя. Попробуйте ещё раз.") from exc
    except APIConnectionError as exc:
        raise AiError("Нет соединения с OpenAI API. Попробуйте ещё раз.") from exc
    except (BadRequestError, APIStatusError, ValidationError, ValueError, TypeError) as exc:
        raise AiError("OpenAI API вернул некорректный ответ. Попробуйте ещё раз.") from exc
    except AiError:
        raise
    except Exception as exc:
        raise AiError("Не удалось проверить ответ OpenAI API. Попробуйте ещё раз.") from exc
