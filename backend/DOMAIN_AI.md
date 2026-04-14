# Domain: AI Multi-Agent System

## Назначение
Система AI-аналитики для Wildberries-продавцов. Пользователь задаёт вопрос →
Orchestrator классифицирует интент → маршрутизирует к 1-2 специализированным агентам →
каждый агент вызывает инструменты (запросы к БД) → Synthesizer объединяет ответы.

## Архитектура
```
Вопрос пользователя
    ↓
orchestrator.py — классификация интента (Claude Haiku)
    ↓ выбирает 1-2 агента
agents/*.py — специализированные агенты (Claude Sonnet)
    ↓ tool_use loop (до 5 раундов)
executor.py — исполнение tools → вызов services/
    ↓
synthesizer.py — объединение ответов (если >1 агент)
    ↓
Ответ → Telegram (HTML) или TMA (JSON)
```

## Ownership
Файлы этого домена:
- `services/ai/orchestrator.py` — классификация интента, маршрутизация, rate limit, history
- `services/ai/agents/base.py` — BaseAgent: tool execution loop, history, brand notes
- `services/ai/agents/analyst.py` — аналитик (воронка, KPI, тренды)
- `services/ai/agents/financier.py` — финансист (P&L, маржа, BDR, OPIU)
- `services/ai/agents/marketer.py` — маркетолог (реклама, DRR, ROI)
- `services/ai/agents/advertiser.py` — рекламщик (кампании, бюджеты, ставки)
- `services/ai/agents/supply_manager.py` — снабженец (остатки, заказы, прогноз)
- `services/ai/agents/logistics.py` — логист (отгрузки, стоимость, история)
- `services/ai/agents/logistician.py` — legacy (deprecated, используй supply_manager/logistics)
- `services/ai/synthesizer.py` — объединение ответов нескольких агентов
- `services/ai/memory.py` — Obsidian-style авто-инсайты в BrandNote (путь: `AI_MEMORY_DIR` env, fallback на tmpdir)
- `services/ai/executor.py` — исполнитель tools (вызов сервисов, JSON сериализация)
- `services/ai/llm_client.py` — клиент Anthropic API (Sonnet + Haiku), retry с exponential backoff для 429/5xx
- `services/ai/tools/` — определения инструментов (JSON schema для Claude)
- `services/ai/tools/finance.py` — 6 tools (BDR, OPIU, DDS, margins, cost)
- `services/ai/tools/marketing.py` — 5 tools (funnel, top products, anomalies, periods)
- `services/ai/tools/logistics.py` — 8 tools (stocks, warehouses, geography, history)
- `services/ai/tools/shipping.py` — tools для логистики
- `services/ai/tools/supply.py` — tools для снабжения
- `services/ai/prompts/` — системные промпты для каждого агента
- `services/ai/agent.py` — legacy монолитный агент (используется для digest)
- `services/ai/tools_legacy.py` — legacy tools (13 шт, для обратной совместимости)
- `services/health_check_service.py` — health check данные (отправки, просрочки, кат.А, неликвид)

### Web-интерфейс (ai_chat)
Отдельный слой над orchestrator для веб-чата на странице `(main)/p/[slug]/ai-chat`.
Сама бизнес-логика AI здесь НЕ реализуется — модуль только хранит историю и проксирует в `orchestrator.ask()`.

- `models/ai_chat.py`:
  - `AiConversation` (SoftDeleteMixin, TimestampMixin) — диалог. Поля: `project_id`, `user_id`, `brand` (опционально), `title` (default `"Новый чат"`), `created_at`.
  - `AiMessage` — одно сообщение. Поля: `conversation_id` (FK с `ondelete="CASCADE"`), `role` (`user`/`assistant`), `content` (Text), `files` JSONB (`[{name, type, size, url}]`), `tools_used` JSONB, `tokens_used`, `created_at`. Без SoftDeleteMixin — каскадно удаляется с conversation.
- `schemas/ai_chat.py` — `AiConversationCreate/Update/Schema/List`, `AiMessageCreate/Schema`, `AiFileUploadResponse`.
- `services/ai_chat_service.py` — `list_conversations`, CRUD conversations и messages. Все запросы фильтруют по `project_id` + `user_id` + `is_deleted == False`.
- `routers/ai_chat.py` (prefix `/ai`):
  - CRUD conversations (с `Depends(get_current_user)` и `get_current_project`).
  - `POST /ai/conversations/{id}/messages` — стримит ответ агента через `StreamingResponse` (SSE).
  - `POST /ai/upload` — file upload (картинки/документы для контекста), возвращает URL.
  - Все write endpoints под `Depends(rate_limit_write)`.
  - Внутри вызывает `from backend.services.ai.orchestrator import ask as orchestrator_ask`.
- Подключён в `backend/main.py` (`app.include_router(ai_chat.router)`).
- Frontend: `frontend-react/src/app/(main)/p/[slug]/ai-chat/page.tsx` + `lib/api/ai-chat.ts`.

## 7 агентов

| Агент | Роль | Типичные вопросы |
|-------|------|------------------|
| Analyst | Аналитик | "Как продажи?", "Покажи воронку", "Тренды за неделю" |
| Financier | Финансист | "Какая прибыль?", "Маржинальность?", "P&L за март" |
| Marketer | Маркетолог | "DRR по товарам", "Эффективность рекламы", "ROI кампаний" |
| Advertiser | Рекламщик | "Бюджеты кампаний", "Ставки", "Аномалии в рекламе" |
| SupplyManager | Снабженец | "Что заказать?", "Остатки на складах", "Прогноз дефицита" |
| Logistics | Логист | "Стоимость логистики", "История отгрузок", "Куда едет товар" |
| Logistician | Legacy | Перенаправляется на SupplyManager/Logistics |

## 19 инструментов (tools)
Каждый tool = JSON schema → executor вызывает backend service → возвращает JSON.
Все tools фильтруют по `project_id` + опционально `brand`.

### Finance (6)
- `get_bdr_data` — **самый точный P&L** (из финансовых строк WB)
- `get_opiu_report` — ОПИУ (выручка, расходы, EBITDA)
- `get_dds_report` — ДДС по месяцам
- `get_margins_analysis` — маржинальность по артикулам
- `get_cost_data` — себестоимость, пропуски
- `get_cost_analysis` — анализ структуры затрат

### Marketing (5)
- `get_funnel_data` — воронка (заказы, выручка, DRR, CTR)
- `get_top_products` — топ/антитоп товаров с трендами
- `get_anomalies` — аномалии в метриках (>30% изменение)
- `compare_periods` — сравнение периодов (WoW, MoM)
- `get_product_rankings` — рейтинг товаров

### Logistics (8)
- `get_stock_info` — остатки, дней до дефицита, светофор
- `get_warehouse_need` — рекомендации по дозаказу
- `get_warehouse_stocks` — распределение по складам WB
- `get_order_geography` — география заказов (топ-20 городов)
- `get_day_analysis` — дневной KPI дашборд с аномалиями
- `get_product_info` — детали по одному товару
- `get_logistics_history` — история отгрузок, стоимость логистики
- `get_supply_plan` — план снабжения

## Business Rules
1. **Rate Limit:** 20 req/hour per chat, 100 req/day per project (Redis)
2. **History:** последние 10 сообщений в Redis (TTL 1 час)
3. **Brand filtering:** все tools фильтруют по бренду если указан в binding
4. **Tool loop:** максимум 5 раундов tool_use за один запрос
5. **Truncation:** ответы tools обрезаются до 15KB для экономии токенов
6. **Memory:** инсайты автоматически сохраняются в BrandNote после ответа
7. **Models:** Haiku для классификации интента, Sonnet для ответов агентов
8. **Digest:** утренний дайджест в 7:00 MSK (Haiku) через scheduler

## Error Handling
- `llm_client.py`: retry 3 раза с exponential backoff (2s→4s→8s) для 429, 5xx, connection errors
- `base.py`: chat() обёрнут в try/except — агент возвращает "Ошибка при обращении к AI" вместо crash
- `orchestrator.py`: single-agent и multi-agent пути оба защищены от необработанных исключений
- `executor.py`: все tools обёрнуты в try/except, возвращают `{"error": "..."}` при ошибке

## Security
- Все tools проверяют project_id ownership
- Multi-tenant изоляция через project_id на каждом запросе
- API key: `ANTHROPIC_API_KEY` в .env
- Rate limiting через Redis (graceful degradation при недоступности)

## Dependencies
- `services/funnel/` — данные воронки, реклама
- `services/reports/` — ДДС, БДР, ОПИУ
- `services/warehouse_*` — склады, остатки
- `services/stock_forecast_service.py` — прогноз дефицита
- `services/cost/` — себестоимость
- `services/health_check_service.py` — health check для TMA dashboard
- Anthropic API (Claude Sonnet + Haiku)
