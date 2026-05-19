# DOMAIN_AI — AI Multi-Agent System

Система AI-аналитики для Wildberries-продавцов. Поток: вопрос пользователя →
`orchestrator` классифицирует интент (Claude Haiku) → маршрутизирует к 1-2
агентам (Claude Sonnet) → каждый агент в tool_use loop вызывает tools (запросы
к БД через `executor`) → `synthesizer` объединяет ответы (если агентов >1) →
ответ в Telegram (HTML) или TMA/web-chat (JSON).

## Агенты
| Агент | Роль |
|-------|------|
| Analyst | аналитик — продажи, воронка, тренды |
| Financier | финансист — прибыль, маржа, P&L (BDR/OPIU) |
| Marketer | маркетолог — DRR, эффективность рекламы, ROI |
| Advertiser | рекламщик — кампании, бюджеты, ставки, аномалии |
| SupplyManager | снабженец — остатки, дозаказ, прогноз дефицита |
| Logistics | логист — отгрузки, стоимость логистики, история |
| Logistician | legacy — перенаправляется на SupplyManager/Logistics |

## Таблицы
Аналитический слой собственных ORM-таблиц не имеет — работает через tools над таблицами других доменов. Собственные таблицы веб-чата:

| Модель | Назначение | Ключ |
|--------|------------|------|
| `AiConversation` | Диалог веб-чата (SoftDeleteMixin, TimestampMixin) | `(project_id, user_id)` |
| `AiMessage` | Сообщение диалога (без SoftDelete — каскад с conversation) | `conversation_id` FK CASCADE |

`AiMessage`: `role` (`user`/`assistant`), `content`, `files` JSONB (`[{name,type,size,url}]`), `tools_used` JSONB, `tokens_used`.

## Tools
Каждый tool = JSON schema → `executor` вызывает backend service → возвращает JSON. Все tools фильтруют по `project_id` + опционально `brand`. Сгруппированы по файлам:
- `tools/finance.py` — BDR (`get_bdr_data` — самый точный P&L), OPIU, DDS, маржинальность, себестоимость.
- `tools/marketing.py` — воронка, топ/антитоп товаров, аномалии (>30% изменение), сравнение периодов.
- `tools/logistics.py` / `shipping.py` / `supply.py` — остатки, дни до дефицита, распределение по складам WB, география заказов, дневной KPI, план снабжения, история отгрузок.
- `tools_legacy.py` — legacy-набор для обратной совместимости (используется монолитным `agent.py` для digest).

## Бизнес-правила
- **Rate limit:** 20 req/hour на чат, 100 req/day на проект (Redis, graceful degradation при недоступности).
- **History:** последние 10 сообщений в Redis (TTL 1 час).
- **Tool loop:** максимум 5 раундов tool_use за запрос.
- **Truncation:** ответы tools обрезаются до 15KB для экономии токенов.
- **Brand filtering:** все tools фильтруют по бренду, если он указан в binding.
- **Models:** Haiku — классификация интента, Sonnet — ответы агентов.
- **Memory:** инсайты автоматически сохраняются в `BrandNote` после ответа (Obsidian-style, путь — env `AI_MEMORY_DIR`, fallback на tmpdir).
- **Digest:** утренний дайджест в 7:00 MSK (Haiku) через scheduler.
- **Prompt caching:** `llm_client` ставит `cache_control=ephemeral` на system + последний tool (`enable_cache=True` по умолчанию) — экономия на повторных вызовах в 5-минутном TTL.
- **Multi-tenant:** каждый tool проверяет `project_id` ownership.

### Web-чат (ai_chat)
Отдельный слой над orchestrator для страницы `(main)/p/[slug]/ai-chat`. Собственной AI-логики не содержит — хранит историю и проксирует в `orchestrator.ask()`. `routers/ai_chat.py` (prefix `/ai`): CRUD conversations, `POST /ai/conversations/{id}/messages` (SSE через `StreamingResponse`), `POST /ai/upload` (картинки/документы для контекста). Все write endpoints — `Depends(rate_limit_write)`.

### Error handling
- `llm_client`: retry 3 раза с exponential backoff (2s→4s→8s) для 429/5xx/connection errors.
- `base.py` `chat()`, `orchestrator`, `executor` — обёрнуты в try/except; агент возвращает текст ошибки или tool возвращает `{"error": "..."}` вместо краша.

## Зависимости
- `services/funnel/`, `services/reports/`, `services/warehouse_*`, `services/cost/`, `services/stock_forecast_service.py`, `services/health_check_service.py` — источники данных для tools.
- Anthropic API (`ANTHROPIC_API_KEY`) — Claude Sonnet + Haiku.

## Грабли
- **XSS в выводе агента:** ответы рендерятся через `dangerouslySetInnerHTML`. Обязательна санитизация через `frontend-react/src/lib/sanitize.ts::sanitizeAIHtml()` (DOMPurify + hook, форсирует `target=_blank rel="noopener noreferrer"`). НЕ использовать ручной regex/allowlist — он пропускает `<img onerror>`, `javascript:` в href, `<svg onload>`.

## Файлы
- `services/ai/orchestrator.py` — классификация интента, маршрутизация, rate limit, history.
- `services/ai/agents/` — `base.py` (tool loop, history, brand notes) + по файлу на агента.
- `services/ai/synthesizer.py` — объединение ответов нескольких агентов.
- `services/ai/executor.py` — исполнитель tools (вызов сервисов, JSON-сериализация).
- `services/ai/llm_client.py` — клиент Anthropic API (retry, prompt caching).
- `services/ai/memory.py` — авто-инсайты в `BrandNote`.
- `services/ai/tools/`, `tools_legacy.py` — определения инструментов.
- `services/ai/prompts/` — системные промпты агентов.
- `services/ai/agent.py` — legacy монолитный агент (digest).
- `models/ai_chat.py`, `schemas/ai_chat.py`, `services/ai_chat_service.py`, `routers/ai_chat.py` — веб-чат.
