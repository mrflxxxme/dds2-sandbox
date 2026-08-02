# ruff: noqa: RUF002, RUF003 — русские комментарии и docstring
"""
LLM-провайдер для ИИ-агентов автоответов на отзывы/вопросы (сменный).

Генерирует черновик ответа продавца СТРОГО из записей базы знаний товара
(wb_product_kb) + по ПРАВИЛАМ агента (тон, ограничения). Модель НЕ придумывает
факты: если в базе знаний нет информации для ответа — обязана вернуть
needs_info=true, и черновик уйдёт продавцу на ручную доработку.

Провайдер:
- `openai_compatible` — любой OpenAI-совместимый эндпоинт (DeepSeek/GigaChat/
  OpenRouter/Groq/локальный) через httpx, ключ `settings.COMPLAINT_LLM_API_KEY`;
- `claude` — существующий `backend.services.ai.llm_client` (fallback).

Транспорт переиспользуется из complaint_llm (те же ключи/таймауты/обработка
ошибок) — не дублируем. Черновик ≠ отправка: текст кладётся в wb_feedback_replies
(status draft, только ручное одобрение), отправляет фоновый sender.
"""

from __future__ import annotations

import json
import logging
import re

from backend.config import settings
from backend.services.ai.complaint_llm import _call_claude, _call_openai_compatible

logger = logging.getLogger("dds.reviews.reply_llm")

# Максимум символов ответа (WB ограничивает ответ на отзыв/вопрос ~5000, держим запас)
MAX_REPLY_LEN = 1000

_SYSTEM = (
    "Ты — помощник продавца Wildberries, отвечаешь покупателям на отзывы и вопросы "
    "ОТ ИМЕНИ продавца. Строго следуй ПРАВИЛАМ продавца (тон, что можно обещать, что "
    "нельзя). Пиши по-русски, коротко (2-5 предложений), по делу, без воды, без "
    "эмодзи если правила не требуют. Не признавай дефект и не обещай компенсаций, "
    "если правила этого не разрешают.\n"
    "Отвечай СТРОГО на основе приведённых данных БАЗЫ ЗНАНИЙ товара. НЕ выдумывай "
    "характеристики, сроки, размеры, состав — только факты из базы знаний. "
    "Если в базе знаний недостаточно информации для полного ответа — верни "
    "needs_info=true (тогда продавец допишет ответ вручную).\n"
    "Отвечай СТРОГО одним JSON-объектом без пояснений и markdown-обёрток:\n"
    '{"reply_text": "текст ответа покупателю (пустая строка, если needs_info=true)", '
    '"needs_info": bool, '
    '"used_kb_ids": [id использованных записей базы знаний]}'
)


def _format_kb_entries(kb_entries: list[dict]) -> str:
    """Записи базы знаний → текстовый блок промпта с id (для used_kb_ids)."""
    lines = []
    for e in kb_entries:
        q = (e.get("question_example") or "").strip()
        q_part = f" Вопрос: «{q}»." if q else ""
        lines.append(
            f"- #{e.get('id')} [{e.get('topic') or 'Прочее'}]{q_part} Ответ: «{(e.get('answer') or '').strip()}»"
        )
    return "\n".join(lines)


def _build_user_prompt(
    rules: str,
    examples: str | None,
    product: dict,
    item: dict,
    target_type: str,
    kb_entries: list[dict] | None = None,
) -> str:
    """Собрать user-промпт: правила + база знаний + примеры + товар + отзыв/вопрос."""
    parts = [
        "ПРАВИЛА ДЛЯ ОТВЕТА (тон и ограничения продавца):",
        rules.strip() or "(правила не заданы — вежливый нейтральный тон)",
    ]
    if kb_entries:
        parts += [
            "\nБАЗА ЗНАНИЙ ТОВАРА (единственный источник фактов для ответа):",
            _format_kb_entries(kb_entries),
        ]
    else:
        parts += [
            "\nБАЗА ЗНАНИЙ ТОВАРА: (пусто — фактов нет, верни needs_info=true)",
        ]
    if examples and examples.strip():
        parts += ["\nПРИМЕРЫ ОТВЕТОВ (стиль, НЕ источник фактов):", examples.strip()]
    parts += [
        "\nНАШ ТОВАР:",
        f"- Название: {product.get('name') or '—'}",
        f"- Предмет: {product.get('subject') or '—'}",
        f"- Бренд: {product.get('brand') or '—'}",
    ]
    if target_type == "question":
        parts += [
            "\nВОПРОС ПОКУПАТЕЛЯ:",
            f"- Текст: {item.get('text') or '—'}",
        ]
    else:
        parts += [
            "\nОТЗЫВ ПОКУПАТЕЛЯ:",
            f"- Оценка: {item.get('rating')}★",
            f"- Текст: {item.get('text') or '—'}",
            f"- Плюсы: {item.get('pros') or '—'}",
            f"- Минусы: {item.get('cons') or '—'}",
        ]
    return "\n".join(parts)


def _clean_reply_text(raw: str) -> str:
    """Очистить текст ответа модели: префиксы/кавычки/лишние строки, ограничить длину."""
    text = (raw or "").strip()
    # модель может обернуть ответ в кавычки или добавить «Ответ:»
    text = re.sub(r"^(ответ|reply)\s*[:—-]\s*", "", text, flags=re.IGNORECASE)
    if len(text) >= 2 and text[0] in "\"«" and text[-1] in "\"»":
        text = text[1:-1].strip()
    # отрезаем возможные пояснения после пустой строки (мета-комментарии модели)
    text = text.split("\n\n\n")[0].strip()
    return text[:MAX_REPLY_LEN]


def _parse_reply(raw: str) -> dict:
    """
    Разобрать структурный ответ модели → {reply_text, needs_info, used_kb_ids}.

    Терпимо к markdown-обёрткам и тексту вокруг JSON. Если JSON не извлечь —
    трактуем весь ответ как текст черновика (needs_info=False), но без
    подтверждённых фактов КБ (used_kb_ids=[]) — прогон решит, что с этим делать.
    """
    fallback = {"reply_text": "", "needs_info": True, "used_kb_ids": []}
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        text = _clean_reply_text(raw or "")
        return {"reply_text": text, "needs_info": not bool(text), "used_kb_ids": []}
    try:
        d = json.loads(m.group(0))
    except (ValueError, TypeError):
        return fallback
    used = d.get("used_kb_ids") or []
    if not isinstance(used, list):
        used = []
    used_ids = [int(x) for x in used if isinstance(x, (int, float)) or (isinstance(x, str) and x.isdigit())]
    needs_info = bool(d.get("needs_info"))
    text = _clean_reply_text(str(d.get("reply_text") or ""))
    if not text:
        # пустой текст — ответа нет, это всегда needs_info
        needs_info = True
    return {"reply_text": text, "needs_info": needs_info, "used_kb_ids": used_ids}


async def draft_reply(
    provider: str,
    model: str,
    base_url: str | None,
    rules: str,
    examples: str | None,
    product: dict,
    item: dict,
    target_type: str = "feedback",
    kb_entries: list[dict] | None = None,
) -> dict:
    """
    Черновик ответа на один отзыв/вопрос строго из базы знаний.

    Возвращает {reply_text, needs_info, used_kb_ids}. Ошибку провайдера
    пробрасываем — прогон её ловит.
    """
    user = _build_user_prompt(rules, examples, product, item, target_type, kb_entries)
    if provider == "claude":
        raw = await _call_claude(model, _SYSTEM, user)
    else:
        raw = await _call_openai_compatible(base_url or settings.COMPLAINT_LLM_BASE_URL, model, _SYSTEM, user)
    parsed = _parse_reply(raw)
    if not parsed["needs_info"] and not parsed["reply_text"]:
        raise ValueError("LLM вернул пустой ответ")
    return parsed
