# Domain: Telegram Bot + AI Agent

## Назначение
Telegram-бот для аналитики — пользователи задают вопросы о продажах, остатках, рекламе
через Telegram, AI-агент (Claude) отвечает на основе данных проекта.

## Ownership
Файлы этого домена:
- `models/telegram.py` — TelegramBotUser, TelegramChatBinding, BrandNote
- `schemas/telegram.py` — Pydantic schemas
- `services/telegram_service.py` — CRUD для bindings, notes
- `services/ai/agent.py` — AI-агент (Claude): обработка вопросов, tool use
- `services/ai/tools.py` — инструменты AI (запросы к БД, отчёты)
- `services/ai/llm_client.py` — клиент Anthropic API
- `services/ai/executor.py` — исполнитель tools
- `integrations/telegram_bot.py` — aiogram bot (dp, handlers, deep link auth)
- `routers/telegram.py` — HTTP API (JWT): управление bindings/notes
- `routers/telegram_webhook.py` — Webhook endpoint (БЕЗ JWT — secret token)
- `scheduler/jobs/ai_digest.py` — фоновый дайджест

## Tables
- `telegram_bot_users` — связь Telegram account ↔ DDS user (via deep link)
- `telegram_chat_bindings` — привязка chat ↔ project + brand
- `brand_notes` — заметки по брендам для AI-контекста

## Business Rules
1. **Авторизация:** deep link → /start {token} → link telegram_id ↔ user_id
2. **Binding:** один chat = один project + (опционально) brand
3. **AI Rate Limit:** ограничение запросов на chat_id (Redis)
4. **Webhook secret:** `X-Telegram-Bot-Api-Secret-Token` — ОБЯЗАТЕЛЕН в production
5. **Background processing:** webhook возвращает 200 сразу, AI обрабатывает в asyncio.create_task
6. **Polling vs Webhook:** `TELEGRAM_USE_POLLING=true` для локальной разработки

## Security
- Webhook endpoint НЕ использует JWT — валидация через secret token header
- `TELEGRAM_WEBHOOK_SECRET` MUST быть непустым в production
- AI agent rate limited через Redis

## Dependencies
- `funnel/` — данные воронки для AI-ответов
- `reports/` — отчёты для AI-ответов
- `auth` — deep link привязка к пользователю
- Anthropic API — `ANTHROPIC_API_KEY` в .env

## Cache Invalidation
Не кэшируется — AI-ответы всегда fresh.
