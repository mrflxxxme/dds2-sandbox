# Domain: Telegram Bot & TMA

## Назначение
Telegram-бот для аналитики + Telegram Mini App (TMA).
Бот: пользователи задают вопросы через Telegram, AI отвечает (см. `DOMAIN_AI.md`).
TMA: мобильный дашборд внутри Telegram (capital, funnel, PnL, pulse, warehouse).

## Ownership
Файлы этого домена:
- `models/telegram.py` — TelegramBotUser, TelegramChatBinding, BrandNote
- `schemas/telegram.py` — Pydantic schemas
- `services/telegram_service.py` — CRUD для bindings, notes, TMA auth
- `integrations/telegram_bot.py` — aiogram 3 bot (dp, handlers, deep link auth, polling/webhook)
- `routers/telegram.py` — HTTP API (JWT): управление bindings/notes
- `routers/telegram_webhook.py` — Webhook endpoint (БЕЗ JWT — secret token)
- `routers/telegram_miniapp.py` — TMA API: auth (initData HMAC), chat, projects
- `scheduler/jobs/ai_digest.py` — фоновый утренний дайджест (7:00 MSK)

**AI-агенты вынесены в отдельный домен:** `DOMAIN_AI.md`

## Tables
- `telegram_bot_users` — связь Telegram account ↔ DDS user (via deep link)
- `telegram_chat_bindings` — привязка chat ↔ project + brand + notify_enabled
- `brand_notes` — заметки по брендам для AI-контекста (Obsidian-style memory)

## Business Rules
1. **Авторизация:** deep link → /start {token} → link telegram_id ↔ user_id
2. **Binding:** один chat = один project + (опционально) brand
3. **AI Rate Limit:** 20 req/hour per chat, 100 req/day per project (Redis)
4. **Webhook secret:** `X-Telegram-Bot-Api-Secret-Token` — ОБЯЗАТЕЛЕН в production
5. **Background processing:** webhook возвращает 200 сразу, AI обрабатывает в asyncio.create_task
6. **Polling vs Webhook:**
   - `TELEGRAM_USE_POLLING=true` + `DDS_ENV=development` → polling (local dev)
   - Production → webhook на `https://app.vyatkin-wb.ru/api/v1/bot/webhook`
   - ⚠️ Polling удаляет webhook — НИКОГДА не включать для prod-токена

## TMA (Telegram Mini App)
- Аутентификация: `POST /tma/auth` — проверка initData HMAC → JWT tokens
- Fallback auth: по telegram_id → по @username (auto-link)
- Страницы TMA (frontend): `frontend-react/src/app/(tma)/tma/[slug]/`
  - Dashboard (health check, anomalии, план-факт)
  - Capital (оборотный капитал, ROI, ликвидность)
  - Funnel (воронка продаж с фильтрами)
  - PnL (БДР + ОПИУ вкладки)
  - Pulse (быстрый KPI дашборд)
  - Warehouse (остатки, сборка, логистика, история)
  - Chat (AI-чат с агентами)

## Commands (Telegram bot)
- `/start <token>` — авторизация через deep link
- `/setup` — привязка chat → project + brand
- `/brand` — сменить бренд
- `/notify on|off` — вкл/выкл утренний дайджест
- `/note <text>` — добавить заметку по бренду
- `/notes` — показать все заметки
- `/delnote N` — удалить заметку #N

## Security
- Webhook endpoint НЕ использует JWT — валидация через secret token header
- `TELEGRAM_WEBHOOK_SECRET` MUST быть непустым в production
- TMA auth проверяет HMAC initData подписи Telegram

## Dependencies
- `services/ai/` — AI-агенты для обработки вопросов (см. DOMAIN_AI.md)
- `services/health_check_service.py` — данные для TMA dashboard
- `funnel/` — данные воронки
- `reports/` — отчёты
- Anthropic API — `ANTHROPIC_API_KEY`
