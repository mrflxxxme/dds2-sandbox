# ruff: noqa: RUF002, RUF003 — русские комментарии и docstring
"""
LLM-провайдер для ИИ-агентов подготовки жалоб (сменный).

Оценивает отзыв по правилам агента и контексту НАШЕГО товара → структурный вердикт
{qualifies, reason, complaint_text, confidence}. Провайдер:
- `openai_compatible` — любой OpenAI-совместимый эндпоинт (DeepSeek/GigaChat/OpenRouter/
  Groq/локальный) через httpx, без новых зависимостей; ключ `settings.COMPLAINT_LLM_API_KEY`;
- `claude` — существующий `backend.services.ai.llm_client` (fallback).

Подготовка ≠ отправка: модель НЕ подаёт жалобу, только решает основание и пишет текст.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from backend.config import settings

logger = logging.getLogger("dds.reviews.agent_llm")

_TIMEOUT = httpx.Timeout(60.0)

_SYSTEM = (
    "Ты — помощник продавца Wildberries. По ПРАВИЛАМ продавца реши, есть ли реальное "
    "ОСНОВАНИЕ пожаловаться на отзыв для удаления (спам, оскорбления, реклама, отзыв о "
    "доставке/ПВЗ а не о товаре, чужой/перепутанный товар, несоответствие карточке по "
    "правилам). Будь ЧЕСТНЫМ: если отзыв — настоящая претензия к товару и правилам не "
    "подходит, qualifies=false. Отвечай СТРОГО одним JSON-объектом без пояснений:\n"
    '{"qualifies": bool, "reason": "коротко почему", "confidence": 0..1, '
    '"complaint_text": "текст жалобы в поддержку WB, если qualifies=true, иначе пустая строка"}'
)


def _build_user_prompt(rules: str, examples: str | None, product: dict, review: dict) -> str:
    parts = [
        "ПРАВИЛА ДЛЯ ЖАЛОБЫ (специфика нашего товара):",
        rules.strip() or "(правила не заданы)",
    ]
    if examples and examples.strip():
        parts += ["\nПРИМЕРЫ ЖАЛОБ:", examples.strip()]
    parts += [
        "\nНАШ ТОВАР:",
        f"- Название: {product.get('name') or '—'}",
        f"- Предмет: {product.get('subject') or '—'}",
        f"- Бренд: {product.get('brand') or '—'}",
        "\nОТЗЫВ ПОКУПАТЕЛЯ:",
        f"- Оценка: {review.get('rating')}★",
        f"- Текст: {review.get('text') or '—'}",
        f"- Плюсы: {review.get('pros') or '—'}",
        f"- Минусы: {review.get('cons') or '—'}",
    ]
    return "\n".join(parts)


def _parse_verdict(raw: str) -> dict:
    """Достать JSON-вердикт из ответа модели (терпимо к обёрткам/тексту вокруг)."""
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {"qualifies": False, "reason": "не распарсить ответ модели", "confidence": 0.0, "complaint_text": ""}
    try:
        d = json.loads(m.group(0))
    except (ValueError, TypeError):
        return {"qualifies": False, "reason": "невалидный JSON модели", "confidence": 0.0, "complaint_text": ""}
    return {
        "qualifies": bool(d.get("qualifies")),
        "reason": str(d.get("reason") or "")[:500],
        "confidence": float(d.get("confidence") or 0.0),
        "complaint_text": str(d.get("complaint_text") or "")[:1000],
    }


async def _call_openai_compatible(base_url: str, model: str, system: str, user: str) -> str:
    api_key = settings.COMPLAINT_LLM_API_KEY
    if not api_key:
        raise ValueError("Не настроен ключ LLM (COMPLAINT_LLM_API_KEY) для агента жалоб")
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": 800,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, headers={"Authorization": f"Bearer {api_key}"}, json=payload)
        if resp.status_code == 401:
            raise ValueError("LLM: неверный ключ (401)")
        if resp.status_code == 429:
            raise ValueError("LLM: лимит запросов (429) — повторите позже")
        if resp.status_code != 200:
            raise ValueError(f"LLM error: HTTP {resp.status_code} — {resp.text[:200]}")
        data = resp.json()
    return str(data["choices"][0]["message"]["content"])


async def _call_claude(model: str, system: str, user: str) -> str:
    from backend.services.ai import llm_client

    resp = await llm_client.chat(
        messages=[{"role": "user", "content": user}],
        system=system,
        model=model or "claude-haiku-4-5-20251001",
        max_tokens=800,
        temperature=0.2,
    )
    for block in resp.content:
        text = getattr(block, "text", None)
        if text:
            return str(text)
    return ""


async def evaluate_review(
    provider: str,
    model: str,
    base_url: str | None,
    rules: str,
    examples: str | None,
    product: dict,
    review: dict,
) -> dict:
    """Вердикт по одному отзыву. Ошибку провайдера пробрасываем — прогон агента её ловит."""
    user = _build_user_prompt(rules, examples, product, review)
    if provider == "claude":
        raw = await _call_claude(model, _SYSTEM, user)
    else:
        raw = await _call_openai_compatible(base_url or settings.COMPLAINT_LLM_BASE_URL, model, _SYSTEM, user)
    return _parse_verdict(raw)
