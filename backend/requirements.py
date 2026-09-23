"""Validated shopping-requirement extraction, separate from catalog lookup and cart actions."""

import json
import math
import re
from typing import Any, Literal

from openai import (
    APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI,
    AuthenticationError, BadRequestError, RateLimitError,
)
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from backend.ai import AiError
from backend.chat import extract_quantity
from backend.config import settings
from backend.requirements_prompt import REQUIREMENTS_PROMPT


# These names were observed in ekt.kz detail properties or in the marked demo catalog.
ATTRIBUTE_ALIASES = {
    "NOMINALNYY_TOK": "номинальный ток",
    "KOLICHESTVO_POLYUSOV": "количество полюсов",
    "NOMINALNOE_NAPRYAZHENIE": "номинальное напряжение",
    "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": "отключающая способность",
    "TIP_USTANOVKI": "тип установки",
    "сечение": "сечение",
    "жилы": "жилы",
    "тип": "тип",
    "номинальный ток": "номинальный ток",
    "полюса": "количество полюсов",
}
AVAILABLE_ATTRIBUTES = sorted(set(ATTRIBUTE_ALIASES.values()))
LIVE_ATTRIBUTES = sorted({ATTRIBUTE_ALIASES[key] for key in (
    "NOMINALNYY_TOK", "KOLICHESTVO_POLYUSOV", "NOMINALNOE_NAPRYAZHENIE",
    "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST", "TIP_USTANOVKI",
)})
DEMO_ATTRIBUTES = sorted({"номинальный ток", "количество полюсов", "сечение", "жилы", "тип"})


class RequestedAttribute(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    attribute: str
    value: str
    required: bool

    @model_validator(mode="after")
    def validate_fields(self) -> "RequestedAttribute":
        if not self.attribute.strip() or len(self.attribute) > 80 or not self.value.strip() or len(self.value) > 80:
            raise ValueError("Invalid requirement")
        return self


class ShoppingRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    intent: Literal["find", "compare", "alternative", "explain", "budget", "list", "unclear"]
    product_terms: list[str]
    article: str | None
    requirements: list[RequestedAttribute]
    quantity: int | None
    max_budget: float | None
    budget_currency: str | None
    clarification_question: str | None

    @model_validator(mode="after")
    def validate_fields(self) -> "ShoppingRequirements":
        if len(self.product_terms) > 5 or any(not term.strip() or len(term) > 80 for term in self.product_terms):
            raise ValueError("Invalid product terms")
        if self.article is not None and (not self.article.strip() or len(self.article) > 80):
            raise ValueError("Invalid article")
        if len(self.requirements) > 8:
            raise ValueError("Too many requirements")
        if self.quantity is not None and self.quantity <= 0:
            raise ValueError("Invalid quantity")
        if self.max_budget is not None and (not math.isfinite(self.max_budget) or self.max_budget <= 0 or self.max_budget > 1_000_000_000):
            raise ValueError("Invalid budget")
        if self.budget_currency is not None and (not self.budget_currency.strip() or len(self.budget_currency) > 20):
            raise ValueError("Invalid currency")
        if self.clarification_question is not None and (
            not self.clarification_question.strip() or len(self.clarification_question) > 200 or
            self.clarification_question.count("?") > 1
        ):
            raise ValueError("Invalid clarification")
        return self


def _compact(value: str) -> str:
    return re.sub(r"[^\w]+", "", value.casefold())


def validate_attributes(requirements: ShoppingRequirements, available_attributes: list[str] | None = None) -> ShoppingRequirements:
    allowed = set(available_attributes or AVAILABLE_ATTRIBUTES)
    for item in requirements.requirements:
        if item.attribute not in allowed:
            raise ValueError("Условие содержит характеристику, не подтверждённую доступным каталогом.")
    return requirements


def validate_extracted(
    requirements: ShoppingRequirements, message: str, history: list[dict[str, str]],
    available_attributes: list[str] | None = None,
) -> ShoppingRequirements:
    """Reject facts invented by the model; edits submitted by a user use validate_attributes only."""
    allowed = available_attributes or AVAILABLE_ATTRIBUTES
    validate_attributes(requirements, allowed)
    source = " ".join([message] + [row["content"] for row in history[-8:] if row.get("role") == "user"])
    normalized_source = _compact(source)
    for term in requirements.product_terms:
        if _compact(term) not in normalized_source:
            raise AiError("Модель выделила товар, которого не было в запросе. Уточните формулировку.")
    if requirements.article and _compact(requirements.article) not in normalized_source:
        raise AiError("Модель выделила артикул, которого не было в запросе.")
    for item in requirements.requirements:
        if _compact(item.value) not in normalized_source:
            raise AiError("Модель выделила характеристику, которой не было в запросе.")
        if not item.value.casefold().strip().startswith(("не ", "not ")) and any(
            _compact(prefix + item.value) in normalized_source for prefix in ("не", "not")
        ):
            raise AiError("Модель потеряла отрицание в требовании. Исправьте запрос.")
    direct_quantity = next(
        (quantity for text in [message] + [row["content"] for row in reversed(history[-8:]) if row.get("role") == "user"]
         if (quantity := extract_quantity(text)) is not None), None,
    )
    if requirements.quantity is not None and requirements.quantity != direct_quantity:
        raise AiError("Модель указала количество, которое не подтверждено текущим запросом.")
    if requirements.max_budget is not None:
        if not re.search(r"\b(?:бюджет|до|не более|не дороже|не выше|в пределах|максимум|лимит|budget|under)\b", message.casefold()):
            raise AiError("Модель указала бюджет без явного ограничения в запросе.")
        number = str(int(requirements.max_budget)) if requirements.max_budget.is_integer() else str(requirements.max_budget)
        if _compact(number) not in normalized_source:
            raise AiError("Модель указала бюджет, которого не было в запросе.")
    if requirements.budget_currency:
        aliases = {
            "тенге": ("тенге", "тг", "₸", "kzt"), "тг": ("тенге", "тг", "₸", "kzt"),
            "kzt": ("тенге", "тг", "₸", "kzt"), "₸": ("тенге", "тг", "₸", "kzt"),
            "руб": ("руб", "рублей", "₽", "rub"), "₽": ("руб", "рублей", "₽", "rub"),
            "usd": ("usd", "доллар", "$"), "$": ("usd", "доллар", "$"),
        }
        candidates = aliases.get(requirements.budget_currency.casefold(), (requirements.budget_currency,))
        if not any((candidate in source.casefold() if candidate in "₸₽$€" else bool(_compact(candidate)) and _compact(candidate) in normalized_source) for candidate in candidates):
            raise AiError("Модель указала валюту, которой не было в запросе.")
    if requirements.clarification_question:
        question = requirements.clarification_question.casefold()
        if not any(name in question for name in allowed):
            requirements = requirements.model_copy(update={"clarification_question": None})
        elif any(item.attribute in question for item in requirements.requirements):
            requirements = requirements.model_copy(update={"clarification_question": None})
    return ensure_checkable_clarification(requirements, allowed)


def ensure_checkable_clarification(requirements: ShoppingRequirements, available_attributes: list[str] | None = None) -> ShoppingRequirements:
    if requirements.clarification_question or requirements.intent not in {"find", "unclear"}:
        return requirements
    terms = " ".join(requirements.product_terms).casefold()
    known = {item.attribute for item in requirements.requirements}
    allowed = set(available_attributes or AVAILABLE_ATTRIBUTES)
    if "автомат" in terms and "номинальный ток" in allowed and "номинальный ток" not in known:
        return requirements.model_copy(update={"clarification_question": "Какой номинальный ток (А) нужен?"})
    if "кабель" in terms and "сечение" in allowed and "сечение" not in known:
        return requirements.model_copy(update={"clarification_question": "Какое сечение кабеля нужно?"})
    return requirements


def offline_extract(message: str, available_attributes: list[str] | None = None) -> ShoppingRequirements:
    """Conservative, separately labeled fallback for a credential-free demo."""
    text = message.casefold()
    terms = [name for name in ("автомат", "кабель", "светильник") if name in text]
    article_match = re.search(r"\b(?:DEMO-[A-Z0-9-]+|\d{5,})\b", message, flags=re.IGNORECASE)
    current_match = re.search(r"\b(не\s+)?(\d+(?:[.,]\d+)?)\s*а\b", text)
    requirements = []
    if current_match:
        value = f"{current_match.group(2)} А"
        if current_match.group(1):
            value = "не " + value
        requirements.append(RequestedAttribute(attribute="номинальный ток", value=value, required=True))
    extracted = ShoppingRequirements(
        intent="find" if terms or article_match else "unclear", product_terms=terms,
        article=article_match.group(0) if article_match else None, requirements=requirements,
        quantity=extract_quantity(message) if (extract_quantity(message) or 0) > 0 else None,
        max_budget=None, budget_currency=None, clarification_question=None,
    )
    return ensure_checkable_clarification(extracted, available_attributes)


async def extract_with_openai(
    message: str, history: list[dict[str, str]], client: Any | None = None,
    available_attributes: list[str] | None = None,
) -> ShoppingRequirements:
    if not settings.openai_api_key or not settings.openai_model:
        raise AiError("Offline demo — OpenAI API is not configured.")
    allowed = available_attributes or AVAILABLE_ATTRIBUTES
    context = {
        "user_message": message,
        "recent_messages": history[-8:],
        "available_attribute_names": allowed,
    }
    sdk = client or AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds, max_retries=0)
    try:
        response = await sdk.responses.parse(
            model=settings.openai_model, instructions=REQUIREMENTS_PROMPT,
            input=json.dumps(context, ensure_ascii=False), text_format=ShoppingRequirements,
            store=False, max_output_tokens=1400,
        )
        if getattr(response, "status", "completed") != "completed" or getattr(response, "output_parsed", None) is None:
            raise AiError("Модель не вернула полный список условий. Попробуйте ещё раз.")
        parsed = ShoppingRequirements.model_validate(response.output_parsed)
        return validate_extracted(parsed, message, history, allowed)
    except (AuthenticationError, RateLimitError) as exc:
        raise AiError("OpenAI API отклонил запрос или достигнут лимит. Проверьте ключ и лимиты проекта.") from exc
    except APITimeoutError as exc:
        raise AiError("OpenAI API не ответил вовремя. Попробуйте ещё раз.") from exc
    except APIConnectionError as exc:
        raise AiError("Нет соединения с OpenAI API. Попробуйте ещё раз.") from exc
    except (BadRequestError, APIStatusError, ValidationError, ValueError, TypeError) as exc:
        raise AiError("OpenAI API вернул некорректный список условий. Попробуйте ещё раз.") from exc
    except AiError:
        raise
    except Exception as exc:
        raise AiError("Не удалось проверить список условий OpenAI API. Попробуйте ещё раз.") from exc
